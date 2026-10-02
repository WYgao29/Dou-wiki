# 仓库清理与目录整理 Implementation Plan

> **执行范围：** 默认由当前 Agent 执行，不委派子 Agent。首批 N1–N4、O7、O8、审核修正及 E1 通用自助接入框架已提交为 `7ccfba3`。O1–O6 与回退工具复审修复已验收，2026-10-02 按用户要求补记修复、同步发行包并提交 O1–O6；结果及提交后回退边界见下方收尾记录。真实客户端仍由调用方自行配置。技能启用遵循全局规则，不因读取本计划而自动启用。

**Goal:** 解决已确认的发行和文档问题，建立仓库维护与 MCP 调用的独立规则入口，按需整理局部职责，保持现有功能、对外接口与用户未提交改动。

**Architecture:** 保留 Python src 布局、Service mixin、原生 JS/CSS 和入口路径。根 AGENTS.md 服务于仓库维护者，docs/agents/ 提供通用自助步骤、参数和验收契约；gateway-agents.md 继续承载唯一共用业务流程。调用 Agent 自行发现客户端能力并转换原生配置，全部生效及运行文件位于仓库外；不维护客户端注册表或专用适配代码。共享聊天传输位于 adapters/chat.py，Web 版本常量位于 webapp/version.py，Web 测试支持位于 tests/support/web.py；旧导入与行为保持。

**Tech Stack:** Python 3.12（支持 `>=3.12,<3.14`）、uv/Hatchling、Typer、MCP、FastAPI/Uvicorn、Jinja2、Pydantic、SQLite、Playwright、原生 JS/CSS、macOS LaunchAgent。

**Spec:** [审计与目标结构](../../audits/2026-10-01-repository-audit.md)，[文件清单](../../audits/2026-10-01-repository-inventory.json)。

**Status:** 首批、E1 及审核修正已于 2026-10-01 提交为 `7ccfba33c9974a9374db8079f6e2768c31ceae24`。O1–O6 以该提交为实施基准，2026-10-02 将源码、支持文件与补记文档一并纳入收尾提交，提交标识以 Git 历史及仓库外收尾记录为准；发行包同步更新，未发布、未代配真实客户端。原审计基准 `81ce1fa` 与 JSON 快照保留历史语义。

## Global Constraints

- “第一阶段只做审计和方案，不修改或删除文件”：此为已完成的审计阶段约束；后续仅实施用户确认或当前明确要求的文件范围。
- “不直接修改 .git 内部，也不重写 Git 历史。”
- “确认后再实施，保持现有功能和对外接口，保留已有未提交改动。默认由当前 Agent 执行。”
- 命令 `douyin-wiki/douyin-wiki-mcp`、包名 `douyin_wiki`、Service 方法、MCP 名称/参数、CLI 别名、Web 路径/负载、状态码、Vault 路径/格式保持兼容。
- 不清理 Vault、SQLite、浏览器 Profile、密钥、系统服务配置、其他 worktree、Git 历史或 `.venv`。
- 不按日期、名称、零字节、静态无引用删除；批准必要清理不包含可选重构。
- 文档和缓存清理不新增镜像测试；代码抽取先用现有行为测试，确缺必要覆盖时采用 TDD。
- 本机服务指向主仓库 `.venv/bin/python`。可选代码阶段使用隔离 worktree/验证环境，不在现用环境盲目 `uv sync`；届时先用 using-git-worktrees 技能核对，仍由当前 Agent 执行。
- 用户计划接入的外部 Agent 只通过 MCP 调用，不直接修改仓库源码、测试、配置或执行仓库 Git 操作；允许的业务数据写入按其角色和既有工具确认条件确定。
- 接入规范文件不等于权限控制。外部客户端的规则加载、工具开放范围、源码写入能力和任务归属须在实际接入时核对；不将 gateway_context 作为已实现的身份鉴权或排他领取机制。

## Review Focus

| 条件 | 保持的行为 | 验收归属 |
| --- | --- | --- |
| 构建目录同时存在本地提示、截图、审查文件 | 包不夹带本地材料，运行资源和许可完整 | 阶段 1/7 包成员与 SHA |
| 用户有已暂存、未暂存或未跟踪改动 | 内容/索引状态保持，不混入本阶段提交 | 每阶段入口和收尾 |
| 旧集成用 purpose、旧 MCP 名称/导入路径 | 旧入口名称与参数仍可用 | 阶段 4/5 契约与测试 |
| 专题 provider、流式错误、鉴权或未配置模型 | 传输、密钥解析和错误语义不变 | 阶段 4 Web/专题回归 |
| 探针用旧源码、补丁、固定 Git 提交 | 整组归档可按原相对目录恢复复现 | 阶段 3 试恢复 |
| 多客户端接入，但没有实际加载规则或分配事件归属 | 核对客户端加载、能力范围；只处理和确认分配给自身的事件 | 阶段 2 文档核对、独立接入任务 E1 |

## 两轮内容的具体执行顺序

用户已确认并执行首批 **N1–N4 + O7 + O8**。随后明确第二批目标为通用自助接入框架：由调用 Agent 阅读项目文档并在仓库外自行配置。当前补齐 E1 文档与索引并同步发行包；wheel 仅更新 README 元数据，不配置某个客户端，不修改 src/ 或 tests/。上述为前两批当时的选择。用户本轮已另行选择全部 O1–O6，仍由当前 Agent 执行；结果见下方“O1–O6 实施与验收”。

| 批次 | 具体操作与文件范围 | 完成判据 | 批准范围 |
| --- | --- | --- | --- |
| A：维护与调用规则 | 阶段 0 后执行阶段 2 的 O7/O8：新建 AGENTS.md、docs/agents/README.md，补充 gateway-agents.md 和文档索引 | 维护/调用/Vault 三类职责分明；共用流程只有一处权威正文，链接有效 | O7 + O8；可以单独批准 |
| B：必要清理 | 阶段 1、阶段 2 的 N3/N4、阶段 7：打包配置、字体/UI/架构文档、两个发行包 | 新包不含本地材料且与最终源码一致；文档验收通过；原接口和用户改动保持 | N1–N4；建议与 A 同批，包最后生成 |
| C：通用 MCP 自助接入框架 | E1：自助步骤、参数契约、规则加载、外部验收格式；客户端自行转换原生配置 | 与客户端无关；路径及启动参数可验证，规则和验收不依赖仓库内客户端文件 | 用户当前明确目标；本批只交付通用文档，实际客户端自行在仓库外接入 |
| D：可选整理 | 原阶段 3–6：缓存/证据归档/聊天传输/版本/测试夹具/私有 helper | 对应阶段验证、补丁和回退材料完整 | O1–O6 分别选择；批准 A/B 不包含 D |

A/B 可合为一次批准。实施顺序为阶段 0 → 阶段 2 → 阶段 1 → 阶段 7；阶段 1 的候选包仍须验收，阶段 7 只有最终内容变化时重新构建，若候选已覆盖全部最终内容且哈希一致可直接复用。若只批准 A，则只做阶段 0/2 的 O7/O8，不重建 dist；若只批准 B，则不创建 Agent 规则文件。

已采用的首批确认：“确认执行首批 N1–N4 + O7 + O8，由当前 Agent 执行；可选整理和真实客户端配置暂不执行。”这句话只批准列明的仓库文档与发行工作，不批准发布、系统服务操作、外部消息发送或真实数据验收。

## 清理与迁移清单

“必要”为建议默认批准的确认问题；“可选”需另选；“待确认/保留”不执行删除。

| ID / 对象 | 原因 | 影响与处置 | 验证 | 回退 |
| --- | --- | --- | --- | --- |
| N1 必要：`.gitignore`、`pyproject.toml` | 旧 sdist 包含仅本地排除的 Pi 提示 | 共享忽略 `.pi/`、明确 sdist 排除；不改提示、依赖和入口 | 在本地材料仍存在时构建到临时目录，枚举禁止路径/运行资源 | 仅回退两处配置补丁 |
| N2 必要：两个 `dist/` 发行包 | wheel 中 33 个文件落后于当前源码，sdist 边界错误 | 备份后重建，不发布、不改版本；最终替换放在所选改动全部完成后 | 包成员、全量包源码 SHA、独立环境 CLI help | 恢复校验过的旧包并注明历史问题 |
| N3 必要：字体 README | Inter 行内路径不存在 | 指向实际资源与复用方式，不复制字体 | 路径存在、4 个输出哈希匹配、字表/许可完整 | 回退文档补丁 |
| N4 必要：UI 状态与脚本索引 | 已完成规格仍标“实施中”，验证列表缺标签脚本 | 明确历史规格/当前权威关系，补齐 8 个入口与模块职责 | 链接/路径、现有文档测试；历史测量保留日期语义 | 回退文档补丁 |
| O1 可选：116 个缓存/Finder 文件 | 可再生，共约 3.83 MiB | 只清单中明确缓存，不递归清全部忽略文件 | 原源码、配置、证据哈希不变 | 相应工具自然再生 |
| O2 可选、本轮已选择：审查/截图 | 混合再生截图与历史恢复材料 | 保留或整组归档；不能单删旧源码、补丁、比较记录 | 归档清单/SHA、固定提交可读、试恢复 | 按原相对路径恢复整组 |
| O3 可选：`adapters/chat.py` | Service 反向依赖 Web 聊天 | 提取纯传输，保留旧导入，Web 留上下文构建 | Web/专题/接口、聊天浏览器回归 | 撤销独立源码补丁/提交 |
| O4 可选：`webapp/version.py` | 操作服务为版本导入 app | 提取常量，app 继续导出原值 | 健康信息、模板缓存参数及原导入 | 撤销常量抽取补丁 |
| O5 可选：`tests/support/web.py` | 脚本/测试直接导入测试文件夹具 | 先抽 Web helper，保留脚本路径/断言 | 收集不减少、完整离线回归、8 个消费者脚本 | 整组撤销 helper 与调用者 |
| O6 可选、本轮已选择：`_chunk_segments` | 跟踪内容只有定义 | 只处理该私有 helper，不删除 LLM 模块 | 重查引用/动态名称及分析回归 | 原样恢复函数 |
| K1 保留：`replace_reminders` | 内部无调用，但为公开方法 | 外部消费者未知，不删除 Python 接口 | 如需弃用另立兼容方案 | 当前不变 |
| K2 待确认、默认保留：品牌 PNG | UI 无引用，但文档要求保留、静态 URL 可访问 | 不自动删除；历史资产策略需单独决定 | 明确决定前不行动 | 当前不变 |
| K3 待确认、默认保留：Pi/Obsidian | 本地用途未知，Pi 有旧人工复核步骤 | N1 只排除打包；仍使用时另行更新提示，不公开配置 | 对照当前 Gateway/MCP 流程 | 授权更新时保留原副本 |
| K4 保留：活跃方案、测试、字体/Swift/许可、兼容别名 | 存在分支、代码、测试、资源/接口依赖 | 不作无用文件删除 | 对应依据/入口保留 | 当前不变 |
| O7 可选、建议纳入首批：项目 `AGENTS.md` | 缺少项目维护入口 | 写维护职责、入口与目录边界，链接全局/项目权威记录，不复制全文 | 链接有效，不混淆 MCP 调用规则或 Vault 模板 | 回退本次新增文件 |
| O8 可选、建议纳入首批：`docs/agents/README.md` 与既有 Gateway 指南 | 多个只调用 MCP 的 Agent 需要独立接入入口 | 新建接入索引，补调用边界、工具角色、规则加载和事件归属；不移动旧指南、不配置真实客户端 | 文档链接、实际工具签名、既有文档测试 | 撤销新增文档及对应局部补丁 |

## 阶段 0：确认与实施基准

**Files:** 只读取当前工作状态、候选清单与规则。
**Interfaces:** 输入为明确选择的 ID；输出为阶段路径清单和回退材料。

- [x] 用户已确认首批 N1–N4 + O7 + O8、随后明确 E1 文档框架，本轮明确全部 O1–O6；各批授权分别记录，K1–K4 保留。
- [x] 重新核对 HEAD/分支、索引/工作区差异、未跟踪/忽略候选，与审计快照比对；新用户改动保留，不覆盖。
- [x] 分别保存本阶段原文件、索引差异、工作区差异；混有用户修改时仅做可检查的局部修改。禁止 blanket git add、git clean、reset --hard 或自动 stash。
- [x] 文档/打包配置可在当前工作区精确改动；可选源码阶段在隔离 worktree 按确认基准及必要补丁验证。现有画面检索 worktree 不复用、不修改。
- [x] 只交付本阶段路径。如采用独立提交，只提交本阶段内容；回退用 `git revert <本阶段提交>` 或核对后反向应用对应补丁，不撤销用户修改。

## 阶段 1：发行边界（N1、N2 的候选验证）

**Files:** Modify `.gitignore`、`pyproject.toml`；只读并备份旧两个发行包，不改 `.pi/prompts/抖库.md`。
**Interfaces:** 版本 `0.1.5`、两个 console scripts、依赖、包名和资源保持。

- [x] 共享忽略增加 `.pi/`；sdist 明确排除 `.pi/**`、`.obsidian/**`、`.test-*/**`、`.codex-tmp/**`、`.superpowers/**`、`.venv/**`、`.worktrees/**`、`docs/screenshots/**`、缓存/构建目录。pattern 用当前 Hatchling 支持的格式，最终以实际包成员验收。
- [x] 原本地材料仍在时执行 `uv build --offline --out-dir "$repo_audit_build"`，变量指向本阶段新建临时目录。后端缓存不足则明确报告，不宣称构建成功，不修改现用虚拟环境。
- [x] 用 zipfile/tarfile 枚举成员：无禁止前缀、字节码或缓存；wheel 全部 `douyin_wiki/` 文件与源码逐字节一致，模板、12 个 JS、2 个 CSS、字体/许可、图标、Swift 均存在。
- [x] 在独立临时 Python 3.12 环境安装候选 wheel；`douyin-wiki --help`、`python -m douyin_wiki --help` 退出 0；entry_points.txt 保留两个入口。不启动 MCP/Worker，不初始化真实 Vault。
- [x] 保存旧包及 SHA/原基准、候选验证结果。最终 `dist/` 替换延后至阶段 7，避免文档/可选代码完成后包又过时；不删除 `dist/.gitignore`。
- [x] 核对只有两处配置变更，不改提示/用户工作状态。不因打包配置变化而跑完整功能回归。

回退：反向应用两处配置补丁；原发行包尚未替换。其旧问题保留在审计记录中。

## 阶段 2：文档准确性与 Agent 规则（N3、N4；O7/O8 按选择）

**Files N3/N4:** Modify `scripts/fonts/README.md`、`docs/web-ui-redesign-readwise.md`、`docs/web-ui-design-system.md`、`docs/architecture.md`、`docs/README.md`。\
**Files O7:** Create `AGENTS.md`。\
**Files O8:** Create `docs/agents/README.md`；Modify `docs/gateway-agents.md`、`docs/README.md`。未选的文件不改；按后续 E1 澄清，不建立仓库内 `docs/agents/clients/`，客户端差异与验收记录位于仓库外。\
**Interfaces:** 文档命令、职责和路径与现有实现一致；保留 gateway-agents.md 的路径和现有流程，不改 MCP 工具、运行指令常量或 Vault 模板。

- [x] Inter 路径改为 `src/douyin_wiki/webapp/static/fonts/InterVariable.woff2`，说明原型复用；保留字表、来源、哈希、生成方法与许可。
- [x] 重设计开头改为“已完成的设计规格与历史实施记录”，链接当前设计系统；初期“截图目录不存在”等限定到历史基准，保留原尺寸/测试数字的日期语义。
- [x] 补齐 8 个脚本及输出目录；architecture 补标签、收藏存储/模型、本地化/时间工具职责；索引加入此次审计与待执行计划。
- [x] O7：用 AGENTS.md 大写复数名称；说明本文件适用于仓库维护者，写明 src/、tests/、scripts/、docs/、资源/发行目录职责，链接 README、architecture、service-collaboration-api 和 Gateway 指南，指向当前维护机器的全局规则 `/Users/weisengao/.codex/AGENTS.md`。不复制全局规则，不将本机绝对路径当作其他机器的固定前置条件。
- [x] O7：标明 Vault 生成的 AGENTS.md 是知识库内容规则；外部 MCP 客户端不因连接服务就必然读取仓库 AGENTS.md。验证入口沿用 README/architecture 的正式命令，不把本轮历史测试数字写成常驻要求。
- [x] O8：docs/agents/README.md 作为接入入口，链接既有 gateway-agents.md；共用业务顺序和确认条件继续只在旧指南维护，各客户端差异与实际加载方式只记录在外部验收文件。
- [x] O8：在 Gateway 指南补“调用边界与多客户端”说明：客户端只使用所开放的 MCP 工具；知识库数据写入与源码修改分开；不得直接修改源码/测试/配置或执行仓库 Git 操作。工具角色分“查询”和“采集/分析”，已有长视频、提醒、专题保存等确认条件沿用现行指南，不新增逐次审批。
- [x] O8：查询角色列出 search_knowledge/get_entry/get_job/list_jobs/list_creators/get_creator/list_topics/search_topic；采集/分析角色另开放实际任务所需工具。客户端不支持可靠工具限制时如实标明，不以提示词或目录名称宣称权限已经强制落实。
- [x] O8：每个客户端使用独立 gateway 标识并保存完整会话路由；按分配的 gateway/channel/conversation 处理事件，不确认其他客户端事件；处理前重读 get_job/get_analysis_context，成功后才 acknowledge。当前 list_job_events 只按游标/未确认/limit 查询，规则只能指导配合，不能宣称已有服务端按 Agent 授权或排他领取；无法划分归属时先限定一个写入/分析执行者，其余作查询入口。
- [x] 检查所选文档的本地链接和行内命令路径；选择 N3 时复核 4 个字体 SHA 及字表/许可；以 `PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -q -p no:cacheprovider tests/test_documentation.py` 验收，现有 1 项测试应通过。文档阶段不扩展完整功能回归。
- [x] git diff --check 通过，仅改上述文档，不新增复述实现的测试。

回退：撤销该组文档补丁或独立提交。

## 阶段 3：缓存与历史材料（仅 O1/O2 获选）

**Files:** 只处理确认时仍存在且类型匹配的清单路径。
**Interfaces:** 保持应用、正式测试、本地配置和历史恢复契约。

- [x] O1：从清单挑选 116 个缓存/Finder 路径，重查类型、归属及内容，精确清理；不执行 git clean -fdx，不进入 .venv 或其他 worktree。
- [x] O2：本轮选择仓库外长期整组归档，无到期自动删除；对 56 个质量材料和 140 个截图/记录列清单，分清当前可再生截图与历史比较证据，不按年龄删除。
- [x] 如归档，保留原项目相对结构、两份旧代码、探针/补丁/XML/清单，以及固定历史基准 `4c9c3a3ddf7397c3cdac5149e81b5d52ffceff59` 和当前基准；验证归档 SHA，再在独立目录试恢复。
- [x] 试恢复能读取 round2/3/4 探针所需原路径。若要求复现，在临时 Vault 环境执行对应探针，不访问真实用户数据。
- [x] 归档验收完成且移除原路径明确获选后才移除，保留恢复说明；不能去重不同配置槽位。
- [x] 核对源码、配置、正式测试、保留证据和用户工作状态不变。

回退：缓存自然再生；历史材料按归档清单还原。没有可验证恢复来源的文件继续保留。

## 阶段 4：局部源码边界（O3/O4 独立选择）

**Files O3:** Create `src/douyin_wiki/adapters/chat.py`；Modify `webapp/chat.py`、`webapp/app.py`、`service_entries.py`；核对 `tests/test_web.py` 的 monkeypatch。\
**Files O4:** Create `src/douyin_wiki/webapp/version.py`；Modify `webapp/app.py`、`webapp/__init__.py`、`web_operation.py`、`tests/test_interfaces.py`。\
**Interfaces:** 保留 `ChatChunk(text: str="", usage: dict[str,int|None]|None=None)`、`ChatProvider.model/configured`、`stream(messages: list[dict[str,str]]) -> AsyncIterator[ChatChunk]`、`OpenAICompatibleChatProvider(settings: LLMSettings)` 及旧 webapp.chat 导入路径；ChatContextBuilder 留原处。WEB_VERSION 保持 `"0.2.43"`，app 仍导出该常量。

- [x] O3：在隔离环境记录 `pytest -q -m 'not live' tests/test_web.py tests/test_topics.py tests/test_interfaces.py` 基线；特别核对 test_web.py:1362,1373,1398 的密钥注入。
- [x] O3：提取纯 HTTP/SSE provider、protocol、chunk；Service 直接依赖 adapters；原 webapp.chat 兼容导出同名对象。测试注入路径可随实现迁移，不能删除旧行为断言或改变密钥/错误/流式语义。
- [x] O3：定向集合由最终完整离线回归覆盖；通过 Ruff 与 verify_readwise_chat.py；检查 Service→webapp.chat 依赖已消除、注册入口与关键负载保持。完整离线集在所有所选代码修改完成后统一运行。
- [x] O4：记录现有 Web/operation 基线，迁移常量；app.WEB_VERSION 保持，WebOperationService 直接从 version 模块读取，缓存参数不变。
- [x] O4：对应 Web/operation 定向集合由最终完整离线回归覆盖，并通过 Ruff；检查健康信息、模板和旧导入版本值一致。
- [x] O4：独立安装验收发现 webapp/__init__.py 提前加载 app；新增独立进程回归，确认先失败再通过，以延迟导出保留 create_app/run_web 原对象及 dir 可见性；读取版本不加载 app。
- [x] 两项分别交付最小补丁/可选独立提交，不捎带拆分路由、数据库或采集流水线。

回退：只撤销对应补丁/提交，没有数据迁移，不重建或回滚用户状态库。

## 阶段 5：共享测试支持（仅 O5）

**Files:** Create `tests/support/__init__.py`、`tests/support/web.py`；Modify `tests/test_web.py`、导入其夹具的全部测试及 8 个 `scripts/verify_readwise_*.py`。保留 tests/__init__.py、脚本 ROOT 推导、路径和输出目录。
**Interfaces:** 原样提取 FakeChatProvider、ConfigurableFakeProvider、TopicFakeProvider、`local_client(app, **kwargs)`、`_web_fixture(tmp_path: Path) -> tuple[AppConfig, DouyinWikiService]`；收藏/博主夹具及其他测试命名暂不动。

- [x] 用导入清单列出全部调用者，记录 `pytest --collect-only -q -m 'not live'` 的 nodeid/参数化数量和现有离线基线。
- [x] 抽取上述 helper 到 support，更新调用者；必要时旧测试模块兼容导出 helper，保持断言原样。
- [x] 重新收集，测试及参数化不减少；Ruff、完整非 live 回归及 8 个脚本通过。所有脚本均用被迁移夹具，因此属于相称验证。
- [x] 不顺带改名 round2/quality 文件，不移动验证脚本；如历史脚本预期冲突，定位并记录，不靠删除断言验收。

回退：整组撤销 support 和调用者导入补丁，保留迁移前基线。

## 阶段 6：私有函数候选（仅 O6 明确获选）

**Files:** Modify `adapters/llm.py` 的 _chunk_segments；公开 replace_reminders 不在删除范围。
**Interfaces:** provider、配置、模块路径和公开方法不变。

- [x] 重新检查引用、配置、装饰器和动态名称，没有新消费者且明确获选才删除这一 helper。
- [x] 跑现有 test_media.py、test_media_models.py、test_analysis_v2.py、test_transcript_quality.py 的非 live 测试及 Ruff，不新增“函数不存在”测试。
- [x] 交付独立最小补丁；发现消费者即原样恢复。

回退：恢复函数或撤销对应提交。

## 阶段 7：所选变更收尾与新包（完成 N2）

**Files:** 仅本次所选路径、`dist/douyin_wiki-0.1.5-py3-none-any.whl`、`dist/douyin_wiki-0.1.5.tar.gz` 和必要验证记录。
**Interfaces:** 包与最终源码一致；所有原接口、运行资源、用户数据和工作状态保持。

- [x] 有源码/夹具改动时跑完整 `pytest -q -m 'not live'`；若最后一次完整集已覆盖全部最终改动，复用证据。文档/打包/缓存清理不另扩大功能测试。
- [x] 对最终所选状态完成阶段 1 包成员/SHA/CLI 验收。阶段 1 之后有新文档或源码改动时重新构建；候选已覆盖最终内容且哈希一致时复用，不重复构建。不用落后于最终内容的中间包替代最终包。
- [x] 旧包备份及恢复清单校验后，替换 dist 的两个对应文件，保留 dist/.gitignore；记录包对应的 Git 基准及未提交差异指纹，不发布。
- [x] 检查 Git 变更范围、所有用户原始内容/索引状态、本机安装路径和保留证据；不操作系统服务，不把配置存在当作运行探针结果。
- [x] 只清理本次创建且不再需要的临时验证文件；保留交付物、复现/回退材料和验证记录。补充实施结果，并确认本次审计材料的长期保留方式。

回退：针对本阶段恢复旧包并撤销本阶段补丁；原包的已知问题仍应明确保留历史标记。

## 第二批 E1：通用自助接入框架

用户明确需要通用文档框架，其他 Agent 阅读后自行配置，并且不在代码仓库生成额外文件。此前“逐个配置实际客户端”的提议不再作为本轮范围。客户端名称、配置位置和原生 schema 由调用 Agent 自行发现；项目只维护通用契约，实际配置、规则快照、日志、状态和验收结果均在仓库外。

**Files:** Modify `docs/agents/README.md`、`docs/gateway-agents.md`、根 README/AGENTS 及文档索引、审计和本计划；Create `docs/agents/self-setup.md`、`docs/agents/integration-contract.md`。发行包随最终文档更新，wheel 仅更新 README 元数据；不创建仓库内 clients、注册表、适配器或安装脚本，不修改 src/、tests/、MCP 服务端指令或系统服务。\
**Interfaces:** 现有 STDIO 及 `-B -m douyin_wiki.mcp_server`、`DOUYIN_WIKI_CONFIG` 和 `DOUYIN_WIKI_BROWSER_PROFILE`；业务工具及参数保持。

- [x] 接入入口链接自助步骤、参数契约和唯一共用 Gateway 业务规范，不绑定客户端名称。
- [x] 定义真实绝对路径、仓库外目录、已有 Python 启动参数、默认查询角色和独立实例标识；配置缺失时停止，避免默认真实 Vault 回落。
- [x] 定义客户端能力发现、原生配置合并、实际规则加载、工具限制和不足能力的记录方式；启动不在仓库同步依赖或写字节码。
- [x] 提供隔离配置、外部验收结果、重复接入和回退规范；不在仓库维护 clients 或实例注册表。
- [x] 核对 JSON/TOML 模板、链接、工具签名；从外部 cwd 用真实 STDIO 验证 initialize/tools/list/查询和进程重连，不触及真实 Vault。
- [x] 按最终文档重建发行包，核对包成员、改动范围、Git 与现用环境；将本轮验证及回退材料放在仓库外。

本轮已核对两份 JSON 模板、一份 TOML 模板与共用工具角色；使用当前 MCP SDK 2.2.0 从外部带空格目录完成两次 STDIO 启动、initialize、44 个工具列举及隔离查询。现用 `.venv` 37,859 项元数据保持，原 Git HEAD/索引保持。候选 wheel 79 个成员、sdist 159 个文件，74 个运行文件逐字节保持；wheel 只有 README 元数据与对应 RECORD 更新。最终记录和回退副本保存在当前维护机器仓库外的 `integration-maintenance/` 独立目录，绝对位置由本次交付说明提供，不作为通用配置参数。

实际客户端适配属于后续调用 Agent 的自助行为。通用探针不证明每个客户端已落实工具过滤、后台续跑或原会话投递；这些能力由实例按外部验收契约分别记录。真实数据范围沿用用户已有明确授权，缺失时保持隔离，不新增逐次审批。

回退：当前文档框架只撤销本批文档和源码发行包补丁，先核对交付后 SHA，保留其他未提交内容。后续实例只回退自身仓库外配置和状态，不修改项目或把配置回退当作业务数据回退。

### E1 复审修正：切换目标时重建连接

用户要求修复复审的 P2，由当前 Agent 沿用 Superpowers 的反馈核验、问题定位和验证流程执行。修正仅涉及 `docs/agents/self-setup.md`、`docs/agents/integration-contract.md`、本计划及审计记录，源码发行包同步；wheel 未受影响并复用，不修改运行源码、接口、客户端或系统服务。

- [x] 核对 `_SERVICE` 的首次业务调用缓存，补充暂停本实例监控、等待在途调用结束、确认旧 STDIO 进程退出及重新握手的顺序。
- [x] 配置与 Profile 环境同时切换，复核实际新进程的 Vault / Profile 和适用的监控环境；切换或回退时重新验收，不能沿用隔离结果或只还原文件。
- [x] 外部验收契约增加 `connection_recreated`、`target_binding` 与 `monitor_binding`；没有监控的查询实例保持该项 `not_requested`，不增加逐次审批。
- [x] 在外部目录以两个隔离 Vault 的不同 job 完成旧连接缓存、新进程切换、目标查询、CLI 配置一致及新进程回退验证；三个进程退出 0，列举 44 个工具，既有文档测试 1 项通过。

验证和修复前副本保存在仓库外独立目录，不覆盖前两批恢复材料。交付时按最终文档重建源码包并核对包内容、wheel SHA、现用环境和原 Git 状态。本次文档修复的回退只恢复上述四份文档及本次源码包，先核对前后 SHA；实例配置的回退流程按自助步骤 7 执行，不能据此撤销业务数据。

## 首批验收与回退记录

阶段 0、1、2、7 已完成，原 HEAD/索引保持，8 个已有文件的变更限定于文档与打包配置；新增根 AGENTS.md 和 docs/agents/README.md。未改 src/、tests/、uv.lock 或运行资源，未清理用户配置、历史证据、缓存、现用 .venv 或系统服务。

既有文档测试 1 项通过；链接/锚点、4 个字体 SHA、6500 个唯一字符/许可、8 个脚本与查询工具核对通过。离线构建的 wheel 有 79 个成员，sdist 有 157 个文件；74 个包内文件与源码一致，本地提示和缓存等禁止路径不存在。独立 Python 3.12.13 安装及两种 CLI help 调用通过，未初始化 Vault。

首批复审后已修正一项 P2：Gateway 指南每轮从零分页扫描混合事件，按整页末尾推进游标，跳过其他客户端事件后继续翻页；处理失败不阻塞后续事件，退避与下一轮重试保留。实际 CLI / 数据库的内存验证覆盖 100 条其他客户端积压、自己的后续事件、交付失败、退避、重启重扫和空数组；既有文档测试通过。此次只更新指南、审计及本计划记录；源码发行包随文档更新，wheel 的运行源码保持。修复前副本及验证记录放在执行目录的 `event-pagination-fix/`。

旧包和原文档已复制并校验，dist 两个文件已替换，dist/.gitignore 保持。[执行记录与回退材料](</Users/weisengao/Library/Application Support/douyin-wiki/integration-maintenance/2026-10-01-o1-o6-etwtkq2_/local-remnants-cleanup-20261002-p2el98s2/README.md#首批资料定位>)保存基准、差异补丁、最终包哈希和验证日志，已于 2026-10-02 整组外置。首批旧脚本默认只核对，原历史状态匹配时才适用；其目录推导依赖不能直接用于搬移后的目录。本次材料恢复入口及映射见归档说明，不操作 Git 索引或历史。最终包按包括本记录的文件状态构建，运行源码和 wheel 相同则复用已通过的 CLI 证据。

首批收尾时 O1–O6 和原实际客户端接入 E1 均未执行。随后 E1 按用户澄清改为通用文档框架；O1–O6 当时未选择；用户本轮已另行明确并完成。未委派 Agent，不把文档限制当作已落实的客户端权限或并发隔离。

## O1–O6 实施与验收（2026-10-01）

用户明确要求先提交既有工作再处理 O1–O6。首个 commit 为 `7ccfba33c9974a9374db8079f6e2768c31ceae24`；本轮代码在独立 worktree 和仓库外验证环境实施，通过验收后将局部补丁同步原仓库，当时不追加提交、不推送或发布。该次交付保留原仓库索引，未改 uv.lock、依赖或产品版本，未委派 Agent。2026-10-02 的追加提交另见收尾记录。

| 项目 | 实际结果 | 验证与回退 |
| --- | --- | --- |
| O1 | 精确清理原清单的 116 个缓存/Finder 文件，4,017,926 bytes；先保存原文件 | SHA/类型复核；自然再生或从 o1-cache.tar.gz 恢复 |
| O2 | 整组归档 56 个质量材料和 140 个截图/比较记录，共 196 个、27,381,016 bytes，验收后移除原件 | 两组归档逐文件 SHA、试恢复、round2/3/4 所需旧路径及固定历史提交可读；按原相对路径恢复，长期保留 |
| O3 | 纯 HTTP/SSE provider、protocol、chunk 原样移入 adapters/chat.py；Service 直接依赖它，webapp/chat.py 保留上下文与同名兼容导出 | 原 AST、密钥/错误/流式语义、旧导入对象一致；回退本组补丁 |
| O4 | WEB_VERSION 移入 webapp/version.py；app 继续导出，包入口改为延迟导出原 create_app/run_web，WebOperationService 不再加载 app 获取版本 | 值仍为 0.2.43；独立进程回归 RED→GREEN、健康信息/模板/旧导入通过；回退本组补丁 |
| O5 | 五个 Web helper 原样抽入 tests/support/web.py，更新 10 个正式测试模块和 8 个脚本；合并重复 local_client | 原 667 个非 live nodeid 与既有正式测试函数 AST 保持，另加 O4 独立进程回归；8 个脚本通过；整组回退 support 和调用者 |
| O6 | 删除唯一跟踪定义的私有 _chunk_segments，23 行；其余 LLM 实现保持 | 重查全部跟踪内容、配置与脚本无运行消费者；媒体/分析回归由完整离线集覆盖，函数之外 AST 保持；原样恢复函数 |

验证环境使用 `uv sync --offline --frozen --extra dev --extra embeddings`，与现用 .venv 分离。最初仅 dev 环境的完整基线为 666 通过、1 失败：既有用例直接导入 sentence_transformers，缺少 embeddings extra。补齐隔离依赖后，所选 Web/operation/媒体/分析集合及该用例的定向基线 171 项通过，首轮完整回归 667 项通过。独立安装随后发现 O4 的包初始化依赖，新增独立进程回归先失败再修正；最终完整离线回归为 **668 通过、1 项 live 未运行**。原 667 个 nodeid 与参数化保留，只新增这一项；Ruff 覆盖 src/tests/scripts 通过，八个浏览器入口在最终源码上重跑通过。最终只改文档时复用已覆盖全部代码的完整集证据。

浏览器验收发现五个既有脚本存在过时预期；在原提交上复现并核对当前实现后修正，产品 JS/CSS、路由和负载保持。资料库改用当前保留文案并增加数据库 favorite/KEEP 断言，手机标签使用卡片；阅读器分别检查模拟 503 错误和真实 404 缺页；聊天等待首个 SSE 片段，用同文章会话检查历史切换竞态，并操作当前确认对话框；操作脚本模拟短链分类，检查当前命令筛选和空白聊天草稿，等待异步来源焦点恢复后保留原断言；快捷键选择器限定资料库页面。既有正式测试断言未删改，脚本没有减少断言，也未通过改产品行为让旧预期通过。

wheel/sdist 按最终源码和文档重建，包成员、全量运行文件与源码/文档 SHA、许可/字体/Swift、入口与独立安装 CLI help 验收通过，dist 旧包先备份再替换，版本仍为 0.1.5。SHA 和完整验证记录在仓库外，避免把包哈希写入自身包含的文档。未运行 live 账号/真实媒体模型测试，未配置真实客户端或操作系统服务。

清理阶段原仓库另有 218 个非环境文件保持，现用 .venv 的 42,371 项文件/目录/链接元数据及安装配置保持。原 JSON、前几批回退目录、本地 Pi/Obsidian 配置、品牌资产、公开 replace_reminders、画面检索分支/worktree 保留。当前阶段截图来自独立 worktree，另存外部验证目录，不覆写已归档的历史截图。

本机长期归档与本轮验证/回退材料位于 `/Users/weisengao/Library/Application Support/douyin-wiki/integration-maintenance/2026-10-01-o1-o6-etwtkq2_`；这是维护记录位置，不是其他机器的安装或运行前提。`archive-manifest.json` 保存归档与全部原文件 SHA、原相对路径、mtime 和 mode；`restore_archive.py` 默认只核对，`--apply` 才恢复，遇到后来内容冲突即停止。

`progress.md`、原仓库基准、最终差异补丁、交付后哈希、旧发行包和 `rollback_changes.py` 一并保留。该脚本针对本轮最初的未提交交付，只处理清单路径，先核对交付后 SHA、HEAD 和索引，发现后来改动即停止；提交后的回退方式见下方收尾记录。归档恢复单独执行，不回退用户数据。当前 Agent 完成整组自审与契约核对，没有宣称独立 Agent 复审或生产状态探针通过。

## 回退工具复审修复（2026-10-01）

复审发现两项 P2：恢复目标的父目录为符号链接时，旧工具可能写到仓库外；Git、文件及归档保护使用 `assert`，在 `PYTHONOPTIMIZE=1` 或 `-O` 下会失效。已修复仓库外的 `rollback_changes.py` 和 `restore_archive.py`：拒绝绝对路径、上行路径及根目录内的父/叶符号链接，核对解析后的目标仍在所选根目录内，并在每次写入前复核；保护条件改用显式判断和异常。默认只核对、显式 `--apply` 与成功 JSON 保持。

修复前真实恢复回归为 39 失败、27 通过，修复后 **66 项通过**，覆盖普通模式、环境优化和 `-O`。原项目两工具共 6 次只读核对通过，两组真实归档的 312 个文件在仓库外试恢复，SHA、mode 和 mtime 均符合清单；没有对原项目执行 `--apply`。产品源码、项目文档、发行包、原归档、HEAD/索引、现用环境和安装配置在该次修复中保持。

[修复记录与可重复验证](</Users/weisengao/Library/Application Support/douyin-wiki/integration-maintenance/2026-10-01-o1-o6-etwtkq2_/recovery-guard-fix-e9awvxx4/README.md>)保存前后工具、副本、差异补丁、回归源码、日志和完整状态对比。修复前脚本只作为历史复现材料；当前工具及这些材料继续位于仓库外。

## 提交收尾（2026-10-02）

用户明确要求“补记修复 → 同步发行包 → 提交 O1–O6”。本次补齐本计划、审计报告和文档索引；O1–O6 的源码、脚本与测试逐文件 SHA 仍匹配已验收交付，复用此前 668 项离线回归和八个浏览器入口的证据。本次另核对文档链接/锚点、既有文档测试、Ruff 和发行包全量成员，不重复完整功能回归。

最新文档纳入重建的 sdist；wheel 的全部运行文件和 README 元数据不变，逐字节核对后复用。版本仍为 0.1.5，34 个项目文件纳入 O1–O6 收尾提交；`dist/` 保持忽略，两个发行包不纳入 Git。外部回退工具、回归和归档不移入代码区；新出现且与源码匹配的忽略字节码保留，现用 `.venv`、安装配置及原恢复材料保持。

[收尾与回退记录](</Users/weisengao/Library/Application Support/douyin-wiki/integration-maintenance/2026-10-01-o1-o6-etwtkq2_/commit-closeout-20261002-jxowzaz5/README.md>)保存本次入口状态、旧包和文档副本、检查日志、最终包 SHA 及实际提交标识，原有清单和历史报告不覆盖。该本机路径只供维护追溯。

提交后，先核对工作区及后续改动，再用 `git revert <本轮 O1–O6 提交>` 回退跟踪文件。发行包不受 Git 控制，需从回退后的源码重新构建并验收，或核对原交付清单后恢复与 `7ccfba3` 对应的仓库外 `backup/dist/`；本次备份的包对应 O1–O6 首次交付，不应与回退后的旧源码混用。旧 `rollback_changes.py` 的清单锁定提交前 HEAD/文件 SHA，提交后应拒绝恢复，不能绕过检查或改写历史清单。O1/O2 归档可按原相对目录单独恢复，遇到后来文件或符号链接冲突即停止。

## 本地残留清理（2026-10-02）

用户确认目标复核中剩余四项均可清理，仍由当前 Agent 执行。本批不改客户端或系统服务；Codex 和 Hermes 由用户重配，Pi 及其他 Agent 客户端停用。

- [x] 停用 Pi 的旧提示 1 个备份后移除，删除因此变空的 `.pi/`。
- [x] 源码字节码 39 个归档后移除，仅处理两处源码缓存，不清理现用 `.venv/`。
- [x] 首批恢复材料 52 个完整外置，保留原结构、内容及 metadata，并将两份文档的旧目录链接改为外部定位入口；删除因此变空的 `.superpowers/`。
- [x] 核对并移除项目内空 `.worktrees/`，保留外部画面检索 worktree。
- [x] 两组归档共 92 个文件在仓库外实际试恢复，SHA、mode 和 mtime_ns 匹配；移除阶段其余文件、现用环境和安装配置保持。

本批同步三份维护文档与 sdist，wheel 和版本保持；验收范围为既有文档测试、链接/锚点、包成员逐字节比对及受保护文件检查，不扩大完整功能回归。最终结果、包 SHA、备份和恢复条件见[本次执行记录](</Users/weisengao/Library/Application Support/douyin-wiki/integration-maintenance/2026-10-01-o1-o6-etwtkq2_/local-remnants-cleanup-20261002-p2el98s2/README.md>)；本批维护文档已随本次 `main` 收尾提交，未推送或发布。原历史清单不改写。

回退材料移除可使用本次归档的已验证恢复工具，默认只核对，`--apply` 才恢复清单文件且不覆盖后续不同内容。原首批脚本依赖旧目录和历史 HEAD，不改写或绕过旧保护。撤回本批文档及包同步时，核对前后 SHA 与后续修改后，仅恢复本次三个文档副本及相应旧包。空目录可重建；字节码通常由运行再生。客户端和服务的禁用字节码启动方式仍需实际验收，当前清理不代表缓存不会再次生成。

## 确认范围

用户已确认并完成首批 N1–N4 + O7 + O8 和 E1 通用自助接入文档框架，已提交为 `7ccfba3`；随后选择的全部 O1–O6 由当前 Agent 完成。真实客户端后续由调用 Agent 按文档在仓库外自行接入。前述实施阶段默认保留品牌资产、公开方法和 Pi/Obsidian 设置；后续本地残留清理已按用户确认备份并移除停用 Pi 的提示，Obsidian 设置及其他保留项保持。大规模重排源码目录、改前端框架、合并采集流水线、删除分支/worktree、重写历史均不在本次范围。

本次静态通过不替代后续功能验收；历史 667 项通过不作为新改动证据。每阶段只有文件范围、接口约束、验证与回退材料都满足才报告完成。
