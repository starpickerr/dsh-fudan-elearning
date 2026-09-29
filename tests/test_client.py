import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src import client, utils
from src.errors import LoginRequired, SyncError
from tests.test_service import FakeCanvas


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env_file = self.root / ".env"
        env_patch = patch.object(utils, "ENV_FILE", self.env_file)
        env_patch.start()
        self.addCleanup(env_patch.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_credentials_keep_special_characters_and_do_not_export(self):
        self.env_file.write_text("ELEARNING_USERNAME=' 123456 '\n"
                                 "ELEARNING_PASSWORD='p #=$HOME${UNDEFINED} \\\\ \\' '\n", encoding="utf-8")
        with patch.dict(utils.os.environ, {}, clear=True):
            self.assertEqual(client.read_login_credentials(), ("123456", "p #=$HOME${UNDEFINED} \\ ' "))
            self.assertNotIn("ELEARNING_PASSWORD", utils.os.environ)
        self.assertEqual(self.env_file.stat().st_mode & 0o777, 0o600)


    def test_missing_and_incomplete_credentials_fail_without_values(self):
        with self.assertRaisesRegex(SyncError, "缺少 .env"):
            client.read_login_credentials()
        for text in ("ELEARNING_USERNAME='private-user'\n", "ELEARNING_PASSWORD='private-password'\n",
                     "ELEARNING_USERNAME=\nELEARNING_PASSWORD=\n"):
            self.env_file.write_text(text)
            with self.assertRaises(SyncError) as caught:
                client.read_login_credentials()
            self.assertNotIn("private-user", str(caught.exception))
            self.assertNotIn("private-password", str(caught.exception))


    def test_login_uses_env_and_saves_only_canvas_cookies(self):
        self.env_file.write_text("ELEARNING_USERNAME='test-user'\nELEARNING_PASSWORD='test-password'\n")
        playwright = MagicMock()
        browser = playwright.chromium.launch.return_value
        context = browser.new_context.return_value
        page = context.new_page.return_value
        page.url = "https://id.fudan.edu.cn/authserver/login"
        page.is_closed.return_value = False
        page.get_by_role.return_value.click.side_effect = lambda **kwargs: setattr(page, "url", FakeCanvas.base)
        canvas_cookie = {"domain": "elearning.fudan.edu.cn", "name": "session", "value": "test-cookie"}
        context.cookies.return_value = [canvas_cookie, {"domain": "id.fudan.edu.cn", "name": "sso"}]
        output = io.StringIO()
        with patch("playwright.sync_api.sync_playwright") as factory, \
                patch.object(client.Canvas, "get", return_value=({"id": 99}, None)) as verify, \
                patch.object(client, "PRIVATE", self.root / ".private"), contextlib.redirect_stdout(output):
            factory.return_value.__enter__.return_value = playwright
            client.login({"base_url": FakeCanvas.base})
        page.locator.return_value.fill.assert_any_call("test-user", timeout=20000)
        page.locator.return_value.fill.assert_any_call("test-password", timeout=20000)
        saved = (self.root / ".private/session.json").read_text()
        self.assertEqual(json.loads(saved), {"cookies": [dict(canvas_cookie, path="/", secure=True, expires=-1)]})
        for secret in ("test-user", "test-password"):
            self.assertNotIn(secret, output.getvalue())
            self.assertNotIn(secret, saved)
        browser.close.assert_called_once()
        verify.assert_called_once_with("/api/v1/users/self/profile")
        context.request.get.assert_not_called()

    def test_env_token_authenticates_without_saved_session(self):
        self.env_file.write_text("CANVAS_TOKEN='file-token'\n")
        with patch.dict(utils.os.environ, {}, clear=True), patch.object(client, "PRIVATE", self.root / ".private"):
            canvas = client.Canvas({"base_url": FakeCanvas.base})
        self.assertEqual(canvas.token, "file-token")
        self.assertFalse((self.root / ".private").exists())

    def test_system_token_takes_precedence_and_does_not_need_env(self):
        self.env_file.write_text("CANVAS_TOKEN='file-token'\n")
        with patch.dict(utils.os.environ, {"CANVAS_TOKEN": "system-token"}), \
                patch.object(client, "PRIVATE", self.root / ".private"), patch.object(client, "read_secrets") as read:
            canvas = client.Canvas({"base_url": FakeCanvas.base})
        self.assertEqual(canvas.token, "system-token")
        read.assert_not_called()

    def test_manual_login_works_without_credentials_file(self):
        playwright = MagicMock()
        browser = playwright.chromium.launch.return_value
        context = browser.new_context.return_value
        page = context.new_page.return_value
        page.url = FakeCanvas.base
        page.is_closed.return_value = False
        context.cookies.return_value = [{"domain": "elearning.fudan.edu.cn", "name": "session", "value": "test-cookie"}]
        with patch("playwright.sync_api.sync_playwright") as factory, \
                patch.object(client.Canvas, "get", return_value=({"id": 99}, None)), \
                patch.object(client, "PRIVATE", self.root / ".private"), \
                patch.object(client, "read_login_credentials") as read, contextlib.redirect_stdout(io.StringIO()):
            factory.return_value.__enter__.return_value = playwright
            client.login({"base_url": FakeCanvas.base}, manual=True)
        read.assert_not_called()
        page.locator.assert_not_called()
        browser.close.assert_called_once()

    def test_success_redirect_waits_for_authenticated_profile(self):
        playwright = MagicMock()
        browser = playwright.chromium.launch.return_value
        context = browser.new_context.return_value
        page = context.new_page.return_value
        page.url = FakeCanvas.base + "/login?success=true"
        page.is_closed.return_value = False
        context.cookies.return_value = [{"domain": "elearning.fudan.edu.cn", "name": "session", "value": "test-cookie"}]
        with patch("playwright.sync_api.sync_playwright") as factory, \
                patch.object(client.Canvas, "get", side_effect=[LoginRequired("跳转未完成"), ({"id": 99}, None)]) as verify, \
                patch.object(client, "PRIVATE", self.root / ".private"), contextlib.redirect_stdout(io.StringIO()):
            factory.return_value.__enter__.return_value = playwright
            client.login({"base_url": FakeCanvas.base}, manual=True)
        self.assertEqual(verify.call_count, 2)
        page.wait_for_timeout.assert_called_once_with(1500)
        self.assertTrue((self.root / ".private/session.json").is_file())

    def test_failed_profile_verification_never_saves_session(self):
        playwright = MagicMock()
        browser = playwright.chromium.launch.return_value
        context = browser.new_context.return_value
        page = context.new_page.return_value
        page.url = FakeCanvas.base + "/login?success=true"
        page.is_closed.return_value = False
        context.cookies.return_value = [{"domain": "elearning.fudan.edu.cn", "name": "session", "value": "test-cookie"}]
        output = io.StringIO()
        with patch("playwright.sync_api.sync_playwright") as factory, \
                patch.object(client.Canvas, "get", side_effect=SyncError("连接失败")), \
                patch.object(client, "PRIVATE", self.root / ".private"), contextlib.redirect_stdout(output):
            factory.return_value.__enter__.return_value = playwright
            with self.assertRaisesRegex(SyncError, "验证登录状态失败"):
                client.login({"base_url": FakeCanvas.base}, manual=True)
        self.assertFalse((self.root / ".private/session.json").exists())
        self.assertNotIn("登录成功", output.getvalue())
        browser.close.assert_called_once()

    def test_saved_cookies_are_reused_and_token_does_not_mask_verification(self):
        cookies = [{"domain": ".fudan.edu.cn", "name": "session", "value": "cookie-for-test", "secure": True},
                   {"domain": "id.fudan.edu.cn", "name": "sso", "value": "do-not-save"}]
        with patch.dict(utils.os.environ, {"CANVAS_TOKEN": "token-for-test"}), \
                patch.object(client, "PRIVATE", self.root / ".private"):
            canvas = client.Canvas({"base_url": FakeCanvas.base}, cookies=cookies)
            self.assertEqual(canvas.token, "")
            utils.save_json(self.root / ".private/session.json", canvas.session_data(), private=True)
        with patch.dict(utils.os.environ, {}, clear=True), patch.object(client, "PRIVATE", self.root / ".private"):
            restored = client.Canvas({"base_url": FakeCanvas.base})
        request = client.urllib.request.Request(FakeCanvas.base + "/api/v1/users/self/profile")
        restored.cookie_jar.add_cookie_header(request)
        self.assertEqual(request.get_header("Cookie"), "session=cookie-for-test")
        self.assertEqual(len(restored.session_data()["cookies"]), 1)

    def test_invalid_or_empty_session_requests_relogin(self):
        with patch.dict(utils.os.environ, {}, clear=True), patch.object(client, "PRIVATE", self.root / ".private"):
            for data in ({"cookies": []}, {}, {"cookies": "bad"}):
                utils.save_json(self.root / ".private/session.json", data, private=True)
                with self.assertRaises(LoginRequired):
                    client.Canvas({"base_url": FakeCanvas.base}, auto_login=False)


if __name__ == "__main__":
    unittest.main()
