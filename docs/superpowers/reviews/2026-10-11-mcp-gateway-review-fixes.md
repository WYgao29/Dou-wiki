# MCP / Gateway 精简审查修复

日期：2026-10-11。基准提交：`eedab69d9b099bc28ae94c2103c185c86a008179`。用户以“sp 修复”确认修复整体复核中的两处问题；本轮复用已确认设计和复现材料，由当前 Agent 执行，没有子 Agent 或独立审查者。

结论：旧库拒绝前的 Vault 写入和收藏目录提示均已修复，修复 wheel 为包 `0.2.1` / Web `0.2.49`。源码行为、实际安装包、Web / Worker / MCP 启动及保留接口已验证。现用服务仍为包 `0.2.0` / Web `0.2.48`；按用户逐步确认要求，本步骤完成后再确认本机更新。

## 修复及原因

`service.py` 的 `initialize()` 与 `initialize_runtime()` 先调用 `Database.initialize()`，完成兼容检查后才取得 Vault 操作锁并执行目录初始化、文件迁移、删除恢复及 Git 操作。原先拒绝旧状态库的异常发生在 Vault 迁移之后，已经改写的文件无法恢复。数据库自身的写锁及取得锁后的第二次版本检查继续保留；Vault 操作锁仍先于文件锁取得，删除恢复及文档迁移的锁前提不变。

`tests/test_setup.py` 新增 9 个真实初始化用例，覆盖版本 0 的非空旧库、版本 15 和版本 17，分别执行默认 Git 初始化、无 Git 初始化和 runtime 初始化。拒绝后比较所有文件的完整字节及目录集合，包括 SQLite、用户 Markdown、`.gitignore`、管理文件和 Git 目录。用例在修复前全部失败，修复后全部通过。

`imports-favorites.js` 将已完成目录任务的空清单提示改为“目录已读取。请让 Agent 根据目录选择收藏夹并发起作品读取。”。既有离线浏览器用例现在检查 Agent 接续提示及新建控件缺席；普通作品搜索的空结果提示继续由既有用例覆盖。Web 版本升为 `0.2.49`，让带版本的资源 URL 加载修正后的脚本；包和 MCP instructions 的版本标识同步为 `0.2.1`。

配置、schema 16 与运行依赖沿用 0.2.0；`uv.lock` 只改变项目自身版本。README 的 wheel 安装示例、架构中的初始化顺序、界面版本及 CHANGELOG 已同步，既有发行与归档记录保留。

## 实际验证

| 检查 | 结果 |
| --- | --- |
| 修改前相关基准：Database、Setup、收藏浏览器 | 66 passed，16.79 秒 |
| 旧库保护 RED → GREEN | 9 failed → 9 passed；覆盖两个 Service 入口及 Git 开关 |
| 收藏目录浏览器 RED → GREEN | 1 failed / 1 passed → 2 passed |
| 完整非 live 源码回归 | 初次 1511 passed / 1 failed / 2 deselected，236.56 秒；唯一失败为旧安装 metadata 0.2.0 与源码 0.2.1 不一致 |
| 版本检查修正后的复验 | 新独立环境安装实际 0.2.1 wheel，源码的原失败用例 1 passed；没有改测试断言或产品实现 |
| 实际 wheel 的正式回归 | 141 passed，6.75 秒；包含旧库保护、空库初始化、数据库并发、CLI / MCP 接口和版本一致性 |
| Ruff、JS 语法、锁文件和 diff | `ruff check --no-cache src tests`、`node --check imports-favorites.js`、`uv lock --offline --check` 和 `git diff --check` 均通过 |
| 收藏目录视觉验证 | 1280px 和 390px 均显示正确接续提示，没有浏览器异常；已检查两张截图 |
| 独立 wheel 启动与资源 | 包 0.2.1 / Web 0.2.49，非 editable，来自独立 site-packages；schema 16、空队列、Web、16 组停用接口和全部静态资源通过 |
| 实际 STDIO MCP | 51 个工具名称与 inputSchema 逐项等于 0.2.0 基准；instructions 为 0.2.1，没有业务调用 |
| 运行依赖比较 | 与已验证的 0.2.0 runtime 导出逐项一致，现用环境未同步或替换 |

原完整回归的环境失败日志保留。版本检查在新安装环境单独复验，运行代码和测试输入的 SHA256 未变；按项目的相称验证规则，没有重复执行其余 1511 个已通过用例。安装回归只通过 PYTHONPATH 加入测试根目录及外部审计脚本，没有加入 `src`，受记录进程的包来源均为新环境 site-packages。

视觉验证使用合成目录记录并拦截所有请求。安装验证阻止外部网络、旧对话模块、Keychain 和下载命令；没有真实账号采集、媒体处理或模型推理。隔离模型缓存为空时，doctor 如实报告缺少模型文件，不据此声明生产采集能力。

当前 Agent 已检查调用顺序、锁前提、受支持空库、旧库文件保持、目录接续、包资源及保留接口，没有剩余已确认的实现问题。

## 证据与交付状态

材料位于仓库外：`/Users/weisengao/Documents/ChatGPT/maintenance/douyin-wiki/mcp-gateway-simplification-2026-10-10/review-fixes-2026-10-11/`。原整体复核仍在相邻 `overall-review-2026-10-11/`，未覆盖。

- `baseline.log`、`p2-red.log`、`p2-green.log`、`p3-red.log`、`p3-green.log`：基准和两处问题的真实 RED / GREEN 证据。
- `full-regression.log`、`metadata-green.log`、`installed-tests.log`：完整初次回归、环境修正复验和实际安装包回归。
- `directory-ui-check.py`、`directory-ui-check.json`、`directory-desktop.png`、`directory-mobile.png`：离线浏览器操作及截图。
- `release-check.py`、`wheel-check.json`、`wheel-mcp-tools.json`、`wheel-audit/`、`installed-tests-audit/`：实际 wheel、资源及 MCP 契约检查。
- `requirements-runtime.txt`、`requirements-check.txt`、`verified-inputs.json`、`repair-ledger.json`：锁定依赖、运行与测试输入哈希及执行记录。
- `production-before-update.json`：只读生产探针，HTTP 200、Web 0.2.48、Worker 运行且队列为零，安装包仍是 0.2.0。

修复 wheel 已构建并通过安装验证。源码包及最终本地 main 集成结果将在完成后追加。下一步是确认更新本机 runtime，使用现有配置及 schema 16 新库，重启 Worker、维护和 Web，再重新连接 MCP 并核对实际解释器和版本。
