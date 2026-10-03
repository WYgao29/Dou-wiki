from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections import deque
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from contextvars import ContextVar
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware
from watchfiles import awatch

from ..adapters.chat import ChatProvider, OpenAICompatibleChatProvider
from ..adapters.llm import FallbackAnalysisProvider, OpenAICompatibleProvider
from ..adapters.share import extract_douyin_url
from ..config import (
    AppConfig,
    LLMSettings,
    default_config_path,
    llm_api_key_required,
    load_config,
    normalize_llm_base_url,
)
from ..errors import DouyinWikiError, EntryNotFoundError, JobStateError
from ..localization import add_display_labels, label_entry_status
from ..models import (
    AnalysisMode,
    CaptureOptions,
    CaptureRequest,
    InspirationDraft,
    InspirationInput,
    RetentionPolicy,
    TopicArtifactKind,
)
from ..secrets import get_secret, store_secret
from ..service import DouyinWikiService
from ..setup import update_config_values, write_config
from ..time_utils import beijing_iso, format_beijing, parse_clock_to_ms
from ..vault import entry_operation_observer
from ..web_operation import WebOperationService
from .catalog import CONTENT_TYPE_LABELS, LibraryCatalog
from .chat import ChatContextBuilder
from .rendering import render_article, render_chat
from .version import WEB_VERSION

ANALYSIS_MODE_INFO = {
    "gateway": {
        "label": "网关 Agent",
        "web_copy": "等待外部 Agent 接续",
        "detail": (
            "下载和本地提取由后台完成；校正和分析要等 OpenClaw/Hermes 等外部 Agent。"
            "网页不会自动分析，也不能单独完成入库整理。"
        ),
        "web_can_complete": False,
    },
    "provider": {
        "label": "后台模型接口",
        "web_copy": "后台模型接口自动整理",
        "detail": (
            "导入后由 Worker 使用「设置 › 共用模型」的同一套接口自动校正和分析，"
            "可能消耗 Token。没有第二套地址或密钥。"
        ),
        "web_can_complete": True,
    },
    "local": {
        "label": "本地模式",
        "web_copy": "本地模式，不调用外部模型",
        "detail": "不调用外部模型，使用本地降级整理。适合无网络或不想消耗 Token 的场景。",
        "web_can_complete": True,
    },
}


def _capture_inspirations(items: list[CaptureInspirationBody]) -> list[InspirationInput]:
    parsed: list[InspirationInput] = []
    for item in items:
        try:
            start = parse_clock_to_ms(item.start_ms)
            end = parse_clock_to_ms(item.end_ms)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        text = item.text.strip()
        quote = (item.quote or "").strip() or None
        if not text and quote is None and start is None and end is None:
            continue
        try:
            parsed.append(InspirationInput(text=text, quote=quote, start_ms=start, end_ms=end))
        except ValidationError as exc:
            raise HTTPException(
                status_code=400,
                detail="结束时间不能早于开始时间，且填写结束时间时也要填写开始时间",
            ) from exc
    return parsed


def _existing_capture(core, video_id: str) -> dict[str, Any] | None:
    entry = core.database.find_entry_by_video_id(video_id)
    if entry is not None:
        return {
            "duplicate": True,
            "in_trash": False,
            "entry_id": entry.id,
            "title": entry.title,
            "href": f"/articles/{entry.id}",
            "message": "已在资料库",
        }
    for item in core.list_trashed_entries():
        if str(item.get("work_id") or "") == video_id:
            return {
                "duplicate": True,
                "in_trash": True,
                "trash_id": item.get("trash_id"),
                "entry_id": item.get("entry_id"),
                "title": item.get("title") or "",
                "href": "/trash",
                "message": "已在废纸篓",
            }
    return None


class CaptureInspirationBody(BaseModel):
    """Capture form clocks stay strings so a bad clock can return HTTP 400, not 422."""

    model_config = ConfigDict(extra="ignore")
    text: str = Field(default="", max_length=4000)
    quote: str | None = Field(default=None, max_length=4000)
    start_ms: int | str | None = None
    end_ms: int | str | None = None


class CaptureSubmissionRequest(BaseModel):
    share_text: str = Field(min_length=1, max_length=20_000)
    inspirations: list[CaptureInspirationBody] = Field(default_factory=list)
    retention: RetentionPolicy = RetentionPolicy.TEMPORARY
    allow_long: bool = False
    approve_cloud_analysis: bool = False


class CreateSessionRequest(BaseModel):
    scope: Literal["library", "entry", "topic"] = "library"
    context_entry_id: str | None = None
    context_topic_id: str | None = None


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=8000)


class CreateTopicRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    entry_ids: list[str] = Field(min_length=1, max_length=500)
    goal: str = Field(default="", max_length=4000)
    instructions: str = Field(default="", max_length=8000)


class TopicSourceRequest(BaseModel):
    entry_id: str = Field(min_length=1, max_length=128)
    enabled: bool = True


class SetTopicSourcesRequest(BaseModel):
    sources: list[TopicSourceRequest] = Field(min_length=1, max_length=500)


class GenerateTopicArtifactRequest(BaseModel):
    kind: TopicArtifactKind


class SaveTopicNoteRequest(BaseModel):
    content: str = Field(min_length=1, max_length=50_000)
    title: str = Field(default="专题笔记", min_length=1, max_length=200)
    confirmed: bool = False


class ConfirmDestructiveActionRequest(BaseModel):
    confirmed: bool = False


class TrashBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: bool = False
    trash_ids: list[str] = Field(min_length=1, max_length=500)


class SetFavoriteRequest(BaseModel):
    favorite: bool


class AnalysisModeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: AnalysisMode


class ModelSettingsRequest(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    base_url: str = Field(min_length=8, max_length=2048)
    model: str = Field(min_length=1, max_length=256)
    api_key: str | None = Field(default=None, max_length=8192)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        return normalize_llm_base_url(value)

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or any(character in normalized for character in {'"', "\\", "\r", "\n"}):
            raise ValueError("模型名称为空或包含不支持的字符")
        return normalized

    @field_validator("api_key")
    @classmethod
    def normalize_api_key(cls, value: str | None) -> str | None:
        normalized = (value or "").strip()
        return normalized or None


_change_origin: ContextVar[str | None] = ContextVar("change_origin", default=None)
_change_base: ContextVar[tuple | None] = ContextVar("change_base", default=None)
_change_spans: ContextVar[list | None] = ContextVar("change_spans", default=None)


class ChangeNotifier:
    def __init__(self, fingerprint_provider: Callable | None = None) -> None:
        self.version = 0
        self.last_name = "library"
        self.last_payload: dict[str, Any] = {"version": 0}
        self._condition = asyncio.Condition()
        self._events = deque(maxlen=256)
        self._fingerprint_provider = fingerprint_provider
        self._acknowledged = {}

    async def publish(self, name: str = "library", payload: dict[str, Any] | None = None) -> None:
        async with self._condition:
            self.version += 1
            self.last_name = name
            self.last_payload = payload or {"version": self.version}
            self._events.append((self.version, name, _change_origin.get()))
            if name == "library" and _change_origin.get():
                spans = _change_spans.get()
                if spans is None and self._fingerprint_provider and _change_base.get() is not None:
                    spans = [(_change_base.get(), self._fingerprint_provider())]
                for base, current in spans or []:
                    if base != current:
                        self._acknowledged[current] = base
                while len(self._acknowledged) > 256:
                    del self._acknowledged[next(iter(self._acknowledged))]
            self._condition.notify_all()

    def already_published(self, before: tuple, after: tuple) -> bool:
        seen = set()
        while after != before:
            if after in seen or after not in self._acknowledged:
                return False
            seen.add(after)
            after = self._acknowledged[after]
        return True

    def library_payload(self, since: int) -> dict[str, Any]:
        origins = {
            origin
            for version, name, origin in self._events
            if version > since and name == "library"
        }
        if self._events and since < self._events[0][0] - 1:
            origins.add(None)  # Missing history must not suppress a background update.
        return {"version": self.version, "origins": list(origins)}

    async def wait(self, last_version: int) -> int:
        async with self._condition:
            await self._condition.wait_for(lambda: self.version > last_version)
            return self.version

    async def wait_named(self, last_version: int) -> tuple[int, str, dict[str, Any]]:
        async with self._condition:
            await self._condition.wait_for(lambda: self.version > last_version)
            return self.version, self.last_name, self.last_payload


def _item_payload(item: Any) -> dict[str, Any]:
    payload = item.model_dump(mode="json", exclude={"body_markdown"})
    payload["content_type_label"] = CONTENT_TYPE_LABELS.get(item.content_type, "其他")
    payload["status"] = label_entry_status(item.status)
    payload["published_at"] = beijing_iso(item.published_at)
    payload["published_display"] = format_beijing(item.published_at)
    payload["captured_at"] = beijing_iso(item.captured_at)
    payload["captured_display"] = format_beijing(item.captured_at)
    payload["inspirations"] = [value.model_dump(mode="json") for value in item.inspirations]
    return payload


def _job_payload(job: Any) -> dict[str, Any]:
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status.value,
        "progress": job.progress,
        "result": job.result,
        "error_code": job.error_code,
        "error_message": job.error_message,
    }


def _event(name: str, payload: Any) -> str:
    return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _topic_payload(value: dict[str, Any]) -> dict[str, Any]:
    labels = {
        "overview": "专题总览",
        "comparison": "跨来源对比表",
        "evidence_map": "证据地图",
        "consensus": "共识与分歧",
        "decision_brief": "决策简报",
        "faq": "专题 FAQ",
        "note": "专题笔记",
    }
    topic = dict(value["topic"])
    topic["created_display"] = format_beijing(topic.get("created_at"))
    topic["updated_display"] = format_beijing(topic.get("updated_at"))
    artifacts = []
    for raw in value.get("artifacts", []):
        artifact = dict(raw)
        artifact["kind_label"] = labels.get(artifact["kind"], artifact["kind"])
        artifact["status_label"] = (
            "需要更新" if artifact["status"] == "needs_update" else "当前版本"
        )
        artifact["created_display"] = format_beijing(artifact.get("created_at"))
        artifact["html"] = render_chat(artifact["content_markdown"])
        artifacts.append(artifact)
    return {"topic": topic, "artifacts": artifacts}


async def _watch_catalog(
    catalog: LibraryCatalog,
    notifier: ChangeNotifier,
    stop_event: asyncio.Event,
) -> None:
    roots: list[Path] = []
    legacy = catalog.vault_path / "wiki" / "sources"
    creators = catalog.vault_path / "creators"
    topics = catalog.vault_path / "topics"
    roots.extend(path for path in (legacy, creators, topics) if path.is_dir())
    if not roots:
        roots = [catalog.vault_path]
    known_fingerprint = catalog.fingerprint

    def refresh_snapshot():
        with catalog.tags.vault.entry_operations_locked():
            current = catalog.compute_fingerprint()
            if current != known_fingerprint:
                catalog.refresh()
            return current

    async for _changes in awatch(
        *roots,
        stop_event=stop_event,
        rust_timeout=500,
        yield_on_timeout=True,
    ):
        current = await asyncio.to_thread(refresh_snapshot)
        if current == known_fingerprint:
            continue
        published = notifier.already_published(known_fingerprint, current)
        known_fingerprint = current
        if not published:
            await notifier.publish()


class FavoritesScanBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    folder_ids: list[str] | None = None
    include_images: bool = False
    directory_only: bool = False
    update_mode: Literal["incremental", "full"] = "incremental"


class FavoritesSelectionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selected: bool
    work_ids: list[str] | None = None
    folder_id: str | None = None


class FavoritesConfirmBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    accept_partial: bool = False


def create_app(
    config: AppConfig | None = None,
    *,
    config_path: Path | None = None,
    service: DouyinWikiService | None = None,
    chat_provider: ChatProvider | None = None,
    chat_provider_factory: Callable[[LLMSettings], ChatProvider] | None = None,
    start_watcher: bool = True,
) -> FastAPI:
    cfg = config or load_config()
    target_config_path = config_path or default_config_path()
    core = service or DouyinWikiService(cfg)
    if service is None:
        core.initialize_runtime()
    else:
        core.database.initialize()
        core.recover_entry_trash_operations()
    catalog = LibraryCatalog(cfg.vault_path, core.database)
    catalog.refresh()
    notifier = ChangeNotifier(catalog.compute_fingerprint)
    operations = WebOperationService(core)
    provider_factory = chat_provider_factory or OpenAICompatibleChatProvider
    provider = chat_provider or provider_factory(cfg.llm)
    context_builder = ChatContextBuilder(core)
    settings_lock = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        stop_event = asyncio.Event()
        task = (
            asyncio.create_task(_watch_catalog(catalog, notifier, stop_event))
            if start_watcher
            else None
        )
        try:
            yield
        finally:
            await operations.auth.close()
            if task:
                stop_event.set()
                with suppress(asyncio.CancelledError, TimeoutError):
                    await asyncio.wait_for(task, timeout=2)
                if not task.done():
                    task.cancel()

    app = FastAPI(
        title="抖库 Web",
        version="0.1",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation_error(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        details = [
            {key: value for key, value in error.items() if key not in {"input", "ctx"}}
            for error in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": details})

    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "testserver"],
    )
    package_root = Path(str(files("douyin_wiki.webapp")))
    from .tag_api import install_tag_api

    templates = Jinja2Templates(directory=str(package_root / "templates"))
    app.mount("/static", StaticFiles(directory=str(package_root / "static")), name="static")
    app.state.config = cfg
    app.state.config_path = target_config_path
    app.state.service = core
    app.state.catalog = catalog
    app.state.chat_provider = provider
    app.state.operations = operations

    async def publish_mutation(
        result: dict[str, Any], *, refresh_catalog: bool = True
    ) -> dict[str, Any]:
        """Keep a completed mutation successful when a UI refresh needs retrying."""
        warnings = list(result.get("warnings") or [])
        if refresh_catalog:
            try:
                await asyncio.to_thread(catalog.refresh)
            except Exception as exc:
                warnings.append(f"操作已完成，但网页目录刷新失败：{exc}")
        try:
            await notifier.publish()
        except Exception as exc:
            warnings.append(f"操作已完成，但网页更新通知失败：{exc}")
        return {**result, "warnings": warnings}

    install_tag_api(app, core, catalog, publish_mutation)

    def read_tag_overview(*, refresh: bool = False):
        if refresh:
            with core.vault.entry_operations_locked(blocking=False) as acquired:
                if acquired and catalog.compute_fingerprint() != catalog.fingerprint:
                    catalog.refresh()
        return core.tags.overview(catalog.list_items(), persist=False)

    def _local_write_origin_ok(request: Request) -> bool:
        """Require Origin or Referer to match this local host on write requests.

        The app binds to 127.0.0.1/localhost only. Same-origin browser UI always
        sends Origin (fetch/XHR) or Referer (form posts); both must match
        http(s)://{Host}. Fetch Metadata still rejects cross-site / same-site
        writes. Headerless mutating clients (curl without -H Origin/Referer)
        are rejected so a local process cannot CSRF the Web API blindly.
        """
        if request.headers.get("sec-fetch-site", "").lower() in {"cross-site", "same-site"}:
            return False
        host = (request.headers.get("host") or "").strip()
        if not host:
            return False
        allowed = {f"http://{host}", f"https://{host}"}
        origin = (request.headers.get("origin") or "").strip().rstrip("/")
        if origin:
            return origin in allowed
        referer = (request.headers.get("referer") or "").strip()
        if referer:
            parsed = urlsplit(referer)
            if not parsed.scheme or not parsed.netloc:
                return False
            return f"{parsed.scheme}://{parsed.netloc}" in allowed
        return False

    @app.middleware("http")
    async def same_origin(request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not _local_write_origin_ok(
            request
        ):
            return JSONResponse({"detail": "拒绝跨来源请求"}, status_code=403)
        origin = request.headers.get("X-Douku-Client", "")[:80]
        token = _change_origin.set(origin or None)
        spans = []
        span_token = _change_spans.set(spans)

        def observe_entry_write():
            before = catalog.compute_fingerprint()
            return lambda: spans.append((before, catalog.compute_fingerprint()))

        observer_token = entry_operation_observer.set(
            observe_entry_write if request.method in {"POST", "PUT", "PATCH", "DELETE"} else None
        )
        try:
            response = await call_next(request)
        finally:
            _change_origin.reset(token)
            _change_spans.reset(span_token)
            entry_operation_observer.reset(observer_token)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        )
        return response

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request):
        return templates.TemplateResponse(
            request,
            "app.html",
            {"page_title": "资料库", "initial_entry_id": "", "web_version": WEB_VERSION},
        )

    def shell_page(
        request: Request,
        *,
        title: str,
        entry_id: str = "",
        topic_id: str = "",
        status_code: int = 200,
        missing: bool = False,
    ):
        return templates.TemplateResponse(
            request,
            "app.html",
            {
                "page_title": title,
                "initial_entry_id": entry_id,
                "initial_topic_id": topic_id,
                "web_version": WEB_VERSION,
                "missing": missing,
            },
            status_code=status_code,
        )

    @app.get("/articles/{entry_id}", response_class=HTMLResponse)
    async def article_page(request: Request, entry_id: str):
        item = catalog.get(entry_id)
        if item is None:
            return shell_page(request, title="页面不存在", status_code=404, missing=True)
        return shell_page(request, title=item.title, entry_id=entry_id)

    @app.get("/topics/{topic_id}", response_class=HTMLResponse)
    async def topic_page(request: Request, topic_id: str):
        try:
            value = await asyncio.to_thread(core.get_topic, topic_id)
        except KeyError:
            return shell_page(request, title="页面不存在", status_code=404, missing=True)
        return shell_page(
            request,
            title=value["topic"]["title"],
            topic_id=topic_id,
        )

    @app.get("/topics", response_class=HTMLResponse)
    async def topics_page(request: Request):
        return templates.TemplateResponse(
            request,
            "app.html",
            {
                "page_title": "专题",
                "initial_entry_id": "",
                "initial_topic_id": "",
                "web_version": WEB_VERSION,
            },
        )

    @app.get("/tags", response_class=HTMLResponse)
    async def tags_page(request: Request):
        return templates.TemplateResponse(
            request,
            "app.html",
            {
                "page_title": "标签管理",
                "initial_entry_id": "",
                "web_version": WEB_VERSION,
            },
        )

    @app.get("/trash", response_class=HTMLResponse)
    async def trash_page(request: Request):
        return templates.TemplateResponse(
            request,
            "app.html",
            {
                "page_title": "废纸篓",
                "initial_entry_id": "",
                "initial_topic_id": "",
                "web_version": WEB_VERSION,
            },
        )

    @app.get("/settings/analysis", response_class=HTMLResponse)
    async def analysis_settings_page(request: Request):
        return templates.TemplateResponse(
            request,
            "app.html",
            {
                "page_title": "导入分析",
                "initial_entry_id": "",
                "initial_topic_id": "",
                "web_version": WEB_VERSION,
            },
        )

    @app.get("/settings/model", response_class=HTMLResponse)
    async def model_settings_page(request: Request):
        return templates.TemplateResponse(
            request,
            "app.html",
            {
                "page_title": "共用模型",
                "initial_entry_id": "",
                "initial_topic_id": "",
                "web_version": WEB_VERSION,
            },
        )

    @app.get("/api/library")
    async def library(
        q: str = "",
        author: str = "",
        content_type: str = "",
        tag: str = "",
        inspiration_only: bool = False,
        recent: bool = False,
        fuzzy: bool = False,
        limit: int | None = Query(None, ge=1, le=500),
    ):
        tags = await asyncio.to_thread(read_tag_overview, refresh=True)
        resolved_tags = core.tags.resolve([tag])
        tag = resolved_tags[0] if resolved_tags else ""
        items = catalog.filter(
            query=q,
            fuzzy=fuzzy,
            author=author,
            content_type=content_type,
            tag=tag,
            inspiration_only=inspiration_only,
        )
        if recent:
            items = items[:20]
        if limit is not None:
            items = items[:limit]
        all_items = catalog.list_items()
        return {
            "items": [_item_payload(item) for item in items],
            "facets": {
                "authors": sorted({item.author for item in all_items}),
                "content_types": [
                    {"value": code, "label": label}
                    for code, label in CONTENT_TYPE_LABELS.items()
                    if any(item.content_type == code for item in all_items)
                ],
                "tags": sorted(row["name"] for row in tags["tags"] if row["count"] > 0),
                "tag_details": tags["tags"],
                "sidebar_tags": tags["sidebar"],
                "tag_aliases": tags["aliases"],
            },
            "total": len(items),
            "limit": limit,
            "version": catalog.version,
        }

    @app.post("/api/captures", status_code=202)
    async def create_capture(payload: CaptureSubmissionRequest):
        try:
            extract_douyin_url(payload.share_text)
        except DouyinWikiError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        inspirations = _capture_inspirations(payload.inspirations)
        try:
            resolved = await core.resolver.resolve(payload.share_text)
        except DouyinWikiError as exc:
            raise HTTPException(400, detail=str(exc)) from exc

        def enqueue():
            with core.vault.entry_operations_locked():
                existing = _existing_capture(core, resolved.video_id)
                if existing:
                    return JSONResponse(existing, status_code=200)
                if catalog.compute_fingerprint() != catalog.fingerprint:
                    catalog.refresh()
                item = next(
                    (item for item in catalog.list_items() if item.work_id == resolved.video_id),
                    None,
                )
                if item:
                    return JSONResponse(
                        {
                            "duplicate": True,
                            "in_trash": False,
                            "entry_id": item.entry_id,
                            "href": f"/articles/{item.entry_id}",
                            "title": item.title,
                            "message": "已在资料库",
                        }
                    )
                new_job_id = uuid.uuid4().hex
                job = core.database.get_or_create_active_job(
                    CaptureRequest(
                        share_text=payload.share_text,
                        inspirations=inspirations,
                        options=CaptureOptions(
                            retention=payload.retention,
                            allow_long=payload.allow_long,
                            approve_cloud_analysis=payload.approve_cloud_analysis,
                        ),
                    ),
                    kind="capture",
                    new_job_id=new_job_id,
                    match_artifact="work_id",
                    artifacts={
                        "work_id": resolved.video_id,
                        "resolved": {
                            "original_url": resolved.original_url,
                            "canonical_url": resolved.canonical_url,
                            "video_id": resolved.video_id,
                            "source_kind": resolved.source_kind.value,
                            "redirect_chain": list(resolved.redirect_chain),
                        },
                    },
                )
            if job.id != new_job_id:
                return JSONResponse(
                    {
                        "duplicate": True,
                        "active": True,
                        "in_trash": False,
                        "job_id": job.id,
                        "href": f"/jobs/{job.id}",
                        "message": "同作品已有采集任务；新填写的灵感与处理选项尚未保存",
                    }
                )
            presented = operations.present(job)
            return {"job_id": job.id, **presented}

        return await asyncio.to_thread(enqueue)

    @app.get("/api/articles/{entry_id}")
    async def article(entry_id: str):
        item = catalog.get(entry_id)
        if item is None:
            raise HTTPException(status_code=404, detail="文章不存在")
        return {
            "item": _item_payload(item),
            "html": render_article(
                item.body_markdown,
                source_path=item.source_path,
                catalog=catalog,
            ),
        }

    @app.put("/api/articles/{entry_id}/favorite")
    async def set_article_favorite(entry_id: str, payload: SetFavoriteRequest):
        existing_item = catalog.get(entry_id)
        if existing_item is None:
            raise HTTPException(status_code=404, detail="文章不存在")
        try:
            result = await asyncio.to_thread(core.set_entry_favorite, entry_id, payload.favorite)
        except EntryNotFoundError as exc:
            raise HTTPException(status_code=404, detail="文章不存在") from exc
        mutation = await publish_mutation({})
        item = catalog.get(entry_id) or existing_item
        item_payload = _item_payload(item)
        persisted = result["entry"]
        item_payload.update(
            {
                "favorite": persisted.favorite,
                "media_status": persisted.media_status,
                "retention": persisted.retention.value,
            }
        )
        restore_job = result["restore_job"]
        response = {
            "item": item_payload,
            "restore_job": _job_payload(restore_job) if restore_job else None,
            "warnings": mutation["warnings"],
        }
        return JSONResponse(response, status_code=202 if restore_job else 200)

    @app.get("/api/jobs/{job_id}")
    async def job_status(job_id: str):
        try:
            return operations.get_job(job_id)
        except JobStateError as exc:
            raise HTTPException(status_code=404, detail="任务不存在") from exc

    @app.post("/api/jobs/{job_id}/retry", status_code=202)
    async def retry_job(job_id: str):
        try:
            core.get_job(job_id)
        except JobStateError as exc:
            raise HTTPException(status_code=404, detail="任务不存在") from exc
        try:
            return operations.present(await asyncio.to_thread(core.retry_job, job_id))
        except JobStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.delete("/api/articles/{entry_id}")
    async def trash_article(entry_id: str, payload: ConfirmDestructiveActionRequest):
        try:
            result = await asyncio.to_thread(
                core.trash_entry, entry_id, confirmed=payload.confirmed
            )
        except EntryNotFoundError as exc:
            raise HTTPException(status_code=404, detail="文章不存在") from exc
        except (ValueError, DouyinWikiError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return await publish_mutation(result)

    @app.get("/api/trash")
    async def list_trash():
        items = []
        for raw in core.list_trashed_entries():
            item = dict(raw)
            item["deleted_display"] = format_beijing(item.get("deleted_at"))
            items.append(item)
        return {"items": items, "total": len(items)}

    @app.post("/api/trash/batch/restore")
    async def restore_trash_items(payload: TrashBatchRequest):
        try:
            result = await asyncio.to_thread(
                core.restore_trashed_entries, payload.trash_ids, confirmed=payload.confirmed
            )
        except EntryNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, DouyinWikiError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return await publish_mutation(result)

    @app.post("/api/trash/batch/purge")
    async def permanently_delete_trash_items(payload: TrashBatchRequest):
        try:
            result = await asyncio.to_thread(
                core.permanently_delete_trashed_entries,
                payload.trash_ids,
                confirmed=payload.confirmed,
            )
        except EntryNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return await publish_mutation(result, refresh_catalog=False)

    @app.post("/api/trash/{trash_id}/restore")
    async def restore_trash_item(trash_id: str, payload: ConfirmDestructiveActionRequest):
        try:
            result = await asyncio.to_thread(
                core.restore_trashed_entry, trash_id, confirmed=payload.confirmed
            )
        except EntryNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, DouyinWikiError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return await publish_mutation(result)

    @app.delete("/api/trash/{trash_id}")
    async def permanently_delete_trash_item(
        trash_id: str, payload: ConfirmDestructiveActionRequest
    ):
        try:
            result = await asyncio.to_thread(
                core.permanently_delete_trashed_entry, trash_id, confirmed=payload.confirmed
            )
        except EntryNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return await publish_mutation(result, refresh_catalog=False)

    @app.get("/api/topics")
    async def list_topics():
        values = await asyncio.to_thread(core.list_topics)
        return {"topics": [_topic_payload(item) for item in values]}

    @app.post("/api/topics", status_code=201)
    async def create_topic(payload: CreateTopicRequest):
        try:
            value = await asyncio.to_thread(
                core.create_topic,
                payload.title,
                payload.entry_ids,
                goal=payload.goal,
                instructions=payload.instructions,
            )
        except (ValueError, EntryNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await asyncio.to_thread(catalog.refresh)
        await notifier.publish()
        return _topic_payload(value)

    @app.get("/api/topics/{topic_id}")
    async def get_topic(topic_id: str):
        try:
            return _topic_payload(await asyncio.to_thread(core.get_topic, topic_id))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.put("/api/topics/{topic_id}/sources")
    async def set_topic_sources(topic_id: str, payload: SetTopicSourcesRequest):
        try:
            value = await asyncio.to_thread(
                core.set_topic_sources,
                topic_id,
                [item.model_dump() for item in payload.sources],
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, EntryNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await asyncio.to_thread(catalog.refresh)
        await notifier.publish()
        return _topic_payload(value)

    @app.post("/api/topics/{topic_id}/artifacts", status_code=201)
    async def generate_topic_artifact(topic_id: str, payload: GenerateTopicArtifactRequest):
        try:
            artifact = await core.generate_topic_artifact(
                topic_id,
                payload.kind,
                provider=app.state.chat_provider,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, DouyinWikiError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await asyncio.to_thread(catalog.refresh)
        await notifier.publish()
        result = {
            "topic": (await asyncio.to_thread(core.get_topic, topic_id))["topic"],
            "artifacts": [artifact],
        }
        return _topic_payload(result)["artifacts"][0]

    @app.post("/api/topics/{topic_id}/notes", status_code=201)
    async def save_topic_note(topic_id: str, payload: SaveTopicNoteRequest):
        try:
            artifact = await asyncio.to_thread(
                core.save_topic_note,
                topic_id,
                payload.content,
                title=payload.title,
                confirmed=payload.confirmed,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await asyncio.to_thread(catalog.refresh)
        await notifier.publish()
        result = {
            "topic": (await asyncio.to_thread(core.get_topic, topic_id))["topic"],
            "artifacts": [artifact],
        }
        return _topic_payload(result)["artifacts"][0]

    @app.get("/api/library/events")
    async def library_events(request: Request):
        async def events() -> AsyncIterator[str]:
            version = notifier.version
            yield _event("ready", {"version": version})
            while not await request.is_disconnected():
                try:
                    since = version
                    await asyncio.wait_for(notifier.wait(version), timeout=20)
                    payload = notifier.library_payload(since)
                    version = payload["version"]
                    if payload["origins"]:
                        yield _event("library", payload)
                except TimeoutError:
                    yield ": keep-alive\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.get("/api/chat/sessions")
    async def list_sessions():
        current_provider = app.state.chat_provider
        sessions = await asyncio.to_thread(core.database.list_chat_sessions)
        return {
            "sessions": [item.model_dump(mode="json") for item in sessions],
            "model": current_provider.model or "未配置",
            "configured": current_provider.configured,
        }

    @app.post("/api/chat/sessions", status_code=201)
    async def create_session(payload: CreateSessionRequest):
        if payload.scope == "entry":
            if not payload.context_entry_id:
                raise HTTPException(status_code=400, detail="文章范围对话需要有效的目标文章")
            item = catalog.get(payload.context_entry_id)
            if item is None:
                raise HTTPException(status_code=400, detail="文章范围对话需要有效的目标文章")
            if not item.database_managed:
                raise HTTPException(
                    status_code=409,
                    detail="该文章来自只读 Markdown，无法创建文章范围对话，请使用全库对话",
                )
        if payload.scope == "topic":
            if not payload.context_topic_id:
                raise HTTPException(status_code=400, detail="专题范围对话需要有效的目标专题")
            try:
                await asyncio.to_thread(core.get_topic, payload.context_topic_id)
            except KeyError as exc:
                raise HTTPException(
                    status_code=400, detail="专题范围对话需要有效的目标专题"
                ) from exc
        session = await asyncio.to_thread(
            core.database.create_chat_session,
            scope=payload.scope,
            context_entry_id=payload.context_entry_id,
            context_topic_id=payload.context_topic_id,
        )
        return session.model_dump(mode="json")

    @app.get("/api/chat/sessions/{session_id}")
    async def get_session(session_id: str):
        try:
            session = await asyncio.to_thread(core.database.get_chat_session, session_id)
            messages = await asyncio.to_thread(core.database.list_chat_messages, session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "session": session.model_dump(mode="json"),
            "messages": [
                {
                    **message.model_dump(mode="json"),
                    "html": render_chat(message.content) if message.role == "assistant" else "",
                }
                for message in messages
            ],
        }

    @app.delete("/api/chat/sessions/{session_id}")
    async def delete_session(session_id: str):
        if not await asyncio.to_thread(core.database.delete_chat_session, session_id):
            raise HTTPException(status_code=404, detail="对话不存在")
        return {"status": "已删除"}

    @app.post("/api/chat/sessions/{session_id}/messages")
    async def send_message(session_id: str, payload: SendMessageRequest):
        try:
            session = await asyncio.to_thread(core.database.get_chat_session, session_id)
            history = await asyncio.to_thread(
                core.database.list_chat_messages, session_id, limit=24
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if session.scope == "topic" and not session.context_topic_id:
            raise HTTPException(status_code=409, detail="该对话关联的专题已不存在，请新建对话")
        try:
            messages, citations = await asyncio.to_thread(
                context_builder.build,
                payload.content,
                context_entry_id=(session.context_entry_id if session.scope == "entry" else None),
                context_topic_id=(session.context_topic_id if session.scope == "topic" else None),
                history=history,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=409, detail="该对话关联的专题已不存在，请新建对话"
            ) from exc
        await asyncio.to_thread(core.database.add_chat_message, session_id, "user", payload.content)
        if session.title == "新对话":
            await asyncio.to_thread(
                core.database.update_chat_session,
                session_id,
                title=payload.content.strip().replace("\n", " ")[:32],
            )

        async def response_stream() -> AsyncIterator[str]:
            current_provider = app.state.chat_provider
            yield _event(
                "meta",
                {
                    "model": current_provider.model or "未配置",
                    "citations": [item.model_dump(mode="json") for item in citations],
                },
            )
            answer = ""
            usage: dict[str, int | None] = {}
            try:
                if session.scope == "topic" and not citations:
                    answer = "当前专题没有相关证据。"
                    yield _event("delta", {"text": answer})
                    saved = await asyncio.to_thread(
                        core.database.add_chat_message,
                        session_id,
                        "assistant",
                        answer,
                        citations=[],
                    )
                    yield _event(
                        "done",
                        {
                            "message": {
                                **saved.model_dump(mode="json"),
                                "html": render_chat(answer),
                            },
                            "usage": None,
                        },
                    )
                    return
                async for chunk in current_provider.stream(messages):
                    if chunk.text:
                        answer += chunk.text
                        yield _event("delta", {"text": chunk.text})
                    if chunk.usage:
                        usage = chunk.usage
                saved = await asyncio.to_thread(
                    core.database.add_chat_message,
                    session_id,
                    "assistant",
                    answer,
                    citations=citations,
                    model=current_provider.model or None,
                    prompt_tokens=usage.get("prompt_tokens"),
                    completion_tokens=usage.get("completion_tokens"),
                    total_tokens=usage.get("total_tokens"),
                )
                yield _event(
                    "done",
                    {
                        "message": {
                            **saved.model_dump(mode="json"),
                            "html": render_chat(answer),
                        },
                        "usage": usage or None,
                    },
                )
            except (DouyinWikiError, httpx.HTTPError) as exc:
                if answer:
                    await asyncio.to_thread(
                        core.database.add_chat_message,
                        session_id,
                        "assistant",
                        answer,
                        citations=citations,
                        model=current_provider.model or None,
                    )
                details = getattr(exc, "details", {})
                cause = str(details.get("cause") or "").strip()
                yield _event(
                    "error",
                    {
                        "message": str(exc),
                        "cause": cause[:500] or None,
                        "partial_saved": bool(answer),
                    },
                )
            except Exception:
                yield _event("error", {"message": "AI 对话暂时不可用，请稍后重试。"})

        return StreamingResponse(response_stream(), media_type="text/event-stream")

    @app.get("/api/settings/model")
    async def get_model_settings():
        current_config = app.state.config
        current_provider = app.state.chat_provider
        key_required = llm_api_key_required(current_config.llm.base_url)
        key_present = bool(await asyncio.to_thread(get_secret, current_config.llm.api_key_env))
        return {
            "enabled": current_config.llm.enabled,
            "base_url": current_config.llm.base_url,
            "model": current_config.llm.model,
            "api_key_configured": key_present,
            "api_key_required": key_required,
            "api_key_source": (
                "环境变量"
                if os.environ.get(current_config.llm.api_key_env)
                else "macOS Keychain"
                if key_present
                else "本机接口无需密钥"
                if not key_required
                else "未配置"
            ),
            "configured": current_provider.configured,
            "analysis_mode": current_config.analysis_mode.value,
            "analysis_mode_label": ANALYSIS_MODE_INFO[current_config.analysis_mode.value]["label"],
            "analysis_modes": [
                {"value": value, **info} for value, info in ANALYSIS_MODE_INFO.items()
            ],
        }

    @app.post("/api/settings/model")
    async def save_model_settings(payload: ModelSettingsRequest):
        try:
            async with settings_lock:
                config_path_value = Path(app.state.config_path)
                current_config = (
                    load_config(config_path_value)
                    if config_path_value.exists()
                    else app.state.config
                )
                existing_key = await asyncio.to_thread(get_secret, current_config.llm.api_key_env)
                endpoint_changed = current_config.llm.base_url.rstrip("/") != payload.base_url
                if (
                    endpoint_changed
                    and llm_api_key_required(payload.base_url)
                    and existing_key
                    and not payload.api_key
                ):
                    raise HTTPException(
                        status_code=400,
                        detail="更换云端接口时必须重新输入该服务对应的 API Key",
                    )
                llm = current_config.llm.model_copy(
                    update={
                        "enabled": True,
                        "base_url": payload.base_url,
                        "model": payload.model,
                    }
                )
                updated = current_config.model_copy(update={"llm": llm})
                if payload.api_key:
                    await asyncio.to_thread(store_secret, llm.api_key_env, payload.api_key)
                if config_path_value.exists():
                    await asyncio.to_thread(
                        update_config_values,
                        config_path_value,
                        {
                            "llm": {
                                "enabled": True,
                                "base_url": llm.base_url,
                                "model": llm.model,
                            }
                        },
                    )
                else:
                    await asyncio.to_thread(
                        write_config, updated, config_path_value, overwrite=True
                    )
                app.state.config = updated
                core.config = updated
                app.state.chat_provider = await asyncio.to_thread(provider_factory, llm)
                if updated.analysis_mode == AnalysisMode.PROVIDER:
                    core.analysis = await asyncio.to_thread(OpenAICompatibleProvider, llm)
                reload_result = operations.request_worker_reload()
        except (DouyinWikiError, OSError) as exc:
            raise HTTPException(status_code=500, detail=f"保存模型配置失败：{exc}") from exc
        current_provider = app.state.chat_provider
        key_present = bool(await asyncio.to_thread(get_secret, llm.api_key_env))
        key_required = llm_api_key_required(llm.base_url)
        return {
            "status": "配置已保存",
            "worker_reload": reload_result["status"],
            "worker_reload_note": reload_result["note"],
            "configured": current_provider.configured,
            "model": current_provider.model or "未配置",
            "api_key_configured": key_present,
            "api_key_required": key_required,
            "analysis_mode": updated.analysis_mode.value,
            "analysis_mode_label": ANALYSIS_MODE_INFO[updated.analysis_mode.value]["label"],
        }

    @app.post("/api/settings/analysis-mode")
    async def save_analysis_mode(payload: AnalysisModeRequest):
        async with settings_lock:
            config_path_value = Path(app.state.config_path)
            current_config = (
                load_config(config_path_value) if config_path_value.exists() else app.state.config
            )
            updated = current_config.model_copy(update={"analysis_mode": payload.mode})
            if config_path_value.exists():
                await asyncio.to_thread(
                    update_config_values,
                    config_path_value,
                    {None: {"analysis_mode": payload.mode.value}},
                )
            else:
                await asyncio.to_thread(write_config, updated, config_path_value, overwrite=True)
            app.state.config = updated
            core.config = updated
            if payload.mode == AnalysisMode.PROVIDER:
                core.analysis = OpenAICompatibleProvider(updated.llm)
            else:
                core.analysis = FallbackAnalysisProvider()
            reload_result = operations.request_worker_reload()
        info = ANALYSIS_MODE_INFO[payload.mode.value]
        warning = None
        if payload.mode == AnalysisMode.PROVIDER and not app.state.chat_provider.configured:
            warning = (
                "已改为后台模型接口，但共用模型尚未配置完整。"
                "请先到「设置 › 共用模型」保存接口和模型。"
            )
        return {
            "status": "分析方式已保存",
            "analysis_mode": payload.mode.value,
            "analysis_mode_label": info["label"],
            "web_copy": info["web_copy"],
            "web_can_complete": info["web_can_complete"],
            "worker_reload": reload_result.get("status"),
            "warning": warning,
        }

    @app.post("/api/settings/model/test")
    async def test_model_settings():
        current_provider = app.state.chat_provider
        if not current_provider.configured:
            raise HTTPException(status_code=400, detail="请先保存模型名称和 API Key")
        answer = ""
        usage: dict[str, int | None] = {}
        try:
            async for chunk in current_provider.stream(
                [
                    {
                        "role": "system",
                        "content": "这是连接测试。请只回复：连接成功",
                    },
                    {"role": "user", "content": "测试连接"},
                ]
            ):
                answer += chunk.text
                if chunk.usage:
                    usage = chunk.usage
        except DouyinWikiError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"模型连接失败：{exc}") from exc
        return {
            "status": "连接成功",
            "model": current_provider.model,
            "response": answer[:200],
            "usage": usage or None,
        }

    @app.post("/api/inspirations/confirm")
    async def confirm_inspiration(payload: InspirationDraft):
        if not payload.confirmed:
            raise HTTPException(status_code=400, detail="保存灵感前必须由用户确认")
        if catalog.get(payload.entry_id) is None:
            raise HTTPException(status_code=404, detail="目标文章不存在")
        try:
            entry = await asyncio.to_thread(
                core.add_inspiration,
                payload.entry_id,
                InspirationInput(
                    text=payload.text,
                    quote=payload.quote,
                    start_ms=payload.start_ms,
                    end_ms=payload.end_ms,
                ),
            )
        except EntryNotFoundError as exc:
            raise HTTPException(status_code=404, detail="目标文章尚未进入知识索引") from exc
        await asyncio.to_thread(catalog.refresh)
        await notifier.publish()
        return {
            "status": "灵感已保存",
            "entry_id": entry.id,
            "inspirations": [item.model_dump(mode="json") for item in entry.inspirations],
        }

    @app.get("/media/{media_path:path}")
    async def media(media_path: str):
        target = catalog.safe_media_path(media_path)
        if target is None:
            raise HTTPException(status_code=404, detail="图片不存在或不允许访问")
        return FileResponse(target, headers={"Cache-Control": "private, max-age=3600"})

    async def favorites_call(method, *args, **kwargs):
        try:
            return await asyncio.to_thread(method, *args, **kwargs)
        except EntryNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except JobStateError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/favorites/imports", status_code=202)
    async def favorites_scan(body: FavoritesScanBody):
        job = await favorites_call(core.favorites.start, **body.model_dump())
        return {"job_id": job.id, "status": job.status}

    @app.get("/api/favorites/imports")
    async def favorites_history(limit: int = Query(50, ge=1, le=200)):
        return add_display_labels(await favorites_call(core.favorites.history, limit=limit))

    @app.get("/api/favorites/imports/{job_id}")
    async def favorites_detail(
        job_id: str,
        page: int = Query(1, ge=1),
        limit: int = Query(50, ge=1, le=200),
        folder_id: str | None = None,
        query: str = "",
        only_new: bool = False,
    ):
        return add_display_labels(
            await favorites_call(
                core.favorites.get,
                job_id,
                page=page,
                limit=limit,
                folder_id=folder_id,
                query=query,
                only_new=only_new,
            )
        )

    @app.post("/api/favorites/imports/{job_id}/selection")
    async def favorites_selection(job_id: str, body: FavoritesSelectionBody):
        return add_display_labels(
            await favorites_call(core.favorites.select, job_id, **body.model_dump())
        )

    @app.post("/api/favorites/imports/{job_id}/confirm", status_code=202)
    async def favorites_confirm(job_id: str, body: FavoritesConfirmBody):
        job = await favorites_call(core.favorites.confirm, job_id, **body.model_dump())
        return {"job_id": job.id, "status": job.status}

    @app.post("/api/favorites/imports/{job_id}/retry", status_code=202)
    async def favorites_retry(job_id: str):
        job = await favorites_call(core.favorites.retry_failed, job_id)
        return {"job_id": job.id, "status": job.status}

    from .operation_api import register_operation_routes

    register_operation_routes(
        app,
        core=core,
        operations=operations,
        templates=templates,
        notifier=notifier,
        web_version=WEB_VERSION,
    )

    @app.get("/{full_path:path}", response_class=HTMLResponse)
    async def spa_fallback(request: Request, full_path: str):
        """Serve the SPA shell for unknown browser paths instead of bare JSON 404."""
        if full_path.startswith(("api/", "static/", "media/")):
            raise HTTPException(status_code=404, detail="Not Found")
        accept = request.headers.get("accept", "")
        if "text/html" not in accept and "*/*" not in accept and accept != "":
            raise HTTPException(status_code=404, detail="Not Found")
        return templates.TemplateResponse(
            request,
            "app.html",
            {
                "page_title": "页面不存在",
                "initial_entry_id": "",
                "initial_topic_id": "",
                "web_version": WEB_VERSION,
            },
            status_code=404,
        )

    return app


def run_web(config_path: Path | None = None) -> None:
    config = load_config(config_path)
    if not config.web.enabled:
        raise RuntimeError("Web 服务未启用，请在配置中设置 web.enabled=true")
    uvicorn.run(
        create_app(config, config_path=config_path or default_config_path()),
        host=config.web.host,
        port=config.web.port,
        access_log=False,
    )
