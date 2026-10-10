"""Persist shared capture callers after short links resolve to a stable work ID."""

from __future__ import annotations

import json
from contextlib import suppress

from .batch_capture import TERMINAL, capture_keys
from .errors import JobCancelledError, JobStateError
from .models import JobStatus
from .time_utils import iso_now


def live(job):
    return (
        job.status not in TERMINAL
        and not job.artifacts.get("user_deleted")
        and not job.artifacts.get("cancel_requested")
    )


def batch_owned(job):
    return any(
        (job.artifacts.get(key) or {}).get("parent_job_id")
        for key in ("creator_context", "favorites_context", "collection_context")
    )


class CaptureSharing:
    def __init__(self, core):
        self.core = core
        self.database = core.database

    def join_if_active(self, job_id, work_id):
        """Called under the work lock, before any media or analysis work."""
        with self.core.vault.entry_operations_locked(), self.database.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self.database.assert_job_claim(conn, job_id)
            current = self.database._job_from_row(
                conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            )
            if not live(current):
                raise JobCancelledError("任务已请求取消")
            # Resuming a checkpoint must keep its own media and Gateway ownership.
            if current.artifacts.get("metadata"):
                return None
            matching = [
                job for row in conn.execute(
                    "SELECT * FROM jobs WHERE kind='capture' ORDER BY created_at,id"
                )
                if live(job := self.database._job_from_row(row))
                and not job.artifacts.get("shared_capture_job_id")
                and work_id in capture_keys(job.request, job.artifacts)
            ]
            # An existing Gateway pause takes priority even if its caller was newer.
            owner = next((job for job in matching if job.artifacts.get("metadata")), None)
            owner = owner or (matching[0] if matching else current)
            if owner.id == job_id:
                return None
            if owner.request.options != current.request.options:
                raise JobStateError(f"作品 {work_id} 已有不同处理选项的活动任务 {owner.id}")
            artifacts = {**current.artifacts, "shared_capture_job_id": owner.id}
            result = {
                "shared_capture_job_id": owner.id,
                "shared_job_status": owner.status.value,
                "reason": "shared_capture",
                "message": "等待正在处理的同一作品",
            }
            conn.execute(
                "UPDATE jobs SET status=?,progress=?,artifacts_json=?,result_json=?,updated_at=?,"
                "locked_at=NULL,lock_owner=NULL,lease_expires_at=NULL WHERE id=?",
                (
                    JobStatus.MONITORING.value, owner.progress,
                    json.dumps(artifacts, ensure_ascii=False),
                    json.dumps(result, ensure_ascii=False), iso_now(), job_id,
                ),
            )
            # Flatten existing waiters if a queued executor itself joins a paused job.
            for row in conn.execute(
                "SELECT * FROM jobs WHERE kind='capture' "
                "AND json_extract(artifacts_json,'$.shared_capture_job_id')=?", (job_id,),
            ).fetchall():
                follower = self.database._job_from_row(row)
                if not live(follower):
                    continue
                follower.artifacts["shared_capture_job_id"] = owner.id
                follower.result.update(result)
                conn.execute(
                    "UPDATE jobs SET artifacts_json=?,result_json=?,updated_at=? WHERE id=?",
                    (json.dumps(follower.artifacts, ensure_ascii=False),
                     json.dumps(follower.result, ensure_ascii=False), iso_now(), follower.id),
                )
            self._repoint_batches(conn, job_id, owner.id)
        return self.database.get_job(job_id)

    @staticmethod
    def _repoint_batches(conn, old_id, owner_id):
        for table, column in (
            ("collection_run_items", "child_id"), ("favorites_items", "child_id"),
            ("creator_run_items", "child_job_id"), ("creator_works", "last_job_id"),
        ):
            conn.execute(f"UPDATE {table} SET {column}=? WHERE {column}=?", (owner_id, old_id))
        # Creator aggregation also supports historical result-only associations.
        for row in conn.execute(
            "SELECT id,result_json FROM jobs "
            "WHERE kind IN ('creator_import','favorites_import','collection_import')"
        ).fetchall():
            result = json.loads(row["result_json"])
            ids = result.get("child_job_ids", [])
            if old_id not in ids:
                continue
            result["child_job_ids"] = list(dict.fromkeys(
                owner_id if child == old_id else child for child in ids
            ))
            conn.execute(
                "UPDATE jobs SET result_json=?,updated_at=? WHERE id=?",
                (json.dumps(result, ensure_ascii=False), iso_now(), row["id"]),
            )

    def followers(self, owner_id):
        with self.database.connect() as conn:
            return [
                self.database._job_from_row(row) for row in conn.execute(
                    "SELECT * FROM jobs WHERE kind='capture' "
                    "AND json_extract(artifacts_json,'$.shared_capture_job_id')=?", (owner_id,),
                )
            ]

    def has_independent_waiters(self, owner_id):
        return any(live(job) and not batch_owned(job) for job in self.followers(owner_id))

    def cancel_batch_followers(self, parent_id):
        """Retract the batch's callers after its relations point at another executor.

        The caller holds the entry operations lock so completion cannot revive them.
        """
        with self.database.connect() as conn:
            followers = [self.database._job_from_row(row) for row in conn.execute(
                "SELECT * FROM jobs WHERE kind='capture' "
                "AND json_extract(artifacts_json,'$.shared_capture_job_id') IS NOT NULL "
                "AND (json_extract(artifacts_json,'$.creator_context.parent_job_id')=? "
                "OR json_extract(artifacts_json,'$.favorites_context.parent_job_id')=? "
                "OR json_extract(artifacts_json,'$.collection_context.parent_job_id')=?)",
                (parent_id, parent_id, parent_id),
            )]
        for follower in followers:
            if live(follower):
                with suppress(JobStateError):
                    self.database.request_cancel(follower.id)

    def inspirations(self, owner):
        # Merge while the executor still owns its lease, before publishing its entry.
        result = list(owner.request.inspirations)
        for follower in self.followers(owner.id):
            if live(follower):
                for inspiration in follower.request.inspirations:
                    if inspiration not in result:
                        result.append(inspiration)
        return result

    def apply_inspirations(self, owner, entry, data):
        inspirations = list(entry.inspirations)
        for inspiration in self.inspirations(owner):
            if inspiration not in inspirations:
                inspirations.append(inspiration)
        data["inspirations"] = [item.model_dump(mode="json") for item in inspirations]
        return entry.model_copy(update={"inspirations": inspirations})

    def cancel_abandoned_batch_owner(self, owner_id):
        owner = self.database.get_job(owner_id)
        if not live(owner) or not batch_owned(owner) or self.has_independent_waiters(owner_id):
            return False
        parents = self.database.capture_parent_ids(owner_id)
        if any(live(self.database.get_job(pid)) for pid in parents):
            return False
        self.database.request_cancel(owner_id)
        return True

    def refresh(self, job_id):
        with self.core.vault.entry_operations_locked():
            job = self.database.get_job(job_id)
            owner_id = job.artifacts.get("shared_capture_job_id")
            if not owner_id or not live(job):
                return job
            owner = self.database.get_job(owner_id)
            if owner.status not in TERMINAL:
                job.result["shared_job_status"] = owner.status.value
                return job
            result = {
                "shared_capture_job_id": owner_id,
                "shared_job_status": owner.status.value,
            }
            if owner.status in {JobStatus.COMPLETED, JobStatus.COMPLETED_WITH_WARNINGS}:
                entry_id = owner.result.get("entry_id")
                if entry_id:
                    for inspiration in job.request.inspirations:
                        self.core.add_inspiration_locked(entry_id, inspiration)
                result.update({
                    "entry_id": entry_id, "source_path": owner.result.get("source_path"),
                    "duplicate": True,
                })
            with suppress(JobStateError, JobCancelledError):
                return self.database.update_job(
                    job_id, status=owner.status, progress=1, result=result,
                    error_code=owner.error_code or "", error_message=owner.error_message or "",
                    expected_updated_at=job.updated_at,
                    expected_child_updates={owner.id: owner.updated_at.isoformat()}, unlock=True,
                )
            return self.database.get_job(job_id)

    def refresh_for_owner(self, owner_id):
        for follower in self.followers(owner_id):
            self.refresh(follower.id)

    def refresh_all(self):
        with self.database.connect() as conn:
            ids = [row[0] for row in conn.execute(
                "SELECT id FROM jobs WHERE kind='capture' AND status=? "
                "AND json_extract(artifacts_json,'$.shared_capture_job_id') IS NOT NULL",
                (JobStatus.MONITORING.value,),
            )]
        for job_id in ids:
            self.refresh(job_id)
