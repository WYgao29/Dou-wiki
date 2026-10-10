# 抖音合集导入

合集导入按“清点 → 选择 → 确认 → 逐条处理”执行。每个作品使用现有视频或静态图文采集流程；合集目录保存顺序及知识资料链接，同一作品跨合集、收藏、博主或单条导入仍复用同一份资料。

本功能已在 `codex/douyin-collection-import` 实现，主线集成和部署状态见[验收记录](superpowers/reviews/2026-10-10-douyin-collection-import-acceptance.md)。

## 网页操作

在导入中心粘贴合集分享文案，或进入 `/imports/collections`。支持 `https://www.douyin.com/collection/{mix_id}` 及 `v.douyin.com` 合集短链。粘贴只识别方式并预填表单；点击“开始清点合集”才创建读取任务，此时不下载作品。

读取完成后，新作品默认“待选择”。每件可导入且未入库的作品都需要选择或跳过；“全选全部页”作用于本轮全部作品，包括没有打开的页。已有完整资料直接关联。选择保存失败时保留重试入口，确认按钮等待保存成功。刷新页面根据地址中的 `job` 恢复批次和已保存的选择。

确认后页面显示新任务、复用任务、已在资料库、失败和取消的真实数量。Gateway 模式下，本地 Worker 完成转写后等待原会话 Agent 校正和分析；等待不代表整批完成。重新清点会创建新批次，不自动选择新发现作品，也不创建定期同步。

## CLI

后台 Worker 需要运行。`add` 返回 `job_id`；以下 `JOB_ID` 必须替换为实际值，等清点进入 `needs_selection` 后再选择。

```bash
uv run douyin-wiki collection add 'https://v.douyin.com/mTVp-S6IApM/'
uv run douyin-wiki collection inventory JOB_ID
uv run douyin-wiki collection select JOB_ID --all
uv run douyin-wiki collection confirm JOB_ID
uv run douyin-wiki collection inventory JOB_ID
```

只导入第 1、3 件时，先跳过全部，再选择这些编号：

```bash
uv run douyin-wiki collection select JOB_ID --all --skip
uv run douyin-wiki collection select JOB_ID --ordinal 1 --ordinal 3
uv run douyin-wiki collection confirm JOB_ID
```

编号是本轮清单的 `ordinal`；界面展示的集数 `episode_label` 可能不同。也可使用重复的 `--work-id` 指定稳定作品 ID。编号、作品 ID、`--all` 必须三选一。

`collection inventory` 不传 `--page/--limit` 返回全部清单；显式分页时 limit 为 1–1000。`collection retry JOB_ID` 在确认前重试失败清点，确认后只重试失败子任务。`collection list` / `show MIX_ID` 查询持久合集关系，`collection history` 查询导入历史。

## MCP 与 Gateway

| 工具 | 用途 |
| --- | --- |
| `capture_douyin_collection` | 提交分享文案，仅创建清点任务；接受 `options` 与原会话 `gateway_context` |
| `get_collection_inventory` | 查询批次；不传 page/limit 返回全部作品 |
| `set_collection_work_selection` | 保存 selected/skipped；ordinals、work_ids、all_works 三选一 |
| `confirm_collection_import` | 根据已保存选择创建或复用 capture 子任务；重复确认不重复入队 |
| `retry_collection_import` | 重试失败清点或失败子任务 |
| `get_collection` | 查询指定 mix_id 的持久成员与资料关联 |
| `list_collections` | 查询全部已读取合集 |
| `list_collection_imports` | 查询批次历史，默认最近 50 条 |

用户已经要求全部入库时，读取清单后直接写入 `decision="selected", all_works=true` 并确认，不重复询问选择范围。只导入部分作品时，先将其他可导入作品标记 skipped；决定保存在数据库中，不能只记在对话里。文章和未知类型不能导入。

合集新建子任务携带 `collection_context`，其中包含合集 ID、名称、父任务、作品 ID、编号和 `batch_silent=true`。Gateway 继续完成每条内容的校正和分析，父任务汇总后再回复用户。复用的独立任务或其他批次任务保留原 Gateway 与通知归属。两种 Gateway 协议都包含合集上下文，调用细节见[Gateway 指南](gateway-agents.md#合集选择式批量采集)。

## 部分清单、登录与失败

原生网页请求在抖库专用浏览器会话中执行；Python 读取网络原始 JSON，避免 19 位 ID 被 JavaScript 数字转换改变。分页按返回 cursor 前进，只有可信 `has_more=false` 且没有警告才表示完整。空/null 异常页、重复 cursor、数量差异或中途失败会保留已经读取的作品和原因。

部分清单确认必须显式传 `accept_partial=true`，网页对应“我接受清单可能不完整”。这项决定只授权本轮已读清单；后续新增作品需要新批次选择。重试清点采用新一轮记录，不将旧轮消失的作品计入本轮。

登录失效或切换账号进入 `needs_auth`，沿用授权中心的抖音通道。恢复原账号后可重试同一批次；换账号需新建批次。普通签名/403 错误不会一律被解释为登录失效。账号身份只用于运行保护，不进入合集知识侧车。

相同作品已有完整知识条目时直接关联；已有同选项活动采集时复用。处理选项冲突会拒绝整批确认，不创建一部分新任务。取消一个批次保留其他活动批次仍需要的共享任务；独立创建的任务仍按原任务执行。一个子任务失败不会删除其他已入库结果，全部结束后父任务显示 `completed` 或 `completed_with_warnings`。

## Vault 与维护

合集首次保存时创建 `collections/<安全名称>_<mix_id>/index.md`。改名更新标题而保留目录。索引按实际知识资料路径生成相对链接；合集本身不作为视频条目，也不移动既有视频资料。

`.data/collection.md` 是机器侧车，即用于恢复关系的结构化 Markdown。它保存合集及作品关系，不保存 Cookie、账号、原始接口响应、任务租约或全部导入历史。只有完整读取更新正式成员与顺序；部分新发现另行标记，之前完整清单的成员仍保留。

删除知识条目会解除对应资料链接，恢复后按稳定作品 ID 重新关联所有合集。重建先校验侧车 ID、目录和条目关系，非法数据不会先清空有效数据库；活动合集任务会阻止应用重建。删除导入历史不删除合集知识目录。

## 验证范围

2026-10-10 使用正式适配器读取《项目经理故事汇》：每页 2 件，9 页共 18 个作品；与同轮网页合集区域通过两次“点击加载更多”取得的全部 ID、集数和顺序一致。这是当前样本数量，不是固定上限。

独立测试 Vault 已实际完成第一条视频的下载、Qwen3 转写、Vision OCR、当前 Agent 校正与分析入库；再次导入复用已有条目，另一个作品使用明确的下载失败注入验证警告和计数。网页回归使用 105 件合成清单与真实 HTTP/API，覆盖全选、保存失败、刷新、键盘、亮暗主题和窄屏。真实合集内静态图文、全部 18 条媒体处理及生产 Vault 导入未包含在本次验收中。完整命令和证据见[验收记录](superpowers/reviews/2026-10-10-douyin-collection-import-acceptance.md)。
