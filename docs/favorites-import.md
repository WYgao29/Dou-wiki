# 收藏批量导入

此功能分成清点和确认两个步骤。清点仅生成可恢复的作品清单；确认后才创建现有采集管线的子任务，执行下载、转录、分析和入库。

## 网页操作

1. 打开导入中心的「导入抖音收藏」。页面显示本地缓存的授权状态；「检查抖音账号授权」直接进入对应授权区域。需要登录时，可在提示旁授权并重试读取。
2. 点击「更新抖音收藏」。日常使用「保留选择并查找新增」，沿用同账号、同范围的上次勾选；「重新核对全部」重新建立清单、默认不勾选。新发现作品都需要主动选择。两种方式均重新读取当前可访问作品，不承诺增量读取更快。只查看历史不会访问抖音。
3. 选择「指定收藏夹」时，就在范围下点击「读取我的收藏夹」，读完可全选或反选收藏夹，再更新作品。默认只允许选择视频；勾选「允许选择图文作品」后可选择静态图文，修改这个选项需要重新读取。文章保留在清单中并标明暂不支持。
4. 默认「只看未入库」。输入标题或作者会自动搜索，可切换每页 50 或 100 件；取消该过滤后，已入库作品提供资料链接。逐条勾选或 Shift 连选立即生效，约 400ms 后批量保存；保存失败时先重试保存，不能带未保存选择提交。若另一页已经取消或确认批次，当前页显示最新状态并说明未保存的勾选未提交，可以切换记录或重新读取。
5. 「选择本页」只选当前页，「选择筛选结果」覆盖当前筛选的所有页，「选择这个收藏夹」覆盖该夹全部未入库作品。「选择／排除整份清单」不受页码和搜索筛选限制。
6. 点击「确认导入所选作品」先查看件数、新建与复用任务数、已入库跳过数和分析方式；超过 30 件需输入件数再确认。清单读取结束但不完整时，必须先勾选接受部分清单。关闭确认框不会创建采集任务；无法识别稳定收藏夹 ID 时，不会用名称代替 ID。
7. 读取中显示已读件数和耗时，可停止读取；导入中显示进度，可取消剩余任务。取消保留清单、选择和已入库资料，不能直接恢复导入；点击「重新读取清单」建立新批次，增量模式沿用相同账号与范围的选择。其他批次共用的采集任务继续处理。
8. 提交后进入任务详情，查看批次汇总、分页子任务和失败项；可回到收藏清单或「在资料库查看本批」。该筛选显示本批确认选择并已入库的资料，包括去重跳过的已选资料，可移除「本批收藏」筛选回到完整资料库。

历史选项显示创建时间、范围、件数和状态；旧目录警告标明「旧版本」，重新读取不会改写旧记录。删除记录后页面清空该批清单；删除导入记录和清理缓存采用独立危险操作确认，保留已入库资料及抖音平台收藏。

清单当前不提供封面、时长、收藏时间或下载体积；界面显示标题全文提示、分开的话题和作品状态，不估算下载 GB 或剩余时间。读取阶段不知道总件数，进度条表示正在读取；导入进度使用实际任务进度。

全部收藏直接读取作品列表，包含未分组作品。该模式不逐个遍历收藏夹；归属信息不足时会单独提示，不应据此断言作品没有加入任何收藏夹。指定多个收藏夹取并集，以作品 ID 去重。

「新增」以本批次首次清点时冻结的历史作品集合为准，只比较同账号、同收藏夹范围的更早批次。
后续清点或删除旧批次记录不会改变已保存批次的新增数量。新建批次在识别当前账号后才继承
该账号的增量清单；换账号应新建批次，原批次继续保留账号变化保护。

## 读取与完整性

收藏夹目录与指定夹内作品通过登录后的抖音网页原生请求读取。收藏夹和作品 ID 从原始响应在 Python 中解析，保持长数字完整，不使用名称代替 ID。默认每页 20 件，按返回的下一页标志与游标逐页读取；目录与每个所选夹各有 500 页安全上限。

目录数量与实际作品数量可能不同。例如收藏夹显示 7 条，而列表只提供 4 条时，会保留这 4 条、提示数量不一致，并要求明确接受不完整清单。夹内分页偶尔返回空/null 列表，但游标仍推进且声明有下一页：继续寻找后页可读作品，保留不完整提示；游标循环、不推进、超过上限或后续失败时停止并保留已读内容。不会根据数量差异推断其他作品的状态。

账号信息首次出现较慢时会等待其加载；读取期间仍核对同一账号。旧批次若保存了包含年龄等邻接文字的错误账号 ID，应新建批次，不自动改写或合并历史账号记录。

## 登录与后台处理

- 收藏清点和图文采集使用专用 Playwright Profile。首次或登录过期时在 Web「设置 › 授权状态」完成抖音账号授权，或运行 `douyin-wiki auth douyin`，然后重试。账号变化会暂停原批次。
- 视频主下载路径也使用专用 Profile；能否下载以具体作品的探测结果为准。`douyin-wiki auth video` 用于准备系统浏览器 Cookie，供 yt-dlp 最后回退使用。
- 后台 Worker 必须运行。Gateway 模式需 Agent 继续消费事件完成校正与分析；待 AI 处理、待确认及历史待复核任务不算完成。
- 新建子任务沿用默认媒体临时保留规则及现有长视频确认规则。已入库作品跳过，进行中的采集任务复用，不覆盖已有灵感、保留策略或原任务 Gateway 上下文。
- 批次汇总通过父任务及 `favorites_context.batch_silent` 表达；Agent 仍应报告需要用户处理的等待项。失败重试只重试失败子任务；需要登录的子任务通过原任务队列或授权流程恢复。
- 清点和重扫均由用户手动触发。没有定时同步，也不会在打开网页时自动清点或导入。

## CLI

```sh
# 仅清点，返回任务 ID。后台 Worker 执行清点。
douyin-wiki favorites scan
# 仅目录；使用返回的稳定 ID，不能直接填收藏夹名称。
douyin-wiki favorites scan --directory-only
douyin-wiki favorites scan --folder-id ID_1 --folder-id ID_2 --include-images
# 恢复历史、分页查看，明确选择需要导入的作品。
douyin-wiki favorites list
douyin-wiki favorites show JOB_ID --page 1 --limit 50
douyin-wiki favorites select JOB_ID --work-id WORK_ID
# 可再排除个别作品。
douyin-wiki favorites select JOB_ID --exclude --work-id WORK_ID
# 不带 work-id/folder-id 的 select 会选择整份可导入清单，需先检查范围。
# douyin-wiki favorites select JOB_ID
douyin-wiki favorites confirm JOB_ID
# 仅在明确接受不完整清单时加 --accept-partial。
douyin-wiki favorites retry JOB_ID
```

`scan` 可传 `--gateway openclaw --conversation-id ...` 绑定后续事件。
MCP 对应工具为 `scan_favorites`、`list_favorites_imports`、`get_favorites_import`、`set_favorites_selection`、`confirm_favorites_import`、`retry_favorites_import`。
Agent 必须先展示清单并获得用户的导入指令，才能调用确认工具。
Web 的确认框和件数输入属于网页交互；CLI/MCP 仍使用明确选择与原有确认参数。

## 验证边界

自动化测试使用临时模拟数据，覆盖数据库事务、幂等派发、断点、账户变化、跨来源去重、超过 5000 条清单、Web/CLI/MCP 接口及离线浏览器页面行为。

2026-10-04 已在会话隔离副本上验证正式适配器：4 个收藏夹、15 个可访问视频，目录、单夹、多夹及每页 2 件的主动分页均与当次网页结果核对。“时尚”显示 7 条、实际读取 4 条，保留不完整提示。原浏览器 Profile 没有被脚本直接改写，临时副本已删除。此次真实验证仅覆盖视频元数据读取；图文、文章、下载与完整入库没有真实账号端到端结论。

网页构建与接口可能变化；原生读取函数不可确认时返回明确错误，不以空清单代替失败。默认全部收藏仍使用账号级 DOM 作品列表，包含未分组作品，其归属可能不完整。

维护者可显式选择只读验收脚本；它不实例化生产服务或任务队列，记录必须放仓库外，退出清理本次会话副本。报告的 `passed` 表示读取与 ID 对照通过，不表示带数量差异的 `complete=False` 清单变完整。

```sh
DOUYIN_WIKI_RUN_LIVE=1 uv run --no-sync python scripts/verify_favorites_api.py \
  --directory-only --page-size 2 --output-dir /tmp/douyin-favorites-verification
# 可重复 --folder-id；使用目录给出的稳定数字 ID。
DOUYIN_WIKI_RUN_LIVE=1 uv run --no-sync python scripts/verify_favorites_api.py \
  --folder-id FOLDER_ID --page-size 2 --output-dir /tmp/douyin-favorites-verification
```

测试命令：`.venv/bin/pytest -q -m 'not live'`、`.venv/bin/ruff check src tests` 和 `node --check src/douyin_wiki/webapp/static/imports-favorites.js`。离线浏览器测试需要已安装 Playwright Chromium 和允许启动测试浏览器的环境；它使用临时上下文，拦截所有网络请求，不加载个人 Profile。

完整操作页面可用 `uv run --no-sync python scripts/verify_readwise_operations.py --output-dir /tmp/douku-operations-verification` 在临时 Vault 中验收；指定输出目录避免覆盖既有截图记录，临时库随脚本退出清理。
