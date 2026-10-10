
import pytest
from pydantic import ValidationError

from douyin_wiki.adapters.share import ResolvedShare
from douyin_wiki.models import JobStatus, TranscriptCorrection
from douyin_wiki.service import DouyinWikiService
from douyin_wiki.worker import Worker
from tests.support.gateway import complete_gateway_job, gateway_analysis, make_video_gateway_service


@pytest.mark.parametrize("text", [" ", "\n\t", "\u3000"])
def test_correction_rejects_whitespace(text):
    with pytest.raises(ValidationError, match="不能仅包含空白"):
        TranscriptCorrection(id=0, text=text)


def restart_service(service):
    restarted = DouyinWikiService(
        service.config, resolver=service.resolver, downloader=service.downloader,
        image_note_downloader=service.image_note_downloader, media=service.media,
        transcriber=service.transcriber, ocr=service.ocr,
        embeddings=service.embeddings, reminders=service.reminders,
    )
    restarted.initialize(initialize_git=False)
    return restarted


@pytest.mark.asyncio
@pytest.mark.parametrize("gateway_protocol", [1, 2])
async def test_gateway_resume_after_restart_reuses_material_checkpoints(
    tmp_path, gateway_protocol
):
    service = make_video_gateway_service(tmp_path)
    job = service.capture_douyin("https://v.douyin.com/uvHsRpXIn8s/")
    paused = await Worker(service).run_once()
    assert paused.id == job.id and paused.result["phase"] == "transcript_correction"
    material = {key: paused.artifacts[key] for key in ("metadata", "transcript_raw", "ocr")}
    service = restart_service(service)
    context = service.get_analysis_context(job.id, gateway_protocol=gateway_protocol)
    corrections = [] if gateway_protocol == 2 else [
        TranscriptCorrection(id=row["id"], text=row["text"]) for row in material["transcript_raw"]
    ]
    corrected = service.submit_transcript_correction(
        job.id, corrections, producer="test-agent", gateway_protocol=gateway_protocol,
        context_revision=context.get("context_revision"),
    )
    assert corrected.result["phase"] == "analysis"
    assert corrected.artifacts["transcript_corrected"] == material["transcript_raw"]
    context = service.get_analysis_context(job.id, gateway_protocol=gateway_protocol)
    submitted = service.submit_gateway_analysis(
        job.id, gateway_analysis(corrected), producer="test-agent",
        gateway_protocol=gateway_protocol, context_revision=context.get("context_revision"),
    )
    assert submitted.status == JobStatus.QUEUED
    service = restart_service(service)
    assert service.get_job(job.id).artifacts["analysis"] == submitted.artifacts["analysis"]
    completed = await Worker(service).run_once()
    assert completed.id == job.id and completed.status == JobStatus.COMPLETED
    assert service.downloader.calls == 1
    for key, value in material.items():
        assert completed.artifacts[key] == value
    data = service.database.get_entry_data(completed.result["entry_id"])
    assert data["transcript_raw"] == material["transcript_raw"]
    assert data["transcript_corrected"] == material["transcript_raw"]
    assert data["analysis"] == submitted.artifacts["analysis"]


@pytest.mark.asyncio
@pytest.mark.parametrize("gateway_protocol", [1, 2])
async def test_gateway_completion_processes_queue_without_analyzing_other_jobs(
    tmp_path, gateway_protocol
):
    service = make_video_gateway_service(tmp_path)
    class OtherResolver:
        async def resolve(self, share_text):
            return ResolvedShare(
                original_url=share_text, canonical_url="https://www.douyin.com/video/7660000000000009001",
                video_id="7660000000000009001", redirect_chain=(share_text,),
            )

    original = service.resolver
    service.resolver = OtherResolver()
    other = service.capture_douyin("https://www.douyin.com/video/7660000000000009001")
    await Worker(service).run_once()
    service.resolver = original
    target = service.capture_douyin("https://v.douyin.com/uvHsRpXIn8s/")
    await Worker(service).run_once()
    service.database.requeue_job(other.id)
    completed = await complete_gateway_job(service, target.id, gateway_protocol=gateway_protocol)
    assert completed.id == target.id and completed.status == JobStatus.COMPLETED
    unrelated = service.get_job(other.id)
    assert unrelated.status == JobStatus.AWAITING_AGENT_ANALYSIS
    assert unrelated.result["phase"] == "transcript_correction"
    assert "analysis" not in unrelated.artifacts
    assert len(service.database.list_entries()) == 1
