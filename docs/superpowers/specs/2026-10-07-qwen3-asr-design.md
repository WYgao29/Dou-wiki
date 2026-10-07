# Qwen3 ASR 单引擎替换设计

日期：2026-10-07。代码基准：`main@5a3ce45`。状态：用户已批准方案并开始独立 worktree 验证；原分段方案失败，长上下文识别加对齐的修订待用户审阅。

执行结果见[可行性审查记录](../reviews/2026-10-07-qwen3-asr-feasibility.md)。识别与对齐共用 180/300 秒窗口的候选也未通过两条素材；纠正后的长上下文识别加固定 150 秒对齐原型通过两条文字质量与数值格式检查。[架构修订说明](2026-10-07-qwen3-asr-alignment-revision.md)已准备；本文件下列 VAD 方案没有被标记为可用实现，修订尚未批准。原有文字规范化规则与该选择独立，已开始实现。

## 目标与需求优先级

把抖库的视频语音转写替换为本地 MLX 版 Qwen3-ASR-1.7B，保留原有分段逐字稿、校正、分析、检索和 Vault 展示格式。

需求来源是用户提供的《抖库_ASR替换为Qwen3_需求文档.md》。用户随后明确要求：**最终只保留 Qwen3；新代码修改和测试完成之后，删除 SenseVoice 与 Whisper。** 因此原文中的多引擎切换、自动回退、旧配置继续运行 Whisper、保留旧引擎测试以及切回 SenseVoice 验收，都由下表中的新要求替代。原文不是执行安装、下载、修改真实 Vault 或启动实现的授权。

| 原要求 | 本次采用的要求 |
| --- | --- |
| R1 多引擎与自动回退 | 最终唯一运行引擎为 `qwen3`；不可用时明确报错，不调用旧引擎 |
| R1 旧配置缺省为 Whisper | 已知旧配置在读取时迁移为 Qwen3；迁移规则明确、可测试，不自动改写配置文件 |
| R2 / R3 / R4 | 保持分段 schema，补充真实音频区间、中文数字规范化和最长 120 分钟的分块处理 |
| R5 / R6 | 懒加载、复用单份模型；验证实际内存、冷加载和包含分段的转写速度 |
| R7 / R8 | 推理离线，不执行模型仓代码；错误分类、中文提示及任务重试继续可用 |
| R9 | 用户已确认本次不做重转；原稿与校正稿仍可读取，重转入口列为后续可选功能 |
| R10 / R11 | 新引擎体检、健康展示和日志；独立环境验证依赖，再更新锁文件 |
| R12 保留旧引擎及切回验收 | 删除前通过新引擎验收；删除后在不含旧引擎依赖的环境再次回归，保留旧数据兼容用例 |
| 热词、上下文提示及术语库 | 用户明确本期不做，后续另建独立术语仓库及接入方案 |

本次规划由当前 Agent 完成。用户已明确授权 Grok Build 的 `grok-4.7 high` 子 Agent 实现，并指定审查由当前 Codex Agent 负责。

## 已核对的现状

- `Transcriber.transcribe(audio_path, output_dir)` 返回 `list[TranscriptSegment]`；`SelectedTranscriber` 目前在初始化失败时由 SenseVoice 回退 Whisper，推理失败不回退。
- `service_capture.py` 在已有 `transcript_raw` 时复用原稿；后续校正按段一一对应。删除旧运行引擎不要求改写已有记录的 provider、时间戳或文本。
- `load_config()` 对缺少 ASR 字段的旧配置默认选 Whisper；OCR 的缺省兼容逻辑独立，必须继续保留。
- 本机配置的 ASR 是 `auto`，媒体并发是 `1`。Worker、Web 和维护 LaunchAgent 都引用源码仓库的 `.venv/bin/python`。
- 现用环境是 MLX `0.32.2`、Transformers `5.17.0`；独立 Qwen 环境的 `mlx-audio` 为 `0.5.8`，声明依赖 MLX `>=0.31.1`、Transformers `>=5.14.0`。现用版本满足这些声明，但尚未做完整依赖解析和集成加载验证。
- 本机缓存已有 bf16 权重，revision 为 `e1f6c266914abc5a46e8756e02580f834a6cf8a7`。Silero 和 Qwen 8bit 权重尚未缓存。
- 已安装的 `mlx-audio 0.5.8`：Qwen tokenizer 加载路径使用 `trust_remote_code=True`；长音频分块共享总 token 预算，本期需要处理这两项适配。`generate()` 已支持 `hotwords` / `system_prompt`，这项能力留待后续术语仓库接入。
- 2026-10-07 核对用户提供的四个上游地址，并把本机 Qwen、Silero、STT utils、ForcedAligner 的 Python AST 与官方 `v0.5.8` tag 对比，四个文件语义一致；tag commit 为 `70f4add32911bab6f869b824864ad9f1e24dcb97`。下面的复用决定以该固定版本为依据。
- 已用原 `cer.py` 和原有文本复算：视频 1 输出 `CER=0.047`，视频 2 输出 `CER=0.013`。视频 2 额外统一 `他/它` 和 `百分之九十/90%` 后为 `13/2062≈0.63%`；报告的“约 0.5%”是人工估计，不能把它直接当作现有脚本的结果。验收保留 4.7% / 约 0.5% 的质量目标，先冻结参考稿、差异规则及实际基线，再比较新输出。

## 上游已有方案与适配边界

| 需求 | 上游已有能力 | 本项目采用方式 |
| --- | --- | --- |
| Mac 本地识别 | `mlx-audio` 已实现 Qwen3；现有 bf16 权重由官方模型转换而来 | 复用 MLX 实现与本机已缓存权重，不安装官方 `qwen-asr`，不再下载一套官方原版权重 |
| 语音区间 | 同一个包的 MLX Silero 提供 `get_speech_timestamps`，还有保留状态的 `feed` 接口 | 复用音频块的时间区间 API；项目处理跨读取边界、区间合并和严格时长限制。不新增 `silero-vad` Python 包或为 VAD 增加 ONNX Runtime |
| 长音频切分 | Qwen 的 `split_audio_into_chunks` 已按低能量位置切分，返回实际采样偏移 | 复用切分函数，调整搜索参数；流式文件读取和长度断言由项目负责，不重写同类切点算法 |
| 更细时间戳 | `mlx-audio` 已实现 Qwen3 ForcedAligner；中英对齐路径不需要日语分词包 | 本次段级时间先用 VAD；细粒度对齐是可重新评估的方案，需要另一份权重和计算。候选 8bit 权重约 1.27 GB，不属于默认安装 |
| 语言、标点、热词 | Qwen 有语言指定/检测、原生标点及 `hotwords` / `system_prompt` | 本期复用语言与标点；热词和上下文提示留待后续，本期全部转写不加提示 |
| 中文数字规范化 | 官方 `transcribe` 与 MLX `generate` 中未找到公开的 ITN 开关或确定性中文数字转换 API | 保留项目的有限规则和反例测试；不把 prompt 的格式请求当作确定性转换保证 |
| 离线和禁止远端代码 | Hugging Face 支持固定 revision、本地路径和 `local_files_only`；MLX Qwen 的 hook 仍使用 `trust_remote_code=True` | 复用缓存接口，对固定 Qwen 架构增加小型加载适配层，不直接使用不符合要求的 hook |

以上不代表已有完整的“读取两小时文件 → VAD → Qwen → 本项目逐字稿”组合入口，也不代表已验证速度与准确率。项目只补上读取、边界、预算检测、文字规则及现有服务接口。

## 最小依赖核查

- 推荐候选从基础包 `mlx-audio==0.5.8` 开始。`[stt]` extra 增加 `sentencepiece` 和 `zstandard`，Qwen3 使用的 Qwen2 tokenizer 与 MLX Silero 没有直接使用它们。已在单独的只读探针进程中模拟二者不存在：Qwen/Silero 导入、固定缓存的安全离线 tokenizer 加载、中文文本编码还原和 feature extractor 配置读取均通过；没有加载权重或执行真实转写。最终仍需在不安装二者的独立环境完成任务 1 的真实模型验证。
- 基础包本身声明 `huggingface_hub`、`miniaudio`、`mlx`、`numpy`、`scipy`、`sounddevice`、`tqdm`、`transformers`。即使项目不播放音频，也按上游元数据正常解析 `sounddevice` / `miniaudio`，不使用 `--no-deps` 强行省包。项目现用环境缺少 `mlx-audio`、`miniaudio`、`sounddevice`；已有其他包能否保持原版本，以独立解析结果为准。
- 当前官方 `qwen-asr 0.0.6` 要求 `transformers==4.57.6`，`mlx-audio 0.5.8` 要求 `transformers>=5.14.0`，两项约束不能同时满足。本项目选择 MLX 路径，不引入官方包及其 vLLM、Gradio、Flask 等依赖。此结论来自所查版本，不能套用于未来版本。
- 官方还提供不依赖 `qwen-asr` 包的原生 Transformers 路径，要求 `transformers>=5.13.0` 及 `Qwen/Qwen3-ASR-1.7B-hf` 权重。本机 `5.17.0` 已含对应模型代码，因此它是减少新增 Python 包的另一候选；但需要另准备 HF 格式权重，Apple MPS 的实际加载、速度与内存尚未验证。现有质量对比和缓存都基于 MLX，本计划继续选择 MLX，不仅为了省几个依赖就切换运行框架。
- `soundfile` 沿用项目已有依赖；FFmpeg 沿用现有媒体抽取工具。Silero 只需显式准备固定 revision 的 MLX 权重：`model.safetensors` 为 `2179454` 字节，约 2.18 MB；它不是新的大型 Python 推理环境。

## 方案比较与选择

VAD 是语音活动检测，用来判断音频中哪些时间区间有人说话；它不负责识别文字。强制对齐是在已有文字的基础上，再计算文字对应的音频时间。

| 方案 | 主要收益 | 代价与本次选择 |
| --- | --- | --- |
| **MLX Silero VAD → Qwen3 分段转写** | 区间来自音频，易保持旧 schema；不依赖 FunASR / FSMN；短段控制内存和 token 预算 | 分段可能损失上下文，需要两条素材实测。推荐本次采用 |
| 较长 Qwen 转写 → Qwen3 ForcedAligner 对齐 | 文字上下文更完整，可生成更细时间 | 多一份模型与对齐计算；对齐错误、词到段映射更复杂。作为推荐方案未达到质量或速度要求时的重新评估对象 |
| Qwen 与 VAD 放进独立常驻进程 | 可以隔离原生崩溃和依赖环境 | 增加进程生命周期、通信、取消与重启管理。当前依赖声明没有证明必须采用；若实测出现原生崩溃或不可解决的依赖冲突，再改设计 |

只实现选定方案，不同时维护三套路径。先验证推荐方案，失败时记录原因并修订方案，不降低验收标准来继续删除旧引擎。

## 分段与长音频

输入继续是 FFmpeg 抽出的 16 kHz 单声道 WAV；不在 ASR 内隐式重采样。采用 `soundfile.SoundFile` 分块读取，单块核心区间 `120000 ms`，VAD 前后各读取 `500 ms` 的上下文。VAD 上下文仅用于判断边缘语音，输出区间按核心范围归属，跨块连续语音合并后再切，防止重复输出。

Silero 参数起点：`threshold=0.5`、`min_speech_duration_ms=250`、`min_silence_duration_ms=300`、`speech_pad_ms=200`。相邻且间隔不超过 `300 ms` 的语音段，在合并后不超时的条件下合并；padding 重叠的区间先合并。默认每段上限 `30000 ms`，配置最多允许 `60000 ms`。连续发声超过上限时，复用上游 `split_audio_into_chunks`：对于上限 `S` 秒，设置 `chunk_duration=S-1`、`search_expand_sec=1`、`min_window_ms=20`、`min_chunk_duration=0`。这样低能量搜索位于上限前 `2000 ms` 内，切分阶段不补齐尾段采样；项目再次检查真实长度、偏移和完整覆盖。选择低能量切点与原 RMS 方案的排序一致，不另写一份切分算法。

上游参数名 `chunk_duration` 本身不保证严格上限：默认搜索可向目标切点后扩展 5 秒。只读函数探针中，`chunk_duration=30` 的首段为 `34.05` 秒；调整上述参数后的 21 个纯音频数组用例覆盖 5/30/60 秒上限、短段与 120 秒输入，均无采样丢失、重复或超长。这只验证上游切分行为，不能替代真实 ASR 或跨文件读取边界的回归。上游 `generate().segments` 也是音频块边界，不等于字词级时间戳。

时间从实际采样位置换算为毫秒，限制在音频时长内。最终段必须满足 `0 <= start_ms < end_ms <= duration_ms`，相邻段不倒序、不重叠，文本非空，`id` 从 0 连续编号。VAD 时间是段级定位，不当作字级对齐结果。

每个区间独立调用 Qwen，`temperature=0.0`、`batch_size=1`、`max_tokens=2048`、`system_prompt=None`、`hotwords=None`；单次调用只处理一个区间。每段重新获得 token 预算；达到预算上限时整次转写失败并明确报错，不返回已截断的成功结果。不采用整条 120 分钟音频的一次 `generate()` 调用。

有效但零采样的 WAV、全静音返回 `[]`；损坏文件、格式不符、无效 VAD 输出明确报错。短语音需要模型内部 padding 时，输出时间仍按真实区间计算。优先在停顿处分段、保留 Qwen 原生标点；硬切点不保证恰好落在句号处，不编造句末标点。这项时间上限与句子完整性的取舍须在真实报告中展示。

## 文字规范化

ITN 在这里指把中文数词改写成数字，同时保留数值和单位。使用本地、确定性的规则，不调用 LLM，不全局替换所有中文数字。

| 输入 | 预期输出或规则 |
| --- | --- |
| `五点六`、`五点六三` | `5.6`、`5.63` |
| `五十九分`、`一百五十万`、`百分之五十` | `59分`、`150万`、`50%` |
| `五点六三个模型` | `5.6 三个模型`；先保护末尾数量短语，再解析小数 |
| `Fable五` | `Fable 5`；英文标识紧邻的版本数字，且后面不是中文量词 |
| `一下`、`三步`、`三个模型`、`十几分钟`、`一心一意`、`三维` | 原样保留；模糊数量、固定表达及普通小数量词不强转 |
| 原有数字、英文术语、大小写及标点 | 原样保留；规范化重复执行结果相同 |

转换范围限于小数、百分比、带明确计量单位的数量（分、分钟、秒、小时、元、万元、亿元、万、亿）以及英文标识后明确的版本数字。无法确定的表达保留原文，不把推测当成数字。

清理 `<|…|>`、`<asr_text>` 等已知特殊标签，仅在开头清理模型格式中的语言前缀。默认语言 `Chinese`，`auto` 映射为模型的 `language=None`，其他语言按模型支持列表校验。本期不传热词或背景提示，数字规范化使用上述独立规则。

`confidence`、`avg_logprob`、`no_speech_prob` 均保持 `None`；继续使用现有“分数不可用，不代表识别质量低”的说明，不制造置信分数。

## 安全加载、离线与依赖

默认模型及固定版本：

| 用途 | 模型 | revision |
| --- | --- | --- |
| 默认 ASR | `mlx-community/Qwen3-ASR-1.7B-bf16` | `e1f6c266914abc5a46e8756e02580f834a6cf8a7`（已缓存） |
| VAD | `mlx-community/silero-vad` | `7bc17f22d3c0451bd3a6cd71e759b009271ff49a`（已查询元数据，待显式下载验证） |
| 可选低内存 ASR | `mlx-community/Qwen3-ASR-1.7B-8bit` | `a8379a2e2f9e313c9292cdf1af4055ab56d50d55`（已查询元数据，非默认验收模型） |

用 `snapshot_download(..., revision=..., local_files_only=True)` 解析本地快照，再加载本地路径。所有 tokenizer / feature extractor 调用明确设置 `local_files_only=True`；tokenizer 明确使用 `trust_remote_code=False`。不修改进程全局离线环境来影响 Embedding 或 LLM，不改已安装的第三方包、不使用全局 monkeypatch。

新增一个针对固定 Qwen 架构的小型加载适配层：使用库内 `ModelConfig`、`Model`、权重清理与量化函数，加载 safetensors 后自行初始化安全的 tokenizer 和 feature extractor，绕过上游不符合要求的 `post_load_hook`。仅接受已知 Qwen / Silero 架构，拒绝需要模型仓动态 Python 的 `auto_map`，权重范围不含 Python 或 pickle 文件。Qwen 使用的 `WhisperFeatureExtractor` 是音频预处理类，删除 Whisper 转写引擎时仍保留这一必要调用。

`qwen3` extra 首先验证 `mlx-audio==0.5.8` 基础包和 `soundfile>=0.12.1,<1`；只有真实加载/转写证明需要时才增加有依据的 STT extra。MLX 相关依赖使用 macOS ARM64 平台条件；锁文件以已有对比环境使用的 MLX `0.32.3`、Transformers `5.19.0` 为初始候选。通过独立环境解析与安全加载验证后确定最终锁定结果。`asr`、`mlx` 安装别名最终指向 Qwen3 依赖，保留既有安装命令的可用性。

删除旧引擎后移除本项目对 `funasr`、`kaldi-native-fbank`、`mlx-whisper` 的依赖。`torch` 仍可能被 Embedding 等功能使用，按最终依赖图决定保留，不能因旧 ASR 删除而一并卸载。

首次模型准备和升级采用 README 中的显式 `hf download ... --revision <sha>` 命令，支持调用方设置 `HF_ENDPOINT`；普通任务和 doctor 不下载模型。Qwen 权重为 Apache-2.0；Silero 上游为 MIT，MLX 转换模型卡未声明 license，记录该事实和上游来源，不随发行包分发任何权重。[Qwen 模型卡](https://huggingface.co/mlx-community/Qwen3-ASR-1.7B-bf16)、[Silero 上游许可](https://github.com/snakers4/silero-vad/blob/master/LICENSE)。

## 配置、旧数据与运行行为

最终生成的 `[media]` 只包含 Qwen3 运行选项：`asr_provider="qwen3"`、`asr_model`、`asr_model_revision`、`vad_model`、`vad_model_revision`、`asr_language="Chinese"`、`asr_itn=true`、`asr_segment_seconds=30`（范围 5–60）。OCR 及其他媒体参数继续按现有方式处理。

读取配置时：

1. 缺少 `asr_provider`，或旧值为 `auto` / `sensevoice` / `whisper`：迁移为 Qwen3，提示旧引擎已移除。未写模型字段、或模型等于现有 SenseVoice / FSMN 默认值时，同时替换模型及对应 revision；绝不把旧 revision 用于 Qwen。
2. 已明确填写 Qwen 模型及 revision：保持原样。换成 8bit 或自定义 Qwen 模型时必须一起提供对应 revision，不能继承 bf16 的 SHA。
3. 旧 provider 下填写了无法确认用途的自定义模型：给出中文配置错误和修改示例，不悄悄舍弃该配置。未知 provider 不归入已知旧值迁移。
4. `whisper_*`、旧 `asr_device` 等运行选项仅作为过渡配置输入识别、提示并忽略；新配置不再输出。配置文件读取不产生写入，OCR 缺少 provider 时仍保持 Vision 的旧兼容规则。

`SelectedTranscriber` 保留现有服务调用入口，最终只包装 `Qwen3Transcriber`。初始化缺包、缺权重、缓存不完整归类为 `BackendUnavailableError`；输入错误、推理错误、Metal/OOM 等可捕获异常归类为 `ExternalToolError`。失败时清除该次临时引用和 MLX 缓存，必要时丢弃损坏模型状态，以便下一次显式重试重新初始化。原生进程崩溃无法由 Python 异常处理保证恢复；若实测出现，必须改为进程隔离后才能通过 R8。

同一 Worker 的 ASR 锁覆盖初始化和推理，模型只在选中时加载，健康检查不加载权重。正常空闲期间保留并复用模型，首版不增加定时卸载；报告需要测量空闲占用和与 OCR、Embedding、现有 oMLX 并行时的内存。如果常驻内存确有压力，先评估 8bit，再决定是否增加空闲卸载，不在未测量前引入后台定时器。

旧 `transcript_raw`、校正稿、provider 和 `fallback_reason` 保持原样，不批量改写或重转。新 provenance 至少增加 `revision`、`vad_model`、`vad_revision`、`segmentation="silero-vad"`、`timestamp_kind="vad"`、`language`、`itn="zh-numbers-v1"`（关闭时为 `disabled`）；新结果没有回退。Gateway 协议 2 的投影白名单补充这些字段，旧 payload 字段继续可读。

每次转写日志记录 provider、模型与 revision、音频时长、首次加载耗时、包含 VAD / ASR / ITN 的耗时、RTF、段数、`fallback=false`、错误分类和可获得的内存指标。RTF 是处理耗时除以音频时长；冷调用另列含加载的总耗时和 RTF，10 分钟端到端验收不扣除加载时间。冷加载从首次所需导入到 ASR/VAD 可用计时。区分进程 RSS、MLX 内存和 macOS physical footprint，不用 RSS 代替统一内存峰值，也不把跨任务的历史高水位当作本任务峰值。

## 验收与删除顺序

| 检查 | 通过条件 |
| --- | --- |
| 段与格式 | 默认参数下所有段不超过 30 秒；任何允许配置都不超过 60 秒；时间有效、有序，id 连续，无空文本 |
| 准确率 | 保留报告的 4.7% / 约 0.5% 目标；用冻结的参考稿分别计算原算法与统一写法后的基线，新结果两项编辑距离均不大于旧 Qwen 输出，人工核对也不新增实质错误。0.5%估计与0.63%复算差异明确展示，不悄悄改成宽松阈值 |
| 重点文字 | 核对 Terra、Luna、Fable 5、Artificial Analysis Intelligence Index、Coding Agent Index、X High 及指定数字；Sol / So 不强行改写为正确答案 |
| 速度 | 模型已缓存时冷加载 ≤10 秒；含读取、VAD、转写和 ITN 的 RTF ≤0.1，冷调用同时计入加载；10 分钟素材端到端处理 ≤60 秒 |
| 长音频 | 至少一次 120 分钟流式读取与真实 ASR 验证；无 OOM、成功结果无截断或重复，开头、中部、末尾哨兵均保留。拼接素材单列为耐久测试，不冒充真实长访谈准确率 |
| 内存 | 记录两条素材峰值、连续任务后空闲值及与现有组件同时运行时的系统压力；没有一个已被需求指定的绝对 GB 阈值，不把 9.6 GB 估算当成保证 |
| 错误恢复 | 缺包/缺权重有修复提示；推理失败不换引擎；任务失败后 Worker 可处理下一条并可重试 |
| 数据兼容 | provider / gateway 校正链路、章节证据、检索与 `[mm:ss]` 展示正常；旧任务恢复不调用新 ASR |
| 离线 | 缓存齐全时 ASR 运行没有任何联网尝试；缺缓存时明确失败，不自动下载 |
| 删除后 | 干净环境没有 FunASR / MLX Whisper，Qwen 转写与正式非 live 回归通过，wheel 不含旧引擎实现 |

必须先完成 Qwen 代码、单元/集成测试和两条素材的真实报告，再删除两个旧引擎；删除后的全量检查再次通过，才具备集成条件。真实回归只读源素材，只写仓库外临时目录和测试 Vault。不同风格、噪声、多人语音补测采用已有合法素材；缺素材列为明确的覆盖限制，不为本任务自动下载新抖音视频。

## 后续：独立术语仓库

用户已确认本期不做热词和上下文提示。后续另建独立术语仓库，先整理 AI 术语并制定 Agent 维护规则；如何挑选相关词条、生成背景提示及接入抖库另写设计和计划。本期不新增提示配置、元数据传递接口、提示记录或提示效果对比任务，也不预建仓库。

## 旧数据重转的可选后续方案

用户已确认本次不做重转入口，先完成 Qwen3 替换。以下仅记录以后可以选择的范围。现有 `entry reanalyze` 复用校正稿，不能当作重新识别音轨的入口。

| 后续范围 | 行为与代价 |
| --- | --- |
| 单条显式重转 | 新任务读取仍保留的音轨；把旧原稿、校正稿、分析和 provenance 归档为新版本；重新校正、分析、章节、证据和检索，发布成功前保留旧条目可用。需要作品/条目锁、租约校验、保留用户修改与失败回滚测试 |
| 再增加批量重转 | 在单条任务之上增加清单预检、跳过缺音轨条目、进度、取消、幂等和失败重试；需要队列与 Web 操作设计，不能仅清空现有 artifacts 后重新分析 |

重转是独立子项目；用户如选择本次纳入，应先补一份独立规格和计划，再开始这一部分，不削弱本次单引擎替换的验收。

## 依据

- 附件需求、`ASR对比测试/对比报告.md`、原 `cer.py` 和两条已有输出：2026-10-07 只读核对。
- 补充附件 [Qwen3-ASR 热词与上下文提示方案](</Users/weisengao/Downloads/GrokBot Handoff/Qwen3-ASR_热词与上下文提示方案.md>)：保留为后续独立术语仓库的参考资料，不纳入本期开发或验收。
- 本机已安装 `mlx-audio 0.5.8` 的 `stt/utils.py`、Qwen `post_load_hook` / `generate` 和 Silero `get_speech_timestamps`：作为版本固定的实现依据。
- [mlx-audio v0.5.8 依赖声明](https://github.com/Blaizzy/mlx-audio/blob/v0.5.8/pyproject.toml)、[固定版本 Qwen 源码](https://github.com/Blaizzy/mlx-audio/blob/v0.5.8/mlx_audio/stt/models/qwen3_asr/qwen3_asr.py)、[Silero 用法](https://github.com/Blaizzy/mlx-audio/blob/v0.5.8/mlx_audio/vad/models/silero_vad/README.md)：最小依赖、切分、加载 hook 与 MLX VAD 的依据。
- [Qwen 官方依赖声明](https://github.com/QwenLM/Qwen3-ASR/blob/main/pyproject.toml)、[官方推理接口](https://github.com/QwenLM/Qwen3-ASR/blob/main/qwen_asr/inference/qwen3_asr.py)：官方包版本约束与公开转写能力；[官方模型卡](https://huggingface.co/Qwen/Qwen3-ASR-1.7B)和 [MLX bf16 模型卡](https://huggingface.co/mlx-community/Qwen3-ASR-1.7B-bf16)：原模型与转换模型的关系。
- [官方 Transformers 原生模型卡](https://huggingface.co/Qwen/Qwen3-ASR-1.7B-hf)：`>=5.13.0` 的直接加载路径，独立于旧 `qwen-asr` 包的固定依赖。
- [固定版本热词合并接口](https://github.com/Blaizzy/mlx-audio/blob/v0.5.8/mlx_audio/stt/utils.py)：`hotwords` 与上下文使用同一提示机制；[官方上下文用法](https://huggingface.co/Qwen/Qwen3-ASR-1.7B-hf#context--hotwords)：自由文本用于引导词汇识别。
- [Qwen 官方项目](https://github.com/QwenLM/Qwen3-ASR)：ASR 与 ForcedAligner 的职责；[MLX ForcedAligner 模型卡](https://huggingface.co/mlx-community/Qwen3-ForcedAligner-0.6B-8bit)：候选对齐模型。
- [Silero 转换模型元数据](https://huggingface.co/api/models/mlx-community/silero-vad?blobs=true)、[ForcedAligner 元数据](https://huggingface.co/api/models/mlx-community/Qwen3-ForcedAligner-0.6B-8bit?blobs=true)、[Qwen 8bit 元数据](https://huggingface.co/api/models/mlx-community/Qwen3-ASR-1.7B-8bit)：已查询版本与权重大小，不等于完成加载验收。
- [Hugging Face 下载接口](https://huggingface.co/docs/huggingface_hub/en/package_reference/file_download)：固定 revision 和仅解析本地缓存的标准能力。
