# Gateway token 优化设计规格

日期：2026-10-03。状态：架构设计、本书面规格和[实施计划](../plans/2026-10-03-gateway-token-optimization.md)均已获用户确认，已在 `codex/gateway-token-optimization` 实现并通过隔离离线验收与自审修复；交付记录见实施计划。主线集成与真实客户端切换尚未执行。

本规格基于工作区对应提交 `400cf269f0cd375547833a074acfc1107c2c82eb` 核对。生产服务是否已加载该版本未核验。实现后的业务流程仍以 [Gateway Agent 接入指南](../../gateway-agents.md) 为唯一共用入口，本文件记录开发设计，不代替现行调用契约。

## 1. 目标与范围

降低 `analysis_mode = "gateway"` 下逐字稿校正、内容分析及工具往返的重复文本量，保留可核验的原话、画面文字、时间轴和图文图片定位。用户确认的范围包含 OCR 去重与重复字幕过滤、增量校正提交、证据引用、按阶段上下文和简短回执。

保留“视频校正 → 内容分析 → Worker 入库”。图文与重新分析继续直接进入 `analysis`。本轮不合并模型调用，不引入独立复核模型，不增加人工校对，不更换 ASR/OCR/Embedding 后端，不调整抽帧间隔。

项目约束：

- Python 支持范围保持 `>=3.12,<3.14`，不新增依赖或新的持久化表。
- MCP 工具名称、既有参数语义、CLI 命令与别名、Service 默认行为、状态码、Web 接口与 Vault 格式保持兼容。
- 旧协议默认保留；新客户端显式选择紧凑协议，不全局切换默认值。
- 原始 OCR、ASR、校正稿、修改记录及最终标准分析结果保留。过滤和引用均为传输层处理，证据校验读取原始任务材料。
- `provider` 与 `local` 模型路径保持现有流程；本地 `_nearby_ocr` 不在本轮调整。
- 灵感逐字保留；标签沿用当前共享规则，通常两个、最多三个，不提供已有标签清单。
- 长视频确认、提醒确认、批量静默、事件归属与成功后确认规则保持。上下文版本不提供身份鉴权或任务排他领取。
- 实际客户端配置、运行状态和验收材料遵循 [接入契约](../../agents/integration-contract.md)，放在仓库外；本规格不授权修改生产 Vault、Profile、现用环境或真实客户端配置。

## 2. 当前消耗与所选方案

当前 [上下文构造](../../../src/douyin_wiki/service_analysis.py) 返回原始 OCR、原稿和校正稿，并在两个阶段均提供既有知识和完整分析 schema。校正提交要求覆盖全部原稿 ID。[MCP 包装层](../../../src/douyin_wiki/mcp_server.py) 的 `get_job` 与两个提交工具序列化整个 `JobRecord`，其 `artifacts` 会再次携带 OCR、逐字稿和已提交分析。

本次核对时，`AnalysisResult.model_json_schema()` 使用 `ensure_ascii=False`、`separators=(",", ":")` 序列化后为 11,600 个字符。此数字不是 token 数。当前基准已移除 `existing_tags`，不重复实施该项。

采用现有工具上的兼容扩展。另建工具会增加接入与工具描述成本；合并校正、分析会绑定两步的失败重试，并改变引用所用的校正稿生成顺序，两者不纳入第一版。

## 3. 协议版本与公开入口

新增可选参数 `gateway_protocol: Literal[1, 2] = 1`。版本 1 保持现行行为，版本 2 为本规格的紧凑协议。只接受整数 1、2。该参数独立于分析结构中的 `analysis_version = 2`。

| 入口 | 新增参数与行为 |
| --- | --- |
| MCP `get_analysis_context` / Service 同名方法 | 增加 `gateway_protocol`；返回对应版本上下文 |
| MCP `submit_transcript_correction` / Service 同名方法 | 增加 `gateway_protocol`、`context_revision: str \| None = None`；版本 2 将 `corrections` 解释为片段替换补丁，版本 1 继续要求全部 ID |
| MCP `submit_gateway_analysis` / Service 同名方法 | 增加 `gateway_protocol`、`context_revision: str \| None = None`；版本 2 先展开证据引用，再验证标准分析 |
| MCP `get_job` | 增加 `gateway_protocol`；版本 2 返回简短状态投影 |

Service 的 `get_job` 和两个提交方法继续返回 `JobRecord`；简短状态及回执由适配层投影，不改变 Service 的既有返回类型。Service 的版本 2 校正、分析上下文与提交只用于 Gateway 模式。

CLI `gateway context`、`gateway submit-correction`、`gateway submit-analysis` 增加同名 `--gateway-protocol` 选项，默认 1；两个提交命令增加 `--context-revision`。旧位置参数、旧 JSON 文件和别名保持；紧凑 CLI 回执与 MCP 使用同一投影。

两个版本使用当前 `corrections` 列表结构和 `analysis` 字典参数。版本 2 提交必须携带非空 `context_revision`，版本 1 不要求新参数。`capture_douyin`、`list_jobs`、事件、Web 和普通查询接口不改形状；紧凑客户端用事件与紧凑 `get_job` 跟踪任务，不用完整任务列表反复轮询正文。

客户端从工具 input schema 的 `gateway_protocol` 参数及其枚举检测支持能力。旧服务没有该参数时走完整旧流程，不将补丁重放成旧协议的全量校正。不用实际业务提交试探协议支持。

## 4. 按阶段提供上下文

版本 2 的共同字段为 `gateway_protocol`、`job_id`、`status`、`phase`、`context_revision`、`gateway_context`、`metadata`、`media_provenance`、`inspirations_verbatim` 和 `rules`。保留状态、阶段等现有中文显示标签；程序仍使用英文状态码。

`metadata` 只保留已有的 `video_id`、`source_kind`、`title`、`author`、`published_at`、`duration_seconds`、`original_url`、`canonical_url`，以及由原始图片列表计算的 `image_count`。不发送媒体与图片的本机路径。作品正文以现有 `metadata.post_text` 为准，只在正文来源中出现一次；校正上下文为 `post_text`，分析上下文为 `sources` 中的正文单元。若 `description` 非空且不同于正文，则作为背景元数据保留，不分配可引用的正文编号，不将它改写成原始 `post_text`。

`media_provenance` 保留后端身份、ASR 分数可用性及其非阻塞说明、已有 OCR/ASR 警告；运行路径与逐帧冗余信息不进入紧凑上下文。无分数、低分或 OCR 缺失不新增复核门槛。

| 阶段 | 阶段专有材料 |
| --- | --- |
| `transcript_correction` | `transcript`：完整原稿，每段只带 `id/start_ms/end_ms/text`；`ocr`：第 6 节投影；`post_text`；`correction_schema`：补丁提交结构。无既有知识与分析 schema |
| 视频 `analysis` | `sources`：校正稿、OCR 和正文的可引用单元；`existing_knowledge`；紧凑 `analysis_schema`。校正稿为空时使用现有原稿回退，无第二份逐字稿 |
| 图文 `analysis` | `sources`：正文和逐图 OCR；`existing_knowledge`；紧凑 `analysis_schema`。无伪造的视频时间轴 |

来源单元自身就是分析输入正文，不再附带重复的全文或独立引文目录。不能按 6000 字、20 条或全片时间窗截断材料。超长输入仍返回完整投影；客户端应报告自身上下文容量限制，不通过截尾、抽样或遗漏图片完成分析。分页与分块协议留在后续独立设计中。

既有知识只在分析阶段检索，保持当前最多 12 条、排序与阈值；检索查询材料沿用原始材料，避免本轮改变检索召回。返回只带 `entry_id/claim_id/claim_text/entry_title`，其他定位与展示信息由服务端现有矛盾归一化补齐。既有知识仅供关联与矛盾判断，不成为本作品原话证据。

紧凑分析 schema 保留所有现代分析字段、判别联合、枚举、必需字段和数值/长度约束，不发送旧兼容字段 `summary/core_points/evidence/steps/applicable_scenarios/risks/claims/ai_judgment`。只删除冗余 schema 展示 `title` 和不再引用的定义，不删除业务约束。标准 `AnalysisResult` 与其兼容字段生成逻辑保持；提交进入模型验证前展开传输字段。

版本 2 的校正规则明确“完整检查原稿，只提交有改动片段”；分析规则明确来源编号、原话/正文/OCR/推断区分和图文定位。公共标签规则复用 `CONTENT_TAG_RULES`，不另维护一份正文。

## 5. 上下文版本与并发

`context_revision` 为 64 位小写十六进制 SHA-256 字符串，由协议版本、阶段、`job_id`、`updated_at`、原始/校正逐字稿、原始 OCR、作品元数据、用户灵感和协议规则/schema 版本生成。规则/schema 版本是共享标签规则、各阶段规则和紧凑 schema 的内容 SHA-256。使用 UTF-8 与确定性 JSON 序列化：`ensure_ascii=False`、`sort_keys=True`、`separators=(",", ":")`；不得使用进程随机 hash 或当前读取时间。

同一任务快照多次读取、进程重启后重建得到相同版本与引用编号。材料、阶段、任务更新或规则/schema 变化会产生新版本。相关知识的检索结果不进入本作品材料版本，其他资料入库不使本任务的来源编号失效。

提交先读取任务并比较当前材料版本，再应用补丁或展开引用，最后使用这次读取的 `job.updated_at` 执行现有数据库原子更新。版本校验与数据库 `expected_updated_at` 均需保留，避免比较后发生并发变更。原始任务记录不为读取上下文而写入新 artifacts 或更新时间。

使用现有 `invalid_job_state` 错误信封；版本缺失、过期、无效引用分别通过 `details.reason` 标记 `context_revision_required`、`context_revision_mismatch`、`invalid_source_ref`。详情包含必要的字段路径、编号、当前阶段/版本，不返回完整材料。非法状态沿用现有错误，不自动重试业务提交。

成功提交推进阶段后，重放旧请求按现行状态约束拒绝。网络丢失回执时，客户端先读紧凑状态，不能据旧补丁重复推进任务；第一版不新增提交幂等表。

## 6. OCR 投影

规则键为 `NFKC → 删除 Unicode 空白 → casefold`，保留标点、负号、小数点、百分号、单位和其他符号。此规则只用于比较；展示文字与还原引文取原始 OCR 字符，证据校验继续使用现有原文匹配器。

视频处理：

1. 使用原始 observation 下标和行下标标识来源。在临时视图中按有效时间戳稳定排序；同一时间保留原始顺序，原始 artifacts 不排序或修改。
2. 长度至少 4 个字符的规则键，仅在该帧 `timestamp_ms` 前后 5000 ms 对应的逐字稿中进行精确子串匹配；窗口条件为 `segment.start_ms - 5000 <= timestamp_ms <= segment.end_ms + 5000`。连续附近片段按原始顺序拼接。不做模糊、同音、语义或去标点匹配。
3. 校正阶段与原稿匹配，分析阶段与有效校正稿匹配。匹配的字幕行不进入模型投影；没有附近逐字稿、无时间戳、少于 4 字或数字/符号不同的行保留。
4. 对剩余相同行，与上次保留文字的时间相隔不超过 60,000 ms 时合并，保留最早文字和每次实际出现位置；被合并的行不重置 60 秒计时。超过该范围建立新的文字组。
5. 无时间戳的观测不参加跨帧去重，不推算时间。同一 observation 中有重复行时，文字可共用，但每个原始行位置仍可追溯。

图文以 `image_index` 为去重边界。相同行只在同一图片内合并，保留全部实际位置；不跨图片删除相同文字，不与正文对删。缺少图片编号的 OCR 保留文字，不补造编号，仍不能用作需要图片定位的合法引用。

投影每项为 `{text, locations}`，位置包含原始观测/行对应的 `source_ref` 与实际 `timestamp_ms` 或 `image_index`。空白行不作为证据单元；非空材料不设数量或字符截断上限。OCR 全部被过滤或 OCR 后端失败时，沿用现有无 OCR 流程。

## 7. 增量校正

版本 2 的 `corrections` 是按原稿片段 ID 的完整片段替换，不是字符编辑指令。只提交改动片段；`corrections=[]` 表示已检查且无需修改，仍记录校正来源、生成完整校正稿并进入 `analysis`。

程序从原稿逐段复制，未提交的片段保持所有原始字段与文本；提交项只替换该段 `text`，不改变 ID、时间或 ASR 分数。文本校验及首尾空白处理沿用当前 `TranscriptCorrection` 和 Service 规则，继续拒绝纯空白、未知或重复 ID。版本 2 原稿 ID 重复时拒绝补丁，不依据列表位置猜测。

`transcript_raw` 保持；`transcript_corrected` 是完整结果；`transcript_edits` 只记录实际文本变化。继续记录 producer/model，保留旧 `review_issues` 参数的说明兼容行为，不由补丁新增人工复核。

版本 1 对非空原稿的 `corrections=[]` 或缺段继续拒绝。图文与已进入分析阶段的视频继续拒绝校正提交。新旧模式共用既有数据库状态检查、并发比较和后续事件发送。

示例仅说明结构，ID 与版本必须来自刚读取的任务：

```json
{"job_id":"example-job","gateway_protocol":2,"context_revision":"0000000000000000000000000000000000000000000000000000000000000000","corrections":[{"id":7,"text":"完整替换后的第七段原话"}],"producer":"example-agent"}
```

## 8. 来源单元与证据还原

分析输入中的每个来源项为 `{kind, text, locations}`。`kind` 使用现有单一来源名 `audio/ocr/image_ocr/post_text`；`locations` 每项含 `source_ref` 及该位置原本可用的定位信息。音频位置还包含 `segment_id/start_ms/end_ms`；OCR 与图文使用真实帧时间或图片编号。合并 OCR 行的文本只发送一次，但各位置有独立编号。

编号在当前任务快照内确定：音频 `a:<segment_id>:<unit_index>`；OCR `o:<observation_index>:<line_index>`；正文 `p:<unit_index>`。observation、line、unit 下标均从 0 开始，图片编号沿用从 1 开始的现有值。编号只在对应 `job_id/context_revision` 内有效，服务器根据任务材料重新建立映射，不接受客户端提供的映射。

音频按句末 `。！？!?；;` 与换行切分；正文按段落切分。过长单元再按最多 256 个 Python Unicode 字符切成连续单元，完整覆盖原始文本；标点和空白保留在原始范围中。OCR 以原始行为单元，不拆列或重新排列。空白/纯标点不构成合法引文。音频句子沿用所属 ASR 段的时间范围，不根据字符比例制造更精确时间。

引用格式与还原位置：

| 分析位置 | 紧凑提交 | 本地还原 |
| --- | --- | --- |
| `chapters[].evidence[]` | `source_refs: list[str]` | 原始 `quote`、定位与 `evidence_type` |
| `knowledge_atoms[]` | `source_refs: list[str]`，其余知识字段保持 | 原始 `quote`、`timestamp_ms/image_index`、`provenance` |
| `reminders[]` | `source_refs: list[str]`，其余提醒字段保持 | 原始 `source_quote`，提醒时间仍由既有字段表示 |

每组 `source_refs` 非空、无重复，按原文顺序引用同类、连续的来源范围。音频可跨相邻逐字稿段；OCR 可跨同一 observation 的连续原始行；正文可跨连续段落。不得跳过中间正文拼成一句。投影归一化不参与取引文，服务器取原始字符范围及其真实分隔符。

定位使用第一单元的原始音频段起点、OCR 实际帧时间或图片编号，跨段规则继续交给现有证据校验。视频 OCR 缺少时间戳不能支持章节/知识原子引用，图文缺少图片编号不能支持图片证据。视频正文保持当前来源限制：用于理解与提醒，不通过引用获得当前模型不允许的章节/知识原子 `post_text` 来源。

第一版引用列表限于同类连续原文。需要 `audio+ocr` 或 `post_text+image_ocr` 的混合引用，继续使用现有显式引文格式；章节也可分别提供音频、OCR 证据。紧凑 schema 同时描述编号引用与现有显式引文两种形式。版本 2 的非推断知识原子必须提供合法编号，或非空显式引文及对应定位；不依据旧模型中可空的 `quote` 放行无引文事实。不能因不支持混合编号而丢弃证据或改写为推断。

若同一项同时提供编号与引文、来源或定位，必须与展开结果一致，否则拒绝；不能用手写引文兜底一个无效编号。`ai_inference` 不使用来源编号，继续与 `atom_type = "inference"` 一致。单元能定位不代表程序已验证其支持模型的论断，来源语义判断仍由 Agent 负责。

完整链路为：检查阶段与版本 → 验证引用范围 → 原文展开 → `AnalysisResult.model_validate` → 标签限制 → `validate_analysis_evidence(..., job.artifacts)` → 现有矛盾归一化 → 原子入队 → Worker 发布。非法提交不更新任何分析 artifact。引用编号与传输单元不进入 Vault 分析格式。

## 9. 简短状态与提交回执

版本 2 的 MCP/CLI 回执只包含 `gateway_protocol/job_id/status/phase/progress/updated_at/context_revision/next_tool/gateway_context`，以及当前必要的错误、确认原因和结果摘要。`context_revision` 只在有可读取校正/分析上下文的阶段提供，否则为 `null`。

结果白名单为 `entry_id/title/summary/original_url/source_path/warnings/auth_scope`；取值从现有任务结果、元数据与 artifacts 确定，不额外读取资料正文或返回完整分析。结果放在 `result` 对象，必要确认信息保留 `reason/duration_minutes`，错误保留 `error_code/error_message`。保留批量任务现有 `favorites_context/creator_context` 的路由、父任务与静默标记，避免逐条误发完成消息；不附带批量作品清单。保留原状态与阶段中文 label，不为正文结构新增展示标签。

等待 Agent 的当前阶段可返回 `next_tool = "get_analysis_context"`；分析提交入队后与处理中返回 `next_tool = "list_job_events"`，不能把已入队任务指向不可读取的分析上下文。完成阶段返回摘要与链接，`next_tool` 为 `null`；用户另行要求完整资料时使用现有查询工具。

回执不包含 `artifacts`、全文 request、ASR、OCR、完整分析或 schema。版本 1 继续返回原 `JobRecord` 的既有完整投影。

## 10. 文件职责与接入

| 文件 | 职责 |
| --- | --- |
| 新增 `src/douyin_wiki/gateway_protocol.py` | 协议参数、阶段上下文、紧凑 schema、版本计算和状态/回执投影；不访问外部模型 |
| 新增 `src/douyin_wiki/gateway_sources.py` | OCR 过滤、来源单元与原始范围映射、引用展开；纯材料处理，不读写数据库 |
| 修改 `src/douyin_wiki/service_analysis.py` | 读取任务、按协议调用上述处理、补丁合成、现有状态/校验/并发/发布流程 |
| 修改 `src/douyin_wiki/mcp_server.py`、`src/douyin_wiki/cli.py` | 增加可选参数、回执投影和与阶段相符的调用说明 |
| 新增 `tests/test_gateway_context.py`、`tests/test_gateway_submission.py`、`tests/support/gateway.py` | 投影、版本、补丁、引用、回执与旧协议回归；共用小夹具复用现有临时服务与假适配器 |
| 修改 `docs/gateway-agents.md`、`docs/agents/integration-contract.md`、`docs/architecture.md`、`docs/README.md`，按交付内容更新 README/CHANGELOG | 业务流程仍在 Gateway 指南；接入契约只说明能力检测、仓库外验证与版本记录；架构与索引指向实际实现 |

不把 Gateway 逻辑放进本地模型适配器，不改 Web 静态资源、数据库模式或 Vault writer。跨 Service mixin 的调用与锁约束遵循 [Service 协作 API](../../service-collaboration-api.md)。

客户端依次读取校正上下文、提交补丁、读取分析上下文、提交分析，随后等待完成事件。每次提交用当前版本，不因阶段状态码相同而复用旧阶段材料。只有源材料版本变化才重新取完整上下文；格式错误返回定位明确的小错误，让客户端在仍有效的快照上修正。

仓库交付包含通用调用示例与隔离 MCP 验收。真实 OpenClaw/Hermes 等客户端的生效配置、历史压缩策略及生产切换是单独接入工作，实际完成后才能记录客户端已启用版本 2。服务端第一版不依赖客户端能裁剪历史；完整历史被重复携带时，真实计费节省会受限制。

## 11. 实施分段与验证

| 顺序 | 交付 | 验证重点 |
| --- | --- | --- |
| 1 | 协议入口、简短回执、阶段上下文、OCR 投影 | 默认旧协议；阶段材料完整；原始材料未变；去重位置可追溯；无全片截尾 |
| 2 | 版本校验与增量校正 | 未修改片段原样保留；空补丁推进；非法/过期/并发提交不写入 |
| 3 | 引用单元、紧凑 schema、证据还原 | 当前版本编号确定；连续引文准确；错时间、错图片、伪引用拒绝；标准落库结构兼容 |
| 4 | 接入文档、隔离 MCP 验收和收益记录 | 两版工具闭环；视频/图文/重新分析；批量静默；计算完整往返量 |

上述为依赖分段，不是可提前上线的残缺协议。版本 2 只在校正与分析全链路完成并通过验收后对实际客户端开放；中间开发提交保持旧客户端默认行为。

新增行为优先 TDD，测试验证用户可观察结果与保真边界，不镜像内部实现。必要样本包括：

- 大量重复 OCR、0/60,000/60,001 ms 边界、乱序观测、无时间戳、相同文字在不同图片、纯标点/短标签，以及只有后半片出现的图表文字。
- 附近相同字幕应过滤；远处相同话语、负号/小数点/百分号差异与人名差异应保留；校正稿改变后投影按阶段变化。
- 全量旧校正、单段补丁、空补丁、全部片段均修改、未知/重复 ID、重复原稿 ID、空白、过期版本、校验后的并发更新及回执丢失后的状态检查。
- 合法单段和跨段音频、OCR 与正文引文；跨图片/非连续/隐藏或其他版本编号、定位冲突、混合来源显式格式、Unicode casefold 扩展和原始字符边界。
- 引用与传统提交得到同等标准分析；推断保持推断；提醒原文还原不改变用户确认条件；回执没有大块 artifacts。
- 旧 schema、调用参数、默认返回和 CLI 仍可使用；历史重新分析直接进入分析；客户端无新参数时使用完整旧流程。

实施收尾运行对应新回归，以及既有接口、图文、重新分析、Unicode 与引文边界回归；完成项目要求的 `uv run ruff check src tests`、`uv run pytest -q -m 'not live'`。先确认验证环境，现用 `.venv` 不盲目同步；可使用已满足依赖的独立环境或 `uv run --no-sync`。真实账号、模型用量与生产客户端切换不属于本轮仓库验收。

## 12. 收益与完成判据

在相同合成/获授权脱敏材料上比较版本 1、2 的完整调用序列：校正上下文、校正请求、校正回执、分析上下文、分析请求、分析回执，以及客户端实际产生的状态轮询、事件与重试。

记录 UTF-8 字节数、字符数、OCR 原始/投影行数、修改片段数、schema 大小、引用输出大小和来源覆盖情况。报告计入编号、位置列表与新工具参数的开销，不只统计删除的正文。测量材料与报告放在仓库外维护目录，不为测量改写生产资料。

没有 Gateway usage 时报告传输字符/字节节省；能获取时再分开记录模型输入/输出 token、缓存 token、重试与时间。字符估计、单次工具体缩小和整体计费节省分别报告，不承诺固定百分比，不用另一种模型的 tokenizer 冒充当前 Gateway 实测。

完成判据为：旧协议回归通过；原始材料保留；补丁可还原完整校正稿；来源引用得到可校验的原文与定位；版本及并发保护生效；简短回执不重传正文；视频、图文与重新分析闭环通过；重复 OCR 和少量校正场景的完整往返量确实减少。真实客户端是否已启用单独记录，仓库验收不代替生产接入验收。

交付记录：[实施计划](../plans/2026-10-03-gateway-token-optimization.md)包含实施提交、验证与收益材料。当前 Agent 执行与自审，未委派子 Agent。
