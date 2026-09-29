import contextlib
import io
import json
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import main
from src import client, service, utils
from src.errors import LoginRequired, SyncError


BASE = "https://elearning.fudan.edu.cn"
PROFILE_URL = BASE + "/api/v1/users/self/profile"


class APIResponse(io.BytesIO):
    def __init__(self, content_type="application/json", url=PROFILE_URL):
        super().__init__(json.dumps({"id": 99}).encode())
        self.headers = {"Content-Type": content_type}
        self.url = url

    def geturl(self):
        return self.url


class AutoLoginTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = {"base_url": BASE, "courses": ["42"], "request_interval": 0}
        self.addCleanup(self.tmp.cleanup)
        for owner, name, value in ((client, "PRIVATE", self.root / ".private"),
                                   (utils, "PRIVATE", self.root / ".private"),
                                   (utils, "ENV_FILE", self.root / ".env")):
            patcher = patch.object(owner, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        env_patch = patch.dict(client.os.environ, {"CANVAS_TOKEN": ""})
        env_patch.start()
        self.addCleanup(env_patch.stop)

    def save_session(self, value="old-cookie", expiry=-1):
        utils.save_json(self.root / ".private/session.json", {"cookies": [
            {"domain": "elearning.fudan.edu.cn", "name": "session", "value": value,
             "expires": expiry, "secure": True, "path": "/"}]}, private=True)

    def renew_session(self, config):
        self.save_session(value="new-cookie")

    def test_startup_without_cookies_logs_in_and_verifies_profile(self):
        opener = MagicMock()
        opener.open.return_value = APIResponse()
        with patch.object(client, "login", side_effect=self.renew_session) as login, \
                patch.object(client.urllib.request, "build_opener", return_value=opener), \
                contextlib.redirect_stdout(io.StringIO()):
            canvas = client.ensure_login(self.config)
        login.assert_called_once_with(self.config)
        self.assertEqual(canvas.session_data()["cookies"][0]["value"], "new-cookie")
        self.assertEqual(opener.open.call_args.args[0].full_url, PROFILE_URL)

    def test_expired_cookie_metadata_triggers_login(self):
        self.save_session(expiry=time.time() - 60)
        with patch.object(client, "login", side_effect=self.renew_session) as login, \
                contextlib.redirect_stdout(io.StringIO()):
            canvas = client.Canvas(self.config)
        login.assert_called_once_with(self.config)
        self.assertEqual(canvas.session_data()["cookies"][0]["value"], "new-cookie")

    def test_valid_cookie_is_reused_without_opening_browser(self):
        self.save_session()
        opener = MagicMock()
        opener.open.return_value = APIResponse()
        with patch.object(client, "login") as login, \
                patch.object(client.urllib.request, "build_opener", return_value=opener):
            client.ensure_login(self.config)
        login.assert_not_called()

    def test_http_401_renews_cookie_and_retries_original_request(self):
        self.save_session()
        old, renewed = MagicMock(), MagicMock()
        old.open.side_effect = urllib.error.HTTPError(PROFILE_URL, 401, "Unauthorized", {}, None)
        renewed.open.return_value = APIResponse()
        with patch.object(client, "login", side_effect=self.renew_session) as login, \
                patch.object(client.urllib.request, "build_opener", side_effect=[old, renewed]), \
                contextlib.redirect_stdout(io.StringIO()):
            canvas = client.Canvas(self.config)
            profile, _ = canvas.get("/api/v1/users/self/profile", {"include": "details"})
        self.assertEqual(profile["id"], 99)
        login.assert_called_once_with(self.config)
        self.assertEqual(old.open.call_args.args[0].full_url, renewed.open.call_args.args[0].full_url)
        self.assertIn("include=details", renewed.open.call_args.args[0].full_url)
        self.assertEqual(canvas.session_data()["cookies"][0]["value"], "new-cookie")

    def test_login_pages_trigger_recovery_for_api_and_download(self):
        for api in (True, False):
            with self.subTest(api=api):
                self.save_session()
                old, renewed = MagicMock(), MagicMock()
                login_page = APIResponse("text/html", BASE + "/login")
                old.open.return_value = login_page
                renewed.open.return_value = APIResponse("application/json" if api else "application/pdf")
                with patch.object(client, "login", side_effect=self.renew_session) as login, \
                        patch.object(client.urllib.request, "build_opener", side_effect=[old, renewed]), \
                        contextlib.redirect_stdout(io.StringIO()):
                    canvas = client.Canvas(self.config)
                    with canvas.open(PROFILE_URL, api=api):
                        pass
                login.assert_called_once_with(self.config)
                self.assertTrue(login_page.closed)

    def test_second_authentication_failure_stops_after_one_login(self):
        self.save_session()
        old, renewed = MagicMock(), MagicMock()
        for opener in (old, renewed):
            opener.open.side_effect = urllib.error.HTTPError(PROFILE_URL, 401, "Unauthorized", {}, None)
        with patch.object(client, "login", side_effect=self.renew_session) as login, \
                patch.object(client.urllib.request, "build_opener", side_effect=[old, renewed]), \
                contextlib.redirect_stdout(io.StringIO()):
            canvas = client.Canvas(self.config)
            with self.assertRaises(LoginRequired):
                canvas.get("/api/v1/users/self/profile")
        login.assert_called_once_with(self.config)
        self.assertEqual(renewed.open.call_count, 1)

    def test_html_and_json_attachments_do_not_trigger_relogin(self):
        self.save_session()
        for kind in ("application/json", "text/html"):
            with self.subTest(kind=kind):
                opener = MagicMock()
                response = APIResponse(kind)
                response.headers["Content-Disposition"] = 'attachment; filename="data"'
                opener.open.return_value = response
                with patch.object(client, "login") as login, \
                        patch.object(client.urllib.request, "build_opener", return_value=opener):
                    canvas = client.Canvas(self.config)
                    with canvas.open(BASE + "/files/1/download", api=False):
                        pass
                login.assert_not_called()

    def test_network_failure_does_not_trigger_login(self):
        self.save_session()
        opener = MagicMock()
        opener.open.side_effect = urllib.error.URLError("network unavailable")
        with patch.object(client, "login") as login, patch.object(client.time, "sleep"), \
                patch.object(client.urllib.request, "build_opener", return_value=opener):
            canvas = client.Canvas(self.config)
            with self.assertRaises(SyncError):
                canvas.get("/api/v1/users/self/profile")
        login.assert_not_called()

    def test_verification_and_token_requests_do_not_trigger_recursive_login(self):
        opener = MagicMock()
        opener.open.side_effect = urllib.error.HTTPError(PROFILE_URL, 401, "Unauthorized", {}, None)
        self.save_session()
        cookies = json.loads((self.root / ".private/session.json").read_text())["cookies"]
        with patch.object(client, "login") as login, \
                patch.object(client.urllib.request, "build_opener", return_value=opener):
            canvas = client.Canvas(self.config, cookies=cookies)
            with self.assertRaises(LoginRequired):
                canvas.get("/api/v1/users/self/profile")
            with patch.dict(client.os.environ, {"CANVAS_TOKEN": "token-for-test"}):
                canvas = client.Canvas(self.config)
                with self.assertRaises(LoginRequired):
                    canvas.get("/api/v1/users/self/profile")
        login.assert_not_called()

    def test_failed_auto_login_propagates_without_retry_loop(self):
        with patch.object(client, "login", side_effect=SyncError("凭据不可用")) as login, \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(LoginRequired, "自动登录未成功"):
                client.Canvas(self.config)
        login.assert_called_once_with(self.config)

    def test_watch_continues_after_automatic_login(self):
        self.save_session(expiry=time.time() - 60)
        path = self.root / "config.yaml"
        utils.save_config(path, self.config)
        opener = MagicMock()
        opener.open.return_value = APIResponse()

        def sync(config, dry_run):
            client.ensure_login(config)
            return False

        def sleep(seconds):
            if seconds >= 300:
                raise KeyboardInterrupt

        with patch.object(client, "login", side_effect=self.renew_session) as login, \
                patch.object(client.urllib.request, "build_opener", return_value=opener), \
                patch.object(service, "sync", side_effect=sync) as sync_mock, \
                patch.object(main, "setup_logging"), patch.object(main.time, "sleep", side_effect=sleep), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main.main(["watch", "--config", str(path)]), 130)
        login.assert_called_once_with(self.config)
        sync_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
