from copy import deepcopy

import pytest
from pydantic import ValidationError

from douyin_wiki.errors import JobStateError
from douyin_wiki.gateway_protocol import context_revision
from douyin_wiki.models import AnalysisMode, JobStatus, ReviewIssue, TranscriptCorrection
from tests.support.gateway import make_gateway_job


def test_patch_preserves_unsubmitted_segments_and_raw_artifacts(service):
    job = make_gateway_job(service)
    raw = deepcopy(job.artifacts["transcript_raw"])
    updated = service.submit_transcript_correction(
        job.id,
        [TranscriptCorrection(id=1, text="校正后的第二段。")],
        producer="test-agent",
        gateway_protocol=2,
        context_revision=context_revision(job),
    )
    assert updated.artifacts["transcript_raw"] == raw
    assert updated.artifacts["transcript_corrected"][0] == raw[0]
    assert updated.artifacts["transcript_corrected"][1]["text"] == "校正后的第二段。"
    assert [row["id"] for row in updated.artifacts["transcript_edits"]] == [1]
    assert updated.result["phase"] == "analysis"
    assert updated.status == JobStatus.AWAITING_AGENT_ANALYSIS


def test_empty_patch_completes_correction_and_legacy_empty_is_rejected(service):
    compact = make_gateway_job(service)
    legacy = make_gateway_job(service)
    updated = service.submit_transcript_correction(
        compact.id,
        [],
        producer="test-agent",
        gateway_protocol=2,
        context_revision=context_revision(compact),
    )
    assert updated.artifacts["transcript_corrected"] == compact.artifacts["transcript_raw"]
    assert updated.artifacts["transcript_edits"] == []
    assert updated.result["phase"] == "analysis"
    assert updated.artifacts["correction_producer"] == "test-agent"
    with pytest.raises(JobStateError):
        service.submit_transcript_correction(legacy.id, [], producer="test-agent")
    assert service.database.get_job(legacy.id) == legacy


@pytest.mark.parametrize(
    "corrections",
    [
        [TranscriptCorrection(id=999, text="新原话")],
        [TranscriptCorrection(id=1, text="新原话"), TranscriptCorrection(id=1, text="又改")],
    ],
)
def test_patch_rejects_invalid_ids_and_blank_text_without_writes(service, corrections):
    job = make_gateway_job(service)
    with pytest.raises(JobStateError):
        service.submit_transcript_correction(
            job.id,
            corrections,
            producer="test-agent",
            gateway_protocol=2,
            context_revision=context_revision(job),
        )
    assert service.database.get_job(job.id) == job
    with pytest.raises(ValidationError):
        TranscriptCorrection(id=1, text="  \n ")
    assert service.database.get_job(job.id) == job


def test_patch_rejects_duplicate_raw_ids_without_writes(service):
    job = make_gateway_job(service)
    raw = deepcopy(job.artifacts["transcript_raw"])
    raw[1]["id"] = 0
    job = service.database.update_job(job.id, artifacts={"transcript_raw": raw})
    with pytest.raises(JobStateError):
        service.submit_transcript_correction(
            job.id,
            [],
            producer="test-agent",
            gateway_protocol=2,
            context_revision=context_revision(job),
        )
    assert service.database.get_job(job.id) == job


@pytest.mark.parametrize(
    "revision,reason",
    [
        (None, "context_revision_required"),
        (" ", "context_revision_required"),
        ("0" * 64, "context_revision_mismatch"),
    ],
)
def test_patch_requires_current_revision(service, revision, reason):
    job = make_gateway_job(service)
    with pytest.raises(JobStateError) as error:
        service.submit_transcript_correction(
            job.id, [], producer="test-agent", gateway_protocol=2, context_revision=revision
        )
    assert error.value.details["reason"] == reason
    assert service.database.get_job(job.id) == job


def test_patch_compare_and_set_rejects_a_racing_update(service, monkeypatch):
    job = make_gateway_job(service)
    original_update = service.database.update_job

    def race(job_id, **kwargs):
        if "expected_updated_at" in kwargs:
            original_update(job_id, result={"concurrent": "preserved"})
        return original_update(job_id, **kwargs)

    monkeypatch.setattr(service.database, "update_job", race)
    with pytest.raises(JobStateError):
        service.submit_transcript_correction(
            job.id,
            [],
            producer="test-agent",
            gateway_protocol=2,
            context_revision=context_revision(job),
        )
    current = service.database.get_job(job.id)
    assert "transcript_corrected" not in current.artifacts
    assert current.result["concurrent"] == "preserved"


def test_patch_preserves_all_original_locator_and_confidence_fields(service):
    job = make_gateway_job(service)
    raw = deepcopy(job.artifacts["transcript_raw"])
    raw[0].update(
        {
            "confidence": 0.2,
            "avg_logprob": -1.2,
            "no_speech_prob": 0.3,
            "asr_extension": {"score": 0.17},
        }
    )
    job = service.database.update_job(job.id, artifacts={"transcript_raw": raw})
    updated = service.submit_transcript_correction(
        job.id,
        [
            TranscriptCorrection(id=0, text="  更正第一段。  "),
            TranscriptCorrection(id=1, text="原始第二段。"),
        ],
        producer="test-agent",
        gateway_protocol=2,
        context_revision=context_revision(job),
    )
    assert updated.artifacts["transcript_corrected"] == [{**raw[0], "text": "更正第一段。"}, raw[1]]
    assert [row["id"] for row in updated.artifacts["transcript_edits"]] == [0]
    assert updated.artifacts["transcript_raw"] == raw


def test_patch_replay_and_image_note_correction_are_rejected(service):
    job = make_gateway_job(service)
    revision = context_revision(job)
    updated = service.submit_transcript_correction(
        job.id, [], producer="test-agent", gateway_protocol=2, context_revision=revision
    )
    with pytest.raises(JobStateError):
        service.submit_transcript_correction(
            job.id, [], producer="test-agent", gateway_protocol=2, context_revision=revision
        )
    assert service.database.get_job(job.id) == updated
    note = make_gateway_job(service, source_kind="image_note", phase="analysis")
    with pytest.raises(JobStateError):
        service.submit_transcript_correction(
            note.id,
            [],
            producer="test-agent",
            gateway_protocol=2,
            context_revision=context_revision(note),
        )
    assert service.database.get_job(note.id) == note


def test_patch_checks_mode_and_explicit_phase(service):
    job = make_gateway_job(service)
    service.config.analysis_mode = AnalysisMode.PROVIDER
    with pytest.raises(JobStateError):
        service.submit_transcript_correction(
            job.id,
            [],
            producer="test-agent",
            gateway_protocol=2,
            context_revision=context_revision(job),
        )
    service.config.analysis_mode = AnalysisMode.GATEWAY
    job = service.database.update_job(job.id, result={"phase": "analysis"})
    with pytest.raises(JobStateError):
        service.submit_transcript_correction(
            job.id,
            [],
            producer="test-agent",
            gateway_protocol=2,
            context_revision=context_revision(job),
        )
    assert service.database.get_job(job.id) == job


def test_legacy_full_correction_and_notes_remain_compatible(service):
    job = make_gateway_job(service)
    updated = service.submit_transcript_correction(
        job.id,
        [
            TranscriptCorrection(id=row["id"], text=row["text"])
            for row in job.artifacts["transcript_raw"]
        ],
        producer="old-agent",
        review_issues=[ReviewIssue(id="note", raw_text="不确定", reason="旧调用方说明")],
    )
    assert updated.artifacts["transcript_corrected"] == job.artifacts["transcript_raw"]
    assert updated.artifacts["correction_notes"][0]["id"] == "note"
    assert updated.artifacts["review_issues"] == []
    assert service.database.get_review_issues(job.id, open_only=True) == []
