# 项目文档

安装、运行、CLI 和发行包说明见根目录 [README](../README.md)，功能变更见 [更新日志](../CHANGELOG.md)。

| 文档 | 用途 |
| --- | --- |
| [仓库维护入口](../AGENTS.md) | 维护 Agent 的目录职责、权威记录和验证边界 |
| [项目结构与处理流程](architecture.md) | 入口、分析模式、模块职责、数据落点与开发验证 |
| [MCP 自助接入入口](agents/README.md) | 与客户端无关的配置步骤、参数契约和仓库外验收记录 |
| [Gateway Agent 接入指南](gateway-agents.md) | MCP 接入、校正分析、批量选择与事件接续 |
| [收藏批量导入](favorites-import.md) | MCP 新建、Web 已有批次管理与历史验证边界 |
| [合集批量导入](collections-import.md) | 合集链接、跨页选择、逐条处理、知识关系与验收范围 |
| [授权与任务排障](troubleshooting.md) | 登录、作品类型、待处理任务和失败重试 |
| [Service 协作 API](service-collaboration-api.md) | Service 与 mixin 的命名、锁和发布约定 |
| [Web UI 设计系统](web-ui-design-system.md) | 当前实现的界面令牌、组件和交互约定 |
| [Readwise 重设计方案](web-ui-redesign-readwise.md) | 已完成的设计规格与历史实施记录；当前界面约定见设计系统 |
| [静态原型](prototypes/readwise/README.md) | 三栏布局、列表与阅读页的演示和截图 |

## 近期完成

[Qwen3 审查问题修复](maintenance/2026-10-12-qwen3-review-fixes.md)处理附加词组失败影响主组修复、停顿中点越界漏掉合法切点两项回归。新增 13 项用例、438 项相关回归、171 项独立安装包测试、2000 组切点边界检查和之前四条视频的模型结果回放通过；复审未发现新的需修复问题，独立 runtime 已更新。

[四条 Qwen3 转写失败修复](maintenance/2026-10-12-qwen3-failure-recovery.md)处理短文字配长静音、重复词错位、按词数误算局部重试预算和词末切点截断语音，并覆盖 180 秒取整临界值。334 项相关回归、158 项独立安装包测试和四条完整真实音轨复验通过；独立 runtime 已更新，四个原任务完成校正、分析和入库。博主批次现为 39 条完成 / 1 条 Live Photo 失败；原有 35 条完成任务及排除项保持不变。

[视频详情页材料](maintenance/2026-10-12-video-detail-materials.md)增加默认折叠的完整逐字稿和可打开、复制的本地视频 URL。118 项相关测试、阅读器专项检查、桌面 / 手机视觉与实际 MP4 打开 / 拖动检查通过；实际 wheel 在独立环境的 15 项功能验收通过，90 个运行文件与源码一致。本机 Web 已更新到 0.2.50，运行依赖、配置和 LaunchAgent 保持原有设置。

[MCP / Gateway 精简审查修复](superpowers/reviews/2026-10-11-mcp-gateway-review-fixes.md)已修正旧状态库拒绝前的 Vault 写入和收藏目录接续提示。包 0.2.1 / Web 0.2.49 的实际 wheel 验证通过，沿用现有配置、schema 16 和运行依赖；完整初次回归的环境版本失败已在新安装环境复验，141 项安装包回归通过。用户确认后，独立 runtime 已更新为 0.2.1，Web / Worker 已重启，维护定时任务已重新注册；24 个静态资源、16 组停用路由和新 MCP 51 项工具契约通过。当前聊天已重连，0.2.1 工具元数据与实际 doctor 均确认使用独立 runtime，本轮修复及部署验收完成。

[MCP / Gateway 精简方案](design/2026-10-10-mcp-gateway-simplification-plan.md)及[实施计划](superpowers/plans/2026-10-10-mcp-gateway-simplification.md)已实现并集成本地 `main`，包 0.2.0、Web 0.2.48 已部署到仓库外独立 runtime。业务通过 MCP 创建，只使用 Gateway，Web 保留批次与知识管理；现有 Vault 已按确认范围初始化为 schema 16 空库。CLI 业务、Web 新建、聊天、模型配置和专题自动生成已停用。作者自查、1503 项非 live 回归、153 项安装包流程回归和八个浏览器入口通过；实际服务、停用路由、诊断和新 MCP 51 项工具契约验证通过，见[验收及部署记录](superpowers/reviews/2026-10-10-mcp-gateway-simplification-acceptance.md)。本聊天重连后的实际 doctor 和进程检查也确认使用新 runtime，旧环境的 MCP 进程为零；固定旧提交的 0.1.5 源码、wheel、恢复说明和 SHA256 已在仓库外归档并验证。十个大步骤全部完成，本次 worktree / 分支和临时构建环境已清理，执行记录和历史截图已另存；实际交付与恢复路径见同一验收记录。

[Qwen3 ASR 单引擎替换](superpowers/specs/2026-10-07-qwen3-asr-design.md)及两项审查修复已集成本地 `main`，语音识别只使用 Qwen3，旧运行实现与依赖声明已删除。主线完整回归为 1276 passed / 2 deselected；两条真实音轨、7200 秒耐久、离线、下游回放与安装包检查的证据和范围见[验收记录](superpowers/reviews/2026-10-08-qwen3-asr-acceptance.md)及[修复与复审记录](superpowers/reviews/2026-10-08-qwen3-asr-review-fixes.md)。用户已接受新安装环境首次约 39 秒准备、首次 10 分钟任务约 93 秒；后续新进程加载约 3.6 秒、10 分钟任务约 57.6 秒，原始超时记录保留。

开发分支已删除，worktree 已归档，旧草案、日志、截图和验收证据保留，执行结果见[主线集成与清理](superpowers/reviews/2026-10-08-qwen3-asr-review-fixes.md#主线集成与清理2026-10-08)。

现用依赖升级与服务重启已完成，Qwen3 实际转写、OCR、向量模型、MCP 和本地模型服务检查通过，见[本机部署验收](superpowers/reviews/2026-10-08-qwen3-asr-review-fixes.md#本机依赖升级与服务验收2026-10-08)。旧逐字稿继续读取；本期不做旧数据重转、热词或上下文提示，独立术语仓库留待后续方案。早期候选与失败记录见[可行性审查](superpowers/reviews/2026-10-07-qwen3-asr-feasibility.md)及[架构修订](superpowers/specs/2026-10-07-qwen3-asr-alignment-revision.md)。

[收藏夹 API 读取设计](superpowers/specs/2026-10-04-favorites-api-reading-design.md)与[实施计划](superpowers/plans/2026-10-04-favorites-api-reading.md)已确认，实现及搜索反馈修复已集成主线。目录与指定夹使用网页原生请求及原始 JSON；真实账号目录、单夹、多夹和主动分页只读验收已完成，数量差异仍要求明确接受不完整清单。作品搜索筛选本次已读清单，刷新保留当前任务。使用契约见[收藏批量导入](favorites-import.md)。

[抖音合集导入方案](superpowers/specs/2026-10-10-douyin-collection-import-design.md)及两轮审查修复已集成本地 `main`，包括 Web/CLI/MCP、共享采集和持久合集关系。完整非 live 回归1415项通过；合并后136项相关测试及 Ruff 检查通过。本地开发分支已删除，工作树已归档，历史截图已外置保存并核对，见[主线集成与清理](superpowers/reviews/2026-10-10-collection-import-follow-up-fixes.md#主线集成与清理2026-10-10)。

正式读取器的18件多页清单与网页 ID、集数、顺序一致；独立 Vault 的一条真实视频已完成 Gateway 校正、分析与入库，复用及故意失败也已验证。使用规范见[合集导入](collections-import.md)，真实样本范围见[验收记录](superpowers/reviews/2026-10-10-douyin-collection-import-acceptance.md)，修正行为与证据见[首次审查修复](superpowers/reviews/2026-10-10-collection-import-review-fixes.md)和[共享采集后续修复](superpowers/reviews/2026-10-10-collection-import-follow-up-fixes.md)。

本机 Web 和 Worker 随后已重启，合集网页及接口检查通过，按现有配置新建的 MCP 连接提供52个工具，包含8个合集工具。当前聊天仍缓存旧工具清单，需要客户端重新连接；检查范围与材料见[本机服务重启记录](superpowers/reviews/2026-10-10-collection-import-follow-up-fixes.md#本机服务重启与入口检查2026-10-10)。


## 开发中的功能


上述「近期完成」中的数字、52 工具和服务重启属于 0.1.5 及更早的历史部署记录；0.2.0 实际工具清单移除 `generate_topic_artifact`，客户端升级后需重新连接。历史 Qwen3、收藏与合集验收内容继续保留。

[独立 AI 术语仓库与热词接入方案](superpowers/specs/2026-10-08-ai-terms-design.md)已根据当前 Qwen3 主线编写，处于方案审阅阶段。建议先建立独立词库、Agent 维护规则与确定性数据导出，再接入抖库的主识别和局部识别；当前尚未建库或修改运行代码。

[Gateway token 优化规格](superpowers/specs/2026-10-03-gateway-token-optimization-design.md)与[实施计划](superpowers/plans/2026-10-03-gateway-token-optimization.md)已确认并在 `codex/gateway-token-optimization` 实现；现有四工具可显式选择协议 2，覆盖 OCR 投影、增量校正、证据引用与简短回执，默认保留旧协议。调用规范见 [Gateway 指南](gateway-agents.md#紧凑协议gateway_protocol2)；主线集成与真实客户端切换尚未执行。

[画面检索方案](design/visual-search-indexed-plan.md)仍用于独立分支 `codex/visual-search-phase1` 的开发；当前主线尚未集成此功能。该方案保留原有设计范围，实际进度以对应分支为准。

## 维护记录

[2026-10-13 项目与 Web 审查](maintenance/2026-10-13-project-review.md)记录 20 个页面的逐页检查、对齐修复和七项已复现的功能问题；后续七项修复、59 项新增行为回归、1610 项完整离线回归、212 项独立 wheel 测试及本机部署状态见[修复与复审记录](maintenance/2026-10-13-project-review-fixes.md)。

[2026-10-01 仓库审计](audits/2026-10-01-repository-audit.md)与[文件清单](audits/2026-10-01-repository-inventory.json)记录审计开始时的基准和判断依据；后续改动不覆盖原清单。[清理与 Agent 规则实施计划](superpowers/plans/2026-10-01-repository-cleanup.md)记录所选范围、验证与回退；首批及通用接入框架已提交为 `7ccfba3`，O1–O6 及仓库外回退工具的两项修复已验收。[2026-10-02 收尾记录](superpowers/plans/2026-10-01-repository-cleanup.md#提交收尾2026-10-02)说明本轮提交、发行包同步、证据位置和提交后的回退边界。

[本地残留清理](superpowers/plans/2026-10-01-repository-cleanup.md#本地残留清理2026-10-02)记录随后确认并完成的 Pi 旧提示、源码缓存、首批材料外置与空目录清理；历史清单保留，现用环境及其他 worktree 不动。首批归档的现行位置和恢复入口以该节所链接的仓库外执行记录为准。

## 文件维护

正式测试保存在 `tests/`，按功能命名。已完成的实施记录应标明历史基准，不作为当前使用规范；保留必要的审计结论、实施结果和回退材料，新的临时脚本、日志及回退副本放在仓库外的维护目录。重要修复进入更新日志与回归测试。临时验证使用独立 Vault，产物放在仓库外；可重新生成的发行包放在 `dist/`。`.test-*/` 和 `docs/screenshots/` 继续忽略，既有脚本生成的验证材料验收后应外置保存；无法原样再生的历史比较记录保留或整组归档，不直接删除。静态原型保留演示页面、捕获脚本、说明与图标许可，字体复用应用文件。
