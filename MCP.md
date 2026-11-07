# dsh-fudan-elearning MCP

独立的本地 stdio MCP 服务，使用官方 Python SDK `mcp==1.30.0`。需要 Python 3.11 或更新版本；登录使用 Playwright，还需要可用的浏览器。CLI 与 MCP 的账号和数据配置相互独立。

## 安装与启动

在仓库目录执行：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python mcp_server.py --data-dir /absolute/private/elearning --user-id local-default
```

将最后一行的 Python 路径、脚本路径和数据目录换成当前机器上的绝对路径，在 MCP 客户端中以 stdio 方式启动。`--data-dir` 是必填项；每个身份使用独立数据目录和进程。启动路径属于可信本地配置，工具调用不能覆盖它。可选 `--legacy-dir` 指向原设置目录以进行一次非破坏性读取，不删除原文件；先备份已有数据并确认来源。

## 配置与使用

在可信的本地 MCP 客户端中使用 `get_settings` 查看公开设置、`save_settings` 配置账号和课程范围；密码等秘密应通过受信任的配置界面输入，不要写入仓库或聊天。普通模式会把密码或 Token 保存在数据目录的本地 JSON 中，文件权限不是加密；请保护该目录及其备份。再使用 `list_courses`、`fetch` 和 `list_files` 查询。`sync_files` 会将所选课程文件下载到已配置且存在的目录，保留已有文件并按记录去重；执行前确认课程和输出目录。客户端应控制哪些调用者可以使用设置及写入工具，MCP 的只读提示本身不是权限控制。

`--dsh-credentials` 是兼容宿主提供凭据的另一种运行模式，仅开放 `list_courses`、`fetch`、`list_files`，不与 `--legacy-dir` 或 `--hosted-connector` 混用。启动时由宿主提供 `ALCOR_ELEARNING_CREDENTIAL`，旧的 `STELLA_ELEARNING_CREDENTIAL` 仅作迁移回退；同时设置时内容必须完全一致，否则启动失败。两项都会在解析前从环境中移除。不要手工把真实密码写进启动命令。宿主插件的分发和兼容性需另行确认。

可使用 `.venv/bin/python -m unittest tests.test_dsh_credentials -v` 运行对应离线测试。真实登录需学校服务、可用浏览器和用户授权；离线测试不证明真实账号可用。原 CLI 仍通过 `python main.py` 使用，见 [README.md](README.md)。
