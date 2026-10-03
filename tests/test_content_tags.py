"""Content tags are checked on new analyses without restricting stored results."""

from __future__ import annotations

import json

import pytest

from douyin_wiki.adapters.llm import (
    ModelContextError,
    ModelOutputError,
    OpenAICompatibleProvider,
)
from douyin_wiki.config import LLMSettings
from douyin_wiki.errors import JobStateError, ModelConnectionError
from douyin_wiki.models import (
    AnalysisMode,
    AnalysisResult,
    CaptureRequest,
    JobStatus,
    OCRObservation,
    TranscriptCorrection,
    TranscriptSegment,
)
from douyin_wiki.webapp.app import create_app
from douyin_wiki.worker import Worker
from tests.support.web import _web_fixture, local_client
from tests.test_image_note import make_service
from tests.test_tags import source


def gateway_job(service):
    job = service.database.create_job(CaptureRequest(share_text="https://www.douyin.com/note/123"))
    return service.database.update_job(
        job.id,
        status=JobStatus.AWAITING_AGENT_ANALYSIS,
        artifacts={"metadata": {"source_kind": "image_note"}},
    )


@pytest.mark.parametrize("entrypoint", ["gateway", "entry"])
@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        ([], []),
        (["FDE"], ["FDE"]),
        ([" FDE ", "", "FDE", "驻场交付", "\t"], ["FDE", "驻场交付"]),
        (["Data Agent", "专用与通用", "企业落地"], ["Data Agent", "专用与通用", "企业落地"]),
        (["Agent", "agent", "AI Agent"], ["Agent", "agent", "AI Agent"]),
    ],
)
def test_new_analysis_normalizes_tags_without_approximate_reuse(
    tmp_path, entrypoint, tags, expected
):
    _, service = _web_fixture(tmp_path)
    payload = {"title": "内容分析", "tags": tags}
    if entrypoint == "gateway":
        job = gateway_job(service)
        result = service.submit_gateway_analysis(job.id, payload, producer="test")
        assert result.status == JobStatus.QUEUED
        assert result.artifacts["analysis"]["tags"] == expected
    else:
        entry = service.submit_analysis("dy-123", payload, producer="test")
        assert entry.tags == expected
        assert service.database.get_entry_data(entry.id)["analysis"]["tags"] == expected


@pytest.mark.parametrize("entrypoint", ["gateway", "entry"])
def test_new_analysis_rejects_four_tags_without_writing_or_requeueing(tmp_path, entrypoint):
    _, service = _web_fixture(tmp_path)
    payload = {"title": "不应写入", "tags": ["一", "二", "三", "四"]}
    before = service.database.get_entry_data("dy-123")
    path = service.config.vault_path / service.database.get_entry("dy-123").source_path
    document = path.read_bytes()
    if entrypoint == "gateway":
        job = gateway_job(service)
        with pytest.raises(JobStateError, match="标签.*3"):
            service.submit_gateway_analysis(job.id, payload, producer="test")
        assert service.database.get_job(job.id) == job
    else:
        with pytest.raises(JobStateError, match="标签.*3"):
            service.submit_analysis("dy-123", payload, producer="test")
    assert service.database.get_entry_data("dy-123") == before
    assert path.read_bytes() == document


def test_tag_limit_is_checked_before_confirmed_alias_resolution(tmp_path):
    _, service = _web_fixture(tmp_path)
    source(service, "1000", ["一", "二", "三", "四"], managed=True)
    service.tags.merge(["一", "二", "三", "四"], "一")
    with pytest.raises(JobStateError, match="标签.*3"):
        service.submit_analysis(
            "dy-123", {"title": "内容分析", "tags": ["一", "二", "三", "四"]}, producer="test"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["video", "image_note", "reanalyze"])
@pytest.mark.parametrize("too_many", [False, True])
async def test_provider_tag_boundary_covers_capture_and_reanalysis(
    tmp_path, service, monkeypatch, path, too_many
):
    if path == "image_note":
        service = make_service(tmp_path / "note")
    previous_entry = None
    if path == "reanalyze":
        service.capture_douyin("https://v.douyin.com/uvHsRpXIn8s/")
        completed = await Worker(service).run_once()
        previous_entry = service.database.get_entry(completed.result["entry_id"])
        previous_data = service.database.get_entry_data(previous_entry.id)
        previous_data["metadata"]["existing_tags"] = ["旧词表"]
        service.database.upsert_entry(previous_entry, previous_data)
        previous_document = (service.config.vault_path / previous_entry.source_path).read_bytes()
        service.reanalyze_entry(previous_entry.id, force=True)
    else:
        service.capture_douyin("https://v.douyin.com/uvHsRpXIn8s/")

    inputs = []
    tags = ["一", "二", "三", "四"] if too_many else [" FDE ", "FDE", "", "驻场交付"]

    async def analyze(segments, ocr, inspirations, metadata):
        inputs.append(metadata)
        return AnalysisResult(title="中心内容", tags=tags)

    monkeypatch.setattr(service.analysis, "analyze", analyze)
    if too_many:
        provider = OpenAICompatibleProvider(
            LLMSettings(enabled=True, base_url="http://localhost:1234", model="test")
        )

        async def json_call(system, user, **kwargs):
            payload = json.loads(user)
            if "校对器" in system:
                return {"segments": payload["segments"]}
            inputs.append(payload["metadata"])
            return {"title": "中心内容", "tags": tags}

        monkeypatch.setattr(provider, "_json_call", json_call)
        service.analysis = provider
    result = await Worker(service).run_once()
    assert len(inputs) == 1
    assert "existing_tags" not in inputs[0]
    if too_many:
        assert result.status == JobStatus.FAILED
        assert "标签" in result.error_message and "3" in result.error_message
        assert "analysis" not in result.artifacts
        assert result.artifacts["llm_checkpoints"] == {}
        if previous_entry:
            assert service.database.get_entry(previous_entry.id) == previous_entry
            assert service.database.get_entry_data(previous_entry.id) == previous_data
            assert (service.config.vault_path / previous_entry.source_path).read_bytes() == (
                previous_document
            )
        else:
            assert service.database.list_entries() == []
        tags = ["FDE", "驻场交付"]
        service.retry_job(result.id)
        result = await Worker(service).run_once()
        assert result.status == JobStatus.COMPLETED
        assert len(inputs) == 2
    else:
        assert result.status == JobStatus.COMPLETED
    entry = service.database.get_entry(result.result["entry_id"])
    assert entry.tags == ["FDE", "驻场交付"]
    data = service.database.get_entry_data(entry.id)
    assert data["analysis"]["tags"] == ["FDE", "驻场交付"]


def test_legacy_seven_tags_remain_readable_and_indexable(tmp_path):
    config, service = _web_fixture(tmp_path)
    legacy_tags = ["Jev模型", "营销案例", "System One", "Doomers", "Laya", "AI模型", "营销套路"]
    path = source(service, "1000", legacy_tags, managed=True)
    before = path.read_bytes()
    entry = service.database.get_entry("dy-1000")
    data = service.database.get_entry_data(entry.id)
    chunks, _, _ = service.prepare_entry_bundle(entry, data)
    assert chunks and all(chunk["tags_text"] == " ".join(legacy_tags) for chunk in chunks)
    with local_client(create_app(config, service=service, start_watcher=False)) as client:
        assert client.get("/api/articles/dy-1000").status_code == 200
        assert client.get("/api/library?tag=营销套路").json()["total"] == 1
    assert path.read_bytes() == before
    assert service.database.get_entry(entry.id).tags == legacy_tags


@pytest.mark.asyncio
@pytest.mark.parametrize("reanalyze", [False, True])
async def test_gateway_can_retry_an_inflight_result_from_before_tag_limit(service, reanalyze):
    if reanalyze:
        service.capture_douyin("https://v.douyin.com/uvHsRpXIn8s/")
        completed = await Worker(service).run_once()
        entry_id = completed.result["entry_id"]
        service.config.analysis_mode = AnalysisMode.GATEWAY
        job = service.reanalyze_entry(entry_id, force=True)
    else:
        service.config.analysis_mode = AnalysisMode.GATEWAY
        job = service.capture_douyin("https://v.douyin.com/uvHsRpXIn8s/")
    pending = await Worker(service).run_once()
    if not reanalyze:
        service.submit_transcript_correction(
            job.id,
            [
                TranscriptCorrection(id=row["id"], text=row["text"])
                for row in pending.artifacts["transcript_raw"]
            ],
            producer="test",
        )
    # This represents an analysis accepted by the earlier, unlimited version.
    service.database.requeue_job(
        job.id, artifacts={"analysis": {"title": "旧分析", "tags": ["一", "二", "三", "四"]}}
    )
    failed = await Worker(service).run_once()
    assert failed.status == JobStatus.FAILED
    service.retry_job(job.id)
    pending = await Worker(service).run_once()
    assert pending.status == JobStatus.AWAITING_AGENT_ANALYSIS
    assert "analysis" not in pending.artifacts
    service.submit_gateway_analysis(
        job.id, {"title": "中心内容", "tags": ["FDE", "驻场交付"]}, producer="test"
    )
    completed = await Worker(service).run_once()
    assert completed.status == JobStatus.COMPLETED
    assert service.database.get_entry(completed.result["entry_id"]).tags == ["FDE", "驻场交付"]


def split_tag_provider(monkeypatch, *, merge_failure="output"):
    """Keep chunking, validation and checkpoints real; replace the model transport."""
    monkeypatch.setattr("douyin_wiki.adapters.llm.get_secret", lambda _: "")
    provider = OpenAICompatibleProvider(
        LLMSettings(enabled=True, base_url="http://127.0.0.1:8000/v1", model="test")
    )
    requests = []
    original_fits = provider._fits

    def fits(system, payload, schema):
        if merge_failure == "input" and "partial_analyses" in payload:
            return False
        return original_fits(system, payload, schema)

    async def respond(system, user, *, response_schema, **kwargs):
        payload = json.loads(user)
        requests.append(payload)
        if "segments" in payload:
            return {"segments": payload["segments"]}
        if "partial_analyses" in payload:
            raise ModelOutputError("merge output limit")
        if set(response_schema["properties"]) == {"tags"}:
            return {"tags": [" FDE 与 Data Agent ", "", "交付边界", "交付边界"]}
        rows = payload["transcript"] or payload["ocr"]
        first = rows[0]
        beginning = first["text"].startswith("第0段")
        title = "FDE 驻场交付" if beginning else "Data Agent 专用与通用"
        quote = first["text"]
        is_video = bool(payload["transcript"])
        return AnalysisResult(
            title=title,
            one_liner=title,
            tags=["FDE", "驻场交付"] if beginning else ["Data Agent", "专用与通用"],
            knowledge_atoms=[
                {
                    "id": "atom",
                    "statement": title,
                    "quote": quote,
                    "provenance": "audio" if is_video else "image_ocr",
                    **({"timestamp_ms": first["start_ms"]} if is_video else {"image_index": 1}),
                }
            ],
            reminders=[
                {
                    "id": "reminder",
                    "title": title,
                    "reason": "原话",
                    "source_quote": quote,
                    "confidence": 0.8,
                    "needs_clarification": True,
                }
            ],
            chapters=[
                {
                    "start_ms": first["start_ms"],
                    "end_ms": rows[-1]["end_ms"],
                    "title": title,
                    "summary": title,
                    "evidence": [{"timestamp_ms": first["start_ms"], "quote": quote}],
                }
            ]
            if is_video
            else [],
        ).model_dump(mode="json")

    monkeypatch.setattr(provider, "_fits", fits)
    monkeypatch.setattr(provider, "_json_call", respond)
    return provider, requests


def split_transcript(count=81):
    return [
        TranscriptSegment(
            id=index,
            start_ms=index * 1000,
            end_ms=(index + 1) * 1000,
            text=f"第{index}段讨论企业交付边界。",
        )
        for index in range(count)
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["video", "image_note", "reanalyze"])
@pytest.mark.parametrize("merge_failure", ["input", "output"])
async def test_limited_merge_regenerates_whole_article_tags_without_losing_content(
    tmp_path, service, monkeypatch, path, merge_failure
):
    if path == "image_note":
        service = make_service(tmp_path / "note")

        async def recognize(frames):
            return [
                OCRObservation(image_index=1, text=f"第{i}段讨论企业交付边界。") for i in range(21)
            ]

        monkeypatch.setattr(service.ocr, "recognize", recognize)
    else:

        async def transcribe(audio_path, output_dir):
            return split_transcript()

        monkeypatch.setattr(service.transcriber, "transcribe", transcribe)
    if path == "reanalyze":
        service.capture_douyin("https://v.douyin.com/uvHsRpXIn8s/")
        previous = await Worker(service).run_once()
        assert previous.status == JobStatus.COMPLETED, previous.error_message
        service.reanalyze_entry(previous.result["entry_id"], force=True)
    else:
        service.capture_douyin("https://v.douyin.com/uvHsRpXIn8s/")
    provider, requests = split_tag_provider(monkeypatch, merge_failure=merge_failure)
    service.analysis = provider

    completed = await Worker(service).run_once()

    assert completed.status == JobStatus.COMPLETED, completed.error_message
    entry = service.database.get_entry(completed.result["entry_id"])
    assert entry.tags == ["FDE 与 Data Agent", "交付边界"]
    data = service.database.get_entry_data(entry.id)
    assert data["analysis"]["tags"] == entry.tags
    assert len(data["analysis"]["knowledge_atoms"]) == 2
    assert len(data["analysis"]["reminders"]) == 2
    assert len(data["analysis"]["chapters"]) == (0 if path == "image_note" else 2)
    assert all(
        chunk["tags_text"] == "FDE 与 Data Agent 交付边界"
        for chunk in service.database.fetch_chunks(entry_ids=[entry.id])
    )
    tag_request = requests[-1]
    assert "partial_analyses" not in tag_request
    assert "tags" not in json.dumps(tag_request, ensure_ascii=False)
    assert "FDE 驻场交付" in json.dumps(tag_request, ensure_ascii=False)
    assert "Data Agent 专用与通用" in json.dumps(tag_request, ensure_ascii=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["input", "context", "output"])
async def test_limited_tag_summary_preserves_content_with_empty_tags_and_a_reason(
    monkeypatch, failure
):
    provider, _ = split_tag_provider(monkeypatch)
    original_fits, original_call = provider._fits, provider._json_call

    def fits(system, payload, schema):
        if failure == "input" and set(schema["properties"]) == {"tags"}:
            return False
        return original_fits(system, payload, schema)

    async def respond(system, user, *, response_schema, **kwargs):
        if set(response_schema["properties"]) == {"tags"}:
            error = ModelOutputError if failure == "output" else ModelContextError
            raise error("tag summary limit")
        return await original_call(system, user, response_schema=response_schema, **kwargs)

    monkeypatch.setattr(provider, "_fits", fits)
    monkeypatch.setattr(provider, "_json_call", respond)

    result = await provider.analyze(split_transcript(), [], [], {})

    assert result.tags == []
    assert len(result.knowledge_atoms) == len(result.reminders) == len(result.chapters) == 2
    assert any("标签" in reason and "预算" in reason for reason in result.open_questions)
    assert any("标签" in note and "预算" in note for note in result.content_card.notes)


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [{"tags": ["一", "二", "三", "四"]}, {}])
async def test_tag_summary_rejects_invalid_output_without_truncating_or_caching(monkeypatch, reply):
    provider, _ = split_tag_provider(monkeypatch)
    original_call = provider._json_call

    async def respond(system, user, *, response_schema, **kwargs):
        if set(response_schema["properties"]) == {"tags"}:
            return reply
        return await original_call(system, user, response_schema=response_schema, **kwargs)

    monkeypatch.setattr(provider, "_json_call", respond)
    checkpoints = {}
    with pytest.raises(ValueError, match="标签|tags"):
        await provider.analyze(split_transcript(), [], [], {}, checkpoints=checkpoints)
    assert len(checkpoints) == 2
    assert all("title" in value for value in checkpoints.values())


@pytest.mark.asyncio
async def test_tag_summary_connection_failure_retries_without_reanalyzing_finished_parts(
    service, monkeypatch
):
    provider, requests = split_tag_provider(monkeypatch)
    original_call = provider._json_call
    fail = True

    async def transcribe(audio_path, output_dir):
        return split_transcript()

    async def respond(system, user, *, response_schema, **kwargs):
        if fail and set(response_schema["properties"]) == {"tags"}:
            raise ModelConnectionError("temporary connection failure")
        return await original_call(system, user, response_schema=response_schema, **kwargs)

    monkeypatch.setattr(service.transcriber, "transcribe", transcribe)
    monkeypatch.setattr(provider, "_json_call", respond)
    service.analysis = provider
    service.capture_douyin("https://v.douyin.com/uvHsRpXIn8s/")

    failed = await Worker(service).run_once()

    assert failed.status == JobStatus.FAILED
    assert failed.error_code == "model_connection_error"
    analysis_checkpoints = [
        value for value in failed.artifacts["llm_checkpoints"].values() if "tags" in value
    ]
    assert len(analysis_checkpoints) == 2
    assert service.database.list_entries() == []
    fail = False
    service.retry_job(failed.id)
    completed = await Worker(service).run_once()
    assert completed.status == JobStatus.COMPLETED, completed.error_message
    assert service.database.get_entry(completed.result["entry_id"]).tags == [
        "FDE 与 Data Agent",
        "交付边界",
    ]
    assert len([request for request in requests if "transcript" in request]) == 2


@pytest.mark.asyncio
async def test_successful_final_merge_after_nested_fallback_keeps_its_tags(monkeypatch):
    provider, requests = split_tag_provider(monkeypatch)
    original_call = provider._json_call

    async def respond(system, user, *, response_schema, **kwargs):
        payload = json.loads(user)
        if len(payload.get("partial_analyses", [])) == 3:
            requests.append(payload)
            return AnalysisResult(title="整篇分析", tags=["跨段对象", "核心判断"]).model_dump(
                mode="json"
            )
        return await original_call(system, user, response_schema=response_schema, **kwargs)

    monkeypatch.setattr(provider, "_json_call", respond)

    result = await provider.analyze(split_transcript(321), [], [], {})

    assert result.tags == ["跨段对象", "核心判断"]
    assert len(result.chapters) == 5
    assert all("content_outline" not in request for request in requests)


@pytest.mark.asyncio
async def test_fallback_tag_summary_includes_compatible_v1_content(monkeypatch):
    provider, requests = split_tag_provider(monkeypatch)
    original_call = provider._json_call

    async def respond(system, user, *, response_schema, **kwargs):
        reply = await original_call(system, user, response_schema=response_schema, **kwargs)
        if "title" in reply:
            return {
                "title": reply["title"],
                "tags": reply["tags"],
                "summary": "旧格式摘要中的企业交付边界。",
                "core_points": ["旧格式收获中的专用工具定位。"],
            }
        return reply

    monkeypatch.setattr(provider, "_json_call", respond)

    result = await provider.analyze(split_transcript(), [], [], {})

    assert result.tags == ["FDE 与 Data Agent", "交付边界"]
    tag_input = json.dumps(requests[-1], ensure_ascii=False)
    assert "旧格式摘要中的企业交付边界。" in tag_input
    assert "旧格式收获中的专用工具定位。" in tag_input
