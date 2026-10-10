from __future__ import annotations

import pytest

from douyin_wiki.adapters.share import DouyinShareResolver
from douyin_wiki.collection_models import CollectionInventory, CollectionProfile, CollectionWork
from douyin_wiki.errors import JobLeaseLostError, JobStateError
from douyin_wiki.models import CaptureOptions, CaptureRequest, JobStatus

MIX = "7651028428363139126"
A = "7672717300746907078"
B = "7646239420807358282"


def snapshot(ids=(A, B), *, complete=True):
    return CollectionInventory(
        profile=CollectionProfile(mix_id=MIX, name="项目经理故事汇"),
        account_id="test-account",
        works=[CollectionWork(work_id=value, position=index) for index, value in enumerate(ids, 1)],
        complete=complete,
        warnings=[] if complete else ["只读到部分页"],
    )


class FakeCollectionAdapter:
    def __init__(self, result):
        self.result = result

    async def inventory(self, *, on_checkpoint=None, **_):
        if on_checkpoint:
            await on_checkpoint(self.result)
        return self.result


async def ready(service, *, ids=(A, B), complete=True, options=None, gateway_context=None):
    controller = getattr(service, "collections", None)
    assert controller is not None, "collection controller is missing"
    controller.adapter = FakeCollectionAdapter(snapshot(ids, complete=complete))
    service.resolver = DouyinShareResolver()
    parent = controller.start(
        f"https://www.douyin.com/collection/{MIX}", options=options, gateway_context=gateway_context
    )
    result = await controller.process(parent)
    assert result.status == JobStatus.NEEDS_SELECTION
    return parent.id


async def test_collection_confirmation_is_idempotent(service):
    parent = await ready(service)
    assert [j.kind for j in service.database.list_jobs()] == ["collection_import"]
    assert service.downloader.calls == 0
    service.collections.select(parent, "selected", all_works=True)
    first = service.collections.confirm(parent)
    again = service.collections.confirm(parent)
    assert first.result["child_job_ids"] == again.result["child_job_ids"]
    assert len(first.result["child_job_ids"]) == 2
    assert len([j for j in service.database.list_jobs() if j.kind == "capture"]) == 2
    child = service.database.get_job(first.result["child_job_ids"][0])
    assert child.artifacts["collection_context"]["collection_id"] == MIX
    assert child.artifacts["collection_context"]["batch_silent"]


async def test_collection_pending_selection_blocks_confirmation(service):
    parent = await ready(service)
    service.collections.select(parent, "selected", work_ids=[A])
    with pytest.raises(JobStateError):
        service.collections.confirm(parent)
    assert len(service.database.list_jobs()) == 1
    service.collections.select(parent, "skipped", work_ids=[B])
    result = service.collections.confirm(parent)
    assert len(result.result["child_job_ids"]) == 1


async def test_collection_partial_inventory_requires_acceptance(service):
    parent = await ready(service, complete=False)
    service.collections.select(parent, "selected", all_works=True)
    with pytest.raises(JobStateError):
        service.collections.confirm(parent)
    assert len(service.database.list_jobs()) == 1
    result = service.collections.confirm(parent, accept_partial=True)
    assert result.status == JobStatus.MONITORING
    assert result.result["accept_partial"]


async def test_collection_option_conflict_rolls_back_whole_confirmation(service):
    existing = service.database.create_job(
        CaptureRequest(
            share_text=f"https://www.douyin.com/video/{B}", options=CaptureOptions(allow_long=True)
        )
    )
    parent = await ready(service)
    service.collections.select(parent, "selected", all_works=True)
    with pytest.raises(JobStateError):
        service.collections.confirm(parent)
    assert service.database.get_job(parent).status == JobStatus.NEEDS_SELECTION
    assert not service.collections.store.load(parent)["confirmed"]
    assert [j.id for j in service.database.list_jobs() if j.kind == "capture"] == [existing.id]


async def test_collection_reuses_global_capture_without_overwriting_context(service):
    existing = service.database.create_job(
        CaptureRequest(share_text=f"https://www.douyin.com/video/{A}"),
        artifacts={"favorites_context": {"parent_job_id": "original", "work_id": A}},
    )
    parent = await ready(service, ids=(A,))
    service.collections.select(parent, "selected", all_works=True)
    result = service.collections.confirm(parent)
    assert result.result["child_job_ids"] == [existing.id]
    assert service.database.get_job(existing.id).artifacts == existing.artifacts


async def test_collection_retry_generation_excludes_disappeared_old_work(service):
    parent = await ready(service)
    service.collections.select(parent, "selected", work_ids=[A])
    service.collections.adapter = FakeCollectionAdapter(snapshot((B, "7650097901481364778")))
    await service.collections.process(service.database.get_job(parent))
    current = service.collections.get(parent)
    assert [w["work_id"] for w in current["works"]] == [B, "7650097901481364778"]
    assert current["summary"]["discovered"] == 2
    assert all(w["decision"] == "pending" for w in current["works"])


async def test_collection_checkpoint_enforces_worker_lease(service):
    parent = await ready(service)
    generation = service.collections.store.load(parent)["generation"]
    with (
        service.database.claimed_job_updates(parent, "expired-owner"),
        pytest.raises(JobLeaseLostError),
    ):
        service.collections.store.checkpoint(parent, generation, snapshot())


async def test_collection_selection_selectors_are_mutually_exclusive(service):
    parent = await ready(service)
    with pytest.raises(ValueError):
        service.collections.select(parent, "selected", all_works=True, work_ids=[A])
    with pytest.raises(JobStateError):
        service.collections.select(parent, "selected", work_ids=["999"])


async def test_collection_all_selects_inventory_across_pages(service):
    ids = tuple(str(1000 + i) for i in range(105))
    parent = await ready(service, ids=ids)
    service.collections.select(parent, "selected", all_works=True)
    detail = service.collections.get(parent, page=2, limit=50)
    assert detail["total"] == 105
    assert detail["summary"]["selected"] == 105
    assert len(detail["works"]) == 50


async def test_partial_collection_retry_keeps_choices_and_reads_new_generation(service):
    parent = await ready(service, complete=False)
    service.collections.select(parent, "selected", work_ids=[A])
    service.collections.select(parent, "skipped", work_ids=[B])
    assert service.get_collection_inventory(parent)["can_retry"]
    result = service.retry_collection_import(parent)
    assert result.status == JobStatus.QUEUED
    new = "7650097901481364778"
    service.collections.adapter = FakeCollectionAdapter(snapshot((B, A, new)))
    await service.process_claimed_job(result)
    detail = service.get_collection_inventory(parent)
    assert detail["generation"] == 2
    assert detail["complete"]
    assert [(w["work_id"], w["decision"]) for w in detail["works"]] == [
        (B, "skipped"), (A, "selected"), (new, "pending")
    ]
    assert service.downloader.calls == 0


async def test_complete_unconfirmed_collection_requires_new_batch_to_inventory_again(service):
    parent = await ready(service)
    with pytest.raises(JobStateError):
        service.retry_collection_import(parent)
