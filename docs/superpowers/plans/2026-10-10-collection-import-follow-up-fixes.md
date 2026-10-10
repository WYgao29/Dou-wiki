# 共享采集接续与取消修复计划

> 当前 Agent 按 `executing-plans` 逐项执行；用户已授权修复全部三项意见，包括 P3，不委派子 Agent，不重复索要确认。

**目标：** 修复共享等待记录的网页接续、批次取消与重试回执，保留原任务 ID、请求和会话。

**依据：** [既有合集设计](../specs/2026-10-10-douyin-collection-import-design.md)、[上一轮修复](../reviews/2026-10-10-collection-import-review-fixes.md)及仓库外审查 `douyin-wiki-validation/2026-10-10-collection-fix-review-LF2qCq/review.md`。用户已通过“修复”确认审查提出的具体行为。

**实现：** 沿用 artifacts 中的共享关系与运行表实际执行者关联。等待记录取消以其原批次上下文查找；解除共享关系时原子更新 artifacts 和 result。网页保留记录状态，用实际执行者呈现阶段、暂停原因与跳转入口。

**技术：** Python、SQLite、FastAPI、原生 JS、pytest、Playwright。

## 范围与约束

- 基准 `09d426526767de96e164afe5c053c5dd5f29d20a`，在既有 `codex/douyin-collection-import` 工作树执行。
- 保持 CLI/MCP 名称、参数、Service 公开调用、Web 路径及负载兼容，不修改 schema15 或 Vault 格式。
- 保留原请求、Gateway 和共享执行者选项；批次取消保护其他仍活动的用户。
- 不修改生产 Vault、浏览器 Profile、主线现用环境，不合并、推送或部署。
- 验证日志和临时数据在仓库外；相关回归按任务执行，全部产品修改结束后执行一次完整非 live 检查。

## 重点边界

1. 合集、收藏、博主取消时，只撤销自己创建的等待记录，其他父任务及独立请求仍可完成。
2. 取消后完成刷新或重启不能把等待记录恢复为成功或再产生完成事件。
3. 等待者单独重试的 queued/downloading 回执不再指向旧执行者；普通重试保留已有检查点和必要结果。
4. Gateway 和授权暂停在网页显示真实阶段，列表筛选与详情入口一致；点击跳转使用执行者 ID。
5. 已取消或删除的等待记录不继续显示共享执行者的操作提示，原请求和会话不变。

## Task 1：取消批次所属等待记录

文件：`capture_sharing.py`、`service.py`、`tests/test_capture_sharing.py`。

接口：新增 `CaptureSharing.cancel_batch_followers(parent_id)`，在现有条目操作锁内使用 `Database.request_cancel()`；批次聚合仍关联实际执行者。

- [x] 扩展回归：三个批次来源、保留独立执行者、停止等待记录及取消后无完成事件。
- [x] 运行新增用例，原实现三个来源均保留 monitoring，证明失败。
- [x] 按原批次上下文撤销共享等待记录，再执行现有执行者保护逻辑。
- [x] 运行共享采集与批次生命周期回归，65 项通过；记录结果并提交。

## Task 2：重试时原子清除旧共享回执

文件：`database.py`、`service.py`、`tests/test_capture_sharing.py`。

接口：为现有 `Database.requeue_job(..., result=None)` 增加可选结果参数，旧调用不改变行为；`retry_job()` 仅在解除共享关系时删除共享 result 字段。

- [ ] 回归 queued 和延迟合成下载期间的公开协议2查询，以及普通重试结果保留。
- [ ] 运行新增断言，预期旧执行者字段仍出现。
- [ ] 在同一数据库更新中清除共享 artifacts 与 result，保持 expected_updated_at 检查。
- [ ] 运行共享、重试与 Gateway 回归，预期全部通过；记录结果并提交。

## Task 3：网页共享执行者入口

文件：`operation.py`、`web_operation.py`、`webapp/static/jobs.js`、共享采集/API/浏览器测试。

接口：`effective_job_status(job)` 仅用于展示与网页筛选；共享等待记录 `next_action` 指向原执行者。`WebOperationService.present()` 获取执行者的阶段、进度及暂停原因，保留等待者 ID 与原请求。

- [ ] 将审查复现纳入正式 HTTP 回归，覆盖 Gateway、授权、运行中与取消状态；增加真实浏览器点击执行者入口用例。
- [ ] 运行新增用例，预期网页仍指向等待者、暂停被当作运行中、入口不可见。
- [ ] 更新展示与筛选，列表和详情均提供执行者链接，取消记录保持自身状态。
- [ ] 运行 API 与浏览器相关回归，预期全部通过；记录结果并提交。

## 最终验证与交付

- [ ] 复用本轮审查探针，预期三个问题全部消失，图文共享检查继续通过。
- [ ] Ruff、`git diff --check`、完整 `uv run --no-sync pytest -q -m 'not live'`、浏览器验证。
- [ ] 构建新版 wheel/sdist，并核对实际运行文件及独立安装后的接口。
- [ ] 当前 Agent 复核所有修改，更新使用文档、修复记录及验证材料，提交开发分支，保留待集成状态。

## 执行记录

前置检查：工作树干净；已安装 Web/Worker/maintenance 仍引用主线 `.venv/bin/python`。沿用上一轮相同源码的1400项完整验证和当前审查61项回归，不重复执行基线全量检查。

Task 1 与 Task 2 只共享 Service 入口，新增接口互不依赖；Task 3 使用既有 shared_capture_job_id/shared_job_status，取消与重试后的展示由正式回归验证。

用户规则优先于技能默认：日志及过程记录放在仓库外；当前 Agent 直接实现和复核；用户明确授权三项修复，不延后 P3；全部代码修改后完成一次全量检查。
