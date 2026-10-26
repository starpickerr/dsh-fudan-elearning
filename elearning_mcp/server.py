"""Independent local MCP server. Run with --data-dir and optional --legacy-dir."""

import argparse
import asyncio
import json
import inspect
import logging
from pathlib import Path
import threading

import anyio
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, TextContent, Tool, ToolAnnotations
from . import __version__
from .service import ElearningService


DESCRIPTIONS = {
    "list_courses": "查询 eLearning 可访问课程的 ID 和名称。账号由设置管理，不接收凭证。",
    "fetch": "读取课程公告、作业、测验、成绩和文件元数据；since/until 为时间窗口，offset/limit 分页。",
    "list_files": "查询已选课程文件；可按课程、文件 ID 和扩展名筛选，不下载文件。",
    "sync_files": "下载已选课程课件到配置目录。保留本地文件；支持 managed/courseware 布局、筛选和重复同步。",
    "list_accounts": "查询已配置邮箱及其账号 ID；不返回密码。",
    "list_sources": "查询已启用的邮件简报来源。",
    "fetch_messages": "只读获取指定邮箱的邮件及建议游标；消费成功后才能提交游标，不自动标为已读。",
}

TOOL_TITLES = {'fetch': '获取课程数据', 'list_courses': '查询课程',
               'list_files': '查询文件', 'sync_files': '同步课件'}


def parameter_schema(name):
    if name in {"payload", "cursors"}:
        return {"type": "object"}
    if name in {"course_ids", "file_ids", "file_extensions"}:
        return {"type": ["array", "null"], "items": {"type": "string"}}
    if name in {"offset", "limit", "lookback_days"}:
        return {"type": "integer"}
    if name == "refresh_existing_content":
        return {"type": "boolean"}
    if name == "layout":
        return {"type": "string", "enum": ["managed", "courseware"]}
    return (
        {"type": ["string", "null"]}
        if name in {"since", "until"}
        else {"type": "string"}
    )


def create_server(data_dir, legacy_dir=None, user_id="local-default"):
    data_dir = Path(data_dir).expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    methods = json.loads(Path(__file__).with_name("methods.json").read_text())
    server = Server("elearning", version=__version__)

    @server.list_tools()
    async def list_tools():
        return [
            Tool(
                name=name,
                title=TOOL_TITLES.get(name),
                description=DESCRIPTIONS.get(name, f"eLearning: {name}"),
                inputSchema={
                    "type": "object",
                    "properties": {
                        key: parameter_schema(key) for key in spec["params"]
                    },
                    "required": [
                        key
                        for key, value in inspect.signature(
                            getattr(ElearningService, name)
                        ).parameters.items()
                        if key != "self" and value.default is inspect.Parameter.empty
                    ],
                    "additionalProperties": False,
                },
                annotations=ToolAnnotations(
                    readOnlyHint=spec["read_only"],
                    destructiveHint=not spec["read_only"],
                ),
            )
            for name, spec in methods.items()
        ]

    @server.call_tool()
    async def call_tool(name, arguments):
        if name not in methods:
            raise ValueError("Unknown tool")
        if not isinstance(arguments, dict) or set(arguments) - set(
            methods[name]["params"]
        ):
            raise ValueError("Unknown tool parameters")
        meta = server.request_context.meta
        scope = getattr(meta, "resourceScope", None) if meta else None
        resources = None
        if scope is not None:
            if (
                not isinstance(scope, dict)
                or scope.get("userId") != user_id
                or not isinstance(scope.get("resources"), list)
                or any(
                    not isinstance(row, dict)
                    or set(row) != {"kind", "id"}
                    or not all(
                        isinstance(value, str) and value for value in row.values()
                    )
                    for row in scope["resources"]
                )
            ):
                raise PermissionError("Invalid request resource scope")
            resources = scope["resources"]
        cancel = threading.Event()

        def invoke():
            service = ElearningService(
                data_dir,
                legacy_dir=legacy_dir,
                cancel_event=cancel,
                user_id=user_id,
                authorized_resources=resources,
            )
            value = getattr(service, name)(**arguments)
            return {"value": value}

        try:
            value = await anyio.to_thread.run_sync(invoke, abandon_on_cancel=True)
            return CallToolResult(
                content=[
                    TextContent(type="text", text=json.dumps(value, ensure_ascii=False))
                ],
                structuredContent=value,
            )
        except BaseException:
            cancel.set()
            raise

    return server


async def main():
    parser = argparse.ArgumentParser(description="elearning local stdio MCP")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--legacy-dir", type=Path)
    parser.add_argument("--user-id", default="local-default")
    args = parser.parse_args()
    server = create_server(args.data_dir, args.legacy_dir, args.user_id)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def cli():
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())


if __name__ == "__main__":
    cli()
