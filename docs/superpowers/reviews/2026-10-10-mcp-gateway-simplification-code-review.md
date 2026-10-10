# MCP / Gateway 精简代码自查

日期：2026-10-11。结论：运行实现符合已确认方案；本轮修正两类文档问题，没有未解决的严重或重要问题，可以进入第 8 步发布前验收。

这是当前实现作者的一轮自查，没有独立审查者，也没有委派子 Agent。使用 `requesting-code-review` 的需求、接口、异常处理、恢复、测试和交付检查要求；作者自查不能替代独立审查。

## 范围与依据

- 旧版基准：`6c4fb7b918e5b37e0faefa5fc8cbefdde82783bb`，标记 `legacy/douyin-wiki-0.1.5-2026-10-10`。
- 已实现代码：`ec291dded4a8a288d1119052b25afad7ffdd9be2`，分支 `codex/mcp-gateway-simplification`，相对基准共 116 个改动文件。
- 需求：[精简设计](../../design/2026-10-10-mcp-gateway-simplification-plan.md)、[设计复核](2026-10-10-mcp-gateway-simplification-design-review.md)、[实施计划](../plans/2026-10-10-mcp-gateway-simplification.md)及 Task 1～7 的验证记录。
- 本轮只修改 README、架构说明、排障指南、计划状态，并新增本记录；源码、测试、依赖和静态资源没有新变化。
- 外部证据目录：`/Users/weisengao/Documents/ChatGPT/maintenance/douyin-wiki/mcp-gateway-simplification-2026-10-10/`，下文简称 `TASK_OUT`。

## 已核对的实现

共享兼容性集中使用 `CaptureOptions.processing_key()`，保留旧输入字段，实际只比较媒体保留策略。博主、收藏、合集都先检查全部候选再写入任务和关系；取消与重试仍保留其他调用者的需求、原任务 ID 和 Gateway 信息。

Gateway 提交仍检查阶段、材料版本、证据和并发更新，Worker 发布仍校验有效租约。视频、图文、重新分析和媒体重新获取分别保留正确阶段，普通重试及重启复用保存材料。Qwen3、ASR 分段和对齐、OCR、向量、媒体适配器没有本轮代码改动。

数据库原生初始化为 schema 16，建表处于写事务内；并发初始化有等待和二次版本检查。旧业务状态库先报错，不执行迁移。专题仅保存笔记，返回的 `topic` / `artifacts` 和空模型用量字段保留；新库来源、删除恢复、版本标记和重建继续工作。

停用路由直接返回 `410`，不依赖必填业务 payload 或实体存在；Origin 和主机检查继续使用原规则。Web 的历史批次、选择、确认、取消、重试、共享执行者跳转及信息 / 灵感 / 来源面板保留。删除聊天与新建实现后，剩余 JS 没有对已删除函数的引用，模板的脚本加载顺序未改变。

## 五项重点核对

| 重点 | 实现与行为证据 | 判断 |
| --- | --- | --- |
| 后面的候选冲突时不能部分派发 | `database.py:1974`、`favorites_store.py:293`、`collection_store.py:211` 的预检与写事务；`test_later_retention_conflict_does_not_dispatch_partial_batch` 检查无新子任务、父状态及确认状态不变 | 通过 |
| 提交后恢复、材料变化及过期租约 | `service_analysis.py` 的版本校验及并发更新；`test_gateway_resume_after_restart_reuses_material_checkpoints`、协议 1 / 2 的过期材料测试、`test_lost_lease_cannot_publish_entry`、共享重启与重试测试 | 通过 |
| 停用接口无副作用 | 六个新建入口和十个模型功能方法 / 路径组合；既有测试分别覆盖旧 payload、空对象、空 body、不存在 ID，以及任务 / 笔记 / 下载不变和无密钥访问 | 通过 |
| 无对话模型配置可运行 | `AppConfig` 只接受 Gateway，新 TOML 无 `[llm]`；Service / Web / doctor 不构造对话 provider；删除模块的 import 检查通过；独立发行包启动留给 Task 9 实测 | 源码检查通过，包验收待下一步 |
| 笔记、来源、删除恢复与重建 | `test_topic_note_roundtrip_and_rebuild_without_chat_tables`、笔记来源变更 / 并发测试、Web 废纸篓恢复及合集重建回归；`Database.restore_entry_dependencies()` 无聊天表依赖 | 通过 |

## 对外接口核对

| 项目 | 核对结果 |
| --- | --- |
| Service | 104 个公开方法及构造入口的参数结构、关键字参数和默认值相同；`analysis` 注入参数继续接收但不执行旧 provider；专题生成方法保留参数并立即报告停用 |
| 其他存储与任务接口 | 187 个保留的公开方法参数结构不变；仅删除七个聊天存储方法和旧收藏基线迁移方法，均属于已确认删除范围 |
| MCP | 实际 STDIO `tools/list` 的 51 项名称和 inputSchema 与旧版快照逐项一致，仅删除 `generate_topic_artifact`；协议默认值仍为 1，指南建议新调用显式选择 2 |
| CLI | 69 个命令及别名仍注册；52 个业务命令 / 别名由 51 个无业务副作用的停用处理函数提供说明，说明中的 MCP 名称均存在；20 个运行函数及共用辅助函数与 Task 4 结束时相同 |
| HTTP | 两个路由模块的 99 组方法 / 路径保留；16 组有意停用的行为已单独记录，保留功能的选择 / 确认 / 取消 / 重试接口未删除 |
| 静态资源与运行构建 | 24 个原静态文件路径全部保留，脚本顺序不变；两个 console scripts、Python 范围、构建与依赖声明仅有根包版本变化 |

`task-8-contract-audit.py` / `.json` 记录上述静态清单；152 个源码、正式测试及脚本 Python 文件解析和 import 检查通过。接口清单属于静态核对，行为判断同时使用实际 MCP、HTTP、Worker 和浏览器证据。

## 发现及处理

### 严重问题

没有发现新的严重问题。

### 重要问题：现行文档仍承诺旧业务数据及任务接续，已修正

审查前 README 的识别说明仍写“旧逐字稿、校正稿……继续可读”，README、架构说明和排障表还指导恢复旧版待确认 / 复核任务。schema 16 会拒绝旧状态库，这些说明与已确认的空库方案冲突，会让用户误判升级和恢复范围。

现已将 [README](../../../README.md)、[架构说明](../../architecture.md)和[排障指南](../../troubleshooting.md)统一为：旧配置字段只兼容识别设置，旧业务数据和旧任务不迁移或接续；新库保存材料及任务仍正常恢复；公开复核 / 确认状态和工具继续保留。文档与实际 schema 检查及 Gateway 行为一致。

### 次要问题：开发命令会隐式同步环境，已修正

三份现行文档中部分运行与验证命令仍用裸 `uv run`。它在执行前会检查并更新项目环境，不能保证保留本任务已经核对的依赖环境；这是 [uv 的明确行为](https://docs.astral.sh/uv/concepts/projects/run/)。

现行运行命令统一加 `--no-sync`，显式依赖准备使用锁文件，并说明已安装服务使用独立 runtime。Qwen extra 说明也不再引导用户追加一次只含单个 extra 的同步。历史验证段落、命令和截图保持原样；计划顶部及流程表同步为当前进度。

这两类修改只涉及文档，没有新增运行行为，因此没有为文案新增测试，也没有重复完整功能回归。

## 验证依据与限制

- Task 6 完整非 live 回归：`1503 passed, 2 deselected in 189.83s`；随后受影响范围定向检查 `164 passed in 57.47s`，Ruff 和 13 个 JS 模块语法检查通过。shell / operations / reader 及笔记浏览器记录通过，覆盖桌面、手机、主题、右栏、旧链接及批次管理。
- Task 7 在此基础上只调整四个说明 / 版本常量、根包版本、锁文件根版本及文档断言；文档、包 metadata、Web 版本和 Vault 初始化 / 保留原文件共 `6 passed in 2.20s`。实际 STDIO 检查 51 项工具无业务调用，未创建 Vault。
- 本轮重新核对测试输入、成功退出码、日志 SHA256 和版本间的常量变化。代码及测试仍与 Task 7 一致；修正后的 13 份现行文档 95 个链接可读，五组历史记录未改变。完整验证依据及内容哈希见 `task-8-verification.json`、`task-8-doc-audit.json` 和既有 `task-6/7-checks.json`。
- 本轮没有运行真实抖音账号、下载真实模型或媒体、连接对话模型，也没有进行新包安装、主线集成、生产切换或用户 Vault 清理。包内资源、独立环境 origin、0.2.0 完整回归和当前版本浏览器验收属于 Task 9。

## 本轮未判断的事项

新发行包安装及四个生产入口的实际版本由 Task 9 / 10 验证，尚未作成功判断；旧版归档由 Task 11 执行。真实网络和账号检查需要显式选择，不用离线结果替代。旧业务数据迁移、外部 Agent 自动唤醒和替代专题生成已由用户排除，未新增实现或兼容要求。

隐藏的 `gateway monitor-events` 继续保留已有单页兼容行为，不把它改为新的事件接入方式；`llm_stats` 等已有返回字段保留兼容，不代表恢复模型执行。

## 结论与下一步

可以进入发布前验收。没有未处理的严重 / 重要问题，也没有新增运行代码问题；两个实际文档问题已修正，接口核对及已有验证证据支持当前实现。此结论不表示已经完成发行包或生产部署验收。

下一步为第 8/10 步、Task 9：正式回归、现行浏览器检查、独立环境 wheel / sdist 验收，并提供可检查的本机部署清单；待用户确认后执行。
