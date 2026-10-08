# Qwen3-ASR 替换验收记录

日期：2026-10-08。基准：`main@5a3ce45`。实现代码：`53f0377`，分支 `codex/qwen3-asr`。当前 Codex 独立完成后续开发、测试和审查；执行方式变更前的 Grok 工作与原验证记录均保留。

后续整体审查的两项修复已提交为 `4fd4f54`，并以 `194a943` 集成本地主线；主线回归为 1276 passed / 2 deselected。修复、复审与清理结果见[后续记录](2026-10-08-qwen3-asr-review-fixes.md)。下文保留原实现的验收基准和测量结果。

Qwen3 单引擎替换、旧配置迁移、旧数据兼容和删除后的回归与交付检查已完成。新安装环境首次导入依赖耗时约 39 秒，超过原定冷加载 10 秒目标；同一环境后续新进程的加载为 3.61 秒。用户在明确获知首次 10 分钟任务约 93 秒、后续约 57.6 秒后回复“接受”。验收按这一首次安装例外通过，原始超时测量完整保留。

## 最终实现

- `asr_text.py` 只清理模型格式并按确定规则转换中文数字；含糊数量与未支持的范围表达保留，不使用术语替换或提示词。
- `asr_segments.py` 按样本流式读取最多 600 秒，完整识别后用局部原文锚点定位约 150 秒的对齐窗口，硬上限 180 秒；展示段默认不超过 30 秒。字符与样本归属、时间顺序、2000 ms 异常修正及 80 ms 末端边界均检查。异常局部重新预测有固定预算，原始和替换预测都保留。
- `qwen3_runtime.py` 使用固定库内架构、完整 revision、本地 safetensors 和安全 tokenizer；缺缓存明确失败，普通 ASR 不下载模型。`qwen3_asr.py` 串行管理完整任务、复用三份模型，失败不返回部分结果、不切换引擎。
- `config.py` 最终只接受 `qwen3` 运行配置；已知旧 provider 和默认模型/SHA 成对迁移。显式新模型必须提供对应完整 SHA，未知旧自定义模型报错。读取不改文件，OCR 的历史缺省行为保持。
- `media.py` / `media_models.py` 已删除两个旧转写类、转换函数、选择与回退；`SelectedTranscriber` 保留。Qwen 使用的 `WhisperFeatureExtractor` 是特征预处理类，仍是必要依赖。依赖锁中没有 `funasr`、`mlx-whisper`、`kaldi-native-fbank`；Torch 继续服务于 Embedding。
- `setup.py`、健康界面只判断 Qwen 可用性；诊断旧 JSON 键保留 `retired` 标记。`service_capture.py` 在转写返回后立即复制本任务 provenance，防止其他任务覆盖。Gateway v2 投影补充模型和分段来源字段，历史 provider / `fallback_reason` 继续可读。

本期没有增加重转入口、热词、上下文提示或术语仓库。CLI/MCP、Service、Web 路径、逐字稿字段和 Vault 格式保持兼容。

## 验证环境与回归

全部安装与写入在仓库外目录进行：

```text
/Users/weisengao/asr/verification/2026-10-07-qwen3-asr-mv_d_dtr
```

下文简称 `ASR_VERIFY_ROOT`。`venv` 是删除前验证环境，`fresh-final` 是删除后的全新组合环境，`wheel-smoke` 只安装实际 wheel 和 Qwen extra。`alias-asr`、`alias-mlx` 分别检查旧安装别名；各环境没有同步生产 `.venv`。

| 检查 | 结果与证据 |
| --- | --- |
| 删除前完整非 live 回归 | 1241 passed / 2 deselected，190.07 秒；`task6/full-pytest-final.log` |
| 删除后全新环境完整非 live 回归 | 1266 passed / 2 deselected，188.57 秒；`task8/full-pytest.log` |
| Ruff、lock 与差异检查 | 通过；命令见下文 |
| 新环境依赖 | `funasr` / `mlx_whisper` 不存在；Python 3.12.13、mlx-audio 0.5.8、MLX 0.32.3、Transformers 5.19.0、SoundFile 0.14.0；`task8/versions.json` |
| `asr` / `mlx` 安装别名 | 包版本相同，三种固定模型类可导入，不安装旧引擎或 Torch；`task8/aliases.json` |
| 实际 wheel | 90 个成员，含 Qwen 模块、Swift OCR、字体及许可，无旧运行实现、环境、权重或本地实验；`task8/wheel-manifest.json` |
| wheel CLI / 配置 / doctor | 从独立 `site-packages` 导入；CLI help、默认 Qwen 配置和三份缓存检查通过，doctor 未加载权重；`task8/wheel-smoke.json`、`wheel-cli-doctor.json` |
| wheel MCP STDIO | 实际进程初始化和 44 个工具清单与源码一致，无业务工具调用；`task8/wheel-mcp.json` |
| 整体接口与删除审查 | CLI、MCP、models、Vault、Service、Web app 入口文件与基准逐字节一致；console scripts 不变；旧类/函数/运行导入不存在；`task8/whole-branch-structural-review.json` |

组合环境保留 Torch 2.14.0、sentence-transformers 6.1.0 和 RapidOCR 3.9.2；最小 Qwen wheel 环境不含 Torch。wheel doctor 使用独立空测试配置，其余尚未配置的 Vault/Embedding 项不是整机服务健康验收。

复现命令须在本功能 checkout 执行，并显式指定新环境与输出目录：

```bash
ASR_VERIFY_ROOT=/Users/weisengao/asr/verification/2026-10-07-qwen3-asr-mv_d_dtr
UV_PROJECT_ENVIRONMENT="$ASR_VERIFY_ROOT/fresh-final" uv sync --frozen --extra dev --extra embeddings --extra qwen3 --extra ocr
UV_PROJECT_ENVIRONMENT="$ASR_VERIFY_ROOT/fresh-final" uv run --no-sync ruff check src tests
UV_PROJECT_ENVIRONMENT="$ASR_VERIFY_ROOT/fresh-final" uv run --no-sync pytest -q -m 'not live'
uv lock --check
uv build --out-dir "$ASR_VERIFY_ROOT/dist"
git diff --check
```

模型准备、许可、配置迁移和分段参数见当前 [README](../../../README.md#安装)。默认 ASR / Silero / ForcedAligner 固定版本与 [设计](../specs/2026-10-07-qwen3-asr-design.md#安全加载离线与依赖)一致，发行包不附带权重。

## 真实音轨、速度与内存

| 素材或检查 | 结果 |
| --- | --- |
| 视频 `7660914966170764223`，369.779 秒 | 删除后耗时 28.699 秒，处理 RTF 0.07761，13 段 |
| 视频 `7669841546241494310`，397.038 秒 | 删除后耗时 36.893 秒，处理 RTF 0.09292，14 段 |
| 缓存模型，新进程 600 秒音频 | 删除前端到端 58.365 秒；删除后独立复测 57.622 秒，含 3.608 秒首次适配器导入及模型加载 |
| 7200 秒完整耐久 | 670.915 秒，RTF 0.09318；13 个识别窗口、242 段、115200000 个源样本完整覆盖，最长展示段 30000 ms，无 OOM |
| 删除后两条音轨内存 | 采样 physical footprint 峰值约 10.60 / 10.72 GiB，MLX 峰值约 8.49 / 8.72 GiB；各段完整指标见 `task8/real` |
| 耐久内存 | 采样 physical footprint 峰值约 13.54 GiB，MLX 峰值约 10.10 GiB；空闲 5 秒后约 6.76 GiB physical footprint、4.99 GiB MLX active；RSS 单列 |
| 现有组件共存 | 缓存 OCR、BGE 与已有本地 oMLX 同时运行通过，内存压力记录完整，无生产配置改动 |

RTF 是处理耗时除以音轨时长，包含读取、静音判断、识别、锚点、对齐和数字规范化；冷调用另加必要导入和三模型初始化。physical footprint 是 macOS 统计的进程实际物理内存占用；MLX 单列模型计算的内存，二者不能相加作为总占用。OS 文件缓存未强制清空。

新安装组合环境的首次运行记录为加载 38.897 秒、600 秒音频总计 93.100 秒；独立的新安装环境复现为加载 38.564 秒、总计 93.434 秒。两次均未达到原冷加载与冷 600 秒目标，失败结果完整保存。独立环境的导入计时显示模型 bindings 导入占 36.374 秒，三份模型构造、权重读取与求值约 0.554 秒；上游 STT 模型集合的导入连带触发 Transformers / Torch / SciPy / scikit-learn。直接加载同一 Qwen2 tokenizer 的外部原型仍需导入该集合，加载 38.851 秒、总计 93.164 秒，未解决问题，未修改生产适配器。

同一环境再次启动独立进程已测得 3.608 秒加载；Worker 正常任务还会复用模型。用户于 2026-10-08 明确接受新安装环境首次约 39 秒依赖准备、首次 10 分钟任务约 93 秒的例外；准备完成后，后续新进程仍要求加载 ≤10 秒、600 秒任务总计 ≤60 秒，现有复测满足这两项条件。证据：`task8/real`、`cold-controlled`、`cold-controlled-profile.json`、`cold-standalone`、`direct-tokenizer-prototype`。较快结果没有替换原失败，用户接受这一项差异没有改变实际测量或放宽其他质量条件。

## 文字质量及计分复现

冻结算法、参考稿和旧 Qwen 输出均没有改动。旧输出是数字规范化前的原文，因此先对新识别原文执行原算法和预先冻结的统一写法算法。CER 是编辑距离除以参考稿字符数。

| 素材 | 原算法：旧稿 → 新原文 | 统一写法：旧稿 → 新原文 | 最终展示稿直接计分：原算法 / 统一算法 |
| --- | --- | --- | --- |
| `7660914966170764223` | 76/1616 → 76/1616，均约 4.70% | 66/1616 → 66/1616，均约 4.08% | 78 / 68，字面分数高于原文基准 |
| `7669841546241494310` | 26/2062 → 26/2062，均约 1.26% | 13/2062 → 13/2062，均约 0.63% | 25 / 17，统一算法的字面分数高于原文基准 |

统一算法只额外统一 `它/他` 和 `百分之九十/90%`。原 `cer.py` 本身包含固定数词和 `Sol/So` 等比较规则，这些不是产品中的替换规则；源算法 SHA256 为 `8036c4903a78546cda0ee465fe82667aa3e50ebf413abd5236b9ef3e9acf3441`。人工“约 0.5%”没有被脚本证明。

最终展示稿与旧原文的字面差异包括 `一分→1分`、`一千万→1000万` 等数字格式，所有实际转换逐项保留上下文并检查数值/单位。另对参考稿、旧稿、新稿同等执行产品 ITN 后做附加比较，视频 1 的原/统一距离均为 76/66，视频 2 均为 21/13，与各自同等处理的旧基准一致；这项附加检查不替代上表的原算法比较，不删除字面失败数据。

复算使用冻结输入和同一脚本，新输出按 `NAME-run1/raw-text.txt` / `text.txt` 保存。以下命令输出到新的文件，保留原记录：

```bash
"$ASR_VERIFY_ROOT/fresh-final/bin/python" "$ASR_VERIFY_ROOT/task6/quality_audit.py" \
  --results "$ASR_VERIFY_ROOT/task8/real" \
  --output "$ASR_VERIFY_ROOT/task8/quality-recheck.json"
```

脚本校验 `task1/frozen-inputs/cer.py`、各参考稿和旧稿哈希，从原 AST 读取原比较函数，再计算原文、展示稿和三方相同 ITN 的结果。数字改动明细、距离、分母与各文件哈希见 `task8/quality.json`。

## 离线、下游与覆盖限制

真实 ASR 同时使用 macOS `(deny network*)`、Python socket/requests/httpx/urllib 请求阻断和计数、该 ASR PID 的 `lsof` 连接观察。正式运行的尝试计数均为 0，没有连接；实际空缓存明确失败、没有下载。零采样与 45 秒静音返回空列表。调用命令形状如下，`--output-dir` 和 `--test-vault` 必须是新的、相互独立的目录：

```bash
/usr/bin/sandbox-exec -p '(version 1) (allow default) (deny network*)' \
  "$ASR_VERIFY_ROOT/fresh-final/bin/python" scripts/verify_qwen3_asr.py \
  --output-dir "$ASR_VERIFY_ROOT/task8/offline-recheck" \
  --test-vault "$ASR_VERIFY_ROOT/task8/offline-recheck-vault" \
  --audio "ten-minutes=$ASR_VERIFY_ROOT/materials/ten-minutes.wav" \
  --network-guard --idle-seconds 5
```

两条真实转写分别经过 provider 和 Gateway，共 4 个隔离 Service/Worker/Vault 回放通过，包含逐段校正、分析提交、章节/引文/检索定位和时间展示。回放使用复制的旧 OCR 与已有分析资料；不能据此宣称真实抽帧、新图像 OCR 或新 LLM 分析质量已验收。所有公开段的整数时间、真实音轨边界、连续 id、无重叠、无空文本与无伪造置信分数均检查。

7200 秒素材由两条已有音轨按清单拼接，证明流式处理与耐久，不能代表自然长访谈准确率。噪声、多说话人、不同风格、其他识别语言、可选 8bit ASR 未进行真实准确率验收；没有自动下载新视频。模型长期空闲卸载不是本期功能。

## 交付与升级边界

完整证据在 [仓库外验证目录](/Users/weisengao/asr/verification/2026-10-07-qwen3-asr-mv_d_dtr)，删除前批准记录在 [Task 6 报告](/Users/weisengao/asr/verification/2026-10-07-qwen3-asr-mv_d_dtr/task6/REPORT.md)。wheel、sdist、原失败结果和重现材料均保留。

本次没有修改主 checkout 的未提交计划文件或暂存状态，也没有同步现用环境、升级服务、重启或改写真实 Vault。生产升级须先安排停止引用该 `.venv` 的服务，采用已验证 lock 安装并确认三份缓存，做当前配置的迁移与 doctor 检查，再启动；具体升级和 GitHub 集成按用户后续明确指令执行。

用户接受首次安装例外后，本轮只补充验收结论、设计/计划和索引，运行代码仍是 `53f0377`；已有 1266 项回归和真实素材结论继续适用。重新检查文档引用、差异和最终构建，保留分支、worktree、产物及复现材料。
