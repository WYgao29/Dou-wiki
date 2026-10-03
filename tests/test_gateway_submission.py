import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError
from typer.testing import CliRunner

from douyin_wiki import cli, mcp_server
from douyin_wiki.errors import JobStateError
from douyin_wiki.gateway_protocol import context_revision
from douyin_wiki.gateway_sources import build_source_catalog, expand_source_references
from douyin_wiki.mcp_server import mcp
from douyin_wiki.models import (
    AnalysisMode,
    GatewayContext,
    JobStatus,
    ReviewIssue,
    TranscriptCorrection,
)
from douyin_wiki.worker import Worker
from tests.support.gateway import (
    make_gateway_job,
    make_video_gateway_service,
    run_gateway_roundtrip,
)
from tests.test_image_note import make_service as make_note_service


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


def test_refs_expand_to_exact_original_quote_without_mutating_input(service):
    job = make_gateway_job(service, phase="analysis")
    catalog = build_source_catalog(job.artifacts, phase="analysis")
    incoming = {
        "title": "资料",
        "knowledge_atoms": [{"id": "atom-1", "statement": "第二段内容", "source_refs": ["a:1:0"]}],
    }
    before = deepcopy(incoming)
    expanded = expand_source_references(incoming, catalog, source_kind="video")
    atom = expanded["knowledge_atoms"][0]
    assert atom["quote"] == "原始第二段。"
    assert atom["timestamp_ms"] == 5000
    assert atom["provenance"] == "audio"
    assert "source_refs" not in atom
    assert incoming == before


def test_ref_and_explicit_submission_produce_equivalent_analysis(service):
    ref_job = make_gateway_job(service, phase="analysis")
    explicit_job = make_gateway_job(service, phase="analysis")
    common = {
        "title": "资料",
        "one_liner": "原话摘要",
        "tags": ["原话"],
        "chapters": [
            {
                "start_ms": 0,
                "title": "开头",
                "summary": "原话",
                "evidence": [{"source_refs": ["a:0:0"]}],
            }
        ],
        "knowledge_atoms": [{"id": "k", "statement": "第二段内容", "source_refs": ["a:1:0"]}],
    }
    explicit = deepcopy(common)
    explicit["chapters"][0]["evidence"][0] = {
        "quote": "第一段原话。",
        "timestamp_ms": 0,
        "evidence_type": "audio",
    }
    explicit["knowledge_atoms"][0] = {
        "id": "k",
        "statement": "第二段内容",
        "quote": "原始第二段。",
        "timestamp_ms": 5000,
        "provenance": "audio",
    }
    ref_result = service.submit_gateway_analysis(
        ref_job.id,
        common,
        producer="test-agent",
        gateway_protocol=2,
        context_revision=context_revision(ref_job),
    )
    explicit_result = service.submit_gateway_analysis(
        explicit_job.id,
        explicit,
        producer="test-agent",
        gateway_protocol=2,
        context_revision=context_revision(explicit_job),
    )
    assert ref_result.artifacts["analysis"] == explicit_result.artifacts["analysis"]
    assert ref_result.artifacts["analysis"]["claims"][0]["source_quote"] == "原始第二段。"
    assert ref_result.status == JobStatus.QUEUED


@pytest.mark.parametrize("source_kind,provenance", [("video", "ocr"), ("image_note", "image_ocr")])
def test_ocr_ref_derives_provenance_without_audio_default(service, source_kind, provenance):
    job = make_gateway_job(
        service,
        phase="analysis",
        source_kind=source_kind,
        artifacts={
            "metadata": {
                "source_kind": source_kind,
                "image_paths": ["1.jpg"],
                "duration_seconds": 90,
            },
            "ocr": [
                {
                    "text": "表格原文２０％",
                    "timestamp_ms": 1000 if source_kind == "video" else None,
                    "image_index": 1 if source_kind == "image_note" else None,
                }
            ],
        },
    )
    submitted = service.submit_gateway_analysis(
        job.id,
        {
            "title": "资料",
            "knowledge_atoms": [{"id": "k", "statement": "参数", "source_refs": ["o:0:0"]}],
        },
        producer="test-agent",
        gateway_protocol=2,
        context_revision=context_revision(job),
    )
    atom = submitted.artifacts["analysis"]["knowledge_atoms"][0]
    assert atom["provenance"] == provenance and atom["quote"] == "表格原文２０％"
    assert atom["timestamp_ms"] == (1000 if source_kind == "video" else None)
    assert atom["image_index"] == (1 if source_kind == "image_note" else None)


def test_cross_segment_and_unicode_refs_preserve_complete_original_ranges(service):
    transcript = [
        {"id": 0, "start_ms": 0, "end_ms": 1000, "text": "ΟΣ"},
        {"id": 1, "start_ms": 1000, "end_ms": 2000, "text": " 。！ "},
        {"id": 2, "start_ms": 2000, "end_ms": 3000, "text": "Α"},
        {"id": 3, "start_ms": 4000, "end_ms": 5000, "text": "Straße/oﬃce"},
    ]
    job = make_gateway_job(
        service, phase="analysis", artifacts={"transcript_corrected": transcript}
    )
    catalog = build_source_catalog(job.artifacts, phase="analysis")
    analysis = {
        "title": "资料",
        "knowledge_atoms": [
            {"id": "greek", "statement": "原文", "source_refs": ["a:0:0", "a:2:0"]},
            {"id": "expanded", "statement": "原文", "source_refs": ["a:3:0"]},
        ],
    }
    expanded = expand_source_references(analysis, catalog, source_kind="video")
    assert expanded["knowledge_atoms"][0]["quote"] == "ΟΣ\n 。！ \nΑ"
    assert expanded["knowledge_atoms"][1]["quote"] == "Straße/oﬃce"
    submitted = service.submit_gateway_analysis(
        job.id,
        analysis,
        producer="test-agent",
        gateway_protocol=2,
        context_revision=context_revision(job),
    )
    assert submitted.artifacts["analysis"]["knowledge_atoms"][0]["timestamp_ms"] == 0


@pytest.mark.parametrize(
    "refs", [["a:999:0"], ["o:0:0"], ["a:0:0", "a:2:0"], ["a:1:0", "a:1:0"], ["a:1:0", "a:0:0"]]
)
def test_refs_reject_unknown_hidden_noncontiguous_and_duplicate_sources(service, refs):
    job = make_gateway_job(service, phase="analysis")
    corrected = deepcopy(job.artifacts["transcript_corrected"])
    corrected.append({"id": 2, "start_ms": 9000, "end_ms": 10000, "text": "最后一段"})
    job = service.database.update_job(
        job.id,
        artifacts={
            "transcript_corrected": corrected,
            "ocr": [{"timestamp_ms": 0, "text": "第一段原话。"}],
        },
    )
    with pytest.raises(JobStateError) as error:
        service.submit_gateway_analysis(
            job.id,
            {
                "title": "资料",
                "knowledge_atoms": [{"id": "k", "statement": "事实", "source_refs": refs}],
            },
            producer="test-agent",
            gateway_protocol=2,
            context_revision=context_revision(job),
        )
    assert error.value.details["reason"] == "invalid_source_ref"
    assert error.value.details["path"] == "knowledge_atoms[0].source_refs"
    assert service.database.get_job(job.id) == job


@pytest.mark.parametrize(
    "source_kind,refs",
    [
        ("image_note", ["o:0:0", "o:1:0"]),
        ("video", ["o:0:0", "o:1:0"]),
        ("video", ["a:0:0", "o:0:0"]),
    ],
)
def test_refs_cannot_cross_images_containers_or_revisions(service, source_kind, refs):
    job = make_gateway_job(
        service,
        phase="analysis",
        source_kind=source_kind,
        artifacts={
            "metadata": {"source_kind": source_kind, "image_paths": ["1.jpg", "2.jpg"]},
            "ocr": [
                {"timestamp_ms": 0, "image_index": 1, "text": "图表文字"},
                {"timestamp_ms": 1000, "image_index": 2, "text": "后面文字"},
            ],
        },
    )
    with pytest.raises(JobStateError):
        service.submit_gateway_analysis(
            job.id,
            {
                "title": "资料",
                "knowledge_atoms": [{"id": "k", "statement": "事实", "source_refs": refs}],
            },
            producer="test-agent",
            gateway_protocol=2,
            context_revision=context_revision(job),
        )
    other = make_gateway_job(service, phase="analysis", source_kind=source_kind)
    with pytest.raises(JobStateError) as error:
        service.submit_gateway_analysis(
            job.id,
            {"title": "资料"},
            producer="test-agent",
            gateway_protocol=2,
            context_revision=context_revision(other),
        )
    assert error.value.details["reason"] == "context_revision_mismatch"
    assert service.database.get_job(job.id) == job


@pytest.mark.parametrize(
    "conflict",
    [
        {"quote": "伪造原话"},
        {"provenance": "ocr"},
        {"timestamp_ms": 0},
        {"image_index": 1},
        {"quote": "Stras"},
    ],
)
def test_refs_and_inline_fields_must_agree(service, conflict):
    job = make_gateway_job(service, phase="analysis")
    with pytest.raises(JobStateError) as error:
        service.submit_gateway_analysis(
            job.id,
            {
                "title": "资料",
                "knowledge_atoms": [
                    {"id": "k", "statement": "事实", "source_refs": ["a:1:0"], **conflict}
                ],
            },
            producer="test-agent",
            gateway_protocol=2,
            context_revision=context_revision(job),
        )
    assert error.value.details["reason"] == "invalid_source_ref"
    assert service.database.get_job(job.id) == job


def test_matching_inline_fields_and_explicit_mixed_quotes_remain_valid(service):
    job = make_gateway_job(
        service, phase="analysis", artifacts={"ocr": [{"timestamp_ms": 5000, "text": "画面表格"}]}
    )
    submitted = service.submit_gateway_analysis(
        job.id,
        {
            "title": "资料",
            "knowledge_atoms": [
                {
                    "id": "same",
                    "statement": "原文",
                    "source_refs": ["a:1:0"],
                    "quote": "原始第二段。",
                    "provenance": "audio",
                    "timestamp_ms": 5000,
                },
                {
                    "id": "mixed",
                    "statement": "原文",
                    "quote": "原始第二段。\n画面表格",
                    "provenance": "audio+ocr",
                    "timestamp_ms": 5000,
                },
            ],
        },
        producer="test-agent",
        gateway_protocol=2,
        context_revision=context_revision(job),
    )
    assert [atom["provenance"] for atom in submitted.artifacts["analysis"]["knowledge_atoms"]] == [
        "audio",
        "audio+ocr",
    ]


@pytest.mark.parametrize(
    "source_kind,ref,quote",
    [
        ("video", "a:0:0", "第一段原话。"),
        ("image_note", "p:0", "正文原文"),
    ],
)
def test_inference_and_reminder_sources_keep_their_semantics(service, source_kind, ref, quote):
    job = make_gateway_job(service, phase="analysis", source_kind=source_kind)
    job = service.database.update_job(
        job.id, artifacts={"metadata": {**job.artifacts["metadata"], "post_text": "正文原文"}}
    )
    result = service.submit_gateway_analysis(
        job.id,
        {
            "title": "资料",
            "knowledge_atoms": [
                {
                    "id": "inference",
                    "statement": "推断",
                    "atom_type": "inference",
                    "provenance": "ai_inference",
                }
            ],
            "reminders": [
                {
                    "id": "r",
                    "title": "稍后检查",
                    "reason": "原文动作",
                    "confidence": 0.7,
                    "needs_clarification": True,
                    "source_refs": [ref],
                }
            ],
        },
        producer="test-agent",
        gateway_protocol=2,
        context_revision=context_revision(job),
    )
    assert result.artifacts["analysis"]["claims"] == []
    assert result.artifacts["analysis"]["reminders"][0]["source_quote"] == quote
    assert service.reminders.created == []


def test_compact_facts_require_refs_or_located_quote(service):
    legacy = make_gateway_job(service, phase="analysis")
    analysis = {
        "title": "资料",
        "knowledge_atoms": [{"id": "k", "statement": "事实", "timestamp_ms": 0}],
    }
    service.submit_gateway_analysis(legacy.id, analysis, producer="old-agent")
    compact = make_gateway_job(service, phase="analysis")
    compact = service.database.update_job(
        compact.id,
        artifacts={
            "metadata": {**compact.artifacts["metadata"], "post_text": "视频正文，只供理解"}
        },
    )
    for atom in [
        analysis["knowledge_atoms"][0],
        {"id": "k", "statement": "事实", "source_refs": ["p:0"]},
    ]:
        with pytest.raises(JobStateError):
            service.submit_gateway_analysis(
                compact.id,
                {"title": "资料", "knowledge_atoms": [atom]},
                producer="test-agent",
                gateway_protocol=2,
                context_revision=context_revision(compact),
            )
    assert service.database.get_job(compact.id) == compact
    note = make_gateway_job(service, phase="analysis", source_kind="image_note")
    with pytest.raises(JobStateError):
        service.submit_gateway_analysis(
            note.id,
            {
                "title": "资料",
                "chapters": [{"start_ms": 0, "title": "伪时间轴", "summary": "原文"}],
            },
            producer="test-agent",
            gateway_protocol=2,
            context_revision=context_revision(note),
        )
    assert service.database.get_job(note.id) == note


@pytest.mark.parametrize("revision", [None, "expired"])
def test_analysis_revision_prevents_stale_requeue(service, revision):
    job = make_gateway_job(service, phase="analysis")
    with pytest.raises(JobStateError):
        service.submit_gateway_analysis(
            job.id,
            {"title": "资料"},
            producer="test-agent",
            gateway_protocol=2,
            context_revision=revision,
        )
    assert service.database.get_job(job.id) == job


@pytest.mark.parametrize("cancel", [False, True])
def test_analysis_compare_and_set_prevents_stale_requeue(service, monkeypatch, cancel):
    job = make_gateway_job(service, phase="analysis")
    original_requeue = service.database.requeue_job

    def race(job_id, **kwargs):
        service.database.update_job(
            job_id,
            result={"concurrent": "preserved"},
            **({"status": JobStatus.CANCELLED} if cancel else {}),
        )
        return original_requeue(job_id, **kwargs)

    monkeypatch.setattr(service.database, "requeue_job", race)
    with pytest.raises(JobStateError):
        service.submit_gateway_analysis(
            job.id,
            {"title": "资料"},
            producer="test-agent",
            gateway_protocol=2,
            context_revision=context_revision(job),
        )
    current = service.database.get_job(job.id)
    assert "analysis" not in current.artifacts and current.result["concurrent"] == "preserved"
    assert current.status == (JobStatus.CANCELLED if cancel else JobStatus.AWAITING_AGENT_ANALYSIS)


def test_unlocated_and_punctuation_only_refs_cannot_be_facts(service):
    for text in ["没有定位的图表", "……！？"]:
        job = make_gateway_job(service, phase="analysis", artifacts={"ocr": [{"text": text}]})
        with pytest.raises(JobStateError):
            service.submit_gateway_analysis(
                job.id,
                {
                    "title": "资料",
                    "knowledge_atoms": [{"id": "k", "statement": "事实", "source_refs": ["o:0:0"]}],
                },
                producer="test-agent",
                gateway_protocol=2,
                context_revision=context_revision(job),
            )
        assert service.database.get_job(job.id) == job


@pytest.mark.parametrize("refs", [[], [42], "a:0:0"])
def test_malformed_reference_shape_uses_small_state_error(service, refs):
    job = make_gateway_job(service, phase="analysis")
    with pytest.raises(JobStateError) as error:
        service.submit_gateway_analysis(
            job.id,
            {
                "title": "资料",
                "knowledge_atoms": [{"id": "k", "statement": "事实", "source_refs": refs}],
            },
            producer="test-agent",
            gateway_protocol=2,
            context_revision=context_revision(job),
        )
    assert error.value.details["reason"] == "invalid_source_ref"
    assert "input" not in str(error.value.details)
    assert service.database.get_job(job.id) == job


@pytest.mark.asyncio
async def test_mcp_compact_correction_ack_does_not_repeat_artifacts(service, monkeypatch):
    job = make_gateway_job(service)
    monkeypatch.setattr(mcp_server, "_SERVICE", service)
    context = await mcp.call_tool("get_analysis_context", {"job_id": job.id, "gateway_protocol": 2})
    result = await mcp.call_tool(
        "submit_transcript_correction",
        {
            "job_id": job.id,
            "corrections": [],
            "producer": "test-agent",
            "gateway_protocol": 2,
            "context_revision": context.structured_content["context_revision"],
        },
    )
    ack = result.structured_content
    assert ack["phase"] == "analysis" and ack["phase_label"] == "内容分析"
    assert ack["next_tool"] == "get_analysis_context"
    assert {"artifacts", "request", "analysis_schema", "ocr"}.isdisjoint(ack)
    assert json.loads(result.content[0].text) == ack


@pytest.mark.asyncio
async def test_mcp_advertises_optional_protocol_and_rejects_nonintegers(service, monkeypatch):
    names = {
        "get_job",
        "get_analysis_context",
        "submit_transcript_correction",
        "submit_gateway_analysis",
    }
    tools = {tool.name: tool for tool in await mcp.list_tools()}
    for name in names:
        schema = tools[name].input_schema
        assert schema["properties"]["gateway_protocol"]["enum"] == [1, 2]
        assert schema["properties"]["gateway_protocol"]["default"] == 1
        assert "gateway_protocol" not in schema.get("required", [])

    def forbidden_service():
        raise AssertionError("非法版本不应访问服务")

    monkeypatch.setattr(mcp_server, "_service", forbidden_service)
    for value in [True, 2.0, "2", 3]:
        with pytest.raises(ToolError):
            await mcp.call_tool("get_job", {"job_id": "invalid", "gateway_protocol": value})


@pytest.mark.asyncio
async def test_mcp_legacy_default_keeps_full_payload_and_stub_calls(service, monkeypatch):
    job = make_gateway_job(service)
    monkeypatch.setattr(mcp_server, "_SERVICE", service)
    full = await mcp.call_tool("get_job", {"job_id": job.id})
    assert "artifacts" in full.structured_content and full.structured_content["id"] == job.id

    def old_context(job_id):
        return {"job_id": job_id, "old_context": True}

    def old_correction(job_id, corrections, *, producer, model, review_issues):
        assert len(corrections) == 2
        return job

    def old_analysis(job_id, analysis, *, producer, model):
        return job

    monkeypatch.setattr(
        mcp_server,
        "_SERVICE",
        SimpleNamespace(
            get_job=lambda job_id: job,
            get_analysis_context=old_context,
            submit_transcript_correction=old_correction,
            submit_gateway_analysis=old_analysis,
        ),
    )
    assert (await mcp.call_tool("get_analysis_context", {"job_id": job.id})).structured_content[
        "old_context"
    ]
    args = {"job_id": job.id, "producer": "old-agent"}
    assert (
        "artifacts"
        in (
            await mcp.call_tool(
                "submit_transcript_correction",
                {
                    **args,
                    "corrections": [
                        {"id": row["id"], "text": row["text"]}
                        for row in job.artifacts["transcript_raw"]
                    ],
                },
            )
        ).structured_content
    )
    assert (
        "artifacts"
        in (
            await mcp.call_tool("submit_gateway_analysis", {**args, "analysis": {"title": "资料"}})
        ).structured_content
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("source_kind", ["video", "image_note"])
@pytest.mark.parametrize("protocol", [1, 2])
async def test_mcp_video_note_and_reanalysis_complete_with_references(
    tmp_path, monkeypatch, source_kind, protocol
):
    service = (
        make_video_gateway_service(tmp_path)
        if source_kind == "video"
        else make_note_service(tmp_path, mode=AnalysisMode.GATEWAY)
    )
    monkeypatch.setattr(mcp_server, "_SERVICE", service)
    response = await mcp.call_tool(
        "capture_douyin",
        {
            "share_text": "https://v.douyin.com/uvHsRpXIn8s/",
            "gateway_context": {"gateway": "test-agent", "conversation_id": "thread-1"},
        },
    )
    job_id = response.structured_content["id"]
    paused = await Worker(service).run_once()
    assert paused.status == JobStatus.AWAITING_AGENT_ANALYSIS
    args = {"job_id": job_id, "gateway_protocol": protocol}
    ctx = (await mcp.call_tool("get_analysis_context", args)).structured_content
    if source_kind == "video":
        assert ctx["phase"] == "transcript_correction"
        corrections = (
            []
            if protocol == 2
            else [{"id": row["id"], "text": row["text"]} for row in ctx["transcript_raw"]]
        )
        await mcp.call_tool(
            "submit_transcript_correction",
            {
                **args,
                "corrections": corrections,
                "producer": "test-agent",
                **({"context_revision": ctx["context_revision"]} if protocol == 2 else {}),
            },
        )
        ctx = (await mcp.call_tool("get_analysis_context", args)).structured_content
    assert ctx["phase"] == "analysis"
    if protocol == 2:
        source = next(
            row
            for row in ctx["sources"]
            if row["kind"] == ("audio" if source_kind == "video" else "image_ocr")
        )
        location = source["locations"][0]
        atom = {"id": "k", "statement": "原文", "source_refs": [location["source_ref"]]}
        quote = source["text"]
    else:
        row = ctx["transcript_corrected"][0] if source_kind == "video" else ctx["ocr"][0]
        quote = row["text"]
        atom = {
            "id": "k",
            "statement": "原文",
            "quote": quote,
            "provenance": "audio" if source_kind == "video" else "image_ocr",
            **(
                {"timestamp_ms": row["start_ms"]}
                if source_kind == "video"
                else {"image_index": row["image_index"]}
            ),
        }
    await mcp.call_tool(
        "submit_gateway_analysis",
        {
            **args,
            "analysis": {"title": "资料", "knowledge_atoms": [atom]},
            "producer": "test-agent",
            **({"context_revision": ctx["context_revision"]} if protocol == 2 else {}),
        },
    )
    completed = await Worker(service).run_once()
    assert completed.id == job_id and completed.status == JobStatus.COMPLETED
    stored = service.database.get_entry_data(completed.result["entry_id"])
    assert stored["analysis"]["knowledge_atoms"][0]["quote"] == quote
    assert "source_refs" not in json.dumps(stored, ensure_ascii=False)
    if source_kind == "image_note":
        assert stored["analysis"]["knowledge_atoms"][0]["image_index"] == 1
    downloader = service.downloader if source_kind == "video" else service.image_note_downloader
    download_count = downloader.calls

    def forbidden_material(*args, **kwargs):
        raise AssertionError("重新分析不能重复媒体处理")

    monkeypatch.setattr(service.ocr, "recognize", forbidden_material)
    rerun = service.reanalyze_entry(
        completed.result["entry_id"],
        force=True,
        gateway_context=GatewayContext(gateway="test-agent"),
    )
    resumed = await Worker(service).run_once()
    assert resumed.id == rerun.id and resumed.status == JobStatus.AWAITING_AGENT_ANALYSIS
    ctx = (
        await mcp.call_tool(
            "get_analysis_context", {"job_id": rerun.id, "gateway_protocol": protocol}
        )
    ).structured_content
    assert ctx["phase"] == "analysis"
    await mcp.call_tool(
        "submit_gateway_analysis",
        {
            "job_id": rerun.id,
            "gateway_protocol": protocol,
            "analysis": {"title": "重新分析"},
            "producer": "test-agent",
            **({"context_revision": ctx["context_revision"]} if protocol == 2 else {}),
        },
    )
    assert (await Worker(service).run_once()).status == JobStatus.COMPLETED
    assert downloader.calls == download_count


@pytest.mark.asyncio
async def test_mcp_replay_checks_phase_and_keeps_batch_silent(service, monkeypatch):
    job = make_gateway_job(
        service,
        artifacts={
            "favorites_context": {"parent_job_id": "parent-f", "batch_silent": True},
            "creator_context": {
                "id": "creator-1",
                "parent_job_id": "parent-c",
                "batch_silent": True,
            },
        },
    )
    monkeypatch.setattr(mcp_server, "_SERVICE", service)
    revision = context_revision(job)
    submit = {
        "job_id": job.id,
        "corrections": [],
        "producer": "test-agent",
        "gateway_protocol": 2,
        "context_revision": revision,
    }
    await mcp.call_tool("submit_transcript_correction", submit)
    state = (
        await mcp.call_tool("get_job", {"job_id": job.id, "gateway_protocol": 2})
    ).structured_content
    assert state["phase"] == "analysis" and state["context_revision"] != revision
    assert state["favorites_context"]["batch_silent"] and state["creator_context"]["batch_silent"]
    assert state["creator_context"]["id"] == "creator-1"
    with pytest.raises(ToolError) as error:
        await mcp.call_tool("submit_transcript_correction", submit)
    assert json.loads(str(error.value))["error"]["code"] == "invalid_job_state"
    ctx = (
        await mcp.call_tool("get_analysis_context", {"job_id": job.id, "gateway_protocol": 2})
    ).structured_content
    assert ctx["creator_context"]["parent_job_id"] == "parent-c"
    ack = (
        await mcp.call_tool(
            "submit_gateway_analysis",
            {
                "job_id": job.id,
                "gateway_protocol": 2,
                "context_revision": ctx["context_revision"],
                "analysis": {"title": "资料"},
                "producer": "test-agent",
            },
        )
    ).structured_content
    assert ack["next_tool"] == "list_job_events" and ack["context_revision"] is None
    events = service.list_job_events()
    assert events and all(event.acknowledged_at is None for event in events)


@pytest.mark.parametrize("protocol", [1, 2])
@pytest.mark.parametrize("wrapped", [False, True])
def test_cli_protocol_options_preserve_old_json_formats(
    service, monkeypatch, tmp_path, protocol, wrapped
):
    job = make_gateway_job(service)
    monkeypatch.setattr(cli, "_service", lambda config_path: service)
    runner = CliRunner()
    options = ["--gateway-protocol", str(protocol)] if protocol == 2 else []
    response = runner.invoke(cli.app, ["gateway", "context", job.id, *options])
    assert response.exit_code == 0, response.output
    assert (
        "correction_schema" in response.output
        if protocol == 2
        else "transcript_raw" in response.output
    )
    corrections = (
        []
        if protocol == 2
        else [{"id": row["id"], "text": row["text"]} for row in job.artifacts["transcript_raw"]]
    )
    path = tmp_path / "correction.json"
    path.write_text(
        json.dumps({"corrections": corrections} if wrapped else corrections), encoding="utf-8"
    )
    revision_options = ["--context-revision", context_revision(job)] if protocol == 2 else []
    result = runner.invoke(
        cli.app,
        [
            "gateway",
            "submit-correction",
            job.id,
            str(path),
            "--producer",
            "test-agent",
            *options,
            *revision_options,
        ],
    )
    assert result.exit_code == 0, result.output
    updated = service.database.get_job(job.id)
    assert "artifacts" not in result.output if protocol == 2 else "artifacts" in result.output
    path.write_text(json.dumps({"title": "资料", "one_liner": "分析摘要"}), encoding="utf-8")
    revision_options = ["--context-revision", context_revision(updated)] if protocol == 2 else []
    result = runner.invoke(
        cli.app,
        [
            "gateway",
            "submit-analysis",
            job.id,
            str(path),
            "--producer",
            "test-agent",
            *options,
            *revision_options,
        ],
    )
    assert result.exit_code == 0, result.output
    assert service.database.get_job(job.id).status == JobStatus.QUEUED


@pytest.mark.asyncio
async def test_compact_roundtrip_reduces_repeated_material_without_changing_analysis(tmp_path):
    legacy, legacy_exchanges = await run_gateway_roundtrip(
        make_video_gateway_service(tmp_path / "v1"), gateway_protocol=1
    )
    compact, compact_exchanges = await run_gateway_roundtrip(
        make_video_gateway_service(tmp_path / "v2"), gateway_protocol=2
    )
    assert compact == legacy

    def size(exchanges):
        return sum(
            len(json.dumps(item["request"], ensure_ascii=False, separators=(",", ":")).encode())
            + len(item["response_text"].encode())
            for item in exchanges
        )

    assert len(legacy_exchanges) == len(compact_exchanges) == 6
    assert size(compact_exchanges) < size(legacy_exchanges)
    context = compact_exchanges[3]["response_structured"]
    assert "o:120:0" in [
        loc["source_ref"] for row in context["sources"] for loc in row["locations"]
    ]
    assert compact["knowledge_atoms"][0]["timestamp_ms"] == 70000
