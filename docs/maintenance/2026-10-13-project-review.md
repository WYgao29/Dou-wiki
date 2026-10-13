# 2026-10-13 项目与 Web 审查

本轮对全项目执行源码审查、完整离线回归和 20 个 Web 页面逐页检查，确认七项功能问题。Web 对齐问题已在源码修复，资源版本为 `0.2.51`；七项功能问题保留为待修复事项。本轮没有构建或安装发行包，现用独立 runtime 的 Web 仍为 `0.2.50`（本轮本机健康接口 HTTP 200 探针确认）。

审查基准为 `1514afde58dd5f7f9603e6a4e74eb26bb167d0ef`。Superpowers 未启用。全部修改、复现和浏览器检查均使用源码及临时 Vault，模拟外部请求；没有读取真实抖音账号或修改现用 Vault。

## 已确认的功能问题

`P1` 表示应优先修复的错误操作或恢复障碍，`P2` 表示特定流程下的功能错误。以下均有独立复现材料，普通回归通过不表示这些问题已经修复。

| 优先级 | 问题 | 主要位置 |
| --- | --- | --- |
| P1 | 旧任务详情响应覆盖新任务页面，操作指向旧任务 | `webapp/static/jobs.js:338` |
| P1 | 删除专题来源后，保留的笔记引用使知识索引重建失败 | `service_trash.py:216`、`service_maintenance.py:332` |
| P2 | 同步封面下载阻塞 Worker 事件循环和租约心跳 | `adapters/media.py:1628`、`:661` |
| P2 | 专题来源保存完成后重新打开已经离开的专题 | `webapp/static/app.js:1373` |
| P2 | 部分直接 SQL 状态更新遗漏完成事件或保留过期事件 | `database.py:2100`、`:804` |
| P2 | 合集外域缩略图被当前内容安全策略拒绝 | `webapp/static/imports-collections.js:70`、`webapp/app.py:424` |
| P2 | CDN 元数据把原始封面排在独立静态封面之前 | `adapters/media.py:429`、`:465` |

### 1. 旧任务详情可以造成错误操作

阻塞 `/api/jobs/old`，导航到 `/jobs/new` 并等待新任务显示，再释放旧请求。地址仍为 `/jobs/new`，标题和正文却变为旧任务；点击页面上的删除按钮并确认，实际请求为 `DELETE /api/jobs/old`。`loadDetail()` 在请求返回后直接替换页面，没有检查当前路由和请求是否仍然有效；旧请求的 404 分支也可以影响当前页面。

修复应校验请求序号、当前页面和任务 ID，并使过期的成功、失败和后续刷新都不再修改页面。复现没有删除真实任务，DELETE 完全由浏览器夹具拦截。

### 2. 正常删除会阻断索引重建

创建两篇有效资料，以两篇资料创建专题并保存用户笔记；首次重建预览无错误。删除其中一篇成功且没有警告，专题当前来源已经更新，但笔记的 `source_revisions` 仍记录被删除资料。再次预览出现 `成果关联资料不存在`，`rebuild_database_from_vault(apply=True)` 拒绝整库重建。

笔记的历史来源记录与当前资料集合使用了不同的生命周期约定：删除流程保留笔记，重建却要求所有历史引用都必须仍然存在。修复应保留用户笔记及其历史来源，同时让合法删除后的 Vault 可以重建；不能通过删除用户笔记消除错误。

### 3. 封面网络请求阻塞任务心跳

`YtDlpDownloader.download()` 是异步函数，其下载命令已使用 `asyncio.to_thread`，但末尾直接调用同步 `download_preferred_cover()`。该函数使用同步 `httpx.Client`，逐个请求候选 URL，超时设置为 60 秒。在它执行期间，同一事件循环中的其他下载、任务调度和租约心跳都无法运行。

离线复现用 0.25 秒的模拟封面请求替代网络，10 毫秒心跳的最大间隔变为 0.256 秒。慢请求持续或多个候选累计耗时较长时，可能错过默认 30 秒心跳，进一步造成默认 180 秒租约过期。修复应把整个同步下载移入线程，或改成异步请求，并保留现有候选顺序和文件写入约定。

### 4. 专题保存回调改变已经离开的页面

修改专题来源，延迟 PUT 请求，随后导航到 `/imports`，再释放保存响应。地址保留 `/imports`，页面状态却变成 `topic`。成功和失败回调都无条件调用 `openTopic(topic.id, false, ...)`，重新发起的详情请求因而绕过了先前导航的保护。

修复应仅在同一专题仍然可见时重新渲染；保存结果可以正常持久化，但不能用过期回调重开页面。

### 5. 任务状态与 Gateway 事件不一致

带 Gateway 上下文的博主清点任务全部跳过并确认后，数据库状态为 `completed`、子任务为空，未确认事件仍只有 `needs_selection`。`dispatch_creator_import()` 直接执行 SQL 更新，没有发布完成事件；`refresh_creator_parent()` 看到没有子任务会直接返回，之后也不能补发完成事件。

另一个直接更新分支中，取消无租约的 `needs_selection` 任务后，未确认事件同时包含 `needs_selection` 和 `cancelled`，旧事件没有标记为失效。按持久事件接续的 Gateway 因而可能继续提示选择，或无法收到结束通知。两个分支应使用与普通状态更新一致的事件发布和过期处理规则。

### 6. 合集 CDN 缩略图无法显示

合集前端直接把 API 的 `thumbnail_url` 写入 `<img src>`，但 Web 的内容安全策略（CSP，浏览器允许页面加载哪些资源的规则）只允许 `img-src 'self' data:`。实际抖音 CDN URL 属于外域。

在保留真实响应头的隔离浏览器中插入同样的外域封面，收到 `securitypolicyviolation`，指令为 `img-src`，图片 `naturalWidth` 为 0。现有合集浏览器测试使用 `data:` 图片，不能覆盖这一错误。应按现有本机媒体边界提供封面，或明确选择受约束的外域加载方案，并使用外域 URL 验证。

### 7. 独立封面优先规则没有在 CDN 流程生效

`_cover_urls_from_aweme()` 按 `origin_cover → cover → dynamic_cover` 提取 URL，`_info_from_aweme_detail()` 又把全部候选都标为 `id="cover"`。`download_preferred_cover()` 因此首先请求原始封面，只要成功就不会尝试真正的独立静态封面，并把它记录为 `douyin_cover`。

离线输入同时包含三种封面时，实际优先列表为原始、独立、动态，违反 README 的独立静态封面优先约定。修复应保留候选的真实类型，让独立封面优先且回退来源标记准确。

## Web 对齐修复

修改 `src/douyin_wiki/webapp/static/app.css`，统一以下几处实际错位：

- 操作页栏头不再额外缩进一层；手机正文去除额外的宽度扣减，使用页面自身的 16px 内边距。
- 任务列表使用一致的状态列，桌面 112px、手机 88px；长状态徽章可换行，各条任务标题和说明的起点一致。
- 专题列表和任务列表的行背景铺满主栏，行内元素只保留一层边距；废纸篓和手机专题详情去除重复缩进。
- 含文字标签的清单下拉框与旁边按钮按底边对齐，修复合集历史选择器与刷新按钮的高度错位。
- 复选框与单选框统一去除浏览器默认外边距，避免 16px 网格里的控件横向偏移。

`webapp/version.py` 更新为 `0.2.51`，使后续安装时浏览器请求新的资源版本；`CHANGELOG.md`、设计系统和文档索引记录本轮修改。

## 逐页检查范围

每个页面均检查 1440px 亮色、1100px 亮色、390px 亮色和 1440px 暗色，共 80 组页面渲染；长页面另外保存分段截图，查看首屏与后续内容。资料、专题、任务和批次使用有内容的临时数据。

| 页面 | 地址 |
| --- | --- |
| 资料库列表、专辑墙 | `/`、`/?view=gallery` |
| 视频详情 | `/articles/{entry_id}` |
| 专题列表、专题详情 | `/topics`、`/topics/{topic_id}` |
| 标签管理、废纸篓 | `/tags`、`/trash` |
| 导入中心、旧单条入口 | `/imports`、`/imports/single` |
| 博主、收藏、合集记录 | `/imports/creators?job=...`、`/imports/favorites?job=...`、`/imports/collections?job=...` |
| 任务列表、任务详情 | `/jobs`、`/jobs/{job_id}` |
| 设置首页、授权、分析、旧模型入口、系统 | `/settings`、`/settings/auth`、`/settings/analysis`、`/settings/model`、`/settings/system` |
| 页面不存在 | `/not-existing` |

修复后各组均未发现横向溢出、浏览器 JavaScript 异常或服务端 5xx。该结果验证页面和布局，不包含真实抖音授权、网络下载、模型推理或现用运行环境的功能验收。

## 验证与证据

- `uv run --no-sync ruff check src tests`：通过。
- `uv run --no-sync pytest -q -m 'not live'`：1551 passed、2 deselected，202.49 秒。
- `scripts/verify_readwise_shell.py`：通过，覆盖 1440 / 1280 / 1279 / 1100 / 960 / 800 / 390px 的栏头、侧栏开合、抽屉焦点与键盘操作。
- `scripts/verify_readwise_operations.py`：通过，覆盖 14 个操作路由，1440 / 1280 / 1100 / 800 / 390 / 320px，以及清单、任务、专题、标签和废纸篓的交互。
- `tests/test_documentation.py`、`tests/test_interfaces.py`、`tests/test_web_shell.py` 的最终相关检查：119 passed，5.25 秒；`git diff --check` 通过。

逐页截图和两个浏览器脚本在资源版本更新前执行，均使用本轮最终 CSS，因此截图页脚仍为 0.2.50。之后仅更新资源版本和维护文档；119 项最终相关检查验证该部分修改。

证据位于仓库外 `/Users/weisengao/Documents/ChatGPT/douyin-wiki-review-2026-10-13/`，保留本轮脚本和检查日志：

| 证据 | 内容 |
| --- | --- |
| `before/`、`after/` | 逐页原始截图、联系表与 `report.json` |
| `audit_pages.py` | 20 页面隔离渲染与尺寸检查脚本 |
| `reproduce_findings.py`、`findings-reproduction.json` | 删除/重建、状态事件、页面切换和封面顺序的最小复现 |
| `reproduce_media.py`、`media-reproduction.json` | 封面同步请求阻塞心跳的离线复现 |
| `reproduce_image_csp.py`、`image-csp-reproduction.json` | 保留实际 CSP 响应头的封面拒绝复现 |
| `full-regression.log`、`final-checks.log` | 离线回归与最终相关检查 |
| `regression/shell/verification.json`、`regression/operations/verification.json` | 现有浏览器回归结果和交互截图 |
| `live-web-probe.json` | 本轮现用 Web 的只读版本探针 |

本轮没有提交 Git、创建 PR 或变更现用安装环境。下一轮修复应优先处理两个 P1，并为上述功能复现增加正式行为回归。

用户随后授权修复全部问题、复审后提交并重启；该后续工作见[修复与复审记录](2026-10-13-project-review-fixes.md)。本文件保留初次审查的状态与证据。
