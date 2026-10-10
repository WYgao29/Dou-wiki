"""Collection inventories, explicit selection, and capture batch coordination."""

from __future__ import annotations

import asyncio
from collections import Counter

from .adapters.share import assert_collection_share
from .batch_capture import TERMINAL, active_capture_jobs
from .collection_store import CollectionStore, eligible
from .errors import JobCancelledError, JobStateError
from .models import CaptureOptions, CaptureRequest, JobStatus


class CollectionService:
    def __init__(self, core, adapter):
        self.core, self.database, self.adapter = core, core.database, adapter
        self.store = CollectionStore(core.database)

    def start(self, source_text, options=None, gateway_context=None):
        assert_collection_share(source_text)
        request = CaptureRequest(
            share_text=source_text,
            options=options or CaptureOptions(),
            gateway_context=gateway_context,
        )
        return self.database.get_job(self.store.create(request))

    async def process(self, job):
        run = self.store.load(job.id)
        if run["confirmed"]:
            return self.refresh(job.id)
        self.database.update_job(job.id, status=JobStatus.RESOLVING)
        resolved = await self.core.resolver.resolve_collection(job.request.share_text)
        self.database.update_job(
            job.id,
            status=JobStatus.INVENTORYING,
            artifacts={
                "collection_target": {
                    "mix_id": resolved.mix_id,
                    "canonical_url": resolved.canonical_url,
                    "original_url": resolved.original_url,
                },
            },
        )
        generation = self.store.begin_inventory(job.id)

        async def checkpoint(value):
            await asyncio.to_thread(self.store.checkpoint, job.id, generation, value)

        snapshot = await self.adapter.inventory(
            mix_id=resolved.mix_id,
            original_url=resolved.original_url,
            expected_account_id=run["snapshot"].get("account_id") or None,
            on_checkpoint=checkpoint,
        )
        await checkpoint(snapshot)
        data = self.get(job.id)
        empty = snapshot.complete and not snapshot.works
        return self.database.update_job(
            job.id,
            status=JobStatus.COMPLETED if empty else JobStatus.NEEDS_SELECTION,
            progress=1 if empty else 0,
            unlock=True,
            result={
                "summary": data["summary"],
                "complete": snapshot.complete,
                "warnings": snapshot.warnings,
                "inventory_generation": generation,
            },
        )

    def _entry_intact(self, row):
        return self.core._entry_documents_intact(self.database._entry_from_row(row))

    def _items(self, job_id, run):
        rows = self.store.rows(job_id)
        with self.database.connect() as conn:
            entries = {
                r["video_id"]: r
                for r in conn.execute("SELECT * FROM entries WHERE status='active'")
            }
            active = {} if run["confirmed"] else active_capture_jobs(conn)
            children = {
                r["id"]: self.database._job_from_row(r)
                for r in conn.execute(
                    "SELECT j.* FROM jobs j JOIN collection_run_items c ON "
                    "c.child_id=j.id WHERE c.parent_id=?",
                    (job_id,),
                )
            }
        result = []
        for row in rows:
            work = row["work"]
            entry = entries.get(row["work_id"])
            entry_id = entry["id"] if entry and self._entry_intact(entry) else None
            child = children.get(row["child_id"])
            disposition = "pending" if row["decision"] == "pending" else "skipped"
            can_import = eligible(work)
            conflict = False
            if work["source_kind"] in {"article", "unknown"}:
                disposition = "unsupported"
            elif not work["available"]:
                disposition = "unavailable"
            elif child:
                if child.artifacts.get("user_deleted"):
                    disposition = "deleted"
                elif child.status == JobStatus.CANCELLED:
                    disposition = "cancelled"
                elif child.status == JobStatus.FAILED:
                    disposition = "failed"
                elif child.status in TERMINAL:
                    disposition = "completed" if entry_id else "failed"
                else:
                    disposition = "active"
            elif entry_id:
                disposition = "imported"
            else:
                candidate = active.get(row["work_id"]) or active.get(work["canonical_url"])
                if candidate:
                    conflict = candidate.request.options != run["request"].options
                    child = candidate
                    disposition = "conflict" if conflict else "active"
                elif row["decision"] == "selected":
                    disposition = "selected"
            result.append(
                {
                    **work,
                    "ordinal": row["position"],
                    "decision": row["decision"],
                    "selected": row["decision"] == "selected" and can_import and not entry_id,
                    "disposition": disposition,
                    "can_import": can_import,
                    "entry_id": entry_id,
                    "job_id": child.id if child else None,
                    "child_id": row["child_id"],
                    "job_status": child.status.value if child else None,
                    "job_updated_at": child.updated_at.isoformat() if child else None,
                    "error_message": child.error_message if child else None,
                    "options_conflict": conflict,
                    "can_retry": bool(
                        child
                        and child.status == JobStatus.FAILED
                        and row["decision"] == "selected"
                        and not child.artifacts.get("user_deleted")
                        and can_import
                    ),
                }
            )
        return result

    @staticmethod
    def _summary(items):
        counts = Counter(i["disposition"] for i in items)
        summary = {
            name: counts[name]
            for name in (
                "pending",
                "selected",
                "skipped",
                "active",
                "imported",
                "completed",
                "failed",
                "cancelled",
                "deleted",
                "unsupported",
                "unavailable",
                "conflict",
            )
        }
        summary.update(
            discovered=len(items),
            eligible=sum(i["can_import"] for i in items),
            selected=sum(i["selected"] for i in items),
            in_library=sum(bool(i["entry_id"]) for i in items),
            new_tasks=sum(i["selected"] and not i["job_id"] for i in items),
            reused_tasks=sum(i["selected"] and bool(i["job_id"]) for i in items),
            retryable_failed=sum(i["can_retry"] for i in items),
        )
        summary["child_status_counts"] = dict(
            Counter(i["job_status"] for i in items if i["child_id"])
        )
        return summary

    def get(self, job_id, *, page=None, limit=None):
        if page is not None or limit is not None:
            page, limit = 1 if page is None else page, 50 if limit is None else limit
            if (
                type(page) is not int
                or type(limit) is not int
                or page < 1
                or not 1 <= limit <= 1000
            ):
                raise ValueError("page 必须大于零，limit 必须在 1 到 1000 之间")
        run = self.store.load(job_id)
        job = self.database.get_job(job_id)
        items = self._items(job_id, run)
        total = len(items)
        visible = items if page is None else items[(page - 1) * limit : page * limit]
        return {
            "job_id": job_id,
            "job_status": job.status.value,
            "profile": run["snapshot"].get("profile"),
            "complete": bool(run["snapshot"].get("complete")),
            "warnings": run["snapshot"].get("warnings", []),
            "confirmed": run["confirmed"],
            "accept_partial": run["accept_partial"],
            "generation": run["generation"],
            "works": visible,
            "total": total,
            "page": page,
            "limit": limit,
            "has_more": page is not None and page * limit < total,
            "summary": self._summary(items),
            "error_message": job.error_message,
        }

    def select(self, job_id, decision, *, ordinals=None, work_ids=None, all_works=False):
        self.store.select(
            job_id, decision, ordinals=ordinals, work_ids=work_ids, all_works=all_works
        )
        return self.get(job_id)

    def confirm(self, job_id, *, accept_partial=False):
        with self.core.vault.entry_operations_locked():
            self.store.confirm(
                job_id, accept_partial=accept_partial, entry_intact=self._entry_intact
            )
            return self.refresh(job_id)

    def refresh(self, job_id):
        run = self.store.load(job_id)
        current = self.database.get_job(job_id)
        if not run["confirmed"]:
            return current
        items = self._items(job_id, run)
        summary = self._summary(items)
        children = [i for i in items if i["child_id"]]
        active = any(JobStatus(i["job_status"]) not in TERMINAL for i in children)
        warnings = (
            not run["snapshot"].get("complete")
            or bool(run["snapshot"].get("warnings"))
            or any(
                i["disposition"] in {"failed", "cancelled", "deleted"}
                or i["job_status"] == JobStatus.COMPLETED_WITH_WARNINGS.value
                for i in children
            )
        )
        inactive = (
            current.status == JobStatus.CANCELLED
            or current.artifacts.get("cancel_requested")
            or current.artifacts.get("user_deleted")
        )
        status = (
            JobStatus.MONITORING
            if active
            else (JobStatus.COMPLETED_WITH_WARNINGS if warnings else JobStatus.COMPLETED)
        )
        result = {
            "summary": summary,
            "child_job_ids": [i["child_id"] for i in children],
            "complete": bool(run["snapshot"].get("complete")),
            "warnings": run["snapshot"].get("warnings", []),
            "accept_partial": run["accept_partial"],
        }
        if current.result == result and (inactive or current.status == status):
            return current
        try:
            return self.database.update_job(
                job_id,
                status=None if inactive else status,
                progress=None if inactive else (0 if active else 1),
                result=result,
                unlock=not inactive,
                expected_updated_at=current.updated_at,
                expected_child_updates={i["child_id"]: i["job_updated_at"] for i in children},
            )
        except (JobStateError, JobCancelledError):
            latest = self.database.get_job(job_id)
            if latest.updated_at != current.updated_at:
                return latest
            raise

    def refresh_for_child(self, child_id):
        for parent_id in self.store.parent_ids(child_id):
            self.refresh(parent_id)

    def refresh_all(self):
        for parent_id in self.store.parent_ids():
            self.refresh(parent_id)

    def retry_failed(self, job_id):
        with self.core.vault.entry_operations_locked():
            run = self.store.load(job_id)
            self.store._assert_mutable(run)
            if not run["confirmed"]:
                return self.core.retry_job(job_id)
            for item in self._items(job_id, run):
                if item["can_retry"]:
                    self.core.retry_job(item["child_id"])
            return self.refresh(job_id)

    def history(self, limit=50):
        if type(limit) is not int or limit < 1:
            raise ValueError("limit 必须为正整数")
        with self.database.connect() as conn:
            ids = [
                r[0]
                for r in conn.execute(
                    "SELECT c.parent_id FROM collection_runs c JOIN jobs j "
                    "ON j.id=c.parent_id WHERE NOT json_extract(j.artifacts_json,'$.user_deleted') "
                    "OR json_extract(j.artifacts_json,'$.user_deleted') IS NULL "
                    "ORDER BY j.created_at DESC LIMIT ?",
                    (limit,),
                )
            ]
        return [self.get(job_id, page=1, limit=1) for job_id in ids]
