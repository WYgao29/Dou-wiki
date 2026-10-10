# 授权与任务排障

## 登录检查

在 Web「设置 › 授权状态」点击「检查」。视频探测使用库内作品和专用 Playwright 浏览器；没有可用作品、网络异常或拿不到媒体地址时会显示「暂时无法确认」，不能据此断定登录失效。

已安装服务使用独立 runtime 的 `douyin-wiki` 命令；下面的 `uv run --no-sync` 示例仅用于已经准备好依赖的隔离开发环境。

视频主下载路径、图文、博主和收藏使用抖库专用 Profile。日常 Chrome 的登录状态不能代替专用 Profile 的授权。先完成专用浏览器登录，再通过 Web 任务详情或 MCP `retry_job` 重试暂停任务：

```bash
uv run --no-sync douyin-wiki auth douyin
```

`auth video` 打开配置的系统浏览器，供 yt-dlp 的最后回退使用。命令行检查可以指定已确认的视频地址：

```bash
uv run --no-sync douyin-wiki auth status --video-url 'https://www.douyin.com/video/作品ID'
```

Web 授权成功后会恢复对应范围内仍需授权的任务。专用 Profile 被采集占用时，等待采集释放后再授权。Web 进程异常退出留下的会话，会在再次发起授权时回收；正常关闭会结束本进程的授权任务。

## 作品类型

检查任务的 `resolved.source_kind` 与 canonical URL。短链解析会跟随重定向，`/note/` 和 `/gallery/` 走图文路径，`/video/` 走视频路径。若浏览器最终落点与任务类型不一致，保留失败任务信息，用最终作品地址重新提交并检查解析链。作品下架、私密或网络错误应按实际错误处理，不要反复登录。

## 任务停留状态

| 状态 | 处理 |
| --- | --- |
| `queued` | 检查 Worker 是否运行，以及任务中心的队列状态 |
| `needs_auth` | 按任务的授权范围登录，验证后重试 |
| `needs_selection` | 查看博主、收藏或合集清单，完成选择并确认导入 |
| `awaiting_agent_analysis` | 需原会话 Agent 读取阶段材料并提交 Gateway 结果；见 [接入指南](gateway-agents.md) |
| `waiting_confirmation` | 兼容状态保留，0.2.0 新采集不因时长进入该状态；旧版待确认任务不导入或接续 |
| `needs_review` | 兼容状态保留，新采集的普通识别疑点不暂停任务；旧版复核任务不接续，新库任务按错误原因选择 `resolve_review` 或重试 |
| `failed` | 先读取错误原因，修正配置或外部条件后重试 |
| `completed_with_warnings` | 查看任务详情提示与已入库资料，批次还需查看子任务 |

博主子任务重试会重新打开父批次，并在子任务结束后更新汇总。收藏恢复媒体只重新下载，不重新转录或分析；恢复期间提交的新分析和灵感会保留。

## 配置与诊断

0.2.0 只支持 `analysis_mode="gateway"`，旧 provider/local 模式会报错。模型设置和业务 CLI 已停用，排障使用 Web 或 MCP `get_job(..., gateway_protocol=2)`，运行诊断使用 `douyin-wiki doctor`。共享记录按 `result.shared_capture_job_id` 查询实际执行任务。

先查看任务错误码、阶段和模型进度，再决定重试。普通重试保留可复用的检查点；不要为排障直接删除正式 SQLite 或原始记录。开发复现使用临时配置和独立 Vault。

旧版状态库不能自动迁移到 schema 16；需要已准备的新库配置。更新 runtime、配置、Vault 或 Profile 后，退出旧 MCP 进程并重建连接，重新读取全部工具和规则。Web 不能唤醒已结束的 Agent 对话，当前任务持续执行及可选的客户端自动接续见[Gateway 指南](gateway-agents.md#事件接续)。
