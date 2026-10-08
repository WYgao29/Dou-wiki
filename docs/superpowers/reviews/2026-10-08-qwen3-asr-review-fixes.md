# Qwen3-ASR 审查修复与复审

日期：2026-10-08。分支：`codex/qwen3-asr`。修复基准：`6a229431497111ca0237c81e2de6d440fa615071`；修复提交：`4fd4f54369e89e52aa1ca478d012d85ae6d8204a`。当前 Codex 独立完成修复、测试和复审。

整体审查发现的两项 P2 问题均已修复，针对修复差异的复审没有发现新的可操作问题。代码、安装说明和回归测试共改动 6 个文件；下述后续文档更新不改变运行代码。

## 修复行为

| 文件 | 修改及原因 |
| --- | --- |
| [asr_segments.py](../../../src/douyin_wiki/adapters/asr_segments.py) | 数词保护组包含所有对应词，包括零时长词；合并时记录组内是否已有正时长词。组已有时间范围时，零时长词保持在原组，避免 `百分之九十` 被拆开后使整条转写失败。整个组都是零时长时，继续并入一个有时间范围的邻组。 |
| [setup.py](../../../src/douyin_wiki/setup.py) | 模型准备命令使用当前 Python 安装的 scripts 目录中的 `hf`，通过标准库 `shlex.join` 处理路径、参数和通配符，支持未激活环境、路径含空格和项目外执行。 |
| [README](../../../README.md) | 三条模型准备命令使用 `uv run --no-sync hf download`，调用已安装的依赖并避免同步时改变当前 extras。 |
| [test_asr_segments.py](../../../tests/test_asr_segments.py)、[test_qwen3_asr.py](../../../tests/test_qwen3_asr.py) | 新增 9 个用例，覆盖 5 / 30 / 60 秒边界、百分数、小数、版本号、整个数词零时长及转写入口；断言完整原文区间、数字展示和实际时间范围。 |
| [test_setup.py](../../../tests/test_setup.py) | 新增 1 个用例，从项目外、空 PATH、含空格环境路径执行三条生成命令，并检查固定模型、revision 和字面通配符参数。测试使用只打印参数的 CLI，不下载模型。 |

## 验证证据

全部验证复用仓库外 `fresh-final` 环境，新增证据和 wheel 安装环境在：

```text
/Users/weisengao/asr/verification/2026-10-07-qwen3-asr-mv_d_dtr/review-fixes-2026-10-08-dtadiiwt
```

下表文件均位于这个目录。

| 验证 | 结果 | 证据 |
| --- | --- | --- |
| 数词故障先复现 | 6 failed / 3 passed；转写入口报 `数词替换不能跨越展示区间` | `numeric-prefix-red.log` |
| 数词修复后相关回归 | 219 passed | `numeric-prefix-green.log` |
| 命令故障先复现 | 1 failed；进程退出 127，找不到 `hf` | `prepare-command-red.log` |
| 命令修复后相关回归 | 70 passed | `prepare-command-green.log` |
| 完整非 live 回归 | **1276 passed / 2 deselected，177.24 秒** | `pytest-full.log` |
| Ruff 与差异检查 | `ruff check src tests`、`git diff --check` 通过 | `ruff.log`、Git 差异检查 |
| 既有对齐记录回放 | 17 个片段，分别按 5 / 30 / 60 秒分组，共 51 次通过；含 226 个 native 零时长词 | `recorded-alignment-replay.json`、`replay_recorded_alignment.py` |
| 新 wheel 独立安装 | 离线安装实际 wheel 和 Qwen extra；从 `site-packages` 导入；项目外、没有全局 `hf` 时，环境内 `hf --help` 和 CLI help 均退出 0 | `wheel-smoke.json`、`verify_installed_wheel.py`、`wheel-*.log` |
| wheel 成员比较 | 90 个成员；相对原产物只改变两份运行源码及 `METADATA` / `RECORD`，资源保持一致 | `wheel-manifest.json` |

新 wheel 的 SHA256 为 `bf459a18c3605dc5a155cec7bba1b601e3adcc2a75a906b707cfbf703d0bcf73`，保存在上述目录的 `dist/`。独立 wheel 环境没有 Torch；空模型缓存检查明确返回不可用，未导入模型框架或读取权重。

完整回归和 Ruff 命令在本分支 checkout 执行：

```bash
ASR_VERIFY_ROOT=/Users/weisengao/asr/verification/2026-10-07-qwen3-asr-mv_d_dtr
UV_PROJECT_ENVIRONMENT="$ASR_VERIFY_ROOT/fresh-final" uv run --no-sync ruff check src tests
UV_PROJECT_ENVIRONMENT="$ASR_VERIFY_ROOT/fresh-final" uv run --no-sync pytest -q -m 'not live'
git diff --check
```

## 复审结论与范围

复审比较 `6a22943..4fd4f54`，检查组的合并状态、零时长词归属、分段上限、原文连续覆盖、数字开关、异常拒绝、命令路径与引号、固定 revision、惰性体检，以及测试能否复现原故障。数词已经有正时长词时不会被并到前面的无关词；整个数词零时长时不会连接前后两个有时间范围的组。原时间字段没有改写，时间和长度检查保持原要求。

体检只生成命令并检查本地快照，不执行下载。模型准备命令不依赖当前目录或全局 PATH；三种模型的 revision 与允许下载的文件类型保持一致。两项修复均只使用已有依赖或 Python 标准库。

本轮新增验证使用合成对齐数据、既有对齐记录和实际安装包，没有重新运行模型推理或速度评测。真实素材质量、120 分钟耐久、首次安装性能例外及覆盖限制沿用[原验收记录](2026-10-08-qwen3-asr-acceptance.md)，不据此扩展质量结论。旧整体审查证据完整保留在同级 `branch-review-2026-10-08-ofsy2oqe` 目录。

主 checkout 的未提交内容、现用 `.venv`、已安装服务、真实 Vault 和模型缓存均未修改。GitHub 集成与生产升级继续按后续明确指令执行。

## 主线集成与清理（2026-10-08）

用户随后要求合并主线、提交、删除分支和清理冗余。合并提交 `194a943b4a5607c3e0adde7c28c28926b59fcff3` 的两个父提交为主线 `5a3ce45` 与开发分支最终提交 `5702667`，合并没有冲突。主仓库中 171 个源码、测试、脚本和安装入口文件与已审查分支逐字节一致；运行代码仍包含 `4fd4f54` 的两项修复。

合并结果使用仓库外环境验证，明确通过 `PYTHONPATH` 导入主仓库源码。完整回归为 **1276 passed / 2 deselected，176.00 秒**，Ruff 和差异检查通过。本阶段没有重新进行模型推理或性能测量。

已删除本地分支 `codex/qwen3-asr`，通过 Codex 归档工具移除其工作目录及生成缓存，保留可恢复 Git 归档。归档标识为 `/Users/weisengao/.codex/worktrees/qwen3-asr/douyin-wiki`。此前不被 Git 归档包含的 75 份执行材料和 8 张截图均已复制并核对字节一致；主线原有三份草案亦保存完整副本和 patch。合并时使用的临时 stash 已删除，其他 stash 未修改。当前活动 worktree 为主仓库和 `visual-search-phase1`。

执行证据位于：

```text
/Users/weisengao/asr/verification/2026-10-07-qwen3-asr-mv_d_dtr/main-integration-2026-10-08-c166m2vo
```

`pytest-main.log`、`ruff.log`、`main-import.json`、`source-and-backup-check.json` 记录主线验证；`before-merge.json`、`cleanup.json`、`main-before-merge/` 和 `worktree-ignored/` 保存清理范围与恢复材料。原真实验收和审查材料均保留。

主线测试的复现命令在 `/Users/weisengao/Documents/ChatGPT/douyin-wiki` 执行。开发环境原先绑定旧 worktree 的源码，显式指定源码路径后直接验证主仓库：

```bash
ASR_VERIFY_ROOT=/Users/weisengao/asr/verification/2026-10-07-qwen3-asr-mv_d_dtr
PYTHONPATH="$PWD/src" UV_PROJECT_ENVIRONMENT="$ASR_VERIFY_ROOT/fresh-final" uv run --no-sync ruff check src tests
PYTHONPATH="$PWD/src" UV_PROJECT_ENVIRONMENT="$ASR_VERIFY_ROOT/fresh-final" uv run --no-sync pytest -q -m 'not live'
git diff --check
```

本机三项 LaunchAgent 仍引用主仓库的 `.venv/bin/python`，配置文件哈希保持一致。现用环境缺少 `mlx-audio`，MLX / Transformers 也尚未升级为已验收版本；本轮保留该环境、服务和真实 Vault，生产依赖升级仍待单独执行。本阶段仅完成本地主线集成，GitHub 上的 `main` 尚未更新。
