"""课程选择、增量下载、作业整理和同步。"""
import datetime as dt
import fnmatch
import http.client
import json
import logging
import os
import re
import urllib.parse
from pathlib import Path

from .client import Canvas
from .errors import LoginRequired, SyncError
from .utils import (ROOT, SHANGHAI, atomic_write, canvas_attachment_ids, choices, digest, display_date,
                    extract, md, parse_date, prompt_choices, safe_name,
                    save_config, save_json)

LOGGER = logging.getLogger("elearning")


def course_list(client):
    return client.list("/api/v1/courses", {"enrollment_type": "student", "enrollment_state": "active", "include[]": "term"})


def select_courses(courses, selectors):
    if not selectors:
        raise SyncError("尚未指定课程，请编辑 config/config.yaml 的 courses。")
    selected = {}
    for selector in selectors:
        query = str(selector).strip()
        match_id = re.search(r"/courses/(\d+)", query)
        if match_id:
            query = match_id.group(1)
        matches = [c for c in courses if str(c.get("id")) == query] if query.isdigit() else [
            c for c in courses if query.casefold() in c.get("name", "").casefold()]
        if len(matches) != 1:
            details = "；".join("{}（ID {}）".format(c.get("name"), c.get("id")) for c in matches)
            raise SyncError("课程“{}”{}。{} 请运行 courses 查看列表，并在配置里填写课程 ID。".format(
                query, "未找到" if not matches else "匹配多个结果", details))
        selected[str(matches[0]["id"])] = matches[0]
    return list(selected.values())


def file_selected(item, config, course_name=""):
    filename = item.get("display_name") or item.get("filename", "")
    fid = str(item.get("id"))
    only = config.get("only_file_ids")
    if only is not None and fid not in {str(x) for x in only}:
        return False
    if fid in {str(x) for x in config.get("ignore_file_ids", [])}:
        return False
    extensions = {x.casefold() if x.startswith(".") or x == "*" else "." + x.casefold()
                  for x in config.get("extensions", [".ppt", ".pptx", ".pdf"])}
    if "*" not in extensions and Path(filename).suffix.casefold() not in extensions:
        return False
    names = [filename.casefold(), (course_name + "/" + filename).casefold()]
    includes = config.get("include_patterns") or ["*"]
    excludes = config.get("ignore_patterns") or []
    if not any(fnmatch.fnmatchcase(name, pattern.casefold()) for name in names for pattern in includes):
        return False
    return not any(fnmatch.fnmatchcase(name, pattern.casefold()) for name in names for pattern in excludes)


def configure(config, config_path):
    client = Canvas(config)
    client.get("/api/v1/users/self/profile")
    courses = [c for c in course_list(client) if c.get("name")]
    if not courses:
        raise SyncError("没有找到可访问的在读课程。")
    print("\n可选择的课程：")
    for index, course in enumerate(courses, 1):
        print("{}．{}（ID {}；{}）".format(index, course["name"], course["id"], (course.get("term") or {}).get("name", "学期未提供")))
    selected = [courses[i] for i in prompt_choices("课程编号（例如 1,3-5）：", len(courses))]
    config["courses"] = [str(c["id"]) for c in selected]
    value = input("文件扩展名，逗号分隔（回车：ppt,pptx,pdf；输入 * 下载所有类型）：").strip() or "ppt,pptx,pdf"
    extensions = [x.strip().lower() for x in re.split(r"[,，]", value) if x.strip()]
    if not extensions or any(not re.fullmatch(r"\*|\.?[a-z0-9]+", x) for x in extensions):
        raise SyncError("文件类型格式不正确，例如 ppt,pptx,pdf 或 *。配置尚未保存。")
    config["extensions"] = [x if x.startswith(".") or x == "*" else "." + x for x in extensions]
    value = input("只包含这些文件名规则（回车：全部；例如 *Lecture*,*讲*）：").strip()
    config["include_patterns"] = [x.strip() for x in re.split(r"[,，]", value) if x.strip()] or ["*"]
    value = input("忽略文件名规则（回车：不忽略；例如 *答案*,*参考*,*旧版*）：").strip()
    config["ignore_patterns"] = [x.strip() for x in re.split(r"[,，]", value) if x.strip()]
    config["only_file_ids"] = None
    config["ignore_file_ids"] = []
    print("\n符合类型和文件名规则的可访问文件：")
    candidates, incomplete = [], False
    for course in selected:
        try:
            files, warnings = list_files(client, course["id"])
            for warning in warnings:
                print("注意：{}：{}".format(course["name"], warning))
                incomplete = True
            for item in files:
                if not item.get("locked_for_user") and not item.get("hidden_for_user") and file_selected(item, config, course["name"]):
                    candidates.append(item)
                    print("{}．{} / {}（文件 ID {}）".format(len(candidates), course["name"], item.get("display_name") or item.get("filename"), item["id"]))
        except LoginRequired:
            raise
        except SyncError as error:
            incomplete = True
            print("注意：{}：{}".format(course["name"], error))
    if candidates:
        print("\n1．自动下载符合规则的现有和未来新增文件")
        print("2．只下载现在勾选的具体文件（未来新文件不会自动纳入）")
        mode = prompt_choices("选择模式 1 或 2：", 2)
        if len(mode) != 1:
            raise SyncError("只能选择一种模式，配置尚未保存。")
        if mode == [1]:
            picked = prompt_choices("要下载的文件编号（例如 1,3-5）：", len(candidates))
            config["only_file_ids"] = [str(candidates[i]["id"]) for i in picked]
        else:
            value = input("另外忽略哪些具体文件？输入编号，回车跳过：").strip()
            if value:
                config["ignore_file_ids"] = [str(candidates[i]["id"]) for i in choices(value, len(candidates))]
    else:
        print("目前没有符合规则的文件；今后新增的匹配文件会自动纳入。")
    if incomplete:
        print("部分课程文件未能完整列出，具体文件选择仅涵盖已列出的内容。")
    save_config(config_path, config)
    print("\n已保存课程和下载规则：{}\n下次同步和持续检查都会使用这些规则。".format(config_path))


def list_files(client, cid):
    try:
        return client.list("/api/v1/courses/{}/files".format(cid)), []
    except LoginRequired:
        raise
    except SyncError as error:
        original = str(error)
    # Some courses disable the Files tab but expose files in modules.
    files, errors = {}, ["文件列表读取失败（{}），尝试从单元补充。此轮文件扫描可能不完整。".format(original)]
    for module in client.list("/api/v1/courses/{}/modules".format(cid)):
        for item in client.list("/api/v1/courses/{}/modules/{}/items".format(cid, module["id"])):
            if item.get("type") == "File" and item.get("content_id"):
                fid = item["content_id"]
                if fid in files:
                    continue
                try:
                    files[fid] = client.get("/api/v1/files/{}".format(fid))[0]
                except LoginRequired:
                    raise
                except SyncError as error:
                    errors.append("单元文件 {}：{}".format(fid, error))
    return list(files.values()), errors


def download(client, item, directory, state):
    fid = str(item["id"])
    filename = safe_name(item.get("display_name") or item.get("filename") or fid)
    version = [item.get("updated_at"), item.get("modified_at"), item.get("size"), filename]
    old = state.get(fid, {})
    if old.get("version") == version and old.get("path"):
        target = Path(old["path"])
        if target.is_file() and digest(target) == old.get("sha256"):
            return None
    directory.mkdir(parents=True, exist_ok=True)
    tmp = directory / (".{}.part".format(fid))
    try:
        url = item.get("url") or "/files/{}/download".format(fid)
        with client.open(url, api=False) as response, tmp.open("wb") as stream:
            content_type = response.headers.get("Content-Type", "").lower()
            if ("text/html" in content_type or "application/json" in content_type) and not response.headers.get(
                    "Content-Disposition", "").lower().startswith("attachment"):
                raise LoginRequired("下载返回了登录或错误页面，未保存为课件。")
            for block in iter(lambda: response.read(1024 * 1024), b""):
                stream.write(block)
        if item.get("size") is not None and tmp.stat().st_size != int(item["size"]):
            raise SyncError("下载不完整，未记录为成功，将在下次重试。")
        file_hash = digest(tmp)
        target = directory / filename
        if target.exists() and digest(target) != file_hash:
            suffix = target.suffix
            target = directory / "{}__文件{}__{}{}".format(target.stem, fid, file_hash[:12], suffix)
        if target.exists():
            if digest(target) != file_hash:
                raise SyncError("版本文件名冲突，已保留原文件。")
            tmp.unlink()
        else:
            os.replace(tmp, target)
        state[fid] = {"version": version, "path": str(target.resolve()), "sha256": file_hash}
        return target
    except http.client.IncompleteRead:
        raise SyncError("下载连接中断，未记录为成功，将在下次重试。") from None
    finally:
        if tmp.exists():
            tmp.unlink()


def assignment_status(assignment, now):
    submission = assignment.get("submission")
    if not isinstance(submission, dict):
        return "提交状态未提供，需确认", False
    if submission.get("workflow_state") == "graded":
        return "已评分", True
    if submission.get("workflow_state") in ("submitted", "pending_review") or submission.get("submitted_at"):
        return "已提交", True
    due = parse_date(assignment.get("due_at"))
    return ("已过截止时间，待确认" if due and due < now else "待完成"), False


def assignment_document(client, course, assignment, now=None):
    now = now or dt.datetime.now(SHANGHAI)
    status, submitted = assignment_status(assignment, now)
    content, links = extract(assignment.get("description"))
    page = assignment.get("html_url") or "{}/courses/{}/assignments/{}".format(client.base, course["id"], assignment["id"])
    submission_types = {"online_upload": "上传文件", "online_text_entry": "在线文本", "online_url": "提交网址",
                        "on_paper": "线下提交", "none": "无需在线提交", "external_tool": "外部工具",
                        "online_quiz": "在线测验", "discussion_topic": "讨论", "media_recording": "音视频"}
    methods = "、".join(submission_types.get(x, x) for x in assignment.get("submission_types", [])) or "未提供"
    lines = ["# " + md(assignment.get("name", "未命名作业")), "", "- 课程：" + md(course["name"]),
             "- 截止时间（北京时间）：" + display_date(assignment.get("due_at")),
             "- 开放时间：" + display_date(assignment.get("unlock_at")),
             "- 关闭时间：" + display_date(assignment.get("lock_at")), "- 状态：" + status,
             "- 提交方式：" + methods, "- 文件格式：" + ", ".join(assignment.get("allowed_extensions") or ["未限定/未提供"]),
             "- [查看原始任务](" + page + ")", "", "## 要求原文", "", content or "网站没有提供文字说明，请打开原始任务查看附件或外部工具。"]
    if assignment.get("locked_for_user"):
        lines += ["", "当前任务受限：" + extract(assignment.get("lock_explanation"))[0]]
    if links:
        lines += ["", "## 附件和参考链接", ""]
        for url in links:
            full = urllib.parse.urljoin(page, url)
            if urllib.parse.urlparse(full).scheme in ("http", "https"):
                lines.append("- [打开链接]({})".format(full.replace(" ", "%20")))
    return {"course": course["name"], "name": assignment.get("name", "作业"), "due_at": assignment.get("due_at"),
            "status": status, "submitted": submitted, "text": "\n".join(lines) + "\n",
            "excerpt": content[:360], "methods": methods}


def requirement_document(client, course, assignment, directory, now):
    document = assignment_document(client, course, assignment, now)
    path = directory / (safe_name(assignment.get("name", "作业")) + "__" + str(assignment["id"]) + ".md")
    atomic_write(path, document.pop("text"))
    document["path"] = path
    return document


def output_directory(config):
    return (ROOT / config.get("output_dir", "data/output")).resolve()


def course_directory(config, course):
    return output_directory(config) / (safe_name(course["name"]) + "__" + str(course["id"]))


def selected_context(config):
    canvas = Canvas(config)
    if not config.get("courses"):
        raise SyncError("尚未选择课程，请先进入“课程”选择要查看的课程。")
    return canvas, select_courses(course_list(canvas), config["courses"])


def assignment_entries(canvas, courses):
    entries, issues = [], []
    for course in courses:
        try:
            assignments = canvas.list("/api/v1/courses/{}/assignments".format(course["id"]), {"include[]": "submission"})
            entries.extend((course, item) for item in assignments if item.get("published") is not False)
        except LoginRequired:
            raise
        except SyncError as error:
            issues.append("{}：作业读取失败（{}）。".format(course["name"], error))
    entries.sort(key=lambda entry: parse_date(entry[1].get("due_at")) or dt.datetime.max.replace(tzinfo=dt.timezone.utc))
    return entries, issues


def folder_path(folder_id, folders):
    """Return a folder's path relative to the course's file root."""
    folder = folders.get(str(folder_id))
    if folder is None:
        raise SyncError("文件夹 {} 的路径未能读取。".format(folder_id))
    full_name = folder.get("full_name")
    if full_name:
        path = full_name.strip("/")
        for root in folders.values():
            if root.get("parent_folder_id") is None:
                prefix = (root.get("full_name") or root.get("name") or "").strip("/")
                if prefix and path == prefix:
                    return ""
                if prefix and path.startswith(prefix + "/"):
                    return path[len(prefix) + 1:]
        return path
    parts, visited = [], set()
    while folder.get("parent_folder_id") is not None:
        key = str(folder["id"])
        if key in visited:
            raise SyncError("文件夹路径存在循环。")
        visited.add(key)
        parts.append(folder["name"])
        folder = folders.get(str(folder["parent_folder_id"]))
        if folder is None:
            raise SyncError("父文件夹的路径未能读取。")
    return "/".join(reversed(parts))


def add_file_paths(canvas, cid, files):
    warnings, folders = [], {}
    if any(item.get("folder_id") is not None for item in files):
        try:
            folders = {str(folder["id"]): folder for folder in canvas.list("/api/v1/courses/{}/folders".format(cid))}
        except LoginRequired:
            raise
        except SyncError as error:
            warnings.append("文件夹路径读取失败（{}）。文件仍可阅览和下载。".format(error))
    paths = {}
    result = []
    for item in files:
        name = item.get("display_name") or item.get("filename", "未命名文件")
        folder_id = item.get("folder_id")
        if folder_id is None:
            path = name
        else:
            key = str(folder_id)
            if key not in paths:
                try:
                    paths[key] = folder_path(folder_id, folders)
                except SyncError as error:
                    paths[key] = "[文件夹 {} 路径不可用]".format(folder_id)
                    if folders:
                        warnings.append(str(error))
            path = (paths[key] + "/" if paths[key] else "") + name
        result.append(dict(item, course_relative_path=path))
    return result, warnings


def file_entries(canvas, courses):
    entries, issues = [], []
    for course in courses:
        try:
            files, warnings = list_files(canvas, course["id"])
            files, path_warnings = add_file_paths(canvas, course["id"], files)
            entries.extend((course, item) for item in files)
            issues.extend(course["name"] + "：" + warning for warning in warnings + path_warnings)
        except LoginRequired:
            raise
        except SyncError as error:
            issues.append("{}：文件读取失败（{}）。".format(course["name"], error))
    return entries, issues


def save_course_selection(config, path, courses):
    updated = dict(config, courses=[str(course["id"]) for course in courses])
    save_config(path, updated)
    config.update(updated)


def download_file(config, canvas, course, item):
    if item.get("locked_for_user") or item.get("hidden_for_user"):
        raise SyncError("文件暂不可访问。")
    state_path = output_directory(config) / "同步记录.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"files": {}}
    file_state = state.setdefault("files", {}).setdefault(str(course["id"]), {})
    saved = download(canvas, item, course_directory(config, course) / "课件", file_state)
    save_json(state_path, state)
    return Path(file_state[str(item["id"])]["path"]), saved is not None


def download_assignment(config, canvas, course, assignment):
    directory = course_directory(config, course) / "作业"
    document = requirement_document(canvas, course, assignment, directory, dt.datetime.now(SHANGHAI))
    state_path = output_directory(config) / "同步记录.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"files": {}}
    file_state = state.setdefault("assignment_files", {}).setdefault(str(course["id"]), {}).setdefault(str(assignment["id"]), {})
    page_url = assignment.get("html_url") or "{}/courses/{}/assignments/{}".format(canvas.base, course["id"], assignment["id"])
    ids = canvas_attachment_ids(assignment.get("description"), canvas.base, page_url)
    attachment_directory = directory / (document["path"].stem + "_附件")
    attachments, issues, added = [], [], 0
    for fid in ids:
        try:
            item, _ = canvas.get("/api/v1/files/" + fid)
            if item.get("locked_for_user") or item.get("hidden_for_user"):
                raise SyncError("附件暂不可访问。")
            saved = download(canvas, item, attachment_directory, file_state)
            save_json(state_path, state)
            added += saved is not None
            attachments.append(Path(file_state[str(item["id"])]["path"]))
        except LoginRequired:
            raise
        except (SyncError, OSError) as error:
            issues.append("附件 {} 下载失败（{}）。".format(fid, error))
    if attachments:
        lines = ["", "## 已下载附件", ""]
        for path in attachments:
            relative = urllib.parse.quote(path.relative_to(directory).as_posix())
            lines.append("- [{}]({})".format(md(path.name), relative))
        atomic_write(document["path"], document["path"].read_text(encoding="utf-8") + "\n".join(lines) + "\n")
    LOGGER.info("作业下载完成：附件新增 %d，未完成 %d", added, len(issues))
    return document["path"], added, issues


def file_preview_url(canvas, item):
    if item.get("locked_for_user") or item.get("hidden_for_user"):
        raise SyncError("文件暂不可访问。")
    data, _ = canvas.get("/api/v1/files/{}/public_url".format(item["id"]))
    url = data.get("public_url")
    if not isinstance(url, str) or urllib.parse.urlparse(url).scheme != "https":
        raise SyncError("网站未提供可用的在线预览地址，请选择下载。")
    return url


def sync(config, dry_run=False):
    client = Canvas(config)
    # Check authenticated identity before any writes or broad listing.
    profile, _ = client.get("/api/v1/users/self/profile")
    if not isinstance(profile, dict) or not profile.get("id"):
        raise LoginRequired("未能验证登录状态，请重新登录。")
    courses = select_courses(course_list(client), config.get("courses"))
    output = (ROOT / config.get("output_dir", "data/output")).resolve()
    state_path = output / "同步记录.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"files": {}}
    now = dt.datetime.now(SHANGHAI)
    tasks, added, issues = [], [], []
    for course in courses:
        cid = course["id"]
        name = course["name"]
        print("检查课程：{}（ID {}）".format(name, cid), flush=True)
        directory = output / (safe_name(name) + "__" + str(cid))
        course_state = state["files"].setdefault(str(cid), {})
        try:
            files, warnings = list_files(client, cid)
            issues.extend(name + "：" + x for x in warnings)
            for item in files:
                filename = item.get("display_name") or item.get("filename", "")
                if not file_selected(item, config, name):
                    continue
                if item.get("locked_for_user") or item.get("hidden_for_user"):
                    issues.append("{}：文件“{}”暂不可访问。".format(name, filename))
                    continue
                if dry_run:
                    print("  可访问课件：" + filename, flush=True)
                    continue
                try:
                    saved = download(client, item, directory / "课件", course_state)
                    if saved:
                        added.append(saved)
                        save_json(state_path, state)
                        print("  已保存：" + saved.name, flush=True)
                except LoginRequired:
                    raise
                except (SyncError, OSError) as error:
                    issues.append("{}：文件“{}”下载失败（{}）。".format(name, filename, error))
        except LoginRequired:
            raise
        except SyncError as error:
            issues.append("{}：课件列表读取失败（{}）。".format(name, error))
        try:
            assignments = client.list("/api/v1/courses/{}/assignments".format(cid), {"include[]": "submission"})
            for assignment in assignments:
                if assignment.get("published") is False:
                    continue
                if dry_run:
                    print("  作业：{}；截止 {}".format(assignment.get("name"), display_date(assignment.get("due_at"))), flush=True)
                else:
                    tasks.append(requirement_document(client, course, assignment, directory / "作业", now))
        except LoginRequired:
            raise
        except SyncError as error:
            issues.append("{}：作业读取失败（{}）；本轮作业汇总不完整。".format(name, error))
        try:
            start = now - dt.timedelta(days=int(config.get("announcement_days", 90)))
            notices = client.list("/api/v1/announcements", {"context_codes[]": "course_{}".format(cid),
                                  "start_date": start.isoformat(), "end_date": now.isoformat()})
            if not dry_run:
                lines = ["# 课程公告", "", "公告原文供查看任务线索；正文中的截止日期请自行核对。", ""]
                for notice in sorted(notices, key=lambda x: x.get("posted_at") or "", reverse=True):
                    text, links = extract(notice.get("message"))
                    lines += ["## " + md(notice.get("title", "公告")), "", "发布时间：" + display_date(notice.get("posted_at")), "", text]
                    if notice.get("html_url"):
                        lines += ["", "[查看公告原文]({})".format(notice["html_url"])]
                    for link in links:
                        full = urllib.parse.urljoin(client.base, link)
                        if urllib.parse.urlparse(full).scheme in ("http", "https"):
                            lines.append("[公告附件/链接]({})".format(full.replace(" ", "%20")))
                    lines.append("")
                atomic_write(directory / "课程公告.md", "\n".join(lines))
        except LoginRequired:
            raise
        except SyncError as error:
            issues.append("{}：公告读取失败（{}）。".format(name, error))
    if dry_run:
        print("预览结束，未下载文件、未写入报告。", flush=True)
        for issue in issues:
            print("注意：" + issue)
        return bool(issues)
    state["last_run"] = now.isoformat()
    save_json(state_path, state)
    lines = ["# elearning 作业清单", "", "更新时间：{}（北京时间）".format(now.strftime("%Y-%m-%d %H:%M")),
             "", "范围：" + "、".join(md(c["name"]) for c in courses), "",
             "说明：任务要求为原文摘录；无截止时间的任务排在末尾。公告与外部工具中的任务请另行核对。", ""]
    if issues:
        lines += ["## 本轮未能完整处理的内容", ""] + ["- " + md(x) for x in issues] + [""]
    tasks.sort(key=lambda x: parse_date(x["due_at"]) or dt.datetime.max.replace(tzinfo=dt.timezone.utc))
    for label, submitted in (("待完成及待确认", False), ("已提交或已评分", True)):
        group = [x for x in tasks if x["submitted"] == submitted]
        lines += ["## " + label, ""]
        if not group:
            lines += ["本轮成功读取的作业中没有此类记录。", ""]
        for task in group:
            relative = urllib.parse.quote(task["path"].relative_to(output).as_posix())
            lines += ["### {} · {}".format(md(task["course"]), md(task["name"])), "",
                      "- 截止：" + display_date(task["due_at"]), "- 状态：" + task["status"],
                      "- 提交：" + task["methods"], "- [查看完整要求和附件链接]({})".format(relative), "",
                      md(task["excerpt"]) + ("…" if len(task["excerpt"]) == 360 else "") if task["excerpt"] else "任务说明未提供，请查看原始任务。", ""]
    lines += ["## 本次新增或更新的课件", ""]
    lines += ["- [{}]({})".format(md(p.name), urllib.parse.quote(p.relative_to(output).as_posix())) for p in added] or ["无新增课件。"]
    report = output / "作业清单.md"
    atomic_write(report, "\n".join(lines) + "\n")
    print("同步完成：保存 {} 个课件，整理 {} 项作业。\n报告：{}".format(len(added), len(tasks), report), flush=True)
    if issues:
        print("有 {} 项未完整处理，请查看报告说明。".format(len(issues)), flush=True)
    LOGGER.info("同步完成：课件 %d，作业 %d，未完成 %d", len(added), len(tasks), len(issues))
    return bool(issues)
