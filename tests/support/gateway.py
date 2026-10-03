"""Offline Gateway jobs shared by protocol and adapter tests."""

from copy import deepcopy
from typing import Any

from douyin_wiki.models import (
    AnalysisMode,
    CaptureRequest,
    GatewayContext,
    InspirationInput,
    JobRecord,
    JobStatus,
    TranscriptSegment,
)
from douyin_wiki.service import DouyinWikiService


def make_gateway_job(
    service: DouyinWikiService,
    *,
    phase: str = "transcript_correction",
    source_kind: str = "video",
    artifacts: dict[str, Any] | None = None,
) -> JobRecord:
    service.config.analysis_mode = AnalysisMode.GATEWAY
    raw = [
        TranscriptSegment(id=0, start_ms=0, end_ms=5000, text="第一段原话。"),
        TranscriptSegment(id=1, start_ms=5000, end_ms=9000, text="原始第二段。"),
    ]
    materials = {
        "metadata": {
            "video_id": "test-video",
            "source_kind": source_kind,
            "title": "资料",
            "duration_seconds": 90,
            "original_url": "https://www.douyin.com/video/test-video",
        },
        "resolved": {"video_id": "test-video"},
        "transcript_raw": [segment.model_dump(mode="json") for segment in raw]
        if source_kind == "video"
        else [],
        "ocr": [],
    }
    if phase == "analysis" and source_kind == "video":
        materials["transcript_corrected"] = deepcopy(materials["transcript_raw"])
    materials.update(deepcopy(artifacts or {}))
    job = service.database.create_job(
        CaptureRequest(
            share_text="https://www.douyin.com/video/test-video",
            inspirations=[InspirationInput(text="逐字保留的灵感")],
            gateway_context=GatewayContext(gateway="test-agent", conversation_id="thread-1"),
        ),
        status=JobStatus.AWAITING_AGENT_ANALYSIS,
        artifacts=materials,
        progress=0.6,
    )
    return service.database.update_job(job.id, result={"phase": phase})
