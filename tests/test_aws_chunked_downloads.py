"""School OBS leaked AWS chunk framing must not become courseware bytes."""
import base64
import hashlib
import io
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import zlib

from tests.test_assignment_transfers import Canvas, Response, BASE
from elearning_mcp.errors import SyncError
from elearning_mcp.transfers import download_file


def aws_chunks(body):
    chunks = [body[start:start + 7] for start in range(0, len(body), 7)]
    crc = base64.b64encode(zlib.crc32(body).to_bytes(4, 'big'))
    return b''.join(f'{len(part):x}\r\n'.encode() + part + b'\r\n' for part in chunks) + b'0\r\nx-amz-checksum-crc32:' + crc + b'\r\n\r\n'


class AwsChunkedDownloadTest(unittest.TestCase):
    def test_verified_aws_framing_is_decoded_before_metadata_and_hash_validation(self):
        content = b'%PDF-1.3\nsynthetic course document\n%%EOF\n'
        canvas = Canvas()
        canvas.file['size'] = len(content)
        wire = aws_chunks(content)
        with tempfile.TemporaryDirectory() as directory, patch.object(canvas, 'open', side_effect=lambda *a, **k: Response(wire)):
            path = Path(directory).resolve() / 'lecture.pdf'
            receipt = download_file(canvas, '42', '10', str(path), lambda: None)
            self.assertEqual(path.read_bytes(), content)
            self.assertEqual(receipt['size'], len(content))
            self.assertEqual(receipt['sha256'], hashlib.sha256(content).hexdigest())
            self.assertFalse(list(Path(directory).glob('.elearning-*')))

    def test_bad_checksum_truncated_or_extra_framing_never_publishes_a_file(self):
        content = b'%PDF-1.3\nsynthetic\n%%EOF\n'
        wire = aws_chunks(content)
        cases = [wire.replace(b'%PDF', b'%PDX', 1), wire[:-4], wire + b'extra',
                 wire.replace(b'x-amz-checksum-crc32:', b'x-other-checksum:'),
                 b'ffffffffff\r\n' + wire, b'0\r\nx-amz-checksum-crc32:AAAAAA==\r\n\r\n']
        for body in cases:
            with self.subTest(body=body[:16]), tempfile.TemporaryDirectory() as directory:
                canvas = Canvas(); canvas.file['size'] = len(content)
                path = Path(directory).resolve() / 'lecture.pdf'
                with patch.object(canvas, 'open', side_effect=lambda *a, **k: Response(body)):
                    with self.assertRaises(SyncError) as failure:
                        download_file(canvas, '42', '10', str(path), lambda: None)
                self.assertEqual(failure.exception.code, 'download_integrity_error')
                self.assertFalse(path.exists())
                self.assertFalse(list(Path(directory).glob('.elearning-*')))

    def test_complete_plain_file_is_never_unframed_even_if_its_bytes_look_chunked(self):
        body = aws_chunks(b'a literal captured HTTP response')
        canvas = Canvas(); canvas.file['size'] = len(body)
        with tempfile.TemporaryDirectory() as directory, patch.object(canvas, 'open', side_effect=lambda *a, **k: Response(body)):
            path = Path(directory).resolve() / 'capture.txt'
            receipt = download_file(canvas, '42', '10', str(path), lambda: None)
            self.assertEqual(path.read_bytes(), body)
            self.assertEqual(receipt['size'], len(body))

    def test_cancelled_decoding_keeps_source_and_cleans_decoded_staging(self):
        from elearning_mcp import file_access
        from elearning_mcp.file_sync import _decode_aws_chunks, _open_directory
        from elearning_mcp.errors import SyncCancelled
        body = b'%PDF-1.3\nsynthetic\n%%EOF\n'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); path = root / '.elearning-original'; path.write_bytes(aws_chunks(body))
            descriptor = _open_directory(root)
            try:
                with patch('elearning_mcp.file_sync._check_cancelled', side_effect=[None, SyncCancelled('cancelled')]):
                    with self.assertRaises(SyncCancelled):
                        _decode_aws_chunks(path.name, descriptor, len(body))
            finally:
                file_access.close(descriptor)
            self.assertEqual(path.read_bytes(), aws_chunks(body))
            self.assertEqual(list(root.iterdir()), [path])
