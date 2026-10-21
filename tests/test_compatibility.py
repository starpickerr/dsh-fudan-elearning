import importlib,io,json,sys
from pathlib import Path
from datetime import datetime,timezone
from unittest.mock import Mock
import pytest
from email.message import EmailMessage as RfcMessage
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
BASE='https://elearning.fudan.edu.cn'
PASSWORD='campus-secret-77'
SECRET='imap-app-secret-9f3'
DRAFT={'host':'127.0.0.1','user':'ada@example.com','password':SECRET,'port':1,'folder':'INBOX'}
def _load_vendored():
    service=importlib.import_module('elearning_mcp.service')
    return service

def _settings(**changes):
    return {"enabled": True, "username": "student", "password": PASSWORD, "courses": ["42"], **changes}

class FakeCanvas:
    base = BASE

    def __init__(self):
        self.rows = {
            "courses": [{"id": 42, "name": "课程"}],
            "discussion_topics": [{"id": 1, "title": "公告", "message": "<p>上课通知</p>",
                                   "posted_at": "2026-09-08T23:00:00Z"}],
            "assignments": [{"id": 2, "name": "作业", "description": "<p>完整作业说明</p>",
                             "updated_at": "2026-09-08T23:00:00Z", "due_at": "2026-09-10T12:00:00Z",
                             "points_possible": 100,
                             "submission": {"workflow_state": "graded", "graded_at": "2026-09-08T23:00:00Z", "score": 0}}],
            "quizzes": [{"id": 3, "title": "测试", "description": "测试要求", "updated_at": "2026-09-08T23:00:00Z"}],
            "files": [{"id": 4, "filename": "讲义.pdf", "display_name": "讲义.pdf", "size": 3,
                       "updated_at": "2026-09-08T23:00:00Z", "url": BASE + "/files/4/download"},
                      {"id": 5, "filename": "作业.pdf", "display_name": "作业.pdf", "size": 3,
                       "updated_at": "2026-09-08T23:00:00Z", "url": BASE + "/files/5/download"}],
            "enrollments": [{"id": 5, "grades": {"current_score": 0}}],
        }
        self.open = Mock(side_effect=lambda *args, **kwargs: io.BytesIO(b"pdf"))

    def get(self, path, params=None):
        return {"id": 99}, None

    def list(self, path, params=None):
        rows = self.rows[path.rsplit("/", 1)[-1]]
        if isinstance(rows, Exception):
            raise rows
        return rows

def _service(tmp_path, canvas=None, **kw):
    module = _load_vendored()
    canvas = canvas or FakeCanvas()
    def clock():
        return datetime(2026, 9, 9, 8, tzinfo=timezone.utc)
    return module.ElearningService(tmp_path / "elearning", client_factory=lambda cfg: canvas, clock=clock, **kw), canvas

def test_domain_courses_connection_and_options(tmp_path):
    service, _ = _service(tmp_path)
    service.save_settings(_settings())
    assert service.list_courses() == [{"id": "42", "name": "课程"}]
    options = service.test_connection({"password": "********"})
    assert options == [{"id": "42", "name": "课程"}]
    assert service.get_settings()["course_options"] == options

def test_domain_fetch_source_proposes_state_and_set_cursors_commits(tmp_path):
    service, _ = _service(tmp_path)
    service.save_settings(_settings())
    window = {"since": "2026-09-08T12:00:00Z", "until": "2026-09-09T00:00:00Z"}
    result = service.fetch_source(**window)
    assert result["errors"] == [] and result["source"] == "elearning"
    assert {i["payload"]["resource_type"] for i in result["items"]} == {
        "announcement", "assignment", "quiz", "grade", "course_grade", "file"}
    assert PASSWORD not in json.dumps(result)
    assert service.get_cursors() == {}  # proposed only
    service.set_cursors(result["cursors"])
    again = service.fetch_source(since="2026-09-09T00:00:00Z", until="2026-09-10T00:00:00Z")
    assert again["items"] == []
    service.save_settings({"enabled": False})
    with pytest.raises(ValueError, match="未启用"):
        service.fetch_source()

def test_domain_partial_read_does_not_propose_cursor(tmp_path):
    service, canvas = _service(tmp_path)
    service.save_settings(_settings())
    errors = importlib.import_module("elearning_mcp.errors")
    canvas.rows["quizzes"] = errors.SyncError("网站返回 HTTP 500。")
    result = service.fetch_source(since="2026-09-08T12:00:00Z", until="2026-09-09T00:00:00Z")
    assert result["cursors"] == {} and result["errors"] and "quizzes" in result["errors"][0]

def test_domain_fetch_pages_and_filters_window(tmp_path):
    service, _ = _service(tmp_path)
    service.save_settings(_settings())
    page = service.fetch(since="2026-09-08T12:00:00Z", until="2026-09-09T00:00:00Z", limit=2)
    assert page["total"] == 7 and len(page["source_items"]) == 2 and page["next_offset"] == 2
    assert page["status"] == "completed"
    outside = service.fetch(since="2026-09-10T00:00:00Z", until="2026-09-11T00:00:00Z")
    # Dated items fall out of the window; only items first seen without a usable timestamp are observed "now".
    assert outside["total"] < page["total"]
    assert all("2026-09-10" < i["occurred_at"] <= "2026-09-11T00:00:01Z" for i in outside["source_items"])
    with pytest.raises(ValueError, match="course_ids"):
        service.fetch(course_ids=[""])

def test_domain_list_and_sync_files_only_to_configured_folder(tmp_path):
    service, canvas = _service(tmp_path)
    root = tmp_path / "selected"
    root.mkdir()
    service.save_settings(_settings(enabled=False))
    service.save_file_sync_settings(str(root))
    listed = service.list_files(file_extensions=["pdf"])
    assert listed["total"] == 2 and listed["files"][0]["filename"] == "讲义.pdf"
    assert service.list_files(file_extensions=["docx"])["total"] == 0
    result = service.sync_files(file_ids=["5"])
    assert result == {"status": "completed", "total": 1, "processed": 1, "next_offset": None,
                      "save_root": str(root.resolve()), "errors": []}
    assert (root.resolve() / "课程--42" / "作业--5.pdf").read_bytes() == b"pdf"
    assert not (root.resolve() / "课程--42" / "讲义--4.pdf").exists()
    with pytest.raises(ValueError, match="已授权"):
        service.sync_files(course_ids=["unselected"])
    courseware = service.sync_files(file_ids=["4"], layout="courseware")
    assert courseware["errors"] == [] and (root.resolve() / "课程" / "课件" / "讲义.pdf").is_file()
    assert "download" in {e["operation"] for e in service.drain_audit_events()}
    service.save_file_sync_settings("")
    with pytest.raises(ValueError, match="eLearning 设置中选择课件保存目录"):
        service.sync_files()
    # Editing the course scope keeps the destination; in-flight configuration is checked separately.
    service.save_file_sync_settings(str(root))
    service.save_settings({"courses": ["43"]})
    assert service.get_settings()["file_sync_root"] == str(root)
