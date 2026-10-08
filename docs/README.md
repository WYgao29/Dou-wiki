# 项目文档

安装、运行、CLI 和发行包说明见根目录 [README](../README.md)，功能变更见 [更新日志](../CHANGELOG.md)。

| 文档 | 用途 |
| --- | --- |
| [仓库维护入口](../AGENTS.md) | 维护 Agent 的目录职责、权威记录和验证边界 |
| [项目结构与处理流程](architecture.md) | 入口、分析模式、模块职责、数据落点与开发验证 |
| [MCP 自助接入入口](agents/README.md) | 与客户端无关的配置步骤、参数契约和仓库外验收记录 |
| [Gateway Agent 接入指南](gateway-agents.md) | MCP 接入、校正分析、批量选择与事件接续 |
| [收藏批量导入](favorites-import.md) | 网页、CLI/MCP 操作和验证边界 |
| [授权与任务排障](troubleshooting.md) | 登录、作品类型、待处理任务和失败重试 |
| [Service 协作 API](service-collaboration-api.md) | Service 与 mixin 的命名、锁和发布约定 |
| [Web UI 设计系统](web-ui-design-system.md) | 当前实现的界面令牌、组件和交互约定 |
| [Readwise 重设计方案](web-ui-redesign-readwise.md) | 已完成的设计规格与历史实施记录；当前界面约定见设计系统 |
| [静态原型](prototypes/readwise/README.md) | 三栏布局、列表与阅读页的演示和截图 |

## 近期完成

[Qwen3 ASR 单引擎替换](superpowers/specs/2026-10-07-qwen3-asr-design.md)及两项审查修复已集成本地 `main`，语音识别只使用 Qwen3，旧运行实现与依赖声明已删除。主线完整回归为 1276 passed / 2 deselected；两条真实音轨、7200 秒耐久、离线、下游回放与安装包检查的证据和范围见[验收记录](superpowers/reviews/2026-10-08-qwen3-asr-acceptance.md)及[修复与复审记录](superpowers/reviews/2026-10-08-qwen3-asr-review-fixes.md)。用户已接受新安装环境首次约 39 秒准备、首次 10 分钟任务约 93 秒；后续新进程加载约 3.6 秒、10 分钟任务约 57.6 秒，原始超时记录保留。

开发分支已删除，worktree 已归档，旧草案、日志、截图和验收证据保留，执行结果见[主线集成与清理](superpowers/reviews/2026-10-08-qwen3-asr-review-fixes.md#主线集成与清理2026-10-08)。生产环境依赖升级仍需单独执行。旧逐字稿继续读取；本期不做旧数据重转、热词或上下文提示，独立术语仓库留待后续方案。早期候选与失败记录见[可行性审查](superpowers/reviews/2026-10-07-qwen3-asr-feasibility.md)及[架构修订](superpowers/specs/2026-10-07-qwen3-asr-alignment-revision.md)。

[收藏夹 API 读取设计](superpowers/specs/2026-10-04-favorites-api-reading-design.md)与[实施计划](superpowers/plans/2026-10-04-favorites-api-reading.md)已确认，实现及搜索反馈修复已集成主线。目录与指定夹使用网页原生请求及原始 JSON；真实账号目录、单夹、多夹和主动分页只读验收已完成，数量差异仍要求明确接受不完整清单。作品搜索筛选本次已读清单，刷新保留当前任务。使用契约见[收藏批量导入](favorites-import.md)。

## 开发中的功能

[Gateway token 优化规格](superpowers/specs/2026-10-03-gateway-token-optimization-design.md)与[实施计划](superpowers/plans/2026-10-03-gateway-token-optimization.md)已确认并在 `codex/gateway-token-optimization` 实现；现有四工具可显式选择协议 2，覆盖 OCR 投影、增量校正、证据引用与简短回执，默认保留旧协议。调用规范见 [Gateway 指南](gateway-agents.md#紧凑协议gateway_protocol2)；主线集成与真实客户端切换尚未执行。

[画面检索方案](design/visual-search-indexed-plan.md)仍用于独立分支 `codex/visual-search-phase1` 的开发；当前主线尚未集成此功能。该方案保留原有设计范围，实际进度以对应分支为准。

## 维护记录

[2026-10-01 仓库审计](audits/2026-10-01-repository-audit.md)与[文件清单](audits/2026-10-01-repository-inventory.json)记录审计开始时的基准和判断依据；后续改动不覆盖原清单。[清理与 Agent 规则实施计划](superpowers/plans/2026-10-01-repository-cleanup.md)记录所选范围、验证与回退；首批及通用接入框架已提交为 `7ccfba3`，O1–O6 及仓库外回退工具的两项修复已验收。[2026-10-02 收尾记录](superpowers/plans/2026-10-01-repository-cleanup.md#提交收尾2026-10-02)说明本轮提交、发行包同步、证据位置和提交后的回退边界。

[本地残留清理](superpowers/plans/2026-10-01-repository-cleanup.md#本地残留清理2026-10-02)记录随后确认并完成的 Pi 旧提示、源码缓存、首批材料外置与空目录清理；历史清单保留，现用环境及其他 worktree 不动。首批归档的现行位置和恢复入口以该节所链接的仓库外执行记录为准。

## 文件维护

正式测试保存在 `tests/`，按功能命名。已完成的实施记录应标明历史基准，不作为当前使用规范；保留必要的审计结论、实施结果和回退材料，新的临时脚本、日志及回退副本放在仓库外的维护目录。重要修复进入更新日志与回归测试。临时验证使用独立 Vault，产物放在仓库外；可重新生成的发行包放在 `dist/`。`.test-*/` 和 `docs/screenshots/` 继续忽略，既有脚本生成的验证材料验收后应外置保存；无法原样再生的历史比较记录保留或整组归档，不直接删除。静态原型保留演示页面、捕获脚本、说明与图标许可，字体复用应用文件。
