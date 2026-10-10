# 抖音合集导入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking. 用户已明确授权按设计开发，执行方式沿用当前 Agent；不委派子 Agent。

**Goal:** 将用户提供的抖音合集清点为可靠的作品清单，再经明确选择将每个作品送入现有采集流程，保存可恢复的合集关联。

**Architecture:** 原生网页 GET 请求负责平台校验，Python 从原始响应解析稳定 ID 与分页。独立 `CollectionService` 和四张表协调批次，子任务继续使用 capture；合集 Vault 索引只引用唯一视频资料。共享任务的关联与取消保护覆盖收藏、博主和合集。

**Tech Stack:** Python 3.12、Pydantic、SQLite、Playwright、FastAPI、Typer、STDIO MCP、原生 JavaScript。

**Spec:** [抖音合集导入开发方案](../specs/2026-10-10-douyin-collection-import-design.md)。用户于 2026-10-10 回复“sp 可以，按这个开发吧”，确认设计并授权执行；本计划保留该授权，不重复设置执行确认。

## Global Constraints

- 保持 Python `>=3.12,<3.14` 与现有 `playwright>=1.54,<2`；首期不引入第三方下载框架或新的常驻服务。
- 保持 console scripts、旧 CLI 别名、MCP 名称和参数、Service 公开方法、Web 路径、现有负载字段与状态码兼容；只增加新入口及可选字段。
- 当前基准为 `54ae500`，隔离工作区 `/Users/weisengao/.codex/worktrees/douyin-collection-import/douyin-wiki`，分支 `codex/douyin-collection-import`。
- 正式测试使用隔离 Vault 与假 provider；浏览器探针、原始响应和日志存放仓库外，正式夹具只保留脱敏数据。
- 不删除、移动或盲目同步现用 `.venv`，不直接修改生产 Vault、状态库或 Profile 文件。
- 当前 Agent 执行全部任务；新增行为采用 TDD，低影响文档改动不另写测试。
- 本计划的过程记录与探针保存于仓库外的专属临时目录；阶段提交与本计划复选框保存完成记录。

## Review Focus

- 分享短链转向合集后又导航到第一集时，导入范围仍为原合集；遇到外部或冲突合集编号应拒绝。
- 一轮读取有 null 页、重复页、游标循环或中途账号变化时，不能声称清单完整，重试不能混用两轮数量。
- 同一作品由多个入口同时提交时，确认必须幂等、选项冲突应整批拒绝，取消一批不能取消其他批仍需要的任务。
- 子任务等待另一个 Gateway 时，原会话事件及通知保持归属，关联父任务仍保持处理中。
- 删除资料、恢复和重建后，合集仍指向同一条目，非法侧车不能先清空有效数据。

---

### Task 1: 原生读取验证、目标解析与原始 JSON

**Files:**
- Create: `src/douyin_wiki/collection_models.py`, `src/douyin_wiki/adapters/collection_api.py`
- Modify: `src/douyin_wiki/adapters/share.py`
- Test: `tests/test_share.py`, `tests/test_collection_api.py`

**Interfaces:**
- Produces: `ResolvedCollection(original_url, canonical_url, mix_id, redirect_chain)`、`extract_collection_id(url: str) -> str | None`、`DouyinShareResolver.resolve_collection(share_text: str) -> ResolvedCollection`。
- Produces: `CollectionProfile`, `CollectionWork`, `CollectionInventory`；`RawCollectionPage(resource, mix_id, request_cursor, request_count, http_status, body)`；`parse_collection_page(raw: RawCollectionPage) -> CollectionPage`。
- `CollectionPage` 包含可选 profile、作品列表、返回 cursor、has_more 和 warnings；解析失败为 `ExternalToolError`，明确授权错误为 `BrowserAuthRequiredError`。

- [x] **Step 1: 在专用 Profile 中核实原生 GET 与主动翻页。** 2026-10-10 已验证详情函数 `{mixId}`，列表函数 `{mixId,cursor,count}`；带 `mixScene` 的列表导出唯一匹配，另一不带该字段的包装不调用。`count=2` 主动读取 cursor 0、2…16，最终 cursor 18、has_more=0，共 18 个稳定视频 ID。
  - 详情字段为 `mix_info.mix_id/mix_name/author/cover_url/statis`；`statis.updated_to_episode` 只用于已更新集数展示，未证明为可见视频总数，因此 `reported_count` 首期为空。
  - 列表字段为 `aweme_list/cursor/has_more/item_id_to_episode/status_code`；集数映射可能为 JSON 字符串，需要 Python 二次解析；作品明确归属来自 `mix_info.mix_id`。
- [x] **Step 2: 写失败测试。** 用 `httpx.MockTransport` 覆盖规范 URL 无网络、短链、合集转第一集、冲突编号和外部跳转；原始 JSON 用手写 19 位整数覆盖精度、冲突字符串 ID、非零状态、null 数组、归属不匹配、未知类型及视频/图文。
  - `test_collection_redirect_keeps_container_identity`: mix_id 为 `7651028428363139126`，分类为 collection，规范 URL 不成为第一集。
  - `test_collection_json_preserves_large_integer_id`: 原始整数 `7644758067783852901` 精确转为同值字符串。
  - `test_updated_episode_is_not_a_reported_work_count`: updated_to_episode=18 不填 reported_count。
- [x] **Step 3: 运行并确认测试因缺少合集能力失败。** `uv run --no-sync pytest -q tests/test_share.py tests/test_collection_api.py`；Expected: 上述新增断言失败。
- [x] **Step 4: 实现模型、解析和分类。** ID 1–32 位 ASCII 数字，拒绝 bool/float；只解析指定数组，不递归搜 ID；最多 8 次跳转且检查每一跳来源；保留旧单条解析形状。
- [x] **Step 5: 测试通过并提交。** 同一测试命令；Expected: 全部通过。提交 `feat: parse Douyin collection targets and raw pages`。

### Task 2: 浏览器客户端、串行清点与 Profile 保护

**Files:**
- Create: `src/douyin_wiki/adapters/collection_browser.py`, `src/douyin_wiki/adapters/collection.py`
- Test: `tests/test_collection_browser.py`, `tests/test_collection_adapter.py`
- Reference: `adapters/favorites_browser.py`, `adapters/image_note.py`

**Interfaces:**
- Consumes: Task 1 模型与 `parse_collection_page()`。
- Produces: `CollectionBrowserClient(page)` 异步上下文；`read_page(*, resource, mix_id, cursor="0", count=20, use_cached=True) -> RawCollectionPage`。
- Produces: `DouyinCollectionAdapter(settings, profile_dir).inventory(*, mix_id, original_url, expected_account_id=None, on_checkpoint=None, count=20) -> CollectionInventory` 和 `check_auth()`；检查点接收 `CollectionInventory`。
- `inventory_with_client(client, ...)` 分页算法可用具体假客户端测试，生产浏览器入口与测试共享该算法。

- [x] **Step 1: 写失败测试。** 使用现有浏览器事件 fake，验证同源 GET、目标/参数/世代关联、迟到响应清理和原生函数歧义；假原始页验证 3 页去重顺序、可信结束、null 后继续但不完整、cursor 循环、数量冲突、500 页及中途失败保留检查点。
  - `test_collection_rejects_other_mix_and_late_epoch_response`。
  - `test_collection_pages_preserve_first_seen_order`：输入 ID 顺序 30、10、20，返回仍为 30、10、20。
  - `test_collection_null_page_advances_but_remains_partial`。
- [x] **Step 2: 运行失败测试。** `uv run --no-sync pytest -q tests/test_collection_browser.py tests/test_collection_adapter.py`；Expected: 缺少读取能力的断言失败。
- [x] **Step 3: 实现读取器。** 原生函数仅匹配已经验证的 endpoint、mixId 和列表 mixScene/cursor/count；业务参数由网页函数生成；网络原始响应由 Python 解析。预算 20 秒、3 次尝试、间隔 1/2 秒，count=20、最多 500 页；缓存只处理匹配的首屏，不缓存旧世代。
- [x] **Step 4: 实现清点。** 复用 Profile 文件锁与现有登录判断；同一账号/页面串行读取，检查点后才翻页。明确登录阻挡为 needs_auth，签名或普通 403 为读取错误。无法完整结束保留 works 和 warnings。
- [x] **Step 5: 测试通过并提交。** 同一命令；Expected: 全部通过。提交 `feat: inventory collections through native browser requests`。

### Task 3: 批次存储、选择与原子分发

**Files:**
- Create: `src/douyin_wiki/collection_store.py`, `src/douyin_wiki/batch_capture.py`, `src/douyin_wiki/collections.py`
- Modify: `src/douyin_wiki/database.py`, `src/douyin_wiki/favorites_store.py`, `src/douyin_wiki/favorites.py`, `src/douyin_wiki/service.py`
- Test: `tests/test_collection_flow.py`, `tests/test_collection_lifecycle.py`

**Interfaces:**
- Consumes: Task 1/2 模型与适配器；既有 `CaptureRequest/CaptureOptions/GatewayContext`。
- Produces: `CollectionStore.create(request)`, `load(job_id)`, `begin_inventory(job_id) -> int`, `checkpoint(job_id, generation, inventory)`, `rows(job_id)`, `select(job_id, decision, *, ordinals=None, work_ids=None, all_works=False)`, `confirm(job_id, *, accept_partial=False, entry_intact)`, `parent_ids(child_id=None)`。
- Produces: `CollectionService(core, adapter).start(source_text, options=None, gateway_context=None)`, `process(job)`, `get(job_id, *, page=None, limit=None)`, `select(...)`, `confirm(...)`, `refresh(job_id)`, `refresh_for_child(child_id)`, `retry_failed(job_id)`, `history(limit=50)`。
- Produces: `batch_capture.capture_keys(request, artifacts) -> list[str]` 与 `active_capture_jobs(conn) -> dict[str, JobRecord]`；收藏旧导出保留为兼容引用。`Database.capture_parent_ids(child_id) -> list[str]` 查询批次关联。
- schema 15：四张 collection 表和 `creator_run_items.child_job_id`；已结束/取消/删除父任务不作为取消保护的活动引用。

- [x] **Step 1: 写失败测试。** 使用真实 SQLite、隔离 Vault 与具体假清点适配器，验证 checkpoint 租约、新世代、选择锁、全选跨页、pending 阻止确认、accept_partial、完整已有条目关联、并发/重复确认、分发异常回滚。
  - `test_collection_confirmation_is_idempotent`：两次确认的 child_job_ids 相同且仅一条 capture。
  - `test_collection_option_conflict_rolls_back_whole_confirmation`：两个所选作品中一个冲突，父任务仍 needs_selection，零新子任务。
  - `test_collection_retry_generation_excludes_disappeared_old_work`。
- [x] **Step 2: 运行失败测试。** `uv run --no-sync pytest -q tests/test_collection_flow.py tests/test_collection_lifecycle.py`；Expected: 功能缺失的断言失败。
- [x] **Step 3: 实现存储与事务。** 当前世代过滤 rows，保存旧摘要；一次 BEGIN IMMEDIATE 发布全部子任务与父任务状态，重复确认返回同一结果；查重与选项核对先于确认成功。子任务仍 capture，新增 collection_context，不覆盖复用任务上下文。
- [x] **Step 4: 实现 Controller 与共享关系。** Service 构造新增可选 collection_adapter；dispatch_creator_import 同步记录 child_job_id、使用公共身份查重，收藏使用公共身份函数。历史不确定活动引用保护子任务。
- [x] **Step 5: 测试通过并提交。** 运行同一命令及 `tests/test_favorites_flow.py tests/test_creator.py`；Expected: 全部通过。提交 `feat: persist and dispatch collection import batches`。

### Task 4: Worker、任务生命周期、Gateway 和授权

**Files:**
- Modify: `service.py`, `worker.py`, `database.py`, `operation.py`, `localization.py`, `web_operation.py`, `auth_guidance.py`, `web_auth.py`, `gateway_protocol.py`, `mcp_server.py`
- Test: `tests/test_collection_lifecycle.py`, `tests/test_gateway_context.py`, `tests/test_auth_guidance.py`

**Interfaces:**
- Consumes: Task 3 `CollectionService` 与 `Database.capture_parent_ids()`。
- Produces: kind `collection_import` 的 Worker 路由、全部关联父任务刷新、共享取消保护、collection 授权范围及协议 1/2 collection_context。
- 父任务以实际终态汇总；等待 Gateway 保持 monitoring，失败/取消/不完整/子任务警告为 completed_with_warnings；取消和删除父任务不恢复执行。

- [x] **Step 1: 写失败测试。** 覆盖复用独立任务不取消、取消一批保留共享任务、取消最后活动批次可取消、不同入口共同刷新、仅失败项重试、迟到刷新不复活取消父任务、等待另一 Gateway、两协议上下文和登录恢复。
  - `test_cancel_collection_preserves_shared_favorites_child`。
  - `test_collection_waits_for_original_gateway_without_rerouting_events`。
  - `test_collection_auth_retry_uses_existing_douyin_channel`。
- [x] **Step 2: 运行失败测试。** `uv run --no-sync pytest -q tests/test_collection_lifecycle.py tests/test_gateway_context.py tests/test_auth_guidance.py`；Expected: 新行为断言失败。
- [x] **Step 3: 实现任务路由与取消/刷新。** 不只依赖 creator/favorites 创建时上下文；数据库关系决定所有父任务，状态更新沿用 expected_updated_at/expected_child_updates。独立创建的 capture 保留原通知行为，只有合集新建子任务带 batch_silent。
- [x] **Step 4: 实现授权和投影。** 增加 auth_scope collection，复用 douyin 登录通道及同通道重试；协议错误不归为登录错误。用户可见任务名称和导入历史包含合集。
- [x] **Step 5: 测试通过并提交。** 同一命令及 `tests/test_favorites_review.py`；Expected: 全部通过。提交 `feat: integrate collection lifecycle and gateway context`。

### Task 5: 合集知识关系、索引、维护与废纸篓

**Files:**
- Modify: `collections.py`, `collection_store.py`, `vault.py`, `database.py`, `service_maintenance.py`, `service_trash.py`
- Test: `tests/test_collection_vault.py`

**Interfaces:**
- Consumes: Task 3 持久 collections/collection_works 及真实 entry_id 路径。
- Produces: `CollectionService.get_collection(mix_id)`, `list_collections()`, `refresh_documents(mix_id, *, action)`；Vault `write_collection(profile, works, entries)`、`load_collections()` 及 `last_collection_load_errors`。
- 索引路径 `collections/<首次安全名称>_<mix_id>/index.md`，侧车 `.data/collection.md`、type collection-machine-data、version 1。成员/顺序仅在完整读取后更新，部分发现单独标识。

- [x] **Step 1: 写失败测试。** 验证相同视频跨合集仍一份资料、真实相对链接、改名目录稳定、侧车可重建、文件失败事务回滚、非法 ID/路径/条目不应用重建、资料删除解除链接并恢复关联、历史删除不删除索引。
  - `test_collection_sidecar_error_preserves_existing_database`。
  - `test_restore_entry_reconnects_all_collections`。
- [x] **Step 2: 运行失败测试。** `uv run --no-sync pytest -q tests/test_collection_vault.py`；Expected: 新持久关系断言失败。
- [x] **Step 3: 实现发布与索引。** entry_operations_locked、SQLite 事务和 reversible_writes 按协作 API 配合；快照不保存 Cookie、账号、原始接口响应或运行租约，合集不成为 EntryRecord。
- [x] **Step 4: 实现重建及删除/恢复。** 先验证全部侧车再替换合集投影，活动导入阻止应用；按 work_id 连接恢复条目。未确认批次的进度展示来自运行表，运行历史清理不损坏知识投影。
- [x] **Step 5: 测试通过并提交。** 同一命令及既有维护/废纸篓覆盖的 `tests/test_service.py`；Expected: 全部通过。提交 `feat: preserve collection knowledge links in vault`。

### Task 6: Service 门面、CLI、MCP 与 Web 导入

**Files:**
- Create: `src/douyin_wiki/webapp/static/imports-collections.js`
- Modify: `service.py`, `cli.py`, `mcp_server.py`, `webapp/operation_api.py`, `webapp/templates/app.html`, `webapp/static/app.js`, `webapp/static/shared.js`, `webapp/static/app.css`
- Test: `tests/test_collection_interfaces.py`, `tests/test_collection_ui.py`, `tests/test_web_navigation.py`

**Interfaces:**
- Consumes: Task 3/5 Controller。
- Produces Service/MCP: `capture_douyin_collection(source_text, options=None, gateway_context=None)`, `get_collection_inventory(job_id, *, page=None, limit=None)`, `set_collection_work_selection(job_id, decision, *, ordinals=None, work_ids=None, all_works=False)`, `confirm_collection_import(job_id, *, accept_partial=False)`, `retry_collection_import(job_id)`, `get_collection(mix_id)`, `list_collections()`, `list_collection_imports(*, limit=50)`。
- Produces CLI: collection add/inventory/select/confirm/retry/list/show/history；MCP 不传 page/limit 时返回全部清单，Web 显式分页，limit 1–1000。
- Produces Web: `/imports/collections`、`POST /api/collections/inventory`、`GET /api/collections[/mix_id]`、`GET /api/collection-imports[/job_id]`、`POST /api/collection-imports/{job_id}/{selection,confirm,retry}`。

- [x] **Step 1: 写失败测试。** 使用 Typer runner、真实 Service/隔离 SQLite、FastAPI TestClient 与 MCP 工具注册，验证创建、默认全清单、三种互斥选择器、400/404/409、全选全部页、分类预填与不自动入库。
  - `test_mcp_collection_inventory_returns_all_by_default`：105 项不截为第一页。
  - `test_collection_web_selection_conflict_is_409`。
- [x] **Step 2: 运行失败测试。** `uv run --no-sync pytest -q tests/test_collection_interfaces.py tests/test_web_navigation.py`；Expected: 新接口断言失败。
- [x] **Step 3: 实现后端入口。** Pydantic 校验、旧入口兼容、创建/确认/重试 HTTP 202；Service 门面方法与 MCP 命名一致。
- [x] **Step 4: 实现导入页面。** 复用现有表单和清单组件；新增菜单/空库/命令面板，粘贴 collection 后预填；新作品默认未选，保存选择后才能确认，全选明确作用于全部页；刷新从数据库恢复，显示部分读取、已有/复用/新任务和实际结果。
- [x] **Step 5: 测试通过并提交。** 同一命令及 `tests/test_favorites_interfaces.py tests/test_web_operation.py`；Expected: 全部通过。提交 `feat: expose collection import through web cli and mcp`。

### Task 7: 集成、真实读取、界面与发行验收

**Files:**
- Modify: `README.md`, `docs/README.md`, `docs/architecture.md`, `docs/gateway-agents.md`, `docs/web-ui-design-system.md`, `docs/service-collaboration-api.md`, `CHANGELOG.md`, 本计划及设计状态。
- Test: 项目正式检查、操作页浏览器验证、wheel 包成员检查；记录保存在本计划专属仓库外目录。

**Interfaces:**
- Consumes: 全部已实现入口和运行行为；文档以实际功能及实测限制为准。
- Produces: 回归结果、真实完整分页证据、隔离 Vault 逐条处理证据、可检查的分支提交。

- [x] **Step 1: 完成正式检查。** `uv run --no-sync ruff check src tests`、`uv run --no-sync pytest -q -m 'not live'`；Expected: 全部通过。若存在原有失败，按名称与证据报告并判断是否阻塞，不隐藏结果。
- [x] **Step 2: 使用正式适配器进行真实读取。** 用户样本按 count=2 强制多页，与同次网页合集区域比较 ID 和顺序；Expected: 明确结束、同一集合与顺序，当前样本18不是固定断言。
- [x] **Step 3: 独立测试 Vault 验证逐条处理。** 新视频、已有视频、故意失败；Gateway 模式校正/分析真正完成后验证条目、关联及父任务计数。不得向生产 Vault 导入测试资料。
- [x] **Step 4: 浏览器验收。** 本地测试服务操作合集粘贴、清点、全部页选择、确认、刷新、键盘、亮/暗主题及窄屏；Expected: 无未处理 JS 错误、全选范围与数据库一致，等待保存后才能确认。
- [x] **Step 5: 构建与文档。** `uv build --out-dir <本计划仓库外目录>/dist`，检查 wheel 包含新 JS 和 Python 文件、无探针或缓存。更新使用文档和协作 API，设计状态对应实际验收结果。
- [x] **Step 6: 整分支审查并完成。** 当前 Agent 独立进行一次审查，检查 Review Focus 与测试证据；重要问题写失败测试修正，再跑相关和必要回归。完成后保留交付和必要验收证据，仅清理本任务确认不再需要的临时内容；本轮不自动推送、部署或执行生产合集入库。

## 实施记录（2026-10-10）

当前 Agent 执行全部开发、审查和真实验证，没有委派。实现基准为 `54ae500`，隔离工作区和生产边界沿用上文。

| 任务 | 提交与已确认验证 |
| --- | --- |
| 1：目标及原始 JSON | `26f0d87`、`0e5ac54`；24 个新增断言先失败后通过，连同旧测试35项通过。第一次静态检查失败后立即修正并复验，没有隐去原记录 |
| 2：浏览器及清点 | `e8d5cf2`；初版14项通过，正式适配器 count=2 读取18件；审查后账号/页上限回归补充 |
| 3：存储及分发 | `e8098f3`；合集、收藏及博主27项相关验证通过 |
| 4：生命周期与 Gateway | `9316283`；102项相关测试通过，新增授权/协议用例后27项通过；Worker 复用现有刷新入口，无需修改 worker.py |
| 5：Vault | `8dec4bc`；7项合集关系测试及114项 Service/博主相关测试通过 |
| 6：三入口及 Web | `d0529f3`；57项接口/生命周期/Web测试与2项真实 HTTP 浏览器测试通过，清单105件 |
| 7：审查修复 | `0238ec2` 修复稳定账号及异常 URL 边界；3项新错误先失败后通过，67项相关回归通过。`66432c1`、`afc2eb5` 修复空白历史及当前历史状态同步，浏览器断言先失败后通过 |

首次完整检查1374项通过、2项失败、2项 live 排除；两处失败均为旧测试预期 schema14，而新增合集迁移已为15，更新预期后完整集1379项通过。最终源码的完整检查、发行包及覆盖边界以[验收记录](../reviews/2026-10-10-douyin-collection-import-acceptance.md)为准。

同轮正式分页与网页独立展开的18个ID、集数和顺序完全一致；网页加载更多两次后消失。独立 Vault 完成一条真实视频的下载/ASR/OCR、当前 Agent 校正分析和入库，之后复用已有条目并对另一子任务注入下载失败。该样本清单来自真实正式适配器，入库测试回放已验证清单，不再次读网页。

界面使用 `tests/test_collection_ui.py` 的隔离真实 HTTP 服务；亮/暗主题为应用主题控件实际选择，390px 截图在侧栏过渡结束后检查。正式浏览器测试和仓库外截图取代额外新增一次性操作脚本。必要原始证据保存仓库外，不进入发行包。

最终检查：运行代码 `afc2eb5` 的完整非 live 集1379 passed / 2 deselected（191.02秒）；Ruff 和 diff 检查通过，wheel 的98个成员与源码一致，独立安装CLI与新增8个MCP工具可用。Task7全部步骤完成；完整证据及覆盖限制见验收记录。本轮保留开发分支与工作区，不集成现用服务。
