# 抖音合集导入开发方案

日期：2026-10-10（Asia/Shanghai）。状态：设计已获用户确认，开发中。本文描述目标设计，不是部署记录。

本方案基于本地 `main` 提交 `9044600f9bbb48a970b796db256de3abb6c578b0`。用户于 2026-10-10 回复“sp 可以，按这个开发吧”，确认设计并授权开发；[实施计划](../plans/2026-10-10-douyin-collection-import.md)沿用该授权，由当前 Agent 执行，未授权子 Agent 或生产入库。

## 1. 目标与范围

用户粘贴一个抖音合集链接或分享文案后，抖库能够识别该合集，清点其中的公开作品，并将选定作品分别送入现有采集流程。用户明确要求“全部入库”时，Gateway 根据完整清单选择全部可导入作品并提交，不再重复询问已经明确的导入范围。

每条视频仍有自己的原始记录、逐字稿、校正稿、分析、知识条目及原作品链接。合集保存名称、原链接、作品顺序和条目关联；同一视频在单条、博主、收藏或多个合集导入中只保留一份知识资料。

首期范围按现有导入功能覆盖 Web、CLI 和 MCP，作为待审阅的范围假设：

- 输入支持已验证的 `/collection/{mix_id}` 长链及 `v.douyin.com` 短链；完整分享文案沿用现有 URL 提取规则。
- 支持清点、全部选择、逐条选择、确认导入、状态汇总、失败项重试和再次手动清点。
- 视频及能明确识别的静态图文继续使用现有采集能力；文章和未知类型显示为暂不支持，不能误认成视频。
- 保存可在 Vault 中恢复的合集关系，支持已有条目的关联、删除和恢复。
- 首期只处理用户给出的单个合集，不遍历作者的全部合集、不建立定时同步，也不调整 ASR、OCR、分析模型或现有条目目录。

再次手动清点生成新批次：已有资料直接关联，正在采集的作品按第 7 节复用任务，新发现作品默认待选择。历史选择不自动授权本次新增作品。

## 2. 已验证事实与验证边界

Profile 指保存浏览器登录状态和配置的目录。

| 证据 | 本轮已确认的结果 | 对设计的影响 |
| --- | --- | --- |
| 用户短链 `https://v.douyin.com/mTVp-S6IApM/` | HTTP 跳转到 `/collection/7651028428363139126?previous_page=app_code_link` | 可在进入浏览器前取得稳定的合集编号 `mix_id` |
| 已登录 Chrome 的合集页 | 小吕泽哥的《项目经理故事汇》；初次显示 6 集，点击两次“点击加载更多”后显示第 1–18 集，共 18 个不同视频 ID，加载按钮消失 | 已验证网页清单可分页展开；这 18 个链接是当前网页样本，不是未来固定作品数 |
| 浏览器导航 | 合集页面转到第一集 `/video/7644758067783852901` | 合集目标必须保留在解析结果中，不能只看浏览器最终 URL |
| 普通 HTTP 请求 `mix/aweme` | HTTP 403，正文为 `Blocked by ArgusSecurityPlugin Uifid Not Found` | Cookie 与普通 HTTP 请求不能替代网页请求环境 |
| 当前 `DouyinShareResolver.classify_target()` | 对用户短链返回 `unknown` 和合集长链 | 需要增加合集分类及独立解析结果 |
| 当前收藏读取器 | 已有原生网页函数识别、原始响应读取、请求关联、超时和跨页处理 | 可沿用读取机制与约束；其两个收藏端点和参数不能原样用于合集 |
| 已安装服务配置 | Web、Worker、maintenance 均引用本仓库 `.venv/bin/python` | 后续开发和打包验收不得在现用环境盲目同步依赖 |

18 个网页链接的首条为 `7644758067783852901`，末条为 `7690534336239652111`。清单顺序不等于发布时间顺序，不能用视频 ID 或发布时间重新排序。

GitHub 参考采用已读取的代码版本：

- [douyin-downloader 合集接口与网页请求环境](https://github.com/jiji262/douyin-downloader/blob/9874f413b2c1f8ad6e731b26e3e05f402fc964f8/core/api_client.py)：`get_mix_detail()`、`get_mix_aweme()`、`_request_json_gated()`。
- [douyin-downloader 合集分页](https://github.com/jiji262/douyin-downloader/blob/9874f413b2c1f8ad6e731b26e3e05f402fc964f8/core/mix_downloader.py)：按返回的翻页位置（cursor）继续读取，区分请求失败与空清单。
- [F2 合集编号提取示例](https://github.com/Johnserf-Seed/f2/blob/f6be8c0ffba9a127075bbeafe4838716650b6325/docs/snippets/douyin/mix-id.py)。

阶段 A 已于 2026-10-10 在抖库专用 Profile 验证：详情原生函数接受 `{mixId}`；带 `mixScene` 的列表函数接受 `{mixId,cursor,count}`。通过 count=2 主动读取9页，最终 cursor=18、has_more=0，共18个不同作品，首末ID与网页核查一致。列表提供 `item_id_to_episode` JSON 字符串；详情的 `statis.updated_to_episode` 是已更新集数，尚未证明为可见作品总数，因此首期不将它填入 reported_count。原始记录存放仓库外。

**尚未验证**：合集内图文及正式适配器的逐条入库。探针结果不代替运行代码、故障恢复与入库验收。

## 3. 路线选择

| 路线 | 结果与代价 | 判断 |
| --- | --- | --- |
| 已登录网页发出原生请求，Python 解析原始 JSON | 使用网页自身的请求校验；获得作品 ID、分页结束信号和媒体类型；与现有收藏读取方式一致 | 推荐，先通过第 11 节的只读验证 |
| 仅从合集区域读取 DOM 卡片 | 已证明本样本能读出 18 个链接；对完整性、类型和作品数量的证明较弱，页面变更也会影响选择器 | 用于独立核对，不作为完整清单的默认读取器 |
| 接入第三方完整下载器或自行维护 HTTP 签名 | 需要跟随第三方的下载、签名、账号和任务实现；普通 HTTP 路径已被当前接口拒绝 | 本期不采用 |

目标端点为同源 GET：

```text
/aweme/v1/web/mix/detail/?mix_id={mix_id}
/aweme/v1/web/mix/aweme/?mix_id={mix_id}&cursor={cursor}&count={count}
```

这些是业务参数示意，完整请求由抖音网页产生。不能修改已签名 URL 的 cursor 后重放，也不能试调用无法确认用途的网页导出函数。网页函数的 JavaScript 参数与最终 HTTP 参数可能不同，必须先实测再固化适配。

原生函数只负责触发请求。Python 从网络响应读取原始字节并解码，避免 19 位 ID 经过 JavaScript Number 后改变。若主动读取和翻页不能验证，暂停后续运行代码开发，更新本设计；不以首屏预取或推荐视频补充清单。

## 4. 架构与模块职责

采用与收藏功能相同的独立 Controller 组织方式。Controller 是负责协调一类任务的服务对象。`CollectionService` 挂在 `DouyinWikiService.collections`，Service 门面提供薄包装；新增 `collection_adapter=None` 构造注入参数便于正式测试。现有 mixin 不重新拆分。

| 新文件 | 单一职责 |
| --- | --- |
| `adapters/collection_api.py` | 两种接口的原始 JSON 解码、字段校验和数据归一化；不操作浏览器或数据库 |
| `adapters/collection_browser.py` | 识别已核实的网页只读函数、关联请求响应、超时和清理监听；不决定导入选择 |
| `adapters/collection.py` | Profile 锁、登录与账号核对、合集元信息、串行分页、作品去重和每页检查点 |
| `collection_models.py` | 合集元信息、作品、分页结果、清单和选择状态模型 |
| `collection_store.py` | 合集与运行记录的 SQLite 存储、事务内选择、任务分发和引用查询 |
| `collections.py` | 清点、确认、重试、父任务汇总及合集索引发布 |
| `webapp/static/imports-collections.js` | 合集导入表单、清单选择、分页和结果展示 |

需要接入的现有文件：

- `adapters/share.py`：增加合集编号识别及 `ResolvedCollection`；保留 `ResolvedShare`、单条和博主方法的现有形状。
- `database.py`：初始化新增 schema、租约校验、合集事件上下文及统一批量任务引用查询。
- `service.py`、`worker.py`：构造注入、`collection_import` 调度、汇总刷新、取消及授权恢复。
- `operation.py`、`localization.py`、`web_operation.py`：合集任务名称、进度、导入历史、重试和隐藏任务。
- `mcp_server.py`、`cli.py`、`webapp/operation_api.py`：新增入口；旧名称、路径、参数和状态码保持兼容。
- `auth_guidance.py`、`web_auth.py`：新增 `collection` 授权范围，继续使用已有 `douyin` 登录通道。
- `gateway_protocol.py`：协议 1、2 都能携带 `collection_context`；现有提交工具和协议默认值保持兼容。
- `vault.py`、`service_maintenance.py`、`service_trash.py`：合集索引、机器侧车、重建、删除及恢复。
- `webapp/templates/app.html`、`webapp/static/app.js`、`webapp/static/shared.js`：导入方式、分类分流、视图和脚本加载；样式优先复用现有清单组件。
- `README.md`、`docs/architecture.md`、`docs/gateway-agents.md`、`docs/web-ui-design-system.md`、`CHANGELOG.md`：实现后更新实际使用契约。

跨 Service 文件调用的方法使用无前导下划线的协作名；锁、SQLite 发布及可逆文件写入继续遵守 [Service 协作 API](../../service-collaboration-api.md)。本期不把收藏、博主和合集重构为通用导入框架。

## 5. 输入、读取与完整性

### 5.1 解析目标

新增 `extract_collection_id(url) -> str | None` 和 `resolve_collection(share_text) -> ResolvedCollection`。返回 `mix_id`、原始 URL、规范合集 URL 和完整 HTTP 跳转链。

`classify_target()` 增加 `collection` 分类。检查每次跳转中的合集编号；同一链中出现不同合集编号时拒绝。先识别合集目标，再处理随后出现的单条视频 URL，避免降级为第一集。域名限制和最多 8 次 HTTP 跳转沿用现有规则。普通作品的 `mix_info` 不改变用户提交单条作品时的导入范围。

非合集链接传入合集专用入口时明确拒绝。不能把无上下文数字自动当成合集编号，也不以标题或作者搜索结果推断编号。

### 5.2 浏览器读取

使用 `config.browser_profile_dir` 及与已有媒体适配器相同的 Profile 锁文件。响应监听先于导航安装，整个清点使用同一页面和账号，逐页串行读取；检查点保存后才请求下一页。

仅接收来源为 `https://www.douyin.com`、完整路径为上述两个端点、方法为 GET、请求 `mix_id` 匹配的响应。列表还要匹配请求 cursor、count 和本次页面世代；不能消费其他合集预取、上一轮读取的迟到响应、无关详情或推荐请求。

默认列表 `count=20`，最多 500 页。单页总等待预算 20 秒，最多 3 次尝试；瞬时网络、5xx 或限流重试间隔为 1、2 秒。明确登录或验证码阻挡进入 `needs_auth`；一般 403、签名拒绝和响应异常不能都归为“需要登录”，更不能归为空合集。

### 5.3 字段与顺序

原始字节在 Python 使用 `json.loads()` 解码。ID 必须是 1–32 位 ASCII 数字字符串或原始 JSON 整数，统一保存为字符串；拒绝布尔值、浮点数和指数表示。可用的字符串与整数 ID 字段冲突时，不发布该记录，并将清单标记为不完整。

合集元信息包含 `mix_id`、名称、规范 URL、原始 URL、可核实的作者与封面、可选 `reported_count`。数量字段只采用阶段 A 核实的字段，不把当前集数、播放次数或任意嵌套 count 当作作品总数。

作品包含 `work_id`、类型、规范 URL、标题、作者、发布时间、时长、封面、可用性及 `position`。`position` 是本次接口清单的首次出现顺序；平台集数或标题只有从已核实字段/合集区域取得时才另存 `episode_label`，不能把内部选择编号冒充真实集数。

只解析接口指定的作品数组，不递归抓取所有带 `aweme_id` 的对象。若作品带有明确的合集编号，它必须与目标一致；反向证据出现时排除该项并保留错误说明。视频和图文按现有明确结构区分，有可信 ID 的未知类型保留占位但不可选择导入。

### 5.4 分页完成判据

成功页要求 HTTP 200、明确 `status_code=0`、可信的分页字段和作品数组。`has_more` 仅接受 bool、整数 0/1 或字符串 `"0"`/`"1"`；缺失不表示结束。下一 cursor 只取响应的 `cursor/max_cursor`，不自行计算。

- 成功页明确 `has_more=false`，且前面没有协议或记录异常，才能确认分页完成。
- 已核实总数与不同作品 ID 数量不符时，仍标记不完整并给出两项数量。
- `has_more=true` 但 cursor 缺失、不变或形成循环，停止并保留已读内容。
- 数组缺失、为 null 或异常空页，但成功状态、继续标志及 cursor 推进均可信时，记不完整提示并继续；null 不能当作可信空清单。
- 达到 500 页、超时、无法确认结束或丢弃异常记录，均为不完整；第一页硬失败无可信内容时直接失败。

`complete` 仅表示本轮接口清单通过上述检查，不能证明私密、已删除或当前账号不可见作品都已取得。网页“加载更多”消失只作为独立核对信号。

### 5.5 中断与重新清点

每次清点尝试分配新的 `inventory_generation`，从 cursor `0` 重新读取。作品行以 `(parent_id, work_id)` 幂等更新，另用 `inventory_generation` 标记最后一次看到该作品的读取轮次；当前清单只统计本轮标记的行，避免把前后两次变化中的合集拼成虚假的完整清单。

检查点保存作品、请求与响应 cursor、数量、完整性原因和读取世代，并调用 `Database.assert_job_claim()` 校验 Worker 租约。旧 Worker 的迟到回调不能更新新一轮清单。开始新一轮前保存上一轮清点摘要及完整性说明；同一作品的新快照替换当前行并保留其明确选择，消失作品的旧行用于历史而不计入本轮数量，新作品仍待选择。此处保留的是批次历史与最后一次作品快照，不承诺保存每次读取的完整原始响应。

部分结果不推断作品移出合集。只有一轮完整清点才更新“最近完整清单”的成员集合；历史已入库关联保留，不据此删除知识资料。

## 6. 持久化与 Vault

新增四组存储，合集远端 ID `mix_id` 直接作为合集主键：

| 表 | 保存内容与约束 |
| --- | --- |
| `collections` | 合集元信息、固定 `folder_path`、最近完整清点时间；`mix_id` 唯一 |
| `collection_works` | 合集与作品关系、最近已核实顺序、是否属于最近完整清单及资料关联；主键 `(mix_id, work_id)` |
| `collection_runs` | 父任务、目标、读取世代、清点快照、确认状态及 `accept_partial`；主键 `parent_id`，解析前 `mix_id` 可为空 |
| `collection_run_items` | 本批次作品快照、首次顺序、pending/selected/skipped 选择、子任务和条目 ID、读取世代；主键 `(parent_id, work_id)`，子任务 ID 建索引 |

持久合集关系属于可重建知识资料；运行和选择记录属于操作历史。清理导入历史不删除合集关系，重建知识索引也不重建旧任务和事件。为了查询博主批次实际复用的子任务，现有 `creator_run_items` 增加可空的 `child_job_id`；在分发事务中同步写入，不改变博主选择规则。历史关联只在父任务结果、作品身份与子任务一致时回填；无法确定的活动引用保留保护状态，不能据此取消子任务。当前 SQLite 数据库结构版本 14 在该基准上新增为 15；执行前若主线版本变化，先调整迁移序号，不能覆盖更高版本。

Vault 使用新增的兼容目录，不迁移已有视频或博主资料：

```text
collections/<首次名称的安全文件名>_<mix_id>/
  index.md
  .data/collection.md
```

首次建立后固定 `folder_path`，合集改名只更新展示名称。`index.md` 按合集顺序链接已有条目的真实相对路径，展示未导入、处理中、失败或已不在最近完整清单中的作品，不复制逐字稿、分析稿或媒体。最近完整清单的成员与顺序在完整读取后一次更新；部分读取中的新发现单独标注，不替换上次完整清单，也不将未读到的旧成员标成移出。

机器侧车是供程序读取和重建索引的元信息文件。它使用 `type: collection-machine-data` 和独立版本 `1`，保存合集元信息与作品关系，不保存 Cookie、签名、浏览器账号、原始接口响应或任务租约。普通 `EntryRecord` 与来源机器侧车格式保持兼容。合集索引不作为新的视频知识条目，不混入原话证据。

合集元信息、持久关系与对应文件按既有发布契约更新：先取得 `entry_operations_locked()`，在同一 SQLite 事务中更新投影，使用 `reversible_writes()` 写侧车与索引，成功后提交事务，再提交 Vault Git 记录。文件失败须恢复文件与事务；网络读取不在此锁内等待。

重建先验证所有合集侧车的 ID、相对路径、版本及条目对应关系，再替换合集知识投影；异常侧车阻止应用重建，现有资料不被清空。活动合集批次存在时沿用活动导入保护。删除条目时保留 `work_id` 与合集成员关系，解除当前资料链接；恢复后按作品 ID 重新关联。删除任务不删除文章或合集索引。

## 7. 分发、复用、取消与结果

父任务类型为 `collection_import`，子任务类型仍为 `capture`。清点与媒体处理分开：只有用户选择并确认后才创建子任务。

```text
queued → resolving → inventorying → needs_selection
                                      ↓ 确认导入
                                  monitoring
                                      ↓
                      completed / completed_with_warnings
```

登录问题走 `needs_auth`；没有可信清单的读取失败走 `failed`；用户取消走 `cancelled`。已明确为空且完整的清单可完成并说明为空；空的不完整清单不能显示“全部完成，0 项”。

确认前必须保证所有可选择作品已 selected 或 skipped；已有完整资料和不可用/不支持作品直接归入对应展示状态，不要求无意义的手动选择。不完整清单须显式 `accept_partial=true`。Gateway 已有明确的部分导入授权时直接传该参数，否则说明实际缺失范围，不能把“全部入库”自动解释成接受不完整清单。

确认在 `entry_operations_locked()` 下以 SQLite `BEGIN IMMEDIATE` 一次发布选择、关联、全部子任务 ID 和父任务 monitoring/完成结果，重复确认返回同一批结果。逐项顺序为：

1. 已存在完整条目：直接关联；缺少必要来源文件的条目继续按现有修复/采集规则处理。
2. 同一作品已有未终止采集任务：按归一化后的 `CaptureOptions` 核对后复用并记录批次关系，不覆盖原任务选项、`gateway_context` 或原有批量上下文。清单预览与实际分发必须使用相同判据；任一所选作品的选项冲突时，整次确认返回状态冲突并列出对应作品，事务不创建子任务或改变确认状态。用户可等待已有任务完成、明确修改本批次选择，或重新创建选项一致的批次后再确认，不能静默忽略所选处理方式。
3. 其余作品：创建普通 `CaptureRequest`，继承父任务的处理选项和 Gateway 路由，附加 `collection_context`，并在同一事务中登记关联。

按作品 ID 和规范 URL 查重；不能只依靠任务结束后的去重来避免两次下载和模型调用。复用规则通过最小公共任务身份函数供收藏、博主和合集分发共同使用；博主仍按现有清单和选择规则分发，只统一子任务身份核对与关联登记。

新增 `Database.capture_parent_ids(child_id)` 查询收藏、博主和合集的已确认有效批次关联，替代取消时只看可能过期的 `result.child_job_ids`。取消一个父任务仅取消由批量导入创建、且没有其他活动批次仍使用的子任务；活动批次不包含已完成、失败、取消或删除的父任务。复用的独立单条任务继续执行。最后一个活动引用批次取消后，该批量子任务可以取消。显式取消单条子任务仍按现有全局任务语义处理，各父任务据此更新。

子任务终态必须同时更新全部关联父任务，不能只看创建时的单一 `parent_job_id`。查询关联时可取得已结束父任务，取消保护另按上述活动状态过滤；已取消或删除父任务仅更新明细，不恢复执行。父任务以实际子任务终态计算成功、已有、处理中、失败、已取消、跳过和不支持数量；只要仍有处理中或等待 Gateway 的子任务，就保持 monitoring。失败、所选子任务取消、子任务自身带警告或接受不完整清单的完成结果为 completed_with_warnings。

“重试失败项”只重试本批次选择的 failed 子任务，保留已成功条目、已取消和已删除项。清点尚未确认时重试重新清点；取消父任务不自动重试。查询结果及索引保留失败的视频编号和原因，不能隐藏失败后声称全部入库。

## 8. Web、CLI、MCP 与 Gateway

### 8.1 Web

导入中心、空资料库入口、导入菜单和命令面板新增“导入合集”；粘贴识别接口返回 `collection` 后预填 `/imports/collections`，粘贴本身不清点或导入。

页面流程为“粘贴 → 清点合集 → 查看清单 → 选择 → 确认导入 → 查看结果”。展示合集名称、原链接、已读数量、完整性、封面、内部编号、可核实集数、标题、类型、时长、已有条目及任务状态。默认不勾选新作品；“全选全部页”作用于本次清单全部可导入作品，不能只选当前可见页。只看尚未入库是展示筛选，不改变选择范围。

选择使用现有复选框、键盘和批量保存模式；确认必须等待选择保存成功。清点中断后保留已读清单与错误，用户可重试清点或明确接受部分导入。确认前显示新任务、复用任务、已有资料及不可导入数量。首期沿用现有批量确认交互，不额外引入下载或时长审批。

建议新增路由，均由正式接口测试固定：

| 路由 | 用途 |
| --- | --- |
| `GET /imports/collections` | 合集导入页 |
| `POST /api/collections/inventory` | 创建清点父任务，HTTP 202 |
| `GET /api/collections`、`GET /api/collections/{mix_id}` | 持久合集及关联条目 |
| `GET /api/collection-imports`、`GET /api/collection-imports/{job_id}` | 导入历史及清单；后者支持 page/limit |
| `POST /api/collection-imports/{job_id}/selection` | 保存选择 |
| `POST /api/collection-imports/{job_id}/confirm` | 确认导入，HTTP 202 |
| `POST /api/collection-imports/{job_id}/retry` | 重试清点或失败项，HTTP 202 |

错误映射沿用现有约定：参数错误 400、对象不存在 404、状态或完整性冲突 409。新资源 URL 为 `/static/imports-collections.js`，加载在 shared/app 基础脚本之后，纳入 wheel；旧资源 URL 保留。

### 8.2 CLI 与 MCP

CLI 新增 `collection` 命令组：`add`、`inventory`、`select`、`confirm`、`retry`、`list`、`show`、`history`。`add SHARE_TEXT` 只创建清点，`select JOB_ID --all` 明确选定所有可导入作品；逐项使用 `--include/--exclude` 内部编号或稳定作品 ID。已有父任务完成状态判断和 JSON 输出方式沿用现有 CLI。

MCP 与 Service 门面新增下列接口，名字在实施计划和测试中保持一致：

```python
capture_douyin_collection(source_text, options=None, gateway_context=None) -> JobRecord
get_collection_inventory(job_id, *, page=None, limit=None) -> dict
set_collection_work_selection(job_id, decision, *, ordinals=None, work_ids=None, all_works=False) -> dict
confirm_collection_import(job_id, *, accept_partial=False) -> JobRecord
retry_collection_import(job_id) -> JobRecord
get_collection(mix_id) -> dict
list_collections() -> list[dict]
list_collection_imports(*, limit=50) -> list[dict]
```

`decision` 为 selected 或 skipped；`all_works=true` 与 ordinals/work_ids 互斥，后两种选择器也互斥。作品编号、选择和确认结果均来自数据库，不能只保存在 Agent 会话中。

MCP 默认一次返回全部清单，与博主入口一致；Web 显式传分页参数。分页 page 从 1 开始，limit 范围 1–1000，返回 `total/has_more`。一轮初始清点仅在完整结束后开放选择；中途检查点仍可读取展示，不能并发修改其选择。部分结束的清单进入 needs_selection 并带 `complete=false`。

### 8.3 Gateway 与登录

新增子任务上下文：

```json
{
  "collection_context": {
    "collection_id": "7651028428363139126",
    "collection_name": "项目经理故事汇",
    "parent_job_id": "实际父任务ID",
    "work_id": "7644758067783852901",
    "ordinal": 1,
    "batch_silent": true
  }
}
```

创建事件、状态事件、完整上下文及协议 2 的紧凑投影都保留该字段。collection_id 在这里等于 mix_id，ordinal 是本批次选择编号。合集新建的子任务继续完成校正和分析，使用 `batch_silent` 抑制逐条完成消息，由父任务发送用户可见汇总；原始引用、时间戳及灵感规则保持不变。

复用任务保留原事件归属及通知方式，新 Gateway 不确认其他会话的事件，也不为复用任务补写 `batch_silent`。新父任务记录复用关系并接收自己的汇总；等待其他会话继续分析时展示该状态，不能把任务挪给新会话或把等待算完成。

新增 `auth_scope="collection"`，检查、系统提示、CLI 参数、Web 展示和登录后的同通道重试均要识别；复用 `douyin-wiki auth douyin` 和已有 Profile，不建立第二个授权入口。协议错误与签名拒绝仍保留对应错误，不通过反复打开登录窗口处理。

## 9. 兼容与执行约束

- 保持 console scripts、旧 CLI 别名、MCP 名称和参数、Service 公开方法、Web 路径、现有负载字段与状态码兼容；只增加新入口及可选字段。
- 保持 Python `>=3.12,<3.14` 与现有 `playwright>=1.54,<2`；首期不引入第三方下载框架或新的常驻服务。
- 合集是来源容器，不能新增成 EntryRecord 的媒体 source_kind；其作品仍为 video/image_note。
- 现有视频目录、不可变原稿、资料侧车及模型选择不改变；合集索引引用真实条目，不复制资料。
- 正式测试使用隔离 Vault 与假 provider；浏览器探针、原始响应和日志存放仓库外，正式夹具只保留脱敏数据。
- 后续实现使用合适的隔离 checkout 和独立验证环境；不删除、移动或盲目同步现用 `.venv`，不直接修改生产 Vault、状态库或 Profile 文件。
- 用户已确认设计并授权开发；生产合集实际入库另依用户指令执行。实现过程默认当前 Agent 执行，子 Agent 需用户明确要求。

## 10. 测试与验收

新增行为测试采用 TDD，测试集中在真实边界与故障恢复；本次文档编写不新增运行测试。

| 测试文件/范围 | 必须证明的行为 |
| --- | --- |
| `tests/test_share.py` | 长链、短链、多级跳转、合集转第一集仍保持合集分类、冲突 ID、外部跳转拒绝；旧单条及博主测试继续通过 |
| `tests/test_collection_api.py` | 19 位整数精度、ID 冲突、类型识别、非零状态、缺失数组、null、数量误用、明确归属不匹配 |
| `tests/test_collection_browser.py` | 请求参数与世代关联、其他合集预取、迟到响应、监听清理、超时、原生函数歧义；不调用未知函数 |
| `tests/test_collection_adapter.py` | 多页、重复记录、真实顺序、cursor 缺失/循环、500 页上限、数量不符、登录失效及逐页检查点 |
| `tests/test_collection_flow.py` | 选择/全选、接受部分清单、幂等确认、事务回滚、重启后重新读取世代、租约失效及全部子任务原子发布 |
| `tests/test_collection_lifecycle.py` | 与单条/收藏/博主共享任务、处理选项冲突、跨批次取消、最后一个批次取消、只重试失败项、多个父任务共同刷新 |
| `tests/test_collection_vault.py` | 一份视频多合集关联、改名路径稳定、索引真实相对路径、文件失败回滚、异常侧车阻止重建、历史清理与删除/恢复 |
| `tests/test_collection_interfaces.py`、现有 Web/Gateway 测试 | CLI/MCP 参数及默认全清单、Web 状态码与选择范围、collection 授权、协议 1/2 上下文和事件归属 |
| `scripts/verify_readwise_operations.py`及静态包验收 | 入口分类、键盘选择、刷新恢复、确认等待保存、窄屏/亮暗主题、结果及错误展示、新 JS 入包 |

实现阶段先跑变更对应的测试，完成后执行项目正式检查：

```sh
uv run ruff check src tests
uv run pytest -q -m 'not live'
```

命令在独立开发/验证环境中执行。涉及页面及包成员时再运行操作页浏览器脚本和 `uv build`；脚本输出指向仓库外目录。通过后只有新增改动、失败或未解决疑点才重复扩大验证。

真实读取验收选择《项目经理故事汇》作为样本，在专用 Profile 验证详情、首屏与主动第二页、明确结束信号及集合顺序，并与同次 Chrome 核对；18 仅为当前网页基准。再通过小 page size 强制多页；不能靠一次返回全部作品证明分页。

逐条入库验收使用独立测试 Vault，覆盖新视频、已有视频和故意失败的子任务；Gateway 模式必须真正完成校正及分析阶段。只有全部选定的可导入作品已有完整条目，才能报告“选定作品全部入库”；其他情况报告对应数量与原因。

## 11. 开发阶段与阶段验收

| 阶段 | 交付物 | 进入下一阶段的判据 |
| --- | --- | --- |
| A：只读验证网页请求 | 仓库外探针记录、原生函数参数、原始响应字段、强制主动翻页、同次网页对照；脱敏夹具进入 tests | 两端点及第二页真实可读，ID 精确、目标明确、分页可终止；未通过则修订设计 |
| B：目标解析与清点 | 合集解析、三层读取适配器、模型、检查点及 schema 迁移 | 新旧链接回归通过；异常分页不冒充完整，失去租约不能写入 |
| C：导入和生命周期 | Controller、原子分发、去重、共享引用、结果刷新、失败重试 | 重复确认/并发确认不重复建任务；跨批次取消与重启恢复通过 |
| D：合集资料关系 | Vault 索引与侧车、重建、删除及恢复 | 条目仍唯一；重建/恢复保留关系，异常不破坏原资料 |
| E：三入口与 Gateway | Web、CLI、MCP、任务展示、授权恢复和批量事件 | 用户可全选全部页并完成导入；两协议均能接续，旧入口保持兼容 |
| F：集成与验收 | 适当回归、浏览器、包成员和独立 Vault 的真实采集记录；文档更新 | 各层证据齐全，所有未通过项及实际覆盖范围明确 |

A–F 是设计中的交付边界，实际完成记录见实施计划。用户已确认设计并授权按此开发，writing-plans 编写实施计划后沿用该授权执行。当前 Agent 执行，不因启用 Superpowers 自动委派。

## 12. 本次文档自检与待确认项

本设计已对照当前解析器、收藏/博主分发、Profile 锁、Worker 租约、Gateway 投影、授权恢复、维护重建与废纸篓流程。原生函数参数已通过阶段 A 验证，数量字段与实际集数分别处理；网页18集和探针完整分页均不表示入库验收通过。

已确认范围为：Web/CLI/MCP 均支持；保留选择与明确的全部导入；以独立合集关系关联唯一视频资料；首期采用一次性清点和手动再次读取。生产导入尚未开始。
