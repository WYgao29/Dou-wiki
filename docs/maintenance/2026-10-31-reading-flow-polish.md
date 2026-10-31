# 搜索与阅读路径精修

2026-10-31。按用户确认的 Impeccable polish 范围，检查“搜索资料 → 阅读正文 → 核对逐字稿与关键帧 → 返回”完整路径。Web 从 0.2.99 更新为 0.2.100，Python 包继续为 0.2.1。

## 修改与结果

| 文件 | 修改 | 用户能看到的结果 |
| --- | --- | --- |
| `src/douyin_wiki/webapp/static/app.js` | 复用安全的标题高亮；把命中片段放到元信息前，列表有片段时省去重复摘要 | 列表、专辑墙和全局搜索先说明为什么匹配；直接命中标题时不重复显示整段标题 |
| `src/douyin_wiki/webapp/static/app.css` | 复用强调色高亮，统一片段间距，搜索资料行按实际文字增高 | 长标题与片段可以换行，亮暗主题继续沿用已有颜色与字体 |
| `src/douyin_wiki/webapp/static/app.js` | 目录、j / k 同步实际焦点；证据展开保留原生焦点；刷新后恢复持有焦点的阅读块 | 从目录进入正文或证据后，键盘操作接着目标内容继续 |
| `src/douyin_wiki/webapp/static/app.css` | 关键帧大图链接改为块状链接，使用已有圆角 | 焦点边框包住整张缩略图，不再只显示几个角 |
| `src/douyin_wiki/webapp/static/app.js` | 加载失败后聚焦主要内容，替换已不存在的 `showArticleFallbackEntry()` 调用 | 错误页面不再报该引用错误，用户可以返回原搜索结果 |
| `tests/test_reading_flow_polish_browser.py`、`tests/test_web_design_improvements.py` | 新增五项路径回归，更新标题高亮与证据焦点的既有断言 | 覆盖文字安全、Unicode、近似匹配、阅读刷新、证据展开、返回位置与加载失败 |
| `scripts/verify_readwise_reader.py` | 目录总数从九项更新为十一项，另检查逐字稿与关键帧两项 | 验证入口与已有证据目录保持一致 |

标题、摘要与证据内容仍来自已有 API。普通浏览的摘要、灵感预览、语义检索证据、组合框状态与快捷键、字体、配色及桌面布局继续沿用现有约定。

## 复现与验证

新增搜索与焦点场景在修改前确认失败；搜索夹具修正为实际 Markdown 标题后，两项标题高亮检查仍确认失败。加载失败场景也先确认主要内容没有获得焦点。修改后十八项相关回归通过，覆盖以下路径：

- 列表、专辑墙、全局搜索中的直接标题命中、摘要命中、`STRASSE` 对 `Straße` 的原字形高亮、HTML 字样安全展示，以及近似匹配说明。
- 960px 暗色和 1440px 亮色下，从带来源筛选的搜索专辑墙页尾进入资料，目录跳转、j / k、同一文章刷新、逐字稿展开、Tab 到关键帧、Space 展开及 Tab 到大图链接。
- Esc 返回后，查询、视图、地址、原条目焦点及滚动位置恢复，滚动误差不超过 1px；加载失败后也能返回原检索状态。

阅读器完整验证脚本最初停在旧的九项目录断言；在未修改的 `git archive HEAD` 临时副本中复现同一失败。更新为已有十一项后，进一步发现错误页面调用不存在的函数。修复后该脚本整体通过，包含嵌套段落导航、目录、阅读进度、灵感与引用、只读边界、延迟响应、加载失败、封面过渡和四种桌面宽度。过程记录分别保留，没有把初次失败记为通过。

## 实际浏览器检查

使用独立临时 Vault 和随机本机端口，不修改用户资料或状态库。Chrome 检查覆盖 960 / 1440px、亮暗主题、列表、专辑墙、正文和证据。

暗色 960px 下，从搜索专辑墙进入页尾资料，返回前后地址相同、主栏滚动均为 606px、焦点均为 `dy-123`。目录进入逐字稿后实际焦点为其 `SUMMARY`；Tab → Space → Tab 可以展开关键帧并聚焦大图链接。修正后的链接焦点边框完整可见。亮色 960px 下，目录进入 Summary 后按 j，实际焦点为对应正文段落。检查时没有出现横向溢出。

原生 Chrome 界面确认 200% 缩放，有效视口 960×403、`devicePixelRatio=2`。八项搜索结果中向上循环到末项，输入保留焦点；选中项位于结果容器内，两段操作说明均在窗口内。验证后恢复 100% 缩放。

本轮单次静态 Impeccable 检查返回四项既有字体选择提示，均为当前设计系统固定字体；没有增加忽略规则。这不是运行时对比度或完整读屏验收。真实 VoiceOver 朗读仍待人工补验，原因与既有检查边界见[全局搜索修复记录](2026-10-31-command-accessibility-fixes.md)。

证据、隔离脚本、截图及后续部署记录保留在仓库外：`/Users/weisengao/Documents/ChatGPT/douyin-wiki-maintenance/2026-10-31-reading-flow-polish/`。其中 `reader-evidence-dark-960.png` 保留最初发现焦点边框不完整的状态，修正后对应 `reader-frame-focus-dark-960.png`。

## 交付验证

- `uv run --no-sync ruff check src tests scripts/verify_readwise_reader.py`、`git diff --check`：通过。
- `uv run --no-sync pytest -q -m 'not live' --tb=short`：1954 passed，2 deselected，341.14 秒。此前在最终焦点与错误路径修复期间中止的一次运行单独保留为 `pytest-full-interrupted-for-final-fixes.txt`，不作为通过证据。
- `scripts/verify_readwise_reader.py` 完整验证通过；十八项搜索、证据与组合框相关回归通过。
- wheel 内 104 个运行文件与源码逐字节一致。独立临时环境以 `--no-index --no-deps` 安装后，实际导入路径、Web 0.2.100、API 与静态资源均确认正确；安装包十项浏览器回归通过。借用测试依赖时有一条 `anyio` 已导入而无法改写断言的 pytest 提示，未影响用例结果。临时安装环境已删除。

wheel SHA-256：`f35e91fc587ed4bc6f0538eda51cb0f290f52714e6bf64db35878f34548013dd`。

## 本机交付

现用独立 runtime 已更新为上述安装包，Web 与 Worker 重启后确认 Web 0.2.100、Worker 正常且空闲。安装前再次确认没有运行、排队或等待用户的任务，以 `uv pip install --no-index --no-deps --reinstall` 仅替换本项目包。依赖版本、配置和三个 LaunchAgent 文件的哈希保持一致；维护定时任务沿用原注册，没有提前执行。资料库升级前后均为 371 条，实际服务的五个主要 JS / CSS 静态文件与源码一致。

现用解释器仍为 `/Users/weisengao/Library/Application Support/douyin-wiki/runtime/.venv/bin/python`，原仓库 `.venv` 保留。安装备份位于仓库外证据目录的 `deployment/backup-20261031-103520/installed-package.zip`，部署前后探针及安装命令分别为 `deployment/before.json`、`after.json`、`install-commands.json`。

使用现用解释器新建 STDIO MCP 连接，51 项工具名称与源码一致，instructions 保持原契约，`doctor` 总体检查通过。其他聊天中已经存在的旧会话仍由客户端重连。原生 Chrome 对实际 8765 服务复核 Web 0.2.100、两条搜索结果的显示顺序、逐字稿实际焦点、Tab / Space 到关键帧链接及 Esc 返回后的查询和条目焦点；记录为 `deployment/mcp-check.json`、`browser-check.json` 与 `live-search-1440.png`。

本地 0.2.1 wheel 与源码归档按交付内容刷新，替换前备份两个旧的同名文件，更早版本保留；包成员与最终文档检查记录为 `deployment/artifacts-check.json`。临时预览服务与页面已关闭，视口覆盖已清理，浏览器缩放恢复 100%。
