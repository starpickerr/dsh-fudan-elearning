#!/usr/bin/env python3
"""统一入口：交互菜单与命令行操作。"""
import argparse
import logging
import sys
import time
from pathlib import Path

from src import client, resources, service, ui
from src.errors import LoginRequired, SyncError
from src.utils import CONFIG_FILE, load_config, run_lock, setup_logging

LOGGER = logging.getLogger("elearning")


def execute_command(args, command, preview=False):
    # Reload configuration after each menu action so saved selections take effect immediately.
    LOGGER.info("开始操作：%s", command)
    config = load_config(args.config)
    dry_run = args.dry_run or preview
    result = 0
    with run_lock():
        if command == "login":
            client.login(config, manual=args.manual_login)
            LOGGER.info("操作结束：%s，退出码 0", command)
            return 0
        if command == "configure":
            service.configure(config, args.config)
        elif command == "courses":
            result = ui.courses_menu(config, args.config)
        elif command == "assignments":
            result = ui.assignments_menu(config, dry_run=dry_run)
        elif command == "files":
            result = ui.files_menu(config, dry_run=dry_run)
        elif command in resources.LABELS:
            result = ui.resources_menu(config, command, dry_run=dry_run)
        elif command == "sync":
            result = 2 if service.sync(config, dry_run) else 0
        elif command == "watch":
            while True:
                try:
                    service.sync(config, dry_run)
                except LoginRequired:
                    raise
                except SyncError as error:
                    print("本轮失败：{}，稍后重试。".format(error), flush=True)
                print("下次检查将在 {} 分钟后。Ctrl+C 停止。".format(max(300, args.interval) // 60), flush=True)
                time.sleep(max(300, args.interval))
    LOGGER.info("操作结束：%s，退出码 %d", command, result)
    return result


def interactive_menu(args, catalog):
    actions = {"1": "login", "2": "courses", "3": "assignments", "4": "files",
               "5": "announcements", "6": "people", "7": "quizzes", "8": "grades"}
    notice = ""
    while True:
        config = load_config(args.config)
        ui.clear_terminal()
        print("\n elearning 自动化助手\n\n"
              "1. 重新登录\n"
              "2. 课程\n"
              "3. 作业\n"
              "4. 文件\n"
              "5. 公告\n"
              "6. 人员\n"
              "7. 测试\n"
              "8. 评分\n"
              "0. 退出")
        print("\n提示：先在「2. 课程」选择课程；3–8 仅查看当前所选课程。")
        print("登录状态会自动维护；输入 1 可重新登录，其他页面输入 0 返回主菜单。")
        if notice:
            print("\n" + notice)
        notice = ""
        selection = ui.read_selection("请输入功能编号（0–8）：", config, catalog)
        if selection == "0":
            return 0
        if selection not in actions:
            notice = "请输入 0–8 之间的功能编号，0 退出。"
            continue
        try:
            result = execute_command(args, actions[selection])
            if actions[selection] == "login" and result == 0:
                notice = "登录成功。"
            if result == 2:
                notice = "部分内容未能处理，请进入对应页面重试。"
        except (SyncError, OSError, ValueError, KeyError) as error:
            LOGGER.warning("操作未完成：%s（%s）", actions[selection], type(error).__name__)
            notice = "未完成：" + str(error)
            print(notice, file=sys.stderr)


def main(argv=None):
    parser = argparse.ArgumentParser(description="复旦 elearning 课件与任务同步；不指定操作时打开菜单")
    parser.add_argument("command", nargs="?", choices=("menu", "login", "courses", "assignments", "files", "announcements", "people", "quizzes", "grades", "configure", "sync", "watch"), default="menu")
    parser.add_argument("--config", type=Path, default=CONFIG_FILE)
    parser.add_argument("--dry-run", action="store_true", help="只预览，不下载和写报告")
    parser.add_argument("--interval", type=int, default=1800, help="watch 的检查间隔（秒，至少 300）")
    parser.add_argument("--manual-login", action="store_true", help="在浏览器中自行登录，不读取 .env 账号密码")
    args = parser.parse_args(argv)
    try:
        setup_logging()
        if args.command == "menu":
            with run_lock():
                canvas = client.ensure_login(load_config(args.config))
                catalog = service.course_list(canvas)
            print("已确认登录状态，进入主菜单。", flush=True)
            return interactive_menu(args, catalog)
        return execute_command(args, args.command)
    except (SyncError, OSError, ValueError, KeyError) as error:
        LOGGER.warning("操作未完成：%s（%s）", args.command, type(error).__name__)
        print("未完成：" + str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已停止。")
        return 130
    except EOFError:
        print("\n输入已结束，操作取消。")
        return 130


if __name__ == "__main__":
    sys.exit(main())
