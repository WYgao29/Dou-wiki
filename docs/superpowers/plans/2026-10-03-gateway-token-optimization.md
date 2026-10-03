# Gateway token 优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 默认由当前 Agent 执行，不委派子 Agent；用户明确另选执行方式时才调整。

**Goal:** 为 Gateway 增加显式启用的紧凑协议，减少校正、分析和工具往返的重复文本，并保持原始证据与旧客户端兼容。

**Architecture:** 纯材料处理集中在 `gateway_sources.py`，阶段上下文、schema、版本和回执集中在 `gateway_protocol.py`。Service 保留状态、补丁合成、数据库并发与原有校验责任；MCP/CLI 在两步提交闭环完成后开放可选版本 2，Vault 继续存储标准分析。

**Tech Stack:** Python `>=3.12,<3.14`、Pydantic、SQLite、MCP、Typer、pytest/pytest-asyncio、ruff；不新增依赖。

**Spec:** [已确认的设计规格](../specs/2026-10-03-gateway-token-optimization-design.md)。执行前同时读取规格与本计划。

**Status:** 2026-10-03 设计、书面规格与实施计划均已获用户确认；任务 1–4 已提交，任务 5 整体离线验收已通过，整体自审待完成。实施分支 `codex/gateway-token-optimization` 从 `70717b9` 开始；现用服务环境与真实客户端未切换。

## Global Constraints

- Python 支持范围保持 `>=3.12,<3.14`，不新增依赖或新的持久化表。
- MCP 工具名称、既有参数语义、CLI 命令与别名、Service 默认行为、状态码、Web 接口与 Vault 格式保持兼容。
- 旧协议默认保留；新客户端显式选择紧凑协议，不全局切换默认值。
- 原始 OCR、ASR、校正稿、修改记录及最终标准分析结果保留。过滤和引用均为传输层处理，证据校验读取原始任务材料。
- `provider` 与 `local` 模型路径保持现有流程；本地 `_nearby_ocr` 不在本轮调整。
- 灵感逐字保留；标签沿用当前共享规则，通常两个、最多三个，不提供已有标签清单。
- 长视频确认、提醒确认、批量静默、事件归属与成功后确认规则保持。上下文版本不提供身份鉴权或任务排他领取。
- 实际客户端配置、运行状态和验收材料遵循 [接入契约](../../agents/integration-contract.md)，放在仓库外；本规格不授权修改生产 Vault、Profile、现用环境或真实客户端配置。

计划确认后，先按 using-git-worktrees 核对已有可用 checkout/worktree，优先复用适用的隔离 checkout；新建时从含本计划的已核验提交开始，不能丢掉本机尚未推送的设计文档。检查当前安装服务引用与工作区/暂存状态，不移动现用 `.venv`，不自动 stash 或捎带提交用户改动。以下验证命令使用已具备开发依赖的隔离环境，`uv run --no-sync` 不触发现用环境同步。

## Review Focus

| ID | 输入或失败方式 | 应有行为与测试归属 |
| --- | --- | --- |
| R1 | 同一句出现在远处、另一张图或带不同数字/符号 | 只过滤附近精确字幕；位置不串用。任务 1 的窗口/图片测试，任务 4 的错位引用测试 |
| R2 | 旧版本、重复提交或版本校验后发生并发更新 | 不把旧补丁/分析写入新任务快照；丢回执后先检查阶段。任务 3/4 的拒绝与并发测试，任务 5 的重放测试 |
| R3 | OCR 引用省略 provenance，或推断被补成事实来源 | 来源从实际编号推导，只有显式字段才比较冲突；推断保持推断。任务 4 的来源与原子等价测试 |
| R4 | 精简 schema 错删业务 title/约束，旧客户端或 Service stub 不接收新 kwargs | 必需字段与联合约束保留；旧入口走原调用方式。任务 2 的 schema 测试，任务 5 的旧接口与 CLI 测试 |
| R5 | Unicode 扩展、256 字边界或第 20 条/6000 字之后仍有材料 | 原文完整，引用不落在字符展开内部，不截尾。任务 1 的单元/全片测试，任务 4 的 Unicode 引文测试，任务 5 的大材料闭环 |

## 文件与依赖

新增生产文件仅 `src/douyin_wiki/gateway_sources.py`、`src/douyin_wiki/gateway_protocol.py`。新增正式测试为 `tests/test_gateway_context.py`、`tests/test_gateway_submission.py`，共用小夹具放在 `tests/support/gateway.py`，复用现有假适配器。既有 Service、MCP、CLI 只做相应局部接入；业务文档随任务 5 更新。不得改 `models.py` 的持久化模型、数据库模式、Vault writer 或 Web 资源。

依赖顺序：任务 1 → 任务 2 → 任务 3 → 任务 4 → 任务 5。每个任务提交前自查新增差异与对应测试；工具 input schema 在任务 5 才声明支持版本 2，中间提交不表示可上线。

## Task 1: OCR 投影与来源单元

**Files:** Create `src/douyin_wiki/gateway_sources.py`、`tests/test_gateway_context.py`。

**Interfaces:**

- Consumes: `artifacts: Mapping[str, Any]`，读取已有 metadata、transcript_raw、transcript_corrected、ocr；不读写数据库。
- Produces: 冻结 dataclass `SourceSpan(kind, container, start, end, timestamp_ms, image_index, segment_id, start_ms, end_ms)`；kind 为 `Literal["audio", "ocr", "image_ocr", "post_text"]`，container 为 str，start/end 为 int，定位字段均为 `int | None`。
- Produces: 冻结 dataclass `SourceCatalog(sources: list[dict[str, Any]], ocr: list[dict[str, Any]], refs: dict[str, SourceSpan], containers: dict[str, str])`。`sources`/`ocr` 是规格的传输结构，后两项仅供本地还原。
- Produces: `build_source_catalog(artifacts: Mapping[str, Any], *, phase: Literal["transcript_correction", "analysis"]) -> SourceCatalog`。

- [x] **Step 1: 写失败测试，固定附近字幕过滤与后半片覆盖**

在 `tests/test_gateway_context.py` 中导入 `deepcopy` 与 `build_source_catalog`，加入：

```python
def test_ocr_projection_filters_nearby_subtitles_and_keeps_late_material():
    artifacts = {
        "metadata": {"source_kind": "video"},
        "transcript_raw": [{"id": 7, "start_ms": 0, "end_ms": 1000,
                            "text": "营收增长20%"}],
        "ocr": [
            {"timestamp_ms": 1000, "text": "营收增长２０％\nAI\n营收增长20.0%"},
            {"timestamp_ms": 10000, "text": "营收增长20%"},
            {"timestamp_ms": 130000, "text": "末段表格42GB"},
        ],
    }
    before = deepcopy(artifacts)
    catalog = build_source_catalog(artifacts, phase="transcript_correction")
    assert [row["text"] for row in catalog.ocr] == [
        "AI", "营收增长20.0%", "营收增长20%", "末段表格42GB"
    ]
    assert "o:0:0" not in catalog.refs
    assert catalog.refs["o:2:0"].timestamp_ms == 130000
    assert artifacts == before
```

同一步增加以下参数化结果测试；表中的变量是对应测试创建的 catalog 或其来源行：

| 测试名 | 固定输入 | 必须断言 |
| --- | --- | --- |
| `test_ocr_dedup_uses_last_retained_time` | 同文三帧时间 0/60000/60001，无逐字稿；再测输入顺序 60000/60001/0 | `assert len(catalog.ocr) == 2`；组位置数 `[2, 1]`，第一组实际时间 `[0, 60000]`，source_ref 仍按原观测下标 |
| `test_ocr_without_timestamp_is_not_deduped_across_frames` | 两个不同观测同文、时间均 None | `assert len(catalog.ocr) == 2`；不填时间 |
| `test_image_dedup_preserves_every_image` | 同文分别在图 1 两次、图 2 一次；正文同文 | `assert len(catalog.ocr) == 2`；位置仍含图 1、2，正文和图片材料均保留 |
| `test_projection_keeps_short_labels_and_symbol_differences` | 附近原稿“增长20%/下降-3/张三”；OCR 的 `AI`、`20.0%`、`下降3`、`张四` | `assert expected_texts <= {row["text"] for row in catalog.ocr}`，expected_texts 为左列四个 OCR 字面值 |
| `test_source_units_preserve_original_characters_and_ranges` | 段 7，时间 0–5000，text 为 `"ΟΣ。" + "a" * 260` | `assert "".join(row["text"] for row in catalog.sources) == text`；音频单元长度 `[3, 256, 4]`，定位均为 0–5000 |
| `test_projection_has_no_global_count_or_size_limit` | 30 个不同 OCR 观测，总文字超过 6000，末条在 130000 ms；图文变体含 30 张图 | `assert len(catalog.ocr) == 30`；末条及图片 30 的定位存在 |

- [x] **Step 2: 运行 RED**

Run: `uv run --no-sync pytest -q tests/test_gateway_context.py`。Expected: 新模块缺失导致导入失败；已有测试不作修改来掩盖失败。

- [x] **Step 3: 实现接口与确定性材料处理**

按规格第 6/8 节实现 NFKC/空白/casefold 比较、至少 4 字的 ±5000 ms 匹配、从上次保留文字计时的 60000 ms 合并、图内去重及原位置列表。无时间戳的观测不跨帧合并；时间缺失不填造定位。原始列表不修改，OCR 长行不截短。

来源编号采用 `a:<segment_id>:<unit_index>`、`o:<observation_index>:<line_index>`、`p:<unit_index>`。音频 canonical container 按原段顺序用换行连接，OCR container 为每个原始 observation.text，正文 container 为原 post_text。SourceSpan 的半开 `[start, end)` 指向这些原文；音频/正文单元最多 256 个 Python 字符，OCR 行保持原长。纯标点可作为阅读材料，但不能支持合法引用。音频连续引用之间仅加入该 canonical container 已有分隔符，不生成新内容。

- [x] **Step 4: 验证 GREEN 与本地路径保留**

Run: `uv run --no-sync pytest -q tests/test_gateway_context.py tests/test_service.py::test_adjacent_ocr_lines_are_deduplicated_only_in_model_input`。Expected: 全部 PASS；sources 单元、位置与原始字符保真，旧 `_nearby_ocr` 行为通过。

- [x] **Step 5: 提交独立交付**

`git add -- src/douyin_wiki/gateway_sources.py tests/test_gateway_context.py`；`git commit -m "feat: add gateway source projection"`。

## Task 2: 阶段上下文、schema、版本与回执

**Files:** Create `src/douyin_wiki/gateway_protocol.py`、`tests/support/gateway.py`；Modify `src/douyin_wiki/service_analysis.py` 的 `get_analysis_context`、`tests/test_gateway_context.py`。

**Interfaces:**

- Consumes: 任务 1 的 `build_source_catalog` 与 `SourceCatalog`；已有 `JobRecord`、`AnalysisResult`、`CONTENT_TAG_RULES`。
- Produces: `validate_gateway_protocol(value: Any) -> Literal[1, 2]`；别名 `GatewayProtocol = Annotated[Literal[1, 2], BeforeValidator(validate_gateway_protocol)]`，只接受 type 为 int 的 1/2，排除 bool、浮点和字符串。
- Produces: `gateway_phase(job: JobRecord) -> str`；显式 result.phase 优先，缺失时图文/已存在校正键为 analysis，其余为 transcript_correction；未知阶段不提供紧凑分析上下文。
- Produces: `compact_correction_schema() -> dict[str, Any]`、`compact_analysis_schema() -> dict[str, Any]`、`validate_compact_analysis_shape(analysis: dict[str, Any]) -> None`。
- Produces: `context_revision(job: JobRecord) -> str`、`require_context_revision(job: JobRecord, revision: str | None) -> None`。
- Produces: `build_compact_context(job: JobRecord, *, catalog: SourceCatalog, related_claims: list[dict[str, Any]]) -> dict[str, Any]`、`compact_job_payload(job: JobRecord) -> dict[str, Any]`。
- Produces: Service `get_analysis_context(self, job_id: str, *, gateway_protocol: GatewayProtocol = 1) -> dict[str, Any]`。
- Test helper: `make_gateway_job(service: DouyinWikiService, *, phase: str = "transcript_correction", source_kind: str = "video", artifacts: dict[str, Any] | None = None) -> JobRecord`。设置 Gateway 模式，以现有 create_job/update_job 创建待处理快照；默认两段原稿为“第一段原话。”（ID 0，0–5000）和“原始第二段。”（ID 1，5000–9000），用 TranscriptSegment.model_dump 保存完整字段。analysis 视频默认复制为校正稿；图文默认无逐字稿。默认灵感“逐字保留的灵感”，gateway 为 test-agent/conversation_id 为 thread-1；artifacts 按调用方覆盖。

- [x] **Step 1: 写失败测试，验证阶段材料与读取无副作用**

```python
def test_correction_context_omits_analysis_and_does_not_lookup_prior_claims(service, monkeypatch):
    job = make_gateway_job(service)
    def forbidden_lookup(*args, **kwargs):
        raise AssertionError("校正不应检索既有知识")
    monkeypatch.setattr(service.indexer, "find_related_claims", forbidden_lookup)
    ctx = service.get_analysis_context(job.id, gateway_protocol=2)
    assert ctx["phase"] == "transcript_correction"
    assert ctx["transcript"][0]["text"] == "第一段原话。"
    assert "correction_schema" in ctx
    assert {"analysis_schema", "existing_knowledge", "transcript_raw",
            "transcript_corrected"}.isdisjoint(ctx)
    assert service.database.get_job(job.id) == job
```

同一步增加下列结果测试，引用 helper 的固定原稿与规格字段：

| 测试名 | 输入与必须断言 |
| --- | --- |
| `test_analysis_context_uses_effective_sources_once` | analysis 视频；`assert "sources" in ctx`，不含 transcript/raw/corrected/ocr 全文副本；灵感逐字，description 与正文相同时不重复；related_claims 仅四个规格字段 |
| `test_schema_keeps_business_title_and_constraints` | `schema = compact_analysis_schema()`；`assert "title" in schema["properties"]` 且 title 在 required；8 类 content_card、12 章/6 条证据/5 条收获等已有约束保持，8 个兼容字段不在 properties |
| `test_transport_shape_accepts_refs_and_explicit_evidence` | validate_compact_analysis_shape 分别验证编号与显式引文；非推断原子缺两种证据、空 refs、inference 带 refs 均拒绝；不通过序列化默认值改写原输入 |
| `test_revision_is_stable_and_invalidates_source_changes` | 相同 job 重建版本相同且 `re.fullmatch(r"[0-9a-f]{64}", revision)`；逐项修改 raw/corrected/OCR/metadata/inspiration/phase/updated_at/规则后 `assert new_revision != revision`；既有知识索引变化不影响版本 |
| `test_revision_survives_a_fresh_python_process` | 将同一 JobRecord JSON 交给新 `python -B` 进程计算；`assert child_revision == revision`；不启动真实 Service |
| `test_compact_receipt_preserves_stage_routing_and_silence` | awaiting/queued/completed 三种快照；next_tool 分别为 get_analysis_context/list_job_events/None，queued 的 revision 为 None；父任务及 batch_silent 保留；`assert "artifacts" not in receipt` |
| `test_compact_receipt_keeps_auth_and_confirmation_explanations` | waiting_confirmation 的 result 为 reason=ai_analysis_confirmation_required、duration_minutes=31.0；需要授权的任务有原 error_code/error_message/auth_scope；各解释保留，不返回原始材料或自动确认 |
| `test_legacy_context_and_non_gateway_behavior_remain` | 省略版本与显式 1 相等，含原旧 schema；provider/local 请求版本 2 拒绝；1 的原返回不受影响 |

- [x] **Step 2: 运行 RED**

Run: `uv run --no-sync pytest -q tests/test_gateway_context.py`。Expected: 新接口不存在或版本参数尚未支持，任务 1 的测试继续通过。

- [x] **Step 3: 实现纯协议处理与 Service 读取分支**

GatewayProtocol 使用 Pydantic BeforeValidator 保持 MCP 能力枚举与严格整数语义。传输模型复用现有现代字段与 ContentCard/嵌套类型，只覆写引用相关结构，不复制或修改全局持久化模型。删 schema 展示 title 时保留 properties.title；删除无引用定义前遍历 `$ref`。validate_compact_analysis_shape 验证原输入，不把默认 provenance 误当作客户端显式字段。

SHA-256 输入严格按规格第 5 节；纳入来源处理规则版本 `SOURCE_PROJECTION_VERSION = 1` 与 4/5000/60000/256 常量，未来算法变更必须更新该版本。revision 比较无写入，无检索副作用；缺失、失效分别给现有错误信封的两种 details.reason。

Service 的版本 1 保留原构造分支。版本 2 保留既有状态检查并要求 Gateway 模式，仅 analysis 执行原材料 related-claim 检索，读取最多 12 条后投影四个字段。metadata、provenance、描述、schema 与正文按规格第 4 节构建。provenance 保留后端 provider/model/backend/engine/language、confidence_status/confidence_note 和已有警告，省略路径与逐帧材料。

compact_job_payload 不访问数据库；按规格第 9 节选择结果与路由，结果仍放在 result 对象，错误保留 error_code/error_message，必要确认信息为 reason/duration_minutes。校正成功后的 awaiting 状态可读新 analysis 版本，分析提交后的 queued 状态不引导重复取上下文，completed 的 next_tool 为 None。此任务不修改 MCP/CLI 签名，不提前广告新协议。

- [x] **Step 4: 验证 GREEN**

Run: `uv run --no-sync pytest -q tests/test_gateway_context.py tests/test_content_tags.py tests/test_interfaces.py`。Expected: 新读取行为与旧接口/标签测试全部 PASS。

- [x] **Step 5: 提交独立交付**

`git add -- src/douyin_wiki/gateway_protocol.py src/douyin_wiki/service_analysis.py tests/support/gateway.py tests/test_gateway_context.py`；`git commit -m "feat: add compact gateway contexts and revisions"`。

## Task 3: 按版本提交增量校正

**Files:** Modify `src/douyin_wiki/service_analysis.py` 的 `submit_transcript_correction`；Create `tests/test_gateway_submission.py`。

**Interfaces:**

- Consumes: 任务 2 的 GatewayProtocol、require_context_revision、context_revision；现有 TranscriptCorrection 与数据库 expected_updated_at。
- Produces: Service `submit_transcript_correction(self, job_id: str, corrections: list[TranscriptCorrection], *, producer: str, model: str = "agent", review_issues: list[ReviewIssue] | None = None, gateway_protocol: GatewayProtocol = 1, context_revision: str | None = None) -> JobRecord`。
- 保留完整 transcript_corrected、原始 transcript_raw、真实 transcript_edits 和 correction_producer/model；不改变数据库更新或事件公开签名。

- [x] **Step 1: 写失败测试，固定省略片段保真**

```python
def test_patch_preserves_unsubmitted_segments_and_raw_artifacts(service):
    job = make_gateway_job(service)
    raw = deepcopy(job.artifacts["transcript_raw"])
    updated = service.submit_transcript_correction(
        job.id, [TranscriptCorrection(id=1, text="校正后的第二段。")],
        producer="test-agent", gateway_protocol=2,
        context_revision=context_revision(job),
    )
    assert updated.artifacts["transcript_raw"] == raw
    assert updated.artifacts["transcript_corrected"][0] == raw[0]
    assert updated.artifacts["transcript_corrected"][1]["text"] == "校正后的第二段。"
    assert [row["id"] for row in updated.artifacts["transcript_edits"]] == [1]
    assert updated.result["phase"] == "analysis"
    assert updated.status == JobStatus.AWAITING_AGENT_ANALYSIS
```

同一步加入：

| 测试名 | 输入与必须断言 |
| --- | --- |
| `test_empty_patch_completes_correction_and_legacy_empty_is_rejected` | 相同非空原稿的两个任务，2 空补丁生成等于原稿的完整校正稿、edits=[]、phase=analysis；1 空列表拒绝且 job 未变 |
| `test_patch_rejects_invalid_ids_and_blank_text_without_writes` | ID 999、两次 ID 1、空白文本、原稿重复 ID；`assert database.get_job(job.id) == before`。空白通过模型/工具合法验证路径拒绝，不绕开 TranscriptCorrection |
| `test_patch_requires_current_revision` | None/空白/旧版本；检查 required/mismatch 两种 reason 与 job 未变 |
| `test_patch_compare_and_set_rejects_a_racing_update` | 版本校验后、数据库 update_job 接收 expected_updated_at 时，先用原绑定方法写一次并发 result 更新；提交拒绝，`assert "transcript_corrected" not in current.artifacts`；并发方自身更新保留 |
| `test_patch_preserves_all_original_locator_and_confidence_fields` | 全部 ID 替换；id/start_ms/end_ms/ASR 分数字段逐项等于原稿；不变文本不记 edits |
| `test_patch_replay_and_image_note_correction_are_rejected` | 成功后同请求重放、图文调用；拒绝，不产生第二次校正/新复核 |
| `test_legacy_full_correction_and_notes_remain_compatible` | 旧全量输入、旧 review_issues；完整结果兼容且仍不阻塞新任务 |

- [x] **Step 2: 运行 RED**

Run: `uv run --no-sync pytest -q tests/test_gateway_submission.py`。Expected: Service 不接收新提交参数或仍以全量规则拒绝补丁。

- [x] **Step 3: 接入补丁与既有原子更新**

新参数只增加在现有 keyword-only 区域。版本 1 保留全覆盖规则；版本 2 检查 Gateway 模式、现有阶段、原稿 ID 唯一与当前版本，再按 ID 构建完整校正稿。缺项用原段，明确提交的项沿用 strip/非空校验。保留现有 notes、edits、producer/model、expected_updated_at、expected_statuses、unlock 与事件发送。任何验证失败均发生在数据库更新之前。

- [x] **Step 4: 验证 GREEN**

Run: `uv run --no-sync pytest -q tests/test_gateway_submission.py tests/test_interfaces.py tests/test_auto_correction.py tests/test_correction_recovery.py`。Expected: 补丁、并发与旧提交全部 PASS；provider 校正恢复不变。

- [x] **Step 5: 提交独立交付**

`git add -- src/douyin_wiki/service_analysis.py tests/test_gateway_submission.py`；`git commit -m "feat: accept revision-bound gateway correction patches"`。

## Task 4: 引用展开与标准分析提交

**Files:** Modify `src/douyin_wiki/gateway_sources.py`、`src/douyin_wiki/service_analysis.py` 的 `submit_gateway_analysis`、`tests/test_gateway_submission.py`。

**Interfaces:**

- Consumes: SourceCatalog 的 refs/containers 与任务 2 的 validate_compact_analysis_shape/require_context_revision；现有 AnalysisResult、标签限制和 validate_analysis_evidence。
- Produces: `expand_source_references(analysis: dict[str, Any], catalog: SourceCatalog, *, source_kind: Literal["video", "image_note"]) -> dict[str, Any]`；返回新字典，不改输入或 artifacts，移除所有传输 source_refs。
- Produces: Service `submit_gateway_analysis(self, job_id: str, analysis: dict[str, Any], *, producer: str, model: str = "agent", gateway_protocol: GatewayProtocol = 1, context_revision: str | None = None) -> JobRecord`。

- [x] **Step 1: 写失败测试，固定原文展开与输入不变**

```python
def test_refs_expand_to_exact_original_quote_without_mutating_input(service):
    job = make_gateway_job(service, phase="analysis")
    catalog = build_source_catalog(job.artifacts, phase="analysis")
    incoming = {"title": "资料", "knowledge_atoms": [
        {"id": "atom-1", "statement": "第二段内容", "source_refs": ["a:1:0"]}
    ]}
    before = deepcopy(incoming)
    expanded = expand_source_references(incoming, catalog, source_kind="video")
    atom = expanded["knowledge_atoms"][0]
    assert atom["quote"] == "原始第二段。"
    assert atom["timestamp_ms"] == 5000
    assert atom["provenance"] == "audio"
    assert "source_refs" not in atom
    assert incoming == before
```

同一步加入以下 Service 与纯展开结果测试：

| 测试名 | 输入与必须断言 |
| --- | --- |
| `test_ref_and_explicit_submission_produce_equivalent_analysis` | 两个相同 analysis 快照，分别以 refs 与同一原文/定位提交；`assert ref_job.artifacts["analysis"] == explicit_job.artifacts["analysis"]`，含派生 claims |
| `test_ocr_ref_derives_provenance_without_audio_default` | 视频 OCR 与图文 image_ocr 的 refs 输入均省略 provenance；结果分别为 ocr/image_ocr，定位取实际来源；显式错误 audio/image_index/timestamp 拒绝 |
| `test_cross_segment_and_unicode_refs_preserve_complete_original_ranges` | 音频含 ΟΣ、纯标点空段、Α、Straße/oﬃce；只允许连续原文范围，保留字符，不能从 casefold 后的内部偏移取“Stras”伪引文；源 quote 与定位经既有校验通过 |
| `test_refs_reject_unknown_hidden_noncontiguous_and_duplicate_sources` | 未知 ID、被过滤的 o:0:0、跳过非空原文、重复 refs；检查 invalid_source_ref/路径，`assert database.get_job(job.id) == before` |
| `test_refs_cannot_cross_images_containers_or_revisions` | OCR 两图或两 observation 合并编号；音频+OCR 混合 refs；另一任务/旧快照 revision；均拒绝，旧显式混合引文继续通过 |
| `test_refs_and_inline_fields_must_agree` | 正确 refs + 错 quote/来源/定位拒绝；一致字段允许；无效 refs 不以正确手写 quote 兜底 |
| `test_inference_and_reminder_sources_keep_their_semantics` | 推断无 refs、不生成事实证据；图文正文 p refs 与音频 a refs 可还原 reminder.source_quote；系统提醒适配器创建列表仍为空 |
| `test_compact_facts_require_refs_or_located_quote` | 2 的事实无引文或视频正文 fact refs 拒绝；1 保持旧模型兼容行为；图文 chapters 拒绝 |
| `test_analysis_revision_and_compare_and_set_prevent_stale_requeue` | 缺失/过期版本、校验后更新/取消；本提交不写 analysis，不覆写并发修改，不把取消任务入队 |

- [x] **Step 2: 运行 RED**

Run: `uv run --no-sync pytest -q tests/test_gateway_submission.py`。Expected: 展开函数缺失或分析提交不支持版本/引用；任务 3 的校正测试继续通过。

- [x] **Step 3: 实现引用与分析提交分支**

在来源模块验证 refs 有序、唯一、同类、同容器、连续且为当前 catalog 暴露的原文。跨音频段取 canonical container 的连续范围；仅空白/标点间隔不代表跳过正文。图文位置来自原图；无定位的 OCR 不成为章节或知识原子证据。混合来源走现有显式引文路径。

展开 chapters evidence、knowledge_atoms 与 reminders；来源/quote/定位冲突拒绝。只比较调用方实际提供的字段，省略字段由编号推导；不要先用持久化模型的默认 audio 污染输入。纯标点引文拒绝，不用 NFKC 后文字做截取。

Service 的 2 分支依次执行 mode/阶段/版本、紧凑形状、catalog/展开、标准模型、标签、原始 artifacts 证据校验、矛盾归一化与 expected_updated_at 入队。1 保留现有分支，禁止为 1 新增无引文事实限制。无效输入不清理原分析、不新建任务、不写引用表。

- [x] **Step 4: 验证 GREEN 与既有证据回归**

Run: `uv run --no-sync pytest -q tests/test_gateway_submission.py tests/test_analysis_v2.py tests/test_evidence_quote_boundaries.py tests/test_unicode_evidence.py tests/test_content_tags.py`。Expected: 引用、标准字段等价、旧证据边界与标签限制全部 PASS。

- [x] **Step 5: 提交独立交付**

`git add -- src/douyin_wiki/gateway_sources.py src/douyin_wiki/service_analysis.py tests/test_gateway_submission.py`；`git commit -m "feat: resolve gateway evidence references before validation"`。

## Task 5: MCP/CLI 闭环、文档与收益验收

**Files:** Modify `src/douyin_wiki/mcp_server.py`、`src/douyin_wiki/cli.py`、`tests/support/gateway.py`、`tests/test_gateway_submission.py`；修改规格第 10 节列出的业务/架构/接入文档、README/CHANGELOG，更新本计划实施记录。收益复现材料保存在仓库外。

**Interfaces:**

- Consumes: 任务 2–4 的 GatewayProtocol、上下文、补丁、引用与 compact_job_payload；现有 mcp._payload、CLI _print、Worker 和事件 API。
- Produces: MCP 原四个入口的 gateway_protocol 默认 1，以及两个提交入口的 context_revision 默认 None；CLI 原三个 gateway 命令的 `--gateway-protocol`，两个提交命令的 `--context-revision`。
- Test helper: `make_video_gateway_service(tmp_path: Path) -> DouyinWikiService`，复用 tests.conftest 的假适配器与 hash Embedding；图文复用 tests.test_image_note.make_service。
- Test helper: `async run_gateway_roundtrip(service: DouyinWikiService, *, gateway_protocol: Literal[1, 2]) -> tuple[dict[str, Any], list[dict[str, Any]]]`，顺序执行 fake Worker 与六次 MCP 上下文/提交调用，返回最终标准分析和 exchanges。每次 exchange 固定为 `{tool, request, response_text, response_structured}`；不触发外部模型，不并行切换全局 _SERVICE。

- [x] **Step 1: 写失败测试，验证工具级真实回执与旧默认**

本机核对的 `mcp.call_tool` 返回 CallToolResult，`.structured_content` 为字典、`.content[0].text` 为其 JSON 文本。通过真实 call_tool 验证，不能只直调包装函数：

```python
@pytest.mark.asyncio
async def test_mcp_compact_correction_ack_does_not_repeat_artifacts(service, monkeypatch):
    job = make_gateway_job(service)
    monkeypatch.setattr(mcp_server, "_SERVICE", service)
    context = await mcp.call_tool("get_analysis_context", {
        "job_id": job.id, "gateway_protocol": 2
    })
    result = await mcp.call_tool("submit_transcript_correction", {
        "job_id": job.id, "corrections": [], "producer": "test-agent",
        "gateway_protocol": 2,
        "context_revision": context.structured_content["context_revision"],
    })
    ack = result.structured_content
    assert ack["phase"] == "analysis"
    assert ack["next_tool"] == "get_analysis_context"
    assert {"artifacts", "request", "analysis_schema", "ocr"}.isdisjoint(ack)
    assert json.loads(result.content[0].text) == ack
```

同一步加入：

| 测试名 | 输入与必须断言 |
| --- | --- |
| `test_mcp_advertises_optional_protocol_and_rejects_nonintegers` | 四工具 input schema 枚举 1/2、默认 1，不新增 required 旧参数；True/2.0/字符串/3 拒绝且不调用 Service |
| `test_mcp_legacy_default_keeps_full_payload_and_stub_calls` | 省略参数含 artifacts，旧两段全量校正有效；只接受旧 kwargs 的 stub 仍调用成功；结构化错误仍是 invalid_job_state 信封 |
| `test_mcp_video_note_and_reanalysis_complete_with_references` | fake capture → pause → 两步/图文一步 → fake Worker 完成；phase 顺序、source image_index 与落库 quote 正确；重新分析不再校正/下载/OCR |
| `test_mcp_replay_checks_phase_and_keeps_batch_silent` | 丢校正回执后 get_job 发现 analysis，同状态但版本不同；旧补丁拒绝；queued 的 next_tool 为 list_job_events；两个批量 context 的 parent_job_id/batch_silent 保留，不自动确认事件 |
| `test_cli_protocol_options_preserve_old_json_formats` | CliRunner 与 fake _service：旧 JSON 列表/字典和三个旧命令有效；2 用显式选项传版本/快照，analysis 文件仍是纯分析字典，输出简短回执 |
| `test_compact_roundtrip_reduces_repeated_material_without_changing_analysis` | 两个隔离服务使用相同 30 段原稿（每段 3000 ms）、120 个重复 OCR 帧（0–59500 ms）及 70000 ms 的末段图表，只改一段及相同事实；`assert compact_analysis == legacy_analysis`，六次业务交换请求+所选响应表示的字节总量严格下降；编号/定位/参数开销计入 |

- [x] **Step 2: 运行 RED**

Run: `uv run --no-sync pytest -q tests/test_gateway_submission.py -k 'mcp or cli or roundtrip'`。Expected: 工具/CLI 尚不接收版本 2 参数；此前 Service 与来源测试继续通过。

- [x] **Step 3: 接入四工具与三个 CLI 命令**

默认分支保留旧 Service 调用的 kwargs 与完整返回；只有 2 分支添加新参数并使用 compact_job_payload。MCP 经现有 _payload 保留中文标签；CLI 经现有 _print 展示。参数采用 GatewayProtocol，CLI 整数也调用严格验证。context_revision 由显式 CLI 选项传入，不改变旧 JSON 文件的解释，不从旧 JSON 静默切换协议。结构化错误经当前 DouyinWikiMCP 处理。

更新 INSTRUCTIONS 与工具说明，使全量/增量条件及两阶段 schema 明确；规则正文继续以 Gateway 指南为权威，不维护特定客户端的常驻脚本或修改其配置。此时才在四工具 schema 中广告枚举 1/2。

- [x] **Step 4: 验证适配层 GREEN**

Run: `uv run --no-sync pytest -q tests/test_gateway_context.py tests/test_gateway_submission.py tests/test_interfaces.py tests/test_image_note.py tests/test_favorites_interfaces.py tests/test_creator.py`。Expected: 两版本闭环、CLI、图文和批量原流程全部 PASS。失败时只按具体失败扩大相关检查。

- [x] **Step 5: 更新业务文档与隔离验收说明**

Gateway 指南补能力检测、版本 2 的实际工具参数/示例、空补丁、refs 与版本错误、回执丢失及静默处理；旧流程继续可查。接入契约记录实际协议支持与所选版本、说明配置/验收在仓库外，不增设假权限字段。架构、docs 索引、README 与 CHANGELOG 说明实际实现和兼容行为；规格与计划标明交付状态。不要将未核验的真实客户端写成已启用。

- [x] **Step 6: 保存可复现的应用负载测量**

在仓库外维护目录保存 run_gateway_roundtrip 的合成输入、两版本 exchanges、运行环境与基准提交，以及 JSON/Markdown 汇总。逐次记录 request 字符/UTF-8 字节、content 文本字节、structured 表示字节、schema 字节、OCR 行数、修改段数、来源覆盖和实际重试；记录四工具定义/服务 instructions 增量。

MCP 文本与 structured 是同一结果的两种表示，分别报告，不假定客户端把两者都送给模型。无 Gateway usage 时标明 token_usage 未获取，不写 0 或承诺节省比例。真实账号、客户端切换、服务重启、发布/发行包替换均留给另行授权的接入/交付工作。

- [x] **Step 7: 完成必要回归与交付核对**

Run: `uv run --no-sync ruff check src tests`；`uv run --no-sync pytest -q -m 'not live'`；`git diff --check`。Expected: ruff 无错误、完整离线集 PASS、无空白错误。验证环境缺依赖时先在独立环境补齐，不修改现用服务环境；不得用跳过失败用例获得通过。

逐条核对规格第 12 节完成判据：原始材料、标准分析、版本/原子更新、回执、两版视频/图文/重新分析、完整往返下降；复核新增 API 默认值、source_refs 不入 Vault、原 ASR/OCR/provider/Web 未被改动。测试已通过后只在有新改动或未解疑点时重跑。

- [x] **Step 8: 提交并报告仓库交付结果**

逐路径 stage 本任务生产/测试/文档文件，核对暂存差异后 `git commit -m "feat: expose compact gateway protocol with verified compatibility"`。报告实现提交、必要测试结果、应用负载测量和真实客户端状态；默认不部署、不发布、不创建新 chat，也不触发生产任务。若独立 worktree 留有交付物则保留，确认无需保留后才按原生工具归档。

## 计划自审与执行节点

本计划覆盖规格第 1–12 节：任务 1 对应第 6/8 节材料与编号，任务 2 对应第 3–5/9 节，任务 3 对应第 7 节，任务 4 对应第 8 节提交与第 5 节并发，任务 5 对应公开接入、第 10–12 节及完整闭环。R1–R5 已分别落到具名测试。各后续任务使用上游 Interfaces 中相同的函数名、参数与类型。

用户已确认本计划，当前 Agent 按 executing-plans 在独立 worktree 中执行；未委派子 Agent。实际实施与验收记录如下；若继续修改范围，先调整对应规格与验收。

## 实施与隔离验收（2026-10-03）

执行起点 `70717b9`，worktree 为 `/Users/weisengao/.codex/worktrees/gateway-token-optimization/douyin-wiki`。现用 MCP/Worker 引用主仓库 `.venv/bin/python`；验证使用新 worktree 的独立环境，按 uv.lock 安装 dev 与 embeddings 可选依赖，未同步现用环境、改生产 Vault/Profile 或切换客户端。

| 任务 | 实施提交 | 必要验证 |
| --- | --- | --- |
| 来源投影 | `e54ccfc` | 11 passed；附近字幕、60 秒边界、乱序/无时间、逐图与全片材料保真 |
| 阶段上下文/版本/回执 | `93f88fa` | 75 passed；现代字段、schema 约束、稳定版本与旧读取兼容 |
| 增量校正 | `e346a03` | 42 passed；缺项原样、空补丁、非法 ID、版本和并发保护 |
| 引用展开与提交 | `c979d00` | 169 passed；真实原文、Unicode/跨段、OCR 来源、冲突与旧证据回归 |
| MCP/CLI 接入 | 本记录所在实现提交 | 124 passed；两版视频/图文/重新分析、CLI 格式、静默与完整往返 |

任务 4 同时调整 gateway_protocol 的引用格式错误信封，使空/非字符串/非列表编号也返回 `invalid_job_state` 与 `invalid_source_ref`，不携带原始输入。任务 5 保留实际 creator_context.id 路由字段，并将“六次工具交换”明确为四次上下文/提交加首尾两次状态查询。Skill helper 原文件没有执行权限，使用本 worktree 下内容相同的临时可执行副本；不修改安装的 Skill。整体审查由当前 Agent 按 requesting-code-review 清单完成，沿用用户不委派约定。

应用负载测量与复现脚本、合成输入、原始 exchanges、标准分析及环境记录放在[仓库外验收目录](/Users/weisengao/.codex/maintenance/douyin-wiki/gateway-token-optimization-2026-10-03/README.md)。同一 90 秒视频包含 30 段原稿、120 个重复 OCR 帧及 70 秒独有图表，只改一段；每版六次调用、两次状态查询、无重试。请求+MCP 文本响应由 766,982 B 降为 293,241 B（61.77%），两版标准分析一致；30/30 段原话和 241/241 个 OCR 位置保留，投影为 3 行。四工具定义增加 639 B，服务 instructions 增加 380 B，单独记录其开销。文本与 structured 是同一结果的两种表示，分别报告，不相加；本合成输入含大量短句，来源逐句定位使单次分析上下文文本从 148,092 B 增至 234,449 B；因此只以完整往返衡量总收益，不宣称每次读取都缩小。未获得实际计费 token usage，不能将应用字节比例当成计费节省。

整体离线回归：857 passed / 1 live deselected；`uv run --no-sync ruff check src tests` 和差异空白检查通过。最终自审：待完成。真实客户端协议 2 启用状态：未核验。交付物保留在实现分支，主线合并、客户端切换、服务重启和发行包替换不在本轮执行范围内。
