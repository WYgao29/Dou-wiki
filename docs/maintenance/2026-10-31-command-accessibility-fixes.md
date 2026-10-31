# 全局搜索焦点与缩放修复

2026-10-31。按用户要求修复[桌面 Web 技术审查](2026-10-30-desktop-accessibility-audit.md)确认的一项 P1、两项 P2。源码 Web 从 0.2.98 更新为 0.2.99，Python 包继续为 0.2.1。

## 修改与结果

| 范围 | 文件 | 行为 |
| --- | --- | --- |
| 输入与选项语义 | `templates/app.html`、`static/app.js` | 输入采用可编辑组合框语义，关联结果列表与既有方向键说明。选项拥有按条目 / 命令身份生成的稳定 ID，当前项通过 `aria-activedescendant` 关联；输入保持键盘焦点，选项由方向键操作 |
| 状态切换 | `static/app.js` | 打开、方向键移动、结果更新时同步展开状态与当前项；等待、无结果、无可用选项的失败及关闭时清除旧引用，空结果列表隐藏，独立状态与重试按钮继续可用 |
| 选项可见性 | `static/app.js`、`static/app.css` | 键盘移动和视口变化后把当前选项滚到最近可见位置；选项保留 4px 滚动边距，避免分数像素高度裁切边缘；鼠标悬停只更新选择，不主动滚动 |
| 短视口布局 | `static/app.css` | 整个弹窗按顶部 20dvh 和底部 16px 分配可用高度；结果区域负责收缩和滚动，输入、状态与两段操作说明保留空间；只对打开的弹窗使用 Flex 布局 |
| 缓存版本 | `version.py` | 静态资源与模板版本更新为 Web 0.2.99 |
| 回归入口 | `tests/test_command_accessibility_browser.py`、`tests/test_web_design_improvements.py` | 新增焦点生命周期、首尾循环、缩放后选择可见性、短视口与失败状态回归；既有搜索状态回归改用组合框角色查找输入 |

前五行文件位于 `src/douyin_wiki/webapp/`。MCP、业务 API、资料格式与现有搜索文案继续沿用原契约。

## 复现与回归

新增用例先在原实现运行，五个测试中四项失败、一项通过：缺少组合框语义、选中末项不可见，以及 960×402、1280×536 的说明裁切均被复现。修复后又用原生 Chrome 发现“弹窗打开后改变缩放”的边界，补充断言后确认失败，再增加视口变化时的当前项同步。

最终五项新增回归通过，覆盖稳定 ID、方向键与输入焦点、等待 / 无匹配 / 关闭后的引用清理、重新打开、命令执行、首尾循环、调整窗口后选中项可见，以及三种有效视口下的成功与错误状态。包含既有可用性及设计改进的 98 项相关回归也已通过；最终完整集结果在交付验证中记录。

## 原生浏览器复验

使用隔离 Vault、测试资料与随机端口，不对用户知识库发起业务写操作。

- 1440×900 亮色：搜索八项，连续向下选择末项，输入仍有焦点，活动 ID 对应实际选项；末项滚入结果容器。
- Chrome 原生界面确认 200%，有效视口 960×402、`devicePixelRatio=2`。弹窗范围为 80.5–386.5px，末尾说明在 355.5–377.5px，保留底部空间。选中项、两段说明和输入均可见；弹窗打开后从 100% 放大到 200% 也能保持当前项可见。
- 200% 暗色：一次受控 503 后，展开状态为 false、活动引用为空，查询与重试保留；Tab / Enter 重试成功后返回输入，七项结果恢复，方向键选择末项仍可见。
- 原生 150% 暗色：有效视口 1280×537，说明与当前项位于窗口内。另检查 960×800 的空结果，展开状态与活动引用均正确。

本轮验证浏览器辅助功能树、控件关系和键盘行为。此前 VoiceOver 启动后，控制工具无法取得真实字幕或朗读文本；本轮没有把语义检查写成完整读屏验收，实际朗读仍待人工补验。

单次 Impeccable 机械检查返回 37 条候选，与审查时相同分类：内距 30、既有字体 6、进度条 1，低对比度 0；没有增加忽略规则。

证据、测试输出、隔离复现脚本和后续交付探针保留在仓库外：`/Users/weisengao/Documents/ChatGPT/douyin-wiki-maintenance/2026-10-31-command-accessibility-fixes/`。`browser-records.json` 保留首次发现缩放后选项不可见的中间记录，最终结果对应名称包含 `after-resize-fix` 及 `recovered-200-dark` 的记录。

## 交付验证

- `uv run --no-sync ruff check src tests`、`git diff --check`：通过。
- `uv run --no-sync pytest -q -m 'not live' --tb=short`：1949 passed，2 deselected，332.18 秒。
- wheel 的 104 个运行文件逐字节与源码一致。另建临时环境以 `--no-deps` 安装 wheel，借用开发环境依赖，确认实际导入位于独立安装目录、Web 0.2.99、本地 API 与静态资源一致；实际安装包的五项新增浏览器回归通过。临时安装环境与验证 Vault 已删除。
- 额外试跑历史 `scripts/verify_readwise_operations.py` 时，在任务进度条高度的旧断言处失败；用未修改的 `git archive HEAD` 独立副本复现同一位置失败。本轮未修改这段历史脚本，也不把它写为已通过；正式完整回归与新增浏览器回归均通过。两次输出保留为 `operations.log` 与 `operations-baseline.log`。

wheel SHA-256：`ddb6531ef4c6bbf7e293d6cfcca1cba2e9d9d88267494f8c329ce6149fd8f626`。

## 本机部署

沿用用户此前要求的本地升级与 GitHub 同步方式，现用独立 runtime 已替换为上述验证过的安装包，Web 与 Worker 重启后确认 Web 0.2.99、Worker 正常且空闲。

部署前确认没有运行、排队或等待用户的任务，备份已安装包、元数据与命令入口，再以 `uv pip install --no-index --no-deps --reinstall` 仅替换本项目包。其他依赖名称及版本、配置文件、三个 LaunchAgent 文件均保持一致；维护定时任务沿用原注册，没有提前执行。资料库升级前后均为 371 条，实际服务的静态文件与源码一致。

升级备份位于本记录仓库外证据目录的 `deployment/backup-20261031-080814/installed-package.zip`，探针与命令记录为 `deployment/before.json`、`after.json`、`install-commands.json`。现用解释器仍为 `/Users/weisengao/Library/Application Support/douyin-wiki/runtime/.venv/bin/python`，原仓库 `.venv` 保留。

使用现用解释器新建 STDIO MCP 连接，51 项工具名称、instructions 与源码一致，`doctor` 总体检查通过；其他聊天已经存在的旧 STDIO 会话仍由客户端重连。原生 Chrome 对实际 8765 服务复核 Web 0.2.99、搜索组合框及方向键当前项，记录为 `deployment/mcp-check.json`、`browser-check.json` 与 `live-command.jpg`。

本地 `dist/` 的 0.2.1 wheel 与源码归档已刷新，替换前备份旧的两个同名文件；更早版本发行文件保留。最终归档检查与 SHA-256 记录为 `deployment/artifacts-check.json`。临时验证服务、页面及视口覆盖已清理，浏览器缩放恢复为 100%。
