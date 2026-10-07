# Qwen3 ASR 可行性审查记录

日期：2026-10-07。分支：`codex/qwen3-asr`。审查人：当前 Codex Agent；实现与实验：Grok Build `grok-4.7 high` 子 Agent。

**结论：原 VAD 分段方案失败；长上下文识别加短窗口对齐的固定原型通过两条素材的原始文字质量与数值格式检查。** 最小依赖与安全加载可行。最终 150 秒对齐目标的处理 RTF 为 0.08050 / 0.09619，但尚不含 ITN，也未完成正式冷加载、长音频和下游验收。产品架构修订见[已批准修订](../specs/2026-10-07-qwen3-asr-alignment-revision.md)；SenseVoice 与 Whisper 仍保留。

## 已完成的验证

worktree 位于 `/Users/weisengao/.codex/worktrees/qwen3-asr/douyin-wiki`，从 `main@5a3ce45` 建立。获批设计与计划已复制并提交；运行适配器、项目依赖及旧引擎仍未修改。任务 2 的文字模块最终提交为 `2c75976`，115 项定向测试与 Ruff 通过；当前 Codex 对普通词语、数量、标签及实际全文的独立审查通过。生产服务继续使用主仓库原环境。

原代码基线：`UV_PROJECT_ENVIRONMENT=<验证目录>/venv uv run --no-sync ruff check src tests` 通过；同一环境的 `pytest -q -m 'not live'` 为 **1016 passed, 2 deselected**。

独立最小环境真实完成两条素材的 Qwen/Silero 安全加载及分段转写，使用 `mlx-audio==0.5.8`、MLX `0.32.3`、Transformers `5.19.0`、SoundFile `0.14.0`。没有安装 `sentencepiece`、`zstandard`、官方 `qwen-asr`、独立 `silero-vad` 或旧引擎。固定 Silero 数据权重已显式准备；没有改动已安装包。

原始参数为 300 ms 静音阈值；随后只校准到 1000 和 1500 ms。各组都保持 30 秒段长上限、无热词或背景提示，并采用同一参考稿和同一计算规则。表中数值是字符编辑距离，越小越好；左、右两项分别是原算法和统一「它/他」「百分之九十/90%」写法后的算法。

| 素材 | 旧整段 Qwen | 300 ms | 1000 ms | 1500 ms |
| --- | --- | --- | --- | --- |
| `7660914966170764223`，参考 1616 字符 | 76 / 66 | 98 / 89 | 88 / 78 | 96 / 86 |
| `7669841546241494310`，参考 2062 字符 | 26 / 13 | 27 / 18 | 27 / 14 | 27 / 14 |

所有新结果的两项距离都大于基线。视频 1 最好的一组原算法字符错误率（CER）仍为 **5.45%**，基线 **4.70%**，并有 `Fable`、`Coding Agent Index` 等词项回归。视频 2 的最好统一算法为 **0.679%**，基线 **0.630%**。

同一安全加载器对视频 1 做整段识别，结果去掉末尾换行后与旧 Qwen 稿完全相同，原算法仍是 76/1616。这把主要原因指向分段导致的上下文损失。把允许的诊断段长改为 60 秒也没有修复问题；它没有被采纳为默认参数。

分段初测耗时约 21–27 秒，耗时除以音频长度的比值（RTF）约 0.058–0.067；有效段长度都在 30 秒内，未达到生成长度上限，也没有空段。完整可用加载约 1.2–1.5 秒，但这些进程启动时权重页缓存已被先前实验触碰，不能据此宣称首次接触的冷加载验收通过。RTF 尚不含独立数字规范化，10 分钟端到端与 120 分钟耐久测试未执行。

分段进程的 macOS physical footprint 峰值约 5.98–6.11 GB；整段对照约 9.44 GB。这是实测的进程物理占用，另有 RSS 和 MLX 指标，三者未混用。生产并行使用及连续任务后的空闲占用仍待正式验收。

## 原设计候选的重新评估

继续验证原设计已经列出的 **较长 Qwen3 识别区间 → Qwen3 ForcedAligner → 最终不超过 30 秒的逐字稿段**。ForcedAligner 只根据已有文字寻找音频时间，不重新识别或改写文字。先验证该路径，结果满足质量、速度与时间要求后，再修改正式设计和实施任务。

新增模型为 [`mlx-community/Qwen3-ForcedAligner-0.6B-8bit`](https://huggingface.co/mlx-community/Qwen3-ForcedAligner-0.6B-8bit)，固定 revision `0e1a68e91d815300c7c9754b2a7639378b23db15`，`model.safetensors` 为 `1271924386` 字节，约 1.27 GB，SHA-256 `be19ef8ac4326d032e7673342930b14c2df30bd68c1632493b0f563e30829f91`，许可 Apache-2.0。[固定模型文件元数据](https://huggingface.co/api/models/mlx-community/Qwen3-ForcedAligner-0.6B-8bit?blobs=true)

官方对齐模型支持的语音长度为最多 5 分钟，因此候选实验先比较 **180 和 300 秒**的识别/对齐窗口，不把整条 6 分钟或 120 分钟音频直接交给对齐器。当前两条素材的窗口划分是否保持原准确率仍需实测；整段对照成功不等于该候选已经通过。[官方模型说明](https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B)

实验范围与通过条件：

1. 按当前开发授权显式准备上述固定版本的模型数据；沿用仓库外最小环境，先验证基础 `mlx-audio` 包是否够用，不直接增加 `[stt]` 或官方包。
2. 安全加载固定 ForcedAligner 架构，使用 safetensors，拒绝模型仓 Python/pickle 和 `auto_map`；tokenizer 设 `trust_remote_code=False`、`local_files_only=True`，绕过同样使用不安全参数的上游 hook。
3. 两条已有音轨各测试 180/300 秒窗口，使用 Qwen 原文作对齐输入。保留原文字与标点，对齐输出只提供位置，不用其重新拼接的文本替换原稿，不增加热词、背景描述或术语替换。
4. 按对齐位置与原文边界生成 ≤30 秒的展示段。检查起/中/末位置、窗口交界、数字和英文标识，不丢词、不重复、不编造时间；无效对齐应报错。
5. 继续使用上述冻结参考稿、原算法和补充规则；两项编辑距离均不得大于基线，专有名词不能出现新的实质错误。测量含识别、对齐和分段的耗时及带指标类型的内存数据；其速度与内存尚未得到验证。
6. 中文与中英混合之外的语言支持、最终长音频窗口、生成预算和 provenance 在可行后补入正式设计；本实验不直接改变这些运行契约。

当前先完成候选的隔离可行性实验。两条素材未通过前，不提交项目依赖或新运行适配器；旧引擎删除继续受正式验收约束。热词、术语仓库与旧视频重转仍不在本期范围。

## 识别与对齐共用窗口的复评结果

固定 ForcedAligner 数据已完成下载、大小与 SHA-256 校验。沿用无 `sentencepiece` / `zstandard` 的最小环境，安全加载完成；量化先于参数键检查，1104 个键完全匹配，tokenizer 不执行远端代码。

| 识别 / 对齐窗口 | 视频 1 原算法 / 统一算法距离 | 视频 2 原算法 / 统一算法距离 | 质量结论 |
| --- | --- | --- | --- |
| 基线 | 76 / 66 | 26 / 13 | 冻结对照 |
| 180 秒 | 74 / 64 | 29 / 16 | 视频 2 回归，新增「圣人感」 |
| 300 秒 | 97 / 87 | 26 / 13 | 视频 1 回归，新增 11 处 `Soul` |

当前 Codex 独立复算四组距离，核对原始与冻结输入哈希、完整采样覆盖、生成预算及原文字符覆盖。四次含读取、识别、对齐与分段的处理 RTF 为 0.074–0.090，峰值 physical footprint 为 10.68–13.24 GB；这些运行没有 ITN，也没有从首次必要导入计时的冷加载、10 分钟或 2 小时验证，不能当成正式验收。

Grok 的初始复评额外禁止了原始时间戳修正。审查纠正：Qwen 官方和 MLX 固定版本都在正常解析中使用 `fix_timestamp`，包括邻值替换和插值，原始类别后退不能直接证明正常 API 不可用。只对已保存预测重新解析后，180 秒窗口的修正最大为 960 ms，生成的全轨段满足原文覆盖、有序、不重叠及 ≤30 秒；300 秒主窗口的单点修正超过 200 秒，排序正确不能证明音频定位准确。文字质量失败在修正前后都成立。[官方处理代码](https://github.com/QwenLM/Qwen3-ASR/blob/main/qwen_asr/inference/qwen3_forced_aligner.py)、[固定 MLX 实现](https://github.com/Blaizzy/mlx-audio/blob/v0.5.8/mlx_audio/stt/models/qwen3_asr/qwen3_forced_aligner.py)

### 识别与对齐分别限制的纠正实验

ForcedAligner 的 300 秒限制只约束对齐输入，不约束 Qwen 识别。上轮把两者窗口绑定，限制了识别上下文，尚不足以排除原设计的替代方案。只再验证一档：两条素材均用完整上下文识别（候选长音频识别窗口最多 600 秒），通过短音频识别及对齐锚点定位原稿中的已有字符边界，再用约 120 秒、硬上限 180 秒的窗口对齐。锚点只定位，不替换原文；含糊或无效匹配使整次实验失败。原质量规则、最终段 ≤30 秒、无提示、本地安全加载及旧引擎删除顺序保持原要求。

该实验在仓库外完成，没有修改产品模型、依赖或运行入口。120 秒对齐目标保留了两条原始全文，但视频 2 的 RTF 为 0.10291，超过 0.1。独立审查推导的 150 秒目标减少一个锚点，主窗口上限仍为 180 秒；首轮发现 40 ms 的窗口末端越界。Grok 随后只修复这一时间栅格问题，没有继续调整匹配阈值或质量规则。

### 最终固定 150 秒原型与独立审查

模型每 80 ms 预测一个时间位置。只有预测超过实际窗口末端不多于一格时，操作时间才限制到实际末端，并单独记录原值、结果、差值和阈值；原始及上游修正时间保持完整。负数、非有限数、倒序和超过一格的越界仍拒绝。探针先因缺少该行为失败，修复后通过；覆盖 40/80 ms、81/160 ms、NaN/Inf、负数、倒序和范围内不变等情况。

| 指标 | 视频 1 | 视频 2 |
| --- | ---: | ---: |
| 原算法编辑距离 / 参考字符数 | 76 / 1616，与旧 Qwen 相同 | 26 / 2062，与旧 Qwen 相同 |
| 统一算法编辑距离 | 66，与旧 Qwen 相同 | 13，与旧 Qwen 相同 |
| 原始正文 | 与旧 Qwen 相同，少末尾换行 | 与旧 Qwen 相同，少末尾换行 |
| 展示段数 / 最长段 | 13 / 30 秒 | 14 / 30 秒 |
| 读取、识别、锚点、对齐、分段耗时 | 29.766 秒 | 38.193 秒 |
| 处理 RTF，未包含 ITN | 0.08050 | 0.09619 |
| 热缓存模型加载 | 2.714 秒 | 2.119 秒 |
| 加上热缓存加载的 RTF | 0.08784 | 0.10153 |
| physical footprint 峰值 | 10.766 GB | 10.870 GB |
| 上游最大时间修正幅度 | 480 ms | 160 ms |
| 项目末端处理 | 无 | 1 处，150080 → 150040 ms |

Codex 逐项核对冻结参考稿与旧稿哈希，用冻结 CER 函数重新计算；重新运行上游 `fix_timestamp`，核对全部原始类别、修正时间和独立边界记录。两条样本完整覆盖 5916467 / 6352608 个采样点和 1821 / 2303 个原文字符，id 连续，段非空、有序、无重叠、长度不超过 30 秒，生成预算未耗尽。唯一的 40 ms 处理位于视频 2 第一主窗口的「突」字，下一段从同一实际时间开始。

原型数值有效不证明每个字的时间准确；仍需正式定位核对。原型先读完整输入再检查 600 秒限制，产品必须重新实现有界读取；Unicode 大小写展开、句读优先及数字表达跨段保护也要覆盖。此次结果不能代替 ITN、包含首次必要导入的冷模型初始化、10 分钟、2 小时、离线连接观察与下游验收。原需求的冷加载以本地模型已缓存为前提，系统文件缓存状态另记；原型的模型阶段耗时没有计入全部首次导入。原始失败证据保留，旧引擎删除门保持原计划。

## 复现记录

验证目录：`/Users/weisengao/asr/verification/2026-10-07-qwen3-asr-mv_d_dtr`。必要脚本、环境清单、输入哈希、原始段和失败记录均保留。

- [初始 Grok 实验报告](/Users/weisengao/asr/verification/2026-10-07-qwen3-asr-mv_d_dtr/task1/agent-report-initial.md)
- [两档 VAD 校准报告](/Users/weisengao/asr/verification/2026-10-07-qwen3-asr-mv_d_dtr/task1/agent-report-calibration.md)
- [Codex 独立复算与输入哈希](/Users/weisengao/asr/verification/2026-10-07-qwen3-asr-mv_d_dtr/task1/root-quality-review.json)
- [Grok 对齐复评报告](/Users/weisengao/asr/verification/2026-10-07-qwen3-asr-mv_d_dtr/task1/agent-report-alignment.md)
- [Codex 对齐独立复算与正常后处理检查](/Users/weisengao/asr/verification/2026-10-07-qwen3-asr-mv_d_dtr/task1/root-alignment-review.json)
- [Codex 对齐审查](/Users/weisengao/asr/verification/2026-10-07-qwen3-asr-mv_d_dtr/task1/root-alignment-review.md)
- [Grok 固定 150 秒边界修正报告](/Users/weisengao/asr/verification/2026-10-07-qwen3-asr-mv_d_dtr/task1/agent-report-boundary-fix.md)
- [Codex 最终原型独立复算](/Users/weisengao/asr/verification/2026-10-07-qwen3-asr-mv_d_dtr/task1/root-boundary-review.json)
- [Codex 最终原型审查](/Users/weisengao/asr/verification/2026-10-07-qwen3-asr-mv_d_dtr/task1/root-boundary-review.md)
- [Codex 最终审查](/Users/weisengao/asr/verification/2026-10-07-qwen3-asr-mv_d_dtr/task1/root-review-final.md)

审查裁定：用户指定的 Grok 4.7 high 覆盖技能内的模型分档建议；用户指定当前 Codex 审查覆盖审查委派步骤；初始 VAD 参数可作有限校准。前两项改变执行分工，后一项的代价是两组额外实验。质量门槛与删除顺序均保持原要求。
