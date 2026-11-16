"""Independent local MCP server. Run with --data-dir and optional --legacy-dir."""

import argparse
import asyncio
import json
import inspect
import logging
import sqlite3
from pathlib import Path
import threading

import anyio
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, TextContent, Tool, ToolAnnotations, Request, Result
from . import __version__
from .service import ElearningService
from .errors import DownloadSizeMismatch


class HostedRequest(Request):
    params: dict


DESCRIPTIONS = {
    "list_courses": "查询 eLearning 可访问课程的 ID 和名称。账号由设置管理，不接收凭证。",
    "fetch": "读取课程公告、作业、测验、成绩和文件元数据；since/until 为时间窗口，offset/limit 分页。",
    "list_files": "查询已选课程文件；可按课程、文件 ID 和扩展名筛选，不下载文件。",
    "sync_files": "下载已选课程课件到配置目录。保留本地文件；支持 managed/courseware 布局、筛选和重复同步。",
    "download_file": "下载课程文件到 destination 指定的本地绝对路径，不覆盖已有文件。file_id 接受文件 ID 或该课程的 eLearning 文件网页链接；最大 512 MB。",
    "get_assignment": "读取课程作业的要求、截止时间、文件格式、允许提交次数及自己的当前提交状态和 attempt；未提交为 0。",
    "submit_assignment": "将 file_paths 中 1 至 20 个本地普通文件上传并正式提交作业，仅支持 online_upload。expected_attempt 必须等于 get_assignment 返回的当前次数；重交前取得用户授权。会影响真实作业，写请求不自动重试；结果未知先查询状态。",
    "list_accounts": "查询已配置邮箱及其账号 ID；不返回密码。",
    "list_sources": "查询已启用的邮件简报来源。",
    "fetch_messages": "只读获取指定邮箱的邮件及建议游标；消费成功后才能提交游标，不自动标为已读。",
}

TOOL_TITLES = {'fetch': '获取课程数据', 'list_courses': '查询课程',
               'list_files': '查询文件', 'sync_files': '同步课件', 'download_file': '下载课程文件',
               'get_assignment': '查看作业提交要求', 'submit_assignment': '上传并提交作业'}


def parameter_schema(name):
    if name == 'expected_attempt':
        return {'type': 'integer', 'minimum': 0}
    if name == 'file_paths':
        return {'type': 'array', 'items': {'type': 'string', 'minLength': 1}, 'minItems': 1, 'maxItems': 20, 'uniqueItems': True}
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


def create_server(data_dir, legacy_dir=None, user_id="local-default", *, hosted_connector=False, dsh_credentials=False):
    if dsh_credentials:
        from .dsh_credentials import take_environment
        raw_credentials = take_environment()
    if dsh_credentials and (hosted_connector or legacy_dir):
        raise ValueError('DSH 凭据连接不能与旧托管模式或数据导入混用')
    data_dir = Path(data_dir).expanduser().resolve()
    if not hosted_connector:
        data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    methods = json.loads(Path(__file__).with_name("methods.json").read_text())
    if dsh_credentials:
        methods = {name: methods[name] for name in ('list_courses', 'fetch', 'list_files', 'download_file', 'get_assignment', 'submit_assignment')}
    store = None
    if dsh_credentials:
        from .dsh_credentials import DshCredentialStore
        store = DshCredentialStore.from_raw(data_dir, raw_credentials)
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
                    destructiveHint=spec.get('destructive', not spec['read_only']),
                ),
            )
            for name, spec in methods.items()
        ]

    @server.call_tool(validate_input=not hosted_connector)
    async def call_tool(name, arguments):
        if name not in methods:
            raise ValueError("Unknown tool")
        if not isinstance(arguments, dict) or set(arguments) - set(
            methods[name]["params"]
        ):
            raise ValueError("Unknown tool parameters")
        if hosted_connector:
            if arguments:
                raise PermissionError('Hosted eLearning arguments are owned by Core')
            result = await server.request_context.session.send_request(
                HostedRequest(method='stella/elearning/invoke', params={'requestId': server.request_context.request_id}),
                Result,
            )
            value = result.model_dump(mode='json', exclude_none=True)
            return CallToolResult(content=[TextContent(type='text', text=json.dumps(value, ensure_ascii=False))],
                                  structuredContent=value)
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
                store=store,
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
        except Exception as error:
            cancel.set()
            if not dsh_credentials:
                raise
            # Only fixed categories cross the process boundary, never raw browser errors or SSO URLs.
            messages = {
                'login_required': '学校登录状态无效，请重新登录。',
                'browser_unavailable': '无法启动登录浏览器，请安装 Edge、Chrome 或 Chromium。',
                'network_error': '无法连接学校服务，请检查网络或校园网连接。',
                'network_dns_error': '学校或文件服务器的网络 DNS 解析失败。请检查实际访问域名的 DNS、代理及校园网连接，不要修改审批策略。',
                'network_tls_error': '连接学校或下载服务器时 TLS 验证失败。请检查系统时间、证书及代理，不要关闭证书验证或修改审批策略。',
                'network_timeout': '连接学校或下载服务器超时。请检查网络、校园网或代理后再试，不要将超时解释为审批拒绝。',
                'network_permission_denied': '操作系统拒绝了学校网络连接，请检查防火墙或系统网络权限；这不是 DSH 审批回执。',
                'proxy_dns_fake_ip': '下载地址解析到了 198.18.0.0/15（常见于 Clash/TUN Fake-IP），被下载地址安全检查拦下。请将学校及实际下载域名加入代理 DNS 的 fake-ip-filter，使用真实 IP 解析后重试；无需开启 DSH 审批，也不能放行私网地址。',
                'file_server_blocked': '下载地址解析到了本机、私网或其他非公网地址，被下载地址安全检查拦下。请检查下载域名与 DNS；不要修改 DSH 审批策略或绕过地址检查。',
                'local_file_error': '本地目标目录不可访问或文件写入失败。请检查绝对路径、父目录是否存在、目录是否为符号链接、系统目录权限及剩余空间；已有文件不会被覆盖，不要仅凭此错误认定需要开启 DSH 审批。',
                'school_access_denied': '学校或文件服务器返回 HTTP 403，拒绝访问。请检查学校账号及文件访问权限；这是服务器拒绝，不是 DSH 审批拒绝。',
                'school_file_unavailable': '学校端文件不存在、被隐藏、锁定或当前账号不可访问。请重新查询该课程的文件列表，不要修改 DSH 审批策略。',
                'school_http_error': '学校或文件服务器返回 HTTP 错误。请检查学校服务状态及该文件是否可访问；不要将其解释为本地写入审批拒绝。',
                'login_form_error': '学校登录表单未能提交，请检查认证页面是否变化。',
                'login_timeout': '学校登录未完成，可能需要验证码或额外认证；尚不能判断密码是否错误。',
                'permission_denied': 'eLearning 资源访问被拒绝，请核对账号及有效资源范围。此回执不能证明 DSH 审批被拒绝，不要仅凭此错误更改审批策略。',
                'query_failed': 'eLearning 查询未成功，请稍后重试。',
                'storage_error': 'eLearning 插件本地数据库操作失败，请检查数据目录权限、磁盘空间及其他实例占用；保留数据库与备份，不要将此错误当作学校登录或审批拒绝。',
                'invalid_arguments': '文件路径、课程、文件或作业参数无效，请核对后重试。',
                'destination_exists': '目标文件已存在，请选择新的文件名；已有文件未被覆盖。',
                'invalid_file': '文件不可读取、格式或大小不符要求，或在上传期间发生变化。请检查普通文件、作业格式和 512 MB 大小限制。',
                'download_failed': '下载未完成或文件大小校验失败，未保存目标文件。',
                'download_incomplete': '接收文件内容时连接中断、超时或长度不足，内部重试已用完，未保存目标文件。请检查网络和代理后再试；此错误不能证明服务器文件损坏。',
                'download_size_mismatch': '完整接收后的文件大小与学校元数据不符，未保存目标文件。请核对学校标注大小、下载地址及代理响应，不要绕过校验，也不能仅凭此错误认定服务器文件损坏。',
                'download_integrity_error': '下载内容包含额外分块编码，但结构、CRC32 或解码后的大小验证失败，未保存目标文件。请检查源文件或学校下载服务，不要绕过校验。',
                'assignment_unavailable': '作业尚未开放、已经锁定或允许的提交次数已用完。',
                'unsupported_submission': '此作业不支持 Canvas 文件上传，请使用其指定的提交方式。',
                'attempt_conflict': '作业提交次数已经变化；请重新查询状态并决定是否重交，本次未提交。',
                'upload_failed': '文件上传未完成或结果无法确认，本次未提交作业；部分文件可能已上传。',
                'submission_rejected': '学校拒绝了写入请求，请检查作业状态和提交要求；部分文件可能已上传。',
                'submission_unknown': '写入请求已发送，但结果无法确认；请先查询作业状态，勿直接重试；部分文件可能已上传。',
            }
            code = ('permission_denied' if isinstance(error, PermissionError) else
                    'storage_error' if isinstance(error, sqlite3.Error) else
                    'invalid_arguments' if isinstance(error, ValueError) else getattr(error, 'code', 'query_failed'))
            if code not in messages:
                code = 'query_failed'
            message = messages[code]
            if isinstance(error, DownloadSizeMismatch):
                message += f' 学校标注 {error.expected_size} 字节，实际接收 {error.actual_size} 字节。'
            return CallToolResult(isError=True, content=[TextContent(type='text', text=json.dumps(
                {'error_code': code, 'message': message}, ensure_ascii=False))])
        except BaseException:
            cancel.set()
            raise

    return server


async def main():
    parser = argparse.ArgumentParser(description="elearning local stdio MCP")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--legacy-dir", type=Path)
    parser.add_argument("--user-id", default="local-default")
    parser.add_argument('--hosted-connector', action='store_true')
    parser.add_argument('--dsh-credentials', action='store_true', help='由 DSH 提供凭据，仅在内存中保存登录状态，提供查询、下载与作业文件提交')
    args = parser.parse_args()
    server = create_server(args.data_dir, args.legacy_dir, args.user_id, hosted_connector=args.hosted_connector, dsh_credentials=args.dsh_credentials)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def cli():
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())


if __name__ == "__main__":
    cli()
