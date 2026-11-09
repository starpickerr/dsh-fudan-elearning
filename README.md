# dsh-fudan-elearning

复旦 eLearning 自动化助手，提供本地命令行程序和独立的 stdio MCP 服务。CLI 支持课程、作业、公告及文件整理；MCP 提供课程查询与文件同步。访问范围由学校账号权限决定。

MCP 的安装、账号配置、工具和运行方式见 [MCP.md](MCP.md)。CLI 与 MCP 使用各自的数据配置：CLI 的 `.env` 不会自动提供给 MCP。服务能力简介保存在 `elearning_mcp/capability.json`，随包分发。

## DSH 凭据模式

服务支持由兼容宿主启动的 `--dsh-credentials` 模式。宿主负责凭据输入和连接管理，服务开放 `list_courses`、`fetch`、`list_files`、`download_file`、`get_assignment` 和 `submit_assignment`；此模式不能与 `--hosted-connector` 或 `--legacy-dir` 混用。它不读取旧配置中的密码、Token 或 session 文件，登录 Cookie 保存在进程内存中。账号替换、删除和重启需要由宿主重建连接。此仓库提供服务端模式，宿主插件的安装和兼容性需按所用发行版另行核实。

## 文件下载与作业提交

`download_file(course_id, file_id, destination)` 接受文件 ID 或课程文件的 eLearning 网页链接，保存到明确指定的本地绝对文件路径，不覆盖已有文件，最大 512 MB。父目录须已存在。

`get_assignment(course_id, assignment_id)` 查询作业提交方式、允许格式、截止时间、锁定状态和自己的提交次数。`submit_assignment(course_id, assignment_id, file_paths, expected_attempt)` 将 1 至 20 个本地普通文件（每个最大 512 MB）上传并正式提交；只支持 Canvas 原生 online_upload。expected_attempt 必须为刚查询的当前次数，未提交为 0；重交须由用户明确要求，新提交可能替换上一轮附件。文件由学校返回的 HTTPS 上传目标接收，不向目标转发 Canvas 密码、Token 或 Cookie。

POST 不自动重试。只有正式回执返回 submitted 才算交作业成功；网络超时或 submission_unknown 时先查询当前次数和附件，避免重复提交。失败可能保留已上传但未提交的文件，不会自动删除远端数据。宿主负责工具调用及本地文件的用户授权，MCP 注解不构成权限控制。

## CLI 使用

CLI 操作通过 **main.py** 启动。在脚本文件夹内运行：

```sh
conda activate elearning
python main.py
```

不带参数会先检查登录状态，再打开交互菜单。没有 Cookie 或 Cookie 已失效时，自动读取 `.env` 登录；有效状态直接复用。完成操作后返回菜单，输入 `0` 退出；子菜单输入 `0` 返回。也可以通过同一入口指定操作，见下方“直接运行”。

## 项目结构

```text
main.py                  # CLI 入口：菜单、命令行调度
config/config.yaml       # 课程、下载规则、输出路径等普通配置
src/
  __init__.py
  client.py              # 登录、Canvas API、网络请求
  service.py             # 课程选择、下载、作业和公告整理
  resources.py           # 公告、人员、测试和本人评分
  models.py              # 页面条目的数据结构
  ui.py                  # 各功能的交互页面
  utils.py               # 配置、路径、文本工具、运行锁、日志
  errors.py              # 应用异常
  
data/
  input/                 # 预留输入目录
  output/                # 课件、作业、公告、同步记录
logs/                    # 运行日志
tests/                   # 自动化测试
.env                     # 本地账号、密码、可选 Token（Git 忽略）
.env.example             # 空白凭据模板
.private/                # 登录 Cookie 和运行锁（Git 忽略）
environment.yml          # elearning Conda 环境
requirements.txt         # Python 依赖
README.md                # 使用说明
```

普通配置使用 YAML，重新选择课程时会写回同一个配置文件。密码和 Token 只从 `.env` 或指定的系统环境变量读取，不写入普通配置。

## 运行环境

使用名为 **elearning** 的 Conda 环境，Python 3.11，依赖为 Playwright、python-dotenv 和 PyYAML。运行前先激活环境；程序入口和菜单均使用 Python。

新电脑需先安装 Conda，再在脚本文件夹创建环境：

```sh
conda env create -f environment.yml
```

如果已经有 `elearning` 环境，只需安装依赖：

```sh
conda activate elearning
python -m pip install -r requirements.txt
```

登录优先使用本机 Chrome；若未安装 Chrome，可在上述环境中运行 `python -m playwright install chromium`。

## 第一次使用

先在脚本同目录的 `.env` 中填写自己的学工号和密码（格式见 `.env.example`）：

```dotenv
ELEARNING_USERNAME='你的学工号'
ELEARNING_PASSWORD='你的校园密码'
CANVAS_TOKEN=''  # 可选：已有 Canvas API Token
```

新电脑可以将 `.env.example` 复制为 `.env` 后填写。建议使用单引号；密码中的单引号写成 `\'`，反斜杠写成 `\\`。读取时关闭变量替换，密码中的 `$`、`${...}`、`#` 和空格会按引号内的内容保留。`.env` 路径固定在脚本目录，不随终端当前目录变化。

1. 运行 `python main.py`，程序自动检查登录并进入主菜单。
2. 进入 **2：课程**，查看所有可选课程和当前勾选状态。输入 `1,3-5` 等编号替换选择，`all` 全选，`clear` 清空，回车或 `0` 返回。选课只修改课程范围，保存后主菜单立即显示所选课程名称。
3. 进入 **3：作业**，查看所选课程的作业、截止时间和提交状态。选择“阅览”阅读完整要求；选择“下载说明和附件”保存 Markdown 说明，以及正文链接和图片中可识别的站内文件附件。输入作业编号可单选或多选，`all` 选择全部。
4. 进入 **4：文件**，查看所选课程的完整文件夹路径、文件名、大小和访问状态，例如 `计算机网络 / 课件/第一章/讲义.pdf`。每页 20 个，`n` 下一页、`p` 上一页。选择“阅览”显示完整路径和详情，并在浏览器打开网站提供的在线预览地址；预览效果取决于浏览器和文件格式。选择“下载”保存指定文件，同样支持单选、多选和 `all`。编号在不同页面之间保持一致，`all` 选择全部所列文件。文件夹路径无法读取时会明确标注，仍可按编号阅览和下载文件。
5. 进入 **5：公告**，按发布时间查看已发布公告，阅览正文及附件链接，保存公告说明，或在浏览器打开原页面。菜单展示所有可访问公告；自动同步命令仍使用 `announcement_days` 的天数范围。
6. 进入 **6：人员**，分页查看所选课程的成员姓名、角色和用户 ID，可阅览、保存成员说明，或打开网站的个人页面。
7. 进入 **7：测试**，查看测试说明、截止和开放时间、限时、题目数与允许次数，支持保存说明或打开网站页面。实际参加测试在浏览器中完成。兼容经典测验及作业接口中可识别的测试。
8. 进入 **8：评分**，查看本人的课程成绩汇总、作业得分、满分和评分状态，支持阅览、保存和打开网站成绩页面。零分正常显示，未评分或未公布的成绩不会作为零分显示；课程汇总采用网站结果。
9. 输入 **0：退出**。各功能页面输入 `0` 返回主菜单。

主菜单：

```text
1. 重新登录
2. 课程
3. 作业
4. 文件
5. 公告
6. 人员
7. 测试
8. 评分
0. 退出

提示：先在「2. 课程」选择课程；3–8 仅查看当前所选课程。
登录状态会自动维护；输入 1 可重新登录，其他页面输入 0 返回主菜单。

请输入功能编号（0–8）：

当前选择的课程：

- 计算机网络
- 操作系统
```

每次提交输入后会清空终端及滚动历史；返回主菜单时再次清屏，主菜单上方不保留课程页面或保存提示。各交互页面的所选课程始终显示在最后，输入位置在课程列表上方。阅览内容和下载结果会等待按回车后再清屏返回列表。

公告、人员、测试和评分每页 20 项，`n` / `p` 翻页。选择阅览、保存或打开网站页面后，再输入条目编号；支持 `1,3-5` 和 `all`，编号跨页保持一致，`all` 选择所有页面的条目。无内容或权限不足时会显示具体提示，其余课程仍可查看。保存功能只写入本地 Markdown 文件。

文件页面列出所选课程中接口可读取的全部文件，下载按你选择的编号执行。作业和文件下载互相独立。外部网页链接保留在作业说明中；附件下载针对可识别的 Canvas 文件，无法访问的附件会单独提示。阅览不会在项目输出目录写入文件。

## 自动同步规则

需要持续自动同步时，可以通过 `python main.py configure` 配置文件类型、包含或忽略规则、固定文件选择，再运行 `python main.py sync` 或 `python main.py watch`。以下规则用于这两个自动同步命令。

## 包含与忽略规则示例

| 希望的行为 | 设置 |
| --- | --- |
| 只下载 PPT、PPTX、PDF | 文件扩展名：`ppt,pptx,pdf` |
| 只下载标题包含 Lecture 或“讲”的文件 | 包含：`*Lecture*,*讲*` |
| 忽略答案、旧版和参考资料 | 忽略：`*答案*,*旧版*,*参考*` |
| 忽略某门课里名为 test 的文件 | 忽略：`课程名称/*test*` |
| 固定下载几份现有文件 | 选择“只下载现在勾选的具体文件”，输入文件编号 |

忽略规则优先。固定选择具体文件时，未来新增文件不会自动加入；需要重新配置。更改规则不会删除已经下载的文件。

## 输出位置

输出默认保存在项目的 **data/output/** 文件夹中；`output_dir` 的相对路径以项目根目录为基准：

```text
data/output/
  作业清单.md
  同步记录.json
  课程名称__课程ID/
    课件/
    作业/
      作业名称__作业ID.md
      作业名称__作业ID_附件/
    课程公告.md
    公告/                  # 公告页面保存的单篇说明
    人员/                  # 人员页面保存的成员说明
    测试/                  # 测试说明
    评分/                  # 本人课程和作业评分说明
```

作业清单列出截止时间（北京时间）、提交状态、提交方式和要求摘录，每项链接到完整要求与附件链接。正文不发送给外部 AI 服务；“总结”采用结构化整理和原文摘录。课程公告独立保存原文，便于查看公告中的任务。没有明确发布在作业接口的任务、外部教学工具里的任务、公告正文中的日期需要自行核对。

首次同步下载所选范围内的所有匹配文件。以后按服务器文件 ID、更新时间、大小与本地内容校验跳过重复文件；更新内容另存版本，保留旧版。中断下载不会记为成功。已有课程资料文件夹里的文件不会自动导入同步记录，也不会被改写。

## 自动检查与登录

持续检查默认每 30 分钟运行一次。终端窗口需保持打开，电脑需要联网；休眠、关机或关闭窗口后不能继续检查。`Ctrl+C` 停止。当前没有安装后台定时任务。

需要更改课程或规则时，先停止持续检查，再重新配置并启动。

账号密码保存在本地 `.env`，已加入 `.gitignore`，程序读取时将权限设为仅当前用户可读写（`0600`）；它是明文文件，Git 忽略不会加密内容。程序不会将账号密码输出到终端、日志、`config/config.yaml` 或同步报告。

脚本保存 elearning 的登录 Cookie 到 `.private/session.json`，文件仅当前用户可读；日常同步复用 Cookie。统一认证站点的 Cookie 不保存。

Cookie 到期、接口返回未认证状态，或下载跳转到登录页面时，程序会自动读取 `.env` 重新登录，更新 Cookie，再重试刚才的请求。持续检查同样支持自动续登，当前的课程选择和已保存的下载进度会保留。每次失效请求最多续登并重试一次；续登失败会给出提示，持续检查会停止。临时网络错误沿用原来的重试机制。

菜单 1 和 `python main.py login` 保留为强制重新登录入口。使用有效的 `CANVAS_TOKEN` 时直接通过 Token 验证；Token 失效需要更新 Token，Cookie 续登不会改写 `.env` 中的 Token。

URL 中的 `success` 表示登录流程的跳转结果。程序使用与同步相同的网络客户端验证账号接口，验证通过后才保存 Cookie 并显示“登录成功”。随后浏览器关闭，程序返回菜单，可以继续选择课程或同步。登录失败时会说明失败阶段；尚未通过验证的状态不会保存。

具体作业和文件的可访问范围由学校网站的权限决定。若课程文件列表不可用，会尝试单元中的文件，并在报告中标明扫描可能不完整。

## 直接运行

在脚本文件夹内先激活 Conda 环境，再运行：

```sh
conda activate elearning
python main.py
python main.py login
python main.py courses      # 选择课程
python main.py assignments  # 作业页面
python main.py files        # 文件页面
python main.py announcements # 公告页面
python main.py people        # 人员页面
python main.py quizzes       # 测试页面
python main.py grades        # 本人评分页面
python main.py configure
python main.py sync --dry-run
python main.py sync
python main.py watch --interval 1800
python main.py login --manual-login
```

`login` 默认读取 `.env` 自动填写账号密码；加上 `--manual-login` 则在浏览器中自行登录，不读取 `.env`。拥有现有 Canvas API Token 时，可在 `.env` 中填写 `CANVAS_TOKEN`，同步无需浏览器登录；也支持同名系统环境变量，系统环境变量中的非空值优先。脚本不会创建 Token。

配置文件为 `config/config.yaml`，可以直接修改课程 ID、扩展名、`include_patterns`、`ignore_patterns`、`only_file_ids` 和 `ignore_file_ids`。`only_file_ids: null` 表示不固定具体文件，空列表 `[]` 表示不下载任何课件；任务和公告仍会整理。文件筛选目前匹配文件名或“课程名称/文件名”，不按网站文件夹路径匹配。

## 日志与测试

日志保存在 `logs/elearning.log`，记录操作开始、结束、错误类型和同步数量；达到 1 MiB 后轮转，保留 3 份备份。日志不记录账号密码、Token、Cookie 或接口响应正文。生成的日志、输入和输出文件均被 Git 忽略。

运行离线测试：

```sh
conda activate elearning
python -m unittest discover -s tests -v
```

## 参考文档

- [Canvas 课程接口](https://developerdocs.instructure.com/services/canvas/resources/courses)
- [Canvas 文件接口](https://developerdocs.instructure.com/services/canvas/resources/files)
- [Canvas 作业接口](https://developerdocs.instructure.com/services/canvas/resources/assignments)
- [Canvas 分页](https://developerdocs.instructure.com/services/canvas/basics/file.pagination)
- [Playwright 登录状态](https://playwright.dev/python/docs/auth)

## MCP Server

Local stdio MCP entry and existing-state compatibility: [MCP.md](MCP.md). The original CLI remains available.
