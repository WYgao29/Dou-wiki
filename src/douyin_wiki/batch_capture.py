"""Shared capture identity; batch selection rules stay in their own controllers."""

from __future__ import annotations

import json

from .adapters.share import extract_douyin_url, extract_video_id
from .database import Database
from .errors import InvalidShareTextError
from .models import CaptureRequest, JobStatus

TERMINAL = {
    JobStatus.COMPLETED,
    JobStatus.COMPLETED_WITH_WARNINGS,
    JobStatus.FAILED,
    JobStatus.CANCELLED,
}


def capture_keys(request: CaptureRequest, artifacts: dict) -> list[str]:
    keys = [request.share_text]
    try:
        if work_id := extract_video_id(extract_douyin_url(request.share_text)):
            keys.append(work_id)
    except InvalidShareTextError:
        pass
    for name, key in (
        ("resolved", "video_id"),
        ("metadata", "video_id"),
        ("favorites_context", "work_id"),
        ("creator_context", "work_id"),
        ("collection_context", "work_id"),
    ):
        value = artifacts.get(name, {})
        if isinstance(value, dict) and value.get(key):
            keys.append(str(value[key]))
    return list(dict.fromkeys(keys))


def active_capture_jobs(conn) -> dict:
    active = {}
    for row in conn.execute("SELECT * FROM jobs WHERE kind='capture' ORDER BY created_at"):
        if JobStatus(row["status"]) in TERMINAL:
            continue
        artifacts = json.loads(row["artifacts_json"])
        if artifacts.get("user_deleted") or artifacts.get("cancel_requested"):
            continue
        child = Database._job_from_row(row)
        for key in capture_keys(child.request, artifacts):
            active.setdefault(key, child)
    return active
