# 共享采集后续审查修复

2026-10-10。用户通过“修复”授权处理上一轮复核的两个 P2 和一个 P3 问题。三项均已修复，完整非 live 回归1415项通过。当前 Agent 直接实现与复核，未委派；修复验收时尚未集成，未修改生产 Vault、Profile 或主线现用环境。后续合并与清理见[主线集成记录](#主线集成与清理2026-10-10)。

## 版本与范围

审查基准为 `09d426526767de96e164afe5c053c5dd5f29d20a`，运行代码提交为 `c046d21`、`ae2c643`、`7f8c844`。验收工作树为 `/Users/weisengao/.codex/worktrees/douyin-collection-import/douyin-wiki`，分支为 `codex/douyin-collection-import`。修复交付时主线仍为 `54ae500fd3abaffaefecb2af812ec6881b1e0e35`，工作区干净，安装的服务继续引用主线 `.venv/bin/python`。

沿用[既有设计](../specs/2026-10-10-douyin-collection-import-design.md)和已确认范围，[本轮计划](../plans/2026-10-10-collection-import-follow-up-fixes.md)记录具体实现。CLI/MCP 名称与参数、Service 调用、Web 路径与状态字段、schema15 和 Vault 格式保持兼容；新增 Web 动作 `view_shared_capture`。

## 修复结果

| 审查问题 | 修改与行为 |
| --- | --- |
| P2：网页等待记录缺少执行者入口，暂停被解释为处理所选作品 | `operation.py` 识别尚有效的共享等待记录，生成执行者链接，并按共享执行状态处理进行中和需要本人操作筛选。`web_operation.py` 读取实际执行者的阶段、进度、授权范围和暂停原因，保留等待者自身 ID、请求及 durable status。`jobs.js` 在列表和详情显示“查看执行任务” |
| P2：批次取消遗漏已改指关联的原等待记录 | `capture_sharing.py` 按原 `creator/favorites/collection_context.parent_job_id` 查找等待者；`service.py` 在现有条目操作锁内先取消这些记录，再执行原有执行者保护逻辑。合集、收藏和博主都撤销自己的等待，独立执行者和其他批次继续完成；被撤销者保持 cancelled，不再生成 completed 事件 |
| P3：等待者单独重试仍返回旧执行者字段 | `database.py` 的 `requeue_job()` 接受可选 result；`service.py` 在相同状态更新事务内清除 artifacts 共享关联和 result 两个共享字段，沿用原 updated_at 条件检查。queued 回执和 downloading 协议2查询均不再指向旧失败任务；普通重试保留结果及媒体检查点 |

取消、删除和终态记录不应用执行者的网页展示或动作。共享关系仍保留原 Gateway；失败等待者重试后也保留自己的会话。明确 JobStatus 的网页查询继续按任务本身状态筛选，共享状态只用于进行中与需要用户操作的行为筛选。

## 验证

新增15项行为回归：三个批次来源的取消测试、两项重试测试、六项 HTTP 测试和四项真实浏览器场景。使用[合集导入说明](../../collections-import.md)和[Gateway 指南](../../gateway-agents.md)记录新的操作行为。

| 检查 | 结果 |
| --- | --- |
| 批次取消 RED → GREEN | 合集、收藏、博主三个参数在旧代码均保留 monitoring；修复后共享与批次相关65项通过 |
| 重试 RED → GREEN | 共享回执新增用例失败，普通重试基线通过；修复后共享、Gateway、数据库和 Service 相关189项通过 |
| 网页 RED → GREEN | 四项 HTTP 断言及桌面浏览器均在旧代码失败；修复窄屏测试操作顺序后，两项窄屏也复现缺少暂停说明；修改后相关51项通过 |
| 浏览器画面检查 | 列表截图前等待按钮可点击并禁用截图动画，四项再验证通过；亮色1440px、暗色390px均可从列表和详情跳转执行任务、取消等待记录，无 pageerror 或横向溢出 |
| 上轮仓库外正式复现探针 | 5 passed / 1 deselected；三个问题对应的四个失败场景均通过，图文单次下载及两份灵感继续通过 |
| `uv run --no-sync ruff check src tests`、`git diff --check` | 通过 |
| `uv run --no-sync pytest -q -m 'not live'` | 1415 passed / 2 deselected，201.40秒；所有产品修改结束后执行一次完整检查 |
| wheel/sdist 与独立安装 | 99个 wheel 成员、94个运行文件与源码及独立安装逐一相同；sdist 同时核对全部运行文件。CLI collection 命令及52个 MCP 工具正常加载，包含8个合集工具 |
| 安装包合成接口检查 | 两种 Gateway 查询协议、公开 HTTP 的 Gateway/授权暂停展示、独立重试移除旧共享回执、取消已改指关联的批次等待者均通过 |

窄屏探针首次先缩小窗口、再操作隐藏侧栏的主题选择器，产生测试超时；改为先切换主题再缩小窗口。首次窄屏列表截图捕获了页面过渡中间帧，补充点击可用性等待和截图动画设置后重新验证、检查画面。两类问题均在测试操作中修正，未据此修改产品行为。

本轮没有重新访问真实账号或处理新增真实媒体。原真实样本范围继续见[最初验收](2026-10-10-douyin-collection-import-acceptance.md)，上轮修复记录保留为[历史记录](2026-10-10-collection-import-review-fixes.md)。已确认的三项问题均有回归，当前 Agent 复核了原批次归属、独立执行者保护、事务条件更新、原会话、取消终态和网页接续；没有独立人员或子 Agent 审查。

## 证据与集成状态

材料保存于 `/Users/weisengao/Documents/ChatGPT/douyin-wiki-validation/2026-10-10-sharing-followup-9oLRTc`：

- `task1-red.log`、`task2-red.log`、`task3-red.log`、`task3-narrow-red.log`：修复前必要失败；后两份区分首次窄屏测试操作错误与正确复现。
- `task1-green.log`、`task2-green.log`、`task3-green.log`、`task3-ui-final.log`、`probes.log`、`full.log`、`ruff.log`：相关与完整验证。
- `screenshots/`：稳定的亮暗、桌面和窄屏列表/详情；探索性过渡截图单独保留。
- `package_check.py`、`package-check.json`、`package-check.log`、`package-cli.log`、`package-install.log`、`build.log`、`dist/`：实际包成员、独立安装和合成接口检查。
- `progress.md`、`source-verification.json`：当前 Agent 执行记录、验证版本和文件哈希。

wheel SHA-256：`71831c8bad71cc4c5812a33b4c5de43736bc42baa501e10231cd34f6e4e31950`。

修复验收时保留开发分支，未合并、推送或部署。此前只读复核仍位于 `douyin-wiki-validation/2026-10-10-collection-fix-review-LF2qCq`；其中未列为审查问题的旧选择事件探针继续排除，本轮没有扩大修复范围。

## 主线集成与清理（2026-10-10）

用户随后明确要求“合并，然后删除分支”。本地 `main` 从 `54ae500fd3abaffaefecb2af812ec6881b1e0e35` 快进到 `40a41234343c18253220f393522adfc6fa04abdd`，包含合集导入及两轮审查修复，没有合并冲突。

合并前后逐一核对172个源码、测试及依赖元数据文件的 SHA-256，均与上述1415项完整检查的版本相同。合并后使用开发工作树的独立环境，显式从主仓库加载 Python 源码，执行合集适配器、分页、选择确认、生命周期、Vault、CLI/MCP/Web 接口及共享采集相关回归：136 passed，12.34秒；Ruff 检查通过。测试数据与缓存均在仓库外，未同步主线现用环境；未重复相同源码的完整回归。

已通过 Codex 工作树归档工具移除开发工作区，并删除本地分支 `codex/douyin-collection-import`；归档标识为 `/Users/weisengao/.codex/worktrees/douyin-collection-import/douyin-wiki`，可通过工作树恢复工具读取。Git 归档不包含的8张历史截图已复制到下列材料目录的 `preserved-worktree-screenshots/` 并逐一核对哈希。此前合集、审查与共享采集验收材料继续保留；`codex/visual-search-phase1` 工作树未修改。

本次范围为本地主线合并和开发分支清理，没有推送远端、重启服务或向生产 Vault 导入作品。已安装服务的配置仍引用主仓库 `.venv/bin/python`；服务实际运行版本和部署验收需另行检查，不能将此次合并解释为全部视频已入库。

合并验证、截图副本、归档及最终 Git 状态保存于 `/Users/weisengao/Documents/ChatGPT/douyin-wiki-validation/2026-10-10-collection-main-merge-mctwfcp0`。文档索引与合集使用说明同步更新为已集成状态，早期验收基准及失败记录保留。

## 本机服务重启与入口检查（2026-10-10）

用户随后要求“重启 Web 和 Worker，确认合集网页入口、MCP 工具正常”。运行源码为主线 `8d598ca26b0bc65486586ce2c53f119cac28d2e6`，本阶段未修改产品代码或同步依赖。重启前 Web 仍是旧进程，合集入口返回404；队列与执行任务为零。使用 `launchctl kickstart -k` 重启已安装的两个服务，Web PID 从720变为43554，Worker 从42785变为43556。两个新进程保持 running，系统健康接口显示新的 Worker 标识与持续更新的心跳。

`/imports`、`/imports/collections`、合集 JS、`/api/collections`、`/api/collection-imports` 及系统健康接口均返回200。网页版本为0.2.47，实际返回的合集 JS 与主线源码逐字节相同。内嵌浏览器实际显示合集链接表单，“开始清点合集”可用，控制台没有错误。只检查入口和读取接口，没有提交清点或确认导入；任务汇总数与重启前相同。

按 Codex 已注册的解释器、参数及环境在仓库外建立新的 MCP STDIO 连接，握手、ping、52个工具清单和合集参数检查通过，包含全部8个合集工具；`list_collections` 和 `list_collection_imports` 实际调用成功。当前聊天的连接仍缓存旧44个工具，不含新增合集工具；客户端需要重新连接 MCP 才能显示新增工具。本阶段未终止其他聊天的 MCP 进程。首次验证脚本的 SDK 字段名错误已按已安装库修正，失败与后续成功记录均保留。

配置与两个 LaunchAgent 文件的 SHA-256 不变；重启后新增 Web、Worker 日志没有异常。证据保存于 `/Users/weisengao/Documents/ChatGPT/douyin-wiki-validation/2026-10-10-service-restart-3fe3b124`：`REPORT.md`、`restart.json`、`verification.json`、`http-check.json`、`browser-check.json`、`mcp-tools.json`、`mcp-check.json`、`check_mcp.py`、MCP 实际返回和新增服务日志。本次没有执行真实账号清点或新增媒体入库。
