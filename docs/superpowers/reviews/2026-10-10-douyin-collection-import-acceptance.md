# 合集导入开发与验收记录

日期：2026-10-10（Asia/Shanghai）。用户已确认[设计](../specs/2026-10-10-douyin-collection-import-design.md)并要求按其开发，当前 Agent 完成实现与整分支审查，未委派子 Agent。操作规范见[合集导入](../../collections-import.md)，任务记录见[实施计划](../plans/2026-10-10-douyin-collection-import.md)。

## 交付状态

- 基准：`54ae500fd3abaffaefecb2af812ec6881b1e0e35`。
- 分支：`codex/douyin-collection-import`；运行代码最终提交 `afc2eb5`，后续仅更新使用文档、方案和验收记录。
- 隔离工作区：`/Users/weisengao/.codex/worktrees/douyin-collection-import/douyin-wiki`。
- 已完成 Web、CLI、8 个 MCP 工具、正式读取适配器、schema15、共享 capture 关联与持久 Vault 合集索引。保持现有 CLI/MCP/Service 入口兼容；Web 静态资源版本为 `0.2.47`，Python 包仍为未发布的 `0.1.5`。
- 本轮保留分支及工作区；尚未合并、推送或部署。检查时原 `main` 仍为上述基准且工作树干净，现用 `.venv` 未同步依赖。生产 Vault 未导入本合集。

## 正式检查

全部命令在工作区独立 `.venv` 执行，正式回归使用隔离 Vault 与假适配器。

| 检查 | 已确认结果 |
| --- | --- |
| `uv run --no-sync ruff check src tests` | All checks passed |
| `uv run --no-sync pytest -q -m 'not live'` | 1379 passed / 2 deselected，191.02秒；最终代码 `afc2eb5` |
| 账号、浏览器、原始 JSON、分享及迁移相关测试 | 67 passed |
| `uv run --no-sync pytest -q tests/test_collection_ui.py` | 最终2项通过，覆盖105件清单；专项历史状态断言先失败后通过 |
| `git diff --check` | 无空白错误 |
| `uv build --out-dir <仓库外目录>/dist` | sdist 与 wheel 均成功 |
| wheel 包成员和独立安装 | 98个成员，新 Python/JS/模板/CSS 存在且与当前源码一致；无探针、Profile、SQLite或缓存；CLI 合集命令可加载，52个 MCP 工具中包含新增8项 |

最初独立环境有2项可选依赖缺失失败，安装 asr/embeddings extras 后针对这两项验证通过，生产环境不变。第一次功能完整回归1374项通过、2项失败；两处都是旧测试仍期望 schema14，实际迁移已经为15。更新预期并补充3项异常输入回归后，完整集为1379项。原日志保留，不将这些运行记作全部通过。

## 真实合集清点

用户短链解析为 `/collection/7651028428363139126`，即小吕泽哥的《项目经理故事汇》。页面随后导航到第一集时仍按该 mix_id 读取。

正式 `DouyinCollectionAdapter` 在专用已登录 Profile 中执行网页原生 GET，`count=2` 依次请求 cursor 0、2、4、6、8、10、12、14、16，最终返回 cursor18、has_more0。Python 原始 JSON 解析取得18个不同ID，没有完整性警告；首条 `7644758067783852901`，末条 `7690534336239652111`。集数来自 `item_id_to_episode`，没有把详情的 `updated_to_episode=18` 当作作品总数。

同轮另从可见 `[data-e2e="aweme-mix"]` 网页区域独立读取，不筛选 API 已知ID、不包含推荐视频。等待实际卡片出现后，两次点击“点击加载更多”取得18件，按钮消失；全部 ID、集数和顺序与适配器结果一致。网页核对先遇到隐藏副本及卡片尚未加载的问题，调整验证脚本的可见区域和等待条件后完成；该 DOM 选择器只用于验证，不是运行读取器。

## 真实单视频 Gateway 路径

测试位于仓库外独立 `gateway-vault`，使用上述正式读取清单回放批次输入。第一批只选择清单第1件，其余17件 skipped；真实下载、Qwen3-ASR 和 Vision OCR 生成上下文后，当前 Agent 检查完整逐字稿与 OCR、校正2个片段，提交协议2分析及来源证据。

结果为 `dy-7644758067783852901` 实际知识条目，capture 和合集父任务均 completed；批次 new_tasks1、in_library1、failed0。合集索引中的相对知识链接已经解析并检查文件存在。该测试使用 hash embedding，验证范围是媒体、Gateway 和知识发布，不衡量生产语义检索质量。

第二批选择第1、2件：第1件直接关联已有条目，第2件注入明确的 `ExternalToolError("development fault injection: download unavailable")`。结果 completed_with_warnings、in_library1、failed1、数据库条目总数1；已成功结果与合集链接保留。失败注入不表示第2件真实视频在平台上不可下载。

## 浏览器与审查修复

正式浏览器测试运行本地真实 HTTP/API 与隔离 SQLite，使用105件合成清单。证明粘贴仅预填、全部页选择与数据库一致、保存中的确认禁用、失败后重试保存、明确确认后建立105个子任务、刷新恢复新任务数量、键盘x更改选择及无 pageerror。亮/暗主题通过应用控件切换；1440px 桌面与390px窄屏截图已检查。手机底栏在正常文档流中，侧栏过渡结束后没有遮挡或横向溢出。

当前 Agent 重点审查了短链容器身份、分页精度和完整性、世代与租约、跨入口共享取消、Gateway 路由、文件/数据库发布及删除恢复。修复情况：

| 问题 | 修正与证据 |
| --- | --- |
| Cookie hash 被当作账号，正常会话轮换可能拒绝原批次 | `0238ec2` 改用自身主页稳定身份；指纹仅检测变化。账号轮换/切换回归先失败后通过，正式18件读取复验 |
| 异常端口、短链网络错误或作品URL可能中断错误边界 | `0238ec2` 将前两者转为分享错误；异常作品跳过并保留其他作品和部分提示。3项回归先失败后通过 |
| 空白历史保留上一批数量及链接 | `66432c1` 统一清空状态、清单、数量和任务入口；真实浏览器断言先失败后通过，重新选择仍恢复原批次 |
| 清点刷新后当前历史选项仍显示旧状态和数量 | `afc2eb5` 随当前清单更新该选项；105件“待选择作品”断言先失败后通过 |

这些修复和迁移预期更新后，未发现阻塞开发交付的问题。审查由当前 Agent 执行，没有独立人员或子 Agent 复核。

## 证据与范围

原始材料保存在 `/Users/weisengao/Documents/ChatGPT/douyin-wiki-validation/2026-10-10-collection-import-KP1YCT`；工作临时目录原为 `/tmp/douyin-collection-import.KP1YCT`。复制保留证据时没有包括安装环境。脚本和部分任务路径记录保留原临时路径，重现时需调整 `OUT`，生产配置不应改写。

- `adapter-inventory.json`、`dom-comparison.json`、`dom-compare.log`：正式多页与网页全部顺序对照。
- `probe-results.json`、`response-*.json`、`functions.json`：最初原生接口验证；这些私有原始数据不进入源码或发行包。
- `e2e-*.json`、`e2e-analysis-result.log`、`gateway-vault/`：真实媒体、校正分析、发布及复用/失败结果。
- `final-tests*.log`、`ui-*.log`、`screenshots/`：回归、失败到通过和视觉检查。
- `build.log`、`package-check.json`、`package-runtime.json`、`package-cli.log`、`dist/`：构建及独立安装检查。

最终 wheel SHA-256：`366f1c631889a104cdea75b27d9060b1741a2b052968e8a2273084c87b2459ae`。它包含最终运行代码，后续文档提交不改变 wheel 内的源码。

本次真实清单全为视频；图文、文章识别及不支持处理通过合成回归验证，真实合集内图文尚未实测。没有处理全部18条媒体，也没有向生产 Vault 入库；不能将本轮结果解释为该合集已经全部保存。
