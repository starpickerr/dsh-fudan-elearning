# 复旦 eLearning DSH 插件

独立安装包为 `@alcor/elearning`，适配官方 DSH Desktop 0.2.2-rc.5，支持 Apple 芯片的 Mac。在 DSH 的插件设置保存学工号和密码、检查连接。在对话里查询课程、公告、成绩和作业，并通过宿主权限管理下载与文件型作业提交。

凭据仅由 DSH 凭据服务管理，连接设置只供本机账号使用。模型工具通过 DSH 工具权限启用或限制。启用 Alcor 时可在简报中使用同一已配置来源，现有配置和已选范围沿用原来的连接身份。

从本仓的 Release 下载 `alcor-elearning-0.2.0.tgz`，运行 `dsh plugin --profile desktop add /path/to/alcor-elearning-0.2.0.tgz`，然后在插件页启用。旧版本使用同一包名，可原地升级。

独立 MCP 服务的用法见本仓根 README。构建插件时将 Alcor 产品仓克隆到相邻的 `Alcor/` 目录，使用 CI 固定的共享代码版本。创建 Python 3.12 虚拟环境 `.venv`，用 `pip install --require-hashes -r requirements.lock` 安装依赖，再安装 `PyInstaller==6.22.3`。在 `plugin/` 运行 `npm ci` 和 `npm pack`。发布包自带冻结后的 MCP 服务。
