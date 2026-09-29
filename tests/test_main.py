import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main
from src import client, service, ui, utils
from src.errors import LoginRequired, SyncError
from tests.test_service import FakeCanvas


class EntryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env_file = self.root / ".env"
        self.env_patch = patch.object(utils, "ENV_FILE", self.env_file)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.addCleanup(self.tmp.cleanup)
        private_patch = patch.object(utils, "PRIVATE", self.root / ".private")
        private_patch.start()
        self.addCleanup(private_patch.stop)
        logger_patch = patch.object(main, "setup_logging")
        logger_patch.start()
        self.addCleanup(logger_patch.stop)
        startup_patch = patch.object(client, "ensure_login")
        startup_patch.start()
        self.addCleanup(startup_patch.stop)
        catalog_patch = patch.object(service, "course_list", return_value=[
            {"id": 42, "name": "计算机网络"}, {"id": 43, "name": "操作系统"}])
        catalog_patch.start()
        self.addCleanup(catalog_patch.stop)
        self.config_path = self.root / "config.yaml"
        utils.save_config(self.config_path, {"base_url": FakeCanvas.base, "courses": ["42"]})


    def test_manual_login_does_not_read_env(self):
        with patch.object(client, "read_login_credentials") as read, patch.object(client, "login") as login, \
                patch.object(utils, "PRIVATE", self.root / ".private"):
            self.assertEqual(main.main(["login", "--manual-login", "--config", str(self.config_path)]), 0)
            login.assert_called_once_with({"base_url": FakeCanvas.base, "courses": ["42"]}, manual=True)
            read.assert_not_called()

    def test_menu_routes_pages_and_reloads_selected_courses(self):
        def configure(config, path):
            config["courses"] = ["43"]
            utils.save_config(path, config)
            return 0

        output = io.StringIO()
        with patch("builtins.input", side_effect=["invalid", "2", "3", "4", "0"]), \
                patch.object(ui, "courses_menu", side_effect=configure), \
                patch.object(ui, "assignments_menu", return_value=0) as assignments, \
                patch.object(ui, "files_menu", return_value=0) as files, \
                patch.object(utils, "PRIVATE", self.root / ".private"), contextlib.redirect_stdout(output):
            self.assertEqual(main.main(["--config", str(self.config_path)]), 0)
        self.assertEqual(assignments.call_args.args[0]["courses"], ["43"])
        self.assertEqual(files.call_args.args[0]["courses"], ["43"])
        self.assertIn("当前选择的课程：\n\n- 计算机网络", output.getvalue())
        self.assertIn("当前选择的课程：\n\n- 操作系统", output.getvalue())
        self.assertIn("0. 退出", output.getvalue())

    def test_menu_recovers_from_failed_action(self):
        output = io.StringIO()
        with patch("builtins.input", side_effect=["1", "3", "0"]), \
                patch.object(main, "execute_command", side_effect=[LoginRequired("请重新登录"), 0]) as execute, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(output):
            self.assertEqual(main.main(["--config", str(self.config_path)]), 0)
        self.assertEqual(execute.call_count, 2)
        self.assertIn("请重新登录", output.getvalue())

    def test_new_pages_are_routed_and_zero_exits(self):
        output = io.StringIO()
        with patch("builtins.input", side_effect=["5", "6", "7", "8", "0"]), \
                patch.object(ui, "resources_menu", return_value=0) as pages, contextlib.redirect_stdout(output):
            self.assertEqual(main.main(["--config", str(self.config_path), "--dry-run"]), 0)
        self.assertEqual([call.args[1] for call in pages.call_args_list], ["announcements", "people", "quizzes", "grades"])
        self.assertTrue(all(call.kwargs["dry_run"] for call in pages.call_args_list))
        self.assertIn("0. 退出", output.getvalue())
        self.assertNotIn("5. 退出", output.getvalue())

    def test_resource_commands_use_unified_entrypoint(self):
        for command in ("announcements", "people", "quizzes", "grades"):
            with self.subTest(command=command), patch.object(ui, "resources_menu", return_value=0) as page:
                self.assertEqual(main.main([command, "--config", str(self.config_path)]), 0)
                self.assertEqual(page.call_args.args[1], command)

    def test_direct_sync_preserves_exit_codes(self):
        with patch.object(utils, "PRIVATE", self.root / ".private"), patch.object(service, "sync", return_value=True) as sync:
            self.assertEqual(main.main(["sync", "--dry-run", "--config", str(self.config_path)]), 2)
            self.assertTrue(sync.call_args.args[1])

    def test_menu_authenticates_before_accepting_choices(self):
        events = []
        def choose(prompt):
            events.append("menu")
            return "0"
        with patch.object(client, "ensure_login", side_effect=lambda config: events.append("authenticated")), \
                patch.object(utils, "PRIVATE", self.root / ".private"), \
                patch("builtins.input", side_effect=choose), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main.main(["--config", str(self.config_path)]), 0)
        self.assertEqual(events, ["authenticated", "menu"])

    def test_startup_login_failure_does_not_open_menu(self):
        with patch.object(client, "ensure_login", side_effect=LoginRequired("自动登录未成功")), \
                patch.object(utils, "PRIVATE", self.root / ".private"), patch("builtins.input") as choose, \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main.main(["--config", str(self.config_path)]), 1)
        choose.assert_not_called()

    def test_logs_record_error_type_without_secret_details(self):
        log_file = self.root / "logs/elearning.log"
        with patch.object(main, "setup_logging", side_effect=lambda: utils.setup_logging(log_file)), \
                patch.object(main, "execute_command", side_effect=SyncError("sensitive-test-value")), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main.main(["sync"]), 1)
        text = log_file.read_text()
        self.assertIn("SyncError", text)
        self.assertNotIn("sensitive-test-value", text)
        for handler in main.LOGGER.handlers[:]:
            handler.close()
            main.LOGGER.removeHandler(handler)


if __name__ == "__main__":
    unittest.main()
