# 合集导入审查修复记录

日期：2026-10-10。用户通过“sp 修复”授权处理三个 P2 问题及界面差异。当前 Agent 复用既有设计，在原隔离工作树实现、验证和复核，未委派子 Agent。修复计划见[实施记录](../plans/2026-10-10-collection-import-review-fixes.md)，使用规范见[合集导入](../../collections-import.md)。

## 状态与范围

- 审查基准：`bfac76ffea76be748c927bb76583986b9f8b45ed`；运行代码修复提交：`fce0c13`。
- 分支：`codex/douyin-collection-import`；工作树：`/Users/weisengao/.codex/worktrees/douyin-collection-import/douyin-wiki`。
- 保留开发分支，未合并、推送或部署；本轮没有修改生产 Vault、浏览器 Profile 或主线现用 `.venv`。主线仍为 `54ae500fd3abaffaefecb2af812ec6881b1e0e35`。
- CLI/MCP 名称和参数、Web 路径、Vault 格式及 schema15 保持兼容。共享等待关系保存于任务 artifacts，不新增迁移。

## 问题与修复

| 问题 | 修复及验证 |
| --- | --- |
| queued 单条短链和合集子任务会重复下载；Gateway 暂停期间再次提交也重复处理 | `capture_sharing.py` 在稳定作品 ID 解析后、媒体处理前选择原执行任务。保留等待者任务 ID、请求和原 Gateway；批次关系指向执行任务。两种先后顺序都只下载一次，等待者跟随同一条目与终态 |
| 部分清单显示待选择，公开重试接口却拒绝重新读取 | 未确认且不完整的清单可通过公开 CLI/MCP/Web 重试进入新清点世代；保留仍存在作品的明确选择，新作品待选择。完整或已确认清单仍需新批次再次读取 |
| 全部已有资料或全部跳过时，确认直接写 completed，缺少完成事件 | 确认事务先记录 monitoring，后续正常状态刷新发布终态及最终汇总；两种情况均仅生成一次完成事件，旧待选择事件被替代，重复确认不重复发送 |
| 确认前未显示新建/复用数量，清单缺封面和时长 | 显示随选择变化的预计新建、复用及不可导入数量，并显示已有资料数量、封面与时长。窄屏时长单独显示，避免被作者信息的省略号遮住 |

共享结果刷新保留原任务的处理选项与分析归属；晚解析的选项冲突只使重复任务失败。执行任务仍被独立等待者使用时，原批次取消不会终止它；最后使用者取消后终止无人使用的批次任务。取消或删除的等待者不复活，失败等待者可明确重试，重启后仍能恢复关联。已有完整条目优先复用，不因另一个排队任务的选项而拒绝补充灵感。

修复过程中复核收藏预览与确认的查重路径，将它们改为使用同一活动任务查询，排除等待记录；新增回归证明收藏关联实际执行者及取消保护。仍有效等待者的灵感在执行任务发布条目前合并，使用执行任务有效租约，避免释放租约后再写条目。

## 验证

新增19个行为回归：部分清点2个、无子任务事件2个、共享采集14个、浏览器综合场景1个；另外扩展既有浏览器断言。

| 检查 | 结果 |
| --- | --- |
| 部分清点/无子任务事件 RED → GREEN | 3 failed / 1 passed，修复后相关35项通过 |
| 共享采集初始 RED | 9 failed / 3 passed，确认重复处理、取消、结果与重启缺陷 |
| 收藏错误关联及完整条目优先复用 RED | 两个新增用例分别在修复前失败 |
| 最终相关功能回归 | 164 passed，覆盖视频、图文、收藏、合集及共享结果 |
| 浏览器 RED → GREEN | 初始2 failed / 1 passed；窄屏时长独立断言先失败，修复后最终3 passed |
| `uv run --no-sync ruff check src tests` | All checks passed |
| `uv run --no-sync pytest -q -m 'not live'` | 最终1398 passed / 2 deselected，191.54秒；前一次完整回归1398项通过后仅调整窄屏时长，再按最终代码完成此轮检查 |
| `git diff --check` | 通过 |
| `uv build --out-dir <仓库外目录>/dist` | wheel 与 sdist 成功 |
| 新临时环境安装实际 wheel | 99个 wheel 成员，94个运行文件与源码逐一一致；CLI collection 命令及52个 MCP 工具正常加载，包含8个合集工具；合成的已暂停任务复用冒烟检查为 monitoring |

浏览器运行真实本机 HTTP/API、隔离 SQLite/Vault 与合成清单。105件覆盖全部页选择、保存失败恢复、确认、历史和刷新；5→6件场景覆盖已有资料、复用任务、新任务、不支持/不可用数量与部分清点重试。亮暗主题、390px窄屏、确认栏、封面和可见时长均已检查，无 pageerror 或横向溢出。

发行包路径的首次探针断言因 macOS `/tmp` 与 `/private/tmp` 的别名失败；规范化路径后探针通过，未修改产品代码。该首次失败日志保留。

复核由当前 Agent 直接执行，重点核对事务与租约、实际关联、Gateway 通知、灵感发布、取消与重试、持久等待、完整条目兼容和界面行为；本轮没有剩余已确认的审查问题。没有独立人员或子 Agent 审查。

## 证据

本轮材料保存于 `/Users/weisengao/Documents/ChatGPT/douyin-wiki-validation/2026-10-10-collection-fixes-pYiBZY`，临时目录为 `/tmp/douyin-collection-fixes.pYiBZY`。

- `partial-events-red.log`、`sharing-red.log`、`favorites-sharing-red.log`、`entry-reuse-red.log`、`ui-red.log`、`ui-duration-red.log`：必要的修复前失败证据。
- `sharing-final-green.log`、`ui-final.log`、`full-final.log`、`ruff.log`：最终功能、浏览器与完整回归。
- `screenshots/`：亮暗主题、部分清点数量与窄屏时长/确认栏。
- `package_check.py`、`package-check.json`、`package-install.log`、`package-cli.log`、`build.log`、`dist/`：实际包成员、独立安装和接口检查；探针使用独立合成状态，没有真实媒体下载。
- `source-verification.json`：验证代码提交与文件哈希。

wheel SHA-256：`961304f422c8842b3999e37d7a15c6bf879126f7b618074967aba543f4a46bec`。

原只读审查材料仍位于 `douyin-wiki-validation/2026-10-10-collection-review-bfac76f`；首次真实合集、媒体及 Gateway 验收仍见[原验收记录](2026-10-10-douyin-collection-import-acceptance.md)。本轮未重新读取真实账号或处理新增真实媒体，不扩大原真实样本覆盖范围。
