# 2026-10-13 项目审查修复、复审与部署

用户授权修复[项目审查](2026-10-13-project-review.md)中的七项问题，并在修复后复审；无新增问题时提交和重启。七项修复及 Web 对齐修改已完成，最终复审未发现新增的待修复问题。包沿用 `0.2.1`，Web 为 `0.2.51`，schema、运行依赖和公开接口保持兼容。

## 修复

| 文件 | 结果与回归 |
| --- | --- |
| `webapp/static/jobs.js` | 按导航、地址、当前视图与请求顺序丢弃旧详情/列表响应和错误，操作完成后的刷新也检查当前任务；事件订阅只为有效页面建立 |
| `webapp/static/app.js` | 专题保存成功或失败后，仅刷新用户仍在查看的同一专题 |
| `webapp/app.py` | 图片策略允许 HTTPS CDN；保持脚本、样式和连接的本机来源限制，合集图片继续不发送 Referrer |
| `service_maintenance.py`、`service_trash.py`、`vault.py` | 合法删除后保留笔记正文、历史来源和关系；只有有效删除日志/身份凭据能证明历史缺失；彻底删除、恢复和重新采集仍可重建，损坏凭据和缺失当前来源继续拒绝 |
| `database.py` | 共用事务内 Gateway 事件写入，博主无子任务确认生成一次完成事件；取消替代旧事件，保留 result 和批次上下文；事件插入失败则状态和事件一起回滚 |
| `adapters/media.py` | 同步封面请求移入线程；静态、原始、动态封面按真实类型回退，缓存也按同样顺序处理，保留 WebP 扩展名 |
| `webapp/static/app.css`、`webapp/version.py` | 前一阶段的跨页面对齐修复随本轮交付，资源版本 `0.2.51` |

彻底删除后的可选身份凭据位于 `wiki/.data/deleted-entries/<entry_id>.json`，只含版本、资料/作品 ID、废纸篓 ID 与删除时间。写入并刷盘成功后才清除可恢复文件；旧废纸篓日志仍然兼容。历史关系只保存于文档数据，SQL 不建立缺失目标的关系；恢复后重新投影。

## 复审与验证

新增 59 项行为回归：页面切换 13 项、封面 14 项、删除历史 21 项、状态事件 11 项。关键用例先验证旧实现失败再修复。复审发现缓存独立封面可能被较低优先级的成功网络封面取代，新增三个失败用例后修正；再次复审未发现新增的待修复问题。

复审覆盖同任务并发刷新、旧 404/500、离开专题、当前任务的实际删除目标、清单过滤切换、真实 CSP 响应头、封面请求期间的心跳、缓存和来源类型、单个/全部来源删除、双向关系、永久删除、崩溃接续、损坏凭据、重新采集，以及事件幂等和事务回滚。

| 验证 | 结果 |
| --- | --- |
| Ruff、`git diff --check` | 通过 |
| 完整非 live 源码回归 | 1610 passed / 2 deselected，221.60 秒 |
| 59 项新增行为回归 | 随完整回归全部通过 |
| Web 相关独立检查 | 67 passed；页面切换最终 13 passed |
| shell 浏览器回归 | 通过；覆盖栏头、各断点开合、抽屉焦点及快捷键 |
| operations 浏览器回归 | 通过；14 个操作路由、320–1440px、真实本机 API 操作，无异常或外部请求 |
| 实际 wheel 安装测试 | 独立临时环境 212 passed，18.99 秒；七项修复、CLI/MCP 接口及视频详情 |
| wheel 文件核对 | 90 个运行文件逐字等于源码和安装目录；包内没有 SQLite、缓存或本机配置 |

wheel SHA256：`f1c71d748a39991b7e306dbcb5dfd1ade22f0a203c613671291abea99aa0fb80`。独立验收环境使用自己的 wheel 安装，依赖通过只读路径复用现用 runtime 和开发环境，没有修改这些环境的依赖；所有业务测试使用临时 Vault 和假适配器，不读取真实账号。

## 本机部署

修复提交为 `80f436fb4215e6d8c03a44c9cf5f29ea2c6c72ac`。部署前只读探针确认 Web `0.2.50`、Worker 正常、无运行和排队任务；随后按用户授权备份现用包与 SQLite，安装已验收的 wheel，并重新注册 Web、Worker 和维护 LaunchAgent。现用依赖未同步，只替换抖库包。

2026-10-13 18:48（北京时间），新 Web 返回 HTTP 200 / `0.2.51`，Worker 使用新的进程和心跳，队列为 0。维护服务按原定周日 03:00 计划等待执行，没有强制运行维护。90 个安装运行文件与源码 / wheel 逐字一致，24 个线上静态资源与安装文件一致；配置、三个 plist、Codex MCP 配置与所有依赖版本保持原值。部署及新 MCP 验收后，34 张表除 Worker 心跳记录外的完整行内容哈希不变。

两个旧 STDIO MCP 进程已退出。使用保存的客户端配置新建 MCP 连接，51 项工具与实际 `doctor` 调用通过，解释器为仓库外独立 runtime。当前聊天旧连接的调用仍返回 `Transport closed`，需要用户在客户端重连抖库；新建验收连接不表示当前聊天已重连。本记录明确保留这个客户端接续事项。

### 当前聊天重连验收（2026-10-14）

用户重启客户端后，本聊天实际 `doctor` RPC 成功且 `overall=true`，当前工具清单为 51 项；模型准备命令使用独立 runtime 的 `bin/hf`，新 STDIO MCP 进程也使用同一 runtime 的解释器。此前 `Transport closed` 是升级后旧会话的历史状态，当前聊天已经重连成功，没有剩余客户端接续事项。

本机 Web 健康接口继续返回 HTTP 200 / `0.2.51`，Worker 心跳正常，无运行和排队任务。90 个安装运行文件仍逐字等于已验收的 wheel 和源码，Git 工作区在检查前干净。Qwen3、OCR、向量模型和必需工具诊断可用；只调用一次只读 `doctor`，没有创建业务任务或执行真实采集。证据为仓库外同目录的 `current-chat-reconnect-2026-10-14.json` 和 `reconnect-runtime-wheel-check-2026-10-14.json`。修复、复审、提交、部署、服务重启及本聊天重连全部完成。

首次新 MCP 验收脚本使用了错误的结果属性名 `isError`，工具调用本身成功后因属性读取失败退出；修正为当前 MCP 客户端的 `is_error` 后全部验收通过，产品代码没有因此改变，首次日志保留。

部署证据包括 `runtime-before-fixes.zip`、`state-before-fixes.sqlite3`、`deployment-before.json`、`deployment.json`、`deployment-commands.log`、`runtime-wheel-check.json`、`live-acceptance.json`、`fresh-mcp-doctor.json`、`current-chat-mcp.json` 与 `final-state-check.json`。备份、代码和验收材料均在下述仓库外目录，没有修改真实业务内容或浏览器 Profile。

证据均在仓库外 `/Users/weisengao/Documents/ChatGPT/douyin-wiki-review-2026-10-13/`：`navigation-red.log`、`navigation-green.log`、`cache-red.log`、`repair-targeted.log`、`fixes-full-regression.log`、`installed-regression.log`、`wheel-check.json`、`fixes-browser/` 与 `release/`。初次审查材料继续保留。
