import re
import subprocess
import sys
from copy import deepcopy
from datetime import timedelta

import pytest
from jsonschema import Draft202012Validator

from douyin_wiki.errors import JobStateError
from douyin_wiki.gateway_protocol import (
    compact_analysis_schema,
    compact_job_payload,
    context_revision,
    validate_compact_analysis_shape,
    validate_gateway_protocol,
)
from douyin_wiki.gateway_sources import build_source_catalog
from douyin_wiki.models import AnalysisMode, InspirationInput, JobStatus
from tests.support.gateway import make_gateway_job


def test_ocr_projection_filters_nearby_subtitles_and_keeps_late_material():
    artifacts = {
        "metadata": {"source_kind": "video"},
        "transcript_raw": [{"id": 7, "start_ms": 0, "end_ms": 1000, "text": "营收增长20%"}],
        "ocr": [
            {"timestamp_ms": 1000, "text": "营收增长２０％\nAI\n营收增长20.0%"},
            {"timestamp_ms": 10000, "text": "营收增长20%"},
            {"timestamp_ms": 130000, "text": "末段表格42GB"},
        ],
    }
    before = deepcopy(artifacts)
    catalog = build_source_catalog(artifacts, phase="transcript_correction")
    assert [row["text"] for row in catalog.ocr] == [
        "AI",
        "营收增长20.0%",
        "营收增长20%",
        "末段表格42GB",
    ]
    assert "o:0:0" not in catalog.refs
    assert catalog.refs["o:2:0"].timestamp_ms == 130000
    assert artifacts == before


@pytest.mark.parametrize("times", [[0, 60000, 60001], [60000, 60001, 0]])
def test_ocr_dedup_uses_last_retained_time(times):
    catalog = build_source_catalog(
        {"ocr": [{"timestamp_ms": time, "text": "原始字符Ａ  B"} for time in times]},
        phase="analysis",
    )
    assert len(catalog.ocr) == 2
    assert [len(row["locations"]) for row in catalog.ocr] == [2, 1]
    assert [loc["timestamp_ms"] for loc in catalog.ocr[0]["locations"]] == [0, 60000]
    assert catalog.ocr[0]["locations"][0]["source_ref"] == f"o:{times.index(0)}:0"


def test_ocr_without_timestamp_is_not_deduped_across_frames():
    catalog = build_source_catalog(
        {"ocr": [{"text": "同一行"}, {"text": "同一行"}]}, phase="analysis"
    )
    assert len(catalog.ocr) == 2
    assert all("timestamp_ms" not in row["locations"][0] for row in catalog.ocr)


def test_image_dedup_preserves_every_image():
    catalog = build_source_catalog(
        {
            "metadata": {"source_kind": "image_note", "post_text": "同一行"},
            "ocr": [
                {"image_index": 1, "text": "同一行\n同一行"},
                {"image_index": 2, "text": "同一行"},
            ],
        },
        phase="analysis",
    )
    assert len(catalog.ocr) == 2
    assert [loc["image_index"] for row in catalog.ocr for loc in row["locations"]] == [1, 1, 2]
    assert [row["kind"] for row in catalog.sources] == ["image_ocr", "image_ocr", "post_text"]


def test_projection_keeps_short_labels_and_symbol_differences():
    expected_texts = {"AI", "20.0%", "下降3", "张四"}
    catalog = build_source_catalog(
        {
            "transcript_raw": [
                {"id": 0, "start_ms": 0, "end_ms": 1000, "text": "增长20%/下降-3/张三"}
            ],
            "ocr": [{"timestamp_ms": 0, "text": "AI\n20.0%\n下降3\n张四"}],
        },
        phase="transcript_correction",
    )
    assert expected_texts <= {row["text"] for row in catalog.ocr}


def test_source_units_preserve_original_characters_and_ranges():
    text = "ΟΣ。" + "a" * 260
    catalog = build_source_catalog(
        {"transcript_corrected": [{"id": 7, "start_ms": 0, "end_ms": 5000, "text": text}]},
        phase="analysis",
    )
    assert "".join(row["text"] for row in catalog.sources) == text
    assert [len(row["text"]) for row in catalog.sources] == [3, 256, 4]
    for row in catalog.sources:
        location = row["locations"][0]
        assert (location["start_ms"], location["end_ms"]) == (0, 5000)
        span = catalog.refs[location["source_ref"]]
        assert catalog.containers[span.container][span.start : span.end] == row["text"]


@pytest.mark.parametrize("source_kind", ["video", "image_note"])
def test_projection_has_no_global_count_or_size_limit(source_kind):
    observations = [
        {"text": f"第{i}张表格" + "原话" * 120, "timestamp_ms": i * 5000, "image_index": i + 1}
        for i in range(30)
    ]
    observations[-1]["timestamp_ms"] = 130000
    catalog = build_source_catalog(
        {"metadata": {"source_kind": source_kind}, "ocr": observations}, phase="analysis"
    )
    assert len(catalog.ocr) == 30
    assert sum(len(row["text"]) for row in catalog.ocr) > 6000
    assert catalog.refs["o:29:0"].timestamp_ms == 130000
    assert catalog.refs["o:29:0"].image_index == 30


def test_subtitle_filter_uses_phase_and_does_not_change_quoted_characters():
    artifacts = {
        "transcript_raw": [{"id": 0, "start_ms": 0, "end_ms": 1000, "text": "错误识别"}],
        "transcript_corrected": [{"id": 0, "start_ms": 0, "end_ms": 1000, "text": "真实字幕"}],
        "ocr": [
            {"timestamp_ms": 6000, "text": "真实字幕"},
            {"timestamp_ms": 6001, "text": "真实字幕"},
        ],
    }
    correction = build_source_catalog(artifacts, phase="transcript_correction")
    analysis = build_source_catalog(artifacts, phase="analysis")
    assert len(correction.ocr[0]["locations"]) == 2
    assert analysis.ocr[0]["locations"] == [{"source_ref": "o:1:0", "timestamp_ms": 6001}]
    assert "o:0:0" not in analysis.refs


def test_correction_context_omits_analysis_and_does_not_lookup_prior_claims(service, monkeypatch):
    job = make_gateway_job(service)

    def forbidden_lookup(*args, **kwargs):
        raise AssertionError("校正不应检索既有知识")

    monkeypatch.setattr(service.indexer, "find_related_claims", forbidden_lookup)
    ctx = service.get_analysis_context(job.id, gateway_protocol=2)
    assert ctx["phase"] == "transcript_correction"
    assert ctx["transcript"][0] == {"id": 0, "start_ms": 0, "end_ms": 5000, "text": "第一段原话。"}
    assert "correction_schema" in ctx
    assert {
        "analysis_schema",
        "existing_knowledge",
        "transcript_raw",
        "transcript_corrected",
    }.isdisjoint(ctx)
    assert service.database.get_job(job.id) == job


def test_analysis_context_uses_effective_sources_once(service, monkeypatch):
    job = make_gateway_job(service, phase="analysis")
    metadata = {
        **job.artifacts["metadata"],
        "post_text": "正文原文",
        "description": "正文原文",
        "media_path": "/private/path.mp4",
    }
    job = service.database.update_job(
        job.id,
        artifacts={
            "metadata": metadata,
            "media_provenance": {
                "asr": {
                    "provider": "asr",
                    "confidence_status": "unavailable",
                    "model_path": "/private/model",
                }
            },
            "ocr_warning": "OCR 未识别文字",
        },
    )
    prior = {
        "entry_id": "dy-prior",
        "claim_id": "prior-1",
        "claim_text": "先前观点",
        "entry_title": "旧资料",
        "source_quote": "旧原话",
        "source_path": "large/path",
    }
    monkeypatch.setattr(service.indexer, "find_related_claims", lambda text: [prior])
    ctx = service.get_analysis_context(job.id, gateway_protocol=2)
    assert [row["text"] for row in ctx["sources"]] == ["第一段原话。", "原始第二段。", "正文原文"]
    assert {"transcript", "transcript_raw", "transcript_corrected", "ocr"}.isdisjoint(ctx)
    assert "description" not in ctx["metadata"] and "post_text" not in ctx["metadata"]
    assert "media_path" not in ctx["metadata"]
    assert ctx["media_provenance"]["asr"] == {"provider": "asr", "confidence_status": "unavailable"}
    assert ctx["media_provenance"]["ocr_warning"] == "OCR 未识别文字"
    assert ctx["inspirations_verbatim"][0]["text"] == "逐字保留的灵感"
    assert set(ctx["existing_knowledge"][0]) == {
        "entry_id",
        "claim_id",
        "claim_text",
        "entry_title",
    }
    assert service.database.get_job(job.id) == job


def test_schema_keeps_business_title_and_constraints():
    schema = compact_analysis_schema()
    assert "title" in schema["properties"] and "title" in schema["required"]
    assert "title" not in schema
    assert {
        "summary",
        "core_points",
        "evidence",
        "steps",
        "applicable_scenarios",
        "risks",
        "claims",
        "ai_judgment",
    }.isdisjoint(schema["properties"])
    assert schema["properties"]["chapters"]["maxItems"] == 12
    assert schema["properties"]["takeaways"]["maxItems"] == 5
    assert len(schema["properties"]["content_card"]["discriminator"]["mapping"]) == 8
    chapter_ref = schema["properties"]["chapters"]["items"]["$ref"].split("/")[-1]
    assert schema["$defs"][chapter_ref]["properties"]["evidence"]["maxItems"] == 6


def test_transport_shape_accepts_refs_and_explicit_evidence():
    for atom in [
        {"id": "k", "statement": "事实", "source_refs": ["a:0:0"]},
        {"id": "k", "statement": "事实", "quote": "第一段原话。", "timestamp_ms": 0},
        {"id": "k", "statement": "判断", "atom_type": "inference", "provenance": "ai_inference"},
    ]:
        value = {"title": "资料", "knowledge_atoms": [atom]}
        before = deepcopy(value)
        validate_compact_analysis_shape(value)
        assert value == before
    for atom in [
        {"id": "k", "statement": "事实"},
        {"id": "k", "statement": "事实", "source_refs": []},
        {"id": "k", "statement": "事实", "quote": "原话"},
        {
            "id": "k",
            "statement": "判断",
            "atom_type": "inference",
            "provenance": "ai_inference",
            "source_refs": ["a:0:0"],
        },
    ]:
        with pytest.raises((ValueError, JobStateError)):
            validate_compact_analysis_shape({"title": "资料", "knowledge_atoms": [atom]})


def test_revision_is_stable_and_invalidates_source_changes(service, monkeypatch):
    from douyin_wiki import gateway_protocol

    job = make_gateway_job(service, phase="analysis")
    revision = context_revision(job)
    assert re.fullmatch(r"[0-9a-f]{64}", revision)
    assert context_revision(job.model_copy(deep=True)) == revision
    for key in ["transcript_raw", "transcript_corrected", "ocr", "metadata"]:
        changed = job.model_copy(deep=True)
        changed.artifacts[key] = {"changed": key}
        assert context_revision(changed) != revision
    changed = job.model_copy(deep=True)
    changed.request.inspirations = [InspirationInput(text="新灵感")]
    assert context_revision(changed) != revision
    assert (
        context_revision(
            job.model_copy(update={"updated_at": job.updated_at + timedelta(seconds=1)})
        )
        != revision
    )
    assert (
        context_revision(job.model_copy(update={"result": {"phase": "transcript_correction"}}))
        != revision
    )
    monkeypatch.setattr(service.indexer, "find_related_claims", lambda text: [{"new": "prior"}])
    assert context_revision(job) == revision
    monkeypatch.setattr(gateway_protocol, "CONTENT_TAG_RULES", ["新标签规则"])
    assert context_revision(job) != revision


def test_revision_survives_a_fresh_python_process(service):
    job = make_gateway_job(service)
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            "import sys; from douyin_wiki.models import JobRecord; "
            "from douyin_wiki.gateway_protocol import context_revision; "
            "print(context_revision(JobRecord.model_validate_json(sys.stdin.read())))",
        ],
        input=job.model_dump_json(),
        text=True,
        capture_output=True,
        check=True,
    )
    assert result.stdout.strip() == context_revision(job)


@pytest.mark.parametrize(
    "status,next_tool",
    [
        (JobStatus.AWAITING_AGENT_ANALYSIS, "get_analysis_context"),
        (JobStatus.QUEUED, "list_job_events"),
        (JobStatus.COMPLETED, None),
    ],
)
def test_compact_receipt_preserves_stage_routing_and_silence(service, status, next_tool):
    job = make_gateway_job(
        service,
        phase="analysis",
        artifacts={
            "favorites_context": {
                "parent_job_id": "parent-1",
                "batch_silent": True,
                "works": ["large"],
            }
        },
    )
    receipt = compact_job_payload(job.model_copy(update={"status": status}))
    assert receipt["next_tool"] == next_tool
    assert receipt["favorites_context"]["parent_job_id"] == "parent-1"
    assert receipt["favorites_context"]["batch_silent"] is True
    assert "works" not in receipt["favorites_context"]
    assert "artifacts" not in receipt and "request" not in receipt
    assert (
        receipt["context_revision"] is not None
        if next_tool == "get_analysis_context"
        else receipt["context_revision"] is None
    )


def test_compact_receipt_keeps_auth_and_confirmation_explanations(service):
    job = make_gateway_job(service)
    confirmation = compact_job_payload(
        job.model_copy(
            update={
                "status": JobStatus.WAITING_CONFIRMATION,
                "result": {"reason": "ai_analysis_confirmation_required", "duration_minutes": 31.0},
            }
        )
    )
    assert confirmation["result"]["reason"] == "ai_analysis_confirmation_required"
    assert confirmation["result"]["duration_minutes"] == 31.0
    auth = compact_job_payload(
        job.model_copy(
            update={
                "status": JobStatus.NEEDS_AUTH,
                "error_code": "browser_auth_required",
                "error_message": "请登录",
                "result": {"auth_scope": "video"},
            }
        )
    )
    assert auth["error_code"] == "browser_auth_required" and auth["error_message"] == "请登录"
    assert auth["result"]["auth_scope"] == "video"


def test_legacy_context_and_non_gateway_behavior_remain(service):
    job = make_gateway_job(service)
    ctx = service.get_analysis_context(job.id)
    assert ctx == service.get_analysis_context(job.id, gateway_protocol=1)
    assert "transcript_raw" in ctx and "summary" in ctx["analysis_schema"]["properties"]
    for mode in [AnalysisMode.PROVIDER, AnalysisMode.LOCAL]:
        service.config.analysis_mode = mode
        with pytest.raises(JobStateError):
            service.get_analysis_context(job.id, gateway_protocol=2)


@pytest.mark.parametrize("value", [True, False, 2.0, "2", 3, None])
def test_protocol_rejects_values_other_than_integer_versions(value):
    with pytest.raises(ValueError):
        validate_gateway_protocol(value)


@pytest.mark.parametrize("phase", ["transcript_correction", "analysis"])
def test_duplicate_audio_ids_do_not_expose_ambiguous_references(phase):
    artifacts = {
        "transcript_raw": [
            {"id": 0, "start_ms": 0, "end_ms": 1000, "text": "第一段原文"},
            {"id": 0, "start_ms": 1000, "end_ms": 2000, "text": "第二段不同原文"},
        ]
    }
    before = deepcopy(artifacts)
    with pytest.raises(JobStateError) as error:
        build_source_catalog(artifacts, phase=phase)
    assert error.value.details["reason"] == "invalid_source_ref"
    assert artifacts == before


@pytest.mark.parametrize(
    "atom",
    [
        {"id": "k", "statement": "事实", "quote": "原文", "timestamp_ms": 0, "source_refs": None},
        {
            "id": "k",
            "statement": "推断",
            "atom_type": "inference",
            "provenance": "ai_inference",
            "source_refs": ["a:0:0"],
        },
        {
            "id": "k",
            "statement": "推断",
            "atom_type": "inference",
            "provenance": "ai_inference",
            "quote": "原文",
            "timestamp_ms": 0,
            "source_refs": ["a:0:0"],
        },
    ],
)
def test_advertised_schema_rejects_null_refs_and_inference_with_refs(atom):
    validator = Draft202012Validator(compact_analysis_schema())
    assert list(validator.iter_errors({"title": "资料", "knowledge_atoms": [atom]}))


def test_advertised_schema_accepts_omitted_refs_and_valid_ref_evidence():
    schema = compact_analysis_schema()
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    for atom in [
        {"id": "k", "statement": "事实", "quote": "原文", "timestamp_ms": 0},
        {"id": "k", "statement": "事实", "source_refs": ["a:0:0"]},
        {"id": "k", "statement": "推断", "atom_type": "inference", "provenance": "ai_inference"},
    ]:
        incoming = {"title": "资料", "knowledge_atoms": [atom]}
        validator.validate(incoming)
        validate_compact_analysis_shape(incoming)


@pytest.mark.parametrize("role", ["atom", "chapter"])
def test_advertised_source_enum_accepts_omission_but_not_null(role):
    evidence = {"quote": "第一段原话。", "timestamp_ms": 0}
    if role == "atom":
        evidence.update(id="k", statement="第一段原话")
        incoming = {"title": "资料", "knowledge_atoms": [evidence]}
        definition, field = "RefKnowledgeAtom", "provenance"
    else:
        incoming = {
            "title": "资料",
            "chapters": [
                {"start_ms": 0, "title": "第一章", "summary": "原话", "evidence": [evidence]}
            ],
        }
        definition, field = "RefChapterEvidence", "evidence_type"
    schema = compact_analysis_schema()
    validator = Draft202012Validator(schema)
    validator.validate(incoming)
    validate_compact_analysis_shape(incoming)
    evidence[field] = None
    assert not validator.is_valid(incoming)
    with pytest.raises(ValueError):
        validate_compact_analysis_shape(incoming)
    # Clients must not fill an omitted, reference-derived source from a schema default.
    assert "default" not in schema["$defs"][definition]["properties"][field]
