"""读取所选课程的公告、人员、测试与本人评分。"""
import datetime as dt
import urllib.parse

from .errors import LoginRequired, SyncError
from .models import Resource
from .utils import SHANGHAI, atomic_write, display_date, extract, md, parse_date, safe_name

LABELS = {"announcements": "公告", "people": "人员", "quizzes": "测试", "grades": "评分"}
ROLES = {"StudentEnrollment": "学生", "TeacherEnrollment": "教师", "TaEnrollment": "助教",
         "DesignerEnrollment": "课程设计者", "ObserverEnrollment": "观察员"}


def value_text(value, fallback="未提供"):
    return fallback if value is None or value == "" else str(value)


def document(title, course, fields, html=None, page=""):
    lines = ["# " + md(title), "", "- 课程：" + md(course["name"])]
    lines.extend("- {}：{}".format(label, md(value)) for label, value in fields)
    if html is not None:
        text, links = extract(html)
        lines += ["", "## 内容", "", text or "网站未提供文字内容，请打开原页面查看。"]
        links = dict.fromkeys(urllib.parse.urljoin(page, link) for link in links)
        for link in links:
            if urllib.parse.urlparse(link).scheme in ("http", "https"):
                lines.append("\n[附件或参考链接]({})".format(link.replace(" ", "%20")))
    if page:
        lines += ["", "[打开网站页面]({})".format(page)]
    return "\n".join(lines) + "\n"


def announcement(canvas, course, item):
    title = item.get("title") or "未命名公告"
    page = item.get("html_url") or "{}/courses/{}/discussion_topics/{}".format(canvas.base, course["id"], item["id"])
    posted = display_date(item.get("posted_at") or item.get("created_at"))
    author = (item.get("author") or {}).get("display_name") or "未提供"
    fields = [("发布时间", posted), ("发布者", author)]
    return Resource(course, str(item["id"]), title, "发布 " + posted,
                    document(title, course, fields, item.get("message") or "", page), page,
                    item.get("posted_at") or item.get("created_at") or "")


def person(canvas, course, item):
    title = item.get("name") or item.get("short_name") or "未提供姓名"
    roles = []
    for enrollment in item.get("enrollments") or []:
        role = enrollment.get("role") or enrollment.get("type")
        if role:
            roles.append(ROLES.get(role, role))
    role = "、".join(dict.fromkeys(roles)) or "角色未提供"
    page = "{}/courses/{}/users/{}".format(canvas.base, course["id"], item["id"])
    fields = [("角色", role), ("用户 ID", item["id"]), ("显示名称", item.get("short_name") or title)]
    return Resource(course, str(item["id"]), title, role, document(title, course, fields, page=page), page)


def quiz_status(item):
    now = dt.datetime.now(SHANGHAI)
    unlock, lock = parse_date(item.get("unlock_at")), parse_date(item.get("lock_at"))
    if unlock and now < unlock:
        return "尚未开放"
    if lock and now >= lock:
        return "已关闭"
    if item.get("locked_for_user"):
        return "当前受限"
    return "请在网站确认可参加状态"


def quiz(canvas, course, item, from_assignment=False):
    title = item.get("title") or item.get("name") or "未命名测试"
    section = "assignments" if from_assignment else "quizzes"
    page = item.get("html_url") or "{}/courses/{}/{}/{}".format(canvas.base, course["id"], section, item["id"])
    status, due = quiz_status(item), display_date(item.get("due_at"))
    limit = item.get("time_limit")
    attempts = item.get("allowed_attempts")
    fields = [("状态", status), ("截止时间", due), ("开放时间", display_date(item.get("unlock_at"))),
              ("关闭时间", display_date(item.get("lock_at"))), ("满分", value_text(item.get("points_possible"))),
              ("题目数", value_text(item.get("question_count"))),
              ("限时", "不限时" if limit == 0 else str(limit) + " 分钟" if limit is not None else "未提供"),
              ("允许次数", "不限次数" if attempts == -1 else value_text(attempts))]
    if item.get("lock_explanation"):
        fields.append(("限制说明", extract(item["lock_explanation"])[0]))
    return Resource(course, section + "-" + str(item["id"]), title, "截止 {} · {}".format(due, status),
                    document(title, course, fields, item.get("description") or "", page), page)


def grade(canvas, course, item):
    submission = item.get("submission") or {}
    title = item.get("name") or "未命名作业"
    hidden = item.get("muted") or ("posted_at" in submission and not submission["posted_at"])
    state = submission.get("workflow_state")
    if submission.get("excused"):
        status = "免交"
    elif hidden and state == "graded":
        status = "评分尚未公布"
    elif state == "graded" or (submission.get("score") is not None and not hidden):
        status = "已评分"
    elif state in ("submitted", "pending_review") or submission.get("submitted_at"):
        status = "已提交，待评分"
    elif state == "unsubmitted":
        status = "未提交"
    else:
        status = "评分状态未提供"
    score = "未公布" if hidden else value_text(submission.get("score"), "未公布")
    grade_text = "未公布" if hidden else value_text(submission.get("grade"), "未公布")
    possible = value_text(item.get("points_possible"))
    fields = [("状态", status), ("得分 / 满分", score + " / " + possible), ("等级或评分", grade_text),
              ("提交时间", display_date(submission.get("submitted_at"))),
              ("评分时间", display_date(submission.get("graded_at"))), ("截止时间", display_date(item.get("due_at")))]
    for comment in submission.get("submission_comments") or []:
        fields.append(("评语（{}）".format(comment.get("author_name") or "教师"), comment.get("comment") or ""))
    page = "{}/courses/{}/grades#assignment_{}".format(canvas.base, course["id"], item["id"])
    return Resource(course, "assignment-" + str(item["id"]), title,
                    "{} / {} · {}".format(score, possible, status), document(title, course, fields, page=page), page)


def course_grade(canvas, course, enrollment):
    grades = enrollment.get("grades") or {}
    current = value_text(grades.get("current_score"), "未公布")
    final = value_text(grades.get("final_score"), "未公布")
    fields = [("当前成绩（网站百分制）", current), ("最终成绩（网站百分制）", final),
              ("当前等级", value_text(grades.get("current_grade"), "未公布")),
              ("最终等级", value_text(grades.get("final_grade"), "未公布")),
              ("说明", "成绩直接采用网站结果；最终成绩的计算可能包含尚未完成的任务，以教师公布为准。")]
    page = "{}/courses/{}/grades".format(canvas.base, course["id"])
    title = "课程成绩汇总"
    return Resource(course, "enrollment-" + str(enrollment["id"]), title, "当前 {} · 最终 {}".format(current, final),
                    document(title, course, fields, page=page), page)


def entries(canvas, courses, kind):
    records, issues = [], []
    user_id = None
    if kind == "grades":
        profile, _ = canvas.get("/api/v1/users/self/profile")
        user_id = profile["id"]

    def fetch(course, suffix, params, build):
        try:
            items = canvas.list("/api/v1/courses/{}/{}".format(course["id"], suffix), params)
            visible = [item for item in items if item.get("published") is not False]
            records.extend(build(canvas, course, item) for item in visible)
            return visible
        except LoginRequired:
            raise
        except SyncError as error:
            issues.append("{}：{}读取失败（{}）。".format(course["name"], LABELS[kind], error))
            return []

    for course in courses:
        if kind == "announcements":
            fetch(course, "discussion_topics", {"only_announcements": "true"}, announcement)
        elif kind == "people":
            fetch(course, "users", {"include[]": "enrollments"}, person)
        elif kind == "quizzes":
            quizzes = fetch(course, "quizzes", None, quiz)
            known_ids = {str(item.get("assignment_id")) for item in quizzes if item.get("assignment_id")}
            known_quiz_ids = {str(item["id"]) for item in quizzes}
            try:
                assignments = canvas.list("/api/v1/courses/{}/assignments".format(course["id"]))
                for item in assignments:
                    if item.get("published") is False or str(item["id"]) in known_ids or str(item.get("quiz_id")) in known_quiz_ids:
                        continue
                    if item.get("is_quiz_assignment") or item.get("quiz_id") or "online_quiz" in (item.get("submission_types") or []):
                        records.append(quiz(canvas, course, item, from_assignment=True))
            except LoginRequired:
                raise
            except SyncError as error:
                issues.append("{}：作业形式的测试读取失败（{}）。".format(course["name"], error))
        elif kind == "grades":
            fetch(course, "enrollments", {"user_id": str(user_id), "type[]": "StudentEnrollment"}, course_grade)
            fetch(course, "assignments", {"include[]": "submission"}, grade)
        else:
            raise ValueError("未知页面：" + kind)
    if kind == "announcements":
        # The API can return pinned notices first; list by actual publication time.
        records.sort(key=lambda record: parse_date(record.timestamp) or dt.datetime.min.replace(tzinfo=dt.timezone.utc), reverse=True)
    return records, issues


def save(record, directory, kind):
    path = directory / LABELS[kind] / (safe_name(record.title) + "__" + safe_name(record.id) + ".md")
    atomic_write(path, record.text)
    return path


def page_url(canvas, record):
    page = urllib.parse.urljoin(canvas.base + "/", record.url)
    parsed, base = urllib.parse.urlparse(page), urllib.parse.urlparse(canvas.base)
    if parsed.scheme != "https" or parsed.netloc != base.netloc or parsed.username or parsed.password:
        raise SyncError("网站页面地址无效，无法打开。")
    return page
