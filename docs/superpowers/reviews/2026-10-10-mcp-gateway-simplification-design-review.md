# MCP 与 gateway 精简方案复核

日期：2026-10-10。状态：第 1 步复核完成，等待用户确认后进入第 2 步实施计划。

设计依据：[MCP 与 gateway 精简方案](../../design/2026-10-10-mcp-gateway-simplification-plan.md)。
代码基准：`main` / `6c4fb7b918e5b37e0faefa5fc8cbefdde82783bb`。包版本：`0.1.5`。现用 Web 版本：`0.2.47`。

## 结论

现有方案的功能范围与当前主线相符，可以进入实施计划。沿用已有设计，不另建功能方案。保留四种 MCP 导入、Gateway 协议 1 / 2、Web 阅读和管理、新库持久化与恢复、专题来源和笔记；按方案停用其他指定入口和实现，不增加旧业务数据迁移或替代生成流程。

需要在任务划分中明确一项顺序约束：删除模型配置、分析适配器和聊天实现时，同一个编码任务必须调整依赖它们的 Web / CLI 初始化、类型引用和测试夹具。不能先删除 `LLMSettings`、`adapters/chat.py` 等定义，等下一个大步骤才修改调用方。第 5 步包含维持保留功能可启动所需的调用方调整，第 6 步继续完成入口、界面和使用文档精简。

执行方式沿用用户指令：由当前 Agent 按十个大步骤推进，每步完成后说明结果和下一步，等待用户确认或修改意见。不委派子 Agent。

## 核对结果与实施约束

| 核对项 | 当前证据 | 对实施计划的要求 |
| --- | --- | --- |
| 共享选项 | `CaptureOptions` 包含 `retention`、`allow_long`、`approve_cloud_analysis`；短链共享、博主派发和合集预览 / 确认仍有整个 options 对象的比较 | 集中定义只比较 `retention` 的规则，保留旧授权参数的接收能力；四种入口均使用该规则 |
| 收藏派发 | `FavoritesStore.confirm()` 把活动任务映射为 ID 后直接复用，没有检查其处理选项 | 派发前保留候选任务及有效选项信息，检查全部候选后再写子任务和关联；补充后一个候选冲突时整批无新子任务的验证 |
| 批次事务 | 合集已经先收集 `plans` 再派发；博主在写事务内逐项检查及插入；收藏也在写事务内派发 | 复用已有锁和事务，统一“先检查全部候选，再发布子任务”，保留租约、取消保护和事件反馈；不重写共享采集模块 |
| 专题笔记 | `save_topic_note()` 创建 `kind="note"` 的 `TopicArtifact`，与自动成果共用表、Vault 写入和知识重建；`get_topic()` 返回 `topic` 与 `artifacts` | 优先将共用结构缩减为新库来源和笔记所需的实现，删除六种自动成果的运行路径；保留现有笔记接口、返回字段和 Vault 格式，不因类型或表名包含 artifact 就整套删除 |
| 公共分析数据 | Gateway 与资料发布仍引用 `adapters/llm.py` 的 `PROMPT_VERSION`；证据校验主要位于 `service_analysis.py` | 删除模型适配器前处理剩余公共引用，保留 Gateway 结果校验、标准分析模型和发布元数据；模型实现不因一个版本常量而继续保留 |
| Web 初始化 | `create_app()` 导入 `LLMSettings` 和两类模型适配器，并无条件创建聊天 provider 与上下文构造器 | 调整初始化和必要路由依赖与删除实现一起完成；运行 Web 不需要构造聊天模型或访问其密钥 |
| Web 任务与右栏 | `imports-collections.js` 等脚本同时承担新建、历史列表、选择、确认和重试；`chat-toggle` 实际控制整个右侧面板 | 按行为删除新建入口，保留任务管理、资源 URL 和加载顺序；保留文章信息、灵感和专题来源面板 |
| Web 停用接口 | 路由分布在 `webapp/app.py` 与 `webapp/operation_api.py`，聊天会话同时有读取、创建、删除和消息接口 | 完整列出所有停用路由与 HTTP 方法，统一返回 `410 Gone`；验证无任务 / 数据写入、无模型调用和无密钥访问 |
| CLI | `configure-model` 的 `--model` 有 `prompt=True`，命令还会保存配置、切换 provider 并可能重启服务 | 停用提示必须发生在交互、配置加载 / 写入、Service 初始化或服务操作之前；保留命令名、别名和可接受的旧参数，使用非零退出码 |
| 诊断 | `setup.py` 当前在判断是否需要 provider 前调用 `get_secret()` | 删除对话模型检查及专属密钥访问；保留 ASR、OCR、向量、授权和运行诊断 |
| 测试与浏览器验证 | `tests/conftest.py` 使用 provider 模式与假分析器；`tests/support/web.py` 和八个浏览器脚本依赖假聊天 provider | 用现有 Gateway 辅助函数调整通用夹具；保留混合功能文件中的有效回归；聊天浏览器入口改查停用行为与保留的右栏，不扩大为新聊天实现 |
| 隐藏事件桥 | `gateway monitor-events` 仍有 CLI 定义和 Gateway 指南引用 | 按方案保留兼容入口，业务命令统一停用时不误删它；本轮不修改其他客户端文件 |

源码工具注册与本聊天可用的抖库工具元数据均为 52 项，包含 `generate_topic_artifact`。仅移除该工具后，按当前集合预期为 51 项；发布验收仍使用实际 `tools/list`，不建立固定工具白名单。本次未对生产 MCP 执行业务工具调用。

## 现用安装与运行状态

已读取安装文件的启动路径和白名单配置字段，并查询 `launchctl` 状态；未读取模型密钥或执行连接测试。

| 对象 | 实际配置 / 状态 |
| --- | --- |
| Python 安装 | 仓库 `.venv/bin/python`，已安装 `douyin-wiki 0.1.5`，为引用当前源码目录的 editable 安装 |
| Worker | `com.local.douyin-wiki.worker`，使用仓库解释器执行 `-m douyin_wiki worker run --forever`；查询时正在运行 |
| Web | `com.local.douyin-wiki.web`，使用仓库解释器执行 `-m douyin_wiki web run --config-path ...`；查询时正在运行 |
| 定期维护 | `com.local.douyin-wiki.maintenance`，使用仓库解释器执行 `-m douyin_wiki maintenance run --apply`；已登记，查询时未运行 |
| Codex MCP | `/Users/weisengao/.codex/config.toml` 的 `mcp_servers.douyin-wiki`，使用同一仓库解释器执行 `-B -m douyin_wiki.mcp_server` |
| 配置文件 | `/Users/weisengao/Library/Application Support/douyin-wiki/config.toml`；当前 `analysis_mode="gateway"`，仍有 `[llm]` |
| 业务库 | `/Users/weisengao/Documents/Obsidian/抖库`；状态库路径为其下 `.douyin-wiki/state.sqlite3`，本轮未直接打开或修改 |
| 识别与检索 | 配置读取后的实际 ASR 为 `qwen3`、OCR 为 `auto`、向量 provider 为 `sentence-transformers`；配置文件中的旧 ASR `auto` 仅在内存中规范化，原文件未改写 |

代码变更必须在隔离工作区进行，避免现用 editable 安装直接执行开发中的实现。第 9 步按方案将经过验证的 wheel 安装到 `/Users/weisengao/Library/Application Support/douyin-wiki/runtime/.venv/`，再统一切换 Worker、Web、维护与 Codex MCP。所有配置切换并确认前保留仓库 `.venv`。

源码及安装包当前都是 `0.1.5`。建议在实施计划中将精简版定义为 `0.2.0`，便于区分本次有意改变入口行为的版本与旧版归档；这是待确认的版本安排，尚未修改版本文件。

## 本轮验证与边界

1. Git 核对：当前完整提交与方案一致；没有指向 HEAD 的 tag，所以尚未完成第 3 步的旧版版本标记。开始时 `docs/README.md` 已修改、原方案文件未跟踪，这两项均保持原样。
2. 纯内存选项探针：相同 `retention` 下，四组旧授权字段组合中三组与默认 options 不相等；媒体保留策略变化也不相等。该结果直接复现了方案指出的旧字段比较问题，没有创建任务或业务数据库。
3. 配置与安装核对：仅调用纯读取的配置加载与包元数据检查，确认 editable 安装来源和生效的识别配置；没有初始化 Service。
4. 本机只读 HTTP 检查：`GET /`、`GET /api/system/health`、`GET /api/system/analysis` 均为 `200`。健康信息显示 Web `0.2.47`、Worker 活跃、gateway 分析及 Qwen3 / auto OCR；主页列出的静态脚本与当前模板一致。
5. 本轮没有运行完整功能回归、浏览器行为测试或真实账号检查。历史文档中的测试通过数量未用作本轮验证结论；相应检查由后续编码任务、第 8 步和第 9 步完成。

本轮只新增此复核记录，没有修改运行源码、原方案、现有文档索引、生产配置或业务数据，没有提交、创建 tag、更新发行包、重启服务或准备新运行环境。

## 下一步

第 2 步：依据原方案和本记录编写可执行的实施计划，明确文件、接口、任务依赖、行为测试和完成标准，并列出发布、空库部署及旧版归档的具体操作。将上述调用方调整纳入对应删除任务，复用现有验证材料与 Gateway 辅助函数。计划写完后由当前 Agent 自查，再交用户确认；本阶段继续不修改运行代码。
