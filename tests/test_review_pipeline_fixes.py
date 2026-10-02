"""Publication and model-evidence regressions from the repository review."""

import json
import sqlite3

import pytest
from pydantic import ValidationError

from douyin_wiki.adapters.llm import (
    ModelOutputError,
    OpenAICompatibleProvider,
    _soft_merge_analysis_partials,
)
from douyin_wiki.config import LLMSettings
from douyin_wiki.models import AnalysisResult, JobStatus, TranscriptSegment
from douyin_wiki.worker import Worker


async def capture(service):
    service.capture_douyin("https://v.douyin.com/uvHsRpXIn8s/")
    completed = await Worker(service).run_once()
    assert completed.status == JobStatus.COMPLETED, completed.error_message
    return completed.result["entry_id"]


def provider(monkeypatch):
    monkeypatch.setattr("douyin_wiki.adapters.llm.get_secret", lambda _: "")
    return OpenAICompatibleProvider(
        LLMSettings(enabled=True, base_url="http://127.0.0.1:8000/v1", model="review-test")
    )


@pytest.mark.asyncio
async def test_submit_analysis_database_failure_preserves_published_documents(service):
    entry_id = await capture(service)
    entry_before = service.database.get_entry(entry_id)
    data_before = service.database.get_entry_data(entry_id)
    chunks_before = service.database.fetch_chunks(entry_ids=[entry_id])
    documents_before = service.get_entry(entry_id, include_documents=True)
    with service.database.connect() as conn:
        conn.execute(
            "CREATE TRIGGER abort_analysis BEFORE UPDATE ON entries "
            "BEGIN SELECT RAISE(ABORT, 'projection rejected'); END"
        )

    with pytest.raises(sqlite3.IntegrityError, match="projection rejected"):
        service.submit_analysis(
            entry_id,
            {
                **data_before["analysis"],
                "one_liner": "发布失败的新分析", "summary": "发布失败的新分析",
            },
            producer="review-test",
        )

    assert service.database.get_entry(entry_id) == entry_before
    assert service.database.get_entry_data(entry_id) == data_before
    assert service.database.fetch_chunks(entry_ids=[entry_id]) == chunks_before
    assert service.get_entry(entry_id, include_documents=True) == documents_before
    restored_entry, restored_data = service.vault.load_entries()[0]
    assert restored_entry.summary == entry_before.summary
    assert restored_data["analysis"] == data_before["analysis"]


@pytest.mark.asyncio
async def test_limited_merge_publishes_distinct_reminders_and_deduplicates_shared_content(
    service, monkeypatch
):
    analysis_provider = provider(monkeypatch)

    async def transcribe(audio_path, output_dir):
        return [
            TranscriptSegment(
                id=index, start_ms=index * 1000, end_ms=(index + 1) * 1000,
                text=f"第{index}句：检查信息源",
            )
            for index in range(81)
        ]

    async def respond(system, user, **kwargs):
        payload = json.loads(user)
        if "segments" in payload:
            return {"segments": [{"id": item["id"], "text": item["text"]}
                                 for item in payload["segments"]]}
        if "partial_analyses" in payload:
            raise ModelOutputError("merge output limit")
        quote = payload["transcript"][0]["text"]
        return AnalysisResult(
            title="提醒汇总",
            reminders=[
                {"id": "reminder-1", "title": quote, "reason": "分段提醒",
                 "source_quote": quote, "confidence": 0.8, "needs_clarification": True},
                {"id": "shared", "title": "检查信息源", "reason": "共享提醒",
                 "source_quote": "检查信息源", "confidence": 0.8,
                 "needs_clarification": True},
            ],
        ).model_dump(mode="json")

    monkeypatch.setattr(service.transcriber, "transcribe", transcribe)
    monkeypatch.setattr(analysis_provider, "_json_call", respond)
    service.analysis = analysis_provider
    entry_id = await capture(service)

    exported = service.get_entry(entry_id, include_documents=True)
    reminders = exported["data"]["analysis"]["reminders"]
    assert {item["title"] for item in reminders} == {
        "第0句：检查信息源", "第80句：检查信息源", "检查信息源",
    }
    assert len(reminders) == len({item["id"] for item in reminders}) == 3
    for item in reminders:
        persisted, status, system_id = service.database.get_reminder(entry_id, item["id"])
        assert persisted.model_dump(mode="json") == item
        assert status == "candidate" and system_id is None
    restored = service.vault.load_entries()[0][1]["analysis"]["reminders"]
    assert restored == reminders


def test_analysis_boundary_rejects_duplicate_reminder_ids():
    reminders = [
        {"id": "duplicate", "title": title, "reason": "原话", "source_quote": title,
         "confidence": 0.8}
        for title in ["整理资料", "检查日期"]
    ]
    with pytest.raises(ValidationError, match="提醒.*id.*重复"):
        AnalysisResult(title="重复提醒", reminders=reminders)


def test_limited_merge_reminder_identity_depends_on_content():
    reminder = {
        "id": "first", "title": "整理资料", "reason": "原话",
        "source_quote": "整理资料", "confidence": 0.8,
    }
    partials = [
        AnalysisResult(title="分段", reminders=[reminder]).model_dump(mode="json"),
        AnalysisResult(title="分段", reminders=[{**reminder, "id": "second"}]).model_dump(
            mode="json"
        ),
    ]

    first = _soft_merge_analysis_partials(partials, reason="降级")
    second = _soft_merge_analysis_partials(list(reversed(partials)), reason="降级")

    assert len(first["reminders"]) == 1
    assert first["reminders"] == second["reminders"]


@pytest.mark.asyncio
@pytest.mark.parametrize("already_cached", [False, True])
async def test_provider_reanalysis_repairs_evidence_even_when_analysis_is_cached(
    service, monkeypatch, already_cached
):
    entry_id = await capture(service)
    wrong = AnalysisResult(
        title="修复后的分析",
        knowledge_atoms=[{"id": "source", "statement": "筛选财经媒体", "timestamp_ms": 60000,
                          "quote": "离职以后我取关了很多财经媒体。"}],
    )
    analysis_provider = provider(monkeypatch)

    async def respond(system, user, **kwargs):
        return wrong.model_dump(mode="json")

    monkeypatch.setattr(analysis_provider, "_json_call", respond)
    service.analysis = analysis_provider
    job = service.reanalyze_entry(entry_id, force=True)
    if already_cached:
        service.database.update_job(job.id, artifacts={"analysis": wrong.model_dump(mode="json")})

    completed = await Worker(service).run_once()

    assert completed.status == JobStatus.COMPLETED, completed.error_message
    assert completed.artifacts["llm_checkpoints"] == {}
    exported = service.get_entry(entry_id, include_documents=True)["data"]["analysis"]
    assert exported["knowledge_atoms"][0]["timestamp_ms"] == 0
    assert exported["claims"][0]["start_ms"] == 0
    assert service.vault.load_entries()[0][1]["analysis"] == exported


@pytest.mark.asyncio
async def test_provider_prune_does_not_export_removed_claims(service, monkeypatch):
    async def respond(segments, ocr, inspirations, metadata):
        return AnalysisResult(
            title="只保留可信原话",
            knowledge_atoms=[
                {"id": "bogus", "statement": "虚构事实", "timestamp_ms": 0,
                 "quote": "REVIEW_NEVER_SPOKEN"},
                {"id": "verified", "statement": "曾取关财经媒体", "timestamp_ms": 0,
                 "quote": "离职以后我取关了很多财经媒体。"},
            ],
        )

    monkeypatch.setattr(service.analysis, "analyze", respond)
    service.capture_douyin("https://v.douyin.com/uvHsRpXIn8s/")
    completed = await Worker(service).run_once()

    assert completed.status == JobStatus.COMPLETED_WITH_WARNINGS
    exported = service.get_entry(completed.result["entry_id"], include_documents=True)
    analysis = exported["data"]["analysis"]
    assert [item["id"] for item in analysis["knowledge_atoms"]] == ["verified"]
    assert [item["id"] for item in analysis["claims"]] == ["verified"]
    assert "REVIEW_NEVER_SPOKEN" not in exported["machine_markdown"]
    assert service.vault.load_entries()[0][1]["analysis"] == analysis


@pytest.mark.asyncio
async def test_invalid_cached_reanalysis_can_request_fresh_model_result(service, monkeypatch):
    entry_id = await capture(service)
    job = service.reanalyze_entry(entry_id, force=True)
    reminder = {
        "id": "duplicate", "title": "检查资料", "reason": "原话",
        "source_quote": "离职以后我取关了很多财经媒体。", "confidence": 0.8,
    }
    bad_analysis = {"title": "旧缓存", "reminders": [reminder, reminder]}
    service.database.update_job(job.id, artifacts={
        "analysis": bad_analysis, "llm_checkpoints": {"old-success": bad_analysis},
    })

    failed = await Worker(service).run_once()

    assert failed.status == JobStatus.FAILED
    assert "analysis" not in failed.artifacts
    assert failed.artifacts["llm_checkpoints"] == {}

    analysis_provider = provider(monkeypatch)

    async def respond(system, user, **kwargs):
        return AnalysisResult(title="新的分析结果").model_dump(mode="json")

    monkeypatch.setattr(analysis_provider, "_json_call", respond)
    service.analysis = analysis_provider
    service.retry_job(job.id)
    completed = await Worker(service).run_once()

    assert completed.status == JobStatus.COMPLETED
    assert service.database.get_entry(entry_id).title == "新的分析结果"


@pytest.mark.asyncio
async def test_reanalysis_reports_removed_evidence_as_warning(service, monkeypatch):
    entry_id = await capture(service)

    async def analyze(segments, ocr, inspirations, metadata):
        return AnalysisResult(title="保留可靠内容", knowledge_atoms=[{
            "id": "bogus", "statement": "虚构事实", "timestamp_ms": 0,
            "quote": "REVIEW_NEVER_SPOKEN",
        }])

    monkeypatch.setattr(service.analysis, "analyze", analyze)
    service.reanalyze_entry(entry_id, force=True)
    completed = await Worker(service).run_once()

    assert completed.status == JobStatus.COMPLETED_WITH_WARNINGS
    assert completed.result["warnings"] == ["模型生成的 1 条无法核实的证据或内容已移除"]
    analysis = service.get_entry(entry_id)["data"]["analysis"]
    assert analysis["knowledge_atoms"] == analysis["claims"] == []
