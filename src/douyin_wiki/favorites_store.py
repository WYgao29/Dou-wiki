"""Durable favorites runs; these tables are operational history, not knowledge cache."""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from .batch_capture import active_capture_jobs
from .database import Database
from .errors import BrowserAuthRequiredError, JobStateError
from .favorites_models import FavoriteInventory
from .models import CaptureRequest, JobStatus
from .time_utils import iso_now

FAVORITES_SCHEMA = """
CREATE TABLE IF NOT EXISTS favorites_runs (
    parent_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    options_json TEXT NOT NULL,
    snapshot_json TEXT NOT NULL DEFAULT '{}',
    confirmed INTEGER NOT NULL DEFAULT 0,
    accept_partial INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS favorites_items (
    parent_id TEXT NOT NULL REFERENCES favorites_runs(parent_id) ON DELETE CASCADE,
    work_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    data_json TEXT NOT NULL,
    selected INTEGER NOT NULL,
    selection_explicit INTEGER NOT NULL DEFAULT 0,
    child_id TEXT REFERENCES jobs(id),
    entry_id TEXT,
    PRIMARY KEY(parent_id, work_id)
);
CREATE INDEX IF NOT EXISTS idx_favorites_items_child ON favorites_items(child_id);
"""

TERMINAL = {
    JobStatus.COMPLETED,
    JobStatus.COMPLETED_WITH_WARNINGS,
    JobStatus.FAILED,
    JobStatus.CANCELLED,
}


def eligible(work: dict, options: dict) -> bool:
    type_allowed = work["source_kind"] == "video" or (
        work["source_kind"] == "image_note" and options.get("include_images")
    )
    folder_allowed = not options.get("folder_ids") or bool(
        set(options["folder_ids"]).intersection(work.get("folder_ids", []))
    )
    return bool(work.get("available", True) and type_allowed and folder_allowed)


class FavoritesStore:
    def __init__(self, database: Database):
        self.database = database

    @staticmethod
    def _run(conn: sqlite3.Connection, job_id: str) -> dict:
        row = conn.execute(
            """SELECT f.*, j.status, j.request_json FROM favorites_runs f
               JOIN jobs j ON j.id=f.parent_id
               WHERE f.parent_id=? AND j.kind='favorites_import'""",
            (job_id,),
        ).fetchone()
        if row is None:
            raise JobStateError("该任务不是收藏导入任务")
        return {
            "options": json.loads(row["options_json"]),
            "snapshot": json.loads(row["snapshot_json"]),
            "confirmed": bool(row["confirmed"]),
            "accept_partial": bool(row["accept_partial"]),
            "status": row["status"],
            "request": CaptureRequest.model_validate_json(row["request_json"]),
        }

    def load(self, job_id: str) -> dict:
        with self.database.connect() as conn:
            return self._run(conn, job_id)

    def create(self, request: CaptureRequest, options: dict) -> str:
        job_id, now = uuid.uuid4().hex, iso_now()
        with self.database.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """INSERT INTO jobs(id,kind,status,progress,request_json,artifacts_json,
                   result_json,created_at,updated_at)
                   VALUES (?,'favorites_import',?,0,?,?,?, ?,?)""",
                (
                    job_id,
                    JobStatus.QUEUED.value,
                    request.model_dump_json(),
                    json.dumps({"favorites_options": options}),
                    "{}",
                    now,
                    now,
                ),
            )
            conn.execute(
                "INSERT INTO favorites_runs(parent_id,options_json) VALUES (?,?)",
                (job_id, json.dumps(options)),
            )
        return job_id

    def previous_work_ids(self, job_id: str) -> set[str]:
        with self.database.connect() as conn:
            run = self._run(conn, job_id)
            snapshot = run["snapshot"]
            if "_baseline_work_ids" not in snapshot:
                if not snapshot.get("account_id"):
                    return set()
                # Compatibility fallback is read-only; initialization freezes legacy baselines.
                source_id = self._previous_run_conn(conn, job_id, run, snapshot["account_id"])
                return set(self._work_ids_conn(conn, source_id))
            return set(snapshot["_baseline_work_ids"])

    @classmethod
    def migrate_legacy_baselines(cls, conn: sqlite3.Connection) -> None:
        for row in conn.execute("SELECT * FROM favorites_runs").fetchall():
            snapshot = json.loads(row["snapshot_json"])
            if "_baseline_work_ids" in snapshot or not snapshot.get("account_id"):
                continue
            run = {"options": json.loads(row["options_json"])}
            source_id = cls._previous_run_conn(conn, row["parent_id"], run, snapshot["account_id"])
            snapshot["_baseline_work_ids"] = cls._work_ids_conn(conn, source_id)
            conn.execute(
                "UPDATE favorites_runs SET snapshot_json=? WHERE parent_id=?",
                (json.dumps(snapshot, ensure_ascii=False), row["parent_id"]),
            )

    @staticmethod
    def _work_ids_conn(conn: sqlite3.Connection, source_id: str | None) -> list[str]:
        return [
            row["work_id"]
            for row in conn.execute(
                "SELECT work_id FROM favorites_items WHERE parent_id=? ORDER BY position,work_id",
                (source_id,),
            )
        ]

    @staticmethod
    def _previous_run_conn(
        conn: sqlite3.Connection, job_id: str, run: dict, account_id: str
    ) -> str | None:
        current = conn.execute("SELECT created_at,rowid FROM jobs WHERE id=?", (job_id,)).fetchone()
        rows = conn.execute(
            """SELECT f.* FROM favorites_runs f JOIN jobs j ON j.id=f.parent_id
               WHERE j.created_at<? OR (j.created_at=? AND j.rowid<?)
               ORDER BY j.created_at DESC,j.rowid DESC""",
            (current["created_at"], current["created_at"], current["rowid"]),
        ).fetchall()
        for row in rows:
            options, snapshot = json.loads(row["options_json"]), json.loads(row["snapshot_json"])
            if (
                not options.get("directory_only")
                and set(options.get("folder_ids") or [])
                == set(run["options"].get("folder_ids") or [])
                and snapshot.get("account_id") == account_id
            ):
                return str(row["parent_id"])
        return None

    @staticmethod
    def _seed_previous_conn(
        conn: sqlite3.Connection, job_id: str, source_id: str, options: dict
    ) -> dict:
        source_confirmed = FavoritesStore._run(conn, source_id)["confirmed"]
        inheritance = {
            "source_job_id": source_id,
            "selected_work_ids": [],
            "ignored_legacy_ids": [],
        }
        rows = conn.execute(
            "SELECT * FROM favorites_items WHERE parent_id=? ORDER BY position,work_id",
            (source_id,),
        ).fetchall()
        for index, row in enumerate(rows, start=1):
            allowed = eligible(json.loads(row["data_json"]), options)
            explicit = bool(row["selection_explicit"]) or source_confirmed
            selected = bool(row["selected"]) and explicit and allowed
            if selected:
                inheritance["selected_work_ids"].append(row["work_id"])
            elif row["selected"] and not explicit and allowed:
                inheritance["ignored_legacy_ids"].append(row["work_id"])
            conn.execute(
                """INSERT OR IGNORE INTO favorites_items
                   (parent_id,work_id,position,data_json,selected,selection_explicit)
                   VALUES (?,?,?,?,?,?)""",
                (job_id, row["work_id"], index, row["data_json"], int(selected), int(explicit)),
            )
        return inheritance

    def checkpoint(self, job_id: str, snapshot: FavoriteInventory) -> None:
        payload = snapshot.model_dump(mode="json")
        with self.database.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self.database.assert_job_claim(conn, job_id)
            run = self._run(conn, job_id)
            previous = run["snapshot"].get("account_id")
            if previous and previous != snapshot.account_id:
                raise BrowserAuthRequiredError("专用浏览器账号已变化，请登录原账号或新建清点任务")
            if run["confirmed"]:
                raise JobStateError("已提交的收藏清单不能重新清点")
            if "_selection_inheritance" in run["snapshot"]:
                payload["_selection_inheritance"] = run["snapshot"]["_selection_inheritance"]
            if "_baseline_work_ids" in run["snapshot"]:
                payload["_baseline_work_ids"] = run["snapshot"]["_baseline_work_ids"]
            else:
                source_id = self._previous_run_conn(conn, job_id, run, snapshot.account_id)
                payload["_baseline_work_ids"] = self._work_ids_conn(conn, source_id)
                if (
                    source_id
                    and not previous
                    and not run["options"].get("directory_only")
                    and run["options"].get("update_mode", "incremental") == "incremental"
                ):
                    payload["_selection_inheritance"] = self._seed_previous_conn(
                        conn, job_id, source_id, run["options"]
                    )
            works = payload.pop("works")
            wanted = set(run["options"].get("folder_ids") or [])
            if wanted:
                scoped = [w for w in works if wanted.intersection(w.get("folder_ids", []))]
                directory_ids = {f["id"] for f in payload.get("folders", [])}
                if len(scoped) != len(works) or not wanted.issubset(directory_ids):
                    payload["complete"] = False
                    payload["warnings"] = list(
                        dict.fromkeys(
                            [
                                *payload.get("warnings", []),
                                "部分作品或收藏夹无法确认属于所选范围，已排除范围外作品",
                            ]
                        )
                    )
                # Explicit contrary evidence invalidates earlier membership; unseen rows survive.
                for work in works:
                    if not wanted.intersection(work.get("folder_ids", [])):
                        conn.execute(
                            "UPDATE favorites_items SET data_json=?,selected=0 "
                            "WHERE parent_id=? AND work_id=?",
                            (json.dumps(work, ensure_ascii=False), job_id, work["work_id"]),
                        )
                works = scoped
            # Keep checkpointed works: visibility on resume is not a deletion signal.
            position = conn.execute(
                "SELECT COALESCE(MAX(position),0) FROM favorites_items WHERE parent_id=?", (job_id,)
            ).fetchone()[0]
            if not run["options"].get("directory_only"):
                for work in works:
                    position += 1
                    conn.execute(
                        """INSERT INTO favorites_items
                           (parent_id,work_id,position,data_json,selected)
                           VALUES (?,?,?,?,?) ON CONFLICT(parent_id,work_id) DO UPDATE
                           SET data_json=excluded.data_json""",
                        (
                            job_id,
                            work["work_id"],
                            position,
                            json.dumps(work, ensure_ascii=False),
                            0,
                        ),
                    )
            conn.execute(
                "UPDATE favorites_runs SET snapshot_json=? WHERE parent_id=?",
                (json.dumps(payload, ensure_ascii=False), job_id),
            )

    def rows(self, job_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM favorites_items WHERE parent_id=? ORDER BY position,work_id",
                (job_id,),
            ).fetchall()
        return [{**dict(r), "work": json.loads(r["data_json"])} for r in rows]

    def select(self, job_id: str, *, selected: bool, work_ids=None, folder_id=None) -> None:
        with self.database.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            run = self._run(conn, job_id)
            if run["confirmed"] or run["status"] != JobStatus.NEEDS_SELECTION.value:
                raise JobStateError("只有待选择作品的收藏任务可以修改选择")
            rows = conn.execute(
                "SELECT work_id,data_json FROM favorites_items WHERE parent_id=?", (job_id,)
            ).fetchall()
            if folder_id and folder_id not in {f["id"] for f in run["snapshot"].get("folders", [])}:
                raise JobStateError("收藏夹不属于当前清单")
            if work_ids is not None and not set(work_ids).issubset({r["work_id"] for r in rows}):
                raise JobStateError("作品不属于当前清单")
            wanted = set(work_ids) if work_ids is not None else None
            targets = []
            for row in rows:
                work = json.loads(row["data_json"])
                if wanted is not None and row["work_id"] not in wanted:
                    continue
                if folder_id and folder_id not in work.get("folder_ids", []):
                    continue
                if selected and not eligible(work, run["options"]):
                    if wanted is not None:
                        raise JobStateError("该作品类型未选择、不支持或不可用")
                    continue
                targets.append((int(selected), job_id, row["work_id"]))
            conn.executemany(
                "UPDATE favorites_items SET selected=?,selection_explicit=1 "
                "WHERE parent_id=? AND work_id=?",
                targets,
            )

    def confirm(self, job_id: str, *, accept_partial: bool, entry_intact) -> None:
        with self.database.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            run = self._run(conn, job_id)
            if run["options"].get("directory_only"):
                raise JobStateError("收藏夹目录任务不能提交作品")
            if run["confirmed"]:
                return
            if run["status"] != JobStatus.NEEDS_SELECTION.value:
                raise JobStateError("清点结束后才能提交收藏作品")
            if not run["snapshot"].get("complete") and not accept_partial:
                raise JobStateError("清单未完整读取；仅导入已发现作品需显式 accept_partial=true")
            request = run["request"]
            active = active_capture_jobs(conn)
            rows = conn.execute(
                "SELECT * FROM favorites_items WHERE parent_id=? AND selected=1 ORDER BY position",
                (job_id,),
            ).fetchall()
            plans = []
            for row in rows:
                work = json.loads(row["data_json"])
                if not eligible(work, run["options"]):
                    continue
                existing = conn.execute(
                    "SELECT * FROM entries WHERE video_id=?", (row["work_id"],)
                ).fetchone()
                if existing and entry_intact(existing):
                    plans.append((row, work, existing["id"], None))
                    continue
                child = active.get(row["work_id"]) or active.get(work["canonical_url"])
                if (
                    child
                    and child.request.options.processing_key() != request.options.processing_key()
                ):
                    raise JobStateError(f"作品 {row['work_id']} 已有不同处理选项的活动任务")
                plans.append((row, work, None, child))
            for row, work, entry_id, child in plans:
                if entry_id:
                    conn.execute(
                        "UPDATE favorites_items SET entry_id=? WHERE parent_id=? AND work_id=?",
                        (entry_id, job_id, row["work_id"]),
                    )
                    continue
                child_id = child.id if child else None
                if not child_id:
                    child_id, now = uuid.uuid4().hex, iso_now()
                    child_request = CaptureRequest(
                        share_text=work["canonical_url"],
                        options=request.options,
                        gateway_context=request.gateway_context,
                    )
                    context = {
                        "parent_job_id": job_id,
                        "work_id": row["work_id"],
                        "batch_silent": True,
                    }
                    conn.execute(
                        """INSERT INTO jobs(id,kind,status,request_json,artifacts_json,
                           result_json,created_at,updated_at)
                   VALUES (?,'capture',?,?,?,'{}',?,?)""",
                        (
                            child_id,
                            JobStatus.QUEUED.value,
                            child_request.model_dump_json(),
                            json.dumps({"favorites_context": context}),
                            now,
                            now,
                        ),
                    )
                conn.execute(
                    "UPDATE favorites_items SET child_id=? WHERE parent_id=? AND work_id=?",
                    (child_id, job_id, row["work_id"]),
                )
            conn.execute(
                "UPDATE favorites_runs SET confirmed=1,accept_partial=? WHERE parent_id=?",
                (int(accept_partial), job_id),
            )

    def parent_ids(
        self, *, child_id: str | None = None, limit: int | None = None, needs_refresh: bool = False
    ) -> list[str]:
        with self.database.connect() as conn:
            if child_id:
                rows = conn.execute(
                    "SELECT DISTINCT parent_id FROM favorites_items WHERE child_id=?", (child_id,)
                ).fetchall()
            else:
                sql = "SELECT f.parent_id FROM favorites_runs f JOIN jobs j ON j.id=f.parent_id"
                if needs_refresh:
                    sql += (
                        " WHERE f.confirmed=1 AND (j.status NOT IN "
                        "('completed','completed_with_warnings','failed','cancelled') OR EXISTS ("
                        "SELECT 1 FROM favorites_items i JOIN jobs c ON c.id=i.child_id "
                        "WHERE i.parent_id=f.parent_id AND (c.updated_at>j.updated_at "
                        "OR c.status NOT IN "
                        "('completed','completed_with_warnings','failed','cancelled'))))"
                    )
                sql += " ORDER BY j.created_at DESC"
                rows = conn.execute(
                    sql + (" LIMIT ?" if limit is not None else ""),
                    (limit,) if limit is not None else (),
                ).fetchall()
        return [r["parent_id"] for r in rows]
