# MCP 与 gateway 精简 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 按用户要求由当前 Agent 执行和审查，不派发子 Agent；每个大步骤结束后等待用户确认。

**Goal:** 将抖库业务入口统一为 MCP、作品校正与分析统一为 gateway，保留 Web 阅读与管理，以空库部署精简版，并归档同一固定提交的旧版源码与 wheel。

**Architecture:** 保留现有 Service、Worker、SQLite / Vault、共享采集和 Gateway 协议，通过修改既有流程完成精简。先统一有效选项，再停用生成与聊天、缩减新库结构，随后删除剩余对话模型执行和配置；调用方、测试夹具与对应删除在同一任务调整。专题笔记沿用公开返回格式和 Vault 路径，只保留来源及笔记需要的实现。

**Tech Stack:** Python `>=3.12,<3.14`、uv、Pydantic、SQLite、FastAPI / Jinja2、原生 JS / CSS、MCP STDIO、Playwright、Qwen3 ASR、RapidOCR / Vision、现有向量检索。

**Spec:** [已确认的精简方案](../../design/2026-10-10-mcp-gateway-simplification-plan.md)；[第 1 步复核记录](../reviews/2026-10-10-mcp-gateway-simplification-design-review.md)。用户已确认第 1 步及版本建议，不重做设计确认。

**Status:** 第 3、4 步已完成，任务 1、2 已通过验证；共享选项和批次预检已在隔离分支修改，等待用户确认第 5 步。任务 2 新增 35 个行为用例，140 项定向测试及 1450 项非 live 回归通过，Ruff 通过，52 个 MCP 工具名称和输入参数与旧基准一致。版本安排：精简版 `0.2.0`，旧版 `0.1.5`；Web 静态资源版本由 `0.2.47` 改为 `0.2.48`。

## Global Constraints

以下引文来自设计，适用于全部任务；其他开发约定以仓库及全局 `AGENTS.md` 为准。

- “保留单条作品、博主、收藏和合集四种；视频和图文均保留”。
- “除已决定停用的 `generate_topic_artifact` 外，保留已有 MCP 工具名称、参数和 Gateway 协议 1 / 2”。
- “它们不再触发授权提醒，也不参加共享兼容性比较”；有效 `CaptureOptions` 只包含 `retention`。
- “批量派发先检查全部候选，再发布子任务，不能在发现冲突前留下部分已派发的批次”。
- “新库中的 Gateway 任务仍保存下载、原稿、校正稿和分析检查点”；保留正常重试、重启恢复、租约与发布事务。
- “不实现旧 `provider` / `local` 配置和检查点的迁移”；停用模式及未知模式明确报错。
- “不新增 Agent 专题生成、成果提交或后台唤醒接口，也不将其列为下一期”。
- “新库不接收旧 Vault、SQLite 状态库、任务、专题、自动生成成果和聊天记录”；不导出再导入旧业务数据。
- “保留当前资料存储和索引的主要结构，不借本次精简重做整套存储”。
- “`httpx` 仍用于分享解析和媒体下载，保留该依赖”；不更换 Qwen3，不改变逐块转写和近期对齐修复。
- “`worker run --forever` 与 `maintenance run --apply`”及两个 console scripts 保持有效；隐藏 `gateway monitor-events` 保留。
- “删除项目内的模型调用代码和配置入口，不修改外部 Agent 的模型设置，不停用本机独立模型服务，也不清除已有 Keychain 密钥或下载的模型”。
- “Worker、Web、维护服务及 Codex MCP 统一改为使用该环境，不以可编辑安装直接引用源码”；目标为 `/Users/weisengao/Library/Application Support/douyin-wiki/runtime/.venv/`。
- 验证命令使用 `uv run --no-sync`；不得同步、删除或移动现用仓库 `.venv`。发行包验收使用独立环境；真实账号与网络业务验证单独选择。

## Review Focus

1. 同一批次前面的候选可以派发、后面的候选出现有效选项冲突时，整批不能新增子任务或改变已确认状态；任务 2 以真实派发接口验证。
2. Gateway 校正或分析提交后重启、租约失效或材料版本变化时，不得重复下载、跳过校验或由过期执行者发布；任务 4 保留并补充恢复与协议测试。
3. 已停用接口收到空请求、旧 payload 或不存在的旧 ID 时，应返回 `410` 且无任务 / 数据 / 模型副作用；任务 3 和 6 验证，不沿用旧业务参数的必填校验。
4. 没有 `[llm]`、密钥和对话模型服务时，空库、Web、Worker、诊断及安装包仍可运行；任务 4 和 9 验证，无额外模型下载或真实账号访问。
5. 专题笔记在来源更新、资料删除恢复和知识索引重建后仍可读取，当前 `artifacts` 返回字段不变，且不依赖聊天表或自动成果分支；任务 3 验证。

---

## 大步骤、任务与确认点

| 用户流程 | 本计划任务 | 完成后下一步 |
| --- | --- | --- |
| 1. 方案与现状复核 | 已完成，见复核记录 | 已获用户确认 |
| 2. 详细实施计划 | 已完成，已获用户确认 | 已进入步骤 3 |
| 3. 固定基准、准备环境 | 任务 1 已完成、已获确认 | 已进入步骤 4 |
| 4. 共享选项修复 | 任务 2 已完成 | 等待用户确认步骤 5 |
| 5. 执行与数据精简 | 任务 3、4，按顺序完成 | 两项完成后确认，进入步骤 6 |
| 6. 入口、界面与文档 | 任务 5、6、7，按顺序完成 | 三项完成后确认，进入步骤 7 |
| 7. 代码审查与修复 | 任务 8 | 确认后进入步骤 8 |
| 8. 发布前验收 | 任务 9 | 确认具体验收结果和部署清单后进入步骤 9 |
| 9. 合并与本机部署 | 任务 10 | 确认实际运行结果后进入步骤 10 |
| 10. 旧版归档与交付 | 任务 11 | 汇总交付物与验证记录 |

计划共 11 项执行任务，仍对应用户确认的 10 个大步骤。大步骤内部持续执行，不为每个小任务重复索要确认。每次报告必须包含改动、验证结果、实际完成范围和下一步；文件修改、包构建和生产部署分别记录。

## 文件职责与共用约定

| 文件 | 本次职责 |
| --- | --- |
| `models.py`、`batch_capture.py`、`capture_sharing.py`、`database.py`、`collection_store.py`、`collections.py`、`favorites_store.py`、`favorites.py` | 有效选项、预览与原子派发；保留共享关系及调用者信息 |
| `errors.py`、`models.py`、`database.py`、`service_entries.py`、`service_trash.py`、`service_maintenance.py`、`vault.py` | 停用错误、新库 schema、专题来源 / 笔记、删除恢复与重建 |
| `config.py`、`setup.py`、`analysis_rules.py`、`service.py`、`service_capture.py`、`service_analysis.py`、`web_operation.py` | gateway 执行、无对话模型配置与诊断、公共校验与发布元数据 |
| `cli.py`、`mcp_server.py`、`webapp/app.py`、`webapp/operation_api.py`、`webapp/rendering.py` | 保留接口与停用处理；调用方调整与对应实现删除一起提交 |
| `webapp/templates/app.html`、`webapp/static/*.js`、`webapp/static/app.css`、`webapp/version.py` | 删除新建 / 生成 / 聊天界面，保留任务页面、阅读及右栏；保持资源路径和必要加载顺序 |
| `tests/conftest.py`、`tests/support/gateway.py`、`tests/support/web.py`、混合功能测试及既有浏览器脚本 | 用 Gateway 提交驱动新任务，保留领域行为测试；停用成功路径改为无副作用测试 |
| `README.md`、`CHANGELOG.md`、`docs/` 现行契约、`pyproject.toml`、`uv.lock`、`__init__.py` | 当前能力、有意改变的行为、独立 wheel 安装和版本 |

源码基准为 `6c4fb7b918e5b37e0faefa5fc8cbefdde82783bb`。任务 1 从该提交准备工作区；本任务已确认的设计、复核和计划复制到工作区，主工作区既有内容及暂存状态保持原样。后续文件行号按实际工作区核对，不使用已经变化的行号执行补丁。

外部验证与部署材料目录定为 `/Users/weisengao/Documents/ChatGPT/maintenance/douyin-wiki/mcp-gateway-simplification-2026-10-10/`，下文记为 `TASK_OUT`；执行任务 1 时才创建。`TASK_WORKTREE` 必须是当前聊天复用或新建工作区工具实际返回的绝对路径；不设置 `HOME` 或 `CODEX_HOME`。

后续命令使用以下固定路径变量；这里只记录命令，不创建这些目录。`TASK_WORKTREE` 在工具返回后单独赋值，开发命令默认在该工作区执行，安装包检查默认在 `TASK_OUT/release-check/` 执行。

```sh
TASK_OUT='/Users/weisengao/Documents/ChatGPT/maintenance/douyin-wiki/mcp-gateway-simplification-2026-10-10'
TASK_RUNTIME='/Users/weisengao/Library/Application Support/douyin-wiki/runtime/.venv'
TASK_CONFIG='/Users/weisengao/Library/Application Support/douyin-wiki/config.toml'
TASK_VAULT='/Users/weisengao/Documents/Obsidian/抖库'
TASK_LEGACY='/Users/weisengao/Documents/ChatGPT/archives/douyin-wiki-legacy'
```

## Task 1：固定旧版提交并准备隔离环境（步骤 3）

**Files:** 只保存本任务文档、工作区来源和验证记录；不修改运行源码或生产安装。旧版 tag 为 `legacy/douyin-wiki-0.1.5-2026-10-10`。

**Interfaces:** Consumes：上述完整提交和已确认文档。Produces：`TASK_WORKTREE`、独立 `.venv`、`TASK_OUT/baseline.md`、`TASK_OUT/mcp-tools-legacy.json`、可验证的旧版 tag。

- [x] **Step 1：复核基准和待保留改动。** 运行 `git status --short`、`git diff --cached --stat`、`git rev-parse HEAD`；记录主工作区原有的 `docs/README.md` 修改及本任务未跟踪文档。核对 LaunchAgent / Codex 启动配置仍引用现用解释器，不写配置。设置上述固定变量，确认 `TASK_OUT` 不含其他任务材料后创建该目录。
- [x] **Step 2：固定版本标记。** 检查同名 tag；不存在时执行 `git tag -a legacy/douyin-wiki-0.1.5-2026-10-10 6c4fb7b918e5b37e0faefa5fc8cbefdde82783bb -m 'Legacy 0.1.5 before MCP gateway simplification'`。已存在则比较目标，不移动 tag。用 `git rev-parse 'legacy/douyin-wiki-0.1.5-2026-10-10^{commit}'` 验证完整提交一致。
- [x] **Step 3：准备工作区。** 使用 `using-git-worktrees`，先 `list_artifacts`，有适用工作区则复用；否则 `create_worktree`，`name="mcp-gateway-simplification"`、`ref` 为上述完整提交、`allowAsync=true`。在返回目录建立或复用 `codex/mcp-gateway-simplification`，复制三个已确认任务文档，不切换主工作区分支。
- [x] **Step 4：准备独立依赖。** 核对 `TASK_WORKTREE` 与原仓库不同；在该目录使用 `UV_PROJECT_ENVIRONMENT="$TASK_WORKTREE/.venv" uv sync --python 3.12 --frozen --extra dev --extra embeddings --extra asr --extra ocr`。检查 `sys.executable`、包 origin 与 `.dist-info` 均属于工作区，确保没有引用原仓库 editable 路径。
- [x] **Step 5：验证旧代码基准并保存 MCP 契约。** 在工作区执行 `uv run --no-sync ruff check src tests`、`uv run --no-sync pytest -q -m 'not live'`，输出保存到 `TASK_OUT` 并读取结果。以独立测试配置启动该工作区 STDIO MCP，仅执行 initialize / tools/list，保存每个实际工具的 name 和 inputSchema 至 `TASK_OUT/mcp-tools-legacy.json`，不调用业务工具。Expected：退出码 `0`、Ruff 无错误、非 live 全部通过；记录实际数量，不把历史 1415 或 52 当成固定断言。失败先查原因，不开始精简。
- [x] **Step 6：记录并提交文档。** 在工作区仅提交已确认的设计、复核和计划，记录起始提交、tag、环境和检查结果。Expected：运行文件与旧版基准没有差异，主工作区原改动及暂存保持原样；本步不构建旧版归档。

任务 1 验证记录：独立工作区为 `/Users/weisengao/.codex/worktrees/mcp-gateway-simplification/douyin-wiki`，分支 `codex/mcp-gateway-simplification`；Python `3.12.13`、包 `0.1.5`，旧版 tag 指向上述完整提交。Ruff 通过；本轮非 live 回归实际结果为 `1415 passed, 2 deselected in 260.98s`。实际 STDIO 清单为 52 项，已保存名称和 inputSchema；仅执行 initialize / tools/list，没有创建业务资料库。完整证据保存于 `TASK_OUT/baseline.md` 及同目录日志。188 个运行源码 / 测试 / 脚本 / 构建文件校验值与基准相同，原主工作区文档、暂存和运行配置校验通过。

## Task 2：统一有效选项和批次派发（步骤 4）

**Files:** Modify：`src/douyin_wiki/models.py`、`capture_sharing.py`、`database.py`、`collection_store.py`、`collections.py`、`favorites_store.py`、`favorites.py`、`localization.py`；Reuse：`batch_capture.py` 的完整活动任务映射，以及 `tests/test_creator.py`、`test_favorites.py` 中的既有 helper；Test：新建 `tests/test_capture_option_compatibility.py`，调整 `tests/test_capture_sharing.py`、`test_collection_flow.py` 中以旧授权字段制造冲突的断言。

**Interfaces:** Consumes：现有 `CaptureOptions`、`active_capture_jobs(conn)`、四种创建 / 确认接口。Produces：`CaptureOptions.processing_key(self) -> tuple[RetentionPolicy]`，仅含 `retention`；公开请求与任务返回保持原格式，不覆盖整个模型的相等运算。

- [x] **Step 1：写旧授权参数不阻止共享的行为测试。** 在新测试文件复用 `tests.test_capture_sharing.confirmed_collection` 和 `SHORT`，参数化两旧字段的四种组合。Worker 先处理执行者，后处理短链等待者；必须满足以下断言，且等待者自己的 Gateway 会话保留：

```python
assert follower.id == requested.id
assert follower.status == JobStatus.MONITORING
assert service.downloader.calls == 1
assert follower.result["shared_capture_job_id"] == owner_id
assert service.get_job(requested.id).request.gateway_context == caller_context
assert service.get_job(owner_id).status == JobStatus.AWAITING_AGENT_ANALYSIS
```

- [x] **Step 2：运行失败测试。** Run：`uv run --no-sync pytest -q tests/test_capture_option_compatibility.py -k legacy_authorization`。Expected：旧字段含 `True` 的用例因整对象比较不能进入共享状态；失败原因必须是行为断言，不是导入或夹具错误。
- [x] **Step 3：补全批次测试并验证失败。** 新建测试函数 `test_batch_reuses_capture_when_only_legacy_authorization_differs` 与 `test_later_retention_conflict_does_not_dispatch_partial_batch`，对 `creator / favorites / collection` 参数化。复用各文件已有清点 helper；活动执行者旧字段不同仍被复用，活动执行者 `retention=KEEP` 与默认 `TEMPORARY` 冲突。让后一个所选作品冲突，断言 `set(j.id for j in service.database.list_jobs())` 与确认前一致、父任务仍待选择、没有新 child 关联或 confirmed 标记。测试合集 / 收藏预览与确认的冲突提示一致。Run：`uv run --no-sync pytest -q tests/test_capture_option_compatibility.py`，确认旧字段共享或收藏有效选项冲突用例因当前行为失败；已有正确的原子性用例允许先通过。
- [x] **Step 4：实现统一比较与全部候选预检。** `processing_key()` 返回单元素 tuple；短链共享、博主派发、合集预览和确认、收藏预览和确认全部调用同一规则。收藏保留活动 `JobRecord` 而不是提前只保留 ID；博主与收藏先形成候选计划，再在原写事务内插入和关联。保留合集已有双循环、租约校验、共享重指、幂等确认和完整资料优先复用。
- [x] **Step 5：验证正反例与既有共享行为。** Run：`uv run --no-sync pytest -q tests/test_capture_option_compatibility.py tests/test_capture_sharing.py tests/test_collection_flow.py tests/test_collection_lifecycle.py tests/test_creator.py tests/test_favorites.py tests/test_capture_sharing_web.py`；再运行 Ruff。Expected：全部通过；不同 retention 明确冲突，取消保护、等待者重试、普通检查点和事件会话不变。实际验证加入收藏预览 / 接口与本地化测试，共 140 项通过；随后完整非 live 回归 1450 passed, 2 deselected，Ruff 通过，STDIO MCP 的 52 项输入契约与旧基准完全一致。
- [x] **Step 6：检查差异并提交。** 只提交此任务文件，消息 `fix: compare effective capture options across import paths`；记录测试结果。本大步骤结束，等待用户确认步骤 5。

## Task 3：停用生成和聊天，缩减新库及专题笔记（步骤 5 的第一项）

**Files:** Modify：`errors.py`、`models.py`、`database.py`、`favorites_store.py`、`service_entries.py`、`service_trash.py`、`service_maintenance.py`、`vault.py`、`mcp_server.py`、`webapp/app.py`、`webapp/operation_api.py`、`webapp/rendering.py`、`web_operation.py`；Delete：`adapters/chat.py`、`webapp/chat.py`。Test：`tests/test_topics.py`、`test_database.py`、`test_web.py`、`test_interfaces.py`、`test_web_operation.py`、`test_quality_baseline_fixes.py`、`test_review_web_fixes.py`、`test_pipeline_state.py`、`tests/support/web.py`；同步调整引用假聊天 provider 的现有浏览器脚本及 `tests/test_usability_browser.py`。

**Interfaces:** Consumes：任务 2 的请求模型与共享行为。Produces：`FeatureDisabledError(DouyinWikiError)`，`code="feature_disabled"`；新状态库 `STATE_SCHEMA_VERSION=16`；仅允许 `kind="note"` 的新库笔记，公开 `get_topic()` 的 `topic / artifacts` 和 `save_topic_note()` 返回字段保留；`render_topic_note(markdown: str) -> str` 提供安全 Markdown 展示。

- [x] **Step 1：写停用、笔记存续与新库测试。** `test_retired_model_features_have_no_side_effects` 参数化下表十个“模型功能”HTTP 方法 / 路径；使用 `tests.support.web.local_client`，请求前后比较 jobs、topic_artifacts 的 ID 集合，断言 `status_code == 410`、`"已停用" in response.json()["detail"]`，不先查 topic / session 是否存在。为 `test_retired_model_features_do_not_read_keys_or_construct_providers` 设置抛异常的 provider 构造与系统密钥访问 spy。新增异步 `test_retired_model_features_service_generation_is_disabled`，直接 await 原 Service 方法，传不存在的 topic、原生成 kind 和调用即报错的 provider，断言先得到 `FeatureDisabledError`，没有专题读取或写入。新增 `test_topic_note_roundtrip_and_rebuild_without_chat_tables`：复用 `test_topics._add_entry` 建来源，调用 `create_topic`、`save_topic_note(..., confirmed=True)`，断言 `kind == "note"`、正文逐字一致、`get_topic()["artifacts"]` 仍含笔记；经来源更新和知识重建后仍可读。新增 `test_empty_schema_has_no_chat_tables`、`test_legacy_state_is_rejected_without_migration`、`test_concurrent_fresh_initialization_keeps_schema_16`：分别检查 sqlite_master、旧样本建表 SQL / 数据未变、双进程初始化和新任务写入。保留已有资料删除 / 恢复、限定来源检索及并发笔记测试。
- [x] **Step 2：运行失败测试。** Run：`uv run --no-sync pytest -q tests/test_web.py tests/test_topics.py tests/test_database.py -k 'retired_model_features or note_roundtrip_and_rebuild_without_chat_tables or empty_schema_has_no_chat_tables or legacy_state_is_rejected_without_migration or concurrent_fresh_initialization_keeps_schema_16'`。Expected：当前入口不是 `410`、直接 Service 调用仍读取专题，或当前 schema 仍含聊天表 / 允许迁移；不使用真实模型请求。spy 必须在应用构造前安装，接口删除后改为监测系统访问与 HTTP 调用，不能继续导入已删除模块。
- [x] **Step 3：先替换调用方，再删除执行器。** `generate_topic_artifact(self, topic_id: str, kind: TopicArtifactKind, *, provider: Any | None = None) -> dict[str, Any]` 保留异步方法及参数，直接抛 `FeatureDisabledError("专题自动生成已停用")`，不读写专题、不调用 provider。删除 MCP 工具注册及 INSTRUCTIONS 中的生成调用要求；其他工具参数不动。`create_app()` 删除聊天初始化及相关注入参数，同步调整所有实际调用方；模型功能路由直接返回 `410`，删除请求专属类型和聊天持久化调用，再删除两聊天文件。本任务暂保留 provider 分析仍使用的 `LLMSettings`，其最后调用方由任务 4 一起删除。
- [x] **Step 4：缩减笔记与 schema。** `TopicArtifact` 保留名称、现有笔记公开字段和默认 `None` 的模型 / token 返回字段，但 `kind` 限为 note；不保留六类成果的运行模型。`topic_artifacts` 表仅保存笔记需要的字段，删除模型及 token 存储列，读取时以模型默认值维持返回格式。新 schema 不创建 `web_chat_sessions / web_chat_messages`；删除聊天关联恢复与旧 schema 迁移，包括收藏旧基线迁移。`Database.initialize()` 在执行建表前检查版本：空状态库可创建 16，已有 16 可重复打开；旧版本或版本 0 但已有业务表明确拒绝，不 DROP / 迁移旧库。保留新库初始化并发、事务、索引、外键和后续重建。
- [x] **Step 5：验证新库与公共笔记链路。** Run：`uv run --no-sync pytest -q tests/test_database.py tests/test_topics.py tests/test_web.py tests/test_web_operation.py tests/test_interfaces.py tests/test_publication_transactions.py tests/test_collection_vault.py`，再运行 Ruff。Expected：全部通过、MCP 清单只移除生成工具、笔记与删除恢复不依赖聊天表，旧样本没有被迁移或改变。
- [x] **Step 6：提交本任务。** 消息 `refactor: remove topic generation and web chat from fresh stores`。浏览器脚本的假聊天依赖在本任务解除，旧聊天成功路径替换为停用断言；完整界面入口精简和浏览器验收在任务 6 完成。继续任务 4。

## Task 4：作品只走 gateway，删除剩余模型配置与依赖（步骤 5 的第二项）

**Files:** Modify：`config.py`、`setup.py`、`models.py`、`analysis_rules.py`、`service.py`、`service_capture.py`、`service_analysis.py`、`web_operation.py`、`cli.py`、`webapp/app.py`；Delete：`adapters/llm.py`、仅供对话模型使用的 `secrets.py`。Test：`tests/conftest.py`、`tests/support/gateway.py`、`test_setup.py`、`test_interfaces.py`、`test_gateway_context.py`、`test_gateway_submission.py`、`test_capture_sharing.py`、`test_service.py`、`test_creator.py`、`test_image_note.py`、`test_pipeline_state.py`、`test_correction_recovery.py`、`test_analysis_checkpoints.py`、`test_auto_correction.py`、`test_content_tags.py`、`test_transcript_quality.py`、`test_publication_transactions.py`、`test_review_pipeline_fixes.py`、`test_review_web_fixes.py`；其他依赖即时完成分析的混合用例按下述审计调整，不整文件删除。

**Interfaces:** Consumes：任务 3 的无聊天 Web 初始化、schema 16、停用错误和笔记。Produces：`AppConfig` 默认且只允许 gateway，新生成 TOML 没有 `[llm]`；现有 Gateway 方法及协议不变；`analysis_rules.PROMPT_VERSION = "v2.4-content-tags"` 保留公开发布元数据；测试 helper `complete_gateway_job` 见下文。

- [x] **Step 1：写无模型运行与流程测试。** 新增 `test_default_config_contains_no_llm_settings`，断言 `"[llm]" not in render_default_config()`、`not hasattr(AppConfig(), "llm")`；`test_disabled_and_unknown_analysis_modes_are_rejected` 参数化 `provider / local / unknown`，均明确报只支持 gateway。用 spy 验证 `doctor`、Web 初始化、Worker 空队列不访问 Keychain 或对话模型。保留协议 1 / 2 roundtrip；视频校正后分析、图文直接分析，分别以 `phase`、`job_id`、完整正文和入库结果断言。新增 `test_fresh_gateway_accepts_existing_protocol_one_analysis_fields`，保留现行协议 1 输入字段转换，不把新提交兼容性误删为旧 Vault 迁移。
- [x] **Step 2：运行失败测试。** Run：`uv run --no-sync pytest -q tests/test_setup.py tests/test_gateway_submission.py -k 'no_llm or unknown_analysis_modes or existing_protocol_one'`。Expected：配置还含 LLM、旧模式被接受或旧诊断访问密钥；失败不连接真实接口。
- [x] **Step 3：先调整通用测试夹具。** `tests/conftest.py` 的 config 改为 gateway，移除 `FakeAnalysisProvider` 和模型配置；保留假解析、下载、媒体、ASR、OCR、提醒及离线向量。`tests/support/gateway.py` 增加 `async def complete_gateway_job(service: DouyinWikiService, job_id: str, *, analysis: dict[str, Any] | None = None, gateway_protocol: Literal[1, 2] = 1) -> JobRecord`：要求目标已等待 Agent，按当前 phase 对视频提交原文校正、对图文直接分析；协议 2 每阶段重新取 context_revision，再提交给定或合成分析。调用真实 Worker，按 get_job(job_id) 检查目标完成，不能假设 run_once 的首个返回就是目标；队列内其他任务正常处理至其等待 / 终态，不替它们提交分析。用有限步数保护测试，队列提前为空或目标异常则报告任务状态，最终只返回目标 JobRecord。合成分析从原稿 / OCR 构造，与现有 fake 的测试数据相同，不伪造完成状态，不调用模型；若暂换 MCP 的 `_SERVICE`，必须用 finally 恢复。`make_gateway_job`、`make_video_gateway_service`、`run_gateway_roundtrip` 签名保留并移除 fake provider 依赖。
- [x] **Step 4：精简运行实现及最后调用方。** `AppConfig` 校验只允许 gateway，保留旧 / 未知模式的明确错误；删除 `LLMSettings`、其默认配置、连接设置和专属密钥访问。`configure-model` 同任务改为无交互、无配置副作用的非零停用提示；`configure-analysis-mode` 仅写 gateway。Service、视频、图文和重新分析删除 provider/local 执行、分段汇总及标签归纳，保留同一 Gateway 等待 / 提交 / 发布与检查点校验。Service 构造参数 `analysis: Any | None = None` 继续接受以保留调用签名，但不选择或调用传入执行器，正式测试不再依赖它。先迁移 PROMPT_VERSION、调整 Web/CLI/诊断引用和测试，再删除 llm 与 secrets 文件；共有分析输入模型、证据校验、提示和错误类型只按真实用途处理。
- [x] **Step 5：验证恢复、材料与租约并调整领域测试。** 更新 `test_gateway_resume_after_restart_reuses_material_checkpoints`，校正前和分析提交后分别新建 Service，断言只下载一次、原稿 / 校正稿 / 分析材料保留且最终完成；保留等待者重试清旧关联、普通重试保留检查点、失效租约不可发布、过期 context_revision 被拒绝的现有测试。直接分析完成的资料 / 索引 / 收藏 / 合集 / 提醒测试改由 `complete_gateway_job` 继续，旧 provider 专属成功路径和旧业务库兼容用例删除，保留原行为中仍有效的输入、发布、引用和错误检查。除 Files 中的直接依赖外，逐项检查 `test_collection_vault.py`、`test_collection_lifecycle.py`、`test_collection_flow.py`、`test_collection_ui.py`、`test_collection_interfaces.py`、`test_favorites.py`、`test_favorites_flow.py`、`test_favorites_ux.py`、`test_favorites_review.py`、`test_analysis_v2.py`、`test_review_root_fixes.py`、`test_review_storage_fixes.py`、`test_recovery_and_navigation.py`、`test_usability_regressions.py`、`test_quality_baseline_fixes.py`；已走 Gateway 的用例保留，不能根据文件名或单个旧模式引用整文件删除。
- [x] **Step 6：运行该批次完整非 live 回归。** Run：`uv run --no-sync ruff check src tests`、`uv run --no-sync pytest -q -m 'not live'`；另用 `rg -n 'adapters\.(llm|chat)|webapp\.chat|LLMSettings|FakeAnalysisProvider|get_secret|store_secret' src tests scripts --glob '*.py'` 审计残留引用。Expected：全部通过，运行代码没有已删除模块引用，剩余文字只允许在明确停用 / 不存在断言中。修复真实失败，不通过恢复旧 provider 或聊天夹具使测试通过。
- [x] **Step 7：提交并报告步骤 5。** 消息 `refactor: make gateway the only work analysis runtime`，记录已删除执行路径和新任务恢复验证。等待用户确认步骤 6。

实际验证：完整非 live 回归 `1376 passed, 2 deselected in 193.11s`；恢复与领域定向检查 `154 passed in 18.10s`；Ruff 通过。完整回归后只调整 `tests/test_web.py` 的停用检查，移除已删除模型对象的替身，直接拦截系统 Keychain 与模型 HTTP，受影响 Web 文件重新验证 `59 passed in 3.14s`；运行代码未再修改。实际 MCP 保留 51 项，名称与 inputSchema 等于旧版快照删除 `generate_topic_artifact` 后的结果。

恢复验证发现重新下载后的图文在等待 Agent 后被误判为已经处理，且重新下载期间删除的目标可能重新出现。新增 `reacquire_entry_id` 材料检查点，恢复时继续同一任务，并再次检查原资料是否存在；协议 1 / 2、Service 重启及删除目标检查均通过。

## Task 5：CLI 业务命令改为统一停用提示（步骤 6 的第一项）

**Files:** Modify：`src/douyin_wiki/cli.py`；Test：`tests/test_interfaces.py`、`test_setup.py`、`test_favorites_interfaces.py`、`test_collection_interfaces.py`；保留其中 MCP 与运行命令测试。

**Interfaces:** Consumes：任务 4 的 gateway 配置与任务 3 的停用语义。Produces：既有业务命令名与别名，退出码 `2`； `_disabled_command(feature: str, mcp_tool: str | None = None) -> NoReturn`，只输出提示后退出，不初始化 Service。

- [ ] **Step 1：写命令无副作用测试并验证失败。** `test_disabled_business_commands_do_not_initialize_service` 参数化下表命令，使用合法旧参数和不存在的 config 路径，令 `_service`、`load_config`、`write_config`、交互 prompt、安装器操作抛异常；断言退出码 `2`、提示含“已停用”及对应 MCP 名称、没有生成配置 / Vault。`test_configure_model_is_disabled_without_prompt` 同时覆盖无参数和带旧 `--model / --base-url / --config-path`。Run：`uv run --no-sync pytest -q tests/test_interfaces.py -k 'disabled_business_commands or configure_model_is_disabled'`。Expected：当前业务命令执行或请求参数而失败；任务 4 已改好的 configure-model 允许先通过。
- [ ] **Step 2：实现提示与保留命令注册。** 旧命令保持名称、别名和合法旧参数接收，去掉 prompt 和必需的交互步骤；没有参数时也直接输出停用信息。可选旧位置参数只为接收旧调用，不验证业务内容。采用 `该命令已停用，请通过 Agent 使用 MCP 工具 {tool}。`；无替代的功能说明已停用。保留运行命令原实现及 monitor-events 所依赖的事件 helper。
- [ ] **Step 3：验证运行入口。** Run：`uv run --no-sync pytest -q tests/test_interfaces.py tests/test_setup.py tests/test_favorites_interfaces.py tests/test_collection_interfaces.py`，再运行 Ruff。Expected：业务调用无副作用；`--help`、auth、doctor、Worker、Web、服务安装器、maintenance 和 database rebuild 的原调用方式有效。
- [ ] **Step 4：提交。** 消息 `refactor: disable duplicate business CLI commands`；继续任务 6。

| CLI 命令 / 分组内命令 | 提示中的 MCP 对应工具 |
| --- | --- |
| `capture`、`search` | `capture_douyin`、`search_knowledge` |
| `creator add / inventory / select / confirm / import / show / list / works / sync` | 依次为 `capture_douyin_creator / get_creator_inventory / set_creator_work_selection / confirm_creator_import / import_creator_works / get_creator / list_creators / list_creator_works / sync_creator` |
| `favorites scan / list / show / select / confirm / retry` | `scan_favorites / list_favorites_imports / get_favorites_import / set_favorites_selection / confirm_favorites_import / retry_favorites_import` |
| `collection add / inventory / select / confirm / retry / list / show / history` | `capture_douyin_collection / get_collection_inventory / set_collection_work_selection / confirm_collection_import / retry_collection_import / list_collections / get_collection / list_collection_imports` |
| `jobs get / list / events / ack-event / approve / retry` | `get_job / list_jobs / list_job_events / acknowledge_job_event / approve_job / retry_job` |
| `review resolve`、`gateway context / submit-correction / submit-analysis` | `resolve_review`、`get_analysis_context / submit_transcript_correction / submit_gateway_analysis` |
| `entry show / submit-analysis / reanalyze / reanalyze-all` | `get_entry / submit_analysis / reanalyze_entry / reanalyze_all` |
| `topic create / list / show / sources / search` | `create_topic / list_topics / get_topic / set_topic_sources / search_topic` |
| `inspiration add`、隐藏 `purpose add`、`reminder confirm` | `add_inspiration`、`add_purpose`、`confirm_reminder` |
| `topic generate`、`configure-model`、`migrate-vocabulary / migrate-remove-validation / migrate-covers` | 无替代；明确说明功能已停用 |

## Task 6：Web 停止新请求，保留任务管理与右栏（步骤 6 的第二项）

**Files:** Modify：`webapp/app.py`、`webapp/operation_api.py`、`webapp/templates/app.html`、`webapp/static/app.js`、`shared.js`、`imports-single.js`、`imports-creators.js`、`imports-favorites.js`、`imports-collections.js`、`analysis-settings.js`、`model-settings.js`、`system-settings.js`、`app.css`；Test：`tests/test_web.py`、`test_web_shell.py`、`test_web_navigation.py`、`test_web_operation.py`、`test_capture_sharing_web.py`、`test_collection_interfaces.py`、`test_collection_ui.py`、`test_collection_flow.py`、`test_favorites_flow.py`、`test_favorites_ux_browser.py`、`test_dialogs_browser.py`、`test_usability_browser.py` 及相关现行浏览器脚本。

**Interfaces:** Consumes：任务 3 的停用路由、任务 4 的 gateway 运行与任务 2 的共享状态。Produces：六个新建业务 HTTP 入口 `410`；新库批次 GET / 选择 / 确认 / 取消 / 重试仍用原接口和 Gateway 信息；旧页面路径仍可访问说明或有效任务。

- [ ] **Step 1：写新建入口无副作用测试。** 新增 `test_web_new_capture_endpoints_are_gone_without_creating_jobs`，对下表六个“新请求”入口分别发送合法旧 payload、`{}` 和空 body，保留有效 Origin；断言 `410`、可理解的 Agent 提示、jobs ID 集合不变、下载次数不变。调用不存在的 creator ID 也先返回停用说明，不访问实体。`/api/imports/classify` 不创建任务，保留既有接口，不因 POST 或路径名字而停用。
- [ ] **Step 2：运行失败测试并准备保留场景。** Run：`uv run --no-sync pytest -q tests/test_web_operation.py -k new_capture_endpoints`。Expected：当前入口会创建任务或先报参数错误。为混合 Web 测试先通过 MCP / Service 准备新批次，再调用原 Web 选择、确认和重试，仍断言原状态码、payload、子任务数量与共享事件。在既有浏览器测试补充 `test_existing_mcp_batch_remains_manageable_after_web_creation_is_removed`、`test_retired_links_and_preserved_right_panel`，先验证旧页面仍有新建 / 模型控件使相应断言失败，再修改界面。
- [ ] **Step 3：实现停用处理与界面删除。** 六新建路由不接收必需业务请求模型、不调用 core 或 resolver，直接返回 `410`。删除新导入按钮、命令面板条目、Cmd+I、分享文本粘贴导航、空库引导、六类生成按钮、聊天 Tab / 消息操作和对话快捷键、模型设置及旧模式选择。保留“导入内容”记录、任务中心、重新分析、信息 / 灵感 / 来源面板；不因 `chat-toggle` 名称删除右栏控制。
- [ ] **Step 4：整理旧页面与静态模块。** `/imports/single` 显示停用说明；`/imports/creators`、`/imports/favorites`、`/imports/collections` 没有新建表单，但历史清单和 `?job=<新库任务ID>` 继续工作；不存在的任务按原不存在处理。`/settings/model` 显示停用说明，`/settings/analysis` 只展示 gateway 状态。保留原 JS 资源 URL；只剩停用页职责的文件保留小模块，不保留提交 / 模型连接实现。任务脚本删除表单绑定并保留历史 / 选择 / 确认 / 重试 / 取消与轮询，不用空 DOM 或假表单维持旧代码。
- [ ] **Step 5：验证 HTTP 与真实浏览器。** Run：`uv run --no-sync pytest -q tests/test_web.py tests/test_web_shell.py tests/test_web_navigation.py tests/test_web_operation.py tests/test_capture_sharing_web.py tests/test_collection_interfaces.py tests/test_collection_ui.py tests/test_favorites_flow.py tests/test_favorites_ux_browser.py tests/test_dialogs_browser.py tests/test_usability_browser.py`。浏览器场景覆盖 1440px / 390px、亮暗主题、命令面板、键盘 / 粘贴、共享执行者跳转、等待 Agent 的 job_id、笔记显示和网络控制台无 JS 异常。Expected：全部通过，既有 MCP 新批次的 Web 选择 / 确认 / 重试保持可用。
- [ ] **Step 6：提交。** 消息 `refactor: remove web creation and model UI while keeping task management`；继续任务 7。

### 停用 HTTP 入口清单

| 类别 | 方法与路径 | 返回与约束 |
| --- | --- | --- |
| 新请求 | `POST /api/captures` | `410`，不创建采集 |
| 新请求 | `POST /api/creators/inventory` | `410`，不创建博主清点 |
| 新请求 | `POST /api/favorites/imports` | `410`；同路径 GET 保留 |
| 新请求 | `POST /api/collections/inventory` | `410`，不创建合集清点 |
| 新请求 | `POST /api/creators/{creator_id}/sync` | `410`，不创建同步 |
| 新请求 | `POST /api/creators/{creator_id}/import-skipped` | `410`，不派发跳过作品 |
| 模型功能 | `POST /api/topics/{topic_id}/artifacts` | `410`；topics 创建、sources 更新和 notes 保存保留 |
| 模型功能 | `GET /api/chat/sessions`、`POST /api/chat/sessions` | `410`，不读写会话 |
| 模型功能 | `GET /api/chat/sessions/{session_id}`、`DELETE /api/chat/sessions/{session_id}` | `410`，不查找或删除记录 |
| 模型功能 | `POST /api/chat/sessions/{session_id}/messages` | `410`，无流式执行、无消息写入 |
| 模型功能 | `GET /api/settings/model`、`POST /api/settings/model` | `410`，不访问或保存密钥 |
| 模型功能 | `POST /api/settings/model/test`、`GET /api/system/model-health` | `410`，不连接模型 |

六类新请求及十个模型功能的方法 / 路径组合分别由任务 6 和任务 3 覆盖。正常 Origin / 安全检查继续使用原规则，不将已有安全校验错误改成业务停用响应。

## Task 7：同步现行契约及版本（步骤 6 的第三项）

**Files:** Modify：`README.md`、`CHANGELOG.md`、`docs/README.md`、`docs/architecture.md`、`docs/gateway-agents.md`、`docs/service-collaboration-api.md`、`docs/web-ui-design-system.md`、`docs/favorites-import.md`、`docs/collections-import.md`、`docs/agents/README.md`、`docs/agents/self-setup.md`、`docs/agents/integration-contract.md`、`src/douyin_wiki/mcp_server.py` 的说明、`vault.py` 的生成规则、`pyproject.toml`、`uv.lock`、`src/douyin_wiki/__init__.py`、`webapp/version.py`；Test：调整 `tests/test_documentation.py` 中受影响的现行约定，复用现有版本测试。

**Interfaces:** Consumes：任务 2～6 的实际接口。Produces：包 `0.2.0`、Web `0.2.48`，反映当前能力的使用契约；不改写历史验证数量或截图证据。

- [ ] **Step 1：更新当前文档。** 明确日常业务只通过 MCP、job_id 是中间回执、Agent 持续执行、协议 2 优先而 1 保留、共享等待者按执行者取材料、Web 不能唤醒结束的对话。列出全部有意停用行为及客户端重连要求，专题研究 / 问答使用保留工具，保存用 `save_topic_note`。删除模型配置、Web 新建和自动成果的现行操作指引，保留 auth、运行命令、monitor-events 兼容说明和新库重建。部署说明改为独立 runtime wheel，不新增 Hermes 配置或额外心跳要求。
- [ ] **Step 2：更新版本与锁文件。** 将 pyproject 与 `__version__` 同时改为 `0.2.0`、WEB_VERSION 改为 `0.2.48`。工作区内执行 `uv lock`，核对锁文件仅有根包版本及已确认依赖声明的变化；不借此升级系统或其他依赖。在工作区独立 `.venv` 中同步已锁依赖以刷新包 metadata，原仓库 `.venv` 不动。
- [ ] **Step 3：检查实际说明与版本。** Run：`uv run --no-sync pytest -q tests/test_documentation.py tests/test_interfaces.py::test_package_version_matches_metadata`、`uv run --no-sync ruff check src tests`、`git diff --check`。审计 `generate_topic_artifact / configure-model / provider / local / chat` 的现行文档引用，保留明确停用、旧版说明和历史记录，删除误导性现行指令。Expected：检查通过、版本一致、所有链接可读。
- [ ] **Step 4：提交并报告步骤 6。** 消息 `docs: describe gateway-only 0.2.0 interfaces and runtime installation`。确认文档更新没有丢失主工作区原索引中的已确认内容。等待用户确认步骤 7。

## Task 8：全分支审查与必要修复（步骤 7）

**Files:** 新增 `docs/superpowers/reviews/2026-10-10-mcp-gateway-simplification-code-review.md`；必要修复只修改审查实际涉及的文件及行为测试。

**Interfaces:** Consumes：设计、本计划、任务记录、相对旧版基准的完整 diff。Produces：需求覆盖和发现清单、重要问题的修复证据、可进入发行验收的提交。

- [ ] **Step 1：当前 Agent 进行单独一轮审查。** 使用 `requesting-code-review` 的检查要求，但按用户规则自行审查，不创建审查 Agent。逐项查看 Review Focus，核对所有公开方法、MCP schemas、CLI 别名、停用路由和静态资源；特别检查新 schema 删除聊天关联后恢复、笔记和重建，以及依赖删除后源码 / 脚本 import。明确记录这是作者自查，没有独立审查者。
- [ ] **Step 2：修复重要行为问题。** 仅对真实发现的严重 / 重要问题写能重现的行为测试，确认失败后修改实现并验证通过。低影响文案等沿用测试例外；未处理的小问题列明，不顺延用户范围。若无发现，不写额外测试或改动。
- [ ] **Step 3：核对修复与覆盖。** 有运行修复时执行相关测试和完整非 live 回归；没有新代码变化则复用对应提交的有效测试证据，不无理由重复全套。记录重要问题已处理、判断依据、剩余项目和提交范围。
- [ ] **Step 4：提交审查记录及实际修复。** Expected：没有未解决的严重 / 重要问题，范围与设计一致。报告后等待用户确认步骤 8。

## Task 9：正式回归、浏览器与独立发行包验收（步骤 8）

**Files:** 构建到 `TASK_OUT/dist/`；创建 `TASK_OUT/release-check/`、导出的锁定依赖清单和 `TASK_OUT/release-check.py`；新增 `docs/superpowers/reviews/2026-10-10-mcp-gateway-simplification-acceptance.md`。本步不写生产 `dist/`、Vault 或运行配置。

**Interfaces:** Consumes：审查完成的 `0.2.0` 提交。Produces：wheel、sdist、哈希、离线验收记录及步骤 9 可检查的部署清单。

- [ ] **Step 1：完成正式检查。** 在工作区运行 `uv run --no-sync ruff check src tests` 和 `uv run --no-sync pytest -q -m 'not live'`。若任务 8 刚对同一源码和测试树跑完同一命令，核对内容未变后复用该记录；其他情况执行本轮检查。Expected：退出码 `0`，记录实际通过 / deselected 数量及对应提交。
- [ ] **Step 2：运行现行浏览器入口。** 按 `docs/web-ui-design-system.md` 的当前清单，依次运行 `uv run --no-sync python scripts/verify_readwise_foundation.py`、`uv run --no-sync python scripts/verify_readwise_shell.py`、`uv run --no-sync python scripts/verify_readwise_library.py`、`uv run --no-sync python scripts/verify_readwise_reader.py`、`uv run --no-sync python scripts/verify_readwise_chat.py`、`uv run --no-sync python scripts/verify_readwise_operations.py`、`uv run --no-sync python scripts/verify_readwise_cleanup.py`、`uv run --no-sync python scripts/verify_readwise_tags.py`，以及共享 / 合集 / 收藏的已调整浏览器场景。先将脚本截图输出定位至本任务外部目录；任务 3 / 6 已把聊天和新建成功路径改为停用检查，文件名不是保留旧功能的理由。已有无法重现的截图和目录不删除。Expected：桌面 / 窄屏、命令面板、旧链接、新批次操作、共享跳转和保留右栏通过，无外部网络、JS 异常或服务错误。
- [ ] **Step 3：构建并检查包成员。** Run：`uv build --out-dir "$TASK_OUT/dist"`。Expected：`douyin_wiki-0.2.0-py3-none-any.whl` 与 `douyin_wiki-0.2.0.tar.gz` 生成；检查模板、全部保留 JS / CSS / 图标 / 字体及许可、`resources/vision_ocr.swift`、两个 console scripts 和 Python 版本要求；无本机配置、Vault、状态库、Cookie、模型密钥或缓存。
- [ ] **Step 4：安装到独立检查环境。** 在工作区导出 `uv export --frozen --no-emit-project --extra dev --extra embeddings --extra asr --extra ocr --format requirements-txt --output-file "$TASK_OUT/requirements-check.txt"`；用 `uv venv --python 3.12 "$TASK_OUT/release-check/.venv"`、`uv pip sync --python "$TASK_OUT/release-check/.venv/bin/python" --require-hashes "$TASK_OUT/requirements-check.txt"` 安装锁定依赖，再 `uv pip install --python "$TASK_OUT/release-check/.venv/bin/python" --no-deps "$TASK_OUT/dist/douyin_wiki-0.2.0-py3-none-any.whl"`。Expected：包 origin 为该环境 site-packages，没有 editable / 原源码路径。
- [ ] **Step 5：从安装包做空库检查。** 外部 release-check 脚本建立独立 TOML 与空 Vault，断言没有 llm 设置和聊天表；验证 `douyin-wiki --help`、新配置 doctor、Web、Worker 空队列、实际 STDIO initialize / tools/list。将工具 name 和 inputSchema 按 name 比较任务 1 的实际快照，只允许移除 generate_topic_artifact，不将数量相等当作兼容性证据。先从外部目录运行 `"$TASK_OUT/release-check/.venv/bin/python" -B "$TASK_OUT/release-check.py"`；脚本断言包 origin 与 metadata 均位于该环境的 site-packages，且没有 editable 指向。在同一外部目录执行 `PYTHONPATH="$TASK_WORKTREE" "$TASK_OUT/release-check/.venv/bin/python" -B -m pytest -q -m 'not live' "$TASK_WORKTREE/tests/test_gateway_context.py" "$TASK_WORKTREE/tests/test_gateway_submission.py" "$TASK_WORKTREE/tests/test_image_note.py" "$TASK_WORKTREE/tests/test_topics.py" "$TASK_WORKTREE/tests/test_capture_sharing.py" "$TASK_WORKTREE/tests/test_publication_transactions.py" "$TASK_WORKTREE/tests/test_collection_vault.py"`，验证协议 1 / 2 roundtrip、图文、笔记、删除恢复、索引和共享；只将工作区根加入 PYTHONPATH 以读取 tests，不加入 src。阻断对话模型 / Keychain 和真实抖音网络，ASR / OCR / 向量配置保留；空库配置本身不能凭借省略保留配置项跳过启动检查。
- [ ] **Step 6：记录并准备部署材料。** 在工作区运行 `uv export --frozen --no-dev --no-emit-project --extra embeddings --extra asr --extra ocr --format requirements-txt --output-file "$TASK_OUT/requirements-runtime.txt"`，生成不含 dev 的锁定 runtime 依赖清单；记录包和提交对应关系、SHA256、模型功能没有调用、保留接口及资源检查结果。部署清单包括现有 Vault 管理目录清除、新配置、四个入口路径切换和重连验证，明确本步未部署。提交验收记录，等待用户确认步骤 9。

## Task 10：本地主线集成、空库与运行环境切换（步骤 9）

**Files:** 已验证 wheel / sdist 按需写入仓库 `dist/`；运行环境 `/Users/weisengao/Library/Application Support/douyin-wiki/runtime/.venv/`；新版 `config.toml`；三个 `com.local.douyin-wiki.*.plist`；Codex `config.toml` 中仅 `mcp_servers.douyin-wiki` 的启动入口；部署记录 `TASK_OUT/deployment.md` 和正式验收文档追加结果。

**Interfaces:** Consumes：任务 9 已验证的包和用户本步确认。Produces：本地主线精简版本、新空库、统一的非 editable 运行安装、实际 Web / Worker / MCP 验证。原仓库 `.venv` 继续保留。

- [ ] **Step 1：复核当前状态并集成本地主线。** 使用 `finishing-a-development-branch`，沿用已确认的本地 `main` 集成方式，不推送。检查 main 进展及主工作区改动；将本任务文档的原文件、二进制 diff、暂存补丁和路径清单保存到 `TASK_OUT/integration/`，不包含业务库。对阻止合并的本任务文档采用只限定这些路径的临时保存，合并后逐项恢复 / 合入已确认内容并核对原暂存状态；其他任务内容不纳入提交、不批量 stash / reset。若存在实际无法判断的用户冲突，先保留现场并说明。合并后只在源码 / 测试内容改变时补跑对应检查；记录主线提交和已验证包来源。
- [ ] **Step 2：准备独立 runtime。** 目标目前不存在，执行前重新核对；执行 `uv venv --python 3.12 "$TASK_RUNTIME"`、`uv pip sync --python "$TASK_RUNTIME/bin/python" --require-hashes "$TASK_OUT/requirements-runtime.txt"`、`uv pip install --python "$TASK_RUNTIME/bin/python" --no-deps "$TASK_OUT/dist/douyin_wiki-0.2.0-py3-none-any.whl"`。不从源码 editable 安装、不访问或同步旧 `.venv`。检查 package version `0.2.0`、origin / metadata / console scripts 为目标环境；现用四个入口此时仍不切换。
- [ ] **Step 3：核对并停止旧业务入口。** 再读取三份 LaunchAgent、Codex 配置和 Vault 顶层路径；保存安装配置备份，不读取或备份密钥。停止旧 Web、Worker 与维护调度，关闭指向旧库的 MCP 会话，确认旧解释器没有这些活动进程或库访问，再处理业务目录。独立模型服务保持当前状态。
- [ ] **Step 4：在现有 Vault 路径初始化空库。** 根目录为 `TASK_VAULT`。根据 2026-10-10 只读清单，清除其 `raw/`、`wiki/`、`creators/`、`collections/`、`topics/`、`.douyin-wiki/`、保存旧业务历史的 `.git/`，以及将重新生成的 `AGENTS.md`、`index.md`、`log.md`；不导出旧数据。`.obsidian/`、`.gitignore` 和 `.DS_Store` 保留，浏览器 Profile 与模型缓存位于此路径之外。操作前重新核对目录真实路径、符号链接和管理归属；新出现的来源不明路径不纳入清除。使用保留字段构造新版 AppConfig，并用 `render_default_config` 写入 `TASK_CONFIG`，仅显式保留实际仍有效的 Vault、Web、识别、检索和运行设置，不复制 `[llm]` 或旧模式。Run：`"$TASK_RUNTIME/bin/python" -B -m douyin_wiki init --vault "$TASK_VAULT" --config-path "$TASK_CONFIG" --overwrite-config`，创建 schema 16 和新 Vault Git。
- [ ] **Step 5：切换四个入口。** Run：`"$TASK_RUNTIME/bin/python" -B -m douyin_wiki service install --config-path "$TASK_CONFIG"`，重新生成 Worker / 维护 plist；`"$TASK_RUNTIME/bin/python" -B -m douyin_wiki web install --config-path "$TASK_CONFIG"`，重新生成 Web plist。更新 Codex 指定 MCP 的 command 为新 `.venv/bin/python`，保留 `-B -m douyin_wiki.mcp_server` 及当前有效配置 / Profile 环境值，不修改其他 MCP 或 Hermes 文件。Codex 重新连接后读取实际 tools/list。
- [ ] **Step 6：验证实际运行。** `launchctl` 和 HTTP 确认 Web `0.2.48`、Worker 活跃，维护登记且使用新解释器；新 MCP 的包路径和清单与安装包一致，生成工具缺席，按当前集合预期 51 项但不固定白名单。验证六类停用入口 `410` 且空库没有多出的业务任务，查询 / 页面正常。Run：`"$TASK_RUNTIME/bin/python" -B -m douyin_wiki doctor --config-path "$TASK_CONFIG"`，验证保留运行检查，无对话模型要求。识别 / 检索模型准备状态可检查，真实账号采集需另行显式选择。若当前聊天仍缓存旧 MCP 清单，明确记录待重连，不能用其他新会话的结果声称它已刷新。
- [ ] **Step 7：记录部署和发行文件。** 核对配置及包哈希后，需替换仓库 `dist/` 同名文件时先备份并检查原包成员，再放入已验证产物；不清理来源不明的 dist 文件。追加正式验收的安装路径、配置、主线提交、运行探针和实际 MCP 清单证据；仍保留旧仓库环境。提交本任务部署文档，报告后等待用户确认步骤 10。

## Task 11：旧版源码与发行包归档、最终交付（步骤 10）

**Files:** 创建 `/Users/weisengao/Documents/ChatGPT/archives/douyin-wiki-legacy/source.zip`、`douyin_wiki-0.1.5-py3-none-any.whl`、`README.md`、`SHA256SUMS`；独立旧版构建 / 安装检查目录位于 `TASK_OUT/legacy-build/` 与 `TASK_OUT/legacy-check/`；正式验收文档追加归档结果。

**Interfaces:** Consumes：任务 1 的完整旧版提交与 tag、已完成的精简版部署。Produces：同一旧提交的源码、wheel、恢复安装说明和校验值；旧版不启动常驻服务、不作为本机运行版本。

- [ ] **Step 1：核对归档来源。** 确认 tag 仍指向 `6c4fb7b918e5b37e0faefa5fc8cbefdde82783bb`，读取该提交 pyproject / lock 中包版本为 `0.1.5`；记录完整提交和实际归档日期。核对归档目标未被其他任务占用后创建 `TASK_LEGACY`。
- [ ] **Step 2：生成源码快照。** Run：`git archive --format=zip --output="$TASK_LEGACY/source.zip" 6c4fb7b918e5b37e0faefa5fc8cbefdde82783bb`。Expected：成员对应该提交的受 Git 管理文件，包括 src、tests、docs、scripts、pyproject、uv.lock 和许可证；不包含工作区改动、Vault、状态库、Cookie、模型密钥、Profile、模型下载或本机 `.venv`。
- [ ] **Step 3：从该源码独立构建旧 wheel。** 在 `TASK_OUT/legacy-build/` 解压 source.zip，独立构建环境执行 `uv build --python 3.12 --wheel --out-dir "$TASK_LEGACY"`，不复用精简后的源码或现用 `.venv`。Expected：生成 `0.1.5` wheel；ZIP 内容与版本元数据、入口、字体 / 静态资源 / Swift 文件完整。
- [ ] **Step 4：核对源码与旧包。** 将源码 ZIP 文件名 / 内容逐项与 `git ls-tree`、`git show` 比较；在独立旧版检查环境安装同提交锁定依赖和 wheel，验证 metadata、`--help` 和仅 tools/list 的 MCP 注册，名称和参数与任务 1 的快照一致，不执行业务工具或模型连接。旧版应含生成工具；精简版运行配置不受影响。
- [ ] **Step 5：写恢复说明与校验值。** README 记录完整提交、tag、版本、归档日期、Python 与系统依赖、从源码 / wheel 重新安装方式，并说明不含旧业务数据及凭据。计算 source.zip、wheel、README 的 SHA256，写入 SHA256SUMS 并重新检查全部文件，校验文件不对自身取值。
- [ ] **Step 6：交付和清理。** 汇总精简版本的代码、包、运行路径、验收记录与旧版归档；列明实际判断和未解决的小问题。只清理本任务确认不再需要的临时文件，保留交付物与必要证据。创建的 managed worktree 不再需要时用 `archive_worktree`，不删除旧仓库 `.venv` 或其他工作区，不新增旧版 Codex 项目。

## 计划自查与范围映射

| 设计内容 | 对应任务 |
| --- | --- |
| §1 / §2 四种导入、共享、保留底层能力 | 任务 2、4、6、9 |
| §3 Web 新请求停用、已有任务、右栏和旧链接 | 任务 3、6、9 |
| §4.1 gateway、重新分析、无旧模式迁移、新任务恢复 | 任务 4，schema / 新库边界见任务 3、10 |
| §4.2 有效选项、预检、共享调用者、取消和重试 | 任务 2、4 |
| §4.3 MCP 参数、协议、持续执行与客户端边界 | 任务 3、4、7、9、10 |
| §5 / §5.1 CLI、生成、聊天、配置及专属依赖删除 | 任务 3、4、5、6、7 |
| §5.2 空库、笔记及知识重建、删除恢复、有意改变的行为 | 任务 3、6、7、10 |
| §6 / §6.1 基准、正式验证、运行环境及旧版归档 | 任务 1、8、9、10、11 |
| §7 保留 Qwen3、对齐修复、无长视频授权与其他范围边界 | Global Constraints、任务 2、4、7、9 |

五项 Review Focus 都有明确所属任务和行为检查；新方法及常量的名称由本计划定义，调用方使用同一接口。文档和版本改动复用现有检查，不为可逆小改动新建测试。实施时记录真实失败和必要计划修正，不扩大为其他功能、旧业务数据恢复或独立客户端改造。

第 2 步自查：已核对 11 项任务及顺序、五项 Review Focus、全部停用路由、现有文件路径、文档链接、方案引文和命令参数；已补充新行为先验证失败的顺序，以及模型定义删除时同步修改调用方的依赖。当前只新增本计划，源码、运行配置、业务数据、Git 提交和现用环境均未改变。这里只确认计划的完整性，运行测试与部署结果由各执行任务记录。
