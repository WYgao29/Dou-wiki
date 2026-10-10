"""Cross-entry captures share processing without losing callers or batch ownership."""

import asyncio

import pytest

from douyin_wiki.errors import JobLeaseLostError
from douyin_wiki.models import (
    AnalysisMode,
    CaptureOptions,
    CreatorWorkDecision,
    GatewayContext,
    InspirationInput,
    JobStatus,
    TranscriptCorrection,
)
from douyin_wiki.service import DouyinWikiService
from douyin_wiki.worker import Worker
from tests.conftest import FakeResolver
from tests.test_collection_flow import A, ready

SHORT = "https://v.douyin.com/unresolved/"
URL = f"https://www.douyin.com/video/{A}"


async def confirmed_collection(service):
    parent = await ready(service, ids=(A,))
    service.collections.select(parent, "selected", all_works=True)
    child = service.confirm_collection_import(parent).result["child_job_ids"][0]
    service.resolver = FakeResolver()
    return parent, child


async def confirmed_batch(service, kind, context):
    if kind == "collection":
        parent_id = await ready(service, ids=(A,), gateway_context=context)
        service.collections.select(parent_id, "selected", all_works=True)
        parent = service.confirm_collection_import(parent_id)
    elif kind == "favorites":
        from tests.test_favorites import scan, snapshot

        parent = await scan(service, snapshot(kinds=[(A, "video")]), gateway_context=context)
        parent = service.favorites.confirm(parent.id)
    else:
        from tests.test_creator import FakeCreatorAdapter, inventory_result

        service.creator_adapter = FakeCreatorAdapter(inventory_result(A))
        parent = service.capture_douyin_creator(
            "https://www.douyin.com/user/example-sec-uid", gateway_context=context
        )
        await service.process_claimed_job(parent)
        service.set_creator_work_selection(parent.id, CreatorWorkDecision.SELECTED, ordinals=[1])
        parent = service.confirm_creator_import(parent.id)
    service.resolver = FakeResolver()
    return parent.id, parent.result["child_job_ids"][0]


async def finish_gateway(service, job_id):
    job = service.get_job(job_id)
    rows = job.artifacts["transcript_raw"]
    service.submit_transcript_correction(
        job_id, [TranscriptCorrection(id=row["id"], text=row["text"]) for row in rows],
        producer="test",
    )
    service.submit_gateway_analysis(
        job_id,
        {
            "title": "财经媒体筛选方法",
            "one_liner": "按信息质量筛选媒体。",
            "knowledge_atoms": [{
                "id": "quality", "statement": "按信息质量筛选媒体。",
                "quote": rows[0]["text"], "timestamp_ms": rows[0]["start_ms"],
                "provenance": "audio",
            }],
            "tags": ["信息筛选"],
        },
        producer="test",
    )
    result = await Worker(service).run_once()
    assert result.id == job_id and result.status == JobStatus.COMPLETED
    return result


async def test_unresolved_capture_and_collection_use_one_gateway_pipeline(service):
    service.config.analysis_mode = AnalysisMode.GATEWAY
    context = GatewayContext(gateway="original", conversation_id="short-link")
    original = service.capture_douyin(SHORT, gateway_context=context)
    parent, duplicate = await confirmed_collection(service)
    assert duplicate != original.id  # Identity is still unknown at confirmation.
    assert (await Worker(service).run_once()).status == JobStatus.AWAITING_AGENT_ANALYSIS
    follower = await Worker(service).run_once()
    assert service.downloader.calls == 1
    assert follower.id == duplicate and follower.status == JobStatus.MONITORING
    assert service.get_job(parent).result["child_job_ids"] == [original.id]
    assert service.database.batch_child_ids(parent) == [original.id]
    assert service.database.get_job(original.id).request.gateway_context == context
    assert "collection_context" not in service.database.get_job(original.id).artifacts
    completed = await finish_gateway(service, original.id)
    assert service.get_job(duplicate).result["entry_id"] == completed.result["entry_id"]
    assert service.get_job(parent).status == JobStatus.COMPLETED


@pytest.mark.parametrize("share_text", [SHORT, URL])
async def test_late_capture_follows_gateway_owner_and_keeps_its_event_route(service, share_text):
    service.config.analysis_mode = AnalysisMode.GATEWAY
    parent, owner = await confirmed_collection(service)
    await Worker(service).run_once()
    context = GatewayContext(gateway="second", conversation_id="late-capture")
    duplicate = service.capture_douyin(
        share_text, inspirations=[InspirationInput(text="保留第二个入口的灵感")],
        gateway_context=context,
    )
    result = await Worker(service).run_once()
    assert result.id == duplicate.id and result.status == JobStatus.MONITORING
    assert service.downloader.calls == 1
    assert result.result["shared_capture_job_id"] == owner
    assert not service.database.list_job_timeline(duplicate.id)
    completed = await finish_gateway(service, owner)
    follower = service.get_job(duplicate.id)
    assert follower.status == JobStatus.COMPLETED
    assert follower.result["entry_id"] == completed.result["entry_id"]
    entry = service.database.get_entry(completed.result["entry_id"])
    assert [item.text for item in entry.inspirations] == ["保留第二个入口的灵感"]
    assert service.get_job(parent).status == JobStatus.COMPLETED
    events = service.database.list_job_timeline(duplicate.id)
    assert len(events) == 1 and events[0].gateway_context == context
    service.get_job(duplicate.id)
    assert len(service.database.list_job_timeline(duplicate.id)) == 1


async def test_late_capture_with_conflicting_options_does_not_download_again(service):
    service.config.analysis_mode = AnalysisMode.GATEWAY
    _, owner = await confirmed_collection(service)
    await Worker(service).run_once()
    duplicate = service.capture_douyin(SHORT, options=CaptureOptions(allow_long=True))
    result = await Worker(service).run_once()
    assert result.id == duplicate.id and result.status == JobStatus.FAILED
    assert "处理选项" in result.error_message
    assert service.downloader.calls == 1
    assert service.get_job(owner).status == JobStatus.AWAITING_AGENT_ANALYSIS
    assert not service.get_job(owner).request.options.allow_long


async def test_cancel_batch_keeps_capture_needed_by_independent_follower(service):
    service.config.analysis_mode = AnalysisMode.GATEWAY
    parent, owner = await confirmed_collection(service)
    await Worker(service).run_once()
    duplicate = service.capture_douyin(SHORT)
    await Worker(service).run_once()
    service.cancel_job(parent)
    assert service.get_job(owner).status == JobStatus.AWAITING_AGENT_ANALYSIS
    assert service.get_job(duplicate.id).status == JobStatus.MONITORING
    completed = await finish_gateway(service, owner)
    assert service.get_job(duplicate.id).result["entry_id"] == completed.result["entry_id"]
    assert service.get_job(parent).status == JobStatus.CANCELLED


@pytest.mark.parametrize("kind", ["collection", "favorites", "creator"])
async def test_cancel_batch_retracts_its_repointed_follower_only(service, kind):
    service.config.analysis_mode = AnalysisMode.GATEWAY
    owner_context = GatewayContext(gateway="original", conversation_id="independent-owner")
    owner = service.capture_douyin(SHORT, gateway_context=owner_context)
    batch_context = GatewayContext(gateway="batch", conversation_id="cancelled-batch")
    parent, follower = await confirmed_batch(service, kind, batch_context)
    assert follower != owner.id
    assert (await Worker(service).run_once()).status == JobStatus.AWAITING_AGENT_ANALYSIS
    assert (await Worker(service).run_once()).status == JobStatus.MONITORING
    assert service.database.batch_child_ids(parent) == [owner.id]
    # Another batch reuses the executor and must still be able to finish.
    other_parent, other_child = await confirmed_batch(service, "collection", None)
    assert other_child == owner.id

    service.cancel_job(parent)
    assert service.get_job(follower).status == JobStatus.CANCELLED
    assert service.get_job(owner.id).status == JobStatus.AWAITING_AGENT_ANALYSIS
    assert service.get_job(other_parent).status == JobStatus.MONITORING
    assert service.get_job(owner.id).request.gateway_context == owner_context
    assert service.get_job(follower).request.gateway_context == batch_context

    await finish_gateway(service, owner.id)
    service.capture_sharing.refresh_all()
    assert service.get_job(follower).status == JobStatus.CANCELLED
    assert service.get_job(parent).status == JobStatus.CANCELLED
    assert service.get_job(other_parent).status == JobStatus.COMPLETED
    assert [event.status for event in service.database.list_job_timeline(follower)] == [
        JobStatus.CANCELLED
    ]


async def test_cancel_last_independent_follower_stops_abandoned_batch_capture(service):
    service.config.analysis_mode = AnalysisMode.GATEWAY
    parent, owner = await confirmed_collection(service)
    await Worker(service).run_once()
    duplicates = [service.capture_douyin(SHORT) for _ in range(2)]
    await Worker(service).run_once()
    await Worker(service).run_once()
    service.cancel_job(parent)
    service.cancel_job(duplicates[0].id)
    assert service.get_job(owner).status == JobStatus.AWAITING_AGENT_ANALYSIS
    service.cancel_job(duplicates[1].id)
    assert service.get_job(owner).status == JobStatus.CANCELLED


async def test_repointed_batch_relations_protect_executor_until_last_batch_cancel(service):
    service.config.analysis_mode = AnalysisMode.GATEWAY
    original = service.capture_douyin(SHORT)
    parents = []
    for _ in range(2):
        parent, duplicate = await confirmed_collection(service)
        parents.append(parent)
    # Run the known canonical child first, then the older unresolved task joins it.
    await service.process_claimed_job(service.database.get_job(duplicate))
    await Worker(service).run_once()
    assert service.downloader.calls == 1
    assert service.get_job(original.id).status == JobStatus.MONITORING
    assert set(service.database.capture_parent_ids(duplicate)) == set(parents)
    # Cancelling a follower must not cancel an executor still used by a batch.
    service.cancel_job(original.id)
    service.cancel_job(parents[0])
    assert service.get_job(duplicate).status == JobStatus.AWAITING_AGENT_ANALYSIS
    service.cancel_job(parents[1])
    assert service.get_job(duplicate).status == JobStatus.CANCELLED


@pytest.mark.parametrize("delete", [False, True])
async def test_cancelled_or_deleted_follower_is_not_revived_by_owner_completion(service, delete):
    service.config.analysis_mode = AnalysisMode.GATEWAY
    _, owner = await confirmed_collection(service)
    await Worker(service).run_once()
    duplicate = service.capture_douyin(SHORT, inspirations=[InspirationInput(text="已撤回")])
    await Worker(service).run_once()
    service.cancel_job(duplicate.id)
    if delete:
        service.delete_job(duplicate.id)
    completed = await finish_gateway(service, owner)
    follower = service.database.get_job(duplicate.id)
    assert follower.status == JobStatus.CANCELLED
    assert bool(follower.artifacts.get("user_deleted")) == delete
    assert not service.database.get_entry(completed.result["entry_id"]).inspirations


async def test_failed_follower_can_retry_without_reattaching_to_failed_owner(service):
    service.config.analysis_mode = AnalysisMode.GATEWAY
    _, owner = await confirmed_collection(service)
    await Worker(service).run_once()
    duplicate = service.capture_douyin(SHORT)
    await Worker(service).run_once()
    service.database.update_job(owner, status=JobStatus.FAILED, error_message="分析失败")
    service.refresh_job_parents(owner)
    assert service.get_job(duplicate.id).status == JobStatus.FAILED
    service.retry_job(duplicate.id)
    retried = await Worker(service).run_once()
    assert retried.id == duplicate.id and retried.status == JobStatus.AWAITING_AGENT_ANALYSIS
    assert "shared_capture_job_id" not in retried.artifacts
    assert service.downloader.calls == 2  # Explicit retry is a new attempt.


async def test_follower_retry_removes_old_executor_from_queued_and_downloading_receipts(
    service, monkeypatch
):
    from douyin_wiki import mcp_server
    from douyin_wiki.gateway_protocol import compact_job_payload

    service.config.analysis_mode = AnalysisMode.GATEWAY
    _, owner = await confirmed_collection(service)
    await Worker(service).run_once()
    context = GatewayContext(gateway="retry", conversation_id="follower-retry")
    follower = service.capture_douyin(SHORT, gateway_context=context)
    await Worker(service).run_once()
    service.database.update_job(owner, status=JobStatus.FAILED, error_message="分析失败")
    service.refresh_job_parents(owner)
    monkeypatch.setattr(mcp_server, "_SERVICE", service)
    retried = service.retry_job(follower.id)
    queued = mcp_server.get_job(follower.id, gateway_protocol=2)

    downloading, release = asyncio.Event(), asyncio.Event()
    original_download = service.downloader.download

    async def delayed_download(*args):
        downloading.set()
        await release.wait()
        return await original_download(*args)

    monkeypatch.setattr(service.downloader, "download", delayed_download)
    running = asyncio.create_task(Worker(service).run_once())
    try:
        await asyncio.wait_for(downloading.wait(), timeout=5)
        live_payload = mcp_server.get_job(follower.id, gateway_protocol=2)
    finally:
        release.set()
        processed = await running

    assert retried.status == JobStatus.QUEUED
    assert queued["status"] == JobStatus.QUEUED.value
    assert live_payload["status"] == JobStatus.DOWNLOADING.value
    assert processed.status == JobStatus.AWAITING_AGENT_ANALYSIS
    for payload in (compact_job_payload(retried), queued, live_payload):
        assert "shared_capture_job_id" not in payload["result"]
        assert "shared_job_status" not in payload["result"]
    assert "shared_capture_job_id" not in retried.artifacts
    assert service.get_job(follower.id).request.gateway_context == context
    assert service.downloader.calls == 2


async def test_ordinary_retry_keeps_gateway_result_and_media_checkpoints(service):
    service.config.analysis_mode = AnalysisMode.GATEWAY
    _, owner = await confirmed_collection(service)
    await Worker(service).run_once()
    failed = service.database.update_job(owner, status=JobStatus.FAILED, error_message="分析失败")
    retried = service.retry_job(owner)
    assert retried.status == JobStatus.QUEUED
    assert retried.result == failed.result
    assert retried.artifacts["metadata"] == failed.artifacts["metadata"]
    assert retried.artifacts["transcript_raw"] == failed.artifacts["transcript_raw"]
    assert (await Worker(service).run_once()).status == JobStatus.AWAITING_AGENT_ANALYSIS
    assert service.downloader.calls == 1


async def test_shared_capture_survives_service_restart(service):
    service.config.analysis_mode = AnalysisMode.GATEWAY
    _, owner = await confirmed_collection(service)
    await Worker(service).run_once()
    duplicate = service.capture_douyin(SHORT)
    await Worker(service).run_once()
    restarted = DouyinWikiService(
        service.config, resolver=service.resolver, downloader=service.downloader,
        media=service.media, transcriber=service.transcriber, ocr=service.ocr,
        analysis=service.analysis, embeddings=service.embeddings, reminders=service.reminders,
    )
    restarted.initialize(initialize_git=False)
    assert restarted.get_job(duplicate.id).status == JobStatus.MONITORING
    completed = await finish_gateway(restarted, owner)
    assert restarted.get_job(duplicate.id).result["entry_id"] == completed.result["entry_id"]
    assert service.downloader.calls == 1


async def test_expired_capture_claim_cannot_publish_shared_link(service):
    service.config.analysis_mode = AnalysisMode.GATEWAY
    _, owner = await confirmed_collection(service)
    await Worker(service).run_once()
    duplicate = service.capture_douyin(SHORT)
    service.database.update_job(
        duplicate.id, artifacts={"resolved": service.get_job(owner).artifacts["resolved"]}
    )
    with (
        service.database.claimed_job_updates(duplicate.id, "expired-worker"),
        pytest.raises(JobLeaseLostError),
    ):
        await service.process_claimed_job(duplicate)
    assert "shared_capture_job_id" not in service.database.get_job(duplicate.id).artifacts
    assert service.downloader.calls == 1
    assert service.get_job(owner).status == JobStatus.AWAITING_AGENT_ANALYSIS


async def test_favorites_confirm_reuses_executor_instead_of_older_follower(service):
    from tests.test_favorites import FakeFavoritesAdapter
    from tests.test_favorites import snapshot as favorite_snapshot

    service.config.analysis_mode = AnalysisMode.GATEWAY
    original = service.capture_douyin(SHORT)
    parent, owner = await confirmed_collection(service)
    await service.process_claimed_job(service.database.get_job(owner))
    await Worker(service).run_once()
    assert service.get_job(original.id).status == JobStatus.MONITORING
    service.favorites.adapter = FakeFavoritesAdapter(favorite_snapshot(kinds=[(A, "video")]))
    favorite = service.favorites.start()
    await service.process_claimed_job(favorite)
    service.favorites.select(favorite.id, selected=True)
    result = service.favorites.confirm(favorite.id)
    assert result.result["child_job_ids"] == [owner]
    service.cancel_job(original.id)
    service.cancel_job(parent)
    assert service.get_job(owner).status == JobStatus.AWAITING_AGENT_ANALYSIS
    service.cancel_job(favorite.id)
    assert service.get_job(owner).status == JobStatus.CANCELLED


async def test_intact_entry_reuse_precedes_active_task_option_conflict(service):
    from tests.test_collection_vault import import_entry

    entry = await import_entry(service)
    waiting = service.capture_douyin(URL, options=CaptureOptions(allow_long=True))
    duplicate = service.capture_douyin(URL, inspirations=[InspirationInput(text="补充灵感")])
    result = await service.process_claimed_job(duplicate)
    assert result.status == JobStatus.COMPLETED and result.result["entry_id"] == entry.id
    assert service.downloader.calls == 1
    assert service.get_job(waiting.id).status == JobStatus.QUEUED
    assert [item.text for item in service.database.get_entry(entry.id).inspirations] == ["补充灵感"]


@pytest.mark.parametrize("protocol", [1, 2])
async def test_mcp_shared_capture_query_exposes_executor_in_both_protocols(
    service, monkeypatch, protocol
):
    from douyin_wiki import mcp_server

    service.config.analysis_mode = AnalysisMode.GATEWAY
    _, owner = await confirmed_collection(service)
    await Worker(service).run_once()
    context = GatewayContext(gateway="caller", conversation_id="shared-query")
    duplicate = service.capture_douyin(SHORT, gateway_context=context)
    await Worker(service).run_once()
    monkeypatch.setattr(mcp_server, "_SERVICE", service)
    payload = mcp_server.get_job(duplicate.id, gateway_protocol=protocol)
    assert payload["status"] == JobStatus.MONITORING.value
    assert payload["result"]["shared_capture_job_id"] == owner
    assert payload["result"]["shared_job_status"] == JobStatus.AWAITING_AGENT_ANALYSIS.value
    if protocol == 2:
        assert payload["gateway_context"] == context.model_dump(mode="json")
