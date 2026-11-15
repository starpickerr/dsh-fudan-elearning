import http.cookiejar
import io
import json
import socket
import ssl
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.request

from elearning_mcp.client import Canvas, SafeRedirect, transfer_url
from elearning_mcp.errors import SyncError

BASE = 'https://elearning.fudan.edu.cn'


class Response(io.BytesIO):
    def __init__(self, value=None, status=200, headers=None):
        super().__init__(json.dumps(value).encode())
        self.status = status
        self.headers = {'Content-Type': 'application/json', **(headers or {})}


class ClientTest(unittest.TestCase):
    def setUp(self):
        self.client = Canvas({'base_url': BASE, 'token': 'synthetic-token', 'request_interval': 0}, Mock(), auto_login=False)

    def test_post_sends_csrf_with_cookies_or_token_without_retry(self):
        opener = Mock()
        opener.open.return_value = Response({'id': 1})
        with patch('urllib.request.build_opener', return_value=opener):
            self.assertEqual(self.client.post('/api/v1/example', {'values[]': ['1', '2']}), {'id': 1})
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_header('Authorization'), 'Bearer synthetic-token')
        self.assertEqual(request.data, b'values%5B%5D=1&values%5B%5D=2')
        self.client.token = ''
        self.client.cookie_jar.set_cookie(http.cookiejar.Cookie(
            0, '_csrf_token', 'synthetic%2Bcsrf', None, False, 'elearning.fudan.edu.cn', False, False,
            '/', True, True, None, True, None, None, {}, False))
        opener.open.return_value = Response({'id': 2})
        with patch('urllib.request.build_opener', return_value=opener):
            self.client.post('/api/v1/example', {})
        self.assertEqual(opener.open.call_args.args[0].get_header('X-csrf-token'), 'synthetic+csrf')
        for error, code in [(TimeoutError(), 'submission_unknown'),
                            (urllib.error.HTTPError(BASE, 503, 'service error', {}, None), 'submission_unknown'),
                            (urllib.error.HTTPError(BASE, 403, 'denied', {}, None), 'submission_rejected')]:
            opener.reset_mock()
            opener.open.side_effect = error
            with patch('urllib.request.build_opener', return_value=opener), self.assertRaises(SyncError) as caught:
                self.client.post('/api/v1/example', {})
            self.assertEqual(caught.exception.code, code)
            self.assertEqual(opener.open.call_count, 1)

    def test_streamed_multipart_file_last_no_canvas_credentials_and_redirect_confirmed(self):
        seen = []
        target = 'https://files.example.com/upload'
        confirmation = BASE + '/api/v1/files/101/create_success?uuid=synthetic'

        def upload(request, timeout):
            seen.append(request)
            content = b''.join(request.data)
            self.assertIn(b'name="policy"\r\n\r\nopaque', content)
            self.assertIn('filename="答案.pdf"'.encode(), content)
            self.assertIn(b'\x00binary\xff', content)
            self.assertLess(content.index(b'name="policy"'), content.index(b'name="file"'))
            self.assertEqual(int(request.get_header('Content-length')), len(content))
            self.assertIsNone(request.get_header('Cookie'))
            self.assertIsNone(request.get_header('Authorization'))
            self.assertIsNone(request.get_header('X-csrf-token'))
            raise urllib.error.HTTPError(target, 303, 'see other', {'Location': confirmation}, io.BytesIO())

        opener = Mock()
        opener.open.side_effect = upload
        with patch.object(self.client, 'post', return_value={'upload_url': target, 'upload_params': {'policy': 'opaque'}}), \
             patch.object(self.client, 'get', return_value=({'id': 101}, None)) as get, \
             patch('urllib.request.build_opener', return_value=opener), \
             patch('socket.getaddrinfo', return_value=[(2, 1, 6, '', ('93.184.216.34', 443))]):
            self.assertEqual(self.client.upload_file('/prepare', io.BytesIO(b'\x00binary\xff'), '答案.pdf', 8), {'id': 101})
        get.assert_called_once_with(confirmation)
        self.assertEqual(len(seen), 1)

    def test_upload_accepts_201_json_and_rejects_untrusted_confirmation(self):
        opener = Mock()
        params = {'upload_url': BASE + '/upload', 'upload_params': {}}
        with patch.object(self.client, 'post', return_value=params), patch('urllib.request.build_opener', return_value=opener):
            opener.open.return_value = Response({'id': 101}, 201)
            self.assertEqual(self.client.upload_file('/prepare', io.BytesIO(b'file'), 'a.pdf', 4), {'id': 101})
            for location in ['http://elearning.fudan.edu.cn/api/v1/files/101', 'https://evil.invalid/api/v1/files/101', BASE + '/login']:
                opener.open.return_value = Response(None, 201, {'Location': location})
                with self.assertRaises(SyncError):
                    self.client.upload_file('/prepare', io.BytesIO(b'file'), 'a.pdf', 4)

    def test_transfer_targets_reject_private_network_and_redirects_strip_secrets(self):
        for url in ['http://files.example.com/x', 'https://user:secret@files.example.com/x', 'https://127.0.0.1/x']:
            with self.subTest(url=url), patch('socket.getaddrinfo', return_value=[(2, 1, 6, '', ('127.0.0.1', 443))]), self.assertRaises(SyncError):
                transfer_url(url, self.client.origin)
        request = urllib.request.Request(BASE + '/files/1/download', headers={
            'Authorization': 'secret', 'Cookie': 'secret', 'X-CSRF-Token': 'secret'})
        with patch('socket.getaddrinfo', return_value=[(2, 1, 6, '', ('93.184.216.34', 443))]):
            redirected = SafeRedirect().redirect_request(request, None, 302, '', {}, 'https://files.example.com/file')
        self.assertFalse(any(key.lower() in ('authorization', 'cookie', 'x-csrf-token') for key, _ in redirected.header_items()))
        with patch('socket.getaddrinfo', return_value=[(2, 1, 6, '', ('10.0.0.1', 443))]), self.assertRaises(SyncError):
            SafeRedirect().redirect_request(request, None, 302, '', {}, 'https://private.example.com/file')

    def test_transfer_dns_distinguishes_proxy_fake_ip_from_private_targets(self):
        for address, code in [('198.18.0.25', 'proxy_dns_fake_ip'),
                              ('198.19.255.254', 'proxy_dns_fake_ip'),
                              ('127.0.0.1', 'file_server_blocked'),
                              ('10.0.0.1', 'file_server_blocked')]:
            with self.subTest(address=address), patch('socket.getaddrinfo',
                    return_value=[(2, 1, 6, '', (address, 443))]):
                with self.assertRaises(SyncError) as error:
                    transfer_url('https://files.example.com/file?token=secret', self.client.origin)
                self.assertEqual(error.exception.code, code)
        with patch('socket.getaddrinfo', side_effect=socket.gaierror()):
            with self.assertRaises(SyncError) as error:
                transfer_url('https://files.example.com/file', self.client.origin)
            self.assertEqual(error.exception.code, 'network_dns_error')

    def test_read_failures_report_network_or_school_category(self):
        failures = [(urllib.error.URLError(socket.gaierror()), 'network_dns_error'),
                    (urllib.error.URLError(ssl.SSLError()), 'network_tls_error'),
                    (urllib.error.URLError(TimeoutError()), 'network_timeout'),
                    (PermissionError(), 'network_permission_denied'),
                    (urllib.error.HTTPError(BASE, 403, 'secret', {}, None), 'school_access_denied'),
                    (urllib.error.HTTPError(BASE, 404, 'secret', {}, None), 'school_file_unavailable')]
        for failure, code in failures:
            with self.subTest(code=code), patch.object(self.client.opener, 'open', side_effect=failure), \
                    patch.object(self.client, 'wait'):
                with self.assertRaises(SyncError) as error:
                    self.client.open('/api/v1/files/10')
                self.assertEqual(error.exception.code, code)


if __name__ == '__main__':
    unittest.main()
