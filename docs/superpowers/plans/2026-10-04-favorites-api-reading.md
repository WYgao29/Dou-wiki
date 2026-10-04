# 抖音收藏夹 API 读取 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 默认由当前 Agent 执行，不委派子 Agent；用户明确另选执行方式时才调整。

**Goal:** 修复真实账号收藏夹目录和指定夹作品读取，使用原始 API JSON 获取稳定 ID，并保留现有导入与不完整结果确认流程。

**Architecture:** 纯 JSON 协议处理放在 `adapters/favorites_api.py`，网页原生只读请求与响应关联放在 `adapters/favorites_browser.py`。`adapters/favorites.py` 保留 Profile、账号、范围、分页合并和检查点责任；全部收藏的既有作品列表路径保持。

**Tech Stack:** Python `>=3.12,<3.14`、Playwright、标准库 JSON/asyncio、现有 Pydantic 模型、pytest/pytest-asyncio、ruff；不新增依赖。

**Spec:** [开发设计草案](../specs/2026-10-04-favorites-api-reading-design.md)。执行前同时读取设计与本计划。

**Status:** 2026-10-04 用户确认方案，并授权在独立 worktree 内开发；不包含发布、重启现用服务或真实导入。

## Global Constraints

- Python 支持范围保持 `>=3.12,<3.14`，不新增依赖或新的持久化表。
- MCP 工具名称与参数语义、CLI 命令与别名、Service 公开方法、Web 路径与负载、状态码、静态资源 URL 和 Vault 格式保持兼容。
- 清点仅读取目录与作品元数据；下载、转录、模型分析和入库继续由用户确认后的既有子任务执行。
- 全部收藏仍包含未分组作品，不主动遍历所有收藏夹；指定多个收藏夹取并集，以作品 ID 去重并保留已确认归属。
- 数量不一致或无法证明分页完成时保留已发现作品，标记不完整，并沿用明确接受不完整清单的确认要求。
- 本机服务引用仓库 `.venv/bin/python`；不移动或盲目同步现用环境。开发与安装验证使用隔离环境；不直接编辑用户 Vault、状态库或原浏览器 Profile。
- 本次不授权发布、重启服务、切换真实客户端配置或批量导入。首次只读实测的授权覆盖同范围开发验证；出现需要用户处理的登录或验证码时再说明具体阻塞。
- 默认由当前 Agent 执行。使用 Superpowers 不授权子 Agent；委派须用户明确要求。

实施确认后使用 using-git-worktrees 检查可用隔离 checkout/worktree，优先复用适用工作区；新建分支使用 `codex/` 前缀。从包含已确认文档的实际 Git 状态开始，保留用户修改与暂存状态，不自动 stash。下列命令中的 `uv run --no-sync` 使用已经具备开发依赖的隔离环境，不能借此同步现用 `.venv`。

## Review Focus

| ID | 容易遗漏的输入或失败方式 | 要求与测试归属 |
| --- | --- | --- |
| R1 | 19 位数字 ID 经 JS Number 舍入、字符串与整数 ID 冲突 | 从原始字节在 Python 解码；冲突不能进入可信目录。任务 2 |
| R2 | 网页预取了未选夹、响应乱序或已结束会话的响应 | 以资源、ID、cursor/count 和当前会话关联；返回清单不串夹。任务 3、4 |
| R3 | HTTP 200 但列表为 null、has_more 缺失、cursor 循环 | 不当成空收藏或分页结束；保留检查点并提示不完整。任务 2、3、4 |
| R4 | 目录显示 7 条，列表 4 条且 has_more=0 | 目录可完整，作品清单不完整；确认仍要求 accept_partial。任务 4、5 |
| R5 | 抖音号与年龄紧邻、分页时换账号或失去租约 | 身份不拼接年龄、不从夹所有者推断；旧账号/旧租约不写检查点。任务 4 |

## 文件与内部接口

| 文件 | 创建或修改的内容 |
| --- | --- |
| `src/douyin_wiki/adapters/favorites_api.py` | `RawFavoritePage`、`FavoriteApiPage`、原始字节解析与字段映射 |
| `src/douyin_wiki/adapters/favorites_browser.py` | `FavoritesBrowserClient`，仅负责网页请求、响应关联和生命周期 |
| `src/douyin_wiki/adapters/favorites.py` | 目录与指定夹改接 API；保留全部收藏 DOM；修正账号回退边界 |
| `tests/test_favorites_api.py` | 协议、精确 ID、类型与异常响应 |
| `tests/test_favorites_browser_client.py` | 假网页模块和拦截网络下的客户端行为 |
| `tests/test_favorites_adapter.py` | API 清点、分页、账号、范围与检查点回归 |
| `tests/test_favorites_browser.py`、`tests/test_favorites.py`、`tests/test_favorites_flow.py` | 保留既有行为断言，补目录和 7/4 的确认链路 |
| `scripts/verify_favorites_api.py`、`tests/test_live_smoke.py` | 显式选择的真实只读验收，不生成采集任务 |
| `docs/favorites-import.md`、`docs/architecture.md`、`docs/README.md`、`CHANGELOG.md` | 实现完成后更新读取方式、验证边界与交付记录 |

两个 GET 端点：`/aweme/v1/web/collects/list/`、`/aweme/v1/web/collects/video/list/`。内部 `resource` 只取 `"directory"` / `"folder"`；默认 `count=20`，每种资源最多 500 页，单次读页含重试总预算 20 秒。顶层公共 `FavoriteInventory`、`FavoriteFolder`、`FavoriteWork` 的字段不变。

内部契约固定如下，字段来自设计规格，不把原始认证数据写入任何模型：

```python
@dataclass(frozen=True)
class RawFavoritePage:
    resource: Literal["directory", "folder"]
    folder_id: str | None
    request_cursor: str
    request_count: int
    http_status: int
    body: bytes

@dataclass(frozen=True)
class FavoriteApiPage:
    folders: list[FavoriteFolder]
    works: list[FavoriteWork]
    next_cursor: str | None
    has_more: bool | None
    reported_count: int | None
    valid: bool
    warnings: list[str]

def parse_favorite_page(response: RawFavoritePage) -> FavoriteApiPage: ...

class FavoritesBrowserClient:
    def __init__(self, page: Any) -> None: ...
    async def __aenter__(self) -> FavoritesBrowserClient: ...
    async def __aexit__(self, exc_type, exc, traceback) -> None: ...
    async def read_page(
        self, *, resource: Literal["directory", "folder"], cursor: str = "0",
        folder_id: str | None = None, count: int = 20, use_cached: bool = True,
    ) -> RawFavoritePage: ...
```

`valid=False` 表示 JSON 可解析但存在不可信字段或记录；有效记录仍可交付。登录拒绝抛出 `BrowserAuthRequiredError`，非 JSON/传输/原生请求函数不可用抛出 `ExternalToolError`。`has_more=None` 永远不表示完成。响应回调不直接调用 `on_checkpoint`。

依赖顺序：任务 1 → 任务 2 → 任务 3 → 任务 4 → 任务 5。每个生产行为任务按测试失败、最小实现、测试通过、检查差异、提交推进；任务 1 是低影响只读探针，不为临时探针编写复述实现的测试。新任务应先读前一个任务的实际结果，不把网页模块参考参数当成已验证事实。

## Task 1: 验证主动请求与真实翻页

**Files:** 仓库外维护目录创建临时 `active_favorites_probe.py` 与结果记录；只在完成后更新设计第 2、3 节的验证事实，不创建产品模块。

**Interfaces:** Consumes 现有配置、Profile 锁与必要会话文件的隔离副本；Produces 经验证的只读函数桥识别规则、参数形状、目录/作品请求关联字段和跨页结果。临时副本关闭后删除，报告不含 Cookie、签名、完整 URL 或个人完整响应。

- [ ] **Step 1: 准备隔离探针。** 沿用此前 Profile 副本方法；只使用这两个已知 GET 端点的网页原生函数，禁止硬编码模块编号或导出名、试调用未知函数、修改已签名 URL。
- [ ] **Step 2: 强制主动调用。** 目录用 `offset=2` 验证两页；至少一个超过 2 条的本账号收藏夹按精确字符串 ID、`offset=2` 读到结束。关闭初始响应缓存复用，证明请求由探针主动触发而非首屏预取；记录接口实际对应的 count/cursor。
- [ ] **Step 3: 检查结果。** 合并后的目录 ID 与首屏原始目录一致；夹内 ID 与当前网页可见列表一致且未被舍入；两种资源均有真实下一页请求、cursor 推进和明确终止。必须核对请求路径、资源 ID、分页参数，不能只核对条数。
- [ ] **Step 4: 固化决定。** 在规格第 3 节写入实际函数定位与参数验证结果，并记录网页构建标识仅供诊断；不把标识变成运行时硬编码。任一主动调用/翻页未通过，停止正式接入并修订设计，不能交付只读首屏的实现。
- [ ] **Step 5: 检查探针清理与范围。** 原 Profile 未被直接改写，临时副本已删除，没有采集子任务或模型调用。保留可复现探针与必要的去敏验证材料在仓库外。

**完成判据：** 两个资源都证实主动跨页读取；明确记录真实账号只覆盖视频。没有通过就不能把任务 2–5 的产品方案当成可上线结果。

## Task 2: 原始协议解析与类型边界

**Files:** Create `src/douyin_wiki/adapters/favorites_api.py`、`tests/test_favorites_api.py`；不修改公开 Pydantic 模型。

**Interfaces:** Produces 上述 `RawFavoritePage`、`FavoriteApiPage` 与 `parse_favorite_page(response)`；后续任务只通过这些契约交付响应与页面结果。

- [ ] **Step 1: 写行为测试。** 定义 `test_folder_id_preserves_int64_precision`，原始 body 含整数 `7000000000000000123` 与同值字符串，断言 `id` 精确；冲突、浮点、布尔和非数字 ID 断言 `valid=False` 且没有虚构目录。定义 `test_empty_is_distinct_from_missing_or_null_list`，成功 `[]` 可与明确结束组成空目录，`null`/缺失不能可信结束。
- [ ] **Step 2: 补类型与协议断言。** `test_has_more_requires_explicit_supported_value` 覆盖 False/0/`"0"` 与缺失；`test_folder_total_is_not_work_total` 区分顶层目录总数与每夹作品数。视频、已有静态图文结构、明确文章路径映射到既有三种类型；`test_unknown_work_kind_remains_unavailable` 断言稳定 ID 保留、available=False、valid=False。非零登录状态抛授权错误，错误 JSON/HTTP 不构造成成功空页。
- [ ] **Step 3: 跑失败测试。** Run `uv run --no-sync pytest -q tests/test_favorites_api.py`。Expected 新函数缺失或断言失败，不接受只有环境导入错误的失败证据。
- [ ] **Step 4: 实现并验证。** 在指定模块实现契约，Python 直接解码原始字节、优先字符串 ID；使用现有模型校验规范 URL，不复用 JS 转换后对象。Run 同一测试命令及 `uv run --no-sync ruff check src/douyin_wiki/adapters/favorites_api.py tests/test_favorites_api.py`。Expected 全部 PASS。
- [ ] **Step 5: 提交本任务。** 只暂存本任务两个文件；提交信息 `feat: parse favorites API responses without ID precision loss`，保留其他暂存内容。

## Task 3: 网页原生请求与响应关联

**Files:** Create `src/douyin_wiki/adapters/favorites_browser.py`、`tests/test_favorites_browser_client.py`；纯解析模块只做契约所需的小修。

**Interfaces:** Consumes 任务 1 验证过的网页函数桥和任务 2 的 `RawFavoritePage`；Produces 上述 `FavoritesBrowserClient`。`__aenter__` 安装监听，调用方随后导航；`read_page` 只读，`use_cached=False` 必须主动调用；`__aexit__` 移除监听并处理所有在途任务。

- [ ] **Step 1: 写请求与关联测试。** 在临时 Playwright 页面提供模拟网页模块，以 `route` 拦截全部网络。`test_listener_precedes_bootstrap_navigation` 断言不会丢首目录响应；`test_read_page_calls_native_api_with_exact_folder_id` 断言任务 1 已核验参数与 19 位 ID 原样传递；`test_response_matching_ignores_wrong_folder_cursor_and_origin` 让错误夹、旧 cursor、外站先返回，仍只能取得正确响应。
- [ ] **Step 2: 写失败与生命周期测试。** `test_force_read_does_not_use_prefetched_response`、`test_native_api_unavailable_does_not_fallback_to_unsigned_http`、`test_cancel_closes_listener_without_late_result`。使用可控时钟/假请求验证 20 秒总预算与最多 2 次、1/2 秒退避；403/无效 JSON/登录拒绝不无限重试，完整 URL 与 Cookie 不进入日志。
- [ ] **Step 3: 跑失败测试。** Run `uv run --no-sync pytest -q tests/test_favorites_browser_client.py`。Expected 新客户端缺失或明确行为失败；测试不得加载个人 Profile 或真实网络。
- [ ] **Step 4: 实现并验证。** 落实 `read_page` 的路径、GET、ID、cursor/count、当前页面会话匹配；按任务 1 的实际规则绑定只读原生函数，原生返回值不用于业务解析。Run 任务 2/3 测试及对应文件 ruff。Expected 全部 PASS，测试结束无未处理的后台任务。
- [ ] **Step 5: 提交本任务。** 提交信息 `feat: read favorites through the authenticated page API`；不新增签名依赖或第二套 Cookie 导出机制。

## Task 4: 接入目录、指定夹分页与检查点

**Files:** Modify `src/douyin_wiki/adapters/favorites.py`、`tests/test_favorites_adapter.py`、`tests/test_favorites_browser.py`；保留全部收藏的 DOM 作品读取测试。

**Interfaces:** Consumes `FavoritesBrowserClient.read_page` 与 `parse_favorite_page`；Produces 现有 `inventory(*, folder_ids=None, directory_only=False, expected_account_id=None, on_checkpoint=None) -> FavoriteInventory`。新增内部 `_scan_api_resource(self, client: FavoritesBrowserClient, *, resource: Literal["directory", "folder"], account_id: str, nickname: str, folders: list[FavoriteFolder], folders_complete: bool, folder_id: str | None, existing: list[FavoriteWork], inherited_warnings: list[str], on_checkpoint: Checkpoint | None) -> FavoriteInventory`；它在每个有效页 await 检查点后读取下一页。

- [ ] **Step 1: 写范围与完整性测试。** `test_api_directory_does_not_require_empty_tabpanel` 断言四目录可读取；`test_directory_only_never_requests_folder_pages` 断言只输出目录；`test_selected_folders_merge_duplicate_memberships` 用两夹共享一作品，断言只一件作品且两归属。`test_unselected_prefetch_does_not_enter_inventory` 断言其他夹响应不混入作品与计数。`test_all_favorites_keeps_ungrouped_dom_items` 断言默认全部模式仍保留未分组作品、不逐夹清点；`test_all_favorites_survives_directory_protocol_failure` 断言目录一般错误只产生提示，仍读取原账号级作品，明确授权错误则继续中止。
- [ ] **Step 2: 写分页与恢复测试。** `test_api_inventory_checkpoints_before_next_page` 断言先存检查点再发下一页；`test_api_inventory_handles_more_than_5000_works` 用 5121 个不同 ID、每页 20 件，断言无截断。循环/不推进 cursor、空页且仍有下一页、缺失 has_more、500 页上限、后续响应失败均保留已读作品并 complete=False；第一可信页前失败抛工具错误。
- [ ] **Step 3: 写数量与身份测试并跑失败。** `test_reported_seven_observed_four_is_partial` 断言目录完整、作品完整性为 False，保留四件和准确提示。离线真实形状 DOM 的抖音号/年龄邻接用例不得把年龄并入 ID；夹所有者变化不改变登录账号。分页换账号抛授权错误；检查点取消/租约异常原样传播。Run `uv run --no-sync pytest -q tests/test_favorites_adapter.py tests/test_favorites_browser.py tests/test_favorites.py`。Expected 新行为失败。
- [ ] **Step 4: 实现并验证。** 在原 Profile 锁与 context 生命周期内接入监听，导航使用收藏夹子页参数；按 API 目录验证所选 ID，使用明确分页信号、范围内去重和数量差异规则。局部修正 `_ACCOUNT_SNAPSHOT_JS`，不迁移历史账号 ID。不捕获取消/租约错误为普通分页失败。新链路通过后删除已无运行用途的目录/开夹 DOM 辅助代码，迁移其安全断言；保留账号与全部作品所需 DOM。Run 任务 2–4 测试及 `uv run --no-sync ruff check src tests`。Expected 全部 PASS。
- [ ] **Step 5: 提交本任务。** 提交信息 `fix: inventory favorites folders by stable API IDs`；Service/store 仅在明确兼容性缺陷需要时做局部修改，并补直接覆盖该缺陷的行为测试，不能顺带重构。

## Task 5: 确认流程、只读验收与交付文档

**Files:** Modify `tests/test_favorites.py`、`tests/test_favorites_flow.py`、`tests/test_favorites_interfaces.py`、`tests/test_favorites_browser.py`、`tests/test_live_smoke.py` 与文档表中的四份记录；Create `scripts/verify_favorites_api.py`。生产 Web/CLI/MCP 文件预计不需修改。

**Interfaces:** Consumes 已集成的真实适配器与既有 `FavoritesService`；Produces 兼容原接口的完整目录与指定夹清点。只读脚本参数固定为 `--directory-only`、可重复 `--folder-id`、`--page-size`（默认 20）、`--output-dir`；要求 `DOUYIN_WIKI_RUN_LIVE=1`，否则拒绝真实访问。

- [ ] **Step 1: 写确认链路回归。** 通过临时 Vault 和假 API 的目录 7/作品 4 数据清点，断言没有 capture 子任务；未接受不完整清单时确认拒绝，`accept_partial=True` 才能创建四件的现有子任务，重复确认不重复派发。Web 仍显示数量提示及接受控件；目录夹 ID 与 CLI/MCP 原参数、负载、状态码保持。
- [ ] **Step 2: 跑失败、实现最小必要修正。** Run `uv run --no-sync pytest -q tests/test_favorites.py tests/test_favorites_flow.py tests/test_favorites_interfaces.py tests/test_favorites_browser.py`。既有确认实现若已满足要求，只增加必要回归，不为测试制造产品修改；新测试通过也如实记录，没有行为缺口就不伪造 RED。
- [ ] **Step 3: 提供只读正式验收脚本。** 复用配置与锁，使用独立 Profile 副本，先执行标准适配器的默认页大小清点，证明正式入口已经接入；`--page-size` 非 20 时，再通过请求客户端以指定大小强制翻页，与标准清点的精确 ID 集合对照，不改变公开 inventory 参数。输出目录、计数、结束状态与必要作品 ID，不实例化生产任务队列或调用 confirm。退出删除本次副本，记录材料放 `--output-dir` 指定的仓库外目录。给 live 测试添加同样的显式开关与只读范围，默认离线集排除它。
- [ ] **Step 4: 完成相称验证。** Run `uv run --no-sync ruff check src tests`、`uv run --no-sync ruff check scripts/verify_favorites_api.py`、`uv run --no-sync pytest -q -m 'not live'`。Expected 全部 PASS；出现失败先确认是否与本次有关，不能减少既有断言。再执行显式选择的真实只读验收：目录、按 ID 单夹、多夹、page-size=2 强制跨页；与当次 Chrome 列表对照，数量不一致如实保留。不得把 15 个当前视频的读取成功写成完整下载/图文/文章/入库通过。
- [ ] **Step 5: 更新文档并收尾。** 更新收藏说明的目录/指定夹 API、默认全部模式、精确 ID、数量不一致与不完整确认；architecture 补两个模块职责；索引和更新日志记录实际状态及仓库外证据。提交信息 `test: verify favorites API reading and partial confirmation`。检查 Git 差异与静态资源/公开签名不变，清理仅本次已确认无用的临时副本，不发布或重启现用服务。

## 最终判据与执行交接

所有判据均应有实际验证记录：主动目录与指定夹第二页已经通过；离线超过 5000 条、精确 ID、范围和恢复行为通过；Web/CLI/MCP 兼容；真实目录和当前可访问作品与 Chrome 对得上；“时尚”7/4 类差异不会被静默清除或宣称完整；清点没有下载、模型调用或创建采集子任务。

设计与计划确认后，当前 Agent 使用 executing-plans 顺序执行。首阶段可能需要根据网页实际构建调整函数桥，但不能改变两个只读端点、公开契约或完整性要求；验证不通过就回到设计，不把原生请求不可用降级成无签名请求、虚假空目录或只读首屏成功。部署、真实客户端切换和批量导入另行按用户指令处理。
