# 五项设计问题修复

2026-10-30。按用户确认的全部五项评价结果修改，源码 Web 版本从 0.2.97 更新为 0.2.98，Python 包继续为 0.2.1。沿用现有桌面界面、英文固定章节和阅读字体。

## 修改

| 范围 | 文件 | 结果 |
| --- | --- | --- |
| 搜索输入名称 | `webapp/templates/app.html` | 两处输入提供明确中文名称，快捷键不参与字段命名；增加独立搜索状态区域 |
| 搜索依据与恢复 | `webapp/catalog.py`、`app.py`、`static/app.js`、`static/app.css` | 返回有长度限制的真实匹配片段，支持 Unicode 大小写折叠及近似匹配说明，安全地强调命中词；区分等待、无匹配与失败并可重试，旧请求不覆盖新查询 |
| 视频证据入口 | `webapp/static/app.js`、`app.css` | 目录显示逐字稿 / 关键帧数量，点击展开与定位，页尾保持正确当前标记；纳入既有阅读焦点与进度处理 |
| 辅助文字 | `webapp/static/app.css` | 亮色 `--text-3` 为 `#606674`，暗色为 `#9296A2`；原有字号和主题结构继续使用 |
| 批次质量提示 | `webapp/static/jobs.js`、`app.css` | 全部子任务的有提示数量与筛选；提示汇总提前，超过三条可展开；质量说明与失败分别呈现 |
| 缓存版本 | `webapp/version.py` | Web 0.2.98，用于模板和静态资源版本参数 |

以上源码路径相对于 `src/douyin_wiki/`。不修改用户资料、索引格式、MCP 工具或已有请求参数。关键词结果的新 `search_match` 字段只在有对应命中说明时添加，不新增整篇 Markdown 字段，也不制造时间定位。

## 验证

- `uv run --no-sync ruff check src tests`：通过。
- `uv run --no-sync pytest -q -m 'not live' --tb=short`：1944 passed，2 deselected，311.19 秒。
- 新增 `tests/test_web_design_improvements.py` 的 8 项定向回归通过，覆盖文字与近似匹配、Unicode 原文、搜索等待 / 失败 / 重试 / 空结果、旧请求、HTML 文本安全、目录展开定位和当前标记、跨页提示筛选及失败区分。
- `scripts/verify_review_web_races.py` 的共用浏览器夹具增加可选 `prepare`，在创建应用前准备独立测试数据；原有调用保持兼容。
- 首轮完整集有 1935 项通过、9 项失败。其中两项仍断言旧文案和旧目录；其余失败来自主题选择器重复匹配及事件未就绪。在 `git archive HEAD` 的独立副本上，两类既有测试问题均复现；修正测试选择器和等待条件后，9 项复验通过，再运行上述完整集通过。
- 原生 Chrome 使用临时 Vault 与专用随机端口，实际覆盖 960 / 1100 / 1280 / 1440px、亮暗主题、列表 / 专辑墙、搜索加载 / 失败 / 恢复、证据目录及 57 子任务批次。已检查画面的文档宽度与窗口一致；4 个提示可单独筛选，失败项仍单独为 1 个。临时页面已关闭，浏览器尺寸已复位。
- 对亮暗主题各九种背景、表面、下沉表面及悬停 / 选中组合计算，辅助文字最低对比度分别为 4.7177:1 与 4.7152:1。没有将本次检查描述为完整读屏或全站无障碍符合性验收。
- Impeccable 最终机械检查运行一次：33 条候选，其中内距 30、字体 2、进度条 1；低对比度为 0。候选延续评价时容器 / 子元素间距、现有字体和不确定进度条的结构性误判、不适用或未逐个复现提示；未为了清空数量修改这些界面，也未新增忽略规则。

## 安装包

`uv build --wheel` 成功生成 `douyin_wiki-0.2.1-py3-none-any.whl`。104 个包内运行文件逐字节与源码一致，不含 Impeccable 本地资料或 Python 字节码。另建独立临时环境以 `--no-deps` 安装 wheel，借用现有依赖执行本地 API 与静态资源检查，确认实际导入路径位于临时安装目录、Web 0.2.98、输入名称和关键词片段均生效。临时安装环境与验证 Vault 已删除。

wheel SHA-256：`4996463aca6bc858073d42b27239f0c8e5ef58de98dea154d74a1fcf979e9cff`。

构建产物、完整测试输出、检测器结果、对比度计算、截图、浏览器记录和验证复现脚本保留在仓库外：`/Users/weisengao/Documents/ChatGPT/douyin-wiki-maintenance/2026-10-30-impeccable-fixes/`。前述设计验证截图使用隔离数据；下述 `deployment/` 截图记录现用服务。

## 本机升级（同日）

用户随后要求升级本地、提交代码并同步 GitHub。北京时间 16:25，仓库外独立 runtime 已安装上述验证过的 wheel，Web 与 Worker 已重启，健康检查确认 Web 0.2.98、Worker 正常且空闲。实际解释器为 `/Users/weisengao/Library/Application Support/douyin-wiki/runtime/.venv/bin/python`，导入路径位于该环境的 `site-packages/douyin_wiki`，104 个运行文件与 wheel 一致。

- 安装前确认没有运行中、排队或等待用户的任务。以 `uv pip install --no-index --no-deps --reinstall` 仅替换本项目包，所有其他已安装依赖的名称及版本保持一致。
- 配置文件和三个 LaunchAgent 文件的 SHA-256 均未变化；维护定时任务继续保持原注册与日程，没有提前执行维护。
- 升级前后资料库均为 371 条；任务仍为 376 个完成、1 个既有失败，没有发起业务处理。API 搜索返回实际命中说明，五个运行中的 JS / CSS 文件与源码逐字节一致。
- 使用上述解释器新建 STDIO MCP 连接，重新读取 instructions 与工具列表；51 项工具名称与源码一致，`doctor` 总体检查通过。本次未重启其他聊天已经存在的 STDIO 进程；已有客户端仍须重连后加载新安装。
- 原生 Chrome 核对现用服务的版本、真实搜索片段，以及带段数的逐字稿目录展开、定位与当前标记。截图与结果为 `deployment/live-search.jpg`、`live-transcript.jpg`、`browser-check.json`；临时页面已关闭。

替换前的包、元数据与命令入口备份在上述仓库外目录的 `deployment/backup-20261030-162330/installed-package.zip`。安装前后探针、命令记录和新 MCP 连接结果分别为 `deployment/before.json`、`after.json`、`install-commands.json`、`mcp-check.json`。原仓库 `.venv` 保留。

本地 `dist/` 的 0.2.1 wheel 与源码归档同时刷新；替换前备份并检查新包成员，旧版本发行文件继续保留。产品背景写入 `PRODUCT.md`，已确认的宣传 / 展示页先制作视觉稿偏好写入 `.impeccable/config.json`；本机评价归档保留在本地并由 `.gitignore` 排除。
