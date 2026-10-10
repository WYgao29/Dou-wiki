from __future__ import annotations

from douyin_wiki.models import JobStatus
from tests.test_collection_flow import A, ready


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
