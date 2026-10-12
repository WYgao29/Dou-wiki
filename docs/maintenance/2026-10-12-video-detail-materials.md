# 视频详情页：完整逐字稿与本地视频

日期：2026-10-12。当前包 0.2.1，Web 0.2.50。用户要求增加默认折叠的完整逐字稿和本地视频 URL；实现、验证及本机 Web 更新均已完成。

## 页面行为

视频正文的来源章节之前增加「完整逐字稿」，首次打开时默认折叠。展开后显示全部段落的起止时间和文字，优先使用已保存的校正稿，没有校正稿时使用原始识别稿，并标明稿件来源和段数。文字按纯文本渲染，保留换行；同一文章刷新保留展开状态，引用定位到折叠区内文字时自动展开。没有稿件时显示「暂无可用逐字稿」。图文和只读 Markdown 不增加该区。

已保留的视频在栏头增加「本地视频」链接，信息栏显示完整本机 URL，并提供复制按钮。URL 使用当前网页来源和 `/api/articles/{entry_id}/video`，不包含磁盘绝对路径。文件已清理或不存在时显示不可用；逐字稿继续可读。

## 实现与边界

| 文件 | 修改与原因 |
| --- | --- |
| `src/douyin_wiki/webapp/catalog.py` | 从当前资料数据读取完整逐字稿，选择校正稿或原始稿；根据该条资料的媒体状态、路径和 assets 目录检查本地视频 |
| `src/douyin_wiki/webapp/app.py` | 详情响应增加 `transcript` 和 `item.local_video_url`；新增 GET / HEAD 视频接口，使用 FileResponse 支持 Range |
| `src/douyin_wiki/webapp/static/app.js` | 渲染默认折叠的全文、视频打开和 URL 复制入口；处理刷新与引用定位 |
| `src/douyin_wiki/webapp/static/app.css` | 增加逐字稿时间段和视频 URL 的样式，长 URL 可换行 |
| `src/douyin_wiki/webapp/version.py` | Web 版本由 0.2.49 更新为 0.2.50，让浏览器加载新资源 |
| `tests/test_video_detail.py` | 验证完整校正稿、原始稿、Range / HEAD、媒体清理、图文和文件访问范围 |
| `tests/test_usability_browser.py` | 验证桌面 / 手机默认折叠、31 段全文、纯文本渲染、打开和复制入口及空材料状态 |
| `README.md`、`docs/web-ui-design-system.md`、`CHANGELOG.md` | 更新使用说明、当前界面约定和变更记录 |

视频接口只读取该条已入库视频的 assets 目录，检查解析后的路径仍在 Vault 内，拒绝外部路径、其他条目的媒体、越界符号链接和非视频文件。现有 `/media/` 图片接口继续保持原范围。资料库列表响应不增加逐字稿读取；详情响应保留原有字段并增加材料字段。

## 验证

| 检查 | 结果 |
| --- | --- |
| Web、资料阅读与恢复相关测试 | 93 passed |
| 完整浏览器交互回归 | 25 passed，包含桌面 1440px 和手机 390px 的新功能 |
| Ruff、JS 语法与 diff 检查 | 通过 |
| 阅读器专项脚本 | 通过，覆盖目录、阅读定位、灵感和亮暗 / 手机布局 |
| 新功能视觉与视频检查 | 默认折叠、手机无横向溢出、无浏览器异常；本机生成的 2 秒 MP4 可打开、解码并拖动到 0.7 秒 |
| 独立环境安装实际 wheel | 15 passed；导入来源为该环境 site-packages，90 个运行文件逐字等于源码和 wheel |
| 当前 runtime 与线上 Web | 90 个安装文件等于 wheel；Web 0.2.50、Worker 健康、无运行或排队任务；线上 JS / CSS 等于安装文件 |
| 当前 runtime 的正向材料读取 | 临时 Vault 的完整逐字稿、视频链接与 Range 206 通过 |

功能验证使用独立临时 Vault、合成逐字稿和本机生成的 MP4。现用知识库当前没有已入库资料，线上检查验证资源和缺失视频的 404；正向材料读取使用实际安装 runtime 与临时资料。

本机更新使用已验证的 wheel，仅替换抖库包，保持运行依赖、配置与 LaunchAgent 文件字节不变。首次重新注册服务返回错误，旧包已恢复并逐字核对；等待后旧 Web 恢复运行。更新脚本增加服务注册的等待重试，第二次更新及全部后续检查通过。视觉检查脚本首次在浏览器原生视频页使用字符串等待时受到 CSP 限制，改用属性断言后通过；产品 CSP 保持原配置。

## 证据与恢复

仓库外材料：`/Users/weisengao/Documents/ChatGPT/maintenance/douyin-wiki/video-detail-2026-10-12-uam8gz89/`。

- `ui-check.json`、桌面 / 手机截图和 `reader-regression/reader/verification.json`：界面、视频和阅读器验证。
- `wheel-check.json`、`dist/douyin_wiki-0.2.1-py3-none-any.whl`：安装包及 90 个运行文件核对。
- `runtime-before.zip`、`runtime-before.json`：更新前包文件、配置哈希、依赖版本与运行状态。
- `runtime-install.log`、`deployment.json`、`live-check.json`：本机更新和实际 HTTP / 安装代码检查。
- `inspect_ui.py`、`deploy_web.py`：本次独立检查和带恢复处理的更新脚本。

wheel SHA256：`84991bea4ba079ba7d206f4675863c346e417bb16b79fd1f3ee2c808d5ab15b9`。
