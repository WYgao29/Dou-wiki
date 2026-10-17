from __future__ import annotations

import json
import warnings
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .config import load_config
from .errors import DouyinWikiError
from .gateway_protocol import GatewayProtocol, compact_job_payload, validate_gateway_protocol
from .localization import (
    add_display_labels,
    parse_creator_decision,
    parse_job_status,
    parse_retention,
)
from .models import (
    CaptureOptions,
    GatewayContext,
    InspirationInput,
    ReviewIssue,
    SourceKind,
    TranscriptCorrection,
)
from .service import DouyinWikiService

INSTRUCTIONS = """这是抖库 0.2.1，本地知识库只由当前 Gateway Agent 完成 AI 校正与分析，\
业务请求通过 MCP 创建。
使用 tools/list 返回的全部工具，按用户任务和已有授权执行，不逐项重复审批。
源码仓库由 Codex 维护，外部 Agent 对仓库只读，不修改源码、项目配置、依赖或 Git 状态；
所有客户端配置、技能、脚本、日志和临时文件放在仓库外。
复用已确认的 0.2.0 配置、schema 16 新库和已登录浏览器 Profile；旧库不自动迁移。
正式运行使用仓库外独立 runtime 的 wheel 安装；升级后退出旧 STDIO 进程并重建连接，
重新读取 tools/list 和 instructions。当前工具仅移除 generate_topic_artifact，其他输入契约保留。
Web 管理已有任务、灵感和专题笔记，不创建业务请求，不提供聊天、模型设置或自动成果；
Web 和持久事件本身不能唤醒已结束的 Agent 对话。业务 CLI 已停用。
工具会保留英文内部状态码供程序判断，并同时返回对应的中文 label 字段。
轮询与分支必须读英文 `status`（或事件中的机读状态），禁止用中文 label 做相等判断；
面向用户回复时只能使用中文 label，不得展示英文状态码。
采集时调用 capture_douyin，并传入实际可获得的 gateway_context。收到 job_id 后用进度消息告知用户，
继续执行同一任务，用 get_job / list_job_events 检查状态；处理未结束时等待后再检查，
先检查 get_job 的 input schema；支持 gateway_protocol=2 时，所有状态查询显式传 2，读取简短状态，
这个选择独立于校正和分析所用的协议。默认完整 get_job 不用于反复轮询；
旧服务以持久事件为主要等待入口，
必要时兼容读取完整任务，不传不支持的参数。job_id 是中间回执，不结束任务、不要求用户再发检查指令。
用户选定的视频直接处理，不按时长增加确认或提示。当前对话内继续执行和回复结果不依赖后台回投，
只有对话结束后仍需处理时才使用客户端的自动续跑、会话心跳或事件桥；原生自动接续无需额外常驻监控器，
自建事件桥按 Gateway 指南单独配置。
共享等待记录的 result.shared_capture_job_id 指向执行任务，result.shared_job_status 表示其状态；
读取材料、提交校正和分析使用执行任务 ID，同一执行任务由已约定的 Agent 处理，
保留等待者任务 ID、灵感与原会话通知，不对等待记录重复分析。任务显示为
“待 AI 处理”后：调用 get_analysis_context；根据原话上下文及对应 OCR 用当前模型直接校正，
只修复识别错误、不扩写，无依据的数字或金额保留原表述；再调用 submit_transcript_correction，
如 source_kind=image_note 且 phase=analysis，则直接根据 analysis_schema 调用
submit_gateway_analysis；视频校正后重新读取分析上下文再提交，不调用独立复核模型。
四个 Gateway 读取/提交工具默认 gateway_protocol=1；新调用优先显式选 2，读取分阶段紧凑材料，
校正只提交改动片段（无改动为 []），分析可用 source_refs；两次提交都携带本阶段 context_revision。
版本 2 用 get_job 获取简短状态，阶段变化后重新取上下文；业务契约以 Gateway 接入指南为准。
历史任务若显示
“需要人工复核”，仅在用户明确要求继续该任务时调用 retry_job，不自动重跑；显示“需要登录授权”时
先调用 get_auth_status，再根据 auth_scope
提示用户运行
douyin-wiki auth video 或 douyin-wiki auth douyin，并在登录后重试；
旧版本的“等待用户确认”任务，已有授权覆盖继续处理时直接调用 approve_job 恢复。
事件处理成功或结果已交付后调用 acknowledge_job_event，不确认其他 Gateway/会话的事件。
查询时优先调用 search_knowledge，并保留 original_url，以及视频 timestamp_ms 或图文 image_index。
创建提醒按用户任务和已有授权执行，时间完整后传 confirmed=true，不重复询问已授权操作。
博主批量采集调用 capture_douyin_creator。任务显示为“待选择作品”后，调用
get_creator_inventory 一次读取并展示全部作品，再用 set_creator_work_selection 保存选择；全部作品均已
选择或跳过后调用 confirm_creator_import；已有选择条件时直接执行，不重复确认。
合集使用 capture_douyin_collection 开始清点，get_collection_inventory 默认返回全部作品。
按已有选择条件使用 set_collection_work_selection 保存所选或跳过；all_works 作用于全部页。
清单内可导入作品均有明确选择后，调用 confirm_collection_import；部分清单需 accept_partial。
带 favorites_context.batch_silent=true、creator_context.batch_silent=true 或
collection_context.batch_silent=true 的子任务仍需
完成校正和分析，但不要逐条向用户发送完成消息；以父任务汇总结果为准。用户任务包含同步时调用
sync_creator，包含定期同步时由客户端安排，普通一次性任务不扩展为长期订阅。
收藏由 scan_favorites 创建，get_favorites_import / list_favorites_imports 读取，
set_favorites_selection 保存选择，confirm_favorites_import 按已有授权确认；没有 Web 新建入口。
专题研究使用 search_knowledge / get_entry 找资料、create_topic / set_topic_sources 保存范围；
get_topic 读取专题与笔记，search_topic 只检索当前启用来源。专题没有相关证据时
明确回复“当前专题没有相关证据”，不得调用 search_knowledge 回退到全库。用户要求保存或已有授权
覆盖保存专题笔记时调用 save_topic_note，不重复确认。
持续执行到完成（含提示）、失败、取消或确实需要用户补充信息。
completed、completed_with_warnings、failed、cancelled 都是终态；回复对应结果并按事件确认规则处理后，
停止该任务的轮询和自动接续，保留其他任务与共用调度；取消后不自动 retry_job。
完成结果需要补充详情时，按回执的 entry_id 读取一次 get_entry。
"""


class DouyinWikiMCP(MCPServer):
    async def call_tool(self, name: str, arguments: dict[str, Any], context=None):
        try:
            return await super().call_tool(name, arguments, context=context)
        except ToolError as exc:
            cause: BaseException | None = exc
            while cause is not None and not isinstance(cause, DouyinWikiError):
                cause = cause.__cause__
            if isinstance(cause, DouyinWikiError):
                payload = {
                    "error": {
                        "code": cause.code,
                        "message": str(cause),
                        "details": cause.details,
                    }
                }
                raise ToolError(json.dumps(payload, ensure_ascii=False, default=str)) from cause
            raise


with warnings.catch_warnings():
    warnings.filterwarnings("ignore", message="Field 'lifespan' has an incomplete definition.*")
    mcp = DouyinWikiMCP("抖库", instructions=INSTRUCTIONS)

_SERVICE: DouyinWikiService | None = None


def _service() -> DouyinWikiService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = DouyinWikiService(load_config())
        _SERVICE.initialize_runtime()
    return _SERVICE


def _payload(value: Any) -> Any:
    return add_display_labels(value)


@mcp.tool()
def capture_douyin(
    share_text: str,
    inspirations: list[dict[str, Any]] | None = None,
    retention: str = "temporary",
    allow_long: bool = False,
    approve_cloud_analysis: bool = False,
    approve_ai_analysis: bool = False,
    gateway_context: dict[str, Any] | None = None,
    purposes: list[dict[str, Any]] | None = None,
    keep_keyframes: bool | None = None,
) -> dict[str, Any]:
    """提交作品并返回中间回执 job_id；Agent 随后继续跟进，旧时长批准参数无需填写。"""
    values = inspirations if inspirations is not None else purposes or []
    job = _service().capture_douyin(
        share_text,
        [InspirationInput.model_validate(item) for item in values],
        CaptureOptions(
            retention=parse_retention(retention),
            keep_keyframes=keep_keyframes,
            allow_long=allow_long,
            approve_cloud_analysis=approve_ai_analysis or approve_cloud_analysis,
        ),
        GatewayContext.model_validate(gateway_context) if gateway_context else None,
    )
    return _payload(job)


@mcp.tool()
def capture_douyin_creator(
    source_text: str,
    inspirations: list[dict[str, Any]] | None = None,
    retention: str = "temporary",
    allow_long: bool = False,
    approve_ai_analysis: bool = False,
    gateway_context: dict[str, Any] | None = None,
    keep_keyframes: bool | None = None,
) -> dict[str, Any]:
    """从博主主页或其任意作品清点公开作品；清点阶段不下载媒体或调用 AI。"""
    job = _service().capture_douyin_creator(
        source_text,
        [InspirationInput.model_validate(item) for item in (inspirations or [])],
        CaptureOptions(
            retention=parse_retention(retention),
            keep_keyframes=keep_keyframes,
            allow_long=allow_long,
            approve_cloud_analysis=approve_ai_analysis,
        ),
        GatewayContext.model_validate(gateway_context) if gateway_context else None,
    )
    return _payload(job)


@mcp.tool()
def get_creator_inventory(
    job_id: str,
    page: int | None = None,
    limit: int | None = None,
    decision: str | None = None,
    source_kind: str | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    """默认一次读取全部带封面的作品；超大清单仍可显式传 page/limit。"""
    return _payload(
        _service().get_creator_inventory(
            job_id,
            page=page,
            limit=limit,
            decision=parse_creator_decision(decision) if decision else None,
            source_kind=SourceKind(source_kind) if source_kind else None,
            query=query,
        )
    )


@mcp.tool()
def set_creator_work_selection(
    job_id: str,
    decision: str,
    ordinals: list[int] | None = None,
    work_ids: list[str] | None = None,
) -> dict[str, Any]:
    """把清单作品标记为“已选入库”或“未入库”；编号在本次清单中稳定。"""
    return _payload(
        _service().set_creator_work_selection(
            job_id,
            parse_creator_decision(decision),
            ordinals=ordinals,
            work_ids=work_ids,
        )
    )


@mcp.tool()
def confirm_creator_import(job_id: str, accept_partial: bool = False) -> dict[str, Any]:
    """所有作品均完成决定后，确认并只派发“已选入库”的作品。"""
    return _payload(_service().confirm_creator_import(job_id, accept_partial=accept_partial))


@mcp.tool()
def import_creator_works(
    creator_id: str,
    work_ids: list[str],
    gateway_context: dict[str, Any] | None = None,
    retention: str = "temporary",
    keep_keyframes: bool | None = None,
) -> dict[str, Any]:
    """把之前跳过的指定作品改为需要并创建采集任务。"""
    return _payload(
        _service().import_creator_works(
            creator_id,
            work_ids,
            retention=parse_retention(retention),
            keep_keyframes=keep_keyframes,
            gateway_context=(
                GatewayContext.model_validate(gateway_context) if gateway_context else None
            ),
        )
    )


@mcp.tool()
def get_creator(creator_id: str) -> dict[str, Any]:
    """读取博主资料、独立目录和作品状态汇总。"""
    return _payload(_service().get_creator(creator_id))


@mcp.tool()
def list_creators() -> list[dict[str, Any]]:
    """列出已经建立的博主知识目录。"""
    return _payload(_service().list_creators())


@mcp.tool()
def list_creator_works(creator_id: str) -> list[dict[str, Any]]:
    """列出博主全部已发现作品及其选择、入库和可用状态。"""
    return _payload(_service().list_creator_works(creator_id))


@mcp.tool()
def sync_creator(
    creator_id: str,
    gateway_context: dict[str, Any] | None = None,
    retention: str = "temporary",
    keep_keyframes: bool | None = None,
) -> dict[str, Any]:
    """仅在用户明确调用时完整同步一次主页；不会创建定时任务。"""
    return _payload(
        _service().sync_creator(
            creator_id,
            retention=parse_retention(retention),
            keep_keyframes=keep_keyframes,
            gateway_context=(
                GatewayContext.model_validate(gateway_context) if gateway_context else None
            ),
        )
    )


@mcp.tool()
def get_job(job_id: str, gateway_protocol: GatewayProtocol = 1) -> dict[str, Any]:
    """状态轮询显式传 gateway_protocol=2，返回简短状态与 next_tool；默认 1 保留完整记录。"""
    protocol = validate_gateway_protocol(gateway_protocol)
    job = _service().get_job(job_id)
    return _payload(compact_job_payload(job) if protocol == 2 else job)


@mcp.tool()
def list_jobs(status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """列出最近任务，可按状态过滤。"""
    value = parse_job_status(status) if status else None
    return _payload(_service().list_jobs(value, limit))


@mcp.tool()
async def get_auth_status(video_url: str | None = None) -> dict[str, Any]:
    """检查视频浏览器 Cookie 与图文专用会话；不返回或上传 Cookie 值。"""
    return _payload(await _service().get_auth_status(video_url=video_url))


@mcp.tool()
def list_job_events(
    after_event_id: int = 0,
    unacknowledged_only: bool = True,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """列出待 Gateway 处理或投递的持久事件；不会调用模型。"""
    return _payload(
        _service().list_job_events(
            after_event_id=after_event_id,
            unacknowledged_only=unacknowledged_only,
            limit=limit,
        )
    )


@mcp.tool()
def acknowledge_job_event(event_id: int) -> dict[str, Any]:
    """Gateway 成功处理或投递事件后确认，防止重复通知。"""
    return _payload(_service().acknowledge_job_event(event_id))


@mcp.tool()
def get_analysis_context(job_id: str, gateway_protocol: GatewayProtocol = 1) -> dict[str, Any]:
    """默认完整材料；版本 2 按 phase 返回校正稿输入或分析来源、schema 与 context_revision。"""
    protocol = validate_gateway_protocol(gateway_protocol)
    if protocol == 1:
        return _payload(_service().get_analysis_context(job_id))
    return _payload(_service().get_analysis_context(job_id, gateway_protocol=protocol))


@mcp.tool()
def submit_transcript_correction(
    job_id: str,
    corrections: list[dict[str, Any]],
    producer: str,
    model: str = "agent",
    review_issues: list[dict[str, Any]] | None = None,
    gateway_protocol: GatewayProtocol = 1,
    context_revision: str | None = None,
) -> dict[str, Any]:
    """默认回传全部 ID；版本 2 只提交改动段或 []，携带当前 context_revision；原稿保留。"""
    protocol = validate_gateway_protocol(gateway_protocol)
    extra = (
        {"gateway_protocol": protocol, "context_revision": context_revision}
        if protocol == 2
        else {}
    )
    job = _service().submit_transcript_correction(
        job_id,
        [TranscriptCorrection.model_validate(item) for item in corrections],
        producer=producer,
        model=model,
        review_issues=[ReviewIssue.model_validate(item) for item in (review_issues or [])],
        **extra,
    )
    return _payload(compact_job_payload(job) if protocol == 2 else job)


@mcp.tool()
def submit_gateway_analysis(
    job_id: str,
    analysis: dict[str, Any],
    producer: str,
    model: str = "agent",
    gateway_protocol: GatewayProtocol = 1,
    context_revision: str | None = None,
) -> dict[str, Any]:
    """按当前 analysis_schema 提交；版本 2 支持 source_refs，需本阶段 context_revision。"""
    protocol = validate_gateway_protocol(gateway_protocol)
    extra = (
        {"gateway_protocol": protocol, "context_revision": context_revision}
        if protocol == 2
        else {}
    )
    job = _service().submit_gateway_analysis(
        job_id,
        analysis,
        producer=producer,
        model=model,
        **extra,
    )
    return _payload(compact_job_payload(job) if protocol == 2 else job)


@mcp.tool()
def approve_job(job_id: str) -> dict[str, Any]:
    """恢复旧版本留下的待确认任务，沿用已有授权和处理检查点。"""
    return _payload(_service().approve_job(job_id))


@mcp.tool()
def retry_job(job_id: str) -> dict[str, Any]:
    """重试失败或需要授权的任务；历史人工复核任务重新执行模型校正。"""
    return _payload(_service().retry_job(job_id))


@mcp.tool()
def resolve_review(
    job_id: str, resolutions: dict[str, str] | None = None, accept_uncertain: bool = False
) -> dict[str, Any]:
    """兼容旧版人工复核任务；新任务校正后直接分析，建议用 retry_job 重跑旧任务。"""
    return _payload(
        _service().resolve_review(job_id, resolutions, accept_uncertain=accept_uncertain)
    )


@mcp.tool()
def submit_analysis(
    entry_id: str,
    analysis: dict[str, Any],
    producer: str,
    model: str = "agent",
) -> dict[str, Any]:
    """Agent 提交符合结构化 schema 的补充或重分析结果，并记录生成者。"""
    return _payload(_service().submit_analysis(entry_id, analysis, producer=producer, model=model))


@mcp.tool()
def reanalyze_entry(
    entry_id: str,
    force: bool = False,
    gateway_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """复用现有逐字稿和 OCR，为单条资料排队生成 v2 分析；不下载媒体。"""
    job = _service().reanalyze_entry(
        entry_id,
        force=force,
        gateway_context=(
            GatewayContext.model_validate(gateway_context) if gateway_context else None
        ),
    )
    return _payload(job)


@mcp.tool()
def reanalyze_all(
    force: bool = False,
    gateway_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """批量排队重分析所有 v1 资料；默认跳过已是 v2 的条目。"""
    return _payload(
        _service().reanalyze_all(
            force=force,
            gateway_context=(
                GatewayContext.model_validate(gateway_context) if gateway_context else None
            ),
        )
    )


@mcp.tool()
def search_knowledge(
    query: str,
    include_stale: bool = False,
    limit: int = 10,
    entry_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """混合检索作品知识，返回原链接，以及视频时间戳或图文图片编号。"""
    return _payload(
        _service().search_knowledge(
            query,
            include_stale=include_stale,
            limit=limit,
            entry_ids=entry_ids,
        )
    )


@mcp.tool()
def create_topic(
    title: str,
    entry_ids: list[str],
    goal: str = "",
    instructions: str = "",
) -> dict[str, Any]:
    """从明确选定的文章创建研究专题；标题、目标与指令均逐字保存。"""
    return _payload(_service().create_topic(title, entry_ids, goal=goal, instructions=instructions))


@mcp.tool()
def get_topic(topic_id: str) -> dict[str, Any]:
    """读取专题、来源启用状态、历史成果和版本状态。"""
    return _payload(_service().get_topic(topic_id))


@mcp.tool()
def list_topics() -> list[dict[str, Any]]:
    """列出全部研究专题。"""
    return _payload(_service().list_topics())


@mcp.tool()
def set_topic_sources(topic_id: str, sources: list[dict[str, Any]]) -> dict[str, Any]:
    """按传入顺序设置专题来源及 enabled 启用状态；停用后立即退出问答范围。"""
    return _payload(_service().set_topic_sources(topic_id, sources))


@mcp.tool()
def search_topic(
    topic_id: str,
    query: str,
    include_stale: bool = False,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """只在专题当前启用来源内检索；不会回退到全库。"""
    return _payload(
        _service().search_topic(topic_id, query, include_stale=include_stale, limit=limit)
    )


@mcp.tool()
def save_topic_note(
    topic_id: str,
    content: str,
    title: str = "专题笔记",
    confirmed: bool = False,
) -> dict[str, Any]:
    """按用户任务和已有授权传 confirmed=true，把内容逐字保存为专题笔记。"""
    return _payload(_service().save_topic_note(topic_id, content, title=title, confirmed=confirmed))


@mcp.tool()
def get_entry(entry_id: str, include_documents: bool = False) -> dict[str, Any]:
    """读取一条抖音作品的结构化数据；可选择包含完整 Markdown。"""
    return _payload(_service().get_entry(entry_id, include_documents=include_documents))


@mcp.tool()
def add_inspiration(
    entry_id: str,
    text: str,
    quote: str | None = None,
    start_ms: int | None = None,
    end_ms: int | None = None,
) -> dict[str, Any]:
    """为已有作品追加用户灵感；灵感原文不会被 AI 改写。"""
    entry = _service().add_inspiration(
        entry_id, InspirationInput(text=text, quote=quote, start_ms=start_ms, end_ms=end_ms)
    )
    return _payload(entry)


@mcp.tool()
def add_purpose(
    entry_id: str,
    text: str,
    quote: str | None = None,
    start_ms: int | None = None,
    end_ms: int | None = None,
) -> dict[str, Any]:
    """旧版兼容工具；新 Agent 应调用 add_inspiration。"""
    entry = _service().add_inspiration(
        entry_id, InspirationInput(text=text, quote=quote, start_ms=start_ms, end_ms=end_ms)
    )
    return _payload(entry)


@mcp.tool()
def confirm_reminder(
    entry_id: str,
    reminder_id: str,
    due_at: str | None = None,
    title: str | None = None,
    confirmed: bool = False,
) -> dict[str, Any]:
    """已有授权且时间完整时传 confirmed=true，在 macOS 提醒事项中创建候选提醒。"""
    return _payload(
        _service().confirm_reminder(
            entry_id,
            reminder_id,
            due_at=due_at,
            title=title,
            confirmed=confirmed,
        )
    )


@mcp.tool()
def run_maintenance(apply: bool = False) -> dict[str, Any]:
    """预览或执行过期标记、孤立页面检查与可恢复媒体清理。"""
    return _payload(_service().run_maintenance(apply=apply))


@mcp.tool()
def doctor() -> dict[str, Any]:
    """检查本地工具、浏览器、Vault、模型和 Embedding 配置。"""
    from .setup import doctor as run_doctor

    return _payload(run_doctor(load_config()))


@mcp.tool()
def scan_favorites(
    folder_ids: list[str] | None = None,
    include_images: bool = False,
    directory_only: bool = False,
    gateway_context: dict[str, Any] | None = None,
    keep_keyframes: bool | None = None,
) -> dict:
    """清点收藏，不下载或导入；按已有选择条件完成清单，再调用 confirm_favorites_import。"""
    job = _service().favorites.start(
        folder_ids=folder_ids,
        capture_options=CaptureOptions(keep_keyframes=keep_keyframes),
        include_images=include_images,
        directory_only=directory_only,
        gateway_context=GatewayContext.model_validate(gateway_context) if gateway_context else None,
    )
    return _payload({"job_id": job.id, "status": job.status})


@mcp.tool()
def list_favorites_imports(limit: int = 50) -> list[dict]:
    """恢复已有收藏导入任务，不访问收藏网页。"""
    return _payload(_service().favorites.history(limit=limit))


@mcp.tool()
def get_favorites_import(
    job_id: str, page: int = 1, limit: int = 50, folder_id: str | None = None, query: str = ""
) -> dict:
    """读取收藏清单及批量汇总；has_more 为真时继续翻页。"""
    return _payload(
        _service().favorites.get(job_id, page=page, limit=limit, folder_id=folder_id, query=query)
    )


@mcp.tool()
def set_favorites_selection(
    job_id: str, selected: bool, work_ids: list[str] | None = None, folder_id: str | None = None
) -> dict:
    """保存选择；不指定作品或收藏夹时作用于整份清单。文章暂不支持导入。"""
    return _payload(
        _service().favorites.select(
            job_id, selected=selected, work_ids=work_ids, folder_id=folder_id
        )
    )


@mcp.tool()
def confirm_favorites_import(job_id: str, accept_partial: bool = False) -> dict:
    """按用户选择和已有授权批量入队；部分导入需覆盖不完整清单，重复调用不重复创建任务。"""
    job = _service().favorites.confirm(job_id, accept_partial=accept_partial)
    return _payload({"job_id": job.id, "status": job.status})


@mcp.tool()
def retry_favorites_import(job_id: str) -> dict:
    """重试失败的清点或子任务，保留已完成结果；不绕过选择确认。"""
    job = _service().favorites.retry_failed(job_id)
    return _payload({"job_id": job.id, "status": job.status})


@mcp.tool()
def capture_douyin_collection(
    source_text: str,
    options: dict[str, Any] | None = None,
    gateway_context: dict[str, Any] | None = None,
) -> dict:
    """读取合集分享链接，仅清点作品；保存选择后再确认导入。"""
    job = _service().capture_douyin_collection(
        source_text,
        CaptureOptions.model_validate(options or {}),
        GatewayContext.model_validate(gateway_context) if gateway_context else None,
    )
    return _payload({"job_id": job.id, "status": job.status})


@mcp.tool()
def get_collection_inventory(
    job_id: str, page: int | None = None, limit: int | None = None
) -> dict:
    """读取本批合集作品及实际处理状态；不传 page/limit 返回全部清单。"""
    return _payload(_service().get_collection_inventory(job_id, page=page, limit=limit))


@mcp.tool()
def set_collection_work_selection(
    job_id: str,
    decision: Literal["selected", "skipped"],
    ordinals: list[int] | None = None,
    work_ids: list[str] | None = None,
    all_works: bool = False,
) -> dict:
    """保存合集作品选择；ordinals、work_ids、all_works 三选一，全选作用于所有页。"""
    return _payload(
        _service().set_collection_work_selection(
            job_id,
            decision,
            ordinals=ordinals,
            work_ids=work_ids,
            all_works=all_works,
        )
    )


@mcp.tool()
def confirm_collection_import(job_id: str, accept_partial: bool = False) -> dict:
    """按已保存选择创建或复用单条任务；部分清单需明确接受，重复确认不会重复入队。"""
    job = _service().confirm_collection_import(job_id, accept_partial=accept_partial)
    return _payload({"job_id": job.id, "status": job.status})


@mcp.tool()
def retry_collection_import(job_id: str) -> dict:
    """重试失败清点或失败子任务，保留已入库结果和原 Gateway 归属。"""
    job = _service().retry_collection_import(job_id)
    return _payload({"job_id": job.id, "status": job.status})


@mcp.tool()
def get_collection(mix_id: str) -> dict:
    """读取持久合集元信息、最近完整成员与知识资料关联，不访问抖音网页。"""
    return _payload(_service().get_collection(mix_id))


@mcp.tool()
def list_collections() -> list[dict]:
    """列出已读取的合集知识目录。"""
    return _payload(_service().list_collections())


@mcp.tool()
def list_collection_imports(limit: int = 50) -> list[dict]:
    """读取合集导入历史和实际批次汇总。"""
    return _payload(_service().list_collection_imports(limit=limit))


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
