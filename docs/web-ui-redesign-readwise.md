# 抖库 Web UI 重设计规格：Readwise Reader 风格

> 状态：已按代码审查修订，分阶段实施中（2026-09-30）
> 日期：2026-09-29
> 适用范围：`src/douyin_wiki/webapp/templates/app.html` + `src/douyin_wiki/webapp/static/*`（单模板 SPA，Jinja2 + 原生 JS，服务于 127.0.0.1）
> 取代：`docs/web-ui-design-system.md`（Apple 风格重构）。实现完成后该文档改写为本规格的"现状版"（见第 6 章 Phase 6）。

---

## 0. 现状盘点（本规格基于的真实代码）

| 项 | 现状 |
|---|---|
| 模板 | 只有一个 `templates/app.html`（326 行），所有页面都是 `<section id="*-view">`，由 JS 切换 `.hidden` |
| 路由（HTML） | `/`、`/articles/{id}`、`/topics`、`/topics/{id}`、`/trash`、`/imports`、`/imports/single`、`/imports/creators`、`/imports/favorites`、`/jobs`、`/jobs/{id}`、`/settings`、`/settings/auth`、`/settings/analysis`、`/settings/model`、`/settings/system`、兜底 `/{path}` → not-found（`webapp/app.py`、`webapp/operation_api.py`） |
| 视图 ID（`shared.js VIEW_IDS`） | `library-view` `article-view` `topics-view` `topic-view` `trash-view` `imports-view` `imports-single-view` `imports-creators-view` `imports-favorites-view` `jobs-view` `job-detail-view` `auth-view` `system-view` `analysis-view` `model-view` `settings-view` `not-found-view` |
| JS | `app.js`（2464 行：资料库、文章、专题、废纸篓、AI 对话、命令面板、快捷键）、`shared.js`（`window.Douku`：路由、toast、setPage、confirmDanger）、`jobs.js`、`auth.js`、`imports-single.js`、`imports-creators.js`、`imports-favorites.js`、`system-settings.js`、`analysis-settings.js`、`model-settings.js`、`theme.js` |
| CSS | `app.css` 一个文件，877 行 / 91 KB（大量单行长规则） |
| 图标 | `static/icons.svg`：Lucide 风格描边 symbol（library、clock-3、sparkles、search、panel-left/right、list、layout-grid、filter、bookmark、tag、trash-2、x、send、check、eye…）；侧栏/顶栏/对话区另有**内联实心 SVG**（design doc 中称"SF Symbols 风格"） |
| 条目数据（`LibraryItem`，`models.py:659`） | `entry_id` `database_managed` `title` `author` `cover_url` `summary`（一句话）`content_type`（→`content_type_label`：教程/知识解释/观点/推荐/新闻事件/故事案例/清单/其他）`tags` `inspirations` `published_at/display` `captured_at/display` `status` `favorite` `media_status` `retention` `source_kind`（video / image_note）`original_url` `creator_id`。**没有时长字段** |
| 文章正文结构（`vault.py:1442+`） | `# 标题` → 封面图 → `## 灵感` → `## 一句话` → `> [!note] AI 推断：与灵感的关系` → `## 核心收获` → `## 内容卡片 · 类型`（图文）→ `## 原图` → `## 时间轴图解`（`### ▶ 01:05　一、章节名` + 要点 + 对比表）… |
| 任务状态（`localization.py JOB_STATUS_LABELS`） | queued 待处理 / resolving / inventorying / needs_selection 待选择作品 / dispatching / monitoring / downloading / extracting / transcribing / awaiting_agent_analysis 待 AI 处理 / needs_auth / needs_review / waiting_confirmation / analyzing / completed / completed_with_warnings / failed |
| AI 对话 | 右侧 `<aside id="chat-panel">`，会话 scope：library / entry / topic；SSE 流式（meta / delta / done / error），带引用 citations（`makeCitation`），可"保存为灵感"/"保存为专题笔记" |
| 已有快捷键（`app.js handleGlobalKeydown`） | `⌘/Ctrl+K` 命令面板、`/` 聚焦搜索、`[` 切左栏、`]` 切右栏、`Esc` 关闭浮层 |
| 截图 | `docs/screenshots/` **不存在**。参考图在 box：`/workspace/doku/album-wall-desktop.jpg`、`/workspace/doku/article-desktop.jpg`、`/workspace/doku/refs/readwise-reader.jpg` |

---

## 1. 设计原则与"去 AI 味"清单

### 1.1 为什么现在看起来像 AI 生成的界面（逐条对应代码）

| # | 症状 | 代码证据 | 处理 |
|---|---|---|---|
| 1 | **渐变 + 内高光的拟物按钮** | `app.css :root` `--button-primary-face: linear-gradient(180deg, #1483f6, #0071e3)`，`.primary-button { box-shadow: 0 1px 2px …, inset 0 1px 0 rgba(255,255,255,.28) }`（line 182） | 删除。主按钮 = 纯色 accent，无阴影 |
| 2 | **紫色渐变 AI 徽标 + ✨ 星芒图标** —— 最典型的"AI 产品"套话 | `.ai-mark { background: linear-gradient(180deg, #c96ef6, #a83ae6); box-shadow: 0 2px 8px … }`（line 408）；星芒 path 在 app.html 出现 3 次（灵感导航、`#chat-toggle`、chat 标题）+ `app.js makeWelcome()` 1 次 | 删除 `.ai-mark` 与星芒。AI 就是右栏里的一个 Tab，文字"AI 对话"，图标用 `message-square` 线性图标 |
| 3 | **彩虹瓷贴导航** | 9 个 `--nav-*` iOS 系统色（`--nav-library #0071e3` `--nav-recent #ff9f0a` `--nav-favorite #ff375f` `--nav-inspiration #bf5af2`…），`.nav-item svg { width:28px; height:28px; padding:6px; border-radius:8px; background:… }`（line 223），`.import-entry:nth-child(2) { --tile: var(--nav-image) }`、`.trash-item-icon.is-video { --tile: var(--nav-video) }` | 全部删除。Chrome 单色，唯一的色彩来自封面图 |
| 4 | **四层阴影卡片 + 卡片套卡片** | `--shadow-card`（4 层含 inset 白高光）、`--shadow-card-lifted`，`box-shadow` 共 67 处；`.gallery-cover` 有 shadow（line 339）；`status-card` / `system-panel` / `operation-grid` 在导入页、设置页里层层嵌套 | 卡片零阴影，1px 边框；设置/导入等页面改为"标题 + 细分隔线"的平铺分区 |
| 5 | **毛玻璃滥用** | `--glass-blur: saturate(180%) blur(20px)`，`backdrop-filter` 14 处（侧栏、侧栏 footer、对话栏、顶栏、toast），但它们下面并没有需要透出的内容 | 删除全部 `backdrop-filter`，改为不透明底色 |
| 6 | **圆角没有体系** | `--radius-xs/sm/md/lg/xl` = 6/9/12/16/22 + pill，另有散落的 `10px` `9px` `8px` `3px`；共 15 种写法 | 只保留 6px（控件/卡片）+ 4px（tag/kbd/checkbox） |
| 7 | **演示型动效** | `:active { transform: scale(.97) }` 挂在 12 组选择器上；`--ease-spring: cubic-bezier(.5,1.35,.4,1)`；专题卡 `nth-child(2..5) { animation-delay: 40/80/120/160ms }` 阶梯入场；专辑墙 `--enter-delay: index*35ms`；消息 `message-enter` 上浮 | 删除按压缩放、弹簧、阶梯入场。只保留：颜色/边框 100ms、浮层 160ms 淡入、面板收起 240ms |
| 8 | **字号过多，阅读字体未使用** | 9 个字号 token + 散落 `11px` `.9375rem`；`.article-body { font-family: var(--font-ui); font-size: var(--text-body) }`（line 385）——定义了 `--font-reading` 却没用在正文；正文 `h2` 被做成 13px、tertiary 灰、`letter-spacing:.08em` 的小标签（line 387），"核心收获"这类章节标题看起来像表单 label | 7 级字号；正文改宋体 17/1.75；h2 恢复为真正的标题 |
| 9 | **解释系统的长段落文案**（AI 写的 UI 最明显的特征） | `imports-view` 标题下："…入库后可在资料库多选文章，再创建专题。侧栏进入本枢纽页；顶栏「+ 导入内容」为快捷菜单。"；`jobs-view`："「进行中」是机器正在处理，不含待选择和失败。"；`model-view`、`analysis-view` 各两三句 | 页面副标题 ≤ 1 行（≤ 24 字）。说明性内容移入字段 hint（12px）或 `?` 帮助 |
| 10 | **顶栏堆 8 个控件** | 搜索、导入、筛选、排序、视图切换、密度、"从资料库选择来源"、AI 按钮，全在一个 `.topbar` 里，任何页面都显示 | 每一栏自己的 48px 栏头，只放本栏相关控件（Readwise 模式） |
| 11 | **多种强调色并存** | Apple 蓝 `#0071e3` + 9 个瓷贴色 + 紫渐变 + 状态色 | 一个 accent；状态色只在任务中心 |
| 12 | **每页都有"← 资料库"面包屑** | 16 处 `<nav class="article-breadcrumb">` | 删除（侧栏已提供导航）。只在文章阅读与任务详情保留"返回"按钮（放在栏头） |
| 13 | **AI 生成的品牌图** | design doc：`brand-mark.png` "TikTok 风 logo… AI 生成后经过去水印与压缩"，黑底 + 青/品红错位重影 | UI 中停用；改为纯文字字标"抖库"（文件保留不删） |
| 14 | **对话栏是浮层而非第三栏** | `.chat-panel { position: fixed; z-index: 80; box-shadow: var(--shadow-dialog); transform: translateX(105%) }`（line 404），默认 `chat-collapsed = "true"` | 宽屏时固定第三栏，不再是浮层 |
| 15 | **结构 bug** | `app.html` 第 240 行 `</main>` 之后才出现 `<section id="settings-view">`（line 242）和 `not-found-view`（line 252），它们在 `<main>` 外面 | 移回 `<main>` 内 |

### 1.2 设计原则

1. **三栏固定，内容为王**：左导航 240 / 中间列表或阅读 / 右侧信息面板 360。Chrome 只用中性色；色彩只来自封面与唯一 accent。
2. **统一行**：资料、任务、专题、废纸篓、导入清单都用同一个"列表行"组件（缩略图 · 标题 · 摘要 · 元信息），只变信息，不变骨架。
3. **密而静**：信息密度参考 Readwise（每屏 8–10 行），但没有阴影、渐变、动画装饰；层级靠字重、灰度和 1px 分隔线。
4. **一个强调色，四种用途**：选中（选中行左边条、选中导航图标、选中 Tab 下划线）、主按钮、高亮（引用定位 `<mark>`、灵感标记）、进度条。其余一律中性。
5. **键盘优先**：列表 `j/k`（及方向键）移动焦点、`Enter` 打开、`Esc` 返回、`[` `]` 开合两侧栏、`⌘K` 命令面板、`?` 快捷键表。
6. **中文阅读优先**：正文宋体 17px / 1.75 行高 / 约 36 字行宽；UI 用无衬线。
7. **文案克制**：一行标题 + 一行副标题；解释放 hint；按钮用动词（"导入""重试""恢复"）。

### 1.3 从 Readwise Reader 借鉴什么（已核实）与不借鉴什么

已核实的 Readwise Reader 事实（来源：readwise.io/read、docs.readwise.io/reader、官方宣传截图 `/workspace/doku/refs/readwise-reader.jpg`）：

- 网页版三栏：左侧导航；中间文档列表；右侧面板包含 **Info、Notebook、Chat** 三个区域（docs › Appearance）。阅读文档时，左侧面板是**目录**（"table of contents (the left side panel)"）。
- `[` 收起/展开左侧面板，`]` 收起/展开右侧面板（docs › Appearance / Navigation）。
- `⌘/Ctrl+K` 打开命令面板，几乎所有操作都在里面；`?` 打开快捷键参考（docs › Basics）。
- 阅读时 **↑/↓ 按段落移动焦点指示器**，`H` 高亮段落，`Shift+N` 在 Notebook 里写文档笔记，`Shift+F` 建立过滤视图，`Shift+M` 在 Info 面板编辑元数据（docs › Highlights, Tags, and Notes / PDFs）。
- 列表行（截图可见）：左侧缩略图，标题一行，摘要一行（截断），元信息行 = 来源图标 · 作者 · 阅读时长 · 标签胶囊；右上角日期；hover 时右侧出现操作图标（`…` 与若干图标）；当前焦点行左侧有一条彩色竖条，行底有阅读进度条；列表上方有 LATER / SHORTLIST / ARCHIVE 分段标签和 "Manage tags"、"Last opened ▾" 排序。
- 官方描述："Keyboard-based reading"、"minimalist interface"。

**注意**：Readwise 官方文档里列表导航用的是方向键；`j/k` 是本规格额外加的 Vim 式别名，不要宣称是 Readwise 的默认键。宣传截图是暗色主题，本规格的亮色配色是自定义的，不来自 Readwise。

不借鉴：Readwise 的 Inbox/Later/Archive 分诊流程（抖库没有"已读/归档"数据）、TTS、段落高亮 `H`（抖库没有高亮数据模型；对应物是"灵感"）。

---

## 2. 设计令牌（Design Tokens）

### 2.1 强调色选择：墨绿 `#2F6F4F`

| 维度 | 墨绿 #2F6F4F | 朱红 #E5484D |
|---|---|---|
| 作白底文字的对比度 | 6.0:1（暖白底 5.7:1），AA 通过，可直接用于链接/选中文字 | 3.9:1，小字不达 AA，需要另配一个更深的文字色 |
| 作按钮底 + 白字 | 6.0:1 | 3.9:1（14px 按钮文字不达标） |
| 与语义色冲突 | 不与危险色（红）冲突；与"成功"同色系，但"成功"只在任务中心出现，且直接复用 accent（"完成"＝"对"） | 与危险/失败红冲突；"删除""彻底删除"会和主按钮同色 |
| 与内容冲突 | 抖音封面普遍高饱和红/青/品红，墨绿在其中最安静、不抢封面 | 与抖音品牌红同色，封面墙上会被淹没 |
| 气质 | "墨"＋暖白纸＋宋体，书房感，契合"个人知识库" | "朱批"的隐喻很好，但做 UI 主色太吵 |

**结论：主强调色 = 墨绿 `#2F6F4F`**（暗色模式 `#6FB38E`）。朱红保留为备选：只改 `--accent*` 5 个变量即可切换（2.2 里附了朱红变量值，注释状态）。

### 2.2 可直接粘贴的 `:root`（替换 `app.css` 第 1–140 行的两个令牌块）

```css
/* ==========================================================
   抖库 Design Tokens — Readwise-style redesign (2026-09)
   只允许使用本块中的值。新增值必须先加到这里。
   ========================================================== */
:root {
  color-scheme: light;

  /* ---------- Color: neutrals (light) ---------- */
  --bg: #FAF9F7;               /* 画布：左栏、中栏背景 */
  --surface: #FFFFFF;          /* 右栏、输入框、弹窗、卡片 */
  --surface-sunken: #F4F2EE;   /* 用户消息、代码、骨架屏、tag 底 */
  --surface-hover: #F1EFEB;    /* 行/导航 hover */
  --surface-selected: #ECEAE5; /* 选中行底色（配合 accent 左边条） */
  --text: #1A1A1A;
  --text-2: #666666;           /* 次要文字：5.457:1 on --bg；选中底色最低 4.776:1 */
  --text-3: #6C6965;           /* 辅助正文、placeholder、kbd、计数、时间戳；最低 4.541:1 */
  --border: #ECEAE6;           /* 默认 1px 分隔线/边框 */
  --border-strong: #D9D6D0;    /* hover 时加深的边框 */
  --overlay-backdrop: rgba(26, 26, 26, .32);

  /* ---------- Color: accent（唯一强调色：墨绿） ---------- */
  --accent: #2F6F4F;
  --accent-hover: #285F44;
  --accent-pressed: #214F39;
  --accent-subtle: rgba(47, 111, 79, .10);   /* 选中 tag、引用高亮底 */
  --accent-text: #2F6F4F;                     /* accent 作为文字时 */
  --on-accent: #FFFFFF;
  /* 备选：朱红（切换时替换以上 6 行）
  --accent: #E5484D; --accent-hover: #D63E43; --accent-pressed: #C3373C;
  --accent-subtle: rgba(229, 72, 77, .10); --accent-text: #C4323A; --on-accent: #FFFFFF; */

  /* ---------- Color: semantic ---------- */
  --danger: #C62828;           /* 仅：确认弹窗里的危险按钮、"彻底删除"文字按钮、表单校验错误 */
  --danger-subtle: rgba(198, 40, 40, .08);

  /* 状态色：只允许在任务中心（jobs-view / job-detail-view）使用 */
  --status-queued: #6B6B6B;   --status-queued-bg: #F1EFEB;
  --status-running: #2563EB;  --status-running-bg: #EEF3FE;
  --status-waiting: #B45309;  --status-waiting-bg: #FDF4E6;
  --status-failed: #C62828;   --status-failed-bg: #FCEDED;
  --status-done: #2F6F4F;     --status-done-bg: rgba(47, 111, 79, .10);

  /* ---------- Focus ---------- */
  --focus-ring: 0 0 0 2px var(--bg), 0 0 0 4px color-mix(in srgb, var(--accent) 60%, transparent);

  /* ---------- Elevation：只有浮层可以有阴影 ---------- */
  --shadow-overlay: 0 8px 24px rgba(26, 26, 26, .12), 0 0 0 1px var(--border);

  /* ---------- Typography ---------- */
  --font-ui: "Inter", "InterVariable", -apple-system, BlinkMacSystemFont, "PingFang SC",
             "Hiragino Sans GB", "Noto Sans SC", "Microsoft YaHei", sans-serif;
  --font-reading: "Newsreader", "Doku Serif SC", "Noto Serif SC", "Source Han Serif SC",
                  "Songti SC", "STSong", serif;   /* 拉丁字符走 Newsreader，汉字回落到思源宋体 */
  --font-mono: "SF Mono", ui-monospace, Menlo, Consolas, monospace;

  /* 字号：只允许 12/13/14/16/20/28/40；阅读正文 17 是唯一例外 */
  --fs-12: .75rem;
  --fs-13: .8125rem;
  --fs-14: .875rem;
  --fs-16: 1rem;
  --fs-20: 1.25rem;
  --fs-28: 1.75rem;
  --fs-40: 2.5rem;
  --fs-reading: 1.0625rem;     /* 17px */
  --lh-reading: 1.75;
  --measure: 36em;             /* 17px × 36 ≈ 612px ≈ 36 个汉字 */

  --fw-regular: 400;
  --fw-medium: 500;
  --fw-semibold: 600;

  /* ---------- Spacing：4px 基数，只允许以下 8 个值 ---------- */
  --space-1: 4px;
  --space-2: 8px;
  --space-3: 12px;
  --space-4: 16px;
  --space-6: 24px;
  --space-8: 32px;
  --space-12: 48px;
  --space-16: 64px;

  /* ---------- Radius / Border ---------- */
  --radius: 6px;               /* 按钮、输入框、卡片、封面、弹窗、菜单 */
  --radius-sm: 4px;            /* tag、kbd、checkbox、状态徽章 */
  --border-width: 1px;

  /* ---------- Motion ---------- */
  --dur-fast: 100ms;           /* 颜色、边框、背景 */
  --dur-base: 160ms;           /* 浮层/弹窗/Toast 淡入、Tab 切换 */
  --dur-panel: 240ms;          /* 侧栏收起/展开、抽屉 */
  --ease: cubic-bezier(.2, 0, 0, 1);          /* 进入 */
  --ease-in-out: cubic-bezier(.4, 0, .2, 1);  /* 面板开合 */

  /* ---------- Z-index ---------- */
  --z-base: 0;
  --z-main: 1;
  --z-sticky: 10;              /* 栏头、底部确认条 */
  --z-panel: 20;               /* 覆盖模式下的右栏 */
  --z-popover: 50;             /* 筛选、导入菜单、会话菜单（测试断言 z-index: 50） */
  --z-drawer: 80;              /* 窄屏抽屉 */
  --z-modal: 100;
  --z-toast: 120;

  /* ---------- Layout ---------- */
  --sidebar-width: 240px;
  --panel-width: 360px;
  --header-height: 48px;
  --row-height: 88px;          /* 资料行（舒适） */
  --row-height-compact: 64px;  /* 任务/专题/废纸篓/导入清单行，及 <640px 的资料行 */
  --thumb-w: 48px;             /* 资料行缩略图 3:4 */
  --thumb-h: 64px;
  --form-width: 640px;         /* 表单页最大宽 */

  /* ---------- 旧令牌兼容层（Phase 0 引入，Phase 6 删除） ---------- */
  --color-canvas: var(--bg);
  --color-surface: var(--surface);
  --color-surface-subtle: var(--surface-sunken);
  --color-surface-hover: var(--surface-hover);
  --color-surface-pressed: var(--surface-selected);
  --color-surface-selected: var(--surface-selected);
  --color-text-primary: var(--text);
  --color-text-secondary: var(--text-2);
  --color-text-tertiary: var(--text-3);
  --color-text-disabled: var(--text-3);
  --color-text-inverse: var(--on-accent);
  --color-border-subtle: var(--border);
  --color-border-default: var(--border);
  --color-border-strong: var(--border-strong);
  --color-primary: var(--accent);
  --color-primary-hover: var(--accent-hover);
  --color-primary-pressed: var(--accent-pressed);
  --color-primary-subtle: var(--accent-subtle);
  --color-primary-text: var(--accent-text);
  --color-success: var(--status-done);
  --color-success-subtle: var(--status-done-bg);
  --color-warning: var(--status-waiting);
  --color-warning-subtle: var(--status-waiting-bg);
  --color-danger: var(--danger);
  --color-danger-hover: var(--danger);
  --color-danger-subtle: var(--danger-subtle);
  --shadow-card: none;
  --shadow-card-lifted: none;
  --shadow-inset: none;
  --shadow-popover: var(--shadow-overlay);
  --shadow-dialog: var(--shadow-overlay);
  --button-primary-face: var(--accent);
  --button-primary-face-hover: var(--accent-hover);
  --button-primary-face-pressed: var(--accent-pressed);
  --glass-canvas: var(--bg);
  --glass-surface: var(--surface);
  --glass-blur: none;
  --duration-fast: var(--dur-fast);
  --duration-normal: var(--dur-base);
  --duration-panel: var(--dur-panel);
  --ease-out: var(--ease);
  --ease-standard: var(--ease-in-out);
  --ease-spring: var(--ease);
  --ease-hover: ease;
  --radius-xs: var(--radius-sm);   /* 注意：旧代码里的 var(--radius-sm)（原 9px）现在解析为 4px；Phase 1 重写组件时把控件上的 --radius-sm 改成 --radius */
  --radius-md: var(--radius);
  --radius-lg: var(--radius);
  --radius-xl: var(--radius);
  --radius-pill: var(--radius-sm);
  --chat-width: var(--panel-width);
  --toolbar-height: var(--header-height);
  --reading-width: var(--measure);
  --text-xs: var(--fs-12);
  --text-sm: var(--fs-13);
  --text-ui: var(--fs-14);
  --text-body: var(--fs-16);
  --text-reading: var(--fs-reading);
  --text-lg: var(--fs-16);
  --text-xl: var(--fs-20);
  --text-2xl: var(--fs-20);
  --text-3xl: var(--fs-28);
  --space-0-5: 4px;            /* 旧 2px → 4px */
  --space-1-5: 8px;            /* 旧 6px → 8px */
  --space-5: 24px;             /* 旧 20px → 24px */
  --space-10: 48px;            /* 旧 40px → 48px */
  --nav-library: currentColor; --nav-recent: currentColor; --nav-favorite: currentColor;
  --nav-inspiration: currentColor; --nav-topic: currentColor; --nav-trash: currentColor;
  --nav-video: currentColor; --nav-image: currentColor; --nav-settings: currentColor;
}

:root[data-theme="dark"] {
  color-scheme: dark;
  --bg: #161615;
  --surface: #1D1D1B;
  --surface-sunken: #232321;
  --surface-hover: #242422;
  --surface-selected: #2A2A27;
  --text: #EDEBE7;
  --text-2: #A3A09A;
  --text-3: #99958D;           /* on --bg 6.069:1；选中底色最低 4.824:1 */
  --border: #2A2926;
  --border-strong: #3B3A36;
  --overlay-backdrop: rgba(0, 0, 0, .5);

  --accent: #6FB38E;
  --accent-hover: #82C29F;
  --accent-pressed: #5E9E7B;
  --accent-subtle: rgba(111, 179, 142, .14);
  --accent-text: #7DBE9A;
  --on-accent: #0F1F17;

  --danger: #F07167;
  --danger-subtle: rgba(240, 113, 103, .12);
  --status-queued: #A3A09A;   --status-queued-bg: #242422;
  --status-running: #7AA2F7;  --status-running-bg: rgba(122, 162, 247, .12);
  --status-waiting: #E0A458;  --status-waiting-bg: rgba(224, 164, 88, .12);
  --status-failed: #F07167;   --status-failed-bg: rgba(240, 113, 103, .12);
  --status-done: #6FB38E;     --status-done-bg: rgba(111, 179, 142, .14);

  --shadow-overlay: 0 8px 24px rgba(0, 0, 0, .5), 0 0 0 1px var(--border);
}
```

> 注：测试 `tests/test_web.py:475-476` 断言 CSS 里有 `--color-canvas` 和 `data-theme="dark"`，兼容层保证 Phase 0–5 期间通过；Phase 6 删除兼容层时保留 `--color-canvas: var(--bg);` 这一行或同步改测试（见 6.3）。

### 2.3 字体角色（Geist 式命名，写成工具类 + 在组件里引用）

| 角色 | 字体 | 字号/行高 | 字重 | 用途 |
|---|---|---|---|---|
| `label-12` | UI | 12/16 | 500 | 元信息、tag、kbd、状态徽章、分组标签 |
| `label-13` | UI | 13/16 | 500 | 按钮、Tab、导航计数外的一切单行控件 |
| `label-14` | UI | 14/20 | 400（选中 500） | 侧栏导航、列表标题、表单 label |
| `copy-13` | UI | 13/20 | 400 | 列表摘要、hint、AI 引用 |
| `copy-14` | UI | 14/22 | 400 | AI 消息、表单说明、设置行 |
| `copy-16` | UI | 16/26 | 400 | 空状态说明、专题目标 |
| `heading-16` | UI | 16/24 | 600 | 弹窗标题、面板内小节标题 |
| `heading-20` | UI（文章内宋体） | 20/28 | 600 | 正文 h2、设置页分区标题 |
| `heading-28` | UI（文章内宋体） | 28/36 | 600 | 页面大标题（资料库空状态、专题标题）、文章标题 |
| `heading-40` | UI | 40/48 | 600 | 仅"资料库为空"首屏 |
| `reading` | 宋体（仅文章） | 17/1.75 | 400 | 文章正文；专题成果沿用尺寸、使用 UI 字体 |

数字统一 `font-variant-numeric: tabular-nums`（计数、时间、进度、文件大小）。

```css
.label-12 { font: var(--fw-medium) var(--fs-12)/16px var(--font-ui); }
.label-13 { font: var(--fw-medium) var(--fs-13)/16px var(--font-ui); }
.label-14 { font: var(--fw-regular) var(--fs-14)/20px var(--font-ui); }
.copy-13  { font: var(--fw-regular) var(--fs-13)/20px var(--font-ui); }
.copy-14  { font: var(--fw-regular) var(--fs-14)/22px var(--font-ui); }
.copy-16  { font: var(--fw-regular) var(--fs-16)/26px var(--font-ui); }
.heading-16 { font: var(--fw-semibold) var(--fs-16)/24px var(--font-ui); }
.heading-20 { font: var(--fw-semibold) var(--fs-20)/28px var(--font-ui); }
.heading-28 { font: var(--fw-semibold) var(--fs-28)/36px var(--font-ui); letter-spacing: -.01em; }
.heading-40 { font: var(--fw-semibold) var(--fs-40)/48px var(--font-ui); letter-spacing: -.015em; }
```

### 2.4 字体加载（必须本地托管）

`app.css` 只定义 UI 字体。Newsreader 与 Doku Serif SC 的字体声明独立放在 `reading.css`：文章直达时模板引用，客户端打开文章时附加，离开时移除。字标、资料库、任务、设置与专题全部使用 UI 字体；不能因共享文章模板或隐藏节点加载宋体。

`tests/test_web.py:461` 与 `test_web_operation.py:400` 断言页面里**不能出现 `cdn.`**，且本应用只跑在 127.0.0.1、要能离线使用 → 不用 Google Fonts，全部放 `static/fonts/`。

| 文件（新建） | 来源 | 许可 | 预计体积 |
|---|---|---|---|
| `static/fonts/InterVariable.woff2` | rsms/inter 官方 release（`InterVariable.woff2`） | OFL 1.1 | ≈ 350 KB |
| `static/fonts/Newsreader-Variable.woff2` | Google Fonts Newsreader（opsz,wght 可变 TTF）→ `fonttools ttLib.woff2 compress` | OFL 1.1 | ≈ 150–250 KB |
| `static/fonts/DokuSerifSC-400.woff2`、`DokuSerifSC-600.woff2` | notofonts/noto-cjk `Serif/SubsetOTF/SC/NotoSerifSC-Regular.otf` / `-SemiBold.otf` → 用 `pyftsubset` 裁到《通用规范汉字表》一级+二级（6500 字）+ 标点 + 拉丁 | OFL 1.1 | 每个约 2–3 MB（**需实测**） |

裁字命令（Codex 在本机执行一次，产物提交；字符表文件放 `scripts/fonts/charset-gb6500.txt`）：

```bash
uv run --with fonttools --with brotli pyftsubset NotoSerifSC-Regular.otf \
  --text-file=scripts/fonts/charset-gb6500.txt \
  --unicodes="U+0020-007E,U+00A0-00FF,U+2000-206F,U+3000-303F,U+FF00-FFEF" \
  --layout-features='*' --flavor=woff2 \
  --output-file=src/douyin_wiki/webapp/static/fonts/DokuSerifSC-400.woff2
```

`app.css` 最顶部：

```css
@font-face { font-family: "Inter"; font-style: normal; font-weight: 100 900; font-display: swap;
  src: local("Inter Variable"), local("InterVariable"), url("/static/fonts/InterVariable.woff2") format("woff2"); }
@font-face { font-family: "Newsreader"; font-style: normal; font-weight: 200 800; font-display: swap;
  src: url("/static/fonts/Newsreader-Variable.woff2") format("woff2");
  unicode-range: U+0000-02FF, U+2000-206F, U+20AC, U+2122; }   /* 只给拉丁字符用 */
@font-face { font-family: "Doku Serif SC"; font-weight: 400; font-display: swap;
  src: local("Noto Serif SC"), local("Source Han Serif SC"), local("Source Han Serif SC VF"),
       url("/static/fonts/DokuSerifSC-400.woff2") format("woff2"); }
@font-face { font-family: "Doku Serif SC"; font-weight: 600; font-display: swap;
  src: local("Noto Serif SC SemiBold"), local("Source Han Serif SC SemiBold"),
       url("/static/fonts/DokuSerifSC-600.woff2") format("woff2"); }
```

`app.html <head>` 在 stylesheet 之前：

```html
<link rel="preload" href="/static/fonts/InterVariable.woff2" as="font" type="font/woff2" crossorigin>
```

（宋体不预加载：只有进入文章页才需要，`font-display: swap` 先用 Songti SC 回落。）
打包：`pyproject.toml` 用 hatchling，`[tool.hatch.build.targets.wheel] packages = ["src/douyin_wiki"]`，包目录下的非 Python 文件默认会打进 wheel（除非被 `.gitignore` 排除）——提交字体前确认 `.gitignore` 没有忽略 `*.woff2`。

### 2.5 全局基础样式

```css
html { font-size: 16px; -webkit-text-size-adjust: 100%; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: var(--fw-regular) var(--fs-14)/20px var(--font-ui);
  -webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility;
  text-size-adjust: 100%; -webkit-text-size-adjust: 100%; }
svg.icon, .icon-16 { width: 16px; height: 16px; fill: none; stroke: currentColor;
  stroke-width: 1.5; stroke-linecap: round; stroke-linejoin: round; flex: 0 0 auto; }
:where(button, a, input, select, textarea, [tabindex]:not([tabindex="-1"])):focus-visible {
  outline: 2px solid var(--accent); outline-offset: 2px; box-shadow: var(--focus-ring); }
#main-content:focus-visible { outline: 0; box-shadow: none; }
::selection { background: var(--accent-subtle); }
@media (prefers-reduced-motion: reduce) {
  /* 保证组件状态和 View Transition 同样遵守系统减少动效偏好。 */
  *, *::before, *::after, ::view-transition-group(*),
  ::view-transition-old(*), ::view-transition-new(*) {
    animation: none !important; transition: none !important; scroll-behavior: auto !important; }
}
```

图标规则：**只用 `icons.svg` 里的描边 symbol**，16px、stroke 1.5、`currentColor`。删除 app.html 与 app.js（`makeWelcome`）中所有内联实心 SVG。需要新增的 symbol（Lucide 同名路径）：`inbox`（导入）、`list-checks`（任务中心）、`layers`（专题）、`hash`（标签）、`message-square`（AI 对话）、`info`（信息）、`quote`（灵感）、`chevron-right`、`keyboard`、`log-in`（授权）、`git-branch`（分析方式）、`arrow-up-right`。未被界面引用的 `sparkles`、`chevron-down/up`、`moon`、`sliders-horizontal`、`cpu`、`hard-drive` 不保留。

---

## 3. 全局应用外壳

### 3.1 三栏结构

```
┌──────────────┬───────────────────────────────────────────┬──────────────────────┐
│ 侧栏 240     │ 中栏 (flex)                                │ 右栏 360             │
│ bg --bg      │ bg --bg                                    │ bg --surface         │
│ 右 1px border│                                            │ 左 1px border        │
│              │ ┌───────────────────────────────────────┐  │ ┌──────────────────┐ │
│ 栏头 48      │ │ 栏头 48（sticky，下 1px border）       │  │ │ 栏头 48: Tabs  ⊟ │ │
│              │ ├───────────────────────────────────────┤  │ ├──────────────────┤ │
│ 导航（滚动） │ │ 通知条（可选，36）                     │  │ │ Tab 内容（滚动） │ │
│              │ │ 已应用筛选（可选，36）                 │  │ │                  │ │
│              │ │ 内容（滚动）                           │  │ │                  │ │
│ footer       │ │                                        │  │ ├──────────────────┤ │
│              │ └───────────────────────────────────────┘  │ │ 输入区（AI Tab） │ │
└──────────────┴───────────────────────────────────────────┴──────────────────────┘
```

HTML 骨架（`app.html`，保持测试断言的 id / class / aria-label）：

```html
<div class="app-shell" id="app-shell" data-page="library">
  <aside class="library-sidebar" id="library-sidebar" aria-label="资料库导航">
    <div class="sidebar-header">…字标 + #sidebar-collapse…</div>
    <div class="sidebar-body">…导航（非文章页）…</div>
    <nav class="reader-toc hidden" id="reader-toc" aria-label="文章目录">…（文章页）…</nav>
    <div class="sidebar-footer">…设置 / 外观 / 版本…</div>
  </aside>
  <main class="library-main" id="main-content" tabindex="-1">
    …所有 *-view（包括 settings-view、not-found-view，修复 line 240 的结构 bug）…
  </main>
  <aside class="side-panel" id="side-panel" aria-label="右侧面板">
    <header class="side-panel-header">
      <div class="panel-tabs" role="tablist" aria-label="右侧面板">…</div>
      <button class="icon-button" id="chat-close" aria-label="收起右侧面板">…panel-right…</button>
    </header>
    <section class="panel-pane" id="info-pane" role="tabpanel" hidden>…</section>
    <section class="panel-pane" id="inspiration-pane" role="tabpanel" hidden>…</section>
    <section class="panel-pane" id="topic-sources-pane" role="tabpanel" hidden>…</section>
    <section class="panel-pane chat-panel" id="chat-panel" role="tabpanel" aria-label="抖库 AI 对话">…原对话 DOM…</section>
  </aside>
</div>
```

说明：原 `<aside id="chat-panel">` 降级为右栏里的一个 tabpanel（保留 `id="chat-panel"`、`class="chat-panel"`、`aria-label="抖库 AI 对话"`，满足 `test_web.py:424`）；`#chat-close` 变为"收起整个右栏"。`#chat-toggle` 保留 id，放在中栏栏头最右侧，图标 `panel-right`，`aria-controls="side-panel"`。

```css
.app-shell { display: grid; height: 100dvh; overflow: hidden;
  grid-template-columns: var(--sidebar-width) minmax(0, 1fr) var(--panel-width);
  grid-template-areas: "sidebar main panel"; }
.app-shell.is-sidebar-collapsed { grid-template-columns: 0 minmax(0, 1fr) var(--panel-width); }
.app-shell.is-panel-collapsed   { grid-template-columns: var(--sidebar-width) minmax(0, 1fr) 0; }
.app-shell.is-sidebar-collapsed.is-panel-collapsed { grid-template-columns: 0 minmax(0, 1fr) 0; }
.library-sidebar { position: relative; z-index: 0; grid-area: sidebar; display: flex; flex-direction: column;
  min-width: 0; overflow: hidden; background: var(--bg); border-right: 1px solid var(--border); }
.library-main { position: relative; z-index: 1; min-width: 0; grid-area: main; overflow: auto; background: var(--bg); }
.side-panel { grid-area: panel; display: flex; flex-direction: column; min-width: 0; overflow: hidden;
  background: var(--surface); border-left: 1px solid var(--border); }
```

（`.library-sidebar { position: relative; z-index: 0;` 与 `.library-main { position: relative; z-index: 1; min-width: 0;` 两段字面量必须保留在规则开头，`test_web.py:527-528`。）

### 3.2 侧栏（非文章页）

```
┌────────────────────────────┐
│ 抖库                    ⊟  │ 48  字标 heading-16 UI；⊟=#sidebar-collapse
├────────────────────────────┤
│ [ + 导入          ⌘I ]     │ 32  #imports-toggle（secondary 按钮，满宽）→ 导入菜单
│ [ ⌕ 搜索            / ]    │ 32  打开 #command-dialog（ghost 按钮，满宽）
│                            │
│ 资料                        │ 28  label-12 --text-3
│ ▥ 资料库              128  │ 32  data-section="all"
│ ◷ 最近加入             12  │     data-section="recent"
│ ⊡ 库内收藏              9  │     data-section="favorite"
│ ❝ 灵感                 21  │     data-section="inspiration"
│                            │
│ 专题                  查看 │ 28  #topics-nav 点击标题区进入 /topics
│ ≡ AI 芯片供应链            │ 32  最近更新的 5 个专题（可选，Phase 5）
│ ≡ 手冲咖啡                 │
│                            │
│ 标签                  全部 │ 28  #all-tags-button
│ # AI投资               14  │ 32  前 8 个标签（文字行，不再是胶囊）
│ # DeepSeek              9  │
│                            │
│ 工作                        │
│ ⇲ 导入中心                 │ 32  #imports-nav → /imports
│ ☑ 任务中心           ● 3   │     #jobs-nav；● = accent 点 + 需要本人操作数
│ 🗑 废纸篓                   │     #trash-nav
├────────────────────────────┤
│ ⚙ 设置                     │ 32  #settings-nav
│ ◐ 外观        跟随系统 ▾   │ 32  label.theme-control > select[data-theme-select]
│ Web v0.1 · 仅本机          │ 20  label-12 --text-3
└────────────────────────────┘
```

- 导航项 `.nav-item`：高 32，padding `0 var(--space-2)`，gap 8，圆角 6；图标 16 `--text-2`；文字 `label-14 --text`；计数 `label-12 --text-3 tabular-nums` 右对齐。
- hover：背景 `--surface-hover`。选中（`.active`）：背景 `--surface-selected`，文字 500，**图标变 `--accent`**。无瓷贴、无色块。
- 分组标题：`label-12 --text-3`，高 28，上间距 16。去掉 `letter-spacing`。
- 计数来源：`state.allItems` 本地计算（全部 / 最近 7 天 captured / favorite / inspirations.length>0）；任务数来自现有 `renderTodoBanners(count)` 的同一接口。
- 标签行点击 = 现有 `renderSidebarTags` 的行为（切换 `state.tags`），选中时文字 `--accent-text` + `#` 图标 accent。
- `.sidebar-body { overflow: auto }`；`.sidebar-footer` 固定底部，上 1px border；保留 class 名（`test_web_navigation.py:61-65`，同时 CSS 里要保留 `overflow: hidden;` 字面量——在 `.library-sidebar` 上）。
- 删除：`brand-mark.png` 的 `<img>`、`brand-copy small`（"个人知识工作台"）、所有 28px 瓷贴。

### 3.3 侧栏（文章页 = 目录，Readwise 模式）

```
┌────────────────────────────┐
│ ← 资料库                ⊟  │ 48  #back-library 移到这里（Esc 同效）
├────────────────────────────┤
│ 目录                        │ 28  label-12 --text-3
│ ┃ 灵感                      │ 28  当前可视章节：左 2px accent 条 + --text
│   一句话                    │     其他：--text-2
│   核心收获                  │
│   时间轴图解                │
│     01:05 一、开场测试      │ 28  h3：缩进 12，时间码 tabular-nums --text-3
│     03:40 二、网页复刻      │
│   来源                      │
├────────────────────────────┤
│ footer 同上                 │
└────────────────────────────┘
```

- `data-page="article"` 时隐藏 `.sidebar-body`、显示 `#reader-toc`。
- 目录由 `buildReaderToc()` 从 `#article-content .article-body` 的 `h2, h3` 生成；为每个标题补 `id="h-<index>"`。`### ▶ 01:05　一、标题` 解析为时间码 + 标题（去掉 ▶）。
- `IntersectionObserver`（rootMargin `-48px 0px -70% 0px`）跟踪当前章节。
- 点击目录项：`scrollIntoView({block:"start"})`，考虑 48px 栏头（`scroll-margin-top: 64px` 写在 `.article-body h2, h3` 上）。

### 3.4 栏头（每个中栏视图自带，替换全局 `.topbar`）

- **宽度预算修订**：1280px 三栏时中栏仅 680px，扣除内边距后为 632px。资料库使用两行：第一行 48px 放标题、搜索和共享侧栏按钮；第二行 48px 放来源 Tabs、筛选、排序、视图和选择。按中栏容器宽度响应，不仅按窗口宽度响应；空间不足时排序收为图标菜单，搜索收为可展开图标。选择模式的下一步入口不能隐藏。

- 高 48，`position: sticky; top: 0; z-index: var(--z-sticky); background: var(--bg); border-bottom: 1px solid var(--border)`；padding `0 var(--space-6)`。
- 左侧固定槽：`#nav-toggle`（<960 显示）、`#sidebar-restore`（侧栏收起时显示）。右侧固定槽：`#chat-toggle`（右栏收起时高亮为 ghost-active）。
- 实现：把现 `<header class="topbar">` 拆成：`#library-view` 内的 `.view-header`（资料库控件）、其他视图各自的 `.view-header`；`#nav-toggle` `#sidebar-restore` `#chat-toggle` 三个按钮放在 `library-main` 顶部一个共享的 `.view-header-slots`，用 CSS grid 叠到每个视图栏头上（或简单做法：每个视图栏头里都放左/右槽的占位，JS 把这三个按钮 `append` 到当前可见视图的栏头——推荐后者，避免绝对叠放导致遮挡，并维持单一 id）。

### 3.5 右栏

- 栏头 48：Tabs（见 5.4）+ `#chat-close`。
- 各页面的 Tab 组合（`Douku.setPage(name)` 时由新函数 `renderPanelTabs(page)` 决定）：

| data-page | Tabs（默认 Tab 加粗） | 右栏默认 |
|---|---|---|
| `library` | **信息** · AI 对话 | 展开 |
| `article` | **信息** · 灵感 · AI 对话 | 展开 |
| `topics` | AI 对话 | 展开 |
| `topic` | 来源 · **AI 对话** | 展开 |
| `trash` `imports*` `jobs` `job-detail` `settings*` `auth` `system` `analysis` `model` `not-found` | AI 对话 | **收起**（仅本次视图，不写入偏好；`]` 可打开） |

- 记忆：每种 page 的上次 Tab 存 `localStorage["douyin-wiki.panel-tab.<page>"]`。
- 右栏开合偏好沿用 `STORAGE.chat = "douyin-wiki.chat-collapsed"`，**默认值改为 `"false"`**（`readPreferences()` 第 98 行）。

### 3.6 响应式断点

沿用现有 JS 中的 `959px` 常量，新增 1280：

| 宽度 | 侧栏 | 中栏 | 右栏 |
|---|---|---|---|
| ≥ 1280 | 固定 240，可 `[` 收起 | flex | 固定 360，可 `]` 收起 |
| 960–1279 | 固定 240 | flex | **覆盖层**：`position: fixed; right: 0; top: 0; bottom: 0; width: 360px; z-index: var(--z-panel); box-shadow: var(--shadow-overlay)`；默认收起；打开时不加遮罩，点中栏空白或 `Esc` 关闭 |
| 640–959 | 抽屉（现 `sidebar-drawer-open`），宽 `min(280px, 100vw - 48px)`，有遮罩 `--overlay-backdrop` | 全宽 | 抽屉（现 `chat-drawer-open`），宽 `min(360px, 100vw)` |
| < 640 | 抽屉 | 全宽；资料行切换为紧凑 64；第一行保留 ☰、标题、搜索图标、▣；第二行保留来源、筛选和「选择」入口，次要控件移入菜单。选择模式增加 sticky 底栏：已选数、取消、下一步 | 全屏 sheet（宽 100vw） |

- 删除 `max-width: 719px`、`639px`、`359px` 三组零碎规则，合并进上表。
- `renderPanelState()` 改：`(min-width:1280px)` → grid 列控制；`960–1279` → 加/去 `.panel-overlay-open`；`<960` → 保留抽屉开合行为，但 `openDrawer()` / `trapDrawerFocus()` 的右栏根节点统一改为 `#side-panel`，涵盖 Tabs、关闭按钮及当前面板。`focusableElements()` 排除 `.hidden`、`[hidden]`、`[inert]` 及不可见元素；收起区域设 inert，关闭后恢复触发按钮焦点。Tab 切换后不得将焦点留在隐藏面板。
- 打开/收起动画：`grid-template-columns` 不做动画（跳变），只对覆盖层/抽屉做 `transform` 240ms `--ease-in-out`。

### 3.7 键盘快捷键

标 R 的是与 Readwise Reader 官方文档一致的；其余是本项目约定。所有单键快捷键在 `input/textarea/select/[contenteditable]` 聚焦时不触发（沿用 `handleGlobalKeydown` 的 `editing` 判断）。

**全局**

| 键 | 动作 | 现状 |
|---|---|---|
| `⌘/Ctrl + K` (R) | 命令面板（搜索文章 + 命令） | 已有 |
| `/` | 聚焦当前栏头搜索框 | 已有 |
| `[` (R) | 开合左栏 | 已有 |
| `]` (R) | 开合右栏 | 已有 |
| `?` (R) | 快捷键表（新 `#shortcuts-dialog`） | 新增 |
| `g` 然后 `l` / `r` / `f` / `i` / `t` / `j` / `x` / `s` | 去 资料库 / 最近加入 / 库内收藏 / 灵感 / 专题 / 任务中心 / 废纸篓 / 设置（1 秒内的两键序列） | 新增 |
| `⌘/Ctrl + I` | 打开导入菜单（`#imports-toggle`） | 新增 |
| `Esc` | 关浮层 → 关抽屉 → 文章页返回列表（按此优先级） | 扩展 |

**列表（资料库、专题列表、任务中心、废纸篓、导入清单）**

| 键 | 动作 |
|---|---|
| `j` / `↓` | 焦点下一行（`↓` 为 Readwise 用法，`j` 为别名） |
| `k` / `↑` | 焦点上一行 |
| `Enter` / `o` | 打开焦点行 |
| `x` | 选中/取消（资料库在"选择"模式下；废纸篓、导入清单始终可用） |
| `Shift + ↓/↑`、`Shift + j/k` | 连续选择（导入清单、废纸篓） |
| `f` | 切换焦点行的库内收藏（仅 `database_managed`） |
| `v` | 切换 列表/专辑墙 视图 |
| `Shift + F` (R 类比) | 打开筛选浮层 |
| `Home` / `End` | 第一行 / 最后一行 |

专辑墙网格中：`←/→` 前后一张，`↑/↓` 上下一行（列数由 `getComputedStyle(root).gridTemplateColumns.split(" ").length` 计算），`j/k` 等价 `→/←`。

**阅读（文章页）**

| 键 | 动作 |
|---|---|
| `↑` / `↓` (R) | 段落焦点上/下移动（焦点段落左侧 2px accent 竖条，见 4.2） |
| `j` / `k` | 同上（别名） |
| `Space` / `Shift+Space` | 翻页（浏览器默认，保留） |
| `n` / `p` | 列表中的下一篇 / 上一篇（按当前筛选排序后的 `filteredItems()`） |
| `Shift + I` | 右栏切到"信息" |
| `Shift + N` (R 类比：文档笔记) | 右栏切到"灵感"并打开"添加灵感"对话框（`openInspiration("", trigger)`） |
| `Shift + C` | 右栏切到"AI 对话"并聚焦 `#chat-input` |
| `f` | 库内收藏 |
| `Shift + O` | 打开原作品（新标签页） |
| `Esc` / `u` | 返回列表，并恢复进入前的滚动位置与焦点行 |

**焦点可见性**：所有可交互元素 `:focus-visible` 使用 `--focus-ring`（2px 底色间隔 + 2px accent 60%）。列表行使用 roving tabindex：焦点行 `tabindex="0"` + `.is-focused`，其余 `-1`；行内按钮在焦点行里按 `Tab` 可达。鼠标 hover 不改变键盘焦点。

---

## 4. 逐页规格

通用约定：
- 每页 = 栏头 48 + 内容区。内容区 padding `var(--space-6)`（24）左右，列表类页面上下 0（行自带分隔线），表单类页面上 24 下 64。
- 页面标题放在栏头左侧（`label-14` 600），不再有内容区里的 `h1 + 长副标题` 大块（`.page-heading` 删除，`h1` 仍保留在栏头里供读屏，视觉为 14px）。例外：文章用宋体大标题，专题详情保留大标题尺寸但使用 UI 字体。
- 三态通用：**加载** = 3–6 条骨架行（与真实行同高，`--surface-sunken` 块，无闪光）；**空** = 5.12 空状态组件；**错误** = 空状态组件 + `alert-circle` + "重试"（secondary）。

### 4.1 资料库首页 `/`（含 `?section=recent|favorite|inspiration`）

**文件**：`app.html #library-view`；`app.js` 的 `renderLibrary / makeListItem / makeGalleryCard / makeMetadata / renderFilters / renderAppliedFilters / renderToolbarState / filteredItems / loadLibrary / showLibrary / renderLibraryEmptyState / updateTopicSelectionButton`。

#### 列表视图（默认）

```
┌─ 中栏栏头 48 ─────────────────────────────────────────────────────────────────────┐
│ ☰ 资料库 128   全部 · 视频 · 图文        [⌕ 搜索知识库  ⌘K] ⚲筛选② ⇅最近加入▾ ≣▦ ☑选择 ▣ │
├───────────────────────────────────────────────────────────────────────────────────┤
│ ● 3 个任务需要你处理 →                                            （通知条 36，可选）│
│ 博主：林禾Mind ×   标签：AI投资 ×   清除全部                     （已应用筛选 36）   │
├───────────────────────────────────────────────────────────────────────────────────┤
│┃┌────┐ DeepSeek 多模态实测：人物识别与视觉编程不及预期                   08/23      │
│┃│封面│ 作者用人物误导、网页复刻和三个交互项目测试 DeepSeek 实验性多模态…          │
│┃│3:4 │ ▶ 林禾Mind · 推荐 · 2026/08/23 · ❝2  [多模态] [模型测评]                    │ 88
│┃└────┘                                                                            │
├───────────────────────────────────────────────────────────────────────────────────┤
│ ┌────┐ 为什么大模型竞争集中于中美：一套产业链解释                  hover: ⊡  ⋯     │
│ │    │ …                                                                          │
│ └────┘ ▶ 林禾Mind · 观点 · 2026/08/15  [AI产业]                                    │
├───────────────────────────────────────────────────────────────────────────────────┤
│ …                                                                                 │
└───────────────────────────────────────────────────────────────────────────────────┘
```

栏头控件（分两行，参见 3.4；各行内左→右）：
1. `#nav-toggle` / `#sidebar-restore`（条件显示）。
2. `h1#view-title`（`label-14` 600）+ `#result-count`（`label-12 --text-3 tabular-nums`，只显示数字"128"，不再"128 篇"）。`#view-summary` 删除可见文本（保留节点，`class="sr-only"`）。
3. 第二行左侧来源 Tabs：`全部 · 视频 · 图文`（5.4 小号 Tab，高 48，下划线贴栏头底边）。映射 `state.sources = [] / ["video"] / ["image_note"]`，写入 URL（已支持 `source` 参数）。筛选浮层里的"来源类型" fieldset 删除，避免两处控制。
4. 第一行右侧：`label.search-box > #search-input`（宽 240，<1280 时 180，<640 收成图标按钮）；第二行右侧：`#filter-toggle`（ghost，计数 `#filter-count` 为 accent 实心 16px 圆角 4 的数字块）→ `label.select-control > #sort-select`（ghost select，无边框，显示当前选项文字 + chevron）→ 视图切换 `.segmented-control`（`#view-list` `#view-gallery`，保留 `aria-label="列表视图"`/`"专辑墙视图"`）→ `#topic-select-toggle`（ghost，可见文字"选择"，`aria-label`/`title` = "从资料库选择来源"）。`#chat-toggle` 留在第一行末尾。
5. 删除：`#density-toggle`（及 `app.js renderToolbarState` 第 502 行、`bindEvents` 中相关监听、`STORAGE.density`）；`#imports-toggle` 从栏头移到侧栏（见 3.2），其浮层 `#imports-popover` 跟着移动，定位函数 `positionImportsPopover()` 改为锚定侧栏按钮下方。

**列表行 `.library-item`**（组件定义见 5.6）：
- 网格：`grid-template-columns: var(--thumb-w) minmax(0,1fr) auto; column-gap: 16px; padding: 12px 24px; min-height: 88px; border-bottom: 1px solid var(--border)`。
- 缩略图 48×64（3:4），`object-fit: cover`，圆角 4，1px `--border` 描边（内描边用 `outline: 1px solid var(--border); outline-offset: -1px`）；图文 `image-note-cover` 用 `contain` + `--surface-sunken` 底；无封面 → `cover-placeholder` 显示 16px `video`/`image` 图标。
- 第一行标题：`label-14` 500 `--text`，单行省略。
- 第二行摘要：`copy-13 --text-2`，单行省略；`item.summary` 为空时显示"暂无一句话摘要"（`--text-3`）。
- 第三行元信息（`label-12 --text-2`，项间 " · "）：来源图标 12px（`video`/`image`）+ `author` · `content_type_label` · 发布日期（本年 `MM/DD`，跨年 `YYYY/MM/DD`）· 灵感数（`quote` 图标 + 数字，仅 >0 时）· 库内收藏（实心 bookmark 12px，仅 favorite 时）· "只读"（仅 `!database_managed`）；随后最多 2 个 tag 胶囊（5.8，不带 `#`）。
- 右列：日期 `label-12 --text-3 tabular-nums`，**跟随排序**：`captured_desc` 显示采集日期，其余显示发布日期。hover 或键盘焦点时日期隐藏，换成行内操作（28px 图标按钮）：`⊡` 库内收藏（`makeFavoriteButton`，仅 managed）、`⋯` 菜单（打开原作品 / 重新分析 / 删除文章）。
- 状态：
  - hover：背景 `--surface-hover`。
  - 键盘焦点 `.is-focused`：背景 `--surface-selected`，左侧 2px accent 竖条（`box-shadow: inset 2px 0 0 var(--accent)`）。
  - 当前打开（`.active`，返回列表后仍标记刚读过的那篇）：同 `.is-focused` 但竖条 `--border-strong`。
  - 专题选择模式：行首出现 16px checkbox（替代现 `.topic-selection-indicator` 的圆形 plus/check），选中 = accent 实心 checkbox + 背景 `--accent-subtle`；非 managed 行 checkbox disabled。
- 点击：单击 = 设置焦点 + 打开文章（与现行为一致）；`⌘/Ctrl/Shift` 点击保持浏览器默认（已有判断）。

**右栏"信息" Tab（列表上下文）**：显示**焦点行**的条目（新 `state.focusedEntryId`；初次加载自动聚焦第一行但不滚动）：

```
┌ 信息 · AI 对话 ─────────────── ⊟ ┐
│ ┌──────────┐                     │
│ │ 封面 3:4 │  120×160            │
│ └──────────┘                     │
│ DeepSeek 多模态实测：人物识别与… │ heading-16，最多 3 行
│ 作者用人物误导…（一句话全文）    │ copy-14 --text-2
│                                  │
│ 作者     林禾Mind ↗筛选          │ 定义列表：label-12 --text-3 左 72px
│ 类型     推荐 · 视频             │          copy-13 --text 右
│ 发布     2026-08-23 09:10        │
│ 采集     2026-08-23 17:01        │
│ 标签     [多模态] [模型测评] …   │
│ 灵感     2 条                    │
│ 媒体     永久保留 / 临时保留     │ retention + media_status
│ 状态     已入库                  │
│                                  │
│ [ 打开阅读 ↵ ]  ⊡ 收藏  ↗ 原作品 │ primary + ghost + ghost
└──────────────────────────────────┘
```
- 作者、标签可点 → 设为筛选（复用 `toggleListValue`）。
- 未聚焦任何行（列表为空）时：显示资料库统计：总数、视频/图文数、近 7 天新增、带灵感数（`copy-13`，每行一个数字 `heading-20 tabular-nums` + 说明）。
- 文件：新函数 `renderInfoPanel(item, {context: "library"})`（app.js）。

#### 专辑墙视图（网格 3:4，`?view=gallery`）

```
┌───────┐ ┌───────┐ ┌───────┐ ┌───────┐ ┌───────┐
│ 封面  │ │       │ │       │ │       │ │       │   minmax(160px, 1fr)，gap 24 横 / 24 纵
│ 3:4   │ │       │ │       │ │       │ │       │
└───────┘ └───────┘ └───────┘ └───────┘ └───────┘
标题两行省略…         label-14 500，2 行 clamp，上 8
林禾Mind · 推荐 · 08/23   label-12 --text-2，1 行
```
- `.gallery-card-link` 块；`.gallery-cover` 保留 class 与 `aspect-ratio: 3 / 4`（测试断言），圆角 6，`outline: 1px solid var(--border); outline-offset: -1px`，**无阴影**；hover：outline 变 `--border-strong`；焦点/选中：`box-shadow: 0 0 0 2px var(--bg), 0 0 0 4px var(--accent)`。
- 右上角仅在 favorite 时显示 20×20 白底（暗色 `--surface`）圆角 4 的实心 bookmark 12px；灵感数显示在元信息行而不是封面上。
- 删除：`gallery-card.is-entering` 阶梯入场、`--enter-delay`（保留 `animatedEntryIds` 状态字段本身——测试 `test_web.py:499` 断言该字符串存在；可改为仅用于 SSE 新入库条目的一次性 160ms 淡入）。
- 保留：`makeCover(item, "gallery-cover")` 与到文章页的 view transition（`::view-transition-group(active-album-cover)` 280ms → 改为 `--dur-panel` 240ms，`--ease-in-out`）。

#### 默认视图迁移
- `app.js`：`UI_PREFERENCE_VERSION` `"2"` → `"3"`；`readPreferences()` 迁移分支写入 `"list"`；`readStoredView` 回落值、`readStateFromURL()` 回落值 `"gallery"` → `"list"`；`state.libraryView` 初值 `"list"`。`currentLibraryURL()` 已经以 list 为默认，不改。
- 同步改测试 `tests/test_web.py:491`。

#### 状态
| 状态 | 表现 |
|---|---|
| 加载 | `#library-status` 6 条 88px 骨架行（缩略图块 48×64 + 两条文字块 60%/40% 宽） |
| 资料库为空 `#empty-library` | 居中偏上（上 padding 64）：`heading-28` 宋体"资料库还是空的"，`copy-16 --text-2` 一行"从单条链接、博主主页或抖音收藏导入。"，primary"导入内容"（`data-route="/imports"`），下方三行快捷入口（导入单条 / 导入博主 / 导入抖音收藏，ghost 行） |
| 无结果 `#empty-results` | `search` 图标 24 + `heading-16`"没有匹配的资料" + `#empty-results-copy`（保留现有三种文案逻辑）+ secondary"清除搜索和筛选" |
| 错误 `#library-error` | `alert-circle` + `heading-16`"资料库暂时无法载入" + `#library-error-copy` + secondary"重试" |
| 授权/待办通知 `#library-auth-banner` `#library-todo` | 36px 通知条（5.13），中性底，不用红/黄 |

#### 筛选浮层 `#filter-popover`
- 宽 320，最大高 `min(560px, 100dvh - 96px)`，锚定 `#filter-toggle` 下方 4px；`.filter-popover { position: fixed; z-index: 50; top: var(--filter-popover-top …`（保持字面量，`test_web.py:529-531`）。
- 分组：博主（带搜索框，>8 项时出现）、内容类型、标签（勾选行，不再是胶囊墙）、"仅显示有灵感的文章"开关；每组标题 `label-12 --text-3`。
- 底部：ghost"清除全部" + primary"完成"。

### 4.2 文章详情 `/articles/{entry_id}`（Readwise 阅读器）

**文件**：`app.html #article-view`；`app.js` 的 `openArticle / renderArticleData / makeArticleHeader / focusCitation / renderArticleError / showArticleFallbackEntry`；正文 HTML 来自 `/api/articles/{id}` 的 `data.html`（`webapp/rendering.py`）。

```
┌ 侧栏：目录 ──────┬─ 中栏 ─────────────────────────────────────────────────┬ 右栏 ───────────────┐
│ ← 资料库      ⊟ │ ←  ·  ‹ 上一篇  下一篇 ›                  ⊡  ↗  ⋯  ▣   │ 信息 · 灵感 · AI 对话│
│                 │━━━━━━━━━━━━━━━━━━━━━━━━━━━━░░░░░░░░░░░░░░░░ 阅读进度 2px│                     │
│ 目录            │                                                        │                     │
│ ┃ 灵感          │        推荐 · 视频 · 林禾Mind              ┌──────┐    │                     │
│   一句话        │        DeepSeek 多模态实测：人物           │ 封面 │    │                     │
│   核心收获      │        识别与视觉编程不及预期              │ 3:4  │    │                     │
│   时间轴图解    │        发布 2026-08-23 · 采集 08-23 17:01  └──────┘    │                     │
│    01:05 一、…  │        [多模态] [模型测评] [视觉编程]                  │                     │
│    03:40 二、…  │        ───────────────────────────────────────         │                     │
│                 │        灵感                                            │                     │
│                 │        未填写；AI 不推测用户灵感。                     │                     │
│                 │                                                        │                     │
│                 │        一句话                                          │                     │
│                 │       ┃作者用人物误导、网页复刻和三个交互项目测试      │                     │
│                 │       ┃DeepSeek 实验性多模态模型……（焦点段落）        │                     │
│                 │        …                                               │                     │
└─────────────────┴────────────────────────────────────────────────────────┴─────────────────────┘
                   ←────────── 36em（≈612px）居中 ──────────→
```

**栏头（sticky 48）**：
- 左：`#back-library`（ghost 图标按钮 `arrow-left`，`aria-label="返回资料库"`，从侧栏目录头部和此处二选一——推荐只放侧栏头部，侧栏收起时显示在这里）；`‹ 上一篇` `下一篇 ›`（ghost 28px，基于 `filteredItems()` 中的位置；首/末篇 disabled）。
- 右：`⊡` 库内收藏（`makeFavoriteButton(item, "article-favorite-button")`，图标按钮，`aria-pressed`）；`↗` 打开原作品（`item.original_url`）；`⋯` 菜单：重新分析（现 `reanalyze` 按钮逻辑）、复制链接、删除文章（现 `remove` 按钮逻辑 → `openDestructiveDialog`）；`#chat-toggle`。
- 底边：阅读进度条 2px `--accent`，宽度 = `scrollTop / (scrollHeight - clientHeight)`，监听 `.library-main` 的 scroll（`requestAnimationFrame` 节流）。

**文章头 `.article-hero`**（`makeArticleHeader` 重写，保持 `hero.querySelector(".article-cover")` 可用）：
- 布局：`display: grid; grid-template-columns: minmax(0,1fr) 96px; gap: 24px; max-inline-size: var(--measure); margin: 48px auto 32px;`
- kicker：`label-12 --text-2`："推荐 · 视频 · 林禾Mind"（`content_type_label · sourceLabel · author`）；删除 `.content-badge`、`.status-badge` 胶囊（状态移到右栏信息）。
- 标题 `h1`：`heading-28` 宋体（`--font-reading`），`text-wrap: balance`。
- byline：`label-12 --text-2 tabular-nums`："发布 2026-08-23 · 采集 08-23 17:01"。
- tags：tag 胶囊（5.8），点击 → 返回列表并筛选该标签。
- 封面 `.article-cover`：96×128，圆角 6，1px outline，无阴影，右上对齐；是 view transition 目标。
- 删除 `.article-actions` 行（操作都在栏头）。只读 Markdown（`!database_managed`）：kicker 末尾加"· 只读"。

**正文 `.article-body`**：
```css
.article-body { max-inline-size: var(--measure); margin-inline: auto; padding-bottom: 96px;
  font-family: var(--font-reading); font-size: var(--fs-reading); line-height: var(--lh-reading);
  color: var(--text); overflow-wrap: anywhere; line-break: strict;
  text-autospace: normal;            /* 中西文间距（渐进增强） */
  text-spacing-trim: normal; hanging-punctuation: allow-end; }
.article-body > h1:first-child { display: none; }                      /* 保留 */
/* 不按 DOM 位置隐藏首图：rendering.py 已移除封面，剩余正文图片全部保留。 */
.article-body h2 { font: var(--fw-semibold) var(--fs-20)/28px var(--font-reading); color: var(--text);
  margin: 48px 0 16px; scroll-margin-top: 64px; }
.article-body h3 { font: var(--fw-semibold) var(--fs-reading)/28px var(--font-reading);
  margin: 32px 0 8px; scroll-margin-top: 64px; }
.article-body p, .article-body ul, .article-body ol { margin: 0 0 16px; }
.article-body li { margin-bottom: 4px; }
.article-body blockquote { margin: 24px 0; padding: 0 0 0 16px; border-left: 2px solid var(--border-strong);
  color: var(--text-2); font-size: var(--fs-16); line-height: 1.75; }
.article-body img { display: block; max-width: 100%; height: auto; margin: 24px 0; border-radius: var(--radius);
  outline: 1px solid var(--border); outline-offset: -1px; }
.article-body a { color: var(--accent-text); text-decoration: underline; text-decoration-thickness: 1px;
  text-underline-offset: 3px; text-decoration-color: color-mix(in srgb, var(--accent) 40%, transparent); }
.article-body table { display: block; overflow-x: auto; border-collapse: collapse; margin: 24px 0;
  font: var(--fw-regular) var(--fs-14)/22px var(--font-ui); }
.article-body th, .article-body td { padding: 8px 12px; border-bottom: 1px solid var(--border); text-align: left; min-width: 120px; }
.article-body th { font-weight: var(--fw-semibold); color: var(--text-2); background: transparent; }
.article-body code { font-family: var(--font-mono); font-size: .875em; background: var(--surface-sunken);
  border-radius: var(--radius-sm); padding: 0 4px; }
.article-body pre { background: var(--surface-sunken); border-radius: var(--radius); padding: 16px; overflow: auto;
  font-size: var(--fs-14); line-height: 22px; }
.article-body mark.citation-highlight { background: var(--accent-subtle); color: inherit; border-radius: 2px;
  transition: background 1200ms var(--ease); }
.article-body mark.citation-highlight.settled { background: transparent; }
.article-body > * { position: relative; }
.article-body .is-reading-focus::before { content: ""; position: absolute; left: -16px; top: 4px; bottom: 4px;
  width: 2px; background: var(--accent); }                       /* 段落焦点：左侧 2px 竖条 */
```
- 测试 `test_web.py:484` 断言 `.article-body { max-inline-size: 40rem`：改为断言 `.article-body { max-inline-size: var(--measure)`（见 6.3）。
- `## 时间轴图解` 下的 `### ▶ 01:05　一、…`：JS 在渲染后把开头的 `▶ mm:ss` 包成 `<span class="chapter-time">`（`font-family: var(--font-ui); font-size: var(--fs-13); color: var(--text-3); font-variant-numeric: tabular-nums; margin-right: 8px`），去掉 ▶ 字符。
- **callout 实际契约**：`rendering.py` 在解析后的 blockquote 中规范化 `[!note]` 等标记，标题已有“AI 推断”时去重，多行正文单独成段；仅省略正文为空或“无”的“与灵感的关系”提示，保留其他文字、普通引用与代码示例。返回结构为 `<blockquote><p><strong>AI 推断</strong> · 标题</p><p>正文</p></blockquote>`（无独立正文时只有首段）。禁止再寻找 `[!note]`。如需装饰，识别首个段落以 `<strong>AI 推断</strong>` 开头的 blockquote，仅添加 `.callout`，保留现有文字和节点；通用 blockquote 不受影响，不重复添加「AI 推断」。
- 段落焦点（`↑/↓`、`j/k`）：按文档顺序，顶层 `p, h2, h3, h4, blockquote, table, pre` 各作为一个块；`ul/ol` 展开到可见 `li`，列表项自身文本作为一站，嵌套列表逐层遍历，不重复选中其父 `p`；块引用、表格、代码块内部不再单独加入候选；图片跟随包含它的段落，直接子级图片单独加入。排除隐藏元素和隐藏标题。`.is-reading-focus` 样式覆盖嵌套 li；当前块 `scrollIntoView({block: "nearest"})`，鼠标点击取最近候选块。输入框、弹窗、菜单、Tabs、按钮等控件优先处理自身键盘事件。

**右栏 Tabs（文章上下文）**：
- **信息**：同 4.1 信息 Tab，但：顶部不重复封面与标题；增加"来源文件"（`source_path` 文件名，`label-12 --text-3`，可复制）；底部操作：ghost"重新分析"、ghost"删除文章"（文字 `--danger`，需确认）。
- **灵感**（Readwise Notebook 的对应物）：
  ```
  ┌ 灵感 (2) ─────────────────────────┐
  │ [ + 添加灵感  ⇧N ]                 │ secondary 满宽 → openInspiration("", btn)
  │                                    │
  │ ┃ 我想用这个思路测我们自己的模型…  │ copy-14 --text；左 2px accent 竖条（"高亮"用途）
  │   ❝ 对应原句：三个交互项目…        │ copy-13 --text-2
  │   01:05–02:30  → 跳到正文         │ label-12 --text-3 tabular；点击 = focusCitation({timestamp_ms})
  │ ────────────────────────────────── │
  │ ┃ …                                │
  └────────────────────────────────────┘
  ```
  数据：`data.item.inspirations[]`（`text` / `quote` / `start_ms` / `end_ms`）。空状态："还没有灵感。读到有用的地方，按 ⇧N 记下来。"只读 Markdown：隐藏添加按钮，显示"只读 Markdown 文章不能保存灵感"（现有文案）。
- **AI 对话**：见 4.10，scope = entry。

**状态**：加载 = 中栏在 36em 容器中显示标题骨架（1 条 28px 高 70% 宽 + 2 条 17px）+ 8 条正文骨架；错误 = `renderArticleError` 输出改为空状态组件（`alert-circle` + "这篇资料无法打开" + 错误文案 + secondary"返回资料库"）。

**返回行为**：`Esc`/`u`/返回按钮 → `showLibrary()`，恢复列表 `scrollTop`（新 `state.libraryScrollTop`，打开文章前记录）并把焦点行设为刚读的条目。

### 4.3 导入中心 `/imports`

**文件**：`app.html #imports-view`；路由在 `app.js`/`shared.js`（`Douku.isOperationPath`）；导入菜单 `#imports-popover`。

```
┌ 栏头：导入内容 ──────────────────────────────────────────────── ▣ ┐
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │  最大宽 640，居中
│  │ 粘贴抖音链接、分享文案或博主主页…                         │   │  textarea 3 行（新，可选）
│  └──────────────────────────────────────────────────────────┘   │
│  识别为：单条作品                              [ 继续 → ]        │  label-12 --text-2 + primary
│                                                                  │
│  导入方式                                                        │  label-12 --text-3
│  ─────────────────────────────────────────────────────────────  │
│  ⊕  导入单条        粘贴链接或分享文案，补充灵感后提交       ›   │  56px 行
│  ◯  导入博主        清点公开作品，选择后再确认                ›   │
│  ⊡  导入抖音收藏    授权后更新清单，勾选后确认                ›   │
│  ─────────────────────────────────────────────────────────────  │
│  最近导入                                          任务中心 ›    │
│  ● 林禾Mind 主页清点 · 待选择作品 · 09-29 20:10               ›  │  任务行紧凑版（无状态色：见说明）
│  …（最多 5 条）                                                  │
└──────────────────────────────────────────────────────────────────┘
```
- 三种方式：现 `.operation-grid` 三张 `status-card import-entry` 改为 56px 行（图标 16 `--text-2` · 标题 `label-14` 500 · 说明 `copy-13 --text-2` 单行 · `chevron-right`），保留 `href` 与 `data-route`。删除 `.status-pill`（"一步完成"等）。
- 页面副标题删除（原文见 1.1 #9）。清点不下载媒体、不调 AI 的说明放到各子页 hint。
- **粘贴框（新增，Phase 5 可选）**：本地判断——含 `douyin.com/user/` → 跳 `/imports/creators` 并预填 `#creator-source`；否则跳 `/imports/single` 并预填 `#single-share`。不新增 API。
- **最近导入**：调用现有 `/api/jobs`（取前 5 条，kind ∈ capture / creator_import / favorites_import），行只显示文字状态（`state_label`），不用状态色（状态色只在任务中心）。
- `#imports-auth-banner`、`#imports-todo`：通知条（5.13）。

**导入菜单 `#imports-popover`**（侧栏 `#imports-toggle` 触发）：宽 280，菜单项 3 个 + 分隔线 + "打开导入中心"；每项 40px（标题 `label-14` + 说明 `label-12 --text-2`）；键盘 `↑/↓` 移动、`Enter` 进入。

### 4.4 导入单条 `/imports/single`

**文件**：`#imports-single-view`，`static/imports-single.js`。

```
┌ 栏头：← 导入内容 / 导入单条 ─────────────────────────────── ▣ ┐
│  （最大宽 640）                                                 │
│  链接或分享文案                                                 │ label-14 500
│  ┌───────────────────────────────────────────────────────┐     │
│  │ https://v.douyin.com/…                                │     │ #single-share 4 行
│  └───────────────────────────────────────────────────────┘     │
│  提交前只检查链接，不下载媒体、不调用 AI。                      │ label-12 --text-3
│                                                                 │
│  灵感（可选）                                                   │
│  ┌───────────────────────────────────────────────────────┐     │ #single-inspiration 3 行
│  └───────────────────────────────────────────────────────┘     │
│  ▸ 时间范围与处理选项                                           │ details.import-advanced
│     对应原话 / 开始 分:秒 / 结束 分:秒 / 媒体保留 ▾ /           │
│     ☐ 预先批准长视频  ☐ 允许进入可能消耗 Token 的 AI 阶段       │
│                                                                 │
│  分析方式：后台模型接口 · gpt-5-mini                            │ #single-analysis-copy label-12
│  [ 确认提交 ]                                                   │ primary，左对齐
└─────────────────────────────────────────────────────────────────┘
```
- 删除 `system-panel` 卡片壳（header/body 两层）；字段直接排版，字段间距 24。
- 面包屑：栏头标题区"导入内容 / 导入单条"，"导入内容"是可点链接（`data-route="/imports"`），取代 `.article-breadcrumb`。
- 错误 `#single-error`：字段下方 `copy-13 --danger`。提交中：按钮 disabled + 文案"正在提交…"。成功：toast + 跳转任务详情（保持现逻辑）。

### 4.5 导入博主 `/imports/creators`

**文件**：`#imports-creators-view`，`static/imports-creators.js`（作品行 `label.work-row`，第 181 行起）。

```
┌ 栏头：导入内容 / 导入博主 ──────────────────────────────────── ▣ ┐
│ ┌ 正在为「林禾Mind」选择作品 · 勾选后确认才会下载   [返回任务] ┐ │ #creator-focus-bar（通知条样式）
│                                                                  │
│ 主页或作品链接                                                   │
│ [ https://www.douyin.com/user/…                 ] [ 开始清点 ]   │ input + primary 同行
│                                                                  │
│ 已清点的博主                                                     │ #creators-list → 紧凑行，不再是卡片
│ ◯ 林禾Mind   86 个作品 · 上次同步 09-28                       ›  │
│                                                                  │
│ 清点完成 · 发现 86 · 新增 12                                     │ #creator-status heading-16 + #creator-summary copy-13
│                                                                  │
│ 作品清单                     [⌕ 搜索作品] ☑只看尚未入库          │ 列表栏头 40
│ 全选全部页 · 全部页改为不导入          作用于本次清点的全部作品 ⓘ│ ghost 按钮 + hint（长说明收进 ⓘ tooltip）
│ ────────────────────────────────────────────────────────────── │
│ ☑ 为什么大模型竞争集中于中美…        视频 · 2026/08/15 · 待入库   │ 48px 行
│ ☐ …                                                              │
│ ────────────────────────────────────────────────────────────── │
│                     ‹ 上一页   第 1 / 5 页   下一页 ›            │
├──────────────────────────────────────────────────────────────────┤
│ 已选 12 / 86   ☐ 我接受清单可能不完整        [ 确认导入所选作品 ]│ sticky 底栏 56（.fav-confirm）
└──────────────────────────────────────────────────────────────────┘
```
- 作品行：`checkbox 16` · 标题 `label-14`（单行）· 右侧元信息 `label-12 --text-2`（现 `small` 的 meta）。行高 48，hover `--surface-hover`，选中背景 `--accent-subtle`。无缩略图（接口不返回封面）。
- 批量选择：保留两个全局按钮（ids `#creator-select-all` `#creator-exclude-all`）改为 ghost 小按钮；`#creator-select-hint` 长句移到 `ⓘ` 的 `title`。
- 新增（可选）：`Shift` 点击范围选择；前端计算当页区间内可选的 `work_id`，一次提交 `POST /api/creator-imports/{job}/selection {decision: "selected"|"skipped", work_ids: [...]}`（`imports-creators.js:189` 已使用该数组 payload，单条时传 1 个）。
- 底栏：sticky bottom，`background: var(--bg); border-top: 1px solid var(--border)`，左侧"已选 N / 共 M"（`label-13 tabular-nums`），右侧 primary。
- 键盘：清单内 `j/k` 移动，`x` 勾选。

### 4.6 导入抖音收藏 `/imports/favorites`

**文件**：`#imports-favorites-view`，`static/imports-favorites.js`（行 `div.favorites-item`，第 97 行起）。

```
┌ 栏头：导入内容 / 导入抖音收藏 ──────────────────────────────── ▣ ┐
│ 与资料库的「库内收藏」无关。                                    │ label-12 --text-3 一行
│                                                                  │
│ 范围 [全部抖音收藏 ▾]  方式 [增量更新 ▾]  ☐同时导入图文  [更新抖音收藏] │ 一行工具条
│ （选"指定收藏夹"时下方展开收藏夹勾选行 #favorites-folders）     │
│                                                                  │
│ 收藏任务 · 待选择作品                                            │ #favorites-status heading-16
│ 发现 120 · 新增 8 · 已选择 8 · 已入库 0 · 处理中 0 · 失败 0      │ #favorites-summary copy-13 tabular
│ 需要登录 1 · 待外部 Agent 2                                      │ #favorites-waits
│ • 提示…                                                          │ #favorites-warnings copy-13 --text-2
│ 需要抖音账号授权 → 设置 › 授权状态                               │ #favorites-auth（通知条样式）
│                                                                  │
│ 作品清单     [⌕ 标题或作者] [全部收藏夹 ▾] ☐只看新增 [搜索]      │
│ 选择整份清单 · 排除整份清单            作用于全部页 ⓘ            │
│ ──────────────────────────────────────────────────────────────  │
│ ☑ 标题（外链 douyin.com）       作者 · 视频 · 可导入 · 新发现    │ 48px 行
│ ──────────────────────────────────────────────────────────────  │
│                     ‹ 上一页   第 1 页   下一页 ›                │
│ ▸ 历史与缓存                                                     │ details.fav-advanced（折叠在底部）
├──────────────────────────────────────────────────────────────────┤
│ ☐ 我接受清单可能不完整…      [重试清点]  [ 确认导入所选作品 ]    │ sticky 底栏
└──────────────────────────────────────────────────────────────────┘
```
- 删除两个 `system-panel` 与 `status-card fav-status` 卡片壳，改为分区：分区间 1px 分隔线 + 32 间距。
- 行：disabled（`data-eligible="false"`：不支持/不可用/未勾"同时导入图文"的图文）→ 整行 `--text-3`，checkbox disabled，右侧说明原因。
- 范围选择：`Shift` 点击 → 收集区间内 eligible 的 `work_id`，一次 `POST {selected, work_ids:[…]}`（接口已支持数组）。
- `#favorites-error`：`copy-13 --danger`，放在状态分区末尾。
- 轮询中（`ongoing`）：状态标题后显示 12px 不定进度条（5.10），而不是转圈。

### 4.7 任务中心 `/jobs`（状态色只在这里出现）

**文件**：`#jobs-view`，`static/jobs.js`（`renderRow / renderList / statusCluster / actionButton / quietAction / dismissButton / creatorRepaste`）。

```
┌ 栏头：任务中心 42   [全部来源 ▾] [全部状态 ▾]                              ▣ ┐
├──────────────────────────────────────────────────────────────────────────────┤
│ 待选择作品                                                                   │ group label-12 --text-3，高 32
│ [● 待选择] 林禾Mind 主页清点                          [去选择作品]  09-29 20:10│ 64px 行
│            清点完成：发现 86 个作品，等待你选择                               │
├──────────────────────────────────────────────────────────────────────────────┤
│ 失败可重试                                                                   │
│ [● 失败]   DeepSeek 多模态实测                    [重试] [不再提醒]  09-29 19:02│
│            下载失败：需要更新抖音登录                                         │
├──────────────────────────────────────────────────────────────────────────────┤
│ 其他任务                                                                     │
│ [● 正在转录] 为什么大模型竞争集中于中美                          62%  20:11   │
│              转录 · 62%                                                       │
│ ▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔░░░░░░░░░░░░░░░░ 2px accent 进度（行底）         │
│ [● 已完成] 三种冠军手冲方案                                [打开资料]  08-15 │
└──────────────────────────────────────────────────────────────────────────────┘
```
- 栏头：`h1#jobs-title`（label-14 600）+ `#jobs-count`（数字）；`#jobs-filter-kind`、`#jobs-filter-action` 改为 ghost select（保留 option 值，`value="in_progress"` 被测试断言）。
- 行 `.job-row`（保留 class、`tabIndex=0`、`role="link"`、`data-attention`）：
  - 网格：`grid-template-columns: auto minmax(0,1fr) auto; grid-template-rows: auto auto; column-gap: 12px; padding: 12px 24px; min-height: 64px; border-bottom: 1px solid var(--border)`。
  - 列 1：状态徽章（5.11），`data-state = job.status`，文字 = `job.state_label`；`job.dismissed` 时后面跟 `label-12 --text-3`"已忽略"。
  - 列 2 行 1：`jobTitle(job)` `label-14` 500 单行；行 2：`listMessage(job)` `copy-13 --text-2` 单行省略（完成态仍用 `.job-oneline`）。
  - 列 3：操作按钮（28px 小按钮：`next_action` 为 primary-sm，其余 secondary-sm）+ 时间 `label-12 --text-3 tabular-nums`（`updated_display` 去掉"（北京时间）"）。
  - 进行中且 `progress > 0`：行底 2px 进度条（accent），`meta` 行里的"阶段 · 62%"合并到列 2 行 2 末尾。
  - hover `--surface-hover`；键盘焦点同资料行（左 2px accent 条）。
- 分组标题（`.job-group-title`）：保留 `titles` 映射（`rest: "其他任务"` 被测试断言），样式 `label-12 --text-3`，上 16 下 4，无背景。
- 状态 → 徽章色映射（CSS `.status-pill[data-state=…]`，同时用于授权页的 `data-state` 需改为中性——见 4.11）：

| 类别 | data-state | 色 |
|---|---|---|
| queued | `queued` | `--status-queued` |
| running | `resolving` `inventorying` `dispatching` `monitoring` `downloading` `extracting` `transcribing` `analyzing` | `--status-running` |
| waiting | `needs_selection` `needs_auth` `needs_review` `waiting_confirmation` `awaiting_agent_analysis` | `--status-waiting` |
| failed | `failed` | `--status-failed` |
| done | `completed` | `--status-done` |
| done+提示 | `completed_with_warnings` | `--status-done` 文字 + 徽章后 6px `--status-waiting` 圆点 |

- 空状态：`D.emptyState("没有符合条件的任务", "提交导入后，任务会出现在这里。")`，改用 5.12 组件（加 `list-checks` 图标）。
- 实时：保留 `EventSource("/api/events")` 刷新逻辑；刷新时保持焦点行（按 `job.id` 恢复）。
- 键盘：`j/k`、`Enter` 打开详情、`r` 触发焦点行的"重试"类 action（若存在）。

### 4.8 任务详情 `/jobs/{id}`

**文件**：`#job-detail-view`，`jobs.js loadDetail`（第 250–418 行）。

```
┌ 栏头：← 任务中心 / 林禾Mind 主页清点                                   ▣ ┐
│ （最大宽 720）                                                            │
│ [● 正在分析]  DeepSeek 多模态实测                                         │ 徽章 + heading-20（UI 字体）
│ 正在分析第 3/8 批                                                         │ message_for_user copy-14 --text-2
│ 阶段 分段分析 · 进度 62%                                                  │ label-12 tabular
│ ████████████████████████░░░░░░░░░░░░  4px accent 进度                     │
│ 分段分析 · 3/8 批 · 最近模型响应 20:10 · 最近输入 3,210 tokens…           │ modelProgress label-12 --text-3
│ [主操作]  [打开已入库资料]  [不再提醒]                                    │ actions
│ ───────────────────────────────────────────────────────────────────────  │
│ 子任务                                  成功 12 · 处理中 2 · 等待 1 · 失败 0│ heading-16 + label-12
│ [● 已完成] 作品 A …                                                   ›   │ 48px 行（.job-child）
│ ───────────────────────────────────────────────────────────────────────  │
│ 时间线                                                                    │
│ ○ 待处理 · 09-29 20:01                                                    │ ol.job-timeline：6px 圆点 + 1px 竖线
│ ● 正在下载 · 09-29 20:02                                                  │
│ ───────────────────────────────────────────────────────────────────────  │
│ 提示 / 媒体识别模型 / 历史校对疑点 / 证据核验记录                         │ 各自一节，同样式
└───────────────────────────────────────────────────────────────────────────┘
```
- `.review-panel`、`.job-warnings`、`#job-children` 保留 class/id（`test_web_navigation.py:47-49` 断言 `job-warnings`、`hasChildWork`、`openEntryViaAction` 字符串），视觉从卡片改为"分区"：`border-top: 1px solid var(--border); padding-top: 24px; margin-top: 24px`，`h2` = `heading-16`。
- 子任务行 `.job-child` 从 `button` 文本拼接改为：徽章 + 标题 + 消息（三段 DOM），仍是 `<button>`。
- 提示列表 `.job-warning-list`：`copy-13 --text`，前缀 6px `--status-waiting` 圆点（不再整段黄色文字）。
- 网关提示 / 选片提示（`gatewayNote` / `selectionNote`）：通知条样式（5.13）。

### 4.9 专题 `/topics` 与专题详情 `/topics/{id}`

**文件**：`#topics-view`、`#topic-view`；`app.js renderTopicsList / showTopics / renderTopicData / artifactButtons / openTopic`；对话框 `#topic-dialog`。

**专题列表**：
```
┌ 栏头：专题 3                                     [ 从资料库选择来源 ] ▣ ┐  #topics-create-button（primary-sm）
├──────────────────────────────────────────────────────────────────────┤
│ ≡  AI 芯片供应链                                         09-28 21:30 │ 64px 行
│    判断 2026 年国产替代的关键环节 · 5 个启用来源                       │
├──────────────────────────────────────────────────────────────────────┤
│ ≡  手冲咖啡方案对比                                                    │
└──────────────────────────────────────────────────────────────────────┘
```
- `.topic-list-card` → 列表行（保留 class 名以减少 JS 改动，CSS 改为行样式）；删除 `nth-child` 阶梯动画、hover 位移。
- 空状态：`layers` 图标 + "还没有专题" + "在资料库里点「选择」勾选文章，创建一个只基于这些来源的研究专题。" + primary"去资料库选择"（进入资料库并开启选择模式 = 现 `#topic-select-toggle` 行为）。
- 右栏：AI 对话（library scope）。

**专题详情**：
```
┌ 栏头：← 专题 / AI 芯片供应链                                      ▣ ┐┌ 来源 · AI 对话 ──────┐
│ （最大宽 36em 居中）                                                ││ 5 / 6 个来源已启用    │
│ 专题                                                               ││ ☑ DeepSeek 多模态…  ↗ │
│ AI 芯片供应链                              heading-28 UI            ││ ☑ 为什么大模型…     ↗ │
│ 判断 2026 年国产替代的关键环节              copy-16 --text-2        ││ ☐ 黄仁勋供应链…     ↗ │
│ ▸ 自定义指令                               details（有才显示）      ││ 停用后立即退出问答范围│
│ ───────────────────────────────────────                            ││                      │
│ 研究成果                                                           ││                      │
│ [专题总览] [对比表] [证据地图] [共识与分歧] [决策简报] [FAQ]        ││                      │
│ 点击才会调用模型并消耗 token。                label-12 --text-3     ││                      │
│                                                                    ││                      │
│ 专题总览                       当前版本 · 09-28 · gpt-5-mini · 3,200 token                 │
│ （成果正文，UI 字体 17/1.75；超过 12 行折叠，"展开全文"）       ││                      │
│ ───────────────────────────────────────                            ││                      │
│ 对比表                         需要更新 ↻                           ││                      │
└────────────────────────────────────────────────────────────────────┘└──────────────────────┘
```
- 来源列表（现 `.topic-source-list` / `.topic-source-row`，`sourceList` change 事件逻辑不变）**移到右栏"来源" Tab**（新 `#topic-sources-pane`）；每行：checkbox · 标题（单行）· `↗` 打开文章（ghost 图标）。
- 成果生成按钮：secondary-sm 一行排列，生成中按钮文字"生成中…"（保持现逻辑）。
- 成果卡 `.topic-artifact-card`：去卡片化 → 分区；标题 `heading-20` UI；元信息 `label-12 --text-3`；状态：`needs_update` 显示 `rotate-ccw` 图标 + "需要更新"（`--text-2`，**不用黄色**），当前版本不显示状态文字。
- AI 对话 Tab：topic scope（`updateChatContext` 已处理），消息可"保存为专题笔记"。

### 4.10 右栏 AI 对话（所有页面共用）

**文件**：`app.html #chat-panel` 内部结构；`app.js loadSessions / createSession / renderSessionSelect / setChatContext / updateChatContext / loadMessages / makeWelcome / makeCitation / makeMessage / sendChat / setSending / resizeChatInput`。

```
┌ 信息 · 灵感 · AI 对话 ────────────────── ⊟ ┐  栏头（Tabs）
│ [我想做手冲咖啡，给我资料 ▾]      ＋   ⋯   │  40：#session-select（ghost select 满宽）#new-session #chat-menu-toggle
│ ▤ 当前文章：DeepSeek 多模态实测…            │  32：#chat-context label-12 --text-2（无底色）
├─────────────────────────────────────────────┤
│ ⚠ 尚未配置对话模型。去设置 ›                │  #chat-persistent-error：通知条样式，中性
│                                             │
│                    ┌──────────────────────┐ │  用户消息：右对齐，max 85%，--surface-sunken 底
│                    │ 我想做手冲咖啡，给我…│ │  圆角 6，padding 8 12，copy-14
│                    └──────────────────────┘ │
│ 根据你的资料，有三种冠军方案……              │  助手消息：无气泡，左对齐满宽，copy-14 --text
│ 1. V60 …                                    │
│ 2. 聪明杯 …                                 │
│                                             │
│ 引用                                        │  label-12 --text-3
│ [1] 三种冠军手冲方案  01:05 · 原作品 ↗      │  .citation：编号 accent-text，标题 label-13 链接
│     "水温 92 度，粉水比 1:15……"             │  预览 copy-13 --text-2，2 行 clamp
│ [2] …                                       │
│ ⊕ 保存为灵感                                │  .message-actions：hover/焦点时出现，ghost-sm
├─────────────────────────────────────────────┤
│ 模型：gpt-5-mini              最近一次用量：1,204 token │  .chat-status label-12 --text-3
│ ┌─────────────────────────────────────────┐ │
│ │ 向知识库提问…                           │ │  #chat-input：textarea 自适应 1–6 行（最高 160）
│ │                                    [↑]  │ │  #send-button 28×28 primary 方形圆角 6
│ └─────────────────────────────────────────┘ │
│ Enter 发送 · Shift+Enter 换行 · 只发送文字证据 │  label-12 --text-3（合并 composer-hint 与 privacy-note）
└─────────────────────────────────────────────┘
```
- 删除：`.chat-header`（"抖库 AI / 基于你的资料回答"+紫色 `.ai-mark`）——右栏 Tab 已经说明了这是 AI 对话；`.session-row` 的"历史对话"label（select 自身显示当前会话标题，`aria-label="切换历史对话"` 保留）。
- `#chat-context`：去掉 `--accent-subtle` 底色块，改为一行 `label-12 --text-2` + 16px 图标（library / file-text / layers）。
- 欢迎态 `makeWelcome()`：去掉星芒徽标；标题"问问你的资料库"`heading-16`，说明 `copy-13 --text-2`，下面给 3 个示例问题（ghost 行按钮，点击填入输入框：列表上下文"最近一周我收藏了哪些 AI 相关内容？"、文章上下文"这篇的核心论据是什么？"、专题上下文"各来源的主要分歧是什么？"）。
- 流式中：助手消息末尾 8px 宽闪烁光标（`opacity` 1↔0，1s steps(2)），reduced-motion 下常亮；`#send-button` 切换为 `square` 停止图标（现有行为）。
- 错误消息 `.message.assistant.error`：`copy-14 --danger` + `alert-circle`，无底色。
- `.message.is-new`：保留 class（测试断言），动画改为 160ms 纯淡入（无位移）。
- "最近一次用量：${latestAssistant.total_tokens} token" 字符串保留（测试断言）。

### 4.11 设置 `/settings` 及子页

**文件**：`#settings-view`（**移入 `<main>`**）、`#auth-view`（`auth.js`）、`#analysis-view`（`analysis-settings.js`）、`#model-view`（`model-settings.js`）、`#system-view`（`system-settings.js`）。

**设置首页**：
```
┌ 栏头：设置 ──────────────────────────────────────────────────── ▣ ┐
│ （最大宽 640）                                                     │
│ ⎆ 授权状态      抖音账号 · 视频下载                 已授权 2/2  › │ 56px 行（替代 4 张 status-card）
│ ⑂ 导入分析      导入后由谁整理                      后台模型接口 › │ 右侧 = 当前值 label-13 --text-2
│ ◫ 共用模型      对话与后台整理共用                  gpt-5-mini   › │
│ ▭ 系统设置      Worker、存储、识别模型              运行中       › │
└────────────────────────────────────────────────────────────────────┘
```
- 当前值来源：`/api/auth/status`、`/api/system/analysis`、`/api/settings/model`、`/api/system/health`（均已存在；并行请求，失败显示"—"）。

**子页通用骨架**：栏头"设置 / 共用模型"（"设置"可点）；内容最大宽 640；分区 = `heading-20` UI 标题（可选一行 `copy-13 --text-2` 说明）+ 字段；分区间 1px 分隔线 + 32。删除 `status-card status-card-inline` 与 `.status-indicator` 彩色圆点：配置状态改为一行文本 + 图标（`check` / `alert-circle`，颜色 `--text` / `--text-2`）。

- **共用模型** `#model-view`：字段 接口地址（`#model-base-url` + datalist）、模型名称、API Key（`#toggle-key` 眼睛按钮在输入框内右侧 28px）、`#key-status` 改为 `label-12` 中性徽章（"已保存在 Keychain" / "未设置"）；"清晰的数据边界"那段 `alert-banner` 压缩为两行 hint；底部 primary"保存配置"+ secondary"测试连接"+ `label-12 --text-3`"测试连接会产生少量 token"。
- **导入分析** `#analysis-view`：`.analysis-mode-card` 三选一改为单选行（16px radio · 标题 `label-14` 500 · 说明 `copy-13 --text-2` 两行内）；选中行：1px `--accent` 边框 + `--accent-subtle` 底（选中 = accent 允许用途）；`#provider-model-panel` 用通知条；primary"保存分析方式"。
- **授权状态** `#auth-view`：两条通道（`#auth-video-card`、`#auth-douyin-card`）改为两个分区（不再并排卡片）：标题 `heading-16` · 用途 `copy-13 --text-2` · 状态行（`status-pill` 改为**中性**文字徽章 + 图标：已授权 `check`，过期/未授权 `alert-circle` + 文字加粗；授权页**不使用状态色**）· 上次检查 · "详情"折叠 · 操作（secondary"检查"、secondary"重新授权"、ghost"查看受影响任务"）。`#auth-session`（进行中的登录会话）：通知条 + 不定进度条 + secondary"稍后处理"。
  - CSS：把 `.status-pill[data-state=authorized|expired|needs_login|unauthorized|authorizing]` 从状态色规则中移除，限定状态色选择器为 `#jobs-view .status-pill, #job-detail-view .status-pill`。
- **系统设置** `#system-view`：
  - `#system-overview`：`.system-metric` 4 项横排统计条（每项：`label-12 --text-3` 标题 / `heading-20` UI 字体 tabular 数值 / `label-12 --text-2` 说明），项间 1px 竖分隔，无卡片；<960 两列。
  - `#system-health`、`#system-storage`：键值行（左 `copy-13 --text-2`，右 `copy-13 --text` tabular），行间 1px 分隔。
  - 系统检查：secondary"运行检查"；结果列表每项 `check`/`alert-circle` 图标 + 文本。
  - 维护：`.system-action-row` = 左标题+说明，右 secondary"预览"+ danger-ghost"执行维护"/"重建数据库"（真正执行在确认弹窗里用 danger 实心按钮）；预览结果 `.system-preview` 用 `--surface-sunken` 底、圆角 6、padding 16。

### 4.12 废纸篓 `/trash`

**文件**：`#trash-view`；`app.js renderTrash / updateTrashSelection / setTrashSelection / openTrashBatchDialog / showTrash`。

```
┌ 栏头：废纸篓 7                         全选 · 全不选                ▣ ┐  #trash-select-all #trash-clear-selection（ghost-sm）
│ 删除的资料保存在本机，可恢复或彻底删除。                               │  label-12 --text-3（36 通知行）
├──────────────────────────────────────────────────────────────────────┤
│ ☐ ▶ DeepSeek 多模态实测                          [恢复] [彻底删除]    │  64px 行
│       林禾Mind · 删除于 09-28 21:10 · 12.3 MB                         │
├──────────────────────────────────────────────────────────────────────┤
│ …                                                                     │
├──────────────────────────────────────────────────────────────────────┤
│ 已选 3 条                       [ ↺ 恢复所选 ]  [ 彻底删除所选 ]       │  #trash-confirm sticky 底栏（有选择时出现）
└──────────────────────────────────────────────────────────────────────┘
```
- `.trash-item`：checkbox 16 · 来源图标 16 `--text-3`（删除 `.trash-item-icon` 彩色瓷贴）· 标题 `label-14` 500 + 元信息 `label-12 --text-2 tabular-nums` · 操作（hover/焦点时显示；触屏常显）：secondary-sm"恢复"、danger-ghost-sm"彻底删除"。
- 批量底栏：secondary"恢复所选"、danger-ghost"彻底删除所选"（确认弹窗里才是实心 danger）。
- 空状态：`trash-2` + "废纸篓是空的" + "从文章页删除的资料会暂存在这里。"
- 右栏默认收起。键盘：`j/k`、`x` 勾选、`Shift+j/k` 连选。

### 4.13 404 `not-found-view`（移入 `<main>`）

空状态组件：`alert-circle` + "页面不存在" + "没有找到这个地址。" + primary"返回资料库"（`data-back="library"`）。保留文案"页面不存在"（`test_web_navigation.py:76`）。

### 4.14 命令面板 `#command-dialog` 与快捷键表 `#shortcuts-dialog`

- 命令面板：宽 560（<640 时 `100vw - 32px`），距顶 20vh，圆角 6，`--shadow-overlay`，无入场动画（保持现状）。输入框高 48，`--fs-16`，无边框，下 1px 分隔；结果行 40（标题 `label-14` + 作者 `label-12 --text-2`，缩略图 24×32），激活行 `--surface-selected` + 左 2px accent 条；底部 `.command-help` `label-12 --text-3`。
- 扩展（Phase 5）：输入以 `>` 开头或无匹配文章时显示命令组："导入单条 / 导入博主 / 导入抖音收藏 / 打开任务中心 / 新建 AI 对话 / 切换主题 / 显示快捷键"，右侧显示对应快捷键 kbd。
- 快捷键表（新增 `<dialog id="shortcuts-dialog">`）：宽 640，两列分组（全局 / 列表 / 阅读 / 任务），每行 `copy-13` + kbd 组合；`?` 打开，`Esc` 关闭。

### 4.15 其他弹窗

- `#inspiration-dialog`（保存为灵感）：删除"写回 Obsidian"kicker 的 `sparkles` 图标，kicker 改 `label-12 --text-3`"写回 Obsidian"；字段按 5.2；按钮右对齐：secondary"取消" + primary"确认保存"。
- `#topic-dialog`（创建专题）：同上；`#topic-selection-summary` 显示"已选 5 篇"+ 前 3 个标题。
- `#destructive-dialog`：宽 400；kicker 去掉，标题 `heading-16`；说明 `copy-14 --text-2`；按钮 secondary"取消" + **danger 实心**确认（全应用唯一的实心红色）。

---

## 5. 组件库

所有尺寸单位 px。"hover"只在 `@media (hover: hover) and (pointer: fine)` 内生效。所有过渡 `var(--dur-fast) ease`（颜色/背景/边框），**禁止** `transform` 类按压反馈。

### 5.1 按钮

| 变体 | class | 高 | 水平 padding | 字体 | 默认 | hover | active | disabled |
|---|---|---|---|---|---|---|---|---|
| Primary | `.primary-button` | 32 | 12 | label-13 | bg `--accent`，字 `--on-accent`，无边框 | bg `--accent-hover` | bg `--accent-pressed` | opacity .4，`cursor: not-allowed` |
| Secondary | `.secondary-button` | 32 | 12 | label-13 | bg `--surface`，1px `--border`，字 `--text` | 边框 `--border-strong`（仅此） | bg `--surface-hover` | opacity .4 |
| Ghost | `.ghost-button`（新；`.text-button` `.toolbar-button` 归并到此） | 32 | 8 | label-13 | 透明，字 `--text-2` | bg `--surface-hover`，字 `--text` | bg `--surface-selected` | opacity .4 |
| Danger | `.danger-button` | 32 | 12 | label-13 | bg `--danger`，字 #FFF | bg `color-mix(in srgb, var(--danger) 88%, #000)` | 同 hover | opacity .4 |
| Danger-ghost | `.danger-text-button` | 32 | 8 | label-13 | 透明，字 `--danger` | bg `--danger-subtle` | 同 hover | opacity .4 |
| Small 修饰 | `.is-sm` | 28 | 8 | label-12 | — | — | — | — |
| Icon | `.icon-button` | 32×32（栏头）/ 28×28（`.is-sm`，行内、面板） | 0 | 图标 16 | 透明，`--text-2` | bg `--surface-hover`，`--text` | bg `--surface-selected` | opacity .4 |

通用：`display: inline-flex; align-items: center; justify-content: center; gap: 8px; border-radius: var(--radius); white-space: nowrap; cursor: pointer`；focus-visible = `--focus-ring`；带图标时图标 16 在文字左侧；`aria-pressed="true"` 的 ghost/icon 按钮：字/图标 `--text`，bg `--surface-selected`。
加载态：按钮文字换成动词进行时（"正在提交…"），保持宽度（`min-width` = 初始宽度，JS 在提交前写入 `style.minWidth`），不加 spinner。

### 5.2 输入控件

| 控件 | 规格 |
|---|---|
| Text input / select | 高 32，padding `0 12`，`copy-14`，bg `--surface`，1px `--border`，圆角 6；hover 边框 `--border-strong`；focus 边框 `--accent` + `--focus-ring`；disabled bg `--surface-sunken`，字 `--text-3`；错误 `aria-invalid="true"` → 边框 `--danger` |
| Select | 同上 + 右侧 16px `chevron-down`（`background-image` 用 CSS mask 或内联 SVG data URI），`appearance: none`，右 padding 32 |
| Ghost select（栏头排序、会话切换、任务筛选） | 无边框透明，`label-13 --text-2`，hover bg `--surface-hover`，高 32 |
| Textarea | padding `8 12`，`copy-14`（行高 22），`resize: vertical`，最小 3 行 |
| Search | 高 32，左 12 放 16px `search` 图标，右侧 `kbd`（⌘K），placeholder `--text-3` |
| Checkbox | 16×16，圆角 4，1px `--border-strong`；checked bg `--accent` + 白色 `check`（stroke 2）；indeterminate 白色横线；focus `--focus-ring`；用 `accent-color: var(--accent)` 兜底 |
| Radio | 16×16 圆，checked 内圆 6px `--accent` |
| Switch（仅"仅显示有灵感的文章"） | 28×16，圆角 8 —— **例外**：开关形状需要全圆，允许 `border-radius: 999px`，仅此一处；开 = `--accent` |
| 字段 label | `label-14` 500 `--text`，与控件间距 8；hint `label-12 --text-3`，控件下 4；错误 `copy-13 --danger`，控件下 4 |
| `details` 折叠 | summary 高 32，`label-13 --text-2`，左 16px `chevron-right`（展开旋转 90°，`--dur-base`） |

### 5.3 Kbd

`display: inline-grid; min-width: 20px; height: 20px; padding: 0 4px; font: var(--fw-medium) var(--fs-12)/20px var(--font-mono); color: var(--text-3); border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface);`

### 5.4 Tabs

| 类型 | 规格 |
|---|---|
| 面板/栏头 Tab（右栏、资料库来源 Tab） | 高 48（贴栏头底边），padding `0 12`，`label-13`；默认 `--text-2`；hover `--text`；选中 `--text` + 底部 2px `--accent`（`box-shadow: inset 0 -2px 0 var(--accent)`）；`role="tab"`、`aria-selected`、`aria-controls`；`←/→` 切换，`Home/End` |
| Segmented（视图切换） | 1px `--border` 外框、圆角 6、内含 28×28 图标按钮 ×2；选中项 bg `--surface-selected`、图标 `--text`；未选 `--text-3` |

### 5.5 侧栏导航项

见 3.2：高 32、padding `0 8`、gap 8、图标 16、`label-14`、计数 `label-12 --text-3`、hover `--surface-hover`、选中 `--surface-selected` + 字重 500 + 图标 `--accent`；`aria-current="page"` 与 `.active` 同步。

### 5.6 列表行（统一组件）

| 变体 | 高 | 结构 | 用于 |
|---|---|---|---|
| `row--entry` | 88（<640：64，隐藏摘要，缩略图 36×48） | 缩略图 48×64 · 标题/摘要/元信息三行 · 右侧日期或 hover 操作 | 资料库、命令面板（缩小版 40） |
| `row--compact` | 64 | [前置：checkbox / 状态徽章 / 图标] · 标题 + 一行说明 · 右侧操作 + 时间 | 任务、专题、废纸篓、设置首页（56）、导入中心方式（56） |
| `row--check` | 48 | checkbox · 标题 · 右侧元信息 | 导入清单（博主、收藏）、专题来源（右栏，40） |

通用状态：
- 默认：透明底，下 1px `--border`（最后一行无）。
- hover：`--surface-hover`。
- 焦点（键盘 / `.is-focused`）：`--surface-selected` + `inset 2px 0 0 var(--accent)`。
- 选中（checkbox 勾选）：`--accent-subtle`。
- disabled：文字 `--text-3`，不响应 hover。
- 行内操作：`opacity: 0` → hover/焦点/`:focus-within` 时 `opacity: 1`（`--dur-fast`）；触屏（`hover: none`）常显。

### 5.7 卡片（仅专辑墙）

- `.gallery-card`：无背景、无边框、无阴影；封面 `.gallery-cover` 3:4，圆角 6，`outline: 1px solid var(--border); outline-offset: -1px`。
- hover：outline 变 `--border-strong`（**仅加深边框**，不上浮、不放大、不加阴影）。
- 焦点/选中：`box-shadow: 0 0 0 2px var(--bg), 0 0 0 4px var(--accent)` 作用在封面上。
- 标题 `label-14` 500 两行 clamp（上间距 8），元信息 `label-12 --text-2` 单行（上间距 4）。
- 选择模式：左上 8/8 位置 16px checkbox（白底带 1px 边框）。

### 5.8 Tag 胶囊

`height: 20px; padding: 0 8px; font: 500 var(--fs-12)/20px var(--font-ui); color: var(--text-2); background: var(--surface-sunken); border-radius: var(--radius-sm); max-width: 120px; overflow: hidden; text-overflow: ellipsis;` 不带 `#` 前缀。
hover（可点击时）：字 `--text`。激活（作为筛选条件）：bg `--accent-subtle`、字 `--accent-text`。
已应用筛选 chip（`makeFilterChip`）：同尺寸，前缀维度名（"博主：林禾Mind"），后跟 12px `x` 图标按钮。

### 5.9 Toast（`#toast`，`shared.js Douku.toast`）

- 位置：底部居中，距底 24；`z-index: var(--z-toast)`。
- 尺寸：高 40（多行时自动），`max-width: 480px`，padding `0 16`，`copy-13`，圆角 6。
- 配色：亮色 bg `#1A1A1A` 字 `#FAF9F7`；暗色 bg `#EDEBE7` 字 `#1A1A1A`。`--shadow-overlay`。
- 动画：`opacity 0→1` + `translateY(4px→0)`，`--dur-base` `--ease`；消失 `--dur-base`。时长沿用 `TOAST_MS = 2600`，sticky 模式沿用。
- 带操作（如 `showRestoreRetry` 的"重试"）：右侧文字按钮，字色同 toast 文字 + 下划线（不用 accent，深底上对比不足）。
- 删除 toast 的 `backdrop-filter`。

### 5.10 进度条

| 变体 | 高 | 轨道 | 填充 | 用于 |
|---|---|---|---|---|
| 行内 | 2 | 透明 | `--accent` | 任务行底、文章栏头阅读进度 |
| 详情 | 4 | `--border` | `--accent`，圆角 2 | 任务详情 |
| 不定 | 2 | `--border` | 30% 宽 `--accent` 段，`translateX(-100%→333%)` 1.2s linear infinite | 清点中、授权会话中；reduced-motion 时显示静止 30% |

`role="progressbar"`，`aria-valuenow`/`aria-valuemin`/`aria-valuemax`（不定态省略 valuenow）。

### 5.11 状态徽章（`.status-pill`，仅任务中心）

`display: inline-flex; align-items: center; gap: 4px; height: 20px; padding: 0 8px; font: 500 var(--fs-12)/20px var(--font-ui); border-radius: var(--radius-sm); color: var(--status-*); background: var(--status-*-bg);` 前置 6×6 圆点 `background: currentColor; border-radius: 50%`（圆点允许全圆）。映射见 4.7。任务中心外的 `.status-pill`（授权页、系统页）使用中性版：`color: var(--text-2); background: var(--surface-sunken)`。

### 5.12 空状态（`Douku.emptyState` + 模板内 `.empty-state`）

`max-width: 360px; margin: 64px auto; text-align: center;` 图标 24 `--text-3`（下 12）→ 标题 `heading-16`（下 4）→ 说明 `copy-14 --text-2`（下 16）→ 操作按钮（secondary，首次引导用 primary）。`Douku.emptyState(title, copy, actionLabel, action, icon)` 新增可选 `icon` 参数。

### 5.13 通知条（`.alert-banner` `.todo-banner` `#creator-focus-bar` 等）

`display: flex; align-items: center; gap: 8px; min-height: 36px; padding: 8px 24px; background: var(--surface-sunken); border-bottom: 1px solid var(--border); copy-13 --text;` 左侧 16px 图标（`info` / `alert-circle`，`--text-2`）；右侧文字链接（`--accent-text`，带 `chevron-right`）。待办条（`#library-todo`）左侧用 6px `--accent` 圆点代替图标。**不使用黄/红底色。**

### 5.14 弹窗（`<dialog>`）

- 宽 480（确认类 400；命令面板 560；快捷键 640），`max-height: calc(100dvh - 64px)`，padding 24，圆角 6，bg `--surface`，1px `--border`，`--shadow-overlay`。
- 背景 `::backdrop { background: var(--overlay-backdrop); }`，**无 blur**。
- 结构：标题 `heading-16`（kicker 可选 `label-12 --text-3` 在其上）→ 说明 `copy-14 --text-2`（上 8）→ 字段（上 16，字段间 16）→ 错误 `copy-13 --danger` → 按钮行（上 24，右对齐，gap 8，取消在左、确认在右）。
- 右上关闭按钮 `.icon-button.is-sm`。
- 动画：`opacity` + `translateY(4px→0)`，`--dur-base`；删除 `dialog-enter` 弹性动画。

### 5.15 浮层菜单（筛选、导入菜单、会话菜单 `#chat-menu`、行内 `⋯` 菜单）

`min-width: 200px; padding: 4px; background: var(--surface); border-radius: var(--radius); box-shadow: var(--shadow-overlay); z-index: var(--z-popover);` 菜单项高 32，padding `0 8`，gap 8，图标 16，`label-14`；hover/键盘激活 `--surface-hover`；危险项字 `--danger`；分隔线 1px `--border` 上下 4。入场 `opacity` 0→1 `--dur-base`（删除 `scale(.97)`）。

### 5.16 AI 消息与引用

| 元素 | 规格 |
|---|---|
| `.message.user` | `margin-left: auto; max-width: 85%; padding: 8px 12px; background: var(--surface-sunken); border-radius: var(--radius); copy-14 --text`；**不用 accent 填充** |
| `.message.assistant` | 无背景无边框无阴影；`copy-14 --text`；段落间 8；列表缩进 24；代码块 `--surface-sunken` 圆角 6 padding 12 `--fs-13` |
| 消息间距 | 16；同一轮 user→assistant 间距 12 |
| `.message-actions` | 消息下方 4，ghost-sm 按钮（"保存为灵感" `quote` 图标、"保存为专题笔记" `layers` 图标）；默认 `opacity: 0`，消息 hover / `:focus-within` 时 1；最后一条助手消息常显 |
| `.citations` | 上 12；标题"引用"`label-12 --text-3`；每条 `.citation`：`grid-template-columns: 20px 1fr; gap: 8px; padding: 8px 0; border-top: 1px solid var(--border)` |
| `.citation-number` | `label-12 --accent-text tabular-nums`，`[1]` 形式 |
| `.citation-title` | `label-13 --text`，hover 下划线；点击 = `openArticle(entry_id, true, null, citation)`（现逻辑） |
| `.citation-meta` | `label-12 --text-3`：位置（01:05 / 第 2 张图片 / 文章片段）· "原作品 ↗" |
| `.citation-preview` | `copy-13 --text-2`，2 行 clamp |
| 流式光标 | `::after` 8×16 `--text-3` 块，1s steps(2) 闪烁 |
| 错误 | `copy-14 --danger` + `alert-circle` 16 |

### 5.17 骨架屏

`.skeleton-row`：与目标行同高；内部块 bg `--surface-sunken`，圆角 4；无 shimmer；可选 `opacity .6↔1` 1.2s ease-in-out 呼吸（reduced-motion 关闭）。

---

## 6. 实施计划（交给 Codex）

### 6.0 总约束

- 不引入前端框架/构建工具/CDN；仍是 `app.html` + 原生 JS + 一个主样式 `app.css`，阅读字体独立为按需附加的 `reading.css`。
- Phase 表示实现与验证范围；按本轮要求，Phase 0–6 与 review 修正一次性提交。收尾运行 `uv run pytest -m "not live"` 和 `uv run ruff check .`（包含本章 6.3 列出的测试修改）。
- 本地验证用隔离实例：`DOUYIN_WIKI_CONFIG=$PWD/.test-web/config.toml uv run douyin-wiki web run`（端口 8766，见 `docs/web-ui-design-system.md` 末尾）。
- 每个 Phase 同时验收 1280px 栏头无碰撞、390px 专题选择可完成、800px 各 Tab 的抽屉焦点循环、正文图片保留和列表/代码块段落导航。
- 验证截图存 `docs/screenshots/redesign/phase-N/`（1440×900 亮/暗 × 资料库/文章），供本地对比；整个 `docs/screenshots/` 被 Git 忽略。review 另截待办条亮暗两张。
- CSS 组织：`app.css` 按以下顺序分段并加注释标题：`@font-face` → tokens → base → utilities（字体角色）→ shell → sidebar → headers → components（按 5.x 顺序）→ pages（按 4.x 顺序）→ responsive → reduced-motion → print。删除所有被新规则取代的旧规则，不要叠加覆盖。

### Phase 0 — 令牌、字体、去装饰（视觉立即变化，结构不动）

| 文件 | 改动 |
|---|---|
| `static/fonts/`（新建） | `InterVariable.woff2`、`Newsreader-Variable.woff2`、`DokuSerifSC-400.woff2`、`DokuSerifSC-600.woff2`（按 2.4 生成） |
| `scripts/fonts/charset-gb6500.txt`（新建） | 通用规范汉字表一、二级字符 |
| `static/app.css` | 顶部加 `@font-face`；替换两个令牌块为 2.2 全文（含兼容层）；全局删除：所有 `backdrop-filter`/`-webkit-backdrop-filter`、所有 `transform: scale(.97)` 规则、`linear-gradient`（8 处）、`.ai-mark` 渐变与阴影、`--nav-*` 瓷贴背景（`.nav-item svg` 的 28px/padding/背景）、专题卡 `nth-child` 动画延迟、`gallery-card.is-entering` 动画；`box-shadow` 只保留 focus ring 与浮层 `--shadow-overlay`；`.article-body` 改宋体 17/1.75、`max-inline-size: var(--measure)`；`.article-body h2/h3` 按 4.2 |
| `templates/app.html` | `<head>` 加 Inter preload |
| `pyproject.toml` | 通常不用改（hatchling 会打包包目录下的文件）；只有 `.gitignore` 排除了 `*.woff2` 时才处理 |
| `tests/test_web.py` | 484 行断言改为 `.article-body { max-inline-size: var(--measure)` |

验收：
- [x] 页面中无渐变、无毛玻璃、无按压缩放；`rg "backdrop-filter|linear-gradient|scale\(" app.css` 无匹配（进度条的 `translateX` 不算）。
- [x] 主按钮为墨绿实心；导航无彩色瓷贴。
- [x] 文章正文为宋体 17px，行宽约 36 字（DevTools 量 `.article-body` 宽 612px）。
- [x] 阻断外部网络请求后刷新，本地字体正确加载；独立浏览器上下文确认非文章页不请求宋体，文章页请求阅读字体。未执行操作系统整体断网；浏览器探针保留本地 TestClient 请求，外部请求为 0。
- [x] Web 定向测试已包含在完整非 live 回归内，457 项通过（1 项 live 排除）；记录见当前设计系统验证节。

### Phase 1 — 应用外壳：三栏、侧栏、栏头、右栏容器、断点、快捷键基础

| 文件 | 改动 |
|---|---|
| `templates/app.html` | ① 把 `#settings-view`、`#not-found-view` 移入 `<main>`；② 侧栏重排为 3.2 结构（`#imports-toggle` + `#imports-popover` 移到侧栏顶部；新增"搜索"按钮；分组 资料/专题/标签/工作；删除 brand-mark `<img>` 与"个人知识工作台"）；新增空的 `<nav id="reader-toc" class="reader-toc hidden">`；③ 所有内联实心 SVG 换成 `<svg class="icon"><use href="/static/icons.svg#…"></use></svg>`；④ 删除全局 `<header class="topbar">`，在 `#library-view` 顶部新建 `.view-header`（4.1 控件），其他视图各自新建 `.view-header`（标题 + 必要控件），删除全部 `.article-breadcrumb`（`#back-library`、`#back-topics`、`#back-jobs` 按钮移入对应栏头，id 不变）；⑤ `.page-heading` 删除（`h1` 移入栏头）；⑥ 右栏：新建 `<aside class="side-panel" id="side-panel">`，把原 `#chat-panel` 移入作为 tabpanel，新增 `#info-pane` `#inspiration-pane` `#topic-sources-pane` 与 tablist；删除 `.chat-header` 与 `.ai-mark`；⑦ 删除 `#density-toggle`；⑧ 新增 `<dialog id="shortcuts-dialog">` |
| `static/icons.svg` | 新增 2.5 列出的 symbol |
| `static/app.css` | shell 网格（3.1）、侧栏（3.2/5.5）、`.view-header`（3.4）、`.side-panel` 与 tabs（3.5/5.4）、断点（3.6，删除 719/639/359 组）、按钮/输入/kbd/弹窗/菜单/toast/通知条/空状态（5.1–5.3、5.9、5.12–5.15） |
| `static/shared.js` | `Douku.setPage(name)` 末尾派发 `douku:page` 事件；新增 `Douku.panel = { setTabs(page), select(tab), open(), close() }`（或在 app.js 实现后挂到 `window.Douku`）；`Douku.emptyState` 加 `icon` 参数 |
| `static/app.js` | `readPreferences()`：`STORAGE.chat` 默认 `"false"`；`renderPanelState()`：三档断点（≥1280 grid 列、960–1279 覆盖层、<960 抽屉）；`setChatPanelOpen()` 改为开合整个右栏；新增 `renderPanelTabs(page)`、`selectPanelTab(tab)`（localStorage 记忆）；`handleGlobalKeydown()` 扩展：`?`、`g x` 序列、`⌘I`、Esc 优先级；删除 `renderToolbarState` 中 `#density-toggle` 相关与 `STORAGE.density`/`VALID_DENSITIES`/`state.density`（`<640` 紧凑改由 CSS 媒体查询实现）；`positionImportsPopover()` 改锚定侧栏按钮 |
| `static/jobs.js` `auth.js` `imports-*.js` `system-settings.js` `analysis-settings.js` `model-settings.js` | 仅调整因栏头/面包屑移除导致的 DOM 查询（若有）；逻辑不动 |

验收：
- [x] 1440 宽：三栏 240 / 自适应 / 360 同时可见，右栏 Tab 随页面变化（表 3.5）。
- [x] `[` `]` 开合两侧栏并记忆；1100 宽时右栏为覆盖层；800 宽时两侧均为抽屉，焦点陷阱可用（`trapDrawerFocus`）。
- [x] 设置页、404 页在 `<main>` 内（`document.querySelector("main #settings-view")` 非空）。
- [x] 全站没有"← 资料库"面包屑；页面副标题 ≤ 1 行。
- [x] `?` 打开快捷键表；`g j` 进入任务中心；`⌘I` 打开导入菜单。
- [x] 键盘 Tab 走查：所有控件焦点环可见（accent 双环）。
- [x] 测试通过（按 6.3 更新）。

### Phase 2 — 资料库：列表默认、行组件、专辑墙、信息面板、j/k

| 文件 | 改动 |
|---|---|
| `static/app.js` | `UI_PREFERENCE_VERSION` → `"3"`，默认视图 `"list"`（4.1"默认视图迁移"）；`makeListItem()` 重写为 5.6 `row--entry`（缩略图 3:4、三行、右侧日期/hover 操作、⋯ 菜单）；`makeMetadata()` 改顺序与格式（4.1）；`makeGalleryCard()` 去掉入场动画与 `.gallery-copy` 旧结构（保留 `makeCover(item, "gallery-cover")`）；新增 `state.focusedEntryId`、`moveFocus(delta)`、`focusEntry(id, {scroll})`、`openFocused()`；列表 roving tabindex；新增 `renderInfoPanel(item, {context})` 与资料库统计态；来源 Tabs（`state.sources`）并从 `renderFilters()` 删除来源 fieldset；`renderSidebarTags()` 改文字行；侧栏计数 `renderSidebarCounts()`；选择模式 checkbox 替换 `makeSelectionIndicator()` 视觉（函数名保留）；`#result-count` 只输出数字 |
| `static/app.css` | `.library-item` / `.gallery-card` / `.gallery-cover`（保留 `aspect-ratio: 3 / 4` 字面量）/ `.item-tag` / 筛选浮层 / 信息面板 `.info-pane` / 选择模式 |
| `templates/app.html` | `#library-view` 栏头控件（4.1）；`#empty-library` 新文案与三条快捷入口 |
| `tests/test_web.py` | 491 行 `"2"` → `"3"` |

验收：
- [x] 首次打开（清空 localStorage）默认列表视图；`?view=gallery` 进入专辑墙；刷新保持选择。
- [x] 行高 88，缩略图 48×64，标题/摘要各单行省略；hover 出现 ⊡ 与 ⋯，日期隐藏。
- [x] `j/k/↑/↓` 移动焦点，焦点行左侧墨绿竖条，右栏信息同步更新；`Enter` 打开；`f` 切换库内收藏并 toast。
- [x] 专辑墙：无阴影，hover 只加深边框；方向键按网格移动。
- [x] 来源 Tab 切换写入 URL `source=video`；筛选浮层无"来源类型"。
- [x] 选择模式勾选 3 篇 → "创建专题"对话框显示已选 3 篇。
- [x] 空库、无结果、加载错误三态与 4.1 表一致。

### Phase 3 — 阅读器：目录、栏头、正文排版、信息/灵感 Tab、段落焦点

| 文件 | 改动 |
|---|---|
| `static/app.js` | `makeArticleHeader()` 按 4.2 重写（保留 `.article-cover` 在 hero 内；操作移到栏头与 ⋯ 菜单，保留"删除文章""彻底删除"等测试断言字符串所在逻辑）；`renderArticleData()` 后调用 `buildReaderToc()`、`decorateChapterTimes()`、`decorateCallouts()`、`initReadingFocus()`、`bindReadingProgress()`；新增 `renderInspirationsPanel(item)`；上一篇/下一篇 `openAdjacentArticle(delta)`；`showLibrary()` 恢复 `state.libraryScrollTop` 与焦点；快捷键 `n/p/f/Shift+I/Shift+N/Shift+C/Shift+O/u` |
| `static/app.css` | `.article-hero`、`.article-body`（4.2 全部）、`.reader-toc`、`.chapter-time`、`.is-reading-focus`、阅读进度条、`data-page="article"` 时侧栏切换 |
| `templates/app.html` | `#article-view` 栏头（返回、上/下一篇、⊡、↗、⋯）；`#reader-toc` 结构 |
| `webapp/rendering.py` | 负责封面移除及 callout 标题去重、正文分段、空关系提示过滤；JS 仅按实际 HTML 添加样式，见 4.2 |

验收：
- [x] 打开文章：左栏变目录并随滚动高亮当前章节；点目录跳转且标题不被栏头遮挡。
- [x] 标题宋体 28，正文宋体 17/1.75，行宽 ≈36 字；h2 为 20px 宋体深色标题（不再是灰色小标签）。
- [x] 时间轴章节显示"01:05 一、标题"，时间码为灰色等宽数字，无 ▶。
- [x] 从 AI 引用点进文章：定位段落出现墨绿浅底 `<mark>`，1.8s 后淡出（`focusCitation` 行为保留）。
- [x] `↑/↓` 段落焦点竖条移动；`Esc` 回到列表原滚动位置与焦点行；`n/p` 切换相邻文章。
- [x] 右栏"灵感"列出 `inspirations`，`⇧N` 打开添加对话框，保存后列表刷新。
- [x] 从专辑墙点封面进入，封面 view transition 在 Chromium 验证通过；保留原生特性检测和降级，Safari 18 实机验证待补。

### Phase 4 — 右栏 AI 对话重做

| 文件 | 改动 |
|---|---|
| `templates/app.html` | `#chat-panel` 内部：会话行（`#session-select` 满宽 ghost + `#new-session` + `#chat-menu-toggle`）、`#chat-context`、`#chat-persistent-error`、`#chat-messages`、`.chat-status`、`#chat-form`（合并 `.composer-hint` 与 `.privacy-note` 为一行） |
| `static/app.js` | `makeWelcome()`（去星芒，加 3 个上下文示例问题）；`makeMessage()`（结构不变，class 不变；actions 默认隐藏）；`makeCitation()`（编号 `[n]`、结构 5.16）；`setChatContext()` 图标换 `layers` 用于专题；流式光标 class `is-streaming` |
| `static/app.css` | 5.16 全部；删除旧 `.chat-header`、`.ai-mark`、`.chat-welcome .ai-mark`、气泡圆角/阴影、`message-enter` 位移动画（`.message.is-new` 改 160ms 淡入） |

验收：
- [x] 用户消息浅灰块右对齐，助手消息无气泡；页面里没有紫色、没有星芒。
- [x] 流式时有闪烁光标，完成后出现引用列表（编号墨绿），点击引用跳转并高亮。
- [x] "保存为灵感"只在 hover/聚焦时出现（最后一条常显）。
- [x] 未配置模型时显示中性通知条 + "去设置"。
- [x] 文章页 / 专题页 / 资料库三种上下文提示正确（沿用 `updateChatContext` 文案）。

### Phase 5 — 操作类页面 + 命令面板扩展

| 文件 | 改动 |
|---|---|
| `templates/app.html` | `#imports-view`（方式行 + 最近导入 + 可选粘贴框）、`#imports-single-view`、`#imports-creators-view`、`#imports-favorites-view`（去 `system-panel`/`status-card` 壳，sticky 底栏）、`#jobs-view`、`#job-detail-view`、`#topics-view`、`#topic-view`、`#trash-view`、`#settings-view`（行列表）、`#auth-view`、`#analysis-view`、`#model-view`、`#system-view`；`#command-dialog` 命令组容器 |
| `static/jobs.js` | `renderRow()` DOM 按 4.7（徽章 / 标题 / 消息 / 操作 / 时间 / 进度条）；子任务行三段 DOM；列表 `j/k/Enter/r`；刷新后按 id 恢复焦点。保留测试断言字符串：`等待你`、`不再提醒`、`display_title`、`rest: "其他任务"`、`已忽略`、`openEntryViaAction`、`hasChildWork`、`job-warnings` |
| `static/imports-creators.js` `imports-favorites.js` | 行结构 `row--check`；`Shift` 范围选择（两个 selection 接口都用 `work_ids` 数组）；已选计数写入底栏 |
| `static/imports-single.js` | 仅样式相关 DOM（错误位置） |
| `static/auth.js` | 卡片 → 分区；状态徽章改中性 + 图标 |
| `static/system-settings.js` | `.system-metric` 统计条、键值行 |
| `static/analysis-settings.js` `model-settings.js` | 单选行、状态行（去彩色圆点 `.status-indicator`） |
| `static/app.js` | `renderTopicsList()` 行样式与新空状态；`renderTopicData()` 把来源列表渲染到 `#topic-sources-pane`，成果区去卡片 + 折叠；`renderTrash()` 行结构；命令面板命令组（`>` 前缀）；设置首页当前值加载 `loadSettingsSummary()` |
| `static/app.css` | 4.3–4.14 全部页面样式；状态色选择器限定到 `#jobs-view`、`#job-detail-view` |

验收：
- [x] 状态色（蓝/琥珀/红）只出现在任务中心与任务详情（在授权、系统、导入页面检查不到彩色徽章）。
- [x] 导入博主/收藏：勾选 → 底栏计数实时变化；`Shift` 点击可范围选择；确认按钮在不满足条件时 disabled（沿用现 `controls()` 逻辑）。
- [x] 任务中心：分组标题正确，进行中任务行底有墨绿进度条，`j/k` 可用，SSE 刷新不丢焦点。
- [x] 专题详情：来源在右栏"来源" Tab，勾选即生效（toast"专题来源已更新"）。
- [x] 设置首页 4 行显示当前值；子页无卡片嵌套。
- [x] `⌘K` 输入 `>导入` 出现导入命令。

### Phase 6 — 清理与文档

| 文件 | 改动 |
|---|---|
| `static/app.css` | 删除 2.2 兼容层（保留 `--color-canvas: var(--bg);` 一行以满足 `test_web.py:475`，或同步改测试为断言 `--bg:`）；删除未被引用的旧选择器（如 `.page-heading`、`.article-breadcrumb`、`.density-button`、旧导入卡片与动画）；保留仍使用的 `.import-choice` 导入菜单和 `.topic-selection-indicator` 复选框，仅删除其过时装饰；确认只剩 2.2 中的值：`grep -oE "#[0-9A-Fa-f]{3,8}\b" app.css` 结果只出现在令牌块与 toast 规则 |
| `docs/web-ui-design-system.md` | 重写为新设计系统现状（令牌表、组件表、快捷键表、测试契约）；删除 Apple 风格、瓷贴、毛玻璃、紫色渐变章节 |
| `static/brand-mark.png` | 不删除（未引用即可）；如需删除另行确认 |
| `CHANGELOG.md` | 记录 Web UI 重设计 |

验收：
- [x] 清理旧规则、兼容变量与重复声明：Phase 5 的 109,731 字节降至 91,874 字节，减少 16.3%。早期基线约 91 KB，“< 60 KB”为估计目标，当前未达到，保留为后续体积优化项。
- [x] 字号审计：`grep -oE "font-size: *[^;]+" app.css | sort -u` 只出现 7 个 `--fs-*` + `--fs-reading` + `.875em`（行内 code）。
- [x] 间距审计：`grep -oE "var\(--space-[0-9-]+\)" app.css | sort -u` 只出现 1/2/3/4/6/8/12/16。
- [x] 圆角审计：仅 `--radius`、`--radius-sm`、`50%`、平面容器的 `0` 与继承值 `inherit`；当前没有 switch 胶囊圆角。
- [x] 18 个路由完成亮 / 暗主题截图和三种宽度审计，`--text-2` on `--bg` 为 5.065 / 6.943:1，达到 4.5:1。这是 Phase 6 当时的结果；review 后 `--text-3` 升级为达标的辅助正文色，当前数值见 2.2 与设计系统。

### 6.3 测试契约清单（Codex 必须逐条核对）

| 测试位置 | 断言 | 处理 |
|---|---|---|
| `test_web.py:422` | `<main class="library-main" id="main-content"` | 保持 |
| `test_web.py:423-424` | `aria-label="资料库导航"`、`aria-label="抖库 AI 对话"` | 保持（后者在 `#chat-panel` tabpanel 上） |
| `test_web.py:425-430` | `data-library-view="list"`/`"gallery"`、`data-section="favorite"`、`aria-label="专辑墙视图"`、无"画廊视图" | 保持 |
| `test_web.py:431-461` | `data-theme-select`、`id="chat-close"`、`id="applied-filters"`、`id="command-dialog"`、trash 系列 id、`id="destructive-dialog"`、`id="imports-toggle"`、`id="settings-nav"`、无 `auth-nav`、无"已保存视图/全部视频/全部图文"、含"导入单条/导入博主/导入抖音收藏/库内收藏"、`id="library-todo"`、`id="settings-view"`、`value="in_progress"`、`id="creator-focus-bar"`、`id="imports-single-view"`、`id="single-share"`、无 `capture-dialog`、含 `/static/icons.svg#`、**无 `cdn.`** | 全部保持；来源 Tab 文案用"全部 / 视频 / 图文"，不要写成"全部视频" |
| `test_web.py:475-487` | CSS：`--color-canvas`、`data-theme="dark"`、`.gallery-cover`、`aspect-ratio: 3 / 4`、`.article-cover`、`--font-reading:`、`.article-body { max-inline-size: 40rem`、`::view-transition-group(active-album-cover)`、`view-transition-name: app-sidebar`、`.message.is-new`；且无 `.editorial-cover` 等 | 正文改为 `max-inline-size: var(--measure)`，兼容名称 `--color-canvas` 在 Phase 6 改为断言 `--bg:`；其余保持 |
| `test_web.py:490-515` | JS 字符串：`uiVersion: "douyin-wiki.ui-version"`、`const UI_PREFERENCE_VERSION = "2"`、`makeCover(item, "gallery-cover")`、`makeCover(item, "article-cover")`、`hero.querySelector(".article-cover")`、`animatedEntryIds`、"删除文章"、"彻底删除"、`最近一次用量：${latestAssistant.total_tokens} token`、`isDatabaseManaged` 相关 6 条、"只读 Markdown，发送时使用全库对话"、"只读 Markdown 文章不能保存灵感"、`currentArticleItem` 3 条、`topicsCreateButton` 2 条 | `UI_PREFERENCE_VERSION` 改 `"3"` 并同步测试；其余保持 |
| `test_web.py:527-534` | `.library-sidebar { position: relative; z-index: 0;`、`.library-main { position: relative; z-index: 1; min-width: 0;`、`.filter-popover { position: fixed; z-index: 50; top: var(--filter-popover-top`、`function positionFilterPopover()` | 保持字面量（规则开头顺序不能变） |
| `test_web.py:933-934` | `id="topics-nav"`、`id="topic-select-toggle"` | 保持 |
| `test_web.py:981-995` | `data-theme-select`、`id="app-shell"`、`id="model-view"`、`id="analysis-view"`、`aria-label="显示 API Key"`、`/static/icons.svg#eye`、`model-settings.js?v=`、"对话模型"、"共用模型"、`name="analysis-mode"`、"保存分析方式"、`href="/settings/analysis"`、`data-route="/settings/model"`；无 `settings-info-panel`/`settings-shell` | 保持（"对话模型"文字在 model-view 说明或 AI 面板"尚未配置对话模型"中保留一处） |
| `test_web_navigation.py:47-65` | `openEntryViaAction`、`hasChildWork`、`job-warnings`；`id="settings-nav"`、无 `system-nav`、`data-route="/settings/system"`、`class="sidebar-body"`、"从资料库选择来源"；CSS `.sidebar-body`、`overflow: hidden;`、`.sidebar-footer` | 保持 |
| `test_web_navigation.py:75-77` | `not-found-view`、"页面不存在" | 保持 |
| `test_web_operation.py:147,243-250,399-400,516-520,566-567` | 任务中心标题、`split("?")`、jobs.js 字符串、`shared.js`、无 `cdn.`、`data-route="/settings"`、`>设置</span>`、`aria-label="更新抖音收藏"`、"设置 › 授权状态" | 保持（`>设置</span>` 要求侧栏设置链接文字包在 `<span>` 里） |

### 6.4 给 Codex 的执行提示

1. 每个 Phase 一个分支/PR；先跑 `uv run pytest tests/test_web.py tests/test_web_navigation.py tests/test_web_operation.py` 建基线。
2. 改 `app.html` 时优先"移动"已有节点而不是重写，保留全部 `id` 与 `data-route`/`data-section`/`data-back` 属性。
3. 改 JS 时不改变 API 调用与数据流，只改 DOM 构建与样式类；新增状态字段集中加在 `app.js` 顶部 `state`。
4. CSS 新规则写在对应分段，旧规则**删除**而不是覆盖；短规则可单行，长规则拆行；媒体查询统一 range 语法，reduced-motion 只保留一个块，保留的 `!important` 写明用途。
5. 任何需要新颜色/字号/间距的地方，先回到 2.2 找现成 token；找不到就停下来问，不要临时写值。

---

## 7. 待确认与风险

1. **思源宋体子集体积**：2 个字重各约 2–3 MB 是估计值；若太大，可只打包 400 字重，600 用 `font-synthesis: weight` 或回落到 Songti SC 粗体。hatchling 默认会打包 `src/douyin_wiki` 下的字体文件，但要确认 `.gitignore` 没排除 `*.woff2`。
2. **时长字段缺失**：Readwise 行里的"阅读时长"在抖库对应"视频时长"，但 `LibraryItem` 没有 `duration_seconds`（`models.py` 里 capture/media 模型有）。本规格不展示时长；如需要，后端在 `_item_payload` 加 `duration_seconds`，前端元信息在类型后插入 `4:32`（可选，未排期）。
3. **callout 渲染**：后端已转换标记；4.2 已改为按实际 HTML 装饰。必须回归验证普通 blockquote 与 callout 均保留原文。
4. **范围选择的后端负载**：博主（`{decision, work_ids}`）与收藏（`{selected, work_ids}`）的 selection 接口都接受 `work_ids` 数组，已核实前端调用方式；一次提交几十个 id 时的服务端耗时未测。
5. **正文图片**：后端已移除封面，禁止新增按首段位置隐藏图片的 CSS。回归用例必须覆盖「封面 + 正文证据图」、只读 Markdown 和多图笔记。
6. **强调色**：选墨绿的理由见 2.1；若更想要中文"朱批"气质，可换朱红（2.2 已附变量），但危险色需改成更深的酒红（如 `#9F1D2A`）以示区分，且 accent 文字要用 `#C4323A`。
7. **Readwise 细节边界**：本规格引用的 Readwise 行为（三栏、Info/Notebook/Chat、`[` `]`、`⌘K`、`?`、↑/↓ 段落焦点、`Shift+N`、`Shift+F`、左栏目录）均来自官方文档；列表行的视觉细节（左侧焦点竖条、hover 操作图标、行底进度条、LATER/SHORTLIST/ARCHIVE 标签）来自官方宣传截图，不是文档。`j/k` 是本项目加的别名。
8. **`docs/screenshots/` 不存在**：任务说明里提到的截图目录在仓库里没有；现状截图只有 box 上的 `/workspace/doku/album-wall-desktop.jpg`、`article-desktop.jpg`。Phase 0 起新建 `docs/screenshots/redesign/`。
9. **截图与代码不一致**：现状截图里 AI 面板是常驻第三栏，但代码中 `.chat-panel` 是 `position: fixed` 覆盖层且默认收起（`STORAGE.chat` 默认 `"true"`）——截图应是手动打开覆盖层后的状态；新设计按常驻第三栏实现。

## 8. 审查修订记录（2026-09-30）

已修正：资料库栏头宽度预算、窄屏专题选择入口、正文首图误隐藏、右栏多 Tab 焦点范围、callout 实际后端契约、列表及代码块阅读导航。静态原型位于 `docs/prototypes/readwise/`，示例截图位于 `docs/screenshots/redesign/prototype/`；原型内容为演示数据，正式实现继续使用原有 API。

### 实施状态

- 基础批次（Phase 0）已落地：新令牌及兼容层、本地字体、去渐变/毛玻璃/卡片阴影/按压缩放、宋体正文和标题；提前统一线性图标，并修复 settings / 404 的 main 归属。
- Phase 1 已落地：240 / 自适应 / 360 三栏、各视图栏头、侧栏导入和搜索、右栏 Tab 容器与页面偏好、桌面覆盖层和窄屏抽屉、inert/焦点陷阱、全局快捷键；保留窄屏专题选择入口与贴底确认栏。Web 版本 0.2.32。
- Phase 2 已落地：列表默认迁移、88px 资料行 / 64px 手机行、3:4 缩略图、来源 Tabs、行内收藏 / 更多菜单、专辑墙网格导航、焦点同步信息面板、近 7 天与侧栏计数、真实多选框；跨筛选的全部已选资料会提交到专题。Web 版本 0.2.33。
- Phase 3 已落地：文章目录、章节时间码、阅读进度、嵌套段落导航、相邻文章、返回列表滚动和焦点恢复；文章操作移入栏头和菜单，信息面板提供来源复制与管理动作，灵感支持逐字添加、原句与时间范围跳转。保存灵感保留阅读状态，延迟响应不会覆盖已离开的页面。Web 版本 0.2.34。
- Phase 4 已落地：浅灰用户消息、无边框助手回答、上下文示例问题、流式光标、常显两行引用与墨绿编号、消息保存操作、中性模型配置通知及统一输入提示；发送时保留未发送草稿，失败与中断提示可见，切换历史时忽略过时响应。Web 版本 0.2.35。
- Phase 5 已落地：导入方式行、最近任务与粘贴预填；博主 / 收藏清单范围选择和已选底栏；任务进度与键盘操作；专题来源右栏及长成果折叠；设置当前值、平面分区、中性配置状态；废纸篓范围选择与命令面板扩展。Web 版本 0.2.36。
- Phase 6 已落地：删除兼容令牌、未引用旧选择器、旧动画和被覆盖声明；统一字号、间距引用与圆角；设计系统补全组件、快捷键和测试契约。102 组清理前后样式对照未改变布局或排版；18 个路由完成 108 组最终亮暗 / 响应式审计。Web 版本 0.2.37。CSS 91,874 字节，减少 16.3%，未达到早期 <60 KB 估算；这项继续作为可选体积优化。
- 静态原型已导出亮暗主题的资料库和阅读页截图；正式基础批次截图在 `docs/screenshots/redesign/phase-0/`，两者不得混作实际完成状态。
- 验证：61 项 Web 定向回归；444 项非 live 测试（1 项 live 未运行）；隔离浏览器校验本地字体、正文 17px / 29.75px / 612px、settings/404 可达与正文图片可见。Phase 1 验证另覆盖 1440 / 1280 / 1100 / 800 / 390px、路由栏头、Tab 与开合偏好、快捷键、抽屉焦点和窄屏专题流程；截图和记录在 `docs/screenshots/redesign/phase-1/`。

- Phase 2 浏览器验证覆盖列表偏好迁移、88 / 64px 行尺寸、焦点和信息同步、网格键盘导航、收藏与只读限制、来源 / 作者筛选、菜单焦点归还、跨筛选创建专题、删除到废纸篓、空库 / 无结果 / 错误重试 / 骨架、320–1440px 工具栏与手机专题流程；截图和记录在 `docs/screenshots/redesign/phase-2/`。

- Phase 3 隔离浏览器验证覆盖 320–1440px 阅读栏头、目录与标题偏移、17px / 29.75px / 612px 正文与 28px 标题、正文图片与 callout、嵌套列表 / 引用 / 表格 / 代码导航、相邻文章、滚动和焦点恢复、真实收藏与灵感保存、引用高亮淡出、只读限制、延迟响应取消、封面 view transition、窄屏目录和灵感抽屉；截图与记录在 `docs/screenshots/redesign/phase-3/`。Safari 18 未在本机运行验证；浏览器验证使用 Chromium。

- Phase 4 隔离浏览器验证使用临时 Vault、模拟模型与真实本地 HTTP/SSE，覆盖资料库 / 文章 / 专题 / 只读资料范围、示例仅填入输入框、流式光标与减少动效、用量、引用跳转高亮、保存按钮显隐、逐字保存灵感和专题笔记、发送时保留草稿、历史切换竞态、失败 / 流结束中断与重试、320–1440px 输入区、未配置模型到设置的跳转。亮暗主题、手机及状态截图和记录在 `docs/screenshots/redesign/phase-4/`；外部请求与页面脚本错误均为 0。

- Phase 5 隔离浏览器验证使用临时 Vault 与现有真实 API，覆盖 14 个操作路由、320 / 390 / 800 / 1100 / 1280 / 1440px、粘贴仅预填、清单 Shift 范围与键盘焦点、不可选作品和不完整清单保护、任务事件刷新焦点 / 进度 / 重试 / 打开详情、来源即时保存与手机抽屉、长成果折叠、设置实时值与中性徽章、废纸篓连选和恢复、文章搜索与 7 个命令。任务事件在浏览器内模拟，API 刷新与修改为真实请求；AI SSE 由 Phase 4 脚本单独回归。截图与验证记录在 `docs/screenshots/redesign/phase-5/`。

- Phase 6 验证：444 项非 live 测试、61 项 Web 定向回归、六组既有浏览器探针全部通过；最终令牌 / 字号 / 间距 / 圆角审计与 18 个路由亮暗截图位于 `docs/screenshots/redesign/phase-6/`。正文保持 17px / 29.75px / 612px，本地字体与正式 wheel 资源已核对；静态原型与正式页面产物分别保留。

- Web 0.2.38 修正实际文章的 callout：消除“AI 推断 · AI 推断”重复，标题与正文分段，仅隐藏空的“与灵感的关系”提示；不改写原 Markdown。

- Web 0.2.39 review 修正：主内容程序焦点不描边、辅助正文亮暗对比度达到 4.5:1；全局 14px / 20px 与待办 13px / accent 圆点 / 右侧链接落地；组件字重改令牌、字标与专题改 UI 字体，宋体仅文章加载；修正文章侧栏和专题栏头、清除原生搜索 ×、死代码与冗余动效规则。HTML 外壳回归、独立字体请求和 Enter 焦点探针补齐。CSS 分段整理，截图被 Git 忽略，原型复用应用字体。

- Web 0.2.39 最终 CSS：`app.css` 92,996 bytes，按需 `reading.css` 788 bytes；合计 93,784 bytes，未达到 60 KB 估算。保留可读分段与仍使用的组件 / 响应式样式；6 项 `!important` 的原因均写入注释。该版本完整回归为 457 项通过、1 项 live 排除，七组浏览器探针及原型捕获通过。

- Web 0.2.40 review 修正：960–1279px 左右栏同时收起时不再残留 240px 空白；桌面隐藏手机导航入口，恢复按钮仅在 ≥960px 且左栏收起时显示。外壳回归覆盖五种宽度的四种开合组合、断点两侧和手机抽屉往返，断言实际位置、宽度与按钮显隐。457 项非 live 测试、ruff、构建及外壳 / 资料库 / 阅读器探针通过。当前 `app.css` 92,890 bytes、`reading.css` 788 bytes，合计 93,678 bytes。收起布局的亮暗截图与记录在 `docs/screenshots/redesign/phase-1/`。
