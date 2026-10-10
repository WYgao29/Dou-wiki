"""Collection knowledge projections and generation-scoped operation history."""

from __future__ import annotations

import json
import re

from .batch_capture import TERMINAL, active_capture_jobs
from .collection_models import CollectionInventory, CollectionProfile, CollectionWork
from .errors import BrowserAuthRequiredError, EntryNotFoundError, JobCancelledError, JobStateError
from .models import CaptureRequest, JobStatus
from .time_utils import iso_now

COLLECTION_SCHEMA = """
CREATE TABLE IF NOT EXISTS collections (
    mix_id TEXT PRIMARY KEY,
    metadata_json TEXT NOT NULL,
    folder_path TEXT NOT NULL UNIQUE,
    last_complete_at TEXT
);
CREATE TABLE IF NOT EXISTS collection_works (
    mix_id TEXT NOT NULL REFERENCES collections(mix_id) ON DELETE CASCADE,
    work_id TEXT NOT NULL,
    data_json TEXT NOT NULL,
    position INTEGER NOT NULL,
    present_in_last_complete INTEGER NOT NULL DEFAULT 0,
    entry_id TEXT REFERENCES entries(id) ON DELETE SET NULL,
    PRIMARY KEY(mix_id,work_id)
);
CREATE TABLE IF NOT EXISTS collection_runs (
    parent_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    mix_id TEXT,
    generation INTEGER NOT NULL DEFAULT 0,
    snapshot_json TEXT NOT NULL DEFAULT '{}',
    confirmed INTEGER NOT NULL DEFAULT 0,
    accept_partial INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS collection_run_items (
    parent_id TEXT NOT NULL REFERENCES collection_runs(parent_id) ON DELETE CASCADE,
    work_id TEXT NOT NULL,
    generation INTEGER NOT NULL,
    position INTEGER NOT NULL,
    data_json TEXT NOT NULL,
    decision TEXT NOT NULL DEFAULT 'pending' CHECK(decision IN ('pending','selected','skipped')),
    child_id TEXT REFERENCES jobs(id) ON DELETE SET NULL,
    entry_id TEXT REFERENCES entries(id) ON DELETE SET NULL,
    PRIMARY KEY(parent_id,work_id)
);
CREATE INDEX IF NOT EXISTS idx_collection_items_child ON collection_run_items(child_id);
"""


def eligible(work: dict) -> bool:
    return bool(work.get("available", True) and work["source_kind"] in {"video", "image_note"})


class CollectionStore:
    def __init__(self, database):
        self.database = database

    @staticmethod
    def _run(conn, job_id):
        row = conn.execute(
            "SELECT c.*,j.status,j.request_json,j.artifacts_json FROM collection_runs c "
            "JOIN jobs j ON j.id=c.parent_id WHERE c.parent_id=? AND j.kind='collection_import'",
            (job_id,),
        ).fetchone()
        if row is None:
            if conn.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone() is None:
                raise JobStateError("job not found")
            raise JobStateError("该任务不是合集导入任务")
        return {
            **dict(row),
            "snapshot": json.loads(row["snapshot_json"]),
            "request": CaptureRequest.model_validate_json(row["request_json"]),
            "artifacts": json.loads(row["artifacts_json"]),
            "confirmed": bool(row["confirmed"]),
            "accept_partial": bool(row["accept_partial"]),
        }

    @staticmethod
    def _assert_mutable(run):
        if run["status"] == JobStatus.CANCELLED.value or run["artifacts"].get("cancel_requested"):
            raise JobCancelledError("合集导入已取消")
        if run["artifacts"].get("user_deleted"):
            raise JobStateError("合集导入已删除")

    def load(self, job_id):
        with self.database.connect() as conn:
            return self._run(conn, job_id)

    def create(self, request):
        with self.database.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            job_id = self.database._insert_job_conn(conn, request, kind="collection_import")
            conn.execute("INSERT INTO collection_runs(parent_id) VALUES (?)", (job_id,))
        return job_id

    def begin_inventory(self, job_id):
        with self.database.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self.database.assert_job_claim(conn, job_id)
            run = self._run(conn, job_id)
            self._assert_mutable(run)
            if run["confirmed"]:
                raise JobStateError("已确认的清单不能重新清点，请新建批次")
            generation = run["generation"] + 1
            history = run["snapshot"].get("history", [])
            if run["snapshot"].get("profile"):
                history = [
                    *history[-19:],
                    {
                        "generation": run["generation"],
                        "complete": run["snapshot"].get("complete", False),
                        "warnings": run["snapshot"].get("warnings", []),
                    },
                ]
            payload = {
                "complete": False,
                "history": history,
                "account_id": run["snapshot"].get("account_id", ""),
            }
            conn.execute(
                "UPDATE collection_runs SET generation=?,snapshot_json=? WHERE parent_id=?",
                (generation, json.dumps(payload), job_id),
            )
        return generation

    def checkpoint(self, job_id, generation, inventory: CollectionInventory):
        with self.database.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self.database.assert_job_claim(conn, job_id)
            run = self._run(conn, job_id)
            self._assert_mutable(run)
            if run["confirmed"] or run["generation"] != generation:
                raise JobStateError("清点世代已变化，不能发布旧清单")
            target = run["artifacts"].get("collection_target", {}).get("mix_id")
            if target != inventory.profile.mix_id:
                raise JobStateError("合集清单与提交目标不一致")
            previous = run["snapshot"].get("account_id")
            if previous and previous != inventory.account_id:
                raise BrowserAuthRequiredError("专用浏览器账号已变化，请恢复原账号或新建批次")
            payload = inventory.model_dump(mode="json")
            works = payload.pop("works")
            payload["history"] = run["snapshot"].get("history", [])
            conn.execute(
                "UPDATE collection_runs SET mix_id=?,snapshot_json=? WHERE parent_id=?",
                (inventory.profile.mix_id, json.dumps(payload, ensure_ascii=False), job_id),
            )
            for work in works:
                conn.execute(
                    "INSERT INTO "
                    "collection_run_items(parent_id,work_id,generation,position,data_json) "
                    "VALUES (?,?,?,?,?) ON CONFLICT(parent_id,work_id) DO UPDATE SET "
                    "generation=excluded.generation,position=excluded.position,data_json=excluded.data_json",
                    (
                        job_id,
                        work["work_id"],
                        generation,
                        work["position"],
                        json.dumps(work, ensure_ascii=False),
                    ),
                )

    def rows(self, job_id):
        with self.database.connect() as conn:
            run = self._run(conn, job_id)
            return [
                {**dict(r), "work": json.loads(r["data_json"])}
                for r in conn.execute(
                    "SELECT * FROM collection_run_items WHERE parent_id=? AND "
                    "generation=? ORDER BY position",
                    (job_id, run["generation"]),
                )
            ]

    def select(self, job_id, decision, *, ordinals=None, work_ids=None, all_works=False):
        if decision not in {"selected", "skipped"}:
            raise ValueError("decision 必须是 selected 或 skipped")
        if sum(bool(v) for v in (ordinals, work_ids, all_works)) != 1:
            raise ValueError("请使用一种选择方式：编号、作品 ID 或全部作品")
        with self.database.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            run = self._run(conn, job_id)
            self._assert_mutable(run)
            if run["confirmed"] or run["status"] != JobStatus.NEEDS_SELECTION.value:
                raise JobStateError("清点结束且未提交的合集才能修改选择")
            rows = conn.execute(
                "SELECT * FROM collection_run_items WHERE parent_id=? AND generation=?",
                (job_id, run["generation"]),
            ).fetchall()
            selected = [
                r
                for r in rows
                if all_works
                or r["work_id"] in (work_ids or [])
                or r["position"] in (ordinals or [])
            ]
            if not all_works and len(selected) != len(set(work_ids or ordinals)):
                raise JobStateError("有作品不在本次清单中")
            for row in selected:
                if not eligible(json.loads(row["data_json"])):
                    if all_works:
                        continue
                    raise JobStateError("作品不可用或暂不支持导入")
                conn.execute(
                    "UPDATE collection_run_items SET decision=? WHERE parent_id=? AND work_id=?",
                    (decision, job_id, row["work_id"]),
                )

    def confirm(self, job_id, *, accept_partial=False, entry_intact):
        with self.database.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            run = self._run(conn, job_id)
            self._assert_mutable(run)
            if run["confirmed"]:
                return
            if run["status"] != JobStatus.NEEDS_SELECTION.value:
                raise JobStateError("清点结束后才能确认导入")
            if not run["snapshot"].get("complete") and not accept_partial:
                raise JobStateError("清单未完整读取；部分导入需显式 accept_partial=true")
            rows = conn.execute(
                "SELECT * FROM collection_run_items WHERE parent_id=? AND generation=? "
                "ORDER BY position",
                (job_id, run["generation"]),
            ).fetchall()
            active, plans = active_capture_jobs(conn), []
            for row in rows:
                work = json.loads(row["data_json"])
                existing = conn.execute(
                    "SELECT * FROM entries WHERE video_id=? AND status='active'", (row["work_id"],)
                ).fetchone()
                intact = bool(existing and entry_intact(existing))
                if eligible(work) and not intact and row["decision"] == "pending":
                    raise JobStateError("仍有未决定作品，请先选择或跳过全部作品")
                if intact:
                    plans.append((row, work, existing["id"], None))
                elif row["decision"] == "selected" and eligible(work):
                    child = active.get(row["work_id"]) or active.get(work["canonical_url"])
                    if child and child.request.options != run["request"].options:
                        raise JobStateError(f"作品 {row['work_id']} 已有不同处理选项的活动任务")
                    plans.append((row, work, None, child))
            child_ids = []
            for row, work, entry_id, child in plans:
                child_id = None
                if not entry_id:
                    child_id = (
                        child.id
                        if child
                        else self.database._insert_job_conn(
                            conn,
                            CaptureRequest(
                                share_text=work["canonical_url"],
                                options=run["request"].options,
                                gateway_context=run["request"].gateway_context,
                            ),
                            artifacts={
                                "collection_context": {
                                    "collection_id": run["mix_id"],
                                    "collection_name": run["snapshot"]["profile"]["name"],
                                    "parent_job_id": job_id,
                                    "work_id": row["work_id"],
                                    "ordinal": row["position"],
                                    "batch_silent": True,
                                }
                            },
                        )
                    )
                    child_ids.append(child_id)
                conn.execute(
                    "UPDATE collection_run_items SET child_id=?,entry_id=? WHERE "
                    "parent_id=? AND work_id=?",
                    (child_id, entry_id, job_id, row["work_id"]),
                )
            conn.execute(
                "UPDATE collection_runs SET confirmed=1,accept_partial=? WHERE parent_id=?",
                (int(accept_partial), job_id),
            )
            conn.execute(
                "UPDATE jobs SET status=?,result_json=?,updated_at=? WHERE id=?",
                (
                    JobStatus.MONITORING.value,
                    json.dumps(
                        {"child_job_ids": child_ids, "accept_partial": bool(accept_partial)}
                    ),
                    iso_now(),
                    job_id,
                ),
            )

    def parent_ids(self, child_id=None):
        with self.database.connect() as conn:
            if child_id:
                return [
                    r[0]
                    for r in conn.execute(
                        "SELECT DISTINCT parent_id FROM collection_run_items WHERE child_id=?",
                        (child_id,),
                    )
                ]
            return [
                r[0]
                for r in conn.execute("SELECT parent_id FROM collection_runs WHERE confirmed=1")
            ]

    @staticmethod
    def publish_inventory_conn(conn, inventory):
        profile = inventory.profile
        previous = conn.execute(
            "SELECT folder_path,last_complete_at FROM collections WHERE mix_id=?", (profile.mix_id,)
        ).fetchone()
        name = re.sub(r"[^\w\-\u4e00-\u9fff]+", "-", profile.name).strip("-_")[:60] or "合集"
        folder = previous["folder_path"] if previous else f"collections/{name}_{profile.mix_id}"
        complete_at = (
            iso_now()
            if inventory.complete
            else (previous["last_complete_at"] if previous else None)
        )
        persisted = CollectionProfile.model_validate(
            {**profile.model_dump(), "folder_path": folder, "last_complete_at": complete_at}
        )
        conn.execute(
            "INSERT INTO collections(mix_id,metadata_json,folder_path,last_complete_at) "
            "VALUES (?,?,?,?) "
            "ON CONFLICT(mix_id) DO UPDATE SET "
            "metadata_json=excluded.metadata_json,last_complete_at=excluded.last_complete_at",
            (profile.mix_id, persisted.model_dump_json(), folder, complete_at),
        )
        if inventory.complete:
            conn.execute(
                "UPDATE collection_works SET present_in_last_complete=0 WHERE mix_id=?",
                (profile.mix_id,),
            )
        for work in inventory.works:
            old = conn.execute(
                "SELECT data_json FROM collection_works WHERE mix_id=? AND work_id=?",
                (profile.mix_id, work.work_id),
            ).fetchone()
            work = work.model_copy(
                update={
                    "ever_present_in_complete": bool(
                        inventory.complete
                        or (old and json.loads(old[0]).get("ever_present_in_complete"))
                    )
                }
            )
            conn.execute(
                "INSERT INTO "
                "collection_works(mix_id,work_id,data_json,position,present_in_last_complete) "
                "VALUES (?,?,?,?,?) "
                "ON CONFLICT(mix_id,work_id) DO UPDATE SET data_json=excluded.data_json,"
                "position=CASE WHEN ? THEN excluded.position ELSE collection_works.position END,"
                "present_in_last_complete=CASE WHEN ? THEN 1 ELSE "
                "collection_works.present_in_last_complete END",
                (
                    profile.mix_id,
                    work.work_id,
                    work.model_dump_json(),
                    work.position,
                    int(inventory.complete),
                    int(inventory.complete),
                    int(inventory.complete),
                ),
            )
        return persisted

    @staticmethod
    def bundle_conn(conn, mix_id):
        row = conn.execute("SELECT * FROM collections WHERE mix_id=?", (mix_id,)).fetchone()
        if row is None:
            raise EntryNotFoundError("合集不存在")
        profile = CollectionProfile.model_validate_json(row["metadata_json"])
        works = [
            CollectionWork.model_validate(
                {
                    **json.loads(r["data_json"]),
                    "position": r["position"],
                    "present_in_last_complete": bool(r["present_in_last_complete"]),
                    "entry_id": r["entry_id"],
                }
            )
            for r in conn.execute(
                "SELECT * FROM collection_works WHERE mix_id=? "
                "ORDER BY present_in_last_complete DESC,position,work_id",
                (mix_id,),
            )
        ]
        return profile, works

    @staticmethod
    def restore_bundle_conn(conn, profile, works):
        conn.execute(
            "INSERT INTO collections VALUES (?,?,?,?)",
            (
                profile.mix_id,
                profile.model_dump_json(),
                profile.folder_path,
                profile.last_complete_at.isoformat() if profile.last_complete_at else None,
            ),
        )
        for work in works:
            conn.execute(
                "INSERT INTO collection_works VALUES (?,?,?,?,?,?)",
                (
                    profile.mix_id,
                    work.work_id,
                    work.model_dump_json(),
                    work.position,
                    int(work.present_in_last_complete),
                    work.entry_id,
                ),
            )

    def relink_conn(self, conn, mix_id, entry_intact):
        profile, works = self.bundle_conn(conn, mix_id)
        for work in works:
            entry = conn.execute(
                "SELECT * FROM entries WHERE video_id=? AND status='active'", (work.work_id,)
            ).fetchone()
            intact = entry is not None and entry_intact(entry)
            child = conn.execute(
                "SELECT j.status,j.artifacts_json FROM collection_run_items i "
                "JOIN collection_runs c ON c.parent_id=i.parent_id JOIN jobs j ON j.id=i.child_id "
                "WHERE c.mix_id=? AND i.work_id=? AND c.confirmed=1 "
                "ORDER BY j.created_at DESC LIMIT 1",
                (mix_id, work.work_id),
            ).fetchone()
            disposition = "imported" if intact else "pending"
            if not intact and child:
                status = JobStatus(child["status"])
                disposition = (
                    "deleted"
                    if json.loads(child["artifacts_json"]).get("user_deleted")
                    else "active"
                    if status not in TERMINAL
                    else "cancelled"
                    if status == JobStatus.CANCELLED
                    else "failed"
                )
            work.entry_id = entry["id"] if intact else None
            work.disposition = disposition
            conn.execute(
                "UPDATE collection_works SET entry_id=?,data_json=? WHERE mix_id=? AND work_id=?",
                (work.entry_id, work.model_dump_json(), mix_id, work.work_id),
            )
        return profile, works
