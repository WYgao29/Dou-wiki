# Qwen3 ASR 可行性审查记录

日期：2026-10-07。分支：`codex/qwen3-asr`。审查人：当前 Codex Agent；实现与实验：Grok Build `grok-4.7 high` 子 Agent。

**结论：任务 1 的质量门未通过，不能继续采用当前分段参数实现，也不能删除 SenseVoice 或 Whisper。** 最小依赖与安全加载可行；先 VAD 分段再独立识别损失了上下文。原设计已把较长识别加对齐列为分段退化后的重新评估对象；继续完成仓库外实验，再提交处理方案修订审阅。对齐模型尚未下载或运行。

## 已完成的验证

worktree 位于 `/Users/weisengao/.codex/worktrees/qwen3-asr/douyin-wiki`，从 `main@5a3ce45` 建立。获批设计与计划已复制并提交；运行代码、项目依赖及旧引擎仍未修改。生产服务继续使用主仓库原环境。

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

## 复现记录

验证目录：`/Users/weisengao/asr/verification/2026-10-07-qwen3-asr-mv_d_dtr`。必要脚本、环境清单、输入哈希、原始段和失败记录均保留。

- [初始 Grok 实验报告](/Users/weisengao/asr/verification/2026-10-07-qwen3-asr-mv_d_dtr/task1/agent-report-initial.md)
- [两档 VAD 校准报告](/Users/weisengao/asr/verification/2026-10-07-qwen3-asr-mv_d_dtr/task1/agent-report-calibration.md)
- [Codex 独立复算与输入哈希](/Users/weisengao/asr/verification/2026-10-07-qwen3-asr-mv_d_dtr/task1/root-quality-review.json)
- [Codex 最终审查](/Users/weisengao/asr/verification/2026-10-07-qwen3-asr-mv_d_dtr/task1/root-review-final.md)

审查裁定：用户指定的 Grok 4.7 high 覆盖技能内的模型分档建议；用户指定当前 Codex 审查覆盖审查委派步骤；初始 VAD 参数可作有限校准。前两项改变执行分工，后一项的代价是两组额外实验。质量门槛与删除顺序均保持原要求。
