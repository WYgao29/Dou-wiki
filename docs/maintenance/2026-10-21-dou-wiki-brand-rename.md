# Dou-wiki 品牌与仓库改名（2026-10-21）

项目对外名称改为 **抖库 Dou-wiki**，Web 品牌区和页面标题使用 **Dou-wiki**。GitHub 仓库现为 [WYgao29/Dou-wiki](https://github.com/WYgao29/Dou-wiki)，默认分支仍为 `main`；原地址已实测通过 HTTP 301 跳转到新地址，最终返回 200。仓库简介增加 `Dou-wiki —`，其余介绍保留。

本轮基于 `73807a0`，按用户要求分三次提交。开始时工作区干净，本地已有的 `c3fd660` 和 `73807a0` 两条界面提交在告知用户后一并保留并推送。

| 步骤 | 提交 | 结果 |
| --- | --- | --- |
| Web 品牌 | `9e7722b49a597a87a1db543ce5cd4294ce5c3d0a` · `feat(ui): Dou-wiki brand mark and wordmark` | 替换品牌区，增加主题图标、本地 700 字重和两种 favicon；Web 版本升至 0.2.89 |
| 文档 | `0281b9ce35ef14b8b4881c10f325088581995373` · `docs: rename project to Dou-wiki` | 中英文 README 同步显示新名称和 logo，更新仓库地址并解释兼容标识 |
| GitHub | 本记录所在提交 · `chore: record Dou-wiki repository rename` | 前两次提交推送后，通过 `gh` 改名与修改简介，更新 remote 并验证网页和跳转 |

GitHub 设置变更本身不会产生 Git commit，第三次提交保存本记录与文档索引。

## Web 与文档检查

- `uv run --no-sync ruff check src tests`：通过。
- `uv run --no-sync pytest -q tests/test_web*.py -m 'not live'`：118 passed。
- 独立临时 Vault 的 Chromium 检查覆盖 17 个页面、1440 / 1280 / 1100 / 960px 的深浅主题共 8 组布局，以及跟随系统、手动覆盖、刷新后保留主题、点击品牌回首页。图标为 22×22px，间距 8px，字标为 Space Grotesk 700、18px / 1、字距 -0.065em，颜色分别为 `#16171A` / `#ECECEE`；无横向溢出、外部字体请求、页面脚本错误或服务端 5xx。
- 正文保持 Inter 14px / 22px，阅读字体和文章页隐藏品牌区的既有行为保留。原有主题键 `douyin-wiki.theme` 继续有效。
- 700 字重从已有固定上游版本提取，文件 31,580 bytes，无可变轴，随应用提供 SIL OFL 1.1 许可；来源、生成方法和哈希见[字体说明](../../scripts/fonts/README.md)。
- 浅色 SVG 原样用作 SVG favicon；Chromium 按原 SVG 的遮罩绘制 32×32px 透明 PNG。8 个新旧静态资源 URL 均返回 200，内容类型正确。
- 第一步构建的独立 wheel 包含新 SVG、PNG、700 字重及许可，逐文件与源码字节比较一致；未替换仓库 `dist/` 或现用 runtime。wheel SHA-256 为 `5fe6ac9973141cc59f62fd03284ab6d57a65b9100681783947bb882dadbfd0e6`。
- 两份 README 的四个 Bash 示例块除 clone 地址和显式目标目录外原样保留；JSON 配置及原有行内技术标识未变，中英文命令逐行一致。全部本地图片引用存在，现有文本中的旧 GitHub 仓库链接已更新。

## GitHub 检查

按以下顺序执行，均成功：

```sh
git push origin main
gh repo rename Dou-wiki --repo WYgao29/douyin-wiki --yes
git remote set-url origin https://github.com/WYgao29/Dou-wiki.git
git fetch origin
```

随后执行 `gh repo edit WYgao29/Dou-wiki --description ...`，在原简介前增加 `Dou-wiki —`。`gh repo view` 返回 `nameWithOwner="WYgao29/Dou-wiki"`、默认分支 `main`、权限 `ADMIN`；remote 的 fetch / push 地址均为 `https://github.com/WYgao29/Dou-wiki.git`。改名后 fetch 成功，当时本地与远端主线均为 `0281b9c`。

已在浏览器实际打开新地址，README 顶部的新 SVG logo、标题“抖库 Dou-wiki”和新徽章链接均正常显示；再打开旧地址，浏览器最终地址变为新地址。独立 HTTP 请求也确认一次 301 后返回新地址的 200。

本机验证脚本、JSON 结果、wheel 和截图保存在仓库外：`/Users/weisengao/Documents/ChatGPT/douyin-wiki-maintenance/2026-10-21-dou-wiki-brand/`。主要证据为 `brand-check.json`、`docs-check.json`、`github-before.json`、`github-after.json`、`github-redirect.json`、`github-readme.png` 和 `library-*-*.png`；此路径只是本机维护记录位置，不是安装前提。

## 按要求保留的内容

- Python 发行名 `douyin-wiki`、导入包 `douyin_wiki`、CLI `douyin-wiki`、`douyin-wiki-mcp` 入口及 MCP 服务器名不变。
- 配置路径、Vault 路径与格式、状态库、浏览器 Profile、环境变量和已有 Web 偏好键不变；不迁移用户数据，也未更新或重启现用 runtime。
- 两份 README 使用 `git clone https://github.com/WYgao29/Dou-wiki.git douyin-wiki`，以保留原有 `cd douyin-wiki` 和后续安装、运行、配置示例。
- `docs/images/banner.png` 和 `docs/images/banner-zh.png` 都含旧 `douyin-wiki` 字样与旧图标，按用户要求只报告、不重做。SHA-256 分别为 `a57b7126a04c6b28156d942103f1449aec8e92f0c738106bdbd71a1bff48af8c`、`3adcdbdcedca26eb3393f05ab156cdf163bdaa8900efef7566378f4adff58008`。
- README 里的历史界面截图保持原样；旧的未使用 `brand-mark.png` 和供 UI 标题使用的 `SpaceGrotesk-Variable.woff2` 保留，已有静态资源 URL 继续可访问。品牌区不再使用旧文字或旧图标。

## 修改文件

Web 文件均位于 `src/douyin_wiki/webapp/`：

- `templates/app.html`：新品牌链接、主题图标和 favicon 声明。
- `static/app.css`：字标、本地 700 字重、尺寸、间距和主题颜色。
- `static/dou-wiki-mark.svg`、`static/dou-wiki-mark-dark.svg`、`static/favicon.svg`、`static/favicon-32.png`、`static/fonts/SpaceGrotesk-700.woff2`：新品牌资源。
- `static/app.js`、`static/shared.js`、`static/jobs.js`、`static/auth.js`、`static/tags.js`、`static/analysis-settings.js`、`static/system-settings.js`、`static/imports-single.js`、`static/imports-creators.js`、`static/imports-favorites.js`、`static/imports-collections.js`：页面标题及界面产品名。
- `app.py`、`version.py`：Web 应用标题和静态资源缓存版本。

其他修改：

- `tests/test_web_ui_audit.py`：同步已有页面标题断言。
- `scripts/fonts/README.md`、`scripts/fonts/output-sha256.json`：700 字重的生成、许可和哈希记录。
- `README.md`、`README.zh-CN.md`：中英文品牌、logo、仓库链接和兼容说明。
- `AGENTS.md`、`docs/gateway-agents.md`：当前仓库与产品名称。
- `docs/maintenance/2026-10-17-deployment-feedback-improvements.md`：更新仓库链接并标明更名日期。
- `docs/web-ui-design-system.md`：同步当前品牌区规格、字体与主题颜色。
- `docs/README.md` 与本文件：维护索引和本轮验证记录。
