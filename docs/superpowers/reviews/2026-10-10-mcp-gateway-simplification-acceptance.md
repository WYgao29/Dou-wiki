# MCP / Gateway 精简发布前验收

日期：2026-10-11。第 8/10 大步骤、Task 9。结论：`0.2.0` 的源码、浏览器和独立发行包验收通过，可以在用户确认后执行第 9 步本地主线集成与部署。本步没有切换生产入口或清除用户 Vault。

依据：[已确认设计](../../design/2026-10-10-mcp-gateway-simplification-plan.md)、[实施计划](../plans/2026-10-10-mcp-gateway-simplification.md)、[作者自查](2026-10-10-mcp-gateway-simplification-code-review.md)。由当前 Agent 执行，没有独立审查者或子 Agent。

## 版本、源码与产物

- 执行基准：`6995dfa83d25a293d60b1e677ff7fb6108ef21a1`。
- 发行源码提交：`8f4986e4185776edb0005dcb5f376fef9490fd86`，分支 `codex/mcp-gateway-simplification`。本验收记录提交后只有文档变化，包内源码不变。
- Python 包：`0.2.0`；Web：`0.2.48`；Python 范围：`>=3.12,<3.14`。构建和独立检查环境均使用 Python `3.12.13`。
- 产物和证据目录：`/Users/weisengao/Documents/ChatGPT/maintenance/douyin-wiki/mcp-gateway-simplification-2026-10-10/`，下文简称 `TASK_OUT`。

| 产物 | SHA256 |
| --- | --- |
| `dist/douyin_wiki-0.2.0-py3-none-any.whl` | `9170032a16ac490c4e61a705b69503265eb04a44794a9f57c3e3e489470b0979` |
| `dist/douyin_wiki-0.2.0.tar.gz` | `323120263dc3d374c84b57def114678541f80f39863409bb08066ce148138d1e` |
| `requirements-check.txt` | `1f74f96304ab0497742b03ffe98add7c42b7368f3e9d3e05c7c41e5c8bfd8894` |
| `requirements-runtime.txt` | `92b1766d223c1e2355df1c9c844326faeb5b9ca8527a5a95ad3f1fa51c9c417e` |

wheel 和 sdist 中 90 个包文件均与源码逐字一致。检查包括全部 24 个静态资源、模板、图标、字体和许可，以及 `resources/vision_ocr.swift`。两个 console scripts 保持 `douyin-wiki` 和 `douyin-wiki-mcp`。sdist 还包含正式测试、脚本、文档、锁文件和许可证；未包含本机配置、Vault、SQLite、Profile、Cookie、模型密钥、模型权重或缓存。

## 验证结果

| 检查 | 实际结果 |
| --- | --- |
| 本轮完整非 live 回归 | `1503 passed, 2 deselected in 199.36s` |
| Ruff / 静态 JS | Ruff 退出 0；13 个 JS 模块语法检查通过 |
| 初始化引导修正后检查 | 既有 setup / CLI `28 passed in 5.37s`；实际新库 init 回执指向 MCP / Gateway |
| 当前八个浏览器入口 | foundation、shell、library、reader、chat、operations、cleanup、tags 全部退出 0 |
| 页面布局 | cleanup 验证 18 个路由 × 3 种宽度 × 亮暗主题，共 108 组；其他入口覆盖键盘、命令面板、旧链接、选择、确认、重试和保留右栏 |
| 浏览器证据 | 八入口 107 张截图；完整回归中的合集 / 共享场景另保留 26 张截图；报告中外部请求、JS 异常及服务错误均为零 |
| 包安装 | 独立环境 `TASK_OUT/release-check/.venv/`，100 个安装依赖兼容，项目从 site-packages 加载，非 editable |
| 新库 / Web / Worker | 原生 schema 16，无聊天表、无 `[llm]`；Web 和 24 项静态资源可读取，16 组停用方法 / 路径直接返回 410，无新增业务任务；Worker 空队列运行通过 |
| doctor | 实际 CLI 正常返回，和内部 doctor 的中文展示一致，无对话模型要求；保留 Qwen3、auto OCR 和 sentence-transformers 配置 |
| 实际 STDIO MCP | 51 项名称和 inputSchema 与旧版快照逐项一致，仅删除 `generate_topic_artifact`；instructions 与安装包源码一致，无业务工具调用，未创建 MCP Vault |
| 安装包流程回归 | Gateway 协议 1 / 2、图文、笔记、共享、发布及合集知识重建共 `153 passed in 14.73s` |

独立空库检查刻意使用空模型缓存、未创建浏览器 Profile、未登记到个人 Obsidian；doctor 的 `overall=false` 是这些隔离条件的实际结果，不能当作生产媒体采集已就绪。检查证明这些缺项不会阻止空库、Web、Worker 和 MCP 启动，没有省略保留的识别或检索配置。

安装包测试只把工作区根目录加入 PYTHONPATH 以读取 `tests`，没有加入 `src`；另加入仓库外的验收 guard。所有受记录进程的包来源均为独立环境 site-packages。guard 阻断真实外部网络、对话模型旧模块、Keychain 和媒体 / 模型下载命令；实际阻断尝试为零。没有访问真实抖音账号、运行模型推理或下载模型。

已查看桌面亮色资料库与手机暗色阅读器截图，主栏、信息面板、正文及窄屏布局符合当前设计。其余布局检查和真实批次操作以浏览器报告及正式测试为依据，不用静态截图替代操作验证。

## 本轮发现与修正

1. [cli.py](../../../src/douyin_wiki/cli.py) 的 `init` 回执仍建议使用已停用的 provider 模式；隔离初始化实际复现后，只替换两段引导文字，明确使用已安装命令和 MCP / Gateway。初始化逻辑、命令参数和其他 AST 不变。
2. [README](../../../README.md) 和计划中的导出格式写为 `requirements-txt`，本机 uv 支持的值为 `requirements.txt`；已修正，并实际成功导出两份带哈希的依赖清单。runtime 清单不包含 pytest、Ruff 等开发工具；`httpx2` 是 MCP 的运行依赖，继续保留。
3. [verify_readwise_cleanup.py](../../../scripts/verify_readwise_cleanup.py) 的任务就绪条件误选隐藏的收藏进度条，首次执行超时。只将条件限定为 `#job-detail-view .progress-track`，原失败场景和剩余标签入口随后通过；已通过的六个浏览器入口没有重复运行。

完整回归执行后只改变两个 CLI 展示字符串和一个浏览器检查定位字符串。变更前文件、AST 等价检查、198 项输入快照、28 项定向检查和相应浏览器复验支持复用本轮全套结果。没有新增业务处理、协议、数据库、测试或依赖变更。

外部空库验收脚本的两次初始失败源于忽略 CLI 对状态和布尔值的中文展示。按 `localize_for_user(doctor(config))` 的既有契约修正后，空库检查通过；没有为验收脚本改变产品返回。原失败日志及独立临时库保留在 `TASK_OUT`。

## 当前生产状态

本轮只读探针确认 Web 仍为 `0.2.47`，Worker 活跃，维护服务已登记。Worker、Web、维护以及 Codex MCP 配置仍指向原仓库 `.venv/bin/python`；主工作区、暂存内容和五份安装配置与任务开始时一致。目标 runtime 尚不存在。

新包只安装到外部检查环境，尚未写生产 `dist/`、集成 `main`、切换 LaunchAgent / Codex MCP、初始化用户空库或归档旧版。当前聊天的旧 MCP 注册也没有声称已经刷新。

## 第 9 步部署清单

下一步沿用用户已确认的空库部署和本地 `main` 集成范围，不推送。执行前重新核对现场。

1. 保存主工作区本任务文档及原暂存状态，集成已验收分支，逐项恢复已确认文档；保留其他任务内容。仅在源码或测试出现新变化时补充对应验证。
2. 从已验收 wheel 和 `requirements-runtime.txt` 准备 `/Users/weisengao/Library/Application Support/douyin-wiki/runtime/.venv/`；核对非 editable 来源和版本，保留原仓库 `.venv`。
3. 备份五份现有安装配置中的运行配置，不读取或备份密钥；停止旧 Web、Worker、维护及旧库 MCP 会话，再处理用户 Vault。
4. 在 `/Users/weisengao/Documents/Obsidian/抖库` 清除已确认的 `raw/`、`wiki/`、`creators/`、`collections/`、`topics/`、`.douyin-wiki/`、`.git/` 以及将重新生成的 `AGENTS.md`、`index.md`、`log.md`。本轮顶层清单与原方案一致，没有符号链接；执行前再次检查。保留 `.obsidian/`、`.gitignore`、`.DS_Store`，不导出或迁回旧业务数据，Profile 和模型缓存保留。
5. 使用外部候选 `TASK_OUT/task-9-proposed-config.toml` 准备新版配置；SHA256 为 `0e647954acdb24b2d336bf7901d441aadb7680072bdb2eb10bb0be5e9eef38df`。它保留当前有效 Vault、Web、识别、检索和运行设置，已将旧 ASR 字段转换为当前配置，没有 `[llm]`。用新 runtime 在现有 Vault 路径初始化 schema 16 和新 Vault Git。
6. 将 Worker、维护、Web 三个 LaunchAgent 和 Codex `mcp_servers.douyin-wiki` 的启动解释器统一切换到新 runtime；保留有效 config / Profile 环境值，不修改其他 MCP、外部 Agent 模型设置或独立模型服务。重启服务并重新连接 MCP。
7. 用实际 HTTP、launchctl、doctor 和 tools/list 核对 Web `0.2.48`、Worker、维护与 51 项工具契约；验证停用入口、空库和保留页面。确认后按需放入已验收发行文件并记录实际安装。真实账号采集仍需显式选择。

## 证据与后续

关键证据：`task-9-checks.json`、`task-9-browser-checks.json`、`task-9-browser-screenshots.json`、`task-9-package-audit.json`、`task-9-release-commands.json`、`task-9-pip-check.json`、`task-9-wheel-check.json`、`task-9-installed-tests.json`、`task-9-verification.json` 及 `major-step-8-delivery.json`。`release-SHA256SUMS` 汇总两份发行包、两份依赖清单和配置候选的校验值。测试、报告和产物均记录 SHA256；原始失败材料保留，只有成功记录用于验收结论。

没有未处理的严重或重要问题。本步验收完成后等待用户确认第 9/10 步：本地主线集成、现有 Vault 空库初始化和四个运行入口切换。旧版源码与 wheel 归档仍留在第 10 步。
