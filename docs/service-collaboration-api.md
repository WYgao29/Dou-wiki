# Service mixin 协作 API

`DouyinWikiService` 由 `service.py` 与六个 mixin 共同组成。方法按调用边界命名：

- 被其他 Service 文件调用的方法使用无前导下划线的协作名。
- 仅定义文件内使用的辅助方法保留 `_`；构造函数等 Python 特殊方法不受此约定影响。
- `*_locked` 表示原有锁前提，重命名不改变获取锁的责任。
- 协作方法并不自动成为 Web、CLI 或 MCP 接口；外部接口仍由各适配层显式定义。

`process_*` 调度入口、资料发布和证据处理等跨 mixin 操作使用协作方法。调用
`persist_entry_documents_and_bundle_locked` 前须持有 `entry_operations_locked()`；
不持锁的调用方使用 `persist_entry_documents_and_bundle`。Worker 发布资料还会在
SQLite 写事务中校验租约，避免已失去任务所有权的执行者覆盖新结果。

资料发布先在事务内写入完整 SQLite 投影，再调用文档写入回调，读取并构造返回值后提交 SQLite。
`VaultWriter.reversible_writes()` 保存文档、索引、关联页和日志的原内容；提交前任一步骤抛出
异常时回滚事务并恢复这些文件。提交后的读取异常不回滚已发布文件。
`write_entry_documents(..., commit_message=None)` 返回修改路径，
让调用方在 SQLite 提交成功后统一执行 Git 提交。创建中的系统提醒保留其数据库状态，
不随资料重写重新插入。

后续拆分文件时，应同时核对定义、跨文件调用、测试及字符串形式的 mock 名称。

## 合集批次与知识关系

`DouyinWikiService.collections` 是独立的 `CollectionService`。MCP 使用完整 Service 门面，Web 保留已有批次与知识关系管理，业务 CLI 已停用。Service 方法包括：`capture_douyin_collection`、`get_collection_inventory`、`set_collection_work_selection`、`confirm_collection_import`、`retry_collection_import`、`get_collection`、`list_collections`、`list_collection_imports`。清点适配器在构造参数 `collection_adapter` 注入；后台任务仍由 `process_claimed_job` 路由，不创建额外常驻服务。

`entry_documents_intact(entry)` 是跨 Service 文件及 Controller 共用的资料完整性检查；旧私有入口保留兼容。确认在 `BEGIN IMMEDIATE` 中核对所有所选作品、已有条目和活动 capture 的 `processing_key()`，再一起写入运行项关联及父任务状态。复用子任务不改写其 Gateway、批次来源或通知上下文。

发布清点检查点及最终知识投影时，校验当前租约、清点世代、mix_id 和稳定账号；新一轮不能被迟到结果覆盖。合集文档发布持有 `entry_operations_locked()` 和 Vault 锁，在 SQLite 写事务与 `reversible_writes()` 中一起更新索引、侧车及关系。事务成功后才提交 Vault Git；文件写入失败恢复原文件和数据库。

`Database.capture_parent_ids()` 和 `batch_child_ids()` 以运行表关系为准。完成、失败、取消或恢复子任务后刷新全部关联父任务及合集知识索引；取消保护计算仍活动的父任务及独立等待任务。`CollectionService.refresh()` 继续使用父任务和子任务的更新时间比较，不能用迟到汇总恢复已取消或删除的父任务。

`DouyinWikiService.capture_sharing` 在作品锁内、媒体处理前检查解析后的作品 ID。`join_if_active()` 持有条目操作锁和 SQLite 写事务，校验原 Worker 租约，保留等待任务的请求、Gateway 和 ID，并把收藏、合集及博主关联原子地改指执行任务。已经有媒体检查点的执行任务继续使用自身材料；完整条目仍优先复用。活动查重排除等待记录，避免批次再次关联到等待记录。

等待关系保存在任务 artifacts 的 `shared_capture_job_id`，新库查询结果也保留该字段。当前 schema 16 原生创建，旧版状态库不迁移。执行任务发布条目前，在其有效租约内合并仍有效等待者的灵感；释放租约后的结果同步不再执行媒体或分析。Worker 刷新、Service 查询和完成刷新均恢复持久等待关系，用 `Database.update_job()` 发布等待者终态及其原 Gateway 事件，取消或删除的等待者不复活。

Gateway 协议1和2的任务查询均保留结果中的 `shared_capture_job_id` 与 `shared_job_status`；简短回执不能过滤掉执行任务 ID，否则等待者所在 Agent 无法继续查询实际任务。

合集确认事务先记录 `monitoring`，随后刷新通过 `Database.update_job()` 发布真实终态。即使没有子任务，也会生成包含最终汇总的完成事件并替代待选择事件；确认后进程中断可由下一次刷新接续。未确认的部分清单可通过公开重试接口进入新清点世代，已确认或完整清单遵守原有重试边界。

`VaultWriter.write_collection(profile, works, entries)` 使用真实 entry 路径生成链接，`load_collections()` 验证侧车后用于知识重建。持久 `collections/collection_works` 与运行 `collection_runs/collection_run_items` 分开；重建不会恢复全部操作历史。实现及调用示例见[合集导入](collections-import.md)。

## Gateway 与停用边界

所有作品校正、分析与重新分析由 Gateway 完成。`get_analysis_context` 和提交方法保留原公开参数、协议 1 / 2、阶段和材料版本校验；Worker 继续等待提交并在租约内发布。`CaptureOptions.processing_key()` 比较 `retention`，并在临时媒体长期保留关键帧时增加该标记；`keep / discard` 下帧标记不改变行为，旧授权字段仍序列化但不影响共享，不改变整个模型的相等语义。新建请求由 `resolve_capture_options()` 解析可选的 `keep_keyframes` 并保存配置快照，批次子任务继承已保存的选项，不能在处理时用当前配置重新解释。

`generate_topic_artifact(topic_id, kind, *, provider=None)` 的异步 Service 签名保留，但在读专题或调用模型之前抛 `FeatureDisabledError`。专题笔记通过 `save_topic_note` 逐字保存，查询返回 `topic / artifacts` 字段仍保留，笔记 `kind="note"`。聊天执行器、数据库表和模型连接已删除。

## 专题废纸篓

`TopicTrashMixin` 通过 `trash_topic` 保存专题、来源选择及笔记快照，将专题目录移入 `.douyin-wiki/trash/topics/<trash_id>/`，再删除数据库中的专题投影；来源文章及其媒体不参与删除。`restore_trashed_entry`、`permanently_delete_trashed_entry` 和原有批量方法按废纸篓编号兼容分派专题操作；混合恢复先恢复文章，再恢复专题，保留来源关联。原文章废纸篓目录及格式不变，不增加数据库 schema 或 MCP 工具。

操作持有 `entry_operations_locked()`；文档索引发布单独取得 `locked()`，避免重复取得不可重入的文件锁。删除与恢复阶段写入持久日志，`recover_entry_trash_operations_locked()` 同时接续专题操作。恢复时过滤已经不存在的来源并返回提示，笔记原文保留。
