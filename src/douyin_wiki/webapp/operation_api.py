"""Web primary-entry API routes for jobs, auth, imports, and system operations."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..adapters.share import DouyinShareResolver
from ..errors import DouyinWikiError, EntryNotFoundError, JobStateError
from ..localization import add_display_labels
from ..models import (
    CreatorWorkDecision,
    SourceKind,
)
from ..operation import analysis_presentation, present_auth_session
from ..web_operation import WebOperationService


def _event(name: str, payload: Any) -> str:
    return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


class AuthStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    channel: Literal["video", "douyin"]
    trigger_job_id: str | None = None


class ConfirmBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: bool = False


class ReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resolutions: dict[str, str] | None = None
    accept_uncertain: bool = False


class CollectionSelectionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["selected", "skipped"]
    work_ids: list[str] | None = Field(default=None, min_length=1)
    ordinals: list[int] | None = Field(default=None, min_length=1)
    all_works: bool = False

    @model_validator(mode="after")
    def validate_selector(self):
        if sum((self.work_ids is not None, self.ordinals is not None, self.all_works)) != 1:
            raise ValueError("work_ids、ordinals、all_works 必须选择其中一个")
        return self


class CreatorSelectionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: CreatorWorkDecision
    work_ids: list[str] | None = None
    ordinals: list[int] | None = None


class CreatorConfirmBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    accept_partial: bool = False


class ReanalyzeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    force: bool = False


class ClassifyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=20_000)


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, EntryNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, JobStateError):
        message = str(exc)
        if "not found" in message:
            return HTTPException(status_code=404, detail="任务不存在")
        return HTTPException(status_code=409, detail=message)
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, DouyinWikiError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail="操作失败")


def register_operation_routes(
    app: FastAPI,
    *,
    core,
    operations: WebOperationService,
    templates,
    notifier,
    web_version: str,
) -> None:
    def spa(
        request: Request,
        title: str,
        *,
        status_code: int = 200,
        missing: bool = False,
    ) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "app.html",
            {
                "page_title": title,
                "initial_entry_id": "",
                "initial_topic_id": "",
                "web_version": web_version,
                "missing": missing,
            },
            status_code=status_code,
        )

    @app.get("/imports", response_class=HTMLResponse)
    async def imports_page(request: Request):
        return spa(request, "导入内容")

    @app.get("/imports/single", response_class=HTMLResponse)
    async def imports_single_page(request: Request):
        return spa(request, "单条导入")

    @app.get("/imports/creators", response_class=HTMLResponse)
    async def imports_creators_page(request: Request):
        return spa(request, "博主导入")

    @app.get("/imports/favorites", response_class=HTMLResponse)
    async def imports_favorites_page(request: Request):
        return spa(request, "导入抖音收藏")

    @app.get("/imports/collections", response_class=HTMLResponse)
    async def imports_collections_page(request: Request):
        return spa(request, "导入抖音合集")

    @app.get("/auth")
    async def auth_alias():
        return RedirectResponse(url="/settings/auth", status_code=307)

    @app.get("/health")
    async def health_alias():
        return RedirectResponse(url="/api/system/health", status_code=307)

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_hub(request: Request):
        return spa(request, "设置")

    @app.get("/jobs", response_class=HTMLResponse)
    async def jobs_page(request: Request):
        return spa(request, "任务中心")

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    async def job_page(request: Request, job_id: str):
        try:
            operations.get_job(job_id)
        except JobStateError:
            return spa(request, "任务不存在", status_code=404, missing=True)
        return spa(request, "任务详情")

    @app.get("/settings/auth", response_class=HTMLResponse)
    async def auth_page(request: Request):
        return spa(request, "授权状态")

    @app.get("/settings/system", response_class=HTMLResponse)
    async def system_page(request: Request):
        return spa(request, "系统设置")

    @app.get("/api/overview")
    async def overview():
        return await operations.overview()

    @app.get("/api/jobs")
    async def list_jobs(
        kind: str | None = None,
        status: str | None = None,
        requires_user_action: bool | None = None,
        parents_only: bool = True,
        page: int = Query(1, ge=1),
        limit: int = Query(50, ge=1, le=200),
    ):
        try:
            return operations.list_jobs(
                kind=kind,
                status=status,
                requires_user_action=requires_user_action,
                parents_only=parents_only,
                page=page,
                limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/jobs/{job_id}/review")
    async def get_review(job_id: str):
        try:
            job = operations.get_job(job_id)
        except JobStateError as exc:
            raise _http_error(exc) from exc
        result = job.get("result") or {}
        warnings = list(result.get("warnings") or [])
        return {
            "job_id": job_id,
            "issues": job.get("review_issues") or [],
            "warnings": warnings,
            "status": job["status"],
        }

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str):
        try:
            job = await asyncio.to_thread(core.cancel_job, job_id)
        except JobStateError as exc:
            raise _http_error(exc) from exc
        await notifier.publish("jobs")
        return operations.present(job)

    @app.delete("/api/jobs/{job_id}")
    async def delete_job(job_id: str):
        try:
            await asyncio.to_thread(core.delete_job, job_id)
        except JobStateError as exc:
            raise _http_error(exc) from exc
        await notifier.publish("jobs")
        return {"deleted": job_id}

    @app.post("/api/imports/classify")
    async def classify_import(body: ClassifyBody):
        try:
            kind = await DouyinShareResolver().classify(body.text)
        except DouyinWikiError as exc:
            raise _http_error(exc) from exc
        return {"kind": kind}

    @app.post("/api/jobs/{job_id}/dismiss")
    async def dismiss_job(job_id: str):
        try:
            job = await asyncio.to_thread(operations.dismiss_job, job_id)
        except JobStateError as exc:
            raise _http_error(exc) from exc
        await notifier.publish("jobs")
        return operations.present(job)

    @app.post("/api/jobs/{job_id}/approve", status_code=202)
    async def approve_job(job_id: str):
        try:
            core.get_job(job_id)
            job = await asyncio.to_thread(core.approve_job, job_id)
        except JobStateError as exc:
            raise _http_error(exc) from exc
        await notifier.publish("jobs")
        return operations.present(job)

    @app.post("/api/jobs/{job_id}/review", status_code=202)
    async def resolve_review(job_id: str, body: ReviewBody):
        try:
            job = await asyncio.to_thread(
                core.resolve_review,
                job_id,
                body.resolutions,
                accept_uncertain=body.accept_uncertain,
            )
        except JobStateError as exc:
            raise _http_error(exc) from exc
        await notifier.publish("jobs")
        return operations.present(job)

    @app.get("/api/auth/status")
    async def auth_status(refresh: bool = False):
        return await operations.auth_status(refresh=refresh)

    @app.post("/api/auth/sessions", status_code=202)
    async def start_auth(body: AuthStartRequest):
        try:
            session = await operations.auth.start(body.channel, trigger_job_id=body.trigger_job_id)
        except (JobStateError, ValueError) as exc:
            raise _http_error(exc) from exc
        await notifier.publish("auth")
        return present_auth_session(session)

    @app.get("/api/auth/sessions/{session_id}")
    async def get_auth_session(session_id: str):
        try:
            return present_auth_session(operations.auth.get(session_id))
        except JobStateError as exc:
            raise _http_error(exc) from exc

    @app.post("/api/auth/sessions/{session_id}/cancel")
    async def cancel_auth(session_id: str):
        try:
            session = operations.auth.cancel(session_id)
        except JobStateError as exc:
            raise _http_error(exc) from exc
        await notifier.publish("auth")
        return present_auth_session(session)

    @app.get("/api/system/health")
    async def system_health():
        return operations.system_health()

    @app.post("/api/system/doctor")
    async def run_doctor():
        return await asyncio.to_thread(operations.run_doctor)

    @app.post("/api/system/maintenance")
    async def run_maintenance(body: ConfirmBody):
        result = await asyncio.to_thread(operations.maintenance, confirmed=body.confirmed)
        if body.confirmed:
            await notifier.publish("health")
        return result

    @app.post("/api/system/rebuild")
    async def run_rebuild(body: ConfirmBody):
        result = await asyncio.to_thread(operations.rebuild, confirmed=body.confirmed)
        if body.confirmed:
            await notifier.publish("health")
        return result

    @app.post("/api/system/worker/reload")
    async def reload_worker():
        return operations.request_worker_reload()

    @app.get("/api/system/storage")
    async def storage():
        return await asyncio.to_thread(operations.storage)

    @app.get("/api/system/analysis")
    async def analysis_status():
        return analysis_presentation(operations.analysis_mode)

    @app.get("/api/system/model-health")
    async def model_health():
        raise HTTPException(status_code=410, detail="模型健康检查已停用")

    async def collection_call(method, *args, **kwargs):
        try:
            return await asyncio.to_thread(method, *args, **kwargs)
        except (DouyinWikiError, ValueError) as exc:
            raise _http_error(exc) from exc

    @app.post("/api/collections/inventory")
    async def collection_inventory():
        raise HTTPException(
            status_code=410,
            detail="网页新建请求已停用，请通过 Agent 使用 MCP 工具 capture_douyin_collection。",
        )

    @app.get("/api/collections")
    async def list_collections():
        return add_display_labels(await collection_call(core.list_collections))

    @app.get("/api/collections/{mix_id}")
    async def get_collection(mix_id: str):
        return add_display_labels(await collection_call(core.get_collection, mix_id))

    @app.get("/api/collection-imports")
    async def collection_history(limit: int = Query(50, ge=1, le=200)):
        return add_display_labels(await collection_call(core.list_collection_imports, limit=limit))

    @app.get("/api/collection-imports/{job_id}")
    async def collection_detail(
        job_id: str, page: int = Query(1, ge=1), limit: int = Query(50, ge=1, le=1000)
    ):
        return add_display_labels(
            await collection_call(core.get_collection_inventory, job_id, page=page, limit=limit)
        )

    @app.post("/api/collection-imports/{job_id}/selection")
    async def collection_selection(job_id: str, body: CollectionSelectionBody):
        data = await collection_call(
            core.set_collection_work_selection, job_id, **body.model_dump()
        )
        await notifier.publish("jobs")
        return add_display_labels(data)

    @app.post("/api/collection-imports/{job_id}/confirm", status_code=202)
    async def collection_confirm(job_id: str, body: CreatorConfirmBody):
        job = await collection_call(
            core.confirm_collection_import, job_id, accept_partial=body.accept_partial
        )
        await notifier.publish("jobs")
        return {"job_id": job.id, "status": job.status.value}

    @app.post("/api/collection-imports/{job_id}/retry", status_code=202)
    async def collection_retry(job_id: str):
        job = await collection_call(core.retry_collection_import, job_id)
        await notifier.publish("jobs")
        return {"job_id": job.id, "status": job.status.value}

    @app.post("/api/creators/inventory")
    async def creator_inventory():
        raise HTTPException(
            status_code=410,
            detail="网页新建请求已停用，请通过 Agent 使用 MCP 工具 capture_douyin_creator。",
        )

    @app.get("/api/creators")
    async def list_creators():
        return {"items": add_display_labels(core.list_creators())}

    @app.get("/api/creators/{creator_id}")
    async def get_creator(creator_id: str):
        try:
            return add_display_labels(core.get_creator(creator_id))
        except Exception as exc:
            raise HTTPException(status_code=404, detail="博主不存在") from exc

    @app.get("/api/creators/{creator_id}/works")
    async def creator_works(creator_id: str):
        try:
            core.get_creator(creator_id)
        except Exception as exc:
            raise HTTPException(status_code=404, detail="博主不存在") from exc
        return {"items": add_display_labels(core.list_creator_works(creator_id))}

    @app.post("/api/creators/{creator_id}/sync")
    async def sync_creator(creator_id: str):
        raise HTTPException(
            status_code=410,
            detail="网页新建请求已停用，请通过 Agent 使用 MCP 工具 sync_creator。",
        )

    @app.post("/api/creators/{creator_id}/import-skipped")
    async def import_skipped(creator_id: str):
        raise HTTPException(
            status_code=410,
            detail="网页新建请求已停用，请通过 Agent 使用 MCP 工具 import_creator_works。",
        )

    @app.get("/api/creator-imports/{job_id}")
    async def creator_import_detail(
        job_id: str,
        page: int = Query(1, ge=1),
        limit: int = Query(50, ge=1, le=200),
        decision: CreatorWorkDecision | None = None,
        source_kind: SourceKind | None = None,
        query: str | None = None,
        not_imported: bool = False,
    ):
        try:
            data = core.get_creator_inventory(
                job_id,
                page=page,
                limit=limit,
                decision=decision,
                source_kind=source_kind,
                query=query,
                not_imported=not_imported,
            )
        except JobStateError as exc:
            raise _http_error(exc) from exc
        job = operations.present(core.get_job(job_id))
        return add_display_labels({**data, "job": job})

    @app.post("/api/creator-imports/{job_id}/selection")
    async def creator_selection(job_id: str, body: CreatorSelectionBody):
        try:
            result = await asyncio.to_thread(
                core.set_creator_work_selection,
                job_id,
                body.decision,
                ordinals=body.ordinals,
                work_ids=body.work_ids,
            )
        except JobStateError as exc:
            raise _http_error(exc) from exc
        return add_display_labels(result)

    @app.post("/api/creator-imports/{job_id}/confirm", status_code=202)
    async def creator_confirm(job_id: str, body: CreatorConfirmBody):
        try:
            job = await asyncio.to_thread(
                core.confirm_creator_import, job_id, accept_partial=body.accept_partial
            )
        except JobStateError as exc:
            raise _http_error(exc) from exc
        await notifier.publish("jobs")
        return operations.present(job)

    @app.post("/api/articles/{entry_id}/reanalyze", status_code=202)
    async def reanalyze_article(entry_id: str, body: ReanalyzeBody):
        try:
            job = await asyncio.to_thread(core.reanalyze_entry, entry_id, force=body.force)
        except EntryNotFoundError as exc:
            raise _http_error(exc) from exc
        await notifier.publish("jobs")
        return operations.present(job)

    @app.delete("/api/imports/{job_id}")
    async def delete_import(job_id: str, body: ConfirmBody):
        try:
            result = await asyncio.to_thread(
                operations.delete_import_history, job_id, confirmed=body.confirmed
            )
        except JobStateError as exc:
            raise _http_error(exc) from exc
        if body.confirmed:
            await notifier.publish("jobs")
        return result

    @app.post("/api/favorites/cache/clear")
    async def clear_favorites_cache(body: ConfirmBody):
        result = await asyncio.to_thread(operations.clear_favorites_cache, confirmed=body.confirmed)
        if body.confirmed:
            await notifier.publish("jobs")
        return result

    @app.get("/api/events")
    async def operation_events(request: Request):
        async def events():
            version = notifier.version
            yield _event("ready", {"version": version})
            while not await request.is_disconnected():
                try:
                    version, name, payload = await asyncio.wait_for(
                        notifier.wait_named(version), timeout=20
                    )
                    yield _event(name, payload)
                except TimeoutError:
                    yield ": keep-alive\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")
