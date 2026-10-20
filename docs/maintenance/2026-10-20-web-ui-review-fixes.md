# Web 0.2.58 修复、复审与本机更新

日期：2026-10-20。修复提交：`ddc1c36f4867f099176d5bbc40b70b9fecc4e48a`。用户要求修复审查问题并再次 review，随后授权提交、更新现用安装并重启本地服务。

重复标签中已排除的名称和选定目标，在排序、搜索及刷新后继续保留；合集首次读取失败时显示错误和重新加载入口；授权会话及请求失败只显示中文，原始错误保留在诊断复制中；资料库区分尚未读取与已读空目录，正确隐藏及恢复来源筛选。任务查询优化的说明改为实际范围：子任务提示预览和前五篇文章标题采用批量查询，其他实时状态与标题补全仍沿用既有流程。

新增九个浏览器复现用例在旧代码全部失败，修复后通过；随后补充三个授权请求失败用例和一个授权会话 HTTP 用例。由当前维护 Agent 复审修复差异，没有发现新的待修问题。

| 验证 | 结果 |
| --- | --- |
| 完整源码离线回归 | 1863 passed / 2 deselected，267.65 秒 |
| Ruff、JavaScript 语法、差异检查 | 通过 |
| 标签、资料库、操作页浏览器验收 | 三组通过，覆盖亮暗主题与 1440 / 1280 / 1100 / 960px |
| 新 wheel 成员 | 95 个运行文件逐字等于已提交源码 |
| 独立 wheel 环境网页和 API 回归 | 48 passed，58.74 秒；确认从临时环境的 site-packages 导入 |

本机更新前，Web 为 0.2.57，运行与排队任务均为零。三个 LaunchAgent 均使用 `/Users/weisengao/Library/Application Support/douyin-wiki/runtime/.venv/bin/python`，包版本仍为 0.2.1。先备份现有包、安装元数据、命令入口、SQLite 和配置，再停止 Web / Worker，离线重新安装已验收 wheel，没有同步依赖。

2026-10-20 10:37（北京时间）更新验收通过。Web / Worker 使用原 LaunchAgent 重启，新进程分别为 75609 / 75612；健康接口返回 Web 0.2.58，新 Worker 正常心跳。95 个运行文件及 24 个实际静态响应与 wheel 一致，首页资源地址使用 `?v=0.2.58`，已停用模型页继续返回 404。维护服务保留原周日 03:00 计划，没有强制执行维护。

配置、运行依赖版本、除正常 Worker 心跳外的全部数据库表行，以及 2764 个 Markdown 文件内容保持一致。部署结束后再次查询，Web / Worker 均健康，没有运行或排队任务。本次重启范围是 Web 和 Worker，现有 STDIO MCP 客户端没有被强制断开。

备份与安装验收记录位于仓库外：`/Users/weisengao/Documents/ChatGPT/maintenance/douyin-wiki/web-0.2.58-20261020-NiN5By/`。

- `runtime-before-upgrade.zip`、`state-before-upgrade.sqlite3`、`configuration-backups/`：原安装、数据库和配置备份。
- `release/douyin_wiki-0.2.1-py3-none-any.whl`：本次安装包；SHA256 为 `44460569b583da83ac5620b49f5cf0c09b0bf16fdce55b269630d1b45f48d9f8`。
- `wheel-tests.log`：实际安装包回归及包来源。
- `deploy.py`、`commands.log`、`deploy.log`、`before.json`、`deployment.json`、`state-after.json`、`markdown-after.json`：备份、安装、重启和数据保持验证记录。
