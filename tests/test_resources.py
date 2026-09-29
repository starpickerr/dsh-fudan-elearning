import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import resources, service, ui
from src.errors import LoginRequired, SyncError
from src.models import Resource


COURSE = {"id": 42, "name": "计算机网络"}


class ResourceCanvas:
    base = "https://canvas.example"

    def __init__(self):
        self.requests = []
        self.data = {}

    def get(self, path, params=None):
        self.requests.append((path, params))
        return {"id": 99}, None

    def list(self, path, params=None):
        self.requests.append((path, params))
        result = self.data.get(path.rsplit("/", 1)[-1], [])
        if isinstance(result, Exception):
            raise result
        return result


class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.canvas = ResourceCanvas()

    def test_announcements_show_full_body_and_links_newest_first(self):
        self.canvas.data["discussion_topics"] = [
            {"id": 1, "title": "旧公告", "posted_at": "2026-01-01T00:00:00Z", "message": "旧内容"},
            {"id": 2, "title": "新公告", "posted_at": "2026-09-01T00:00:00Z",
             "message": '<p>请阅读第一章。</p><a href="/files/7">讲义</a>'},
            {"id": 3, "title": "草稿", "published": False}]
        records, issues = resources.entries(self.canvas, [COURSE], "announcements")
        self.assertEqual([record.title for record in records], ["新公告", "旧公告"])
        self.assertIn("请阅读第一章", records[0].text)
        self.assertIn("https://canvas.example/files/7", records[0].text)
        self.assertEqual(self.canvas.requests[0][1], {"only_announcements": "true"})
        self.assertEqual(issues, [])

    def test_people_roles_are_shown_and_absent_roles_are_explicit(self):
        self.canvas.data["users"] = [
            {"id": 1, "name": "教师甲", "enrollments": [{"type": "TeacherEnrollment"}]},
            {"id": 2, "name": "学生乙", "enrollments": [{"role": "StudentEnrollment"}]},
            {"id": 3, "name": "成员丙"}]
        records, issues = resources.entries(self.canvas, [COURSE], "people")
        self.assertEqual([record.summary for record in records], ["教师", "学生", "角色未提供"])
        self.assertIn("用户 ID：2", records[1].text)
        self.assertEqual(issues, [])

    def test_quizzes_merge_assignment_quizzes_without_duplicates(self):
        self.canvas.data["quizzes"] = [{"id": 31, "assignment_id": 21, "title": "经典测试", "allowed_attempts": -1,
                                        "time_limit": 30, "points_possible": 10, "question_count": 5}]
        self.canvas.data["assignments"] = [
            {"id": 21, "name": "经典测试", "quiz_id": 31, "is_quiz_assignment": True},
            {"id": 22, "name": "新版测试", "is_quiz_assignment": True, "submission_types": ["external_tool"]},
            {"id": 23, "name": "隐藏测试", "is_quiz_assignment": True, "published": False},
            {"id": 24, "name": "普通作业"}]
        records, issues = resources.entries(self.canvas, [COURSE], "quizzes")
        self.assertEqual([record.title for record in records], ["经典测试", "新版测试"])
        self.assertIn("不限次数", records[0].text)
        self.assertIn("30 分钟", records[0].text)
        self.assertTrue(records[1].url.endswith("/assignments/22"))
        self.assertEqual(issues, [])

    def test_grades_only_request_self_and_preserve_zero_scores(self):
        self.canvas.data["enrollments"] = [{"id": 1, "grades": {"current_score": 0, "final_score": None}}]
        self.canvas.data["assignments"] = [
            {"id": 21, "name": "零分作业", "points_possible": 10,
             "submission": {"workflow_state": "graded", "score": 0, "grade": "0", "posted_at": "2026-09-01"}},
            {"id": 22, "name": "待评分", "points_possible": 10, "submission": {"workflow_state": "submitted"}},
            {"id": 23, "name": "未公布", "points_possible": 10, "muted": True,
             "submission": {"workflow_state": "graded", "score": 95, "grade": "A"}}]
        records, issues = resources.entries(self.canvas, [COURSE], "grades")
        self.assertEqual(self.canvas.requests[1][1], {"user_id": "99", "type[]": "StudentEnrollment"})
        self.assertIn("当前 0 · 最终 未公布", records[0].summary)
        self.assertIn("0 / 10 · 已评分", records[1].summary)
        self.assertIn("已提交，待评分", records[2].summary)
        self.assertIn("评分尚未公布", records[3].text)
        self.assertNotIn("95", records[3].text)
        self.assertEqual(issues, [])

    def test_unposted_grade_is_not_shown_as_published(self):
        record = resources.grade(self.canvas, COURSE, {"id": 1, "name": "待公布", "points_possible": 10,
                                 "submission": {"score": 9, "grade": "9", "workflow_state": "graded", "posted_at": None}})
        self.assertEqual(record.summary, "未公布 / 10 · 评分尚未公布")

    def test_failed_course_totals_preserve_assignment_grades(self):
        self.canvas.data["enrollments"] = SyncError("网站返回 HTTP 403。")
        self.canvas.data["assignments"] = [{"id": 21, "name": "作业", "submission": {"workflow_state": "unsubmitted"}}]
        records, issues = resources.entries(self.canvas, [COURSE], "grades")
        self.assertEqual(len(records), 1)
        self.assertEqual(len(issues), 1)

    def test_expired_login_propagates_to_existing_auto_login_flow(self):
        self.canvas.data["users"] = LoginRequired("登录过期")
        with self.assertRaises(LoginRequired):
            resources.entries(self.canvas, [COURSE], "people")

    def test_browser_urls_must_point_to_the_canvas_website(self):
        record = Resource(COURSE, "1", "标题", "", "", "https://outside.example/test")
        with self.assertRaises(SyncError):
            resources.page_url(self.canvas, record)


class ResourceMenuTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = {"courses": ["42"], "output_dir": str(self.root / "output")}
        self.canvas = ResourceCanvas()
        self.records = [Resource(COURSE, str(index), "条目{}".format(index), "信息", "详情{}\n".format(index),
                                 self.canvas.base + "/courses/42/quizzes/" + str(index)) for index in range(1, 26)]
        context = patch.object(service, "selected_context", return_value=(self.canvas, [COURSE]))
        context.start()
        self.addCleanup(context.stop)

    def test_reading_details_does_not_save_files(self):
        output = io.StringIO()
        with patch.object(resources, "entries", return_value=(self.records[:1], [])), \
                patch("builtins.input", side_effect=["1", "1", "", "0"]), contextlib.redirect_stdout(output):
            self.assertEqual(ui.resources_menu(self.config, "announcements"), 0)
        self.assertIn("详情1", output.getvalue())
        self.assertIn("当前选择的课程：\n\n- 计算机网络", output.getvalue())
        self.assertFalse((self.root / "output").exists())

    def test_pagination_and_export_use_global_item_numbers(self):
        output = io.StringIO()
        with patch.object(resources, "entries", return_value=(self.records, [])), \
                patch("builtins.input", side_effect=["n", "2", "25", "", "0"]), contextlib.redirect_stdout(output):
            self.assertEqual(ui.resources_menu(self.config, "grades"), 0)
        saved = list((self.root / "output").rglob("*.md"))
        self.assertEqual([path.name for path in saved], ["条目25__25.md"])
        self.assertEqual(saved[0].read_text(), "详情25\n")
        self.assertIn("第 2/2 页", output.getvalue())

    def test_quiz_browser_action_opens_details_page(self):
        with patch.object(resources, "entries", return_value=(self.records[:1], [])), \
                patch("builtins.input", side_effect=["3", "1", "", "0"]), \
                patch.object(ui.webbrowser, "open", return_value=True) as browser, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(ui.resources_menu(self.config, "quizzes"), 0)
        browser.assert_called_once_with("https://canvas.example/courses/42/quizzes/1")
        self.assertFalse((self.root / "output").exists())

    def test_dry_run_prevents_export(self):
        with patch.object(resources, "entries", return_value=(self.records[:1], [])), \
                patch("builtins.input", side_effect=["2", "all", "", "0"]), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(ui.resources_menu(self.config, "people", dry_run=True), 0)
        self.assertFalse((self.root / "output").exists())

    def test_empty_or_forbidden_page_gives_a_readable_message(self):
        output = io.StringIO()
        with patch.object(resources, "entries", return_value=([], ["网站返回 HTTP 403。"])), \
                patch("builtins.input", return_value=""), contextlib.redirect_stdout(output):
            self.assertEqual(ui.resources_menu(self.config, "people"), 2)
        self.assertIn("暂无可查看的人员", output.getvalue())
        self.assertIn("HTTP 403", output.getvalue())


if __name__ == "__main__":
    unittest.main()
