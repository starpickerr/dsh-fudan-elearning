import io
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from elearning_mcp.dsh_credentials import DshCredentialStore
from elearning_mcp.errors import SyncError, SyncCancelled
from elearning_mcp.service import ElearningService

BASE = 'https://elearning.fudan.edu.cn'


class Response(io.BytesIO):
    def __init__(self, body=b'%PDF-homework'):
        super().__init__(body)
        self.headers = {'Content-Type': 'application/pdf', 'Content-Length': str(len(body))}


class Canvas:
    base = BASE
    opener_factory = None

    def __init__(self):
        self.calls = []
        self.assignment = {'id': 20, 'course_id': 42, 'name': 'Homework', 'submission_types': ['online_upload'],
                           'allowed_extensions': ['pdf'], 'locked_for_user': False, 'allowed_attempts': 3,
                           'submission': {'attempt': None, 'workflow_state': 'unsubmitted'}}
        self.file = {'id': 10, 'size': 13, 'url': BASE + '/files/10/download', 'filename': 'lecture.pdf'}

    def get(self, path, params=None):
        self.calls.append(('get', path, params))
        return (self.file if '/files/' in path else self.assignment), None

    def open(self, url, api=True):
        self.calls.append(('download', url))
        return Response()

    def upload_file(self, path, stream, filename, size):
        self.calls.append(('upload', path, filename, stream.read(), size))
        return {'id': 100 + len([row for row in self.calls if row[0] == 'upload'])}

    def post(self, path, fields):
        self.calls.append(('post', path, fields))
        return {'assignment_id': 20, 'attempt': 1, 'workflow_state': 'submitted',
                'submission_type': 'online_upload', 'submitted_at': '2026-11-09T12:00:00Z',
                'attachments': [{'id': row} for row in fields['submission[file_ids][]']]}


class TransferTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.canvas = Canvas()
        self.store = DshCredentialStore(self.root / 'data', {'username': 'synthetic', 'password': 'synthetic-secret'})
        self.service = ElearningService(self.root / 'data', store=self.store, client_factory=lambda _: self.canvas)
        self.homework = self.root / '答案.pdf'
        self.homework.write_bytes(b'%PDF-homework')

    def test_download_from_web_link_and_preserve_existing_destination(self):
        destination = self.root / 'lecture.pdf'
        result = self.service.download_file('42', BASE + '/courses/42/files/10/download?download_frd=1', str(destination))
        self.assertEqual(destination.read_bytes(), b'%PDF-homework')
        self.assertEqual(result['path'], str(destination))
        self.assertEqual(result['file_id'], '10')
        with self.assertRaises(SyncError):
            self.service.download_file('42', '10', str(destination))
        self.assertEqual(destination.read_bytes(), b'%PDF-homework')
        self.assertFalse(list(self.root.glob('.elearning-*')))

    def test_download_mismatch_and_symlinks_leave_no_partial_files(self):
        self.canvas.file['size'] = 100
        destination = self.root / 'lecture.pdf'
        with self.assertRaises(SyncError):
            self.service.download_file('42', '10', str(destination))
        self.assertFalse(destination.exists())
        self.assertFalse(list(self.root.glob('.elearning-*')))
        destination.symlink_to(self.homework)
        with self.assertRaises(SyncError):
            self.service.download_file('42', '10', str(destination))
        self.assertEqual(self.homework.read_bytes(), b'%PDF-homework')

    def test_outside_course_scope_or_link_never_reads_file(self):
        self.service.authorized_resources = []
        self.service.user_id = 'user'
        with self.assertRaises(PermissionError):
            self.service.download_file('42', '10', str(self.root / 'x.pdf'))
        with self.assertRaises(PermissionError):
            self.service.get_assignment('42', '20')
        with self.assertRaises(PermissionError):
            self.service.submit_assignment('42', '20', [str(self.homework)], expected_attempt=0)
        self.assertEqual(self.canvas.calls, [])
        self.service.authorized_resources = None
        for link in [BASE + '/courses/99/files/10', 'https://evil.invalid/files/10', '../10', '0']:
            with self.subTest(link=link), self.assertRaises((ValueError, SyncError)):
                self.service.download_file('42', link, str(self.root / 'x.pdf'))
        self.assertEqual(self.canvas.calls, [])

    def test_assignment_details_and_multi_file_submission_receipt(self):
        details = self.service.get_assignment('42', '20')
        self.assertEqual(details['submission']['attempt'], 0)
        second = self.root / 'appendix.pdf'
        second.write_bytes(b'appendix')
        result = self.service.submit_assignment('42', '20', [str(self.homework), str(second)], expected_attempt=0)
        self.assertEqual(result['status'], 'submitted')
        self.assertEqual(result['attempt'], 1)
        self.assertEqual(result['file_ids'], ['101', '102'])
        uploads = [row for row in self.canvas.calls if row[0] == 'upload']
        self.assertEqual(uploads[0][3], b'%PDF-homework')
        self.assertEqual(uploads[1][3], b'appendix')
        self.assertEqual(self.canvas.calls[-1][2], {'submission[submission_type]': 'online_upload', 'submission[file_ids][]': ['101', '102']})

    def test_reject_stale_attempt_locked_unsupported_and_exhausted_before_upload(self):
        for change in [{'submission': {'attempt': 1}}, {'locked_for_user': True},
                       {'submission_types': ['external_tool']}, {'allowed_attempts': 0}]:
            with self.subTest(change=change):
                original = dict(self.canvas.assignment)
                self.canvas.assignment.update(change)
                with self.assertRaises(SyncError):
                    self.service.submit_assignment('42', '20', [str(self.homework)], expected_attempt=0)
                self.canvas.assignment = original
                self.assertFalse(any(row[0] in {'post', 'upload'} for row in self.canvas.calls))

    def test_validate_all_local_files_before_any_upload(self):
        text = self.root / 'answer.txt'
        text.write_text('answer')
        symbolic = self.root / 'linked.pdf'
        symbolic.symlink_to(self.homework)
        for files in [[], [str(self.homework), str(text)], [str(symbolic)], [str(self.root)], [str(self.root / 'missing.pdf')]]:
            with self.subTest(files=files), self.assertRaises((ValueError, SyncError)):
                self.service.submit_assignment('42', '20', files, expected_attempt=0)
            self.assertFalse(any(row[0] in {'post', 'upload'} for row in self.canvas.calls))

    def test_upload_failure_and_cancellation_never_submit(self):
        with patch.object(self.canvas, 'upload_file', side_effect=SyncError('upload failed')):
            with self.assertRaises(SyncError):
                self.service.submit_assignment('42', '20', [str(self.homework)], expected_attempt=0)
        self.assertFalse(any(row[0] == 'post' for row in self.canvas.calls))
        self.service.cancel_event = threading.Event()
        self.service.cancel_event.set()
        with self.assertRaises(SyncCancelled):
            self.service.submit_assignment('42', '20', [str(self.homework)], expected_attempt=0)

    def test_attempt_changes_during_upload_stop_submission(self):
        upload = self.canvas.upload_file

        def changed(*args):
            result = upload(*args)
            self.canvas.assignment['submission'] = {'attempt': 1, 'workflow_state': 'submitted'}
            return result

        with patch.object(self.canvas, 'upload_file', side_effect=changed), self.assertRaises(SyncError) as error:
            self.service.submit_assignment('42', '20', [str(self.homework)], expected_attempt=0)
        self.assertEqual(error.exception.code, 'attempt_conflict')
        self.assertFalse(any(row[0] == 'post' for row in self.canvas.calls))

    def test_explicit_resubmission_uses_expected_attempt_and_new_receipt(self):
        self.canvas.assignment['submission'] = {'attempt': 1, 'workflow_state': 'submitted'}
        receipt = {'assignment_id': 20, 'attempt': 2, 'workflow_state': 'submitted', 'submission_type': 'online_upload',
                   'submitted_at': '2026-11-09T13:00:00Z', 'attachments': [{'id': 101}]}
        with patch.object(self.canvas, 'post', return_value=receipt):
            result = self.service.submit_assignment('42', '20', [str(self.homework)], expected_attempt=1)
        self.assertEqual(result['attempt'], 2)
        self.assertEqual(result['file_ids'], ['101'])

    def test_unverified_submission_is_unknown_and_not_retried(self):
        with patch.object(self.canvas, 'post', return_value={'assignment_id': 20, 'workflow_state': 'unsubmitted'}) as post:
            with self.assertRaises(SyncError) as error:
                self.service.submit_assignment('42', '20', [str(self.homework)], expected_attempt=0)
            self.assertEqual(error.exception.code, 'submission_unknown')
            self.assertEqual(post.call_count, 1)


if __name__ == '__main__':
    unittest.main()
