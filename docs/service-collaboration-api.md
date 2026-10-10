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

`DouyinWikiService.collections` 是独立的 `CollectionService`。Web、CLI 和 MCP 调用 Service 门面：`capture_douyin_collection`、`get_collection_inventory`、`set_collection_work_selection`、`confirm_collection_import`、`retry_collection_import`、`get_collection`、`list_collections`、`list_collection_imports`。清点适配器在构造参数 `collection_adapter` 注入；后台任务仍由 `process_claimed_job` 路由，不创建额外常驻服务。

`entry_documents_intact(entry)` 是跨 Service 文件及 Controller 共用的资料完整性检查；旧私有入口保留兼容。确认在 `BEGIN IMMEDIATE` 中核对所有所选作品、已有条目和活动 capture 选项，再一起写入运行项关联及父任务状态。复用子任务不改写其 Gateway、批次来源或通知上下文。

发布清点检查点及最终知识投影时，校验当前租约、清点世代、mix_id 和稳定账号；新一轮不能被迟到结果覆盖。合集文档发布持有 `entry_operations_locked()` 和 Vault 锁，在 SQLite 写事务与 `reversible_writes()` 中一起更新索引、侧车及关系。事务成功后才提交 Vault Git；文件写入失败恢复原文件和数据库。

`Database.capture_parent_ids()` 和 `batch_child_ids()` 以运行表关系为准。完成、失败、取消或恢复子任务后刷新全部关联父任务及合集知识索引；取消保护只计算仍活动的父任务。`CollectionService.refresh()` 继续使用父任务和子任务的更新时间比较，不能用迟到汇总恢复已取消或删除的父任务。

`VaultWriter.write_collection(profile, works, entries)` 使用真实 entry 路径生成链接，`load_collections()` 验证侧车后用于知识重建。持久 `collections/collection_works` 与运行 `collection_runs/collection_run_items` 分开；重建不会恢复全部操作历史。实现及调用示例见[合集导入](collections-import.md)。
