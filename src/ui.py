"""课程、作业与文件的交互菜单。"""
import datetime as dt
import shutil
import sys
import unicodedata
import webbrowser

from . import resources, service
from .errors import LoginRequired, SyncError
from .utils import SHANGHAI, choices, display_date


def selected_course_summary(config, catalog):
    names = []
    for selector in config.get("courses", []):
        try:
            selected = service.select_courses(catalog, [selector])
            names.extend(course["name"] for course in selected)
        except SyncError:
            names.append(str(selector) + "（当前不可用）")
    return "\n".join("- " + name for name in names) if names else "未选择课程"


def clear_terminal():
    if sys.stdout.isatty():
        print("\033[H\033[2J\033[3J", end="", flush=True)


def read_selection(prompt, config, catalog):
    """Keep the course footer below the input line, then clear after submission."""
    block = prompt + "\n\n当前选择的课程：\n\n" + selected_course_summary(config, catalog) + "\n"
    print(block, end="", flush=True)
    terminal = sys.stdin.isatty() and sys.stdout.isatty()
    if terminal:
        columns = max(1, shutil.get_terminal_size().columns)
        rows = 0
        for line in block.splitlines():
            width = sum(0 if unicodedata.combining(char) else
                        2 if unicodedata.east_asian_width(char) in ("W", "F") else 1 for char in line)
            rows += max(1, (width + columns - 1) // columns)
        print("\033[{}A\r".format(rows), end="", flush=True)
    value = input(prompt if terminal else "").strip()
    clear_terminal()
    return value


def choose_items(count, label, config, catalog, render):
    while True:
        render()
        value = read_selection("{}编号（如 1,3-5；all 全选；0 返回）：".format(label), config, catalog)
        if value in ("", "0"):
            return []
        if value.lower() == "all" or value == "全部":
            return list(range(count))
        try:
            return choices(value, count)
        except SyncError as error:
            print(error)


def courses_menu(config, config_path):
    canvas = service.Canvas(config)
    courses = [course for course in service.course_list(canvas) if course.get("name")]
    if not courses:
        raise SyncError("没有找到可访问的在读课程。")
    selected_ids = set()
    for selector in config.get("courses", []):
        try:
            selected_ids.update(str(course["id"]) for course in service.select_courses(courses, [selector]))
        except SyncError:
            pass
    while True:
        print("\n── 课程 ──")
        for index, course in enumerate(courses, 1):
            mark = "已选" if str(course["id"]) in selected_ids else "未选"
            term = (course.get("term") or {}).get("name", "学期未提供")
            print("{}. [{}] {} · {}".format(index, mark, course["name"], term))
        print("输入新的课程编号将替换当前选择；clear 清空；回车或 0 返回。")
        value = read_selection("选择课程（如 1,3-5；all 全选）：", config, courses)
        if value in ("", "0"):
            return 0
        if value.lower() == "clear" or value == "清空":
            selected = []
        elif value.lower() == "all" or value == "全部":
            selected = courses
        else:
            try:
                selected = [courses[i] for i in choices(value, len(courses))]
            except SyncError as error:
                print(error)
                continue
        service.save_course_selection(config, config_path, selected)
        print("已保存课程：" + ("、".join(course["name"] for course in selected) or "未选择课程"))
        return 0


def show_issues(issues):
    for issue in issues:
        print("提示：" + issue)


def assignments_menu(config, dry_run=False):
    canvas, courses = service.selected_context(config)
    entries, issues = service.assignment_entries(canvas, courses)
    show_issues(issues)
    if not entries:
        print("当前所选课程没有可读取的作业。")
        read_selection("按回车返回主菜单：", config, courses)
        return 2 if issues else 0

    def render():
        print("\n── 作业 ──")
        now = dt.datetime.now(SHANGHAI)
        for index, (course, assignment) in enumerate(entries, 1):
            status, _ = service.assignment_status(assignment, now)
            print("{}. {} / {} · 截止 {} · {}".format(index, course["name"], assignment.get("name", "未命名作业"),
                                                        display_date(assignment.get("due_at")), status))

    while True:
        render()
        print("\n1. 阅览\n2. 下载说明和附件\n0. 返回主菜单")
        action = read_selection("请选择：", config, courses)
        if action in ("", "0"):
            return 2 if issues else 0
        if action not in ("1", "2"):
            print("请输入有效编号。")
            continue
        picked = choose_items(len(entries), "作业", config, courses, render)
        if not picked:
            continue
        if action == "2" and dry_run:
            print("当前为预览模式，不写入文件。")
            read_selection("按回车返回作业列表：", config, courses)
            continue
        for index in picked:
            course, assignment = entries[index]
            try:
                if action == "1":
                    print("\n" + service.assignment_document(canvas, course, assignment)["text"])
                else:
                    path, count, warnings = service.download_assignment(config, canvas, course, assignment)
                    print("已保存：{}（新增附件 {} 个）".format(path, count))
                    show_issues(warnings)
                    issues.extend(warnings)
            except LoginRequired:
                raise
            except (SyncError, OSError) as error:
                warning = "{}：{}".format(assignment.get("name", "作业"), error)
                issues.append(warning)
                show_issues([warning])
        read_selection("按回车返回作业列表：", config, courses)


def file_size(size):
    if size is None:
        return "大小未知"
    size = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return "{:.1f} {}".format(size, unit)
        size /= 1024


def file_display_path(item):
    return item.get("course_relative_path") or item.get("display_name") or item.get("filename", "未命名文件")


def files_menu(config, dry_run=False):
    canvas, courses = service.selected_context(config)
    entries, issues = service.file_entries(canvas, courses)
    show_issues(issues)
    if not entries:
        print("当前所选课程没有可读取的文件。")
        read_selection("按回车返回主菜单：", config, courses)
        return 2 if issues else 0
    page, page_size = 0, 20
    last_page = (len(entries) - 1) // page_size

    def render():
        print("\n── 文件 ── 第 {}/{} 页，共 {} 个".format(page + 1, last_page + 1, len(entries)))
        start = page * page_size
        for index, (course, item) in enumerate(entries[start:start + page_size], start + 1):
            access = " · 暂不可访问" if item.get("locked_for_user") or item.get("hidden_for_user") else ""
            print("{}. {} / {} · {}{}".format(index, course["name"], file_display_path(item),
                                                 file_size(item.get("size")), access))

    while True:
        render()
        print("\n1. 阅览（在线预览）\n2. 下载\nn. 下一页\np. 上一页\n0. 返回主菜单")
        action = read_selection("请选择：", config, courses)
        if action in ("", "0"):
            return 2 if issues else 0
        if action.lower() == "n":
            page = min(last_page, page + 1)
            continue
        if action.lower() == "p":
            page = max(0, page - 1)
            continue
        if action not in ("1", "2"):
            print("请输入有效编号。")
            continue
        picked = choose_items(len(entries), "文件", config, courses, render)
        if not picked:
            continue
        if action == "2" and dry_run:
            print("当前为预览模式，不写入文件。")
            read_selection("按回车返回文件列表：", config, courses)
            continue
        for index in picked:
            course, item = entries[index]
            name = file_display_path(item)
            try:
                if action == "1":
                    print("\n文件：{}\n课程：{}\n大小：{}\n更新时间：{}".format(
                        name, course["name"], file_size(item.get("size")), display_date(item.get("updated_at"))))
                    url = service.file_preview_url(canvas, item)
                    if webbrowser.open(url):
                        print("已在浏览器中打开预览。")
                    else:
                        raise SyncError("无法打开浏览器，请选择下载后查看。")
                else:
                    path, added = service.download_file(config, canvas, course, item)
                    print(("已下载：" if added else "本地文件已是最新：") + str(path))
            except LoginRequired:
                raise
            except (SyncError, OSError) as error:
                warning = "{}：{}".format(name, error)
                issues.append(warning)
                show_issues([warning])
        read_selection("按回车返回文件列表：", config, courses)


def resources_menu(config, kind, dry_run=False):
    canvas, courses = service.selected_context(config)
    records, issues = resources.entries(canvas, courses, kind)
    label = resources.LABELS[kind]
    hints = {"announcements": "公告按发布时间排列；阅览正文或保存公告说明。",
             "people": "查看课程内可访问的成员姓名、角色和个人页面。",
             "quizzes": "查看测试时间和要求；参加测试请打开网站页面后操作。",
             "grades": "查看本人的课程成绩和作业评分；未公布的成绩会单独标注。"}
    if not records:
        print("\n── {} ──".format(label))
        show_issues(issues)
        print("当前所选课程暂无可查看的{}。".format(label))
        print("请确认课程选择、内容是否已发布，以及网站是否开放此功能。")
        read_selection("按回车返回主菜单：", config, courses)
        return 2 if issues else 0
    page, page_size = 0, 20
    last_page = (len(records) - 1) // page_size
    notice = ""

    def render():
        clear_terminal()
        print("\n── {} ── 第 {}/{} 页，共 {} 项".format(label, page + 1, last_page + 1, len(records)))
        print(hints[kind])
        show_issues(issues)
        if notice:
            print(notice)
        start = page * page_size
        for index, record in enumerate(records[start:start + page_size], start + 1):
            print("{}. {} / {} · {}".format(index, record.course["name"], record.title, record.summary))

    while True:
        render()
        notice = ""
        print("\n1. 阅览详情\n2. 保存为 Markdown\n3. 打开网站页面\nn. 下一页\np. 上一页\n0. 返回主菜单")
        print("选择操作后输入条目编号，支持 1,3-5 或 all；all 选择所有页面的条目。")
        action = read_selection("请选择操作：", config, courses)
        if action in ("", "0"):
            return 2 if issues else 0
        if action.lower() == "n":
            page = min(last_page, page + 1)
            continue
        if action.lower() == "p":
            page = max(0, page - 1)
            continue
        if action not in ("1", "2", "3"):
            notice = "请输入 1、2、3、n、p 或 0。"
            continue
        picked = choose_items(len(records), label, config, courses, render)
        if not picked:
            continue
        if action == "2" and dry_run:
            print("当前为预览模式，不写入文件。")
        else:
            for index in picked:
                record = records[index]
                try:
                    if action == "1":
                        print("\n" + record.text)
                    elif action == "2":
                        path = resources.save(record, service.course_directory(config, record.course), kind)
                        print("已保存：" + str(path))
                    elif not webbrowser.open(resources.page_url(canvas, record)):
                        raise SyncError("无法打开浏览器，请先阅览详情或保存说明。")
                    else:
                        print("已打开：{} / {}".format(record.course["name"], record.title))
                except LoginRequired:
                    raise
                except (SyncError, OSError) as error:
                    warning = "{}：{}".format(record.title, error)
                    issues.append(warning)
                    show_issues([warning])
        read_selection("按回车返回{}列表：".format(label), config, courses)
