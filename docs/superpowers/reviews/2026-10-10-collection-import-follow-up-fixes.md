# 共享采集后续审查修复

2026-10-10。用户通过“修复”授权处理上一轮复核的两个 P2 和一个 P3 问题。三项均已修复，完整非 live 回归1415项通过。当前 Agent 直接实现与复核，未委派；分支仍待集成，未修改生产 Vault、Profile 或主线现用环境。

## 版本与范围

审查基准为 `09d426526767de96e164afe5c053c5dd5f29d20a`，运行代码提交为 `c046d21`、`ae2c643`、`7f8c844`。工作树为 `/Users/weisengao/.codex/worktrees/douyin-collection-import/douyin-wiki`，分支为 `codex/douyin-collection-import`。主线仍为 `54ae500fd3abaffaefecb2af812ec6881b1e0e35`，工作区干净，安装的服务继续引用主线 `.venv/bin/python`。

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

保留开发分支，未合并、推送或部署。此前只读复核仍位于 `douyin-wiki-validation/2026-10-10-collection-fix-review-LF2qCq`；其中未列为审查问题的旧选择事件探针继续排除，本轮没有扩大修复范围。
