# 抖库

抖库是一个面向 AI Agent 的 macOS 本地抖音知识库。OpenClaw、Hermes 等 Gateway Agent 接收
用户的抖音分享文本和灵感，本地 Worker 处理媒体，Gateway 使用当前会话模型校正与分析，
最后生成可追溯的 Obsidian Markdown。

为兼容已经安装的环境，命令名、Python 包名、MCP 配置标识和本地状态目录继续使用
`douyin-wiki`；它们是稳定的技术标识，项目展示名称统一为“抖库”。

## 业务入口与任务管理

0.2 系列的日常业务通过 MCP（Agent 调用本地工具的接口）执行。Agent 接收单条作品、博主、收藏或合集请求，创建持久任务并继续完成校正、分析和结果回复。`job_id` 是中间回执，收到它后继续跟进。

本机 Web 仅绑定 `127.0.0.1`，用于授权、阅读与筛选、管理已有批次和任务、灵感、专题笔记、重新分析及系统维护。Web 不再创建作品或批次请求，也不提供 AI 聊天、模型设置或专题自动生成。CLI 保留安装、授权、运行和维护，业务命令返回明确的停用提示。

收藏与合集由 Agent 开始清点，已有批次仍可在 Web 中选择、确认、取消和重试。使用方式与真实验证范围见[收藏导入](docs/favorites-import.md)、[合集导入](docs/collections-import.md)和[文档索引](docs/README.md)。

## 0.2 系列能力

- 从整段抖音分享文案提取短链，自动识别视频或静态图文作品。
- 从博主主页或任意单条作品反查博主，先清点全部公开作品，再由用户选择后批量采集。
- 从合集分享链接读取完整分页清单，选择后逐条入库，并保存可恢复的合集知识关系。
- 博主同步按用户给出的目标执行；普通一次性同步不会扩展为定期主页订阅。
- 博主作品按“待入库、已入库、未入库”分区；所有用户可见日期时间统一使用北京时间。
- 视频优先用专用 Playwright Profile 拦截 CDN 地址再下载；失败时才回退 `yt-dlp`（优先读专用 Profile Cookie，系统 Chrome 仅作最后手段）。
- 图文与博主采集同样使用独立 Playwright 浏览器会话，不接触日常 Chrome Profile。
- 通过 `ffmpeg`、Qwen3-ASR、Silero VAD 与 Qwen3 ForcedAligner 完成本地转录；OCR 使用 RapidOCR PP-OCRv6 small 或 macOS Vision。
- 只由当前 Gateway Agent 的会话模型校正与分析文字；原视频不上传给模型。
- 本地 Worker 在转录后暂停，通过持久任务事件把校正、人工确认和完成结果交回原会话。
- 模型直接校正逐字稿并保留疑点说明，用户选定的视频直接处理，不因时长再次确认。
- 生成不可变 `raw/` 记录、`wiki/sources/` 一屏精华页、隐藏机器侧车以及概念/实体 wikilink。
- 自动分类教程、解释、观点、推荐、事件、案例、清单等内容并选择对应知识卡片。
- 视频按内容展开顺序生成“时间轴图解”，以章节起点、摘要、要点和可选对比表完整整理内容；
  不再输出零散的“关键片段”列表。
- 优先保存抖音单独设置的视频封面，并在 `wiki/sources/` 主资料页中直接展示；
  没有独立封面时回退到原始封面或视频关键帧。
- 使用 SQLite FTS5 + 本地 Embedding 返回带原作品链接，以及时间戳或图片编号的证据包。
- 优先索引带上下文、时间戳、原文和来源类型的 `knowledge_atoms`。
- 标签按本篇中心内容生成，不参考已有标签；通常两个、最多三个，中心不足可少写。
  侧栏只提供标签管理入口，文章、信息面板和筛选保留标签；旧文章不自动回写。
- 按用户任务和已有授权创建 macOS 提醒事项。
- 每周标记过期内容、检查孤立页面，并把到期媒体移入系统废纸篓。
- Web 0.2.52 以资料库为首页，支持关键帧画廊、关键词 / 语义搜索，以及已有导入记录、任务和知识管理。

## 安装

正式运行从 wheel 安装到仓库外独立环境，步骤见[本地发行包与运行安装](#本地发行包与运行安装)。下面的 `uv` 命令仅用于独立的源码开发环境，不在现用服务环境中同步依赖；已安装用户使用对应 runtime 的命令入口。开发环境按所需 extras 显式同步一次，后续运行使用 `uv run --no-sync`，保留已经准备的依赖。

0.2.0 仅初始化 schema 16 新库，没有聊天表；旧版状态库会被拒绝，不自动迁移旧业务数据。首次使用应准备新的库，既有 Obsidian 笔记不由安装过程覆盖。升级部署先核对目标配置和实际服务入口。

```bash
uv sync --locked --extra dev
uv run --no-sync douyin-wiki init
uv run --no-sync douyin-wiki doctor
```

首次执行 `init` 时会启动引导：

1. 选择“新建独立 Vault（推荐）”或“使用已有 Obsidian Vault”。
2. 新建时选择上级目录和 Vault 名称；使用已有 Vault 时填写它的根目录。
3. 系统只创建缺失的目录与模板，不覆盖已有笔记或 Obsidian 配置。
4. 系统初始化本地 Git 和 SQLite，并提示在 Obsidian 中打开该目录。
5. `doctor` 会检查该目录是否已经登记为 Obsidian Vault，并给出具体操作提示。

自动化或无人值守安装可以跳过交互：

```bash
uv run --no-sync douyin-wiki init --vault /absolute/path/to/抖库
```

默认建议位置：

- 配置：`~/Library/Application Support/douyin-wiki/config.toml`
- Vault：`~/Documents/Obsidian/抖库`（已有 Vault 不自动改名或移动）
- 状态库：`<Vault>/.douyin-wiki/state.sqlite3`
- 图文专用浏览器：`~/Library/Application Support/douyin-wiki/browser-profile/`

初始化后会注入 `.obsidian/`、`raw/`、`wiki/`、`index.md`、`log.md`、`AGENTS.md`、
`.gitignore` 和 `.douyin-wiki/`。如果目标目录属于已有 Vault，原文件保持不变。

Apple Silicon 的 Qwen3-ASR、本地中文语义 Embedding 与 RapidOCR 是可选依赖：

```bash
uv sync --locked --extra dev --extra embeddings --extra asr --extra ocr --extra mlx
```

Qwen3-ASR 使用 Apple Silicon 上的 `qwen3` extra，`asr` / `mlx` 是它的安装兼容别名；上面的命令已包含 Qwen3 依赖，已固定为 `mlx-audio==0.5.8`。普通转写和体检只读取本地缓存，不自动下载三份模型。首次准备模型时执行：

```bash
uv run --no-sync hf download mlx-community/Qwen3-ASR-1.7B-bf16 --revision e1f6c266914abc5a46e8756e02580f834a6cf8a7 --include '*.safetensors' --include '*.json' --include '*.txt'
uv run --no-sync hf download mlx-community/silero-vad --revision 7bc17f22d3c0451bd3a6cd71e759b009271ff49a --include '*.safetensors' --include '*.json' --include '*.txt'
uv run --no-sync hf download mlx-community/Qwen3-ForcedAligner-0.6B-8bit --revision 0e1a68e91d815300c7c9754b2a7639378b23db15 --include '*.safetensors' --include '*.json' --include '*.txt'
```

需要镜像时由调用方设置 `HF_ENDPOINT` 后执行准备命令；准备成功后转写继续离线。显式 Qwen 配置同时填写 `asr_provider="qwen3"`、上述 ASR/VAD 模型及各自 `asr_model_revision` / `vad_model_revision`；对齐模型/SHA、`asr_language="Chinese"`、`asr_itn=true`、`asr_segment_seconds=30` 使用配置默认值。识别窗口最多 600 秒，对齐窗口最多 180 秒，展示段最长 5–60 秒；本期不传热词或上下文提示。Qwen ASR 与对齐权重遵循 Apache-2.0，Silero 上游为 MIT，MLX 转换模型卡未声明许可，项目不分发权重。

不按整条音轨的总时长拒绝处理，音频按最多 600 秒的窗口逐块读取和识别。

首次 `init` 创建的配置启用 `alignment_failure_policy="retain_window"`：当前窗口完整识别后，指定对齐错误修复失败时保留该窗口全文与真实范围，标记“时间未经精确对齐”，并继续处理后续窗口。支持上游大幅修正、缺少修复词、锚点文字差异、时间倒序或无法衔接、时间越界；整条音轨全部处理成功后才继续校正。已有配置缺字段仍为严格的 `fail`；修改策略后通过现有接口重试指定失败任务。内存不足、音频损坏、识别截断和原文覆盖异常不降级。识别检查点、对齐失败诊断、OCR 和关键帧各自保存，重试按媒体与配置版本复用。

首次 `init` 同时设置 `[media] keep_keyframes = true`：新任务默认视频 / 音频临时保留 30 天，关键帧长期保留。已有配置缺字段仍为 `false`，旧任务和旧条目维持原策略。MCP 采集、补导和同步可选传 `keep_keyframes`；`retention="keep"` 保留全部，`"discard"` 处理后清理全部。策略在创建任务时保存，之后改配置只影响后续请求。

0.2.0 的 Worker、维护、Web 和 MCP 应共同使用仓库外独立 runtime 中的 wheel 安装。部署前读取实际配置核对入口；旧服务若仍引用源码仓库 `.venv`，先保留该环境，完成新安装验收后再切换。开发依赖同步只在隔离工作区执行。

Embedding 普通运行同样只读取本地缓存。首次准备可在已安装的独立 runtime 中执行 `hf download BAAI/bge-small-zh-v1.5`；缺缓存时使用字符检索替代，体检说明当前准备情况。

国内网络安装依赖时，可在开发环境使用以下命令从锁文件导出版本与哈希，再在仓库外独立环境安装；先确保所用镜像提供锁定版本，不能为安装成功悄悄换版本：

```bash
uv export --locked --no-dev --extra asr --extra ocr --extra embeddings --no-emit-project --output-file /absolute/path/douyin-requirements.txt
uv venv /absolute/path/douyin-runtime
uv pip install --python /absolute/path/douyin-runtime/bin/python --require-hashes --index-url https://pypi.tuna.tsinghua.edu.cn/simple -r /absolute/path/douyin-requirements.txt
uv pip install --python /absolute/path/douyin-runtime/bin/python --no-deps /absolute/path/douyin_wiki.whl
```

上述 wheel 使用实际构建的文件名。Playwright 内核单独执行 `python -m playwright install chromium`，需要自定义下载来源时设置 `PLAYWRIGHT_DOWNLOAD_HOST`；镜像应兼容当前 Playwright 所需的版本与目录结构。`doctor` 会实际启动一次临时无界面浏览器，并检查登录内核文件，不读取已登录 Profile。

未安装 Embedding 时使用字符 n-gram；OCR 的 `auto` 优先 RapidOCR，初始化不可用时使用 macOS Vision。当前语音识别只有 Qwen3，不回退旧引擎；`asr` / `mlx` 是 `qwen3` 的安装兼容别名，不再安装 FunASR 或 mlx-whisper。

新配置使用 `asr_provider="qwen3"` 与上述三份固定模型。默认 `ocr_provider="auto"`；旧配置缺 OCR 字段仍使用 Vision。缺省、`auto`、`sensevoice`、`whisper` 及已知旧模型/SHA 在读取时成对迁移到 Qwen/Silero，原文件不被改写。`asr_device` / `whisper_*` 只作为废弃输入识别并忽略；新生成 TOML 不包含它们。未知旧自定义模型或不匹配的旧 SHA 会明确报错，需要显式配置兼容 Qwen 的模型与完整 revision。显式设置任一新模型时，必须一起提供对应 revision，不能继承其他模型的 SHA。配置字段的兼容只作用于识别设置，不迁移旧业务数据；新库已保存的逐字稿、校正稿和识别记录继续用于重试和重新分析。

模型加载仅使用本地固定 revision、已知库架构和 safetensors；拒绝模型仓 Python 文件与 `auto_map`，不执行远端代码。缺缓存时体检提供上面的准备命令，转写本身不下载模型。默认语言 `Chinese`，中文和中英混合已实测；其他语言需要识别器与对齐器共同支持，部分语言还需要独立分词包，缺少时明确报错。本期不加入热词、上下文提示或术语仓库。

视频由当前模型一次校对后直接分析入库，不增加独立复核模型，也不要求人工校对。模型结合前后文与对应 OCR 修复同音词、错字和断句；没有依据的数字、金额、人名保留原表述，不扩写或补造事实。Gateway 按本阶段上下文和 schema 提交结果；原稿、校正版和修改记录均保存。ASR 缺分、低分和普通文字疑点不会暂停新任务或单独产生完成警告；图文 OCR 低分同样只记录说明。模型超限等技术错误仍会重试或失败。Qwen 不制造置信分数；历史分数也不代表经过校准的正确率。这些规则适用于新库任务；旧版本的待复核任务不接续。

模型权重由用户显式从模型仓准备，项目不分发权重。Qwen ASR/对齐的 MLX 模型卡标注 Apache-2.0；Silero 上游为 MIT，其 MLX 转换卡未声明许可。来源见 [Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR)、[ForcedAligner MLX 模型卡](https://huggingface.co/mlx-community/Qwen3-ForcedAligner-0.6B-8bit)、[Silero](https://github.com/snakers4/silero-vad)、[RapidOCR 模型与许可](https://github.com/RapidAI/RapidOCR)。

## 分析模式

精简版只支持 Gateway：

```toml
analysis_mode = "gateway"
```

Worker 完成下载、Qwen3 转写与 OCR 后保存材料并等待 Agent。视频先经 `get_analysis_context` / `submit_transcript_correction` 校正，再重新读取上下文并用 `submit_gateway_analysis` 提交分析；图文直接进入分析。重新分析复用已保存的正文、逐字稿与 OCR，不再选择其他模型后端。

`provider` / `local` 配置会明确报错。程序不再使用 `[llm]`、对话模型 API、模型 Keychain 密钥或环境变量密钥；`configure-model` 和模型 HTTP 功能已停用。`configure-analysis-mode gateway` 仍可写入唯一支持的模式，Web 的「导入分析」仅展示状态。

Gateway 工具的默认协议仍为 `1`。新调用优先显式选 `gateway_protocol=2`，使用简短状态、阶段材料、增量校正和证据引用；两次提交必须携带刚读取的本阶段 `context_revision`。兼容协议 `1` 仍保留，不能把协议 `2` 的补丁按协议 `1` 重放。完整顺序见[Gateway 指南](docs/gateway-agents.md#紧凑协议gateway_protocol2)。

## CLI 运行与授权

CLI 保留 `init`、`doctor`、`configure-analysis-mode gateway`、`auth douyin / video / status`、`worker run`、`service install / uninstall`、`web run / open / install / uninstall / status`、`maintenance run`、`database rebuild`，以及隐藏的 `gateway monitor-events` 兼容入口。

以下业务命令和旧别名仍能识别合法旧参数，但立即以退出码 `2` 返回停用说明，不读取业务配置或执行任务：

| 已停用命令 | MCP 替代入口 |
| --- | --- |
| `capture`、`search` | `capture_douyin`、`search_knowledge` |
| `creator add / inventory / select / confirm / import / show / list / works / sync` | `capture_douyin_creator / get_creator_inventory / set_creator_work_selection / confirm_creator_import / import_creator_works / get_creator / list_creators / list_creator_works / sync_creator` |
| `favorites scan / list / show / select / confirm / retry` | `scan_favorites / list_favorites_imports / get_favorites_import / set_favorites_selection / confirm_favorites_import / retry_favorites_import` |
| `collection add / inventory / select / confirm / retry / list / show / history` | `capture_douyin_collection / get_collection_inventory / set_collection_work_selection / confirm_collection_import / retry_collection_import / list_collections / get_collection / list_collection_imports` |
| `jobs get / list / events / ack-event / approve / retry` | `get_job / list_jobs / list_job_events / acknowledge_job_event / approve_job / retry_job` |
| `review resolve`、`gateway context / submit-correction / submit-analysis` | `resolve_review`、`get_analysis_context / submit_transcript_correction / submit_gateway_analysis` |
| `entry show / submit-analysis / reanalyze / reanalyze-all` | `get_entry / submit_analysis / reanalyze_entry / reanalyze_all` |
| `topic create / list / show / sources / search` | `create_topic / list_topics / get_topic / set_topic_sources / search_topic` |
| `inspiration add`、隐藏的 `purpose add`、`reminder confirm` | `add_inspiration`、`add_purpose`、`confirm_reminder` |
| `topic generate`、`configure-model`、`migrate-vocabulary / migrate-remove-validation / migrate-covers` | 已停用，无自动生成、模型配置或旧库迁移替代命令 |

### 登录授权

视频和图文均由 Agent 调用 `capture_douyin`。视频 Cookie 失效、图文页面要求登录或出现验证码时，
任务统一进入“需要登录授权”。本机 Worker 会先保存并解锁任务，再显示 macOS 系统弹框；选择
“打开浏览器授权”后，抖库会打开对应浏览器、验证授权状态，并在验证成功后自动重试同一授权通道中仍处于
“需要登录授权”的任务。选择“稍后处理”、等待超时或浏览器启动失败不会丢失任务，下面的手工命令始终可用。

状态检查同时覆盖视频、图文和博主主页；传入一个视频 URL 时，视频检查会让 `yt-dlp` 执行只读模拟访问，
不下载媒体：

```bash
uv run --no-sync douyin-wiki auth status
uv run --no-sync douyin-wiki auth status --video-url 'https://www.douyin.com/video/作品ID'
```

主路径（视频 CDN、图文、博主）共用抖库专用 Playwright Profile。先完成专用浏览器登录，再在 Web 任务详情或通过 MCP `retry_job` 重试原任务：

```bash
uv run --no-sync douyin-wiki auth douyin
```

`auth video` 仅打开配置中的系统浏览器，给 yt-dlp 最后回退准备 Cookie；日常请优先 `auth douyin`。
两处命令都不读取、复制或输出 Cookie 值。

```bash
uv run --no-sync douyin-wiki auth video
```

`auth status` 只返回状态、Cookie 来源和修复动作，从不返回 Cookie 值。视频侧路径会优先检查专用 Profile，
并在文案中标明与系统 Chrome 的双来源关系；本地检查只读 Cookie 名/域名/过期时间，不解密 Cookie 值。
最终服务器可用性仍会在实际下载时验证。专用浏览器目录与用户日常 Chrome Profile 完全分离。
静态图文只执行图片下载和逐图 OCR（按配置使用 RapidOCR 或 Vision），不调用 yt-dlp、ffmpeg、ASR 或逐字稿校正；
背景音乐只保存曲名和作者元数据。

自动授权引导默认启用，可在配置中调整最长等待时间和轮询间隔，或完全关闭：

```toml
[auth_guidance]
enabled = true
timeout_seconds = 600
poll_seconds = 5
```

设为 `enabled = false` 只关闭系统弹框，不会禁用 `auth video`、`auth douyin` 或任务手工重试。
选择“稍后处理”只保留任务暂停状态，不会在同一次失败中循环弹框。
自动流程不会读取、复制或显示 Cookie 值、密码和 Local Storage。视频 CDN / 图文 / 博主授权共用抖库专用 Playwright Profile；
系统浏览器 Cookie 仅作 yt-dlp 回退。同一授权通道同时只显示一个引导窗口。


### Worker 与知识重建

```bash
douyin-wiki worker run --forever
douyin-wiki maintenance run
douyin-wiki maintenance run --apply
douyin-wiki database rebuild
douyin-wiki database rebuild --apply
```

维护和知识重建先预览，按用户目标执行确认。知识重建只恢复当前 Vault 的知识投影与持久关系，不恢复全部任务或导入历史，也不升级旧版状态库。

不带参数的 `worker run` 与 `--once` 只领取并处理一个任务；持续处理后续入队任务请使用 `--forever`。正式后台服务已经使用常驻模式。

Worker 使用可续期租约，进程异常后恢复有检查点的新库任务。发布时在 SQLite 写事务内校验租约，写入检索投影并可逆地更新 Vault 文档；提交前失败恢复数据库及文档，成功后才提交 Vault Git。

`gateway monitor-events` 没有未确认事件时保持静默，有事件时输出最早 100 条；它仅作单页兼容探测。完整扫描使用 MCP `list_job_events` 分页，业务查询和提交不再使用 CLI。

## 专题研究

Agent 使用 `search_knowledge` / `get_entry` 查找和读取资料，用 `create_topic` 及 `set_topic_sources` 保存选定来源；Web 资料库也可多选创建专题、启停来源和阅读笔记。

专题内问答使用 `search_topic` 和 `get_topic`，仅引用当前启用来源。无相关证据时明确说明，不回退到全库。用户要求保存或已有授权覆盖保存时，用 `save_topic_note(..., confirmed=true)` 逐字保存专题笔记。

专题仅保存笔记，没有总览、对比表、证据地图、决策简报等自动生成入口。`generate_topic_artifact` 已从 MCP 移除，`topic generate` 和 `POST /api/topics/{topic_id}/artifacts` 已停用。来源改变后可提示笔记需要更新，但不会自动重写。

## MCP

外部 Agent 从 [MCP 自助接入入口](docs/agents/README.md)读取通用步骤和参数，自行转换为当前客户端的原生配置。复用与 0.2.0 新库匹配的已确认配置、Vault 和已登录浏览器 Profile，开放 `tools/list` 返回的全部工具，按用户任务和已有授权执行。项目源码由 Codex 维护；外部 Agent 对仓库只读，实际配置、技能、工作目录、凭据、日志及记录均放在仓库外。

已有 STDIO 入口 `douyin-wiki-mcp` 保持，也可使用独立 runtime 的已安装解释器执行 `<PYTHON_EXECUTABLE> -B -m douyin_wiki.mcp_server`。完整启动环境、现有目标配置和文件边界见[接入契约](docs/agents/integration-contract.md)；不需要为某个客户端修改代码或新增适配模块。

采集是异步任务，因此 MCP 调用迅速返回 `job_id`，Agent 随后继续等待、检查状态并完成校正、分析和结果回复。
`job_id` 是中间回执，不代表整个任务结束，不要求用户再发一次检查指令。状态查询先核对 `get_job` 的 schema，
支持时显式传 `gateway_protocol=2`，避免反复返回逐字稿等完整材料；校正和分析的协议可独立选择。
任务完成（含提示）、失败或取消并交付结果后停止跟进，取消后不自动重试。当前任务内持续执行不依赖后台心跳；
只有对话结束后仍需接续时，才复用客户端的自动续跑或会话心跳，无需额外常驻监控器；自建事件桥是另一种可选方式。Gateway 应传入原会话的
`gateway_context`，使用 `list_job_events` 接收持久事件，并在成功处理或投递后调用
`acknowledge_job_event`。没有路由上下文的任务不会产生投递事件；同一任务的新状态会淘汰
尚未处理的旧状态，防止 Gateway 恢复后执行过时动作。完整接入配置和 Agent 工具顺序见
[Gateway Agent 接入指南](docs/gateway-agents.md)。


服务返回 51 个工具；名称与输入参数沿用旧契约，仅移除 `generate_topic_artifact`。升级后应退出旧 STDIO 进程并重建客户端连接，重新读取 `tools/list` 和 instructions；修改 TOML 或再次 initialize 不能切换已有进程的缓存配置。

共享等待记录的 `result.shared_capture_job_id` 指向执行任务，`result.shared_job_status` 表示其状态。用执行任务 ID 读取材料、提交校正和分析；保留等待记录原 ID 与原会话通知，批次未结束时继续跟进。

Web 不能唤醒已结束的 Agent 对话。当前对话内继续处理不需要额外心跳；只有确需结束后接续时才核对并复用客户端能力，不额外安装 Hermes 配置或常驻监控器。

## 后台服务

```bash
uv run --no-sync douyin-wiki service install
```

这会安装两个本地 LaunchAgent：常驻 worker，以及每周日 03:00 的维护任务。卸载：

```bash
uv run --no-sync douyin-wiki service uninstall
```

维护任务只处理知识过期、孤立页面和媒体保留策略，不会访问或同步博主主页。
常驻 Worker 会检测本地程序文件是否已更新：停止领取新任务，等待正在处理的任务结束后退出，
再由 LaunchAgent 自动启动新进程，避免更新前的旧代码处理新任务。

## Web 界面

Web 页面动态扫描 `wiki/sources/` 和 `creators/*/sources/`，不为每篇资料生成或维护单独的
HTML 文件。新文章入库或已有文章更新后，文件监听器会刷新资料库；`raw/`、`.data/`、日志和
机器侧车不会显示。

资料卡片和文章页都可收藏。收藏视频会切换为永久保留；如果本地媒体此前已被维护任务清理，
后台只重新下载媒体，不会再次转录、分析或消耗 AI token。取消收藏后，视频重新采用配置的临时
保留期限；图文资料仍保持永久媒体策略。

前台调试运行：

```bash
uv run --no-sync douyin-wiki web run
```

默认地址是 [http://127.0.0.1:8765](http://127.0.0.1:8765)。安装为登录后自动运行的
macOS 本地服务：

```bash
uv run --no-sync douyin-wiki web install
uv run --no-sync douyin-wiki web status
uv run --no-sync douyin-wiki web open
# 只移除常驻服务，不删除 Vault
uv run --no-sync douyin-wiki web uninstall
```

可在配置中调整端口，但主机固定为 `127.0.0.1`：

```toml
[web]
enabled = true
host = "127.0.0.1"
port = 8765
```

浏览 Markdown、封面和文章不调用模型。右栏保留信息、文章灵感和专题来源；用户灵感逐字保存。Web 支持已有任务重试 / 取消、批次选择 / 确认、资料删除 / 恢复、重新交给外部 Agent 分析和维护预览。

视频详情页在正文末尾、来源之前显示默认折叠的「完整逐字稿」，按时间段完整保留文字；优先展示校正稿，没有校正稿时展示原始识别稿。顶部「本地视频」可打开已保留的视频，信息栏显示并可复制完整本机 URL。链接使用 `GET /api/articles/{entry_id}/video`，支持 `HEAD` 和播放所需的 Range 请求；文件已清理或不存在时不提供链接，接口返回 `404`。现有 `/media/` 继续只提供图片。

同一区域的「关键帧」默认折叠，展开后懒加载缩略图，点图打开大图；视频仍在时可点时间打开对应位置。视频过期而帧长期保留时仍可浏览图片，缺失图片不会产生失效链接；旧文件无法确认时间时显示“时间未知”。

资料库默认关键词搜索，可切到“语义”按文字含义查询，并按相关度查看证据片段及时间 / 图片位置。语义检索遵守当前作者、标签、类型、来源和收藏批次筛选；无本地模型或查询失败时会提示并显示关键词结果。只读 Markdown 继续使用关键词搜索。此功能复用文字索引，不增加生成模型或画面模型。

`/imports` 提供「去 Agent 里导入」和可复制的示例指令；博主、收藏、合集入口只在已有记录时出现。`/imports/single` 同样是可复制指令；另外三个导入页不含新建表单，历史和 `?job=<新库任务ID>` 继续可用。`/settings/model` 不再提供设置页，打开后是「页面不存在」。`/settings/analysis` 只显示外部 Agent 状态。聊天 Tab、消息操作、模型设置、自动生成按钮、Cmd+I 新导入及分享文本粘贴导航已删除。

以下新建请求直接返回 `410`，不创建任务：

| 方法 | 路径 |
| --- | --- |
| POST | `/api/captures` |
| POST | `/api/creators/inventory` |
| POST | `/api/favorites/imports` |
| POST | `/api/collections/inventory` |
| POST | `/api/creators/{creator_id}/sync` |
| POST | `/api/creators/{creator_id}/import-skipped` |

以下模型功能同样返回 `410`，不查询实体、访问密钥或连接模型：`POST /api/topics/{topic_id}/artifacts`；`GET / POST /api/chat/sessions`；`GET / DELETE /api/chat/sessions/{session_id}`；`POST /api/chat/sessions/{session_id}/messages`；`GET / POST /api/settings/model`；`POST /api/settings/model/test`；`GET /api/system/model-health`。原 Origin 安全检查仍适用。

`GET /api/favorites/imports`、已有批次管理和不创建任务的 `POST /api/imports/classify` 保留。全部静态资源 URL 保留。网页不再加载模型设置脚本，打开 `/settings/model` 显示页面不存在。

## Vault 数据边界

- `raw/`：分享原文、原始/校正逐字稿、OCR、来源元数据；首次写入后不覆盖。
- `raw/assets/<video_id>/`：原视频、音轨与关键帧；新配置默认临时视频 30 天、关键帧长期保留，旧条目继续使用保存的策略，也可全部永久保留或处理后清理。
  已过期媒体重采集成功后重新计算临时保留期；尚未到期时保留当前期限。
- `raw/covers/<video_id>.*`：Markdown 使用的视频封面；不随临时视频清理。
- `raw/images/<作品ID>/`：图文原图，按 `001`、`002` 顺序永久保留；不进入 Git，也不参与
  30 天媒体清理。
- `wiki/sources/`：封面/首图、灵感、一句话、核心收获、自适应卡片和定位证据组成的一屏资料页。
- `wiki/.data/sources/<video_id>.md`：完整分析、知识原子、ASR/OCR 证据和模型溯源；由 Git
  管理，因为目录以 `.` 开头而默认不显示在 Obsidian 文件列表。
- `wiki/concepts/`、`wiki/entities/`、`wiki/syntheses/`：跨条目知识。
- `creators/<博主名_短ID>/`：博主批量采集的自包含目录，内含 `index.md`、`log.md`、
  `sources/`、`raw/`、概念/实体目录和隐藏 `.data/`；不会与旧资料目录混放。
- 已经存在于 `wiki/sources/` 的作品保持原位，博主 `index.md` 只链接它，不搬迁或复制。
- `index.md` 和 `log.md`：内容地图与只追加日志。
- `topics/`：选定来源、来源状态和逐字专题笔记，不自动生成成果。
- `.douyin-wiki/`：任务、事件、索引、向量和临时工作目录，不进入 Git；schema 16 新库无聊天表，知识重建不能恢复全部操作历史。

Vault 会初始化为独立本地 Git 仓库。程序每次只暂存本次生成的 Markdown，不自动推送远端。

## 开发验证

```bash
uv run --no-sync ruff check src tests
uv run --no-sync pytest -q -m 'not live'
```

默认测试不访问网络、不读取浏览器 cookie、不创建系统提醒。真实抖音验证使用 `live` marker，需显式运行。

## 本地发行包与运行安装

在隔离的源码开发环境构建发行包并导出锁定的 runtime 依赖，输出目录位于仓库外。替换下面的绝对路径：

```bash
DOUKU_RELEASE_DIR=/absolute/path/to/douku-release
DOUKU_RUNTIME=/absolute/path/to/douku-runtime/.venv
uv build --out-dir "$DOUKU_RELEASE_DIR"
uv export --frozen --no-dev --no-emit-project --extra embeddings --extra asr --extra ocr \
  --format requirements.txt --output-file "$DOUKU_RELEASE_DIR/requirements-runtime.txt"
uv venv --python 3.12 "$DOUKU_RUNTIME"
uv pip sync --python "$DOUKU_RUNTIME/bin/python" --require-hashes "$DOUKU_RELEASE_DIR/requirements-runtime.txt"
uv pip install --python "$DOUKU_RUNTIME/bin/python" --no-deps "$DOUKU_RELEASE_DIR/douyin_wiki-0.2.1-py3-none-any.whl"
```

`asr` 仍是 Qwen3 的安装兼容别名，锁定依赖不含旧语音引擎。wheel 从该独立环境的 `site-packages` 加载，不使用源码 editable 安装。开发环境的 `.venv` 与旧已安装环境保留。

先在仓库外工作目录核对版本、来源、CLI、空库 Web / Worker 和实际 MCP 连接；配置必须指向已准备的 schema 16 新库与正确 Profile。0.2.1 沿用 0.2.0 的配置和新库。通过安装验收后，按已确认的部署范围把 Worker、定期维护、Web 和 MCP 四个入口统一改到 `$DOUKU_RUNTIME/bin/python`，再重启并重连客户端。只修改文档或构建包不表示已部署。

MCP 使用 `-B -m douyin_wiki.mcp_server`、仓库外 cwd、`DOUYIN_WIKI_CONFIG` 和 `DOUYIN_WIKI_BROWSER_PROFILE`，完整模板见[接入契约](docs/agents/integration-contract.md#stdio-启动契约)。服务配置变化需创建新进程；不要在正在使用的旧环境中同步开发依赖。

发行包不会包含 Vault、SQLite、Cookie、媒体、模型密钥或本机配置。完整媒体处理仍要求 macOS、系统 `yt-dlp` / `ffmpeg`、Qwen3、RapidOCR 或 Swift Vision，以及对应的浏览器授权；模型权重由用户单独准备。

## 当前边界

仅支持本地 macOS 单用户和抖音来源。图文第一版支持静态单图和多图，检测到 Live Photo 会
明确报错而不会静默丢弃动态内容。Web 仅绑定本机回环地址，内部接口不作为远程公共 API。
不包含移动端分享菜单、公网或局域网服务、SaaS、多用户权限、远程同步或自动 Git 推送。
抖音页面与 cookie 规则可能变化，下载错误会保留稳定错误码和原始诊断信息。

## 许可证

本项目采用 [MIT License](LICENSE) 开源。
