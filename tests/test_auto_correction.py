
import pytest

from douyin_wiki.models import AnalysisMode, JobStatus, ReviewIssue
from douyin_wiki.operation import next_action_for, retryable
from douyin_wiki.worker import Worker


@pytest.mark.asyncio
async def test_correction_edits_survive_vault_roundtrip(service):
    from douyin_wiki.models import TranscriptCorrection
    from tests.support.gateway import complete_gateway_job

    job = service.capture_douyin("https://v.douyin.com/uvHsRpXIn8s/")
    paused = await Worker(service).run_once()
    assert paused.status == JobStatus.AWAITING_AGENT_ANALYSIS
    service.submit_transcript_correction(
        job.id,
        [TranscriptCorrection(id=row["id"], text=row["text"].replace("离职以后", "离职之后"))
         for row in paused.artifacts["transcript_raw"]],
        producer="test-agent",
    )
    completed = await complete_gateway_job(service, job.id)
    assert completed.status == JobStatus.COMPLETED
    assert completed.result["warnings"] == []
    data = service.database.get_entry_data(completed.result["entry_id"])
    assert data["transcript_edits"]
    entry, restored = service.vault.load_entries()[0]
    assert restored["transcript_edits"] == data["transcript_edits"]
    assert restored["transcript_raw"] == data["transcript_raw"]
    assert restored["transcript_corrected"] == data["transcript_corrected"]
    assert "模型校正" in (service.config.vault_path / entry.raw_path).read_text()
    assert service.database.get_review_issues(job.id) == []


@pytest.mark.asyncio
async def test_legacy_review_explicit_retry_archives_and_recorrects(service):
    service.config.analysis_mode = AnalysisMode.GATEWAY
    job = service.capture_douyin("https://v.douyin.com/uvHsRpXIn8s/")
    await Worker(service).run_once()
    issue = ReviewIssue(id="old-0", start_ms=0, end_ms=1000,
                        raw_text="旧疑点", reason="旧规则")
    service.database.replace_review_issues(job.id, [issue])
    old = service.database.update_job(
        job.id, status=JobStatus.NEEDS_REVIEW,
        artifacts={"transcript_corrected": [{"id": 0, "text": "错误旧稿"}],
                   "analysis": {"title": "旧分析"}},
    )
    assert retryable(old)
    assert next_action_for(old, analysis_mode="gateway")["code"] == "retry"
    queued = service.retry_job(job.id)
    assert queued.status == JobStatus.QUEUED
    assert service.database.get_review_issues(job.id) == []
    archive = queued.artifacts["previous_review_attempts"][0]
    assert archive["review_issues"][0]["id"] == "old-0"
    assert archive["transcript_corrected"][0]["text"] == "错误旧稿"
    for key in ("transcript_corrected", "analysis"):
        assert key not in queued.artifacts
    waiting = await Worker(service).run_once()
    assert waiting.status == JobStatus.AWAITING_AGENT_ANALYSIS
    assert waiting.result["phase"] == "transcript_correction"
