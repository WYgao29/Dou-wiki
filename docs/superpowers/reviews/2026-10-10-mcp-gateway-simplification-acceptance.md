# MCP / Gateway 精简发布前验收

日期：2026-10-11。第 8/10 大步骤发布前验收（Task 9）、第 9/10 大步骤本机部署（Task 10）及第 10/10 大步骤归档与最终交付（Task 11）。结论：`0.2.0` 的源码、浏览器和独立发行包验收通过，并已在用户确认后部署到本机独立 runtime；现有 Vault 已按确认范围初始化空库。当前聊天重连后的实际 MCP 诊断与进程检查确认使用新 runtime、提供 51 项工具，旧环境的 MCP 进程为零；固定旧版归档和本次工作区清理也已完成，十个大步骤全部完成。

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

以上是第 8/10 步发布前验收记录；用户随后已确认第 9 步的主线集成、空库初始化和四入口切换，实际结果见下节。旧版源码与 wheel 归档仍留在第 10 步。

## 本机部署结果（2026-10-11，第 9/10 步）

用户确认上述部署清单后，由当前 Agent 执行 Task 10。已在本地 `main` fast-forward 集成 `3f06f9eaa5f1001a6ada4b75e5091d47b2d04c48`，没有推送；198 项源码、测试、脚本、构建及现行说明输入与 Task 9 验收相同，没有新增运行改动。主工作区原四份任务文档、工作 diff、空暂存补丁及演进 diff 保存到 `TASK_OUT/integration/`；设计与复核记录逐字相同，早期计划与索引使用已确认的新版本。原暂存为空，集成后仍为空。后续提交只更新部署文档、索引与维护规则中的实际环境说明。

从已验收 wheel 和带哈希 runtime 清单安装 `/Users/weisengao/Library/Application Support/douyin-wiki/runtime/.venv/`。Python `3.12.13`，包 `0.2.0`，93 个运行依赖兼容；包和 metadata 均来自该环境的 site-packages，非 editable，全部 90 个包文件逐字对应 wheel。未同步、删除或移动原仓库 `.venv`。

停止旧 Web、Worker、维护调度，终止两个旧 MCP 会话，确认旧解释器的业务进程和旧状态库文件句柄均为零后，在原 Vault 路径清除清单中的七个管理目录及三个重新生成的文件。没有导出或迁回旧业务数据。新配置的哈希与候选配置相同，没有 `[llm]`；保留有效识别、检索和运行设置。实际 `init` 退出 0，创建 schema 16、无聊天表的新状态库和新 Vault Git；`.obsidian`、`.gitignore`、`.DS_Store` 原有文件内容校验通过，Profile 与模型缓存保留。

三个 LaunchAgent 均使用新解释器和有效 config；Worker / Web 实际进程命令符合配置，维护调度登记正常。Codex 配置只改变 `mcp_servers.douyin-wiki.command` 一行，原 args、cwd、全部三个环境字段及其他配置字节不变。五份无密钥运行配置保存在仓库外 `TASK_OUT/deployment-config-backups/`；未备份 `[llm]`、Keychain 或其他 Codex 配置段。

| 实际运行检查 | 结果 |
| --- | --- |
| HTTP health | `200`，Web `0.2.48`，Gateway，Worker 活跃、队列为零 |
| 页面与空库查询 | 11 个页面 / 查询入口 `200`；旧模型设置页面跳转到分析设置 |
| 静态资源 | 24 个文件全部 `200`，响应字节与安装包一致 |
| 停用接口 | 16 组方法 / 路径实际返回 `410`，业务任务与资料未增加 |
| 新库 | schema 16，无聊天表，导入任务、资料、专题及其他业务表为空 |
| 实际 doctor CLI | 退出 0，与既有中文展示一致；`overall=true`、`qwen3.ok=true`，无对话模型要求 |
| 新 MCP 连接 | 按当前配置使用新 runtime 完成实际 STDIO initialize / tools/list，51 项名称和输入 schema 与发布前验收一致；stderr 为空，业务调用为零 |
| 当前聊天重连补验 | 实际 doctor 的三个模型准备可执行路径均来自新 runtime；当前进程命令正确，旧环境 MCP 进程为零；51 项工具名称与原生连接一致 |

Worker 按既有规则补做一次到期的空库周维护，另写入运行心跳；它们是本轮新运行记录。搜索索引内部初始化记录与独立空库相同，不能将这些内部记录当作旧业务残留。没有执行真实账号采集、模型下载或推理。九个受记录的验收进程均使用新 runtime，外部网络、Keychain 和旧模型模块的阻断尝试为零。

部署脚本检查中修正五个验收假设：保留既有 `PYTHONDONTWRITEBYTECODE` 环境字段；等待 `launchctl bootout` 后进程实际退出；用 SQLite 表类型识别空索引内部记录；按当前 MCP SDK 的序列化别名读取输入 schema；将旧版附注 tag 解析到实际提交再比较。这些修正仅涉及仓库外验收脚本，未改变产品或配置契约。实际问题与处理记录见 `TASK_OUT/task-10-validator-issues.json`。

已将同哈希 `0.2.0` wheel / sdist 放入主仓库 `dist/`。目标文件原先不存在，未替换其他包，原三个 dist 文件原样保留。部署说明见 `TASK_OUT/deployment.md`；核心证据为 `task-10-integration.json`、`task-10-runtime-origin.json`、`task-10-stop.json`、`task-10-reset.json`、`task-10-mcp-config-change.json`、`task-10-live-check.json`、`task-10-dist.json` 与 `task-10-verification.json`。

部署过程中，当前聊天曾显示旧缓存的 52 项工具；平台随后自动刷新为 51 项、生成工具缺席，但当时实际 connector doctor 的模型准备命令仍指向原仓库 `.venv/bin/hf`，两个缓存旧命令的 MCP 会话也重新出现。该命令由运行进程的 sysconfig scripts 路径生成，工具清单刷新不能代替实际连接验证。界面控制工具因安全限制拒绝访问 Codex，没有绕过限制；当时保留完成材料并请用户重连，未提前将 Task 10 记为完成。

2026-10-11 用户继续后，对本聊天调用一次只读 doctor，三个模型的准备可执行路径均为独立 runtime 的 `bin/hf`，`overall=true`、`qwen3.ok=true`；读取的 51 项工具名称与原生新连接一致。实际 MCP 进程使用新 runtime，旧解释器的业务进程为零。原始回执、清单和进程证据保存在 `task-10-current-chat-doctor-after-reconnect.json`、`task-10-current-chat-tools-after-reconnect.json`、`task-10-reconnect-processes-after-reconnect.json`；最新结论见 `task-10-reconnect-diagnosis.json`，重连前证据单独保留。

重连补验脚本首次将 doctor 的 `analysis_mode` 检查对象误作字符串，按已有 `ok` / `mode` 字段修正后通过；只修改仓库外检查，未增加 MCP 调用或产品改动。严格校验还发现 Codex 配置其他段的字节与部署当时不同；目标 MCP 段与已验证配置完全相同，本轮不修改或恢复其他段，保存当前哈希供最终复核。部署时仅改一行的证据保持不变，不能据此声称暂停期间其他配置也未变。具体见 `task-10-reconnect-validator-issue.json` 与 `task-10-post-reconnect-config.json`。

严格 `verify-task-10.py` 已通过，确认当前源码 / 安装包、服务探针、配置、空业务库及保留文件符合部署结果；本轮只补连接验证和四份维护文档，没有重复主线集成、安装、清除或初始化。第 9/10 步完成；旧版固定提交、tag 和原环境保留，第 10/10 步随后已获用户确认并完成，结果见下一节。

## 旧版归档与最终交付（2026-10-11，第 10/10 步）

用户确认后从固定提交 `6c4fb7b918e5b37e0faefa5fc8cbefdde82783bb` 生成旧版归档，标签 `legacy/douyin-wiki-0.1.5-2026-10-10` 未改变。源码和锁文件均声明 `0.1.5`，旧 wheel 仅由该源码独立构建。归档目录为 `/Users/weisengao/Documents/ChatGPT/archives/douyin-wiki-legacy/`，包含以下四个文件：

| 文件 | SHA256 / 用途 |
| --- | --- |
| `source.zip` | `58a27fc5b640a16334354681a8ddbf0ba470dcc379f6499745031233399af79b` |
| `douyin_wiki-0.1.5-py3-none-any.whl` | `71831c8bad71cc4c5812a33b4c5de43736bc42baa501e10231cd34f6e4e31950` |
| `README.md` | `164bec10072135f37e8c021c566b18fd6aa1c36e12c5225610988039a7144e41` |
| `SHA256SUMS` | 校验上述三份文件，不包含自身；实际 shasum 校验全部通过 |

源码 ZIP 的 232 个文件名及内容逐项对应固定提交；包括 src、tests、docs、scripts、pyproject、uv.lock 与许可证，没有业务库、凭据、Profile、模型或本机环境。旧包的 94 个文件与源码及独立安装字节一致，24 项静态资源、字体、Swift 资源、许可证和两个 console scripts 完整。独立 Python 3.12.13 使用同提交带哈希依赖清单，93 个运行包兼容，非 editable；CLI --help 和 pip check 通过。

实际旧版 STDIO initialize / tools/list 返回 52 项，名称、input schema 和 instructions 与 Task 1 基准逐项一致，包含 generate_topic_artifact。只读元数据检查没有创建 Vault 或 Profile，没有业务、模型、网络或 Keychain 调用，四个记录进程的 guard 阻断为零。首次 Requires-Python 文本比较失败来自等价约束的排序差异，改为 SpecifierSet 比较后通过；只修正仓库外检查，源码 / wheel 不变。恢复 README 写明系统条件、从原锁文件安装依赖、wheel / 源码安装方法及独立配置 / 空库路径；本轮没有执行旧版初始化或账号采集。

清理前的完整核对返回 PRE_CLEANUP_PASS，确认旧版归档、新版源码 / 包、51 项当前 MCP、实际服务、schema 16 空业务库和生产配置均有效。该检查未提前标记 Task 11 完成。本任务 worktree 已通过原生 archive_worktree 归档，平台附件记录为 archived_worktree；已集成的 codex/mcp-gateway-simplification 分支使用 git branch -d 删除。只移除本轮新建、确认未被修改的 legacy-build 和 legacy-check，原仓库 .venv 与其他 worktree 继续保留。

本计划的 ledger、briefs 和日志，以及忽略的八张 PNG，逐字保存在 TASK_OUT/final-worktree-evidence；旧版安装的 94 文件哈希、metadata、纯测试配置及环境说明保存在 legacy-install-evidence。当前源码、0.2.0 wheel / sdist 和独立 runtime 保持已验收版本，本步不增加运行代码或测试改动，未推送。生产配置字节与本步骤开始时一致；当前 MCP 的实际 doctor 使用新 runtime，51 项工具与新原生清单相同，Web 0.2.48、Worker 及维护服务正常。

最终完成校验从 main 执行，task-done 使用仓库外已保存且 plan-path 相符的执行目录，避免在主仓库生成临时执行材料。完整记录见 TASK_OUT/task-11-verification.json、task-11-cleanup.json 与 final-delivery.json；构建、旧版安装、工具清单、进程记录及保留材料分别见 task-11-build.json、task-11-legacy-check.json、task-11-legacy-mcp-tools.json、task-11-process-audits/ 和 task-11-preserved-evidence.json。

全流程由当前 Agent 执行及作者自查，没有独立审查者。正式 1503 项非 live 回归、八个浏览器入口及 153 项安装包流程验收的输入仍有效，本步只补归档安装和实际运行检查，未重复完整回归；真实账号采集不在本轮验证范围。当前没有未解决的实现问题。下一步为用户最终验收。
