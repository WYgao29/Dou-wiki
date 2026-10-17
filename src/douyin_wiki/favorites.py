"""Favorites inventory and explicit dispatch into the existing capture pipeline."""

from __future__ import annotations

import asyncio
from collections import Counter
from typing import TYPE_CHECKING

from .batch_capture import active_capture_jobs
from .errors import JobCancelledError, JobStateError
from .favorites_store import TERMINAL, FavoritesStore, eligible
from .models import CaptureRequest, JobStatus

if TYPE_CHECKING:
    from .service import DouyinWikiService

FAVORITES_URL = "https://www.douyin.com/user/self?showTab=favorite_collection"


class FavoritesService:
    def __init__(self, core: DouyinWikiService, adapter):
        self.core = core
        self.database = core.database
        self.adapter = adapter
        self.store = FavoritesStore(core.database)

    def start(
        self,
        *,
        folder_ids=None,
        include_images=False,
        directory_only=False,
        gateway_context=None,
        update_mode: str = "incremental",
        capture_options=None,
    ):
        if folder_ids is not None:
            if not folder_ids or any(not isinstance(v, str) or not v.strip() for v in folder_ids):
                raise ValueError("指定收藏夹时必须提供非空的收藏夹 ID 列表")
            folder_ids = list(dict.fromkeys(folder_ids))
        if directory_only and folder_ids:
            raise ValueError("读取目录不能同时指定收藏夹")
        if update_mode not in {"incremental", "full"}:
            raise ValueError("更新方式必须是 incremental 或 full")
        options = {
            "folder_ids": folder_ids,
            "include_images": bool(include_images),
            "directory_only": bool(directory_only),
            "update_mode": update_mode,
        }
        job_id = self.store.create(
            CaptureRequest(
                share_text=FAVORITES_URL,
                gateway_context=gateway_context,
                options=self.core.resolve_capture_options(capture_options),
            ),
            options,
        )
        return self.database.get_job(job_id)

    async def process(self, job):
        run = self.store.load(job.id)
        if run["confirmed"]:
            return self.refresh(job.id)
        self.database.update_job(job.id, status=JobStatus.INVENTORYING, progress=0)

        async def checkpoint(snapshot):
            await asyncio.to_thread(self.store.checkpoint, job.id, snapshot)

        snapshot = await self.adapter.inventory(
            folder_ids=run["options"].get("folder_ids"),
            directory_only=run["options"].get("directory_only", False),
            expected_account_id=run["snapshot"].get("account_id"),
            on_checkpoint=checkpoint,
        )
        await checkpoint(snapshot)
        directory_only = run["options"].get("directory_only")
        if directory_only:
            status = (
                JobStatus.COMPLETED
                if snapshot.folders_complete
                else JobStatus.COMPLETED_WITH_WARNINGS
            )
        else:
            status = JobStatus.NEEDS_SELECTION
        data = self.get(job.id)
        return self.database.update_job(
            job.id,
            status=status,
            progress=1 if directory_only else 0,
            result={
                "summary": data["summary"],
                "complete": data["complete"],
                "folders_complete": data["folders_complete"],
                "warnings": data["warnings"],
            },
            unlock=True,
        )

    def _items(self, job_id, run):
        rows = self.store.rows(job_id)
        with self.database.connect() as conn:
            entries = {
                r["video_id"]: r
                for r in conn.execute(
                    "SELECT e.* FROM entries e JOIN favorites_items f ON e.video_id=f.work_id "
                    "WHERE f.parent_id=?",
                    (job_id,),
                )
            }
            # Include globally queued captures so the preview agrees with dispatch-time dedupe.
            active = {} if run["confirmed"] else active_capture_jobs(conn)
            children = {
                r["id"]: self.database._job_from_row(r)
                for r in conn.execute(
                    """SELECT j.* FROM jobs j JOIN favorites_items f ON f.child_id=j.id
                       WHERE f.parent_id=?""",
                    (job_id,),
                )
            }
        items = []
        for row in rows:
            work = row["work"]
            child = children.get(row["child_id"])
            entry = entries.get(row["work_id"])
            entry_id = None
            if entry is not None and self._entry_intact(entry):
                entry_id = entry["id"]
            state = "pending"
            can_import = eligible(work, run["options"])
            conflict = False
            if work["source_kind"] == "article":
                state = "unsupported"
            elif not work.get("available", True):
                state = "unavailable"
            elif child is not None:
                if child.status in {JobStatus.FAILED, JobStatus.CANCELLED}:
                    state = "failed"
                elif child.status in TERMINAL:
                    state = "completed"
                    entry_id = child.result.get("entry_id") or entry_id
                else:
                    state = "active"
            elif entry_id:
                state = "imported"
            elif not can_import or not row["selected"]:
                state = "excluded"
            elif not run["confirmed"]:
                child = active.get(row["work_id"]) or active.get(work["canonical_url"])
                if child:
                    conflict = (
                        child.request.options.processing_key()
                        != run["request"].options.processing_key()
                    )
                    state = "conflict" if conflict else "active"
            item = {
                **work,
                "selected": bool(row["selected"]) and can_import,
                "disposition": state,
                "job_id": child.id if child else None,
                "entry_id": entry_id,
                "error_message": (
                    f"作品 {row['work_id']} 已有不同处理选项的活动任务"
                    if conflict
                    else child.error_message
                    if child
                    else None
                ),
                "options_conflict": conflict,
                "job_status": child.status.value if child else None,
                "job_updated_at": child.updated_at.isoformat() if child else None,
                "can_retry": bool(
                    child
                    and child.status == JobStatus.FAILED
                    and not child.artifacts.get("user_deleted")
                    and row["selected"]
                    and can_import
                ),
            }
            items.append(item)
        return items

    def _entry_intact(self, row) -> bool:
        return self.core._entry_documents_intact(self.database._entry_from_row(row))

    @staticmethod
    def _summary(items, options):
        counts = Counter(i["disposition"] for i in items)
        result = {
            name: counts[name]
            for name in (
                "imported",
                "active",
                "completed",
                "failed",
                "excluded",
                "unsupported",
                "unavailable",
                "conflict",
            )
        }
        result.update(
            discovered=len(items),
            in_library=sum(bool(i.get("entry_id")) for i in items),
            eligible=sum(eligible(i, options) for i in items),
            selected=sum(i["selected"] and i["disposition"] != "imported" for i in items),
        )
        result["child_status_counts"] = dict(
            Counter(i["job_status"] for i in items if i["job_id"] and i["selected"])
        )
        result["retryable_failed"] = sum(i["can_retry"] for i in items)
        return result

    def get(
        self,
        job_id: str,
        *,
        page=1,
        limit=50,
        folder_id=None,
        query="",
        only_new=False,
        only_unimported=False,
    ):
        if page < 1 or not 1 <= limit <= 1000:
            raise ValueError("page 必须大于零，limit 必须在 1 到 1000 之间")
        run = self.store.load(job_id)
        job = self.database.get_job(job_id)
        items = self._items(job_id, run)
        previous_ids = self.store.previous_work_ids(job_id)
        for item in items:
            item["is_new"] = item["work_id"] not in previous_ids
        summary = self._summary(items, run["options"])
        summary["new"] = sum(1 for item in items if item.get("is_new"))
        chosen = [i for i in items if i["selected"] and not i["entry_id"]]
        reused = sum(bool(i["job_id"]) and i["disposition"] == "active" for i in chosen)
        saved_entries = {}
        if run["confirmed"]:
            job = self._refresh_with(job_id, run, items, summary)
            # Batch membership survives raw-cache removal; the catalog handles missing sources.
            with self.database.connect() as conn:
                saved_entries = {
                    row["work_id"]: row["entry_id"]
                    for row in conn.execute(
                        "SELECT work_id,entry_id FROM favorites_items "
                        "WHERE parent_id=? AND selected=1 AND entry_id IS NOT NULL",
                        (job_id,),
                    )
                }
        filtered = self._filter_items(
            items,
            folder_id=folder_id,
            query=query,
            only_new=only_new,
            only_unimported=only_unimported,
        )
        snapshot = run["snapshot"]
        inheritance = snapshot.get("_selection_inheritance", {})
        selectable_ids = {
            i["work_id"] for i in items if not i["entry_id"] and eligible(i, run["options"])
        }
        selection_inheritance = {
            "selected": len(selectable_ids.intersection(inheritance.get("selected_work_ids", []))),
            "ignored_legacy": len(
                selectable_ids.intersection(inheritance.get("ignored_legacy_ids", []))
            ),
        }
        start = (page - 1) * limit
        return {
            "job_id": job_id,
            "status": job.status.value,
            "progress": job.progress,
            "created_at": job.created_at.isoformat(),
            "updated_at": job.updated_at.isoformat(),
            "first_read": not previous_ids,
            "selection_inheritance": selection_inheritance,
            **run["options"],
            "account_id": snapshot.get("account_id"),
            "nickname": snapshot.get("nickname", ""),
            "complete": snapshot.get("complete", False),
            "folders_complete": snapshot.get("folders_complete", False),
            "warnings": snapshot.get("warnings", []),
            "folders": snapshot.get("folders", []),
            "items": filtered[start : start + limit],
            "total": len(filtered),
            "page": page,
            "limit": limit,
            "has_more": start + limit < len(filtered),
            "summary": summary,
            "confirmation_preview": {
                "selected": len(chosen),
                "new_tasks": len(chosen) - reused - sum(i["options_conflict"] for i in chosen),
                "reused_tasks": reused,
                "already_imported": summary["in_library"],
                "source_kinds": dict(Counter(i["source_kind"] for i in chosen)),
            },
            "imported_entry_ids": list(
                dict.fromkeys(
                    i["entry_id"] or saved_entries.get(i["work_id"])
                    for i in items
                    if run["confirmed"]
                    and i["selected"]
                    and (i["entry_id"] or saved_entries.get(i["work_id"]))
                )
            ),
            "analysis_mode": self.core.config.analysis_mode.value,
            "error_message": job.error_message,
            "error_code": job.error_code,
            "auth_scope": job.result.get("auth_scope"),
            "confirmed": run["confirmed"],
            "cancel_requested": bool(job.artifacts.get("cancel_requested")),
            "can_retry": bool(
                job.status != JobStatus.CANCELLED
                and not job.artifacts.get("cancel_requested")
                and not job.artifacts.get("user_deleted")
                and (
                    summary["retryable_failed"]
                    if run["confirmed"]
                    else job.status in {JobStatus.FAILED, JobStatus.NEEDS_AUTH}
                )
            ),
        }

    def history(self, *, limit=50):
        if not 1 <= limit <= 1000:
            raise ValueError("limit 必须在 1 到 1000 之间")
        return [
            self.get(job_id, limit=1)
            for job_id in self.store.parent_ids(limit=limit)
            if not self.database.get_job(job_id).artifacts.get("user_deleted")
        ]

    def select(self, job_id: str, *, selected: bool, work_ids=None, folder_id=None):
        self.store.select(job_id, selected=selected, work_ids=work_ids, folder_id=folder_id)
        return self.get(job_id)

    @staticmethod
    def _filter_items(items, *, folder_id=None, query="", only_new=False, only_unimported=False):
        term = query.casefold()
        return [
            i
            for i in items
            if (not folder_id or folder_id in i.get("folder_ids", []))
            and (not term or term in (i["title"] + " " + i["author"]).casefold())
            and (not only_new or i.get("is_new"))
            and (not only_unimported or not i["entry_id"])
        ]

    def select_filtered(
        self,
        job_id: str,
        *,
        selected: bool,
        folder_id=None,
        query="",
        only_new=False,
        only_unimported=False,
    ):
        # The store validates eligibility and applies the whole selection in one transaction.
        run = self.store.load(job_id)
        items = self._items(job_id, run)
        previous_ids = self.store.previous_work_ids(job_id)
        for item in items:
            item["is_new"] = item["work_id"] not in previous_ids
        filtered = self._filter_items(
            items,
            folder_id=folder_id,
            query=query,
            only_new=only_new,
            only_unimported=only_unimported,
        )
        work_ids = [
            i["work_id"]
            for i in filtered
            if not selected or (eligible(i, run["options"]) and not i["entry_id"])
        ]
        return self.select(job_id, selected=selected, work_ids=work_ids, folder_id=folder_id)

    def confirm(self, job_id: str, *, accept_partial=False):
        with self.core.vault.entry_operations_locked():
            self.store.confirm(
                job_id, accept_partial=accept_partial, entry_intact=self._entry_intact
            )
            return self.refresh(job_id)

    def _refresh_with(self, job_id, run, items, summary):
        current = self.database.get_job(job_id)
        if not run["confirmed"]:
            return current
        inactive = (
            current.status == JobStatus.CANCELLED
            or current.artifacts.get("cancel_requested")
            or current.artifacts.get("user_deleted")
        )
        children = [i for i in items if i["job_id"] and i["selected"]]
        finished = sum(i["disposition"] in {"failed", "completed"} for i in children)
        warnings = (
            not run["snapshot"].get("complete")
            or not run["snapshot"].get("folders_complete")
            or bool(run["snapshot"].get("warnings"))
            or summary["failed"] > 0
            or any(i["job_status"] == JobStatus.COMPLETED_WITH_WARNINGS.value for i in children)
        )
        if finished < len(children):
            status = JobStatus.MONITORING
            progress = finished / len(children)
        else:
            status = JobStatus.COMPLETED_WITH_WARNINGS if warnings else JobStatus.COMPLETED
            progress = 1
        result = {
            "summary": summary,
            "child_status_counts": summary["child_status_counts"],
            "complete": run["snapshot"].get("complete", False),
            "warnings": run["snapshot"].get("warnings", []),
            "accept_partial": run["accept_partial"],
            "child_job_ids": [i["job_id"] for i in children],
        }
        if inactive:
            status, progress = current.status, current.progress
            result = {**current.result, **result}
        if current.status == status and current.result == result and current.progress == progress:
            return current
        try:
            return self.database.update_job(
                job_id,
                status=None if inactive else status,
                progress=None if inactive else progress,
                result=result,
                unlock=not inactive,
                expected_updated_at=current.updated_at,
                expected_child_updates={i["job_id"]: i["job_updated_at"] for i in children},
            )
        except JobStateError:
            # Cancellation, deletion or another refresh can invalidate this snapshot.
            # Preserve the newer parent instead of failing the worker's polling loop.
            latest = self.database.get_job(job_id)
            if latest.updated_at != current.updated_at:
                return latest
            raise

    def refresh(self, job_id: str):
        run = self.store.load(job_id)
        items = self._items(job_id, run)
        return self._refresh_with(job_id, run, items, self._summary(items, run["options"]))

    def refresh_all(self) -> None:
        for job_id in self.store.parent_ids(needs_refresh=True):
            self.refresh(job_id)

    def refresh_for_child(self, child_id: str) -> None:
        for job_id in self.store.parent_ids(child_id=child_id):
            self.refresh(job_id)

    def retry_failed(self, job_id: str):
        with self.core.vault.entry_operations_locked():
            parent = self.database.get_job(job_id)
            if parent.artifacts.get("user_deleted"):
                raise JobStateError("任务已删除")
            if parent.status == JobStatus.CANCELLED or parent.artifacts.get("cancel_requested"):
                raise JobCancelledError("任务已取消或已请求取消，不能重试")
            run = self.store.load(job_id)
            if not run["confirmed"]:
                return self.core.retry_job(job_id)
            for item in self._items(job_id, run):
                if not item["can_retry"]:
                    continue
                try:
                    self.core.retry_job(item["job_id"])
                except JobStateError:
                    latest = self.database.get_job(item["job_id"])
                    if latest.status == JobStatus.FAILED and not latest.artifacts.get(
                        "user_deleted"
                    ):
                        raise
            return self.refresh(job_id)
