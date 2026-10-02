# 仓库文件与目录审计（2026-10-01）

状态：首批 N1–N4、O7、O8、审核修正及第二批通用自助接入框架已提交为 `7ccfba3`；O1–O6 与回退工具复审修复已验收，2026-10-02 按用户要求补记修复、同步发行包并提交 O1–O6，见下方收尾记录。真实客户端配置未执行。审计基准：`main` / `81ce1faf37be5bd5a96cd3049eabc1cf288f2559`。日期使用北京时间。

第一阶段只新增本报告、[实施计划](../superpowers/plans/2026-10-01-repository-cleanup.md)和[文件清单](2026-10-01-repository-inventory.json)，未修改原有文件。开始时已跟踪文件没有已暂存或未暂存改动，非忽略的未跟踪文件为 0。用户随后确认首批实施；本报告下文的目录、数量和旧包差异保留审计开始时快照，原 JSON 清单不改写。首批结果见下一节；审计及前两批当时未提交，用户本轮已要求并完成首个提交。未直接修改 Git 内部或重写历史。

## 结论与建议范围

- 主线已在 `f087e59` 做过整理，没有发现可直接删除的整套废弃源码模块。49 个 Python 模块中，除通过子模块导入而自动加载的两个包初始化文件外，其余模块都能从入口沿静态导入关系到达。可达不代表每个函数都被使用。
- 必要清理是发行包边界与文档准确性：旧 wheel 的 74 个包内文件中，33 个与当前源码不同；旧 sdist 实际包含本地 `.pi/prompts/抖库.md`。字体说明存在失效路径，已完成的重设计文档仍标为“实施中”。
- 忽略文件共 38,179 个，约 1.71 GiB；主要是仍被安装配置引用的 `.venv`，不应当作缓存删除。明确可再生的缓存与 Finder 元数据共 116 个、4,017,926 bytes，是否清理由用户选择，空间收益有限。
- 历史审查材料、截图比较记录、画面检索方案及兼容接口有实际用途。未知外部消费者、本地配置用途和证据保留期限均标为待确认，不纳入默认删除范围。
- 推荐保留 `src/douyin_wiki`、业务 mixin、原生 JS/CSS 和入口路径。共享聊天适配器、Web 版本常量及测试夹具的职责改进各自独立、按需实施。
- 用户补充计划让多个 Agent 仅通过 MCP 调用。建议首批另选维护规则入口 O7 与调用文档入口 O8；真实客户端配置单独作为 E1，不能把接入文档存在当作权限、并发归属或实际加载已经验收。

## 首批实施结果（2026-10-01）

用户确认 N1–N4 + O7 + O8，由当前 Agent 执行；O1–O6 和 E1 均未选择。实际改动为 8 个已有文档/打包配置、2 个新增规则入口、2 个发行包，以及本次审计和计划的执行状态。src/、tests/、uv.lock、依赖、版本、入口和运行资源保持；本地 Pi/Obsidian 配置、历史材料、缓存、现用 .venv 与系统服务没有清理或配置操作。

- 新增[根 AGENTS.md](../../AGENTS.md)与[MCP 调用方入口](../agents/README.md)，共用调用流程仍在[原 Gateway 指南](../gateway-agents.md)维护；补充工具角色、规则加载、源码边界和事件归属，没有建立空客户端目录或宣称已落实客户端权限。
- 修正字体说明的 Inter 路径和原型复用；标明重设计规格为已完成的设计及历史记录；补齐 8 个浏览器验证入口和模块职责，保留原历史数字及日期。
- `.gitignore` 共享忽略 `.pi/`；sdist 明确排除本地配置、依赖、验证产物及缓存。旧包先复制并校验，再替换对应 dist 文件；版本仍为 0.1.5，未发布。
- 实际通过：既有文档测试 1 项、文档链接及锚点检查、4 个字体 SHA、6500 个唯一字符及许可、8 个脚本路径/查询工具核对；离线构建，79 个 wheel 成员和 157 个 sdist 文件检查，74 个包内文件与源码逐字节匹配；独立 Python 3.12.13 环境安装、douyin-wiki --help 和 python -m douyin_wiki --help 均退出 0，没有初始化真实或临时 Vault。

包成员检查同时核对 sdist 的源码和文档与工作区相同。收尾构建包含最终实施记录；发行包最终 SHA、差异补丁、原文件和旧包备份、CLI 日志及回退清单已于 2026-10-02 整组移入[仓库外首批归档](</Users/weisengao/Library/Application Support/douyin-wiki/integration-maintenance/2026-10-01-o1-o6-etwtkq2_/local-remnants-cleanup-20261002-p2el98s2/README.md#首批资料定位>)。回退前先核对交付后哈希，发现后来修改则停止，不能覆盖用户后续工作；旧脚本的目录依赖及本次材料恢复入口见归档说明。原文件清单仍用于复现审计基准。

本批没有业务源码改动，未扩大完整功能测试、截图脚本、真实账号授权或 MCP 客户端验收；不能把上述检查解释为新一轮功能全量回归或生产状态探针。多客户端规则尚需在实际接入时核对加载和权限。

## 首批审核修正（2026-10-01）

当前 Agent 复审发现一项 P2：`gateway monitor-events` 固定返回最早的 100 条未确认事件，原接入伪代码跳过其他客户端事件后不翻页；前 100 条属于离线客户端时，自己的后续事件会一直不可见。已修正 Gateway 指南，使用现有 `jobs events` / `list_job_events` 每轮从零逐页扫描，按整页末尾推进游标，处理失败继续扫描，下一轮重新查找未确认事件。兼容监控命令保留为单页探测；接口及业务源码不变。

使用实际 CLI 和数据库方法在内存中验证：前 100 条属于 Agent A 时仍读到 Agent B 的第 101、102 条；第 101 条交付失败不阻塞第 102 条，退避期间跳过重试，重新从零扫描后可重试第 101 条；Agent A 的 100 条事件保持未确认，CLI 空页 `[]` 被正确识别。既有文档测试 1 项通过；未访问真实 Vault 或配置客户端。修复前副本与本轮验证保存在上述执行目录的 `event-pagination-fix/`，原审计 JSON 清单保持。

## 通用自助接入框架（2026-10-01）

用户澄清目标为：其他 Agent 读取项目文档后自行配置，且不在代码仓库生成额外文件。已将 E1 从逐个客户端代配调整为通用文档框架：[接入入口](../agents/README.md)、[自助步骤](../agents/self-setup.md)和[参数／验收契约](../agents/integration-contract.md)。共用业务规则仍只在 Gateway 指南维护；不建立仓库内 clients、实例注册表、安装脚本或客户端适配代码。

接入参数使用已解析的路径与动态实例身份，客户端自行检测原生 schema 和加载能力。生效配置、工作目录、规则快照、日志、重试状态、测试 Vault 和验收结果位于仓库外；默认查询角色，实际权限及后台能力按实例验收。使用已有 Python、外部 cwd、显式配置和禁用字节码的启动方式；根 README 与 Gateway 指南中的固定客户端及本机启动示例已替换。

通用验证已通过：两份 JSON 和一份 TOML 模板解析，当前 MCP SDK 2.2.0 在仓库外带空格目录完成两次启动／重连、initialize、44 个工具列举和隔离空查询；查询角色的 8 个工具存在。候选包含两份新增规范，wheel 79 个成员、sdist 159 个文件，74 个运行文件保持，wheel 差异仅 README 元数据和 RECORD。既有文档测试通过，现用环境及 Git 状态保持。本批未配置真实客户端，不将通用协议检查解释为其规则加载、权限过滤、采集或原会话投递已经验收。

本轮验证及修复前副本保存在当前维护机器仓库外的 `integration-maintenance/` 独立目录；最终绝对路径和包 SHA 由交付记录提供，不是通用框架的固定安装位置。原审计 JSON 快照及首批回退材料保持，第二批回退只处理本批文档和发行包，核对 SHA 后还原，保留已有未提交内容。

### 第二批复审修正：目标切换（2026-10-01）

复审发现一项 P2：自助步骤只要求切换配置，未明确现有 MCP 进程的配置缓存和目标复核。`mcp_server._service()` 在首次业务调用后保留 Service，修改 TOML 不会切换已加载的 Vault；客户端修改环境也不会自动更新现有子进程。

已补齐自助步骤的目标切换、重入和回退顺序：暂停本实例监控与派发，等待在途调用结束并确认旧 STDIO 进程退出，以目标配置和 Profile 环境启动新进程，核对实际目标及受影响能力后恢复监控。契约增加连接重建、目标绑定和适用的监控验收记录；隔离验收结果不能代替目标切换结果。采集角色核对 Worker 的 Vault / Profile，不操作系统现有 Worker 或其他客户端。

在仓库外带空格目录，用两个隔离 Vault 的不同测试 job 验证了完整过程：更新配置文件后旧连接仍读到原 job；旧进程退出后，新进程只读到目标 job，收到目标 Profile 环境，CLI 使用同一目标配置；恢复原配置并再次重建连接后可读回原 job。三个 STDIO 进程均退出 0，44 个工具列举和查询通过，既有文档测试 1 项通过。没有访问真实 Vault、调用媒体或模型、修改客户端原生配置；这些检查不证明具体客户端的规则、权限和后台投递已验收。

本次只更新两份接入规范、本报告和实施计划；源码发行包随最终文档重建，wheel 内容不变并复用。修复前副本、探针和独立回退清单保存在仓库外的本次修复目录，绝对位置与最终包 SHA 由交付记录提供；原清单及前两批恢复材料保留。

## O1–O6 实施结果（2026-10-01）

用户在首批及 E1 完成后明确要求“提交 commit，然后修复 O1–O6”。已先提交 `7ccfba33c9974a9374db8079f6e2768c31ceae24`，再由当前 Agent 在隔离 worktree 和外部验证环境整理，验证后将局部改动交付原仓库。此节为新结果；下文审计数量、引用位置和“待确认”均保留原快照语义，不用后续状态改写原 JSON。

缓存/Finder 116 个（4,017,926 bytes）精确备份清理；56 个质量材料与 140 个截图/比较记录整组归档（196 个、27,381,016 bytes），逐文件 SHA 和原相对目录试恢复通过后移除原件。round2/3/4 所需旧源码、补丁、清单及固定历史提交可读；未宣称历史探针针对当前代码重跑通过。

运行源码新增 `adapters/chat.py` 与 `webapp/version.py`；Service 聊天传输不再反向依赖 Web，操作服务不再为版本加载 app。包入口延迟导出原 create_app/run_web，读取版本不触发 app 初始化，独立进程回归先失败再修正通过。旧聊天导入仍为同一对象，版本 0.2.43、传输/上下文 AST、公开方法、CLI/MCP、Web 负载及资源保持，仅删除已复查无消费者的私有 `_chunk_segments`。五个 Web helper 原样进入 `tests/support/web.py`，更新全部正式调用者和八个脚本，保留 tests/__init__.py。

新验证：隔离环境定向基线 171 项通过；最终完整离线回归 668 通过、1 项 live 未运行；原 667 个非 live nodeid 和既有正式测试函数断言保持，只新增一项 O4 独立进程回归；Ruff src/tests/scripts 与最终源码上的八个浏览器脚本通过。初始 dev 环境仅一项缺少 embeddings 可选依赖，补齐在外部环境并记录，不跳过该用例。五个历史浏览器脚本的当前文案、移动选择器、错误状态、会话范围、确认控件和模拟网络边界已按实际契约修正，断言保留或增加；产品 UI 逻辑未变。

最终发行包包含新模块、支持文件和本轮文档，源码/文档逐字节一致、运行资源/许可与入口保持，独立安装 CLI help 通过；旧包先备份、版本保持 0.1.5、未发布。既有环境/安装配置、先前恢复记录、本地设置、品牌与公开方法、其他 worktree 保留。真实账号/数据和客户端未验收，不能将上述回归解释为生产状态或具体客户端权限验证。

本机长期归档与本轮验证/回退材料位于 `/Users/weisengao/Library/Application Support/douyin-wiki/integration-maintenance/2026-10-01-o1-o6-etwtkq2_`；这是维护记录位置，不是其他机器的安装或运行前提。`archive-manifest.json` 保存归档与全部原文件 SHA、原相对路径、mtime 和 mode；`restore_archive.py` 默认只核对，`--apply` 才恢复，遇到后来内容冲突即停止。

逐项验证、回退和浏览器脚本修正依据见[实施计划的本轮结果](../superpowers/plans/2026-10-01-repository-cleanup.md#o1o6-实施与验收2026-10-01)。当前 Agent 已自审；2026-10-01 首次交付时尚未追加 commit，用户原索引保持。后续提交另记于下方。

## 回退工具复审修复（2026-10-01）

仓库外两份恢复工具已修复父目录符号链接越界及 Python 优化模式跳过 `assert` 保护的两项 P2。目标限制在所选根目录内，拒绝父/叶符号链接及绝对/上行路径，写入前再次检查；Git、文件/备份 SHA、归档成员/内容和冲突检查改用显式判断和异常。默认只核对、`--apply` 与成功 JSON 保持，工具和回归没有进入项目代码区。

真实恢复回归从 39 失败、27 通过变为 66 通过，覆盖普通、`PYTHONOPTIMIZE=1` 和 `-O`；原项目 6 次默认只读核对通过，312 个真实归档文件在仓库外试恢复，SHA/mode/mtime 符合清单。原项目未执行恢复，源码、文档、包、环境、安装配置及 Git 状态保持。证据与修复前副本见[计划中的复审修复记录](../superpowers/plans/2026-10-01-repository-cleanup.md#回退工具复审修复2026-10-01)。

## 提交收尾（2026-10-02）

本次按用户明确顺序补记修复、同步发行包并提交 O1–O6。新改动仅为本报告、计划和索引；原 O1–O6 源码/脚本/测试 SHA 与已验收交付一致，复用 668 项离线回归及八个浏览器入口的历史证据。本次检查文档、Ruff 和包成员；sdist 纳入最终文档，wheel 的源码与 README 元数据不变且逐字节匹配，版本保持 0.1.5。34 个项目文件纳入提交，忽略的发行包和仓库外恢复材料不纳入 Git。

提交标识、最终包 SHA、本次副本与提交后的回退边界见[计划中的收尾记录](../superpowers/plans/2026-10-01-repository-cleanup.md#提交收尾2026-10-02)。跟踪文件回退使用本轮提交的 `git revert`，发行包另按回退后的源码验收；锁定提交前状态的旧回退脚本保持原清单并拒绝不匹配状态。历史归档可单独恢复，原审计 JSON、既有环境、安装配置、其他 worktree 和本地设置继续保留。

## 本地残留清理（2026-10-02）

用户确认清理目标复核列出的四项。已外部备份后移除停用 Pi 的旧提示 1 个、源码字节码 39 个，将首批回退与验证材料 52 个整组外置，并移除项目内空 `.worktrees/`；共移出 92 个文件、17,129,699 bytes。两组归档在仓库外逐文件试恢复，SHA、mode 和 mtime_ns 匹配，移除阶段其余 169 个项目文件、42,371 项现用环境元数据和 3 个安装配置保持。

本批仅更新本报告、实施计划和索引中的现行材料位置及清理记录，源码发行包随最终文档同步；wheel、版本、业务源码、接口和测试保持。文档与包验收结果、前后清单及回退入口见[本次仓库外执行记录](</Users/weisengao/Library/Application Support/douyin-wiki/integration-maintenance/2026-10-01-o1-o6-etwtkq2_/local-remnants-cleanup-20261002-p2el98s2/README.md>)。原审计 JSON 和历史材料保持，不把上文的历史保留状态解释为当前目录仍存在。

Codex 和 Hermes 由用户重配，其他 Agent 客户端停用；本次未调整客户端或服务。缓存已清理，持续无源码字节码仍需按接入规范配置启动参数。首批旧回退脚本有目录推导及历史状态依赖，保留原件作追溯，本次材料恢复使用外部记录中的已验证入口。既有环境、Obsidian 设置、其他 worktree 与归档保持；本批维护文档已随本次 `main` 收尾提交，未推送或发布。

## 规则与权威来源

审计开始时仓库内没有项目级 `AGENTS.md` 或 `CLAUDE.md`。本次读取全局权威规则 `/Users/weisengao/.codex/AGENTS.md`，与用户提供的规则一致。首批已新增薄的项目 `AGENTS.md`，链接权威记录并补充项目边界，不复制全局规则；下文目录树仍记录实施前结构。

| 记录 | 职责及核对结果 |
| --- | --- |
| [README](../../README.md)、[文档索引](../README.md) | 安装、入口、验证和文件维护规范 |
| [architecture.md](../architecture.md) | Service、Worker、数据边界；与源码大体一致，模块表可补齐标签和时间工具 |
| [service-collaboration-api.md](../service-collaboration-api.md) | mixin 跨模块调用、操作锁、发布与租约约束 |
| [gateway-agents.md](../gateway-agents.md)、[favorites-import.md](../favorites-import.md) | Agent/MCP 顺序和真实账号验收边界 |
| [web-ui-design-system.md](../web-ui-design-system.md) | 当前 UI 约定及明确标注的历史验证记录 |
| [web-ui-redesign-readwise.md](../web-ui-redesign-readwise.md) | 早期规格与实施记录；开头状态及“取代现状文档”的表述落后于完成状态 |
| [visual-search-indexed-plan.md](../design/visual-search-indexed-plan.md) | 未集成主线的方案，对应分支和 worktree 均存在，保留 |
| [CHANGELOG](../../CHANGELOG.md) | 已落地修复和历史测试结果，不能作为本次执行结果 |

`vault.py:235` 会向用户 Vault 写入 `AGENTS.md`；这是产品生成的知识库指令，不能混作当前源码仓库的项目规则。

## 两轮讨论补充：维护与调用的执行边界

本节保留两轮讨论时的方案判断；用户随后确认了 N1–N4、O7、O8，实际结果见“首批实施结果”。再后续用户明确第二批为通用自助接入框架，不按客户端逐个代配；当前规范见 [MCP 自助接入入口](../agents/README.md)与计划 E1。下文曾拟议的仓库内 clients 示例不是当前接入方式。

使用大写复数 `AGENTS.md` 作仓库维护入口，写项目职责、验证入口和权威链接；全局规则仍维持原来的唯一来源。

调用方共用流程已有 docs/gateway-agents.md。推荐新增 docs/agents/README.md 作接入索引，补充既有 Gateway 指南中的工具角色、源码维护边界、规则加载检查和任务归属；保留指南旧路径，不为各 Agent 复制整套业务规则。首个实际客户端示例经验证后再建立 docs/agents/clients/，客户端生效配置、工作目录和凭据放在仓库外。

| 规则层 | 对象 | 权威记录与拟议入口 |
| --- | --- | --- |
| 仓库维护 | 当前维护 Agent/开发者 | 拟新增根 AGENTS.md，链接全局规则、README、architecture、service-collaboration-api |
| MCP 调用 | 外部查询或采集/分析 Agent | 现有 gateway-agents.md 继续承载共用流程；拟新增 docs/agents/README.md 与经验证的客户端差异 |
| 知识库内容 | 用户 Vault 中的文档 | vault.py 的 VAULT_AGENTS 模板及生成的 Vault AGENTS.md；本次不改模板或用户文件 |

本轮核对了现有实现：mcp_server.py:28、81 将 INSTRUCTIONS 配置给 STDIO 服务，但不能据此证明各客户端都接收或遵循；mcp_server.py:262 的 list_job_events 没有 gateway/客户端筛选参数；database.py:1339 的查询只按游标、未确认/过时状态和数量取事件，:1379 的确认只使用 event_id。gateway_context 携带回传路由，当前证据不能证明它提供身份鉴权或排他领取。

因此，目录只划分职责；实际接入时需核对客户端能力和源码写权限。规则上分配各自 gateway/会话任务，处理前读最新阶段、成功后确认，不能确认其他客户端事件；无法划分任务归属时先限定一个采集/分析执行者，其余作为查询入口。需要服务端强制隔离或同任务抢占并发时另立方案，不能在“只做文档/接入”阶段改接口。

推荐首批 **N1–N4 + O7 + O8**：基准核对 → 规则及文档 → 发行边界 → 最终包验收。不改 src/、tests/，不删除缓存/历史材料，不配置真实客户端。E1 接入需明确实际客户端名称、版本、用途和数据权限，先隔离验收再切换真实 Vault；O1–O6 仍分别选择。具体文件、验收和回退见[合并实施计划](../superpowers/plans/2026-10-01-repository-cleanup.md)。

## 当前目录职责

```text
douyin-wiki/
├── pyproject.toml / uv.lock / .python-version / .gitignore
├── README.md / CHANGELOG.md / LICENSE
├── src/douyin_wiki/
│   ├── cli.py / __main__.py / mcp_server.py      # CLI/MCP 入口
│   ├── service.py + service_*.py               # 门面与六个业务 mixin
│   ├── database.py / vault.py / search.py      # 状态、文档与检索
│   ├── favorites*.py / tags.py                # 收藏与标签领域
│   ├── models.py / errors.py                  # 数据和错误契约
│   ├── config.py / setup.py / secrets.py      # 配置、安装、密钥访问
│   ├── worker.py / runtime.py                 # 调度、租约与更新检测
│   ├── review.py / auth_guidance.py / web_auth.py
│   ├── operation.py / web_operation.py        # 展示与 Web 操作门面
│   ├── localization.py / time_utils.py
│   ├── adapters/                             # 抖音、媒体、模型、向量、提醒
│   ├── resources/vision_ocr.swift             # 动态加载的 macOS OCR
│   └── webapp/
│       ├── app.py / catalog.py / chat.py / rendering.py
│       ├── operation_api.py / tag_api.py
│       ├── templates/app.html                # Jinja2 单模板 SPA
│       └── static/                           # JS/CSS、图标、字体与许可
├── tests/                                    # 正式回归、夹具
├── scripts/verify_readwise_*.py               # 8 个浏览器验证入口
├── scripts/fonts/                            # 字表、许可、哈希、生成说明
├── docs/                                     # 当前使用文档、规格、方案与原型
├── dist/                                     # 忽略：旧发行包及生成元数据
├── docs/screenshots/                         # 忽略：截图和历史验证证据
├── .test-quality-baseline-20261001/           # 忽略：复现、补丁、旧代码与记录
├── .venv/                                    # 忽略：运行/开发依赖
├── .pytest_cache/ / .ruff_cache/ / __pycache__/ # 忽略：缓存
├── .obsidian/ / .pi/                          # 忽略：本地用户配置
└── .worktrees/                               # 忽略：当前为空
```

`src` 的 74 个跟踪文件包括：49 个 Python、12 个 JS、2 个 CSS、1 个模板、1 个 Swift、4 个字体、3 个字体许可、1 个 SVG、1 个 PNG。四个运行字体共 4,316,692 bytes，约占全部跟踪文件字节数的 57%；体积大不构成删除依据。`tests` 包含 42 个 `test_*.py`、`conftest.py` 和 `__init__.py`。

用户 Vault、SQLite、媒体、浏览器 Profile、密钥和本机配置由应用配置管理，不属于仓库清理范围。仓库中未发现 `.douyin-wiki` 状态目录、用户媒体或 SQLite 文件；`.obsidian` 是本地编辑器配置，不能据此把源码目录当作用户数据 Vault 处理。

## 构建、测试、运行、部署和脚本入口

| 类型 | 实际入口 | 依赖和边界 |
| --- | --- | --- |
| 依赖与版本 | `pyproject.toml`、`uv.lock`、`.python-version` | Python `>=3.12,<3.14`，选定 3.12；FastAPI、Typer、MCP、Jinja2、Pydantic、SQLite、Playwright；无前端构建链 |
| 构建 | `uv build` → Hatchling，wheel 包路径 `src/douyin_wiki` | 输出 `dist/`；模板、字体、静态文件与 Swift 必须随包提供；sdist 无专门排除配置 |
| 离线回归 | `uv run pytest -q -m 'not live'` | `testpaths=["tests"]`；包含隔离 Chromium 回归，部分用例写截图 |
| 静态检查 | `uv run ruff check src tests`；`node --check <file.js>` | 本次 Ruff 使用 `--no-cache` 并覆盖脚本与捕获代码 |
| 真实授权冒烟 | `DOUYIN_WIKI_RUN_LIVE=1 uv run pytest -q -m live` | `test_live_smoke.py:13` 的 marker/环境变量双重门槛，本次未执行 |
| CLI | `douyin-wiki = douyin_wiki.cli:main` | `python -m douyin_wiki` 同一入口；60 个命令函数，含隐藏迁移命令 |
| MCP | `douyin-wiki-mcp = douyin_wiki.mcp_server:main` | STDIO，44 个注册工具；工具名、参数、状态码/label 是兼容契约 |
| Web | `douyin-wiki web run` → `webapp.app:run_web/create_app` | Uvicorn、回环地址；Jinja2 单模板；`StaticFiles` 整体暴露资源目录 |
| Worker | `douyin-wiki worker run --forever` / `--once` | Service/持久队列；`runtime.py` 扫描包内全部 `*.py` 判断更新 |
| macOS 部署 | `douyin-wiki service install`、`douyin-wiki web install` | `setup.py:575,655` 生成 LaunchAgent，绑定 `sys.executable`；维护每周日 03:00 |
| Gateway 接续 | `douyin-wiki gateway monitor-events` | 文档测试防止再引用已删除的 monitor 脚本 |
| 字体生成 | `scripts/fonts/README.md` 中 `pyftsubset` 命令 | 下载、裁剪、改内部名称；4 个输出哈希均匹配 |
| 静态原型 | 根目录 HTTP server；`uv run python docs/prototypes/readwise/capture.py` | 相对复用正式字体；输出 `docs/screenshots/redesign/prototype/` |

8 个脚本均保留，命令前缀为 `uv run python scripts/`，输出均位于 `docs/screenshots/redesign/`：

| 脚本 | 职责 | 输出目录 |
| --- | --- | --- |
| `verify_readwise_foundation.py` | 字体、令牌、基础页面 | `phase-0/` |
| `verify_readwise_shell.py` | 外壳、响应式、路由、焦点 | `phase-1/` |
| `verify_readwise_library.py` | 资料库与操作 | `phase-2/` |
| `verify_readwise_reader.py` | 阅读器、证据、灵感 | `phase-3/` |
| `verify_readwise_chat.py` | HTTP/SSE、聊天范围/状态 | `phase-4/` |
| `verify_readwise_operations.py` | 导入、任务、专题、设置 | `phase-5/` |
| `verify_readwise_cleanup.py` | UI 收尾覆盖与截图 | `phase-6/` |
| `verify_readwise_tags.py` | 标签、别名、侧栏、窄屏 | `tags/` |

本机三个已安装 LaunchAgent 配置均以当前仓库的 `.venv/bin/python -m douyin_wiki` 启动。本次只读配置，未调用 `launchctl` 或探测服务，不能据此宣称运行正常。移动仓库根目录或删除 `.venv` 会破坏这些配置。

## Git 文件分类

统计为审计开始时快照，不含本次 3 个新增文件。字节数是普通文件逻辑大小，不含 `.git`、符号链接指向的外部文件或磁盘块开销；空目录不计入 Git 文件数。

| 类别 | 数量 | bytes | 判断 |
| --- | ---: | ---: | --- |
| 已跟踪 | 151 | 7,552,184 | 源码、配置、锁、测试、脚本、资源、文档；没有跟踪缓存、wheel 或截图 |
| 非忽略未跟踪 | 0 | 0 | 不能据此删除忽略文件 |
| 忽略 | 38,179 | 1,836,421,851 | 依赖、生成产物、缓存、历史证据、本地配置 |

| 忽略类别 | 数量 | bytes | 处置 |
| --- | ---: | ---: | --- |
| `.venv/` | 37,859 | 1,795,148,098 | 保留运行依赖，不逐个审计第三方实现的死代码 |
| `dist/` | 3 | 9,868,140 | 旧 wheel/sdist 和生成 `.gitignore`；备份验收后重建 |
| `docs/screenshots/` | 140 | 26,114,406 | 128 张 PNG、12 个记录；当前截图与历史比较证据并存 |
| `.test-quality-baseline-20261001/` | 56 | 1,266,610 | 探针、旧代码、补丁、XML、清单与审查报告；保留/整组归档 |
| `.pytest_cache/` | 5 | 79,102 | 可选清理 |
| `.ruff_cache/` | 12 | 10,662 | 可选清理 |
| 源码/测试字节码 | 95 | 3,903,570 | 可选清理 |
| 源码/测试 `.DS_Store` | 4 | 24,592 | 可选清理 |
| `.obsidian/` | 4 | 5,660 | 本地偏好，使用状态待确认，保留 |
| `.pi/prompts/抖库.md` | 1 | 1,011 | 本地提示，用途与准确性待确认，保留 |

除 `.pi/` 外，忽略来源可追溯到项目 `.gitignore`。`.pi/` 仅由 `git check-ignore -v` 报告的本地排除规则排除；规则不随克隆分发，现有 sdist 也证明它没有阻止打包。

另一个已注册 worktree 是 `~/.codex/worktrees/visual-search-phase1/douyin-wiki`，分支 `codex/visual-search-phase1`。本次没有进入或修改它，也未判断其未提交状态；不清理该分支、worktree 或设计文档。

## 疑似无用、重复和过时文件的核验

覆盖全部跟踪文件、320 个非虚拟环境忽略文件及依赖目录汇总。通过 AST 核对导入、装饰器和函数，再查源码、配置、脚本、测试、模板及文档引用；单独检查资源加载、脚本顺序、字体、包成员和安装配置。静态无引用不等于没有外部调用。

| 对象 | 证据与判断 | 建议 |
| --- | --- | --- |
| `dist/*.whl` | 74 个包内文件，33 个与源码 SHA-256 不同，41 个相同，无缺少/多出的包文件；清单列出差异路径 | 必要：备份后重建，不用于判断主线功能 |
| `dist/*.tar.gz` | 145 个成员，实际包含 `.pi/prompts/抖库.md` | 必要：修正排除、生成新包；新构建尚未验收 |
| `scripts/fonts/README.md:5` | `docs/font-files/InterVariable.woff2` 不存在，实际文件在应用 `static/fonts/` | 必要：修正说明，不补回重复字体 |
| `web-ui-redesign-readwise.md:3,6,1522` | 开头仍为“实施中/取代现状文档”；后文 Phase 完成，却有“截图目录不存在”等初期判断 | 必要：标明历史背景、已完成状态和当前权威文档；保留决策 |
| `web-ui-design-system.md:137–149` | 验证入口列表缺 `verify_readwise_tags.py`，后文标签验收和实际脚本存在 | 必要：补齐索引；历史数量仍保持日期语义 |
| `static/brand-mark.png`（50,679 bytes） | 源码/模板/JS/CSS/脚本/测试无引用；4 处文档引用且明确“保留/另行确认”；静态 URL 可直接访问 | 待确认，默认保留 |
| `llm.py:_chunk_segments`（1548 行） | 全部跟踪内容仅定义命中，未注册、无同名配置/动态加载；实际分析用其他分段逻辑 | 疑似旧私有 helper；可选且待确认，不删 LLM 模块 |
| `database.py:replace_reminders`（2893 行） | 仅定义命中，但为公开方法，外部 Python 调用无法排除 | 待确认，默认保留接口 |
| 两个下载器 `_locked_profile`（image_note:343、media:718） | 去除名字/docstring 后 AST 相同；两个路径均实际调用，已共用底层锁函数 | 9 行重复实现，暂不单独增加抽象 |
| MCP `add_inspiration/add_purpose` | 函数体相同，后者明确是旧工具，仍注册 | 保留两个工具，不作重复删除 |
| `PurposeInput`、`purposes`、隐藏 purpose CLI、`needs_review`、隐藏迁移命令 | 模型 alias、入口、旧状态恢复和测试均有引用 | 保留兼容契约 |
| `resources/vision_ocr.swift` | `service.py:107` 通过 `importlib.resources.files` 加载；Vision/auto 回退使用 | 保留并验证 wheel；无 Python import 不等于无用 |
| 字体、字表、SHA 文件和许可 | CSS、原型、生成说明和许可链有用途，4 个输出哈希匹配 | 保留；已生成字体仍是必要交付资源 |
| `review.py`、ASR/OCR 回退与旧复核状态 | 当前校正、旧任务恢复或配置回退仍引用 | 保留，不能按主流程变化删旧恢复路径 |
| `docs/prototypes/readwise/` | README 明确为早期演示；捕获入口仍存在，字体复用 | 可选归档，不是第二套生产前端 |
| 画面检索方案 | 文档声明独立分支，分支/worktree 实际存在 | 保留，未合并不等于废弃 |
| 两份历史 `service_analysis.py` | `test_round3_edges.py:19` 读取 round2-fix-before；`test_round4_edges.py:19` 读取 round3-fix-before；round2 探针还读取固定 Git 提交 | 保留原目录或整组归档，不能单删旧代码 |
| 质量审查其余材料 | 既有 cleanup-verification 已记录备份去重；恢复清单、补丁、XML、探针仍构成证据链；最新 3 个审查哈希匹配 | 保留期限待确认；旧失败记录不是当前缺陷 |
| `phase-6/{comparison,cleanup}.json`、`review-verification.md`、`tags/runtime/` | 跟踪脚本没有这些历史记录/目录的再生成入口；只能生成当前截图及 verification.json | 保留/归档，不能声称 140 个文件都能原样再生 |
| `.pi/prompts/抖库.md` | 仍要求“校正、人工复核、分析”，与当前 Gateway/MCP 不新增独立人工复核的流程不一致 | 用途待确认；仍使用时单独更新本地提示，不擅自公开/删除 |
| 两个空 Obsidian JSON | app.json、appearance.json 都是 `{}`，但为不同配置槽位 | 保留，不作文件去重 |
| 4 个 `.DS_Store` | SHA 相同，无应用引用，均为 Finder 元数据 | 可选清理 |
| `tests/__init__.py` | 虽为空，多处 tests.test_* 导入依赖本地包边界 | 保留，不按零字节删除 |
| round2/quality/review 命名的正式测试 | 有保留期、证据定位、发布事务、提醒并发和批量恢复断言，也有跨测试导入 | 保留所有断言；领域命名/拆分为可选 |

151 个跟踪文件不存在字节完全相同的重复文件。非虚拟环境忽略文件的精确重复只有空 Obsidian 配置与 Finder 元数据。函数 AST 比较仅报告至少 9 行的相同实现，未用相似片段判断可删除代码。

## 职责与跨目录依赖

| 依赖 | 代码证据 | 影响及方向 |
| --- | --- | --- |
| Service → Web 聊天 → Service | service_entries.py:341 延迟导入 webapp.chat；chat.py:15 导入 Service | CLI/MCP 专题生成也依赖 Web 包；可提取传输到 adapters/chat.py，保留旧导入兼容 |
| Web 操作 → 应用入口 → Web 操作 | web_operation.py:563 为 WEB_VERSION 导入 app，app 又导入 WebOperationService | 可提取 webapp/version.py，app 继续导出同名常量 |
| Database ↔ 领域存储 | database.py:389–390 导入 schema，:2387 调 TagManager._resolve；领域类依赖 Database | 有事务一致性目的，暂不迁移 schema、锁和发布逻辑 |
| 验证脚本 → 脚本/测试模块 | chat/cleanup/operations/reader 用 library；foundation 用 cleanup.contrast；8 个脚本均用 tests.test_* | 不按历史阶段删除；可抽取共享 tests/support |
| 测试 → 其他测试 | round2→quality、tags/navigation→web、浏览器→favorites/tags/web | 改名影响调用者，先抽取夹具，保留断言 |
| 原型 → 正式字体 | index.html:8–9 相对路径；capture.py 从仓库根目录提供服务 | 搬目录需同步字体路径和捕获入口 |
| 模板 → 顺序加载的前端模块 | theme 在 head，shared→功能脚本→app；reading.css 由模板/JS 动态加载 | 保留 URL、DOM id、加载顺序、全局对象与存储键，不改框架 |

静态图包含延迟与 TYPE_CHECKING 导入，不能把环等同运行错误。例如 favorites.py:14 仅为类型检查，授权引导的 Service 延迟导入用于独立进程。本次未以存在环为理由删代码。

大文件是维护信号：database.py 3,793 行、app.js 3,519 行、vault.py 1,914 行、media.py 1,946 行、webapp/app.py 1,530 行、service_capture.py 1,277 行。视频/图文流程虽然共享步骤，但证据、授权、恢复和发布不同；本次不提议统一重写。

## 目标结构与方案比较

| 方案 | 范围 | 判断 |
| --- | --- | --- |
| A | 修正发行边界与文档，保留源码物理结构；可选清缓存 | 推荐默认，解决确认问题且独立验收 |
| B | A 后分别提取聊天传输、版本常量、测试夹具 | 按需选择；批准 A 不自动包含 B |
| C | 重排 services/interfaces/storage、重做前端、合并采集流水线 | 当前收益/证据不足，排除出本次计划 |

```text
src/douyin_wiki/
├── 当前入口、service_*.py、领域/存储模块    # 保留路径与接口
├── adapters/chat.py                         # 可选：共享聊天传输
├── webapp/chat.py                           # 留上下文构建及旧导入兼容
├── webapp/version.py                        # 可选：Web 版本常量
└── webapp/templates/、webapp/static/、resources/ # 原资源路径保留
tests/
├── 当前正式测试                            # 保留断言
└── support/web.py                           # 可选：共用测试夹具
scripts/
├── verify_readwise_*.py                     # 保留命令路径
└── fonts/                                  # 保留来源、字表、许可、哈希
docs/
├── 当前使用文档、design/、prototypes/        # 明确当前/分支/历史用途
├── gateway-agents.md                       # 现有共用 MCP 调用规则，保留路径
├── agents/README.md                        # 通用自助接入入口，客户端运行文件位于仓库外
├── agents/self-setup.md                    # 能力发现、外置配置、规则加载及验收步骤
├── agents/integration-contract.md          # 参数、启动与外部验收记录契约
├── audits/                                 # 本次审计与清单
└── superpowers/plans/                       # 本次待确认计划
AGENTS.md                                   # O7：维护入口，不代替调用或 Vault 规则
```

该树为职责目标，新增源码文件只有对应阶段获确认才创建。完成后按文档维护约定保留必要结论和实施结果，不把逐轮临时探针堆进跟踪文档；本次审计材料的长期保留方式在实施收尾决定。

## 本次验证与限制

实际执行并通过：Git 分类/忽略来源/分支与 worktree 元数据核对；Ruff `--no-cache` 检查源码、测试、8 个脚本及原型捕获；102 个 Python 文件 AST 解析；12 个 JS 的 `node --check`；原有文档 21 个本地链接检查；wheel/sdist 成员与 SHA 检查；4 个字体和 3 个最新审查哈希匹配。字体 README 的问题属于行内路径，已另行人工核对。

第一阶段交付前重新核对原文件内容、mtime/符号链接和目录分组指纹，排除当时 3 份新增材料；Git HEAD/索引保持原状态。清单列出全部 151 个跟踪和 320 个非虚拟环境忽略文件，提供虚拟环境聚合指纹、资源引用、导入关系、注册入口与包差异。

审计第一阶段未执行构建、应用运行、完整 pytest、真实账号授权或截图脚本。首批新增的文档与包验收见上文，不代表功能全量回归通过。既有 cleanup-verification 的“667 passed / 1 deselected”为历史记录，本次未重跑。外部 Python/静态 URL 消费者、本地配置是否使用、证据保留期限仍待确认。
