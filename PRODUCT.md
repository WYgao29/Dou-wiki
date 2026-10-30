# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

主要用户是在 Mac 上使用 AI Agent 的个人知识库用户。他们在抖音中发现值得保存的视频和图文，希望把内容变成可以长期保存、检索、阅读并追溯原始证据的资料，而不只是保存一个分享链接。

## Product Purpose

抖库 Dou-wiki 将抖音作品整理成本地 Obsidian Markdown 知识库。用户把分享文本、作品链接或批量范围交给 Agent，由本地工具提取文字，再由该 Agent 完成校正与分析。

成功意味着用户以后能找到相关知识，读懂整理后的内容，并通过原作品链接、视频时间范围或图片编号核对证据。采集完成并生成摘要只是这个过程的一部分。

## Positioning

产品采用本地媒体处理与 Agent 分析协作的机制：下载、语音识别和图片文字识别在 Mac 上执行，提取后的文字交给用户已经使用的 Agent 模型。抖库不保存对话模型密钥，也不在 Worker 中运行独立的对话分析后端。

资料同时保留为用户持有的 Markdown 文件和可检索的本地索引。来源与证据定位是知识内容的一部分；AI 推断和用户灵感分别保留，不作为作品原话呈现。

## Operating Context

- 用户通过 Agent 对话发起单条采集，或指定博主、收藏夹、合集的导入范围。Agent 使用 MCP（模型上下文协议，供 Agent 调用本地工具的接口）执行操作。
- 本地 Worker 处理持久任务。视频完成转写后等待 Agent 校正，再等待分析；静态图文完成文字识别后等待分析。任务、检查点与事件支持状态查询和后续接续。
- 批量导入先清点作品，再选择并确认。Web 保留已有批次的选择、确认、取消、重试和任务进度管理。
- 用户在本机 Web 或 Obsidian 中阅读资料，通过关键词或本地文字语义检索寻找证据，并管理库内收藏、标签、灵感和专题来源。
- 专题研究由 Agent 基于当前启用来源完成；笔记通过现有工具保存。Web 用于管理来源和阅读笔记，不提供 AI 聊天或专题自动生成。
- CLI 负责安装、授权、运行、诊断和维护；新采集等业务请求由 Agent 通过 MCP 发起。

## Capabilities and Constraints

- 采集对象是抖音视频和静态图文，支持分享文本、单条作品及博主、收藏、合集批量流程。各类作品的支持范围以当前工具契约与实际识别结果为准。
- 部署面向 macOS；当前 Qwen3 本地语音识别依赖 Apple Silicon。前端是 FastAPI / Jinja2 提供的本机网页，使用原生 JavaScript 和 CSS。
- Web 只监听本机回环地址。界面只支持桌面浏览器，最窄按约 960px 设计；更窄窗口保持最小宽度并允许横向滚动，不以手机布局为交付目标。
- 用户 Vault（Obsidian 知识库目录）与源码仓库分离，保存原始记录、资料页、结构化附属数据和媒体；SQLite 保存任务、事件及检索索引。重建知识索引不等于恢复全部任务历史。
- 媒体由本机下载和处理，交给 Agent 分析的是提取后的文字。媒体保留和关键帧保存遵循用户配置与条目策略，不承诺所有媒体永久存在。
- 搜索包含关键词检索和可选的本地文字语义检索。语义模型不可用时返回带说明的关键词结果；未集成的画面检索方案不属于现有能力。
- 视频时间精度必须沿用实际材料。只能定位到识别窗口时，应说明真实时间范围，不能显示为更精确的句子时间；图文证据使用实际图片编号。
- 已有 MCP、CLI、Service、Web 路径与负载、静态资源 URL 和 Vault 格式的兼容要求遵循 [AGENTS.md](AGENTS.md) 及其引用的契约。

## Brand Commitments

产品名称为“抖库 Dou-wiki”，Web 界面产品名为“Dou-wiki”。界面以简体中文提供操作与状态说明。

已有名称、图标、亮暗主题资源及字体许可都是当前产品资产，位置见 `src/douyin_wiki/webapp/static/` 与 [字体说明](scripts/fonts/README.md)。Python 包、CLI/MCP、配置路径和环境变量继续使用既有技术标识，不随品牌名称变化而重命名。

## Evidence on Hand

- [README](README.md) 与 [中文 README](README.zh-CN.md) 包含已实现的功能、安装方式、工作流程和真实本机截图说明。
- `docs/images/web-library.png`、`web-article.png`、`web-jobs.png`、`web-creators.png` 是 README 使用的历史界面截图；`docs/images/obsidian-note-source.png` 展示生成的 Obsidian Markdown。截图不是当前界面或当前安装版本的自动证明。
- [项目结构与处理流程](docs/architecture.md)、[Gateway Agent 接入指南](docs/gateway-agents.md) 和 [Service 协作 API](docs/service-collaboration-api.md) 记录处理机制、证据要求及工具协作契约。
- [Web UI 设计系统](docs/web-ui-design-system.md) 是当前界面约定；本文件只保存产品背景，不复制其视觉令牌和组件规格。
- [项目文档索引](docs/README.md) 指向开发方案、维护记录与有明确范围的历史验收证据。历史版本、数量和性能数据不能直接作为当前运行状态或普遍承诺。

## Product Principles

1. 让保存的内容以后找得到、读得懂、核对得了，而不只追求导入数量或摘要长度。
2. 保留来源边界，区分作品原文、文字识别结果、AI 推断与用户灵感；不伪造证据或提高时间精度。
3. 让本地工具处理媒体和持久状态，让用户已经使用的 Agent 完成文字校正、分析和研究。
4. 保持用户对知识文件、来源选择和保存结果的控制；维护操作遵循既有预览、确认与恢复流程。
5. 让阅读、检索和任务状态容易理解，持续保留桌面键盘操作与辅助技术所需的交互语义。

## Accessibility & Inclusion

当前 Web 已有键盘导航、可见焦点、跳到主要内容、语义标签及隐藏面板的焦点管理，并响应系统“减少动态效果”设置。后续界面工作应保留这些能力；具体行为与验证范围见 [Web UI 设计系统](docs/web-ui-design-system.md)。

## Open Decisions

本次没有新增特定人群的无障碍需求、正式符合性等级或量化产品成功指标；后续出现实际需求时再确认，不从现有实现推导额外承诺。
