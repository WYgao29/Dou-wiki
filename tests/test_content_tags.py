"""Content tags are checked on new analyses without restricting stored results."""

from __future__ import annotations

import pytest

from douyin_wiki.errors import JobStateError
from douyin_wiki.models import (
    AnalysisMode,
    CaptureRequest,
    JobStatus,
    TranscriptCorrection,
)
from douyin_wiki.webapp.app import create_app
from douyin_wiki.worker import Worker
from tests.support.gateway import complete_gateway_job
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
async def test_gateway_tag_boundary_covers_capture_and_reanalysis(
    tmp_path, service, path, too_many
):
    from tests.support.gateway import complete_gateway_job

    if path == "image_note":
        service = make_service(tmp_path / "note")
    previous_entry = None
    job = service.capture_douyin("https://v.douyin.com/uvHsRpXIn8s/")
    await Worker(service).run_once()
    if path == "reanalyze":
        completed = await complete_gateway_job(service, job.id)
        previous_entry = service.database.get_entry(completed.result["entry_id"])
        previous_data = service.database.get_entry_data(previous_entry.id)
        previous_data["metadata"]["existing_tags"] = ["旧词表"]
        service.database.upsert_entry(previous_entry, previous_data)
        previous_document = (service.config.vault_path / previous_entry.source_path).read_bytes()
        job = service.reanalyze_entry(previous_entry.id, force=True)
        await Worker(service).run_once()
    context = service.get_analysis_context(job.id)
    assert "existing_tags" not in context["metadata"]
    if too_many:
        if service.get_job(job.id).result.get("phase") == "transcript_correction":
            service.submit_transcript_correction(
                job.id,
                [TranscriptCorrection(id=row["id"], text=row["text"])
                 for row in context["transcript_raw"]], producer="test-agent",
            )
        with pytest.raises(JobStateError, match="标签最多 3"):
            service.submit_gateway_analysis(
                job.id, {"title": "中心内容", "tags": ["一", "二", "三", "四"]},
                producer="test-agent",
            )
        assert "analysis" not in service.database.get_job(job.id).artifacts
        if previous_entry:
            assert service.database.get_entry(previous_entry.id) == previous_entry
            assert service.database.get_entry_data(previous_entry.id) == previous_data
            assert (
                service.config.vault_path / previous_entry.source_path
            ).read_bytes() == previous_document
        else:
            assert service.database.list_entries() == []
    result = await complete_gateway_job(
        service, job.id, analysis={"title": "中心内容", "tags": [" FDE ", "FDE", "", "驻场交付"]},
    )
    entry = service.database.get_entry(result.result["entry_id"])
    assert entry.tags == ["FDE", "驻场交付"]
    assert service.database.get_entry_data(entry.id)["analysis"]["tags"] == ["FDE", "驻场交付"]


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
        if completed.status == JobStatus.AWAITING_AGENT_ANALYSIS:
            completed = await complete_gateway_job(service, completed.id)
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
