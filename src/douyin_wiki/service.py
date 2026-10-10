from __future__ import annotations

import asyncio
import warnings
from contextlib import suppress
from importlib.resources import files
from pathlib import Path
from typing import Any

from .adapters.creator import DouyinCreatorAdapter
from .adapters.embeddings import EmbeddingService
from .adapters.image_note import PlaywrightImageNoteDownloader
from .adapters.llm import (
    AnalysisProvider,
    FallbackAnalysisProvider,
    OpenAICompatibleProvider,
)
from .adapters.media import (
    FFmpegMediaProcessor,
    YtDlpDownloader,
)
from .adapters.media_models import OCREngine, SelectedOCR, SelectedTranscriber, Transcriber
from .adapters.reminders import MacOSReminderAdapter
from .adapters.share import DouyinShareResolver
from .auth_guidance import AuthGuidanceLauncher, NoopAuthGuidanceLauncher
from .capture_sharing import CaptureSharing
from .config import AppConfig
from .database import Database
from .errors import (
    BrowserAuthRequiredError,
    CookieRequiredError,
    DouyinWikiError,
    EntryNotFoundError,
    JobCancelledError,
    JobLeaseLostError,
    JobStateError,
)
from .models import (
    AnalysisMode,
    AuthCheckResult,
    CaptureOptions,
    CaptureRequest,
    EntryRecord,
    GatewayContext,
    InspirationInput,
    JobEvent,
    JobRecord,
    JobStatus,
    ReminderCandidate,
)
from .search import KnowledgeIndexer, KnowledgeSearch
from .service_analysis import AnalysisMixin
from .service_capture import CaptureMixin
from .service_entries import EntriesMixin
from .service_import import ImportMixin
from .service_maintenance import MaintenanceMixin
from .service_trash import TrashMixin
from .tags import TagManager
from .vault import VaultWriter


class DouyinWikiService(
    CaptureMixin,
    AnalysisMixin,
    ImportMixin,
    EntriesMixin,
    TrashMixin,
    MaintenanceMixin,
):
    def __init__(
        self,
        config: AppConfig,
        *,
        resolver: DouyinShareResolver | None = None,
        downloader: YtDlpDownloader | None = None,
        image_note_downloader: PlaywrightImageNoteDownloader | None = None,
        creator_adapter: DouyinCreatorAdapter | None = None,
        favorites_adapter=None,
        collection_adapter=None,
        media: FFmpegMediaProcessor | None = None,
        transcriber: Transcriber | None = None,
        ocr: OCREngine | None = None,
        analysis: AnalysisProvider | None = None,
        embeddings: EmbeddingService | None = None,
        reminders: MacOSReminderAdapter | None = None,
        auth_guidance_launcher: AuthGuidanceLauncher | None = None,
    ) -> None:
        self.config = config
        self.database = Database(config.database_path)
        self.vault = VaultWriter(config.vault_path)
        self.tags = TagManager(self.database, self.vault)
        self.capture_sharing = CaptureSharing(self)
        self.resolver = resolver or DouyinShareResolver()
        self.downloader = downloader or YtDlpDownloader(config.media, config.browser_profile_dir)
        self.image_note_downloader = image_note_downloader or PlaywrightImageNoteDownloader(
            config.media, config.browser_profile_dir
        )
        self.creator_adapter = creator_adapter or DouyinCreatorAdapter(
            config.media, config.browser_profile_dir, self.resolver
        )
        from .adapters.favorites import DouyinFavoritesAdapter
        from .favorites import FavoritesService

        self.favorites = FavoritesService(
            self,
            favorites_adapter or DouyinFavoritesAdapter(config.media, config.browser_profile_dir),
        )
        from .adapters.collection import DouyinCollectionAdapter
        from .collections import CollectionService

        self.collections = CollectionService(
            self,
            collection_adapter or DouyinCollectionAdapter(config.media, config.browser_profile_dir),
        )
        self.media = media or FFmpegMediaProcessor()
        self.transcriber = transcriber or SelectedTranscriber(config.media)
        script = Path(str(files("douyin_wiki").joinpath("resources/vision_ocr.swift")))
        self.ocr = ocr or SelectedOCR(config.media, script)
        if analysis is not None:
            self.analysis = analysis
        elif config.analysis_mode == AnalysisMode.LOCAL:
            self.analysis = FallbackAnalysisProvider()
        elif config.analysis_mode == AnalysisMode.PROVIDER:
            # Provider mode is an explicit promise to use the configured API.
            # Keep the unconfigured provider so the job fails visibly instead
            # of silently replacing an existing analysis with heuristic output.
            self.analysis = OpenAICompatibleProvider(config.llm)
        else:
            self.analysis = FallbackAnalysisProvider()
        self.embeddings = embeddings or EmbeddingService(config.embeddings)
        self.indexer = KnowledgeIndexer(self.database, self.embeddings)
        self.searcher = KnowledgeSearch(self.database, self.embeddings)
        self.reminders = reminders or MacOSReminderAdapter()
        self.auth_guidance_launcher = auth_guidance_launcher or NoopAuthGuidanceLauncher()
        self.download_semaphore = asyncio.Semaphore(config.worker.download_concurrency)
        self.media_semaphore = asyncio.Semaphore(config.worker.media_concurrency)
        self.analysis_semaphore = asyncio.Semaphore(config.worker.analysis_concurrency)

    def initialize(self, *, initialize_git: bool = True) -> None:
        with self.vault.entry_operations_locked():
            self.vault.initialize(initialize_git=initialize_git)
            branding = self.vault.migrate_branding()
            statuses = self.vault.migrate_visible_status_labels()
            times = self.vault.migrate_visible_times_to_beijing()
            self.database.initialize()
            self.recover_entry_trash_operations_locked()
            creator_views = self._migrate_creator_views()
            if initialize_git:
                self.vault.commit(
                    [*branding, *statuses, *times, *creator_views],
                    "chore: localize 抖库 vault",
                )

    def initialize_runtime(self) -> None:
        """Ensure older Vaults receive safe, additive layout migrations on every entrypoint."""
        with self.vault.entry_operations_locked():
            with self.vault.locked():
                changed = self.vault.initialize(initialize_git=False)
                branding = self.vault.migrate_branding()
                statuses = self.vault.migrate_visible_status_labels()
                times = self.vault.migrate_visible_times_to_beijing()
            self.database.initialize()
            self.recover_entry_trash_operations_locked()
            with self.vault.locked():
                creator_views = self._migrate_creator_views()
                self.vault.commit(
                    [*changed, *branding, *statuses, *times, *creator_views],
                    "chore: update 抖库 vault layout",
                )

    def _migrate_creator_views(self) -> list[Path]:
        entries = self.database.list_entries()
        changed: list[Path] = []
        for creator in self.database.list_creators():
            if not self.vault.creator_index_needs_refresh(creator.folder_path):
                continue
            works = self.database.list_creator_works(creator.id)
            changed.extend(
                self.vault.write_creator(
                    creator,
                    works,
                    entries,
                    action="资料页升级",
                    append_log=False,
                )
            )
        return changed

    async def authenticate_douyin(self, *, timeout_seconds: int = 600) -> dict[str, Any]:
        await self.image_note_downloader.authenticate(timeout_seconds=timeout_seconds)
        check = await self.image_note_downloader.check_auth()
        return check.model_dump(mode="json")

    async def authenticate_video(self) -> dict[str, Any]:
        check = await self.downloader.authenticate()
        return check.model_dump(mode="json")

    async def check_auth_scope(
        self,
        scope: str,
        *,
        video_url: str | None = None,
    ) -> AuthCheckResult:
        if scope == "video":
            adapter = self.downloader
            kwargs = {"video_url": video_url}
            source = str(self.config.browser_profile_dir)
        elif scope == "image_note":
            adapter = self.image_note_downloader
            kwargs = {}
            source = str(self.config.browser_profile_dir)
        elif scope == "favorites":
            adapter = self.favorites.adapter
            kwargs = {}
            source = str(self.config.browser_profile_dir)
        elif scope == "collection":
            adapter = self.collections.adapter
            kwargs = {}
            source = str(self.config.browser_profile_dir)
        elif scope == "creator":
            adapter = self.creator_adapter
            kwargs = {}
            source = str(self.config.browser_profile_dir)
        else:
            raise ValueError(f"不支持的授权范围：{scope}")

        check_auth = getattr(adapter, "check_auth", None)
        if check_auth is None:
            return AuthCheckResult(
                scope=scope,
                state="unavailable",
                ok=False,
                cookie_source=source,
                message="当前注入的下载适配器不支持认证状态检查",
            )
        return await check_auth(**kwargs)

    async def get_auth_status(self, *, video_url: str | None = None) -> dict[str, Any]:
        video, image_note, creator = await asyncio.gather(
            self.check_auth_scope("video", video_url=video_url),
            self.check_auth_scope("image_note"),
            self.check_auth_scope("creator"),
        )
        return {
            "video": video.model_dump(mode="json"),
            "image_note": image_note.model_dump(mode="json"),
            "creator": creator.model_dump(mode="json"),
            "cookie_values_exposed": False,
        }

    def capture_douyin(
        self,
        share_text: str,
        inspirations: list[InspirationInput] | None = None,
        options: CaptureOptions | None = None,
        gateway_context: GatewayContext | None = None,
    ) -> JobRecord:
        request = CaptureRequest(
            share_text=share_text,
            inspirations=inspirations or [],
            options=options or CaptureOptions(),
            gateway_context=gateway_context,
        )
        return self.database.create_job(request)

    def capture_douyin_collection(self, source_text, options=None, gateway_context=None):
        return self.collections.start(source_text, options, gateway_context)

    def get_collection_inventory(self, job_id, *, page=None, limit=None):
        return self.collections.get(job_id, page=page, limit=limit)

    def set_collection_work_selection(
        self, job_id, decision, *, ordinals=None, work_ids=None, all_works=False
    ):
        return self.collections.select(
            job_id, decision, ordinals=ordinals, work_ids=work_ids, all_works=all_works
        )

    def confirm_collection_import(self, job_id, *, accept_partial=False):
        return self.collections.confirm(job_id, accept_partial=accept_partial)

    def retry_collection_import(self, job_id):
        return self.collections.retry_failed(job_id)

    def get_collection(self, mix_id):
        return self.collections.get_collection(mix_id)

    def list_collections(self):
        return self.collections.list_collections()

    def list_collection_imports(self, *, limit=50):
        return self.collections.history(limit=limit)

    def get_job(self, job_id: str) -> JobRecord:
        job = self.database.get_job(job_id)
        if job.artifacts.get("shared_capture_job_id"):
            job = self.capture_sharing.refresh(job_id)
        self._apply_live_creator_selection(job)
        if job.kind == "favorites_import":
            job = self.favorites.refresh(job.id)
        elif job.kind == "collection_import":
            job = self.collections.refresh(job.id)
        if job.status == JobStatus.NEEDS_REVIEW:
            job.result["review_issues"] = [
                issue.model_dump(mode="json")
                for issue in self.database.get_review_issues(job_id, open_only=True)
            ]
        return job

    def list_jobs(self, status: JobStatus | None = None, limit: int = 50) -> list[JobRecord]:
        jobs = self.database.list_jobs(status, limit)
        for index, job in enumerate(jobs):
            if job.artifacts.get("shared_capture_job_id"):
                jobs[index] = self.capture_sharing.refresh(job.id)
            self._apply_live_creator_selection(job)
            if job.kind == "favorites_import":
                jobs[index] = self.favorites.refresh(job.id)
            elif job.kind == "collection_import":
                jobs[index] = self.collections.refresh(job.id)
        return jobs

    def _apply_live_creator_selection(self, job: JobRecord) -> None:
        """Expose current creator decisions instead of the dispatch-time snapshot."""
        if job.kind != "creator_import" or not job.artifacts.get("creator_id"):
            return
        summary = self.database.creator_inventory_summary(job.id)
        if summary.get("total", 0):
            job.result["selection"] = summary

    def list_job_events(
        self,
        *,
        after_event_id: int = 0,
        unacknowledged_only: bool = True,
        limit: int = 50,
    ) -> list[JobEvent]:
        return self.database.list_job_events(
            after_event_id=after_event_id,
            unacknowledged_only=unacknowledged_only,
            limit=limit,
        )

    def acknowledge_job_event(self, event_id: int) -> JobEvent:
        return self.database.acknowledge_job_event(event_id)

    def retry_job(self, job_id: str) -> JobRecord:
        """Retry a failed job or explicitly reprocess a legacy review job."""
        job = self.database.get_job(job_id)
        if job.artifacts.get("user_deleted"):
            raise JobStateError("任务已删除")
        if job.kind == "media_restore":
            updated = self.database.requeue_job_deduplicated(job_id, match_artifact="entry_id")
            if updated.id == job_id and updated.artifacts.get("user_dismissed"):
                return self.database.update_job(job_id, remove_artifacts={"user_dismissed"})
            return updated
        if job.status == JobStatus.NEEDS_REVIEW:
            archived = [
                *(job.artifacts.get("previous_review_attempts") or []),
                {
                    "review_issues": [
                        issue.model_dump(mode="json")
                        for issue in self.database.get_review_issues(job_id)
                    ],
                    "transcript_corrected": job.artifacts.get("transcript_corrected"),
                },
            ]
            updated = self.database.requeue_job(
                job_id,
                artifacts={"previous_review_attempts": archived, "review_issues": []},
                remove_artifacts={
                    "transcript_corrected",
                    "transcript_edits",
                    "correction_notes",
                    "review_resolved",
                    "analysis",
                    "analysis_candidate",
                    "llm_checkpoints",
                    "user_dismissed",
                    "cancel_requested",
                },
                clear_review_issues=True,
                expected_updated_at=job.updated_at,
            )
            return updated
        if job.status not in {JobStatus.FAILED, JobStatus.NEEDS_AUTH}:
            raise JobStateError("只有“失败”“需要登录授权”或历史“需要人工复核”的任务可以重试")
        # Sticky analysis_candidate would skip the next model call and replay a
        # previously failed candidate forever — clear it on ordinary retries.
        result = None
        if job.artifacts.get("shared_capture_job_id"):
            result = {
                key: value for key, value in job.result.items()
                if key not in {"shared_capture_job_id", "shared_job_status"}
            }
        updated = self.database.requeue_job(
            job_id,
            remove_artifacts={
                "analysis_candidate", "user_dismissed", "cancel_requested", "shared_capture_job_id"
            },
            result=result,
            expected_updated_at=job.updated_at,
        )
        self.refresh_job_parents(updated.id)
        return updated

    def cancel_job(self, job_id: str) -> JobRecord:
        with self.vault.entry_operations_locked():
            return self._cancel_job_locked(job_id)

    def _cancel_job_locked(self, job_id: str) -> JobRecord:
        job = self.database.request_cancel(job_id)
        if job.kind in {"creator_import", "favorites_import", "collection_import"}:
            self.capture_sharing.cancel_batch_followers(job_id)
            terminal = {
                JobStatus.COMPLETED,
                JobStatus.COMPLETED_WITH_WARNINGS,
                JobStatus.CANCELLED,
                JobStatus.FAILED,
            }
            for child_id in self.database.batch_child_ids(job_id):
                with suppress(JobStateError):
                    child = self.database.get_job(child_id)
                    owned_by_batch = any(
                        (child.artifacts.get(key) or {}).get("parent_job_id")
                        for key in ("creator_context", "favorites_context", "collection_context")
                    )
                    parents = [
                        self.database.get_job(pid)
                        for pid in self.database.capture_parent_ids(child_id)
                        if pid != job_id
                    ]
                    shared = any(
                        p.status not in terminal
                        and not p.artifacts.get("cancel_requested")
                        and not p.artifacts.get("user_deleted")
                        for p in parents
                    ) or self.capture_sharing.has_independent_waiters(child_id)
                    if owned_by_batch and not shared:
                        self.database.request_cancel(child_id)
                        self.refresh_job_parents(child_id)
        owner_id = job.artifacts.get("shared_capture_job_id")
        if owner_id and self.capture_sharing.cancel_abandoned_batch_owner(owner_id):
            self.refresh_job_parents(owner_id)
        self.refresh_job_parents(job_id)
        if job.kind == "favorites_import":
            self.favorites.refresh(job_id)
        elif job.kind == "creator_import":
            self.refresh_creator_parent(job_id)
        elif job.kind == "collection_import":
            self.collections.refresh(job_id)
        return self.database.get_job(job_id)

    def refresh_job_parents(self, job_id: str) -> None:
        self.capture_sharing.refresh_for_owner(job_id)
        self.favorites.refresh_for_child(job_id)
        self.collections.refresh_for_child(job_id)
        for parent_id in self.database.capture_parent_ids(job_id):
            parent = self.database.get_job(parent_id)
            if parent.kind == "creator_import":
                self.refresh_creator_parent(parent.id)

    def delete_job(self, job_id: str) -> None:
        with self.vault.entry_operations_locked():
            self.database.delete_finished_job(job_id)
            self.refresh_job_parents(job_id)

    def check_job_cancelled(self, job: JobRecord) -> None:
        current = self.database.get_job(job.id)
        if current.status == JobStatus.CANCELLED or current.artifacts.get("cancel_requested"):
            raise JobCancelledError("任务已请求取消")

    def finish_cancel_if_requested(self, job: JobRecord) -> JobRecord | None:
        current = self.database.get_job(job.id)
        if current.status == JobStatus.CANCELLED:
            return current
        if not current.artifacts.get("cancel_requested"):
            return None
        return self.database.update_job(
            current.id,
            status=JobStatus.CANCELLED,
            error_message="已取消",
            unlock=True,
        )

    def record_job_outcome(self, job: JobRecord, **changes) -> JobRecord:
        try:
            return self.database.update_job(job.id, **changes)
        except JobCancelledError:
            cancelled = self.finish_cancel_if_requested(job)
            if cancelled is None:
                raise
            return cancelled

    async def process_claimed_job(self, job: JobRecord) -> JobRecord:
        cancelled = self.finish_cancel_if_requested(job)
        if cancelled is not None:
            self.refresh_job_parents(cancelled.id)
            return cancelled
        try:
            if job.kind == "favorites_import":
                outcome = await self.favorites.process(job)
            elif job.kind == "collection_import":
                outcome = await self.collections.process(job)
            elif job.kind == "creator_import":
                outcome = await self.process_creator_import(job)
            elif job.kind == "reanalyze":
                outcome = await self.process_reanalysis(job)
            elif job.kind == "media_restore":
                outcome = await self.process_media_restore(job)
            else:
                outcome = await self.process_capture(job)
        except JobCancelledError:
            outcome = self.finish_cancel_if_requested(job)
        except JobLeaseLostError:
            raise
        except (BrowserAuthRequiredError, CookieRequiredError) as exc:
            is_video = isinstance(exc, CookieRequiredError)
            is_creator = job.kind == "creator_import"
            next_command = "douyin-wiki auth video" if is_video else "douyin-wiki auth douyin"
            auth_scope = "video" if is_video else ("creator" if is_creator else "image_note")
            if job.kind == "favorites_import":
                auth_scope = "favorites"
            elif job.kind == "collection_import":
                auth_scope = "collection"
            outcome = self.record_job_outcome(
                job,
                status=JobStatus.NEEDS_AUTH,
                error_code=exc.code,
                error_message=str(exc),
                result={
                    "reason": exc.code,
                    "auth_scope": auth_scope,
                    "next_command": next_command,
                    "retry_command": f"douyin-wiki jobs retry {job.id}",
                    "details": exc.details,
                },
                unlock=True,
            )
            if outcome.status == JobStatus.NEEDS_AUTH:
                with suppress(Exception):
                    self.auth_guidance_launcher.launch(
                        scope=auth_scope,
                        trigger_job_id=job.id,
                    )
        except DouyinWikiError as exc:
            outcome = self.record_job_outcome(
                job,
                status=JobStatus.FAILED,
                error_code=exc.code,
                error_message=str(exc),
                result={"details": exc.details},
                unlock=True,
            )
        except Exception as exc:  # noqa: BLE001 - worker must persist unexpected failures
            outcome = self.record_job_outcome(
                job,
                status=JobStatus.FAILED,
                error_code="internal_error",
                error_message=str(exc),
                unlock=True,
            )
        self.refresh_job_parents(outcome.id)
        if outcome.status in {JobStatus.COMPLETED, JobStatus.COMPLETED_WITH_WARNINGS}:
            entry_id = str(outcome.result.get("entry_id") or "")
            if entry_id:
                for creator_id in self.database.mark_capture_creator_links(outcome.id, entry_id):
                    self.refresh_creator_documents(creator_id, action="work-imported")
        creator_context = outcome.artifacts.get("creator_context")
        if creator_context:
            if outcome.status in {JobStatus.COMPLETED, JobStatus.COMPLETED_WITH_WARNINGS}:
                entry_id = str(outcome.result.get("entry_id") or "")
                if entry_id:
                    self.database.mark_creator_work_imported(
                        str(creator_context.get("id")),
                        str(creator_context.get("work_id")),
                        entry_id,
                    )
                    self.refresh_creator_documents(
                        str(creator_context.get("id")), action="work-imported"
                    )
            self.refresh_creator_parent(str(creator_context.get("parent_job_id") or ""))
        return outcome

    def prepare_entry_bundle(
        self,
        entry: EntryRecord,
        data: dict[str, Any],
        *,
        reminders: list[ReminderCandidate] | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[ReminderCandidate]]:
        self.indexer.ensure_embedding_compatibility()
        relations = self.indexer.build_relations(entry, data, persist=False)
        data["relations"] = relations
        chunks = self.indexer.index_entry(entry, data, persist=False)
        reminder_values = reminders or [
            ReminderCandidate.model_validate(item)
            for item in data.get("analysis", {}).get("reminders", [])
        ]
        return chunks, relations, reminder_values

    def write_entry_documents(
        self,
        entry: EntryRecord,
        data: dict[str, Any],
        *,
        action: str,
        log_summary: str,
        commit_message: str | None,
    ) -> list[Path]:
        entries = [item for item in self.database.list_entries() if item.id != entry.id]
        entries.append(entry)
        with self.vault.locked():
            written = self.vault.write_entry(entry, data)
            index = self.vault.rebuild_index(entries)
            changed = [*written.changed_paths, index]
            if not data.get("creator", {}).get("folder_path"):
                log = self.vault.append_log(
                    action,
                    entry.title,
                    log_summary,
                    [*written.changed_paths, index],
                )
                changed.append(log)
            if commit_message is not None:
                self.commit_vault(changed, commit_message)
            return changed

    def persist_entry_documents_and_bundle(
        self,
        entry: EntryRecord,
        data: dict[str, Any],
        chunks: list[dict[str, Any]],
        relations: list[dict[str, Any]],
        reminders: list[ReminderCandidate],
        *,
        action: str,
        log_summary: str,
        commit_message: str,
        require_existing: bool = False,
    ) -> EntryRecord:
        """Commit one entry mutation under the cross-process operation lock."""
        with self.vault.entry_operations_locked():
            return self.persist_entry_documents_and_bundle_locked(
                entry,
                data,
                chunks,
                relations,
                reminders,
                action=action,
                log_summary=log_summary,
                commit_message=commit_message,
                require_existing=require_existing,
            )

    def persist_entry_documents_and_bundle_locked(
        self,
        entry: EntryRecord,
        data: dict[str, Any],
        chunks: list[dict[str, Any]],
        relations: list[dict[str, Any]],
        reminders: list[ReminderCandidate],
        *,
        action: str,
        log_summary: str,
        commit_message: str,
        require_existing: bool = False,
    ) -> EntryRecord:
        """Persist an entry while the caller already owns the operation lock."""
        if require_existing:
            self.database.get_entry(entry.id)
        # Resolve again under the operation lock: a merge can finish while AI is running.
        tags = self.tags.resolve(entry.tags)
        entry = entry.model_copy(update={"tags": tags})
        data.setdefault("analysis", {})["tags"] = tags
        for chunk in chunks:
            chunk["tags_text"] = " ".join(tags)
        changed: list[Path] = []

        def publish() -> None:
            changed.extend(
                self.write_entry_documents(
                    entry,
                    data,
                    action=action,
                    log_summary=log_summary,
                    commit_message=None,
                )
                or []
            )

        with self.vault.reversible_writes():
            persisted = self.database.persist_entry_bundle(
                entry, data, chunks, relations, reminders, publish_documents=publish
            )
        self.commit_vault(changed, commit_message)
        return persisted

    def commit_vault(self, changed: list[Path], message: str) -> bool:
        if not (self.config.vault_path / ".git").exists():
            return True
        committed = self.vault.commit(changed, message)
        if not committed:
            warnings.warn(
                f"Vault 与 SQLite 已保存，但 Git 提交失败，文件保持为待提交状态：{message}",
                RuntimeWarning,
                stacklevel=2,
            )
        return committed

    def vault_path(self, value: str | Path) -> Path:
        path = Path(value)
        return path if path.is_absolute() else self.config.vault_path / path

    def vault_relative(self, path: Path) -> str:
        return str(path.resolve().relative_to(self.config.vault_path.resolve()))

    @staticmethod
    def ocr_model(value: dict[str, Any]):
        from .models import OCRObservation

        return OCRObservation.model_validate(value)

    def normalize_contradictions(
        self, values: list[dict[str, Any]], current_entry_id: str
    ) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for value in values:
            target_id = value.get("conflicts_with_entry_id")
            if not target_id or target_id == current_entry_id:
                continue
            try:
                target = self.database.get_entry(target_id)
            except EntryNotFoundError:  # reject hallucinated or removed entry ids
                continue
            normalized.append(
                {
                    **value,
                    "target_title": target.title,
                    "target_source_path": target.source_path,
                    "target_original_url": target.original_url,
                }
            )
        return normalized
