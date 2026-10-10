from __future__ import annotations

from douyin_wiki.models import JobStatus
from tests.test_collection_flow import MIX, A, FakeCollectionAdapter, ready, snapshot


async def test_collection_confirmed_parent_can_be_found_from_shared_child(service):
    first = await ready(service, ids=(A,))
    service.collections.select(first, "selected", all_works=True)
    child = service.collections.confirm(first).result["child_job_ids"][0]
    second = await ready(service, ids=(A,))
    service.collections.select(second, "selected", all_works=True)
    assert service.collections.confirm(second).result["child_job_ids"] == [child]
    assert set(service.database.capture_parent_ids(child)) == {first, second}


async def test_collection_failed_child_updates_every_related_parent(service):
    parents = []
    for _ in range(2):
        parent = await ready(service, ids=(A,))
        service.collections.select(parent, "selected", all_works=True)
        child = service.collections.confirm(parent).result["child_job_ids"][0]
        parents.append(parent)
    service.database.update_job(child, status=JobStatus.FAILED, error_message="下载失败")
    service.collections.refresh_for_child(child)
    for parent in parents:
        record = service.database.get_job(parent)
        assert record.status == JobStatus.COMPLETED_WITH_WARNINGS
        assert record.result["summary"]["failed"] == 1


async def test_collection_cancelled_parent_is_not_revived_by_late_refresh(service):
    parent = await ready(service, ids=(A,))
    service.collections.select(parent, "selected", all_works=True)
    child = service.collections.confirm(parent).result["child_job_ids"][0]
    service.database.update_job(parent, status=JobStatus.CANCELLED)
    service.database.update_job(child, status=JobStatus.FAILED)
    service.collections.refresh_for_child(child)
    assert service.database.get_job(parent).status == JobStatus.CANCELLED


async def test_worker_routes_collection_without_downloading(service):
    from douyin_wiki.adapters.share import DouyinShareResolver

    service.resolver = DouyinShareResolver()
    service.collections.adapter = FakeCollectionAdapter(snapshot((A,)))
    parent = service.collections.start(f"https://www.douyin.com/collection/{MIX}")
    result = await service.process_claimed_job(parent)
    assert result.status == JobStatus.NEEDS_SELECTION
    assert service.downloader.calls == 0


async def test_cancel_last_collection_cancels_batch_child_using_database_relations(service):
    parents = []
    for _ in range(2):
        parent = await ready(service, ids=(A,))
        service.collections.select(parent, "selected", all_works=True)
        child = service.collections.confirm(parent).result["child_job_ids"][0]
        parents.append(parent)
    service.database.update_job(parents[1], result={})  # A stale projection is not a relation.
    service.cancel_job(parents[0])
    assert service.database.get_job(child).status == JobStatus.QUEUED
    service.cancel_job(parents[1])
    assert service.database.get_job(child).status == JobStatus.CANCELLED


async def test_cancel_collection_preserves_independent_capture(service):
    from douyin_wiki.models import CaptureRequest

    child = service.database.create_job(
        CaptureRequest(share_text=f"https://www.douyin.com/video/{A}")
    )
    parent = await ready(service, ids=(A,))
    service.collections.select(parent, "selected", all_works=True)
    assert service.collections.confirm(parent).result["child_job_ids"] == [child.id]
    service.cancel_job(parent)
    assert service.database.get_job(child.id).status == JobStatus.QUEUED


async def test_cancel_collection_preserves_shared_favorites_child(service):
    from tests.test_favorites import FakeFavoritesAdapter
    from tests.test_favorites import snapshot as favorite_snapshot

    parent = await ready(service, ids=(A,))
    service.collections.select(parent, "selected", all_works=True)
    child = service.collections.confirm(parent).result["child_job_ids"][0]
    service.favorites.adapter = FakeFavoritesAdapter(favorite_snapshot(kinds=[(A, "video")]))
    favorite = service.favorites.start()
    await service.process_claimed_job(favorite)
    service.favorites.select(favorite.id, selected=True)
    assert service.favorites.confirm(favorite.id).result["child_job_ids"] == [child]
    service.cancel_job(parent)
    assert service.database.get_job(child).status == JobStatus.QUEUED
    service.cancel_job(favorite.id)
    assert service.database.get_job(child).status == JobStatus.CANCELLED


async def test_retry_child_refreshes_all_collection_parents(service):
    parent = await ready(service, ids=(A,))
    service.collections.select(parent, "selected", all_works=True)
    child = service.collections.confirm(parent).result["child_job_ids"][0]
    service.database.update_job(child, status=JobStatus.FAILED)
    assert service.get_job(parent).status == JobStatus.COMPLETED_WITH_WARNINGS
    service.retry_job(child)
    assert service.get_job(parent).status == JobStatus.MONITORING


async def test_collection_auth_retry_uses_existing_douyin_channel(service):
    from douyin_wiki.auth_guidance import auth_channel
    from douyin_wiki.errors import BrowserAuthRequiredError

    parent = await ready(service, ids=(A,))

    class LoginRequired(FakeCollectionAdapter):
        async def inventory(self, **_):
            raise BrowserAuthRequiredError("请登录")

    service.collections.adapter = LoginRequired(snapshot())
    result = await service.process_claimed_job(service.database.get_job(parent))
    assert result.status == JobStatus.NEEDS_AUTH
    assert result.result["auth_scope"] == "collection"
    assert auth_channel("collection") == "douyin"
    service.retry_job(parent)
    service.collections.adapter = FakeCollectionAdapter(snapshot((A,)))
    assert (
        await service.process_claimed_job(service.database.get_job(parent))
    ).status == JobStatus.NEEDS_SELECTION


async def test_collection_waits_for_original_gateway_without_rerouting_events(service):
    from douyin_wiki.models import CaptureRequest, GatewayContext

    original = GatewayContext(gateway="original", conversation_id="chat-a")
    child = service.database.create_job(
        CaptureRequest(
            share_text=f"https://www.douyin.com/video/{A}",
            gateway_context=original,
        )
    )
    parent = await ready(service, ids=(A,))
    service.collections.select(parent, "selected", all_works=True)
    service.collections.confirm(parent)
    service.database.update_job(child.id, status=JobStatus.AWAITING_AGENT_ANALYSIS)
    assert service.get_job(parent).status == JobStatus.MONITORING
    events = service.list_job_events()
    assert len(events) == 1
    assert events[0].gateway_context == original
    assert "collection_context" not in events[0].result
