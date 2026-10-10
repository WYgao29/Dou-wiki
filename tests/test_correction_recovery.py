"""Correction segment normalization and analysis retry recovery."""

from __future__ import annotations

from douyin_wiki.models import AnalysisResult, JobStatus


def test_failed_retry_clears_analysis_candidate(service):
    job = service.capture_douyin("https://v.douyin.com/uvHsRpXIn8s/")
    candidate = AnalysisResult(
        title="未入库坏候选",
        summary="应在失败重试时被清除",
        core_points=["x"],
        evidence=["y"],
        steps=["z"],
        applicable_scenarios=[],
        risks=[],
        actions=[],
        tags=[],
        concepts=[],
        entities=[],
        claims=[],
        reminders=[],
        ai_judgment="bad",
    ).model_dump(mode="json")
    service.database.update_job(
        job.id,
        status=JobStatus.FAILED,
        error_code="external_tool_error",
        error_message="证据校验失败",
        artifacts={
            "analysis_candidate": candidate,
            "transcript_corrected": [
                {"id": 0, "start_ms": 0, "end_ms": 1000, "text": "保留校正稿"}
            ],
        },
    )
    queued = service.retry_job(job.id)
    assert queued.status == JobStatus.QUEUED
    assert "analysis_candidate" not in queued.artifacts
    assert queued.artifacts["transcript_corrected"][0]["text"] == "保留校正稿"
