# 接入参数与验收契约

本文定义通用接入参数，不是任何客户端可直接导入的原生配置格式。配置文件名称、字段和加载机制由接入 Agent 按当前客户端版本转换；业务规则只在 [Gateway 指南](../gateway-agents.md)维护。

## 参数

| 参数 | 来源与约束 |
| --- | --- |
| `repo_root` | 读取本文件所在的项目根目录；解析为真实绝对路径，仅供读取 |
| `external_root` | 客户端用户数据目录下独立的抖库实例目录；真实路径必须在仓库外 |
| `python_executable` | 仓库外独立 runtime 中已安装 0.2.0 wheel 的 Python，来源为该环境 site-packages；不使用源码 editable 安装或在接入时同步依赖 |
| `client_config_path` | 客户端实际读取的原生用户配置路径；必须在仓库外，不硬编码客户端名称 |
| `config_path` | 已存在且验证过的抖库 TOML 配置绝对路径；通过 `DOUYIN_WIKI_CONFIG` 传入 |
| `vault_path` | 从上述 TOML 读取已准备的 0.2.0 schema 16 新库真实路径；必须在仓库外，旧库不自动迁移 |
| `browser_profile_path` | 复用现有服务已登录的浏览器 Profile，通过 `DOUYIN_WIKI_BROWSER_PROFILE` 指定 |
| `allowed_tools` | `tools/list` 返回的全部工具；不按查询或采集角色缩减 |
| `gateway` | 每个实例的独立标识，长度 1–64 字符，与现有 GatewayContext 约束一致；根据客户端、主机和实例身份生成并保存，重启复用，不按客户端名称写死 |
| `rules_sources` | 本入口、自助步骤、本文及 Gateway 指南的绝对路径、加载方式与内容 SHA |
| `gateway_protocol` | 工具默认 1，新调用优先显式选 2；状态查询在 `get_job` schema 支持时显式选 2，可独立于校正和分析的协议。校正和分析采用协议 2 前核对四个 Gateway 工具能力；与 `analysis_version` 独立 |

所有路径都应解析环境变量、用户目录和符号链接后再比较。解释器启动路径保留其虚拟环境入口，不把 `.venv/bin/python` 替换成符号链接指向的系统 Python；真实路径仅用于核对来源和文件边界。`external_root`、原生配置、Vault、Profile、规则快照及其写入目标不得位于 `repo_root` 内；多个实例的工作和重试状态不共用同一目录。使用已确认且与 0.2.0 匹配的配置和新库，服务初始化可能在 Vault 中补齐模板与索引，这是正常运行的一部分；接入不删除或迁移旧业务数据。

## 仓库外目录

```text
<EXTERNAL_ROOT>/
├── integration.json       # 本实例的参数和实际验收结果
├── work/                 # 启动工作目录与任务临时文件
├── state/                # 去重、失败次数和退避状态
├── logs/                 # 连接及监控日志，不写凭据
├── backups/              # 修改前的客户端配置及 SHA
└── rules/                # 客户端必须内联指令时才生成加载快照
```

只创建实际需要的目录。客户端原生配置可以留在其已有用户配置目录，记录其真实位置即可；不要复制一份不会生效的文件充当配置。规则快照是由共用文档生成的加载材料，不是新的权威正文，重连时核对来源 SHA。

## STDIO 启动契约

现有服务只提供 STDIO。以下为逻辑模板，将占位符替换成已解析的绝对路径，再映射到客户端原生字段；JSON 本身不会展开占位符或环境变量。

```json
{
  "transport": "stdio",
  "command": "<PYTHON_EXECUTABLE>",
  "args": ["-B", "-m", "douyin_wiki.mcp_server"],
  "cwd": "<EXTERNAL_ROOT>/work",
  "env": {
    "PYTHONDONTWRITEBYTECODE": "1",
    "DOUYIN_WIKI_CONFIG": "<CONFIG_PATH>",
    "DOUYIN_WIKI_BROWSER_PROFILE": "<BROWSER_PROFILE_PATH>"
  }
}
```

`-B` 与环境变量用于禁止本进程及继承环境的 Python 子进程写字节码。启动时不使用会自动安装或同步仓库依赖的命令。客户端不能设置 `cwd` 时，先确认其实际进程工作目录在仓库外；无法确保时记录阻塞。仅支持 HTTP 的客户端不能直接连接当前服务，不创建源码内适配层。

工具列表、`gateway` 和验收状态属于客户端配置或外部记录，不是抖库 `config.toml` 的服务端权限字段。默认向客户端开放全部工具；历史记录里的 `role` 不作为缩减工具或重复审批的依据。采集时按工具 schema 传入 `gateway_context`，实例标识随任务传递。

0.2.0 的业务请求只通过 MCP 创建；Web 保留已有批次、任务、灵感和专题笔记，业务 CLI、聊天、模型设置及自动生成已停用。tools/list 当前返回 51 项，仅移除 generate_topic_artifact；工具开放以实际返回内容为准。

服务在首次业务调用后缓存配置，不支持通过修改 TOML 或重新 `initialize` 热切换。更换配置、Vault、Profile 或启动环境，以及恢复旧配置时，按[切换目标步骤](self-setup.md#6-记录结果和切换目标)暂停本实例监控、退出旧 STDIO 进程并重建连接；同时更新配置与 Profile 两个环境变量。实际新进程的目标绑定和适用的监控复核通过后，才更新接入结果并恢复派发。

`config_path` 必须指向实际存在、能解析的现有配置。当前 `load_config` 在文件不存在时会回落默认配置，接入前应核对文件和目标路径，不能只填写一个预期路径。客户端与 Worker 使用同一已确认的 0.2.0 新库与 Profile，Worker、维护、Web、MCP 四个入口使用同一独立 runtime；接入 Agent 不自行另建目标替代现有服务。

## 验收记录

接入 Agent 将实际结果写入仓库外 `integration.json`。以下为待填写模板，不代表已验收；路径、客户端信息、工具清单、来源 SHA 和限制必须填写实际值，不写凭据或 Cookie。

```json
{
  "contract_version": 1,
  "status": "planned",
  "client": {"name": "<DETECTED_CLIENT>", "version": "<DETECTED_VERSION>"},
  "repo_root": "<REPO_ROOT>",
  "external_root": "<EXTERNAL_ROOT>",
  "runtime": {
    "python_executable": "<PYTHON_EXECUTABLE>",
    "package_version": "<DETECTED_PACKAGE_VERSION>",
    "module_origin": "<DETECTED_MODULE_PATH>"
  },
  "client_config_path": "<CLIENT_CONFIG_PATH>",
  "config_path": "<CONFIG_PATH>",
  "vault_path": "<VAULT_PATH>",
  "browser_profile_path": "<BROWSER_PROFILE_PATH>",
  "gateway": "<UNIQUE_INSTANCE_ID>",
  "gateway_protocol": {"supported": [], "selected": 1, "verified": false},
  "rules": {"load_method": "<ACTUAL_METHOD>", "sources": []},
  "allowed_tools": [],
  "checks": {
    "initialize": "pending",
    "tools": "pending",
    "rules_loaded": "pending",
    "query": "pending",
    "connection_recreated": "not_requested",
    "target_binding": "pending",
    "monitor_binding": "not_requested",
    "repository_unchanged": "pending",
    "capture_and_route": "not_requested"
  },
  "limitations": [],
  "rollback": {"config_backup": "<BACKUP_PATH>", "before_sha256": "<SHA256>"}
}
```

`verified` 表示连接、全部工具加载、规则加载和目标绑定等必要检查实际通过，不表示已逐个执行所有工具。`limited` 表示部分用途可用，但用户所需流程存在明确限制；`blocked` 表示完成目标必需的能力或路径不满足。普通采集在当前任务中持续执行，不以后台监控验收为前提。只完成查询时，不能把采集和会话回传标为通过。已有配置未修改时，回退字段记录“复用，无配置变更”。

支持与选择的协议由实际 MCP schema 和执行结果填写；`gateway_protocol` 是调用参数与客户端记录，
不新增服务端权限配置。仅使用简短状态时核对 `get_job` 的 schema 和回执；校正和分析使用协议 2 时须验证阶段上下文、空/增量校正、材料版本、引用还原与简短回执；
未执行提交流程时如实记录。旧服务不支持 2 时记录 `[1]` 并使用完整流程，不能用提交试探。
具体顺序、失败处理与静默规则仍以 [Gateway 指南](../gateway-agents.md#紧凑协议gateway_protocol2)为准。

`target_binding` 记录实际 MCP 进程与所记录 Vault / Profile 的对应证据。切换或回退配置时，将 `connection_recreated`、`target_binding`、`query` 及受影响的能力重新标为 `pending`，复核通过后再填写结果。使用监控的实例还须复核 `monitor_binding`；没有监控时保持 `not_requested`，不影响当前任务内持续执行。客户端实际启动记录、进程信息和目标证据保存在仓库外，不记录凭据。

升级后的连接必须重新执行 initialize / tools/list 并加载 instructions，缓存的旧清单不能作为验收结果。Web 与持久事件本身不会唤醒已结束的对话；当前任务内持续执行不需要额外心跳，结束后接续能力单独核对。共享等待关系使用 result.shared_capture_job_id / shared_job_status，材料与提交针对执行任务 ID，终态结果仍交给原请求会话。
