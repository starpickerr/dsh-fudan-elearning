import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import service, ui, utils
from src.errors import SyncError
from tests.test_service import FakeCanvas


class MenuTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.config = {"base_url": FakeCanvas.base, "courses": ["42"], "extensions": [".pdf"],
                       "ignore_patterns": ["*答案*"], "output_dir": str(self.root / "output")}
        self.course = {"id": 42, "name": "计算机网络"}
        FakeCanvas.assignment_error = False
        FakeCanvas.body = b"%PDF-test"
        FakeCanvas.downloads = 0

    def test_course_selection_only_updates_courses(self):
        path = self.root / "config.yaml"
        with patch.object(service, "Canvas", FakeCanvas), patch("builtins.input", side_effect=["bad", "1"]), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(ui.courses_menu(self.config, path), 0)
        saved = utils.load_config(path)
        self.assertEqual(saved["courses"], ["42"])
        self.assertEqual(saved["ignore_patterns"], ["*答案*"])
        self.assertEqual(FakeCanvas.downloads, 0)

    def test_cancelled_course_selection_does_not_write_config(self):
        path = self.root / "config.yaml"
        with patch.object(service, "Canvas", FakeCanvas), patch("builtins.input", return_value="0"), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(ui.courses_menu(self.config, path), 0)
        self.assertFalse(path.exists())

    def test_clear_selection_is_reflected_in_main_menu_summary(self):
        path = self.root / "config.yaml"
        with patch.object(service, "Canvas", FakeCanvas), patch("builtins.input", return_value="clear"), \
                contextlib.redirect_stdout(io.StringIO()):
            ui.courses_menu(self.config, path)
        self.assertEqual(utils.load_config(path)["courses"], [])
        self.assertEqual(ui.selected_course_summary(self.config, [self.course]), "未选择课程")

    def test_assignment_preview_reads_full_requirements_without_writing(self):
        output = io.StringIO()
        with patch.object(service, "Canvas", FakeCanvas), patch("builtins.input", side_effect=["1", "1", "", "0"]), \
                contextlib.redirect_stdout(output):
            self.assertEqual(ui.assignments_menu(self.config), 0)
        self.assertIn("完成第一章习题", output.getvalue())
        self.assertIn("2099-10-02 00:00", output.getvalue())
        self.assertIn("上传文件", output.getvalue())
        self.assertFalse((self.root / "output").exists())
        self.assertEqual(FakeCanvas.downloads, 0)

    def test_assignment_download_is_independent_of_course_file_downloads(self):
        with patch.object(service, "Canvas", FakeCanvas), patch.object(service, "download_assignment", return_value=(self.root / "task.md", 1, [])) as download, \
                patch("builtins.input", side_effect=["2", "1", "", "0"]), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(ui.assignments_menu(self.config), 0)
        self.assertEqual(download.call_count, 1)
        self.assertEqual(download.call_args.args[3]["id"], 20)
        self.assertEqual(FakeCanvas.downloads, 0)

    def test_file_preview_opens_authorized_url_without_local_download(self):
        output = io.StringIO()
        item = {"id": 10, "display_name": "参考答案.pdf", "course_relative_path": "课件/第一章/参考答案.pdf"}
        with patch.object(service, "Canvas", FakeCanvas), \
                patch.object(service, "file_entries", return_value=([(self.course, item)], [])), \
                patch.object(service, "file_preview_url", return_value="https://files.example/preview") as preview, \
                patch.object(ui.webbrowser, "open", return_value=True) as browser, \
                patch("builtins.input", side_effect=["1", "1", "", "0"]), contextlib.redirect_stdout(output):
            self.assertEqual(ui.files_menu(self.config), 0)
        self.assertEqual(preview.call_args.args[1]["id"], 10)
        browser.assert_called_once_with("https://files.example/preview")
        self.assertIn("参考答案.pdf", output.getvalue())
        self.assertIn("文件：课件/第一章/参考答案.pdf", output.getvalue())
        self.assertFalse((self.root / "output").exists())
        self.assertEqual(FakeCanvas.downloads, 0)

    def test_only_explicitly_selected_files_are_downloaded(self):
        with patch.object(service, "Canvas", FakeCanvas), patch("builtins.input", side_effect=["2", "1", "", "0"]), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(ui.files_menu(self.config), 0)
        files = list((self.root / "output").rglob("*.pdf"))
        self.assertEqual([path.name for path in files], ["Lecture 1.pdf"])
        self.assertFalse(list((self.root / "output").rglob("作业*")))
        self.assertEqual(FakeCanvas.downloads, 1)

    def test_file_pagination_preserves_global_selection_numbers(self):
        entries = [(self.course, {"id": index, "display_name": "file{}.pdf".format(index)}) for index in range(1, 26)]
        output = io.StringIO()
        with patch.object(service, "selected_context", return_value=(FakeCanvas({}), [self.course])), \
                patch.object(service, "file_entries", return_value=(entries, [])), \
                patch.object(service, "download_file", return_value=(self.root / "file25.pdf", True)) as download, \
                patch("builtins.input", side_effect=["n", "2", "25", "", "p", "0"]), contextlib.redirect_stdout(output):
            self.assertEqual(ui.files_menu(self.config), 0)
        self.assertEqual(download.call_args.args[3]["id"], 25)
        self.assertIn("第 2/2 页", output.getvalue())
        self.assertIn("25. 计算机网络 / file25.pdf", output.getvalue())

    def test_dry_run_prevents_both_download_actions(self):
        for menu in (ui.files_menu, ui.assignments_menu):
            with self.subTest(menu=menu.__name__), patch.object(service, "Canvas", FakeCanvas), \
                    patch("builtins.input", side_effect=["2", "all", "", "0"]), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(menu(self.config, dry_run=True), 0)
        self.assertFalse((self.root / "output").exists())

    def test_no_selected_courses_gives_actionable_message(self):
        with patch.object(service, "Canvas", FakeCanvas):
            with self.assertRaisesRegex(SyncError, "先进入“课程”"):
                ui.files_menu(dict(self.config, courses=[]))


class AssignmentDownloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.config = {"output_dir": str(self.root / "output")}
        self.course = {"id": 42, "name": "计算机网络"}
        self.assignment = {"id": 20, "name": "作业一", "description":
                           '<a href="/courses/42/files/123/preview">附件</a><a href="https://external.example/files/456">外部链接</a>'}
        self.canvas = FakeCanvas({})
        self.canvas.get = lambda path: ({"id": 123, "display_name": "题目.pdf", "size": len(FakeCanvas.body)}, None)
        FakeCanvas.body = b"%PDF-test"
        FakeCanvas.downloads = 0

    def test_download_exports_requirements_and_canvas_attachments_incrementally(self):
        path, added, issues = service.download_assignment(self.config, self.canvas, self.course, self.assignment)
        self.assertEqual(added, 1)
        self.assertEqual(issues, [])
        self.assertIn("已下载附件", path.read_text())
        self.assertIn("https://external.example/files/456", path.read_text())
        self.assertEqual(len(list(path.parent.rglob("题目.pdf"))), 1)
        state = json.loads((self.root / "output/同步记录.json").read_text())
        self.assertIn("123", state["assignment_files"]["42"]["20"])
        self.assertEqual(FakeCanvas.downloads, 1)
        _, added, issues = service.download_assignment(self.config, self.canvas, self.course, self.assignment)
        self.assertEqual(added, 0)
        self.assertEqual(FakeCanvas.downloads, 1)

    def test_attachment_failure_preserves_requirements_and_reports_issue(self):
        def inaccessible(path):
            raise SyncError("网站返回 HTTP 403。")
        self.canvas.get = inaccessible
        path, added, issues = service.download_assignment(self.config, self.canvas, self.course, self.assignment)
        self.assertTrue(path.is_file())
        self.assertEqual(added, 0)
        self.assertEqual(len(issues), 1)
        self.assertFalse(list(path.parent.rglob("*.pdf")))

    def test_canvas_attachment_ids_handle_images_metadata_and_external_links(self):
        html = ('<a href="https://files.example/signed" data-api-endpoint="/api/v1/files/123">附件</a>'
                '<img src="/courses/42/files/456/preview" alt="题目图" />'
                '<a href="/courses/42/files?preview=123">重复</a>'
                '<a href="https://outside.example/files/999">外部</a>'
                '<a href="javascript:alert(1)">忽略</a>')
        self.assertEqual(utils.canvas_attachment_ids(html, FakeCanvas.base, FakeCanvas.base + "/courses/42/assignments/20"), ["123", "456"])


if __name__ == "__main__":
    unittest.main()
