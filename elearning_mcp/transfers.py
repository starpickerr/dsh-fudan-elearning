"""Explicit local file transfers and Canvas assignment submission."""
from contextlib import ExitStack
import os
from pathlib import Path
import re
import stat
from urllib.parse import urlparse

from .errors import SyncError
from .file_sync import _download, _open_directory, _check_directory, _MAX_FILE_SIZE
from .locking import state_lock


def numeric_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]*", value):
        raise ValueError("课程、作业和文件 ID 必须为正整数字符串")
    return value


def file_id_from_link(value, base, course_id):
    if not isinstance(value, str):
        raise ValueError("请使用文件 ID 或 eLearning 文件网页链接")
    if isinstance(value, str) and value.isdecimal():
        return numeric_id(value)
    parsed, origin = urlparse(value), urlparse(base)
    match = re.fullmatch(r"/(?:api/v1/)?(?:courses/([1-9][0-9]*)/)?files/([1-9][0-9]*)(?:/download|/preview)?/?", parsed.path)
    if (parsed.scheme != 'https' or parsed.netloc != origin.netloc or not match
            or match[1] not in (None, course_id)):
        raise ValueError("请使用该课程的文件 ID 或 eLearning 文件网页链接")
    return match[2]


def local_path(value):
    if not isinstance(value, str) or not value or any(ord(char) < 32 for char in value):
        raise ValueError("请指定本地文件的完整路径")
    path = Path(value).expanduser()
    if not path.is_absolute() or '..' in path.parts or path.name in ('', '.', '..'):
        raise ValueError("请指定本地文件的绝对路径")
    return path


def download_file(client, course_id, file_id, destination, check_cancelled):
    path = local_path(destination)
    directory = _open_directory(path.parent)
    temporary = None
    try:
        if os.path.lexists(path):
            raise SyncError("目标文件已存在，请使用新文件名。", code='destination_exists')
        raw, _ = client.get(f'/api/v1/courses/{course_id}/files/{file_id}')
        if (not isinstance(raw, dict) or str(raw.get('id')) != file_id
                or raw.get('locked_for_user') or raw.get('hidden_for_user') or not raw.get('url')):
            raise SyncError("课程文件不可访问。", code='permission_denied')
        expected = raw.get('size')
        if not isinstance(expected, int) or isinstance(expected, bool) or not 0 <= expected <= _MAX_FILE_SIZE:
            raise SyncError("文件大小无效或超过 512 MB。", code='invalid_file')
        temporary, digest = _download(client, raw['url'], directory, client.cancel_event if hasattr(client, 'cancel_event') else None)
        if os.stat(temporary, dir_fd=directory, follow_symlinks=False).st_size != expected:
            raise SyncError("下载文件大小与服务器不符，未保存。", code='download_failed')
        check_cancelled()
        _check_directory(path.parent, directory)
        try:
            os.link(temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
        except FileExistsError:
            raise SyncError("目标文件已存在，请使用新文件名。", code='destination_exists') from None
        return {'status': 'downloaded', 'course_id': course_id, 'file_id': file_id,
                'path': str(path), 'size': expected, 'sha256': digest}
    finally:
        if temporary is not None:
            os.unlink(temporary, dir_fd=directory)
        os.close(directory)


def read_assignment(client, course_id, assignment_id):
    raw, _ = client.get(f'/api/v1/courses/{course_id}/assignments/{assignment_id}', {'include[]': 'submission'})
    if not isinstance(raw, dict) or str(raw.get('id')) != assignment_id or str(raw.get('course_id')) != course_id:
        raise SyncError("未能验证课程作业。", code='query_failed')
    submission = raw.get('submission')
    if not isinstance(submission, dict):
        submission, _ = client.get(f'/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions/self')
    if not isinstance(submission, dict):
        raise SyncError("未能读取当前提交状态。")
    attempt = submission.get('attempt')
    if attempt is None and submission.get('workflow_state') == 'unsubmitted':
        attempt = 0
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 0:
        raise SyncError("当前提交次数无效。")
    return {key: raw.get(key) for key in ('name', 'description', 'submission_types', 'allowed_extensions',
            'due_at', 'unlock_at', 'lock_at', 'locked_for_user', 'allowed_attempts', 'has_submitted_submissions')} | {
        'course_id': course_id, 'assignment_id': assignment_id,
        'page_url': f'{client.base}/courses/{course_id}/assignments/{assignment_id}',
        'submission': {'attempt': attempt, **{key: submission.get(key) for key in (
            'workflow_state', 'submitted_at', 'late', 'missing', 'submission_type')},
            'attachments': [{'id': str(row['id']), 'filename': row.get('display_name') or row.get('filename')}
                            for row in submission.get('attachments') or [] if isinstance(row, dict) and row.get('id')]}}


def submit_assignment(client, store, course_id, assignment_id, file_paths, expected_attempt, check_cancelled):
    if isinstance(expected_attempt, bool) or not isinstance(expected_attempt, int) or expected_attempt < 0:
        raise ValueError("expected_attempt 必须是当前提交次数（未提交为 0）")
    if (not isinstance(file_paths, list) or not 1 <= len(file_paths) <= 20
            or any(not isinstance(value, str) for value in file_paths) or len(set(file_paths)) != len(file_paths)):
        raise ValueError("请选择 1 至 20 个不同的本地文件")
    paths = [local_path(value) for value in file_paths]
    # Serialize attempts within this profile; never replay a write after a timeout.
    with state_lock(store.root, getattr(client, 'cancel_event', None)), ExitStack() as stack:
        details = read_assignment(client, course_id, assignment_id)
        current = details['submission']['attempt']
        if current != expected_attempt:
            raise SyncError("提交次数已变化，请重新查询作业状态后决定是否重交。", code='attempt_conflict')
        if details['locked_for_user']:
            raise SyncError("作业尚未开放或已经锁定。", code='assignment_unavailable')
        if 'online_upload' not in (details['submission_types'] or []):
            raise SyncError("此作业不支持文件上传，请使用作业指定的提交方式。", code='unsupported_submission')
        allowed = details['allowed_attempts']
        if isinstance(allowed, int) and allowed >= 0 and current >= allowed:
            raise SyncError("作业允许的提交次数已用完。", code='assignment_unavailable')
        extensions = {str(value).lstrip('.').lower() for value in details['allowed_extensions'] or []}
        files = []
        for path in paths:
            if extensions and path.suffix.lstrip('.').lower() not in extensions:
                raise SyncError("所选文件格式不符合作业要求。", code='invalid_file')
            directory = _open_directory(path.parent)
            try:
                descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            except OSError:
                raise SyncError("上传源必须是可读取的普通文件，不能使用符号链接。", code='invalid_file') from None
            finally:
                os.close(directory)
            stream = stack.enter_context(os.fdopen(descriptor, 'rb'))
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or not 0 <= info.st_size <= _MAX_FILE_SIZE:
                raise SyncError("上传源必须是 512 MB 以内的普通文件。", code='invalid_file')
            files.append((path, stream, info))
        uploaded = []
        endpoint = f'/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions'
        for path, stream, info in files:
            check_cancelled()
            result = client.upload_file(endpoint + '/self/files', stream, path.name, info.st_size)
            after = os.fstat(stream.fileno())
            if (info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise SyncError("文件在上传期间发生变化，未提交作业。", code='invalid_file')
            if not isinstance(result, dict) or not re.fullmatch(r'[1-9][0-9]*', str(result.get('id', ''))):
                raise SyncError("未能确认文件上传成功，未提交作业。", code='upload_failed')
            uploaded.append(str(result['id']))
        check_cancelled()
        # Another browser may have submitted while these files were uploading.
        if read_assignment(client, course_id, assignment_id)['submission']['attempt'] != current:
            raise SyncError("提交次数已变化，文件已上传但未提交作业。", code='attempt_conflict')
        result = client.post(endpoint, {'submission[submission_type]': 'online_upload', 'submission[file_ids][]': uploaded})
        attachments = result.get('attachments') if isinstance(result, dict) else None
        if (not isinstance(result, dict) or str(result.get('assignment_id')) != assignment_id
                or result.get('submission_type') != 'online_upload' or not result.get('submitted_at')
                or result.get('workflow_state') not in ('submitted', 'pending_review', 'graded')
                or result.get('attempt') != current + 1
                or not isinstance(attachments, list) or any(not isinstance(row, dict) for row in attachments)
                or {str(row.get('id')) for row in attachments} != set(uploaded)):
            raise SyncError("提交请求已发送，但结果尚不能确认；请查询状态，勿直接重试。", code='submission_unknown')
        return {'status': 'submitted', 'course_id': course_id, 'assignment_id': assignment_id,
                'attempt': result['attempt'], 'submitted_at': result['submitted_at'], 'late': result.get('late'),
                'file_ids': uploaded, 'page_url': details['page_url']}
