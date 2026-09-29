import contextlib
import datetime as dt
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import client, service, utils
from src.errors import SyncError


class Response(io.BytesIO):
    def __init__(self, data=b"%PDF-test", content_type="application/pdf"):
        super().__init__(data)
        self.headers = {"Content-Type": content_type}


class FakeCanvas:
    base = "https://elearning.fudan.edu.cn"
    downloads = 0
    body = b"%PDF-test"
    assignment_error = False

    def __init__(self, config):
        pass

    def get(self, path, params=None):
        return {"id": 99}, None

    def list(self, path, params=None):
        if path == "/api/v1/courses":
            return [{"id": 42, "name": "计算机网络", "term": {"name": "2026 秋"}}]
        if path.endswith("/files"):
            return [{"id": 10, "display_name": "Lecture 1.pdf", "size": len(self.body),
                     "updated_at": hashlib.sha256(self.body).hexdigest(), "url": self.base + "/files/10/download"},
                    {"id": 11, "display_name": "参考答案.pdf", "size": 4, "url": self.base + "/files/11/download"}]
        if path.endswith("/assignments"):
            if self.assignment_error:
                raise SyncError("网站返回 HTTP 403。")
            return [{"id": 20, "name": "作业一", "due_at": "2099-10-01T16:00:00Z",
                     "description": '<p>完成第一章习题。</p><a href="/courses/42/files/123">附件</a>',
                     "submission": {"workflow_state": "unsubmitted"}, "submission_types": ["online_upload"]}]
        return []

    def open(self, url, api=True):
        type(self).downloads += 1
        return Response(self.body)


class FilePathTests(unittest.TestCase):
    def test_nested_paths_and_same_named_files_are_distinguishable(self):
        canvas = FakeCanvas({})
        files = [{"id": 10, "folder_id": 3, "display_name": "讲义.pdf"},
                 {"id": 11, "folder_id": 4, "display_name": "讲义.pdf"},
                 {"id": 12, "folder_id": 1, "display_name": "课程介绍.pdf"}]
        folders = [{"id": 1, "name": "course files", "full_name": "course files", "parent_folder_id": None},
                   {"id": 3, "full_name": "course files/课件/第一章", "parent_folder_id": 2},
                   {"id": 4, "full_name": "course files/实验", "parent_folder_id": 1}]
        canvas.list = lambda path: folders if path.endswith("/folders") else files
        entries, issues = service.file_entries(canvas, [{"id": 42, "name": "计算机网络"}])
        self.assertEqual([item["course_relative_path"] for _, item in entries],
                         ["课件/第一章/讲义.pdf", "实验/讲义.pdf", "课程介绍.pdf"])
        self.assertEqual(issues, [])
        self.assertNotIn("course_relative_path", files[0])

    def test_folder_failure_preserves_file_list_with_explicit_warning(self):
        canvas = FakeCanvas({})
        def listing(path):
            if path.endswith("/folders"):
                raise SyncError("网站返回 HTTP 403。")
            return [{"id": 10, "folder_id": 3, "display_name": "讲义.pdf"}]
        canvas.list = listing
        entries, issues = service.file_entries(canvas, [{"id": 42, "name": "计算机网络"}])
        self.assertEqual(entries[0][1]["id"], 10)
        self.assertEqual(entries[0][1]["course_relative_path"], "[文件夹 3 路径不可用]/讲义.pdf")
        self.assertIn("文件夹路径读取失败", issues[0])

    def test_parent_hierarchy_is_used_when_full_name_is_missing(self):
        folders = {"1": {"id": 1, "name": "course files", "parent_folder_id": None},
                   "2": {"id": 2, "name": "课件", "parent_folder_id": 1},
                   "3": {"id": 3, "name": "第一章", "parent_folder_id": 2}}
        self.assertEqual(service.folder_path(3, folders), "课件/第一章")
        folders["2"]["parent_folder_id"] = 3
        with self.assertRaises(SyncError):
            service.folder_path(3, folders)


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = {"base_url": FakeCanvas.base, "courses": ["计算机网络"],
                       "extensions": [".pdf"], "ignore_patterns": ["*答案*"],
                       "output_dir": str(self.root / "output")}
        FakeCanvas.body = b"%PDF-test"
        FakeCanvas.downloads = 0
        FakeCanvas.assignment_error = False

    def tearDown(self):
        self.tmp.cleanup()

    def sync(self, dry=False):
        with patch.object(service, "Canvas", FakeCanvas), contextlib.redirect_stdout(io.StringIO()):
            return service.sync(self.config, dry)

    def test_incremental_and_version_preserves_original(self):
        self.assertFalse(self.sync())
        self.assertEqual(FakeCanvas.downloads, 1)
        self.sync()
        self.assertEqual(FakeCanvas.downloads, 1)
        first = next((self.root / "output").rglob("Lecture 1.pdf"))
        self.assertEqual(first.read_bytes(), b"%PDF-test")
        FakeCanvas.body = b"%PDF-new version"
        self.sync()
        self.assertEqual(first.read_bytes(), b"%PDF-test")
        self.assertEqual(len(list((self.root / "output").rglob("*.pdf"))), 2)
        self.sync()
        self.assertEqual(FakeCanvas.downloads, 2)

    def test_corrupted_local_file_is_recovered(self):
        self.sync()
        next((self.root / "output").rglob("Lecture 1.pdf")).write_bytes(b"corrupt")
        self.sync()
        self.assertEqual(FakeCanvas.downloads, 2)
        records = json.loads((self.root / "output/同步记录.json").read_text())["files"]["42"]["10"]
        self.assertEqual(Path(records["path"]).read_bytes(), FakeCanvas.body)

    def test_dry_run_writes_nothing(self):
        self.sync(dry=True)
        self.assertFalse((self.root / "output").exists())
        self.assertEqual(FakeCanvas.downloads, 0)

    def test_reports_requirements_and_beijing_deadline(self):
        self.sync()
        report = (self.root / "output/作业清单.md").read_text(encoding="utf-8")
        self.assertIn("2099-10-02 00:00", report)
        self.assertIn("完成第一章习题", report)
        self.assertIn("待完成", report)
        detail = next((self.root / "output").rglob("作业一__20.md")).read_text()
        self.assertIn("https://elearning.fudan.edu.cn/courses/42/files/123", detail)

    def test_partial_api_failure_is_visible(self):
        FakeCanvas.assignment_error = True
        self.assertTrue(self.sync())
        self.assertIn("作业汇总不完整", (self.root / "output/作业清单.md").read_text())

    def test_filters_types_patterns_and_ids(self):
        cfg = dict(self.config, include_patterns=["*Lecture*"], ignore_file_ids=[12])
        self.assertTrue(service.file_selected({"id": 10, "display_name": "LECTURE 1.PDF"}, cfg))
        self.assertFalse(service.file_selected({"id": 10, "display_name": "Lecture答案.pdf"}, cfg))
        self.assertFalse(service.file_selected({"id": 12, "display_name": "Lecture 2.pdf"}, cfg))
        self.assertFalse(service.file_selected({"id": 13, "display_name": "Lecture 3.docx"}, cfg))
        cfg.update(extensions=["*"], only_file_ids=[13])
        self.assertTrue(service.file_selected({"id": 13, "display_name": "Lecture 3.docx"}, cfg))
        self.assertFalse(service.file_selected({"id": 14, "display_name": "Lecture 4.pdf"}, cfg))
        cfg["only_file_ids"] = []
        self.assertFalse(service.file_selected({"id": 13, "display_name": "Lecture 3.docx"}, cfg))

    def test_ambiguous_course_requires_id(self):
        courses = [{"id": 1, "name": "计算机网络 A"}, {"id": 2, "name": "计算机网络 B"}]
        with self.assertRaises(SyncError):
            service.select_courses(courses, ["计算机网络"])
        self.assertEqual(service.select_courses(courses, [FakeCanvas.base + "/courses/2"])[0]["id"], 2)

    def test_failed_download_never_records_success(self):
        state = {}
        with self.assertRaises(SyncError):
            service.download(FakeCanvas({}), {"id": 3, "display_name": "lecture.pdf", "size": 999}, self.root, state)
        self.assertEqual(state, {})
        self.assertEqual(list(self.root.iterdir()), [])

    def test_html_login_response_is_not_saved(self):
        client = FakeCanvas({})
        client.open = lambda *a, **k: Response(b"<html>login</html>", "text/html")
        with self.assertRaises(service.LoginRequired):
            service.download(client, {"id": 3, "display_name": "lecture.pdf"}, self.root, {})
        self.assertEqual(list(self.root.iterdir()), [])

    def test_html_and_json_downloads_with_attachment_headers_are_saved(self):
        for suffix, kind, body in (("json", "application/json", b'{"value": 1}'),
                                   ("html", "text/html", b"<p>Course material</p>")):
            with self.subTest(suffix=suffix):
                canvas = FakeCanvas({})
                response = Response(body, kind)
                response.headers["Content-Disposition"] = 'attachment; filename="data.' + suffix + '"'
                canvas.open = lambda *args, **kwargs: response
                path = service.download(canvas, {"id": 3, "display_name": "data." + suffix, "size": len(body)}, self.root, {})
                self.assertEqual(path.read_bytes(), body)

    def test_pagination_follows_next_and_detects_cycles(self):
        canvas = client.Canvas.__new__(client.Canvas)
        canvas.get = lambda path, params: ([{"id": 1}], "/second") if path == "/first" else ([{"id": 2}], None)
        self.assertEqual(canvas.list("/first"), [{"id": 1}, {"id": 2}])
        canvas.get = lambda path, params: ([], "/first")
        with self.assertRaises(SyncError):
            canvas.list("/first")
        self.assertEqual(utils.next_link('<https://x.test/2?a=1>; rel="next", <https://x.test/3>; rel="last"'), "https://x.test/2?a=1")

    def test_configure_saves_selected_course_and_specific_ignores(self):
        config_path = self.root / "config.yaml"
        answers = ["1", "pdf", "", "*答案*", "1", "1"]
        with patch.object(service, "Canvas", FakeCanvas), patch("builtins.input", side_effect=answers), contextlib.redirect_stdout(io.StringIO()):
            service.configure(dict(self.config), config_path)
        configured = utils.load_config(config_path)
        self.assertEqual(configured["courses"], ["42"])
        self.assertEqual(configured["ignore_file_ids"], ["10"])
        self.assertFalse(service.file_selected({"id": 10, "display_name": "Lecture 1.pdf"}, configured))

    def test_unknown_submission_is_not_claimed_unsubmitted(self):
        now = dt.datetime.now(dt.timezone.utc)
        self.assertEqual(service.assignment_status({}, now), ("提交状态未提供，需确认", False))
        self.assertEqual(service.assignment_status({"submission": {"workflow_state": "graded"}}, now), ("已评分", True))

    def test_long_chinese_filename_keeps_extension(self):
        name = utils.safe_name("计算机网络" * 100 + ".pptx")
        self.assertTrue(name.endswith(".pptx"))
        self.assertLessEqual(len(name.encode("utf-8")), 170)


if __name__ == "__main__":
    unittest.main()
