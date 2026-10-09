# 仓库维护入口

本文件适用于维护 `douyin-wiki` 源码仓库的 Codex 和开发者。本项目源码由 Codex 维护；其他 Agent 从[调用方入口](docs/agents/README.md)接入，使用全部 MCP 工具完成抖库任务，对源码仓库只读，不在其中创建客户端配置、技能、脚本、日志或临时文件。

当前维护机器的全局规则以[全局 AGENTS.md](/Users/weisengao/.codex/AGENTS.md)为准，本文件只补充项目约定，不复制全局正文。其他机器使用其已加载的全局约定；上述本机路径不是安装或运行前提。

## 权威记录

| 记录 | 用途 |
| --- | --- |
| [README](README.md) | 安装、运行、CLI/MCP、部署和发行入口 |
| [项目文档索引](docs/README.md) | 当前文档、开发中方案和维护记录 |
| [项目结构与处理流程](docs/architecture.md) | 分析模式、模块职责、数据边界和开发验证 |
| [Service 协作 API](docs/service-collaboration-api.md) | mixin 调用、操作锁、发布和租约约束 |
| [Gateway Agent 接入指南](docs/gateway-agents.md) | 全部 MCP 工具、已有授权、任务持续执行和事件接续 |
| [Web UI 设计系统](docs/web-ui-design-system.md) | 当前界面约定；重设计规格保留为历史记录 |

## 目录与入口

| 路径 | 职责 |
| --- | --- |
| `src/douyin_wiki/` | Python 包；`cli.py` / `__main__.py` 为 CLI，`mcp_server.py` 为 STDIO MCP，`worker.py` 为后台处理 |
| `src/douyin_wiki/adapters/` | 下载、媒体、模型、向量和系统提醒适配 |
| `src/douyin_wiki/webapp/` | 本机 Web、Jinja2 模板和原生 JS/CSS；资源 URL、加载顺序及字体许可属于运行交付 |
| `src/douyin_wiki/resources/` | 动态加载的运行资源，包括 Vision OCR Swift 文件 |
| `tests/` | 正式回归测试；`support/web.py` 共用 Web 夹具和假 provider；保留 `tests/__init__.py` 和跨文件引用 |
| `scripts/` | 浏览器验证入口与字体来源、字表、许可、哈希 |
| `docs/` | 使用、架构、设计与维护记录；`docs/agents/` 放通用 MCP 自助接入规范，客户端生效配置、运行文件及验收记录均在仓库外 |
| `dist/` | 可重新生成的发行包；替换前备份并核对包成员，不发布本地提示或缓存 |
| `.venv/`、`.test-*/`、`docs/screenshots/` | 本地依赖或验证材料；忽略不代表可删除，历史证据未必能原样再生 |

用户 Vault 与源码仓库分离。`vault.py` 生成的 Vault `AGENTS.md` 约束知识库内容，不代替本仓库维护规则；维护源码不直接编辑用户 Vault、状态库或浏览器 Profile。

## 修改与验证边界

保持 console scripts、MCP 工具名称与参数、CLI 别名、Service 公开方法、Web 路径与负载、状态码、静态资源 URL 和 Vault 格式兼容。静态无引用、零字节或历史命名不足以证明文件可删除。

正式验证入口见 [README](README.md)与[开发验证](docs/architecture.md#开发验证)：`uv run ruff check src tests`、`uv run pytest -q -m 'not live'`；浏览器脚本和发行包验收见[Web 验证](docs/web-ui-design-system.md#验证)与 README。按改动范围选择检查，不为文档改动扩大完整功能回归；真实网络和账号验证需显式选择。

本机已安装服务引用此仓库的 `.venv/bin/python`。维护时先核对实际安装配置，不删除或移动该环境，不在现用环境盲目同步依赖；打包安装验收使用独立临时环境。用户未提交内容及 Git 暂存状态按全局规则保留。
