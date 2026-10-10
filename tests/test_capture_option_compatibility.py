"""Effective capture options govern sharing across every import entry point."""

import pytest

from douyin_wiki.errors import JobStateError
from douyin_wiki.models import (
    AnalysisMode,
    CaptureOptions,
    CreatorWorkDecision,
    GatewayContext,
    JobStatus,
    RetentionPolicy,
)
from douyin_wiki.worker import Worker
from tests.test_capture_sharing import SHORT, confirmed_collection
from tests.test_collection_flow import A, B, ready
from tests.test_creator import FakeCreatorAdapter, inventory_result
from tests.test_favorites import scan, snapshot


async def selected_batch(service, kind, work_ids, *, gateway_context=None):
    if kind == "collection":
        job_id = await ready(service, ids=work_ids, gateway_context=gateway_context)
        service.collections.select(job_id, "selected", all_works=True)
    elif kind == "favorites":
        job = await scan(
            service,
            snapshot(kinds=[(work_id, "video") for work_id in work_ids]),
            gateway_context=gateway_context,
        )
        job_id = job.id
    else:
        service.creator_adapter = FakeCreatorAdapter(inventory_result(*work_ids))
        job = service.capture_douyin_creator(
            "https://www.douyin.com/user/example-sec-uid", gateway_context=gateway_context
        )
        processed = await service.process_claimed_job(job)
        assert processed.status == JobStatus.NEEDS_SELECTION
        service.set_creator_work_selection(
            job.id, CreatorWorkDecision.SELECTED, ordinals=list(range(1, len(work_ids) + 1))
        )
        job_id = job.id
    return job_id


def confirm_batch(service, kind, job_id):
    if kind == "collection":
        return service.confirm_collection_import(job_id)
    if kind == "favorites":
        return service.favorites.confirm(job_id)
    return service.confirm_creator_import(job_id)


@pytest.mark.parametrize("allow_long", [False, True])
@pytest.mark.parametrize("approve_cloud_analysis", [False, True])
async def test_legacy_authorization_does_not_block_short_link_sharing(
    service, allow_long, approve_cloud_analysis
):
    service.config.analysis_mode = AnalysisMode.GATEWAY
    _, owner_id = await confirmed_collection(service)
    owner = await Worker(service).run_once()
    assert owner.id == owner_id and owner.status == JobStatus.AWAITING_AGENT_ANALYSIS
    caller_context = GatewayContext(gateway="second", conversation_id="legacy-options")
    requested = service.capture_douyin(
        SHORT,
        options=CaptureOptions(
            allow_long=allow_long, approve_cloud_analysis=approve_cloud_analysis
        ),
        gateway_context=caller_context,
    )

    follower = await Worker(service).run_once()

    assert follower.id == requested.id
    assert follower.status == JobStatus.MONITORING
    assert service.downloader.calls == 1
    assert follower.result["shared_capture_job_id"] == owner_id
    assert service.get_job(requested.id).request.gateway_context == caller_context
    assert service.get_job(owner_id).status == JobStatus.AWAITING_AGENT_ANALYSIS


@pytest.mark.parametrize("kind", ["creator", "favorites", "collection"])
@pytest.mark.parametrize("allow_long", [False, True])
@pytest.mark.parametrize("approve_cloud_analysis", [False, True])
async def test_batch_reuses_capture_when_only_legacy_authorization_differs(
    service, kind, allow_long, approve_cloud_analysis
):
    owner_context = GatewayContext(gateway="first", conversation_id="owner")
    owner = service.capture_douyin(
        f"https://www.douyin.com/video/{A}",
        options=CaptureOptions(
            allow_long=allow_long, approve_cloud_analysis=approve_cloud_analysis
        ),
        gateway_context=owner_context,
    )
    batch_context = GatewayContext(gateway="second", conversation_id=kind)
    job_id = await selected_batch(service, kind, (A,), gateway_context=batch_context)
    before_ids = {job.id for job in service.database.list_jobs()}

    result = confirm_batch(service, kind, job_id)

    assert result.id == job_id and result.status == JobStatus.MONITORING
    assert result.result["child_job_ids"] == [owner.id]
    assert service.database.batch_child_ids(job_id) == [owner.id]
    assert {job.id for job in service.database.list_jobs()} == before_ids
    assert service.get_job(owner.id).request.gateway_context == owner_context
    assert service.get_job(job_id).request.gateway_context == batch_context
    assert service.get_job(owner.id).request.options == owner.request.options
    assert service.downloader.calls == 0


@pytest.mark.parametrize("kind", ["creator", "favorites", "collection"])
async def test_later_retention_conflict_does_not_dispatch_partial_batch(service, kind):
    owner = service.capture_douyin(
        f"https://www.douyin.com/video/{B}", options=CaptureOptions(retention=RetentionPolicy.KEEP)
    )
    job_id = await selected_batch(service, kind, (A, B))
    before_ids = {job.id for job in service.database.list_jobs()}
    before_result = service.get_job(job_id).result
    assert not service.database.batch_child_ids(job_id)

    with pytest.raises(JobStateError, match="处理选项"):
        confirm_batch(service, kind, job_id)

    assert {job.id for job in service.database.list_jobs()} == before_ids
    assert service.get_job(job_id).status == JobStatus.NEEDS_SELECTION
    assert service.get_job(job_id).result == before_result
    assert not service.database.batch_child_ids(job_id)
    assert service.get_job(owner.id).status == JobStatus.QUEUED
    assert service.get_job(owner.id).request.options.retention == RetentionPolicy.KEEP
    assert service.downloader.calls == 0
    if kind != "creator":
        controller = service.favorites if kind == "favorites" else service.collections
        assert not controller.store.load(job_id)["confirmed"]


@pytest.mark.parametrize("kind", ["favorites", "collection"])
@pytest.mark.parametrize(
    ("retention", "conflict"),
    [(RetentionPolicy.TEMPORARY, False), (RetentionPolicy.KEEP, True)],
)
@pytest.mark.parametrize("allow_long", [False, True])
@pytest.mark.parametrize("approve_cloud_analysis", [False, True])
async def test_preview_matches_confirmation_for_effective_capture_options(
    service, kind, retention, conflict, allow_long, approve_cloud_analysis
):
    owner = service.capture_douyin(
        f"https://www.douyin.com/video/{B}",
        options=CaptureOptions(
            retention=retention,
            allow_long=allow_long,
            approve_cloud_analysis=approve_cloud_analysis,
        ),
    )
    job_id = await selected_batch(service, kind, (B,))
    controller = service.favorites if kind == "favorites" else service.collections

    preview = controller.get(job_id)

    item = preview["items" if kind == "favorites" else "works"][0]
    assert item["job_id"] == owner.id
    assert item.get("options_conflict", False) is conflict
    assert item["disposition"] == ("conflict" if conflict else "active")
    assert preview["summary"].get("conflict", 0) == int(conflict)
    counts = preview["confirmation_preview"] if kind == "favorites" else preview["summary"]
    assert counts["new_tasks"] == 0
    assert counts["reused_tasks"] == (0 if conflict else 1)
    if conflict:
        assert "处理选项" in (item["error_message"] or "")
        with pytest.raises(JobStateError) as error:
            confirm_batch(service, kind, job_id)
        assert str(error.value) == item["error_message"]
        assert service.get_job(job_id).status == JobStatus.NEEDS_SELECTION
        assert not service.database.batch_child_ids(job_id)
