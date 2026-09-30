"""Regression coverage for data-preserving undo and usability state transitions."""

from __future__ import annotations

import asyncio

import httpx
import pytest
import yaml

from douyin_wiki.errors import JobCancelledError, JobStateError
from douyin_wiki.models import CaptureRequest, JobStatus
from douyin_wiki.vault import VaultWriter
from douyin_wiki.webapp.app import ChangeNotifier, _change_origin, create_app
from douyin_wiki.webapp.rendering import _strip_empty_inspiration
from tests.test_favorites import A, B, scan, snapshot
from tests.test_tags import source
from tests.test_web import _web_fixture, local_client


def test_merge_undo_restores_files_membership_index_pins_and_aliases(tmp_path):
    config, service = _web_fixture(tmp_path)
    paths = [
        source(service, "1000", ["AI 代理", "咖啡"], managed=True),
        source(service, "1001", ["AI Agent"], managed=True),
    ]
    app = create_app(config, service=service, start_watcher=False)
    with local_client(app) as client:
        client.get("/api/tags")
        client.post("/api/tags/pin", json={"name": "AI 代理"})
        with service.database.connect() as conn:
            conn.execute("INSERT INTO tag_aliases VALUES ('Agent', 'AI 代理')")
            pins = [
                tuple(row)
                for row in conn.execute("SELECT name,pinned_at,position FROM tags ORDER BY name")
            ]
            chunks = [
                tuple(row) for row in conn.execute("SELECT id,tags_text FROM chunks ORDER BY id")
            ]
            fts = [
                tuple(row)
                for row in conn.execute("SELECT chunk_id,tags FROM chunks_fts ORDER BY chunk_id")
            ]
        before = {path: path.read_text() for path in paths}
        data = service.database.get_entry_data("dy-1000")
        merged = client.post(
            "/api/tags/merge", json={"names": ["AI 代理", "AI Agent"], "target": "AI Agent"}
        )
        assert merged.status_code == 200, merged.text
        assert service.database.get_entry("dy-1000").tags == ["AI Agent", "咖啡"]
        assert service.tags.resolve(["Agent", "AI 代理"]) == ["AI Agent"]
        operation = merged.json()["operation_id"]
        assert client.get("/api/tags").json()["undo_by_alias"]["AI 代理"] == operation
        # Persistence across a new API instance; a repeat undo is harmless.
    with local_client(create_app(config, service=service, start_watcher=False)) as client:
        for _ in range(2):
            restored = client.post("/api/tags/undo", json={"operation_id": operation})
            assert restored.status_code == 200, restored.text
        assert service.database.get_entry_data("dy-1000") == data
        assert {path: path.read_text() for path in paths} == before
        assert service.tags.resolve(["Agent"]) == ["AI 代理"]
        with service.database.connect() as conn:
            assert [
                tuple(row)
                for row in conn.execute("SELECT name,pinned_at,position FROM tags ORDER BY name")
            ] == pins
            assert [
                tuple(row) for row in conn.execute("SELECT id,tags_text FROM chunks ORDER BY id")
            ] == chunks
            assert [
                tuple(row)
                for row in conn.execute("SELECT chunk_id,tags FROM chunks_fts ORDER BY chunk_id")
            ] == fts


@pytest.mark.parametrize(
    "action,payload",
    [
        ("delete", {"names": ["咖啡"]}),
        ("rename", {"name": "咖啡", "target": "饮品"}),
    ],
)
def test_tag_undo_preserves_later_file_edits(tmp_path, action, payload):
    config, service = _web_fixture(tmp_path)
    path = config.vault_path / service.database.get_entry("dy-123").source_path
    app = create_app(config, service=service, start_watcher=False)
    with local_client(app) as client:
        response = client.post(f"/api/tags/{action}", json=payload)
        assert response.status_code == 200
        changed = path.read_text() + "\n后续编辑\n"
        path.write_text(changed)
        undo = client.post("/api/tags/undo", json={"operation_id": response.json()["operation_id"]})
        assert undo.status_code == 409
        assert path.read_text() == changed
        assert service.database.get_entry("dy-123")


def test_rename_undo_rejects_new_references_and_delete_undo_restores(tmp_path):
    config, service = _web_fixture(tmp_path)
    app = create_app(config, service=service, start_watcher=False)
    with local_client(app) as client:
        renamed = client.post("/api/tags/rename", json={"name": "咖啡", "target": "饮品"}).json()
        added = source(service, "1000", ["饮品"])
        assert (
            client.post(
                "/api/tags/undo", json={"operation_id": renamed["operation_id"]}
            ).status_code
            == 409
        )
        assert "饮品" in added.read_text()
        deleted = client.post("/api/tags/delete", json={"names": ["饮品"]}).json()
        assert (
            client.post(
                "/api/tags/undo", json={"operation_id": deleted["operation_id"]}
            ).status_code
            == 200
        )
        assert service.database.get_entry("dy-123").tags == ["饮品"]
        assert "饮品" in added.read_text()


def test_cleanup_refreshes_catalog_and_split_invalidates_alias_cache(tmp_path):
    config, service = _web_fixture(tmp_path)
    app = create_app(config, service=service, start_watcher=False)
    with local_client(app) as client:
        client.get("/api/tags")
        stale = source(service, "1000", ["先前空标签"])
        with service.database.connect() as conn:
            conn.execute("INSERT INTO tags(name,created_at) VALUES ('先前空标签', '2026-01-01')")
        result = client.post("/api/tags/cleanup", json={"confirmed": True})
        assert result.status_code == 200
        assert "先前空标签" not in result.json()["deleted"]
        assert "先前空标签" in stale.read_text()
        client.post("/api/tags/merge", json={"names": ["先前空标签", "咖啡"], "target": "咖啡"})
        manual = source(service, "1001", ["先前空标签"])
        assert client.get("/api/library", params={"tag": "咖啡"}).json()["total"] == 3
        assert client.post("/api/tags/split", json={"name": "先前空标签"}).status_code == 200
        independent = client.get("/api/library", params={"tag": "先前空标签"}).json()["items"]
        assert [item["work_id"] for item in independent] == ["1001"]
        assert "先前空标签" in manual.read_text()


async def test_cancel_children_are_terminal_and_deleting_child_keeps_foreign_keys(service):
    parent = await scan(service, snapshot(kinds=[(A, "video")]))
    service.favorites.confirm(parent.id)
    child = next(job for job in service.database.list_jobs() if job.kind == "capture")
    service.cancel_job(child.id)
    refreshed = service.database.get_job(parent.id)
    assert refreshed.status == JobStatus.COMPLETED_WITH_WARNINGS
    service.delete_job(child.id)
    assert service.database.get_job(child.id).artifacts["user_deleted"]
    with service.database.connect() as conn:
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
    assert service.favorites.get(parent.id)["summary"]["failed"] == 1
    with pytest.raises(JobStateError):
        service.retry_job(child.id)


async def test_cancel_parent_preserves_shared_children(service):
    parent = await scan(service, snapshot(kinds=[(A, "video")]))
    service.favorites.confirm(parent.id)
    second = await scan(service, snapshot(kinds=[(A, "video")]))
    service.favorites.confirm(second.id)
    child = next(job for job in service.database.list_jobs() if job.kind == "capture")
    service.cancel_job(parent.id)
    assert service.database.get_job(parent.id).status == JobStatus.CANCELLED
    assert service.database.get_job(child.id).status == JobStatus.QUEUED
    service.cancel_job(second.id)
    assert service.database.get_job(child.id).status == JobStatus.CANCELLED


@pytest.mark.parametrize("deleted_index", [0, 1])
async def test_favorites_retry_skips_deleted_failures_and_keeps_batch_references(
    service, deleted_index
):
    parent = await scan(service, snapshot(kinds=[(A, "video"), (B, "video")]))
    service.favorites.confirm(parent.id)
    children = [row["child_id"] for row in service.favorites.store.rows(parent.id)]
    for child_id in children:
        service.database.update_job(child_id, status=JobStatus.FAILED, unlock=True)
    service.delete_job(children[deleted_index])
    before = service.favorites.get(parent.id)
    assert before["summary"]["failed"] == 2
    assert before["summary"]["retryable_failed"] == 1
    assert before["can_retry"]
    with local_client(create_app(service.config, service=service, start_watcher=False)) as client:
        assert client.post(f"/api/favorites/imports/{parent.id}/retry").status_code == 202
    assert service.database.get_job(children[deleted_index]).status == JobStatus.FAILED
    assert service.database.get_job(children[deleted_index]).artifacts["user_deleted"]
    assert service.database.get_job(children[1 - deleted_index]).status == JobStatus.QUEUED
    after = service.favorites.get(parent.id)
    assert after["status"] == "monitoring"
    assert after["summary"]["retryable_failed"] == 0
    assert not after["can_retry"]
    with service.database.connect() as conn:
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()


@pytest.mark.parametrize("blocked_by", ["cancelled", "cancel_requested", "deleted"])
async def test_favorites_retry_rejects_inactive_parent_without_requeuing_children(
    service, blocked_by
):
    parent = await scan(service, snapshot(kinds=[(A, "video"), (B, "video")]))
    service.favorites.confirm(parent.id)
    children = [row["child_id"] for row in service.favorites.store.rows(parent.id)]
    service.database.update_job(children[0], status=JobStatus.FAILED, unlock=True)
    if blocked_by == "cancel_requested":
        with service.database.connect() as conn:
            conn.execute("UPDATE jobs SET locked_at=updated_at WHERE id=?", (parent.id,))
        service.cancel_job(parent.id)
    elif blocked_by == "cancelled":
        service.cancel_job(parent.id)
    else:
        service.database.update_job(parent.id, status=JobStatus.FAILED, unlock=True)
        service.delete_job(parent.id)
    before = [service.database.get_job(child_id) for child_id in children]
    assert not service.favorites.get(parent.id)["can_retry"]
    with local_client(create_app(service.config, service=service, start_watcher=False)) as client:
        assert client.post(f"/api/favorites/imports/{parent.id}/retry").status_code == 409
    assert [service.database.get_job(child_id) for child_id in children] == before


async def test_favorites_retry_tolerates_child_deleted_after_inventory_read(service, monkeypatch):
    parent = await scan(service, snapshot(kinds=[(A, "video"), (B, "video")]))
    service.favorites.confirm(parent.id)
    children = [row["child_id"] for row in service.favorites.store.rows(parent.id)]
    for child_id in children:
        service.database.update_job(child_id, status=JobStatus.FAILED, unlock=True)
    retry = service.retry_job

    def delete_before_retry(job_id):
        if job_id == children[0]:
            service.delete_job(job_id)
        return retry(job_id)

    monkeypatch.setattr(service, "retry_job", delete_before_retry)
    assert service.favorites.retry_failed(parent.id).status == JobStatus.MONITORING
    assert service.database.get_job(children[0]).status == JobStatus.FAILED
    assert service.database.get_job(children[0]).artifacts["user_deleted"]
    assert service.database.get_job(children[1]).status == JobStatus.QUEUED


async def test_retry_shared_failure_preserves_other_parents_cancellation(service):
    parents = [await scan(service, snapshot(kinds=[(A, "video")])) for _ in range(2)]
    for parent in parents:
        service.favorites.confirm(parent.id)
    child_id = service.favorites.store.rows(parents[0].id)[0]["child_id"]
    assert service.favorites.store.rows(parents[1].id)[0]["child_id"] == child_id
    service.database.update_job(child_id, status=JobStatus.FAILED, unlock=True)
    service.cancel_job(parents[0].id)
    assert service.favorites.retry_failed(parents[1].id).status == JobStatus.MONITORING
    service.refresh_batch_parents()
    assert service.database.get_job(child_id).status == JobStatus.QUEUED
    assert service.database.get_job(parents[0].id).status == JobStatus.CANCELLED
    assert service.database.get_job(parents[1].id).status == JobStatus.MONITORING
    assert not service.favorites.get(parents[0].id)["can_retry"]


async def test_worker_continues_after_cancellation_during_favorites_refresh(service, monkeypatch):
    from douyin_wiki.worker import Worker

    parent = await scan(service, snapshot(kinds=[(A, "video")]))
    service.favorites.confirm(parent.id)
    child_id = service.favorites.store.rows(parent.id)[0]["child_id"]
    service.database.update_job(child_id, status=JobStatus.FAILED, unlock=True)
    other = service.database.create_job(
        CaptureRequest(share_text=f"https://www.douyin.com/video/{B}")
    )
    update = service.database.update_job
    cancelled = False

    def cancel_before_publication(job_id, **kwargs):
        nonlocal cancelled
        if job_id == parent.id and not cancelled:
            cancelled = True
            service.cancel_job(parent.id)
        return update(job_id, **kwargs)

    monkeypatch.setattr(service.database, "update_job", cancel_before_publication)
    worker = Worker(service)
    source_checks = iter([False, True])
    monkeypatch.setattr(worker, "source_changed", lambda: next(source_checks))
    monkeypatch.setattr(worker, "run_due_maintenance", lambda: None)
    processed = []

    async def process(job):
        processed.append(job.id)
        return service.database.update_job(job.id, status=JobStatus.COMPLETED, unlock=True)

    monkeypatch.setattr(worker, "_process_with_heartbeat", process)
    await asyncio.wait_for(worker.run_forever(), timeout=5)
    assert cancelled
    assert processed == [other.id]
    assert service.database.get_job(parent.id).status == JobStatus.CANCELLED
    assert service.database.get_job(child_id).status == JobStatus.FAILED


def test_media_retry_cannot_reactivate_job_deleted_after_initial_check(service, monkeypatch):
    job = service.database.create_job(
        CaptureRequest(share_text=f"https://www.douyin.com/video/{A}"), kind="media_restore"
    )
    service.database.update_job(job.id, status=JobStatus.FAILED, artifacts={"entry_id": "dy-123"})
    requeue = service.database.requeue_job_deduplicated

    def delete_before_requeue(job_id, **kwargs):
        service.delete_job(job_id)
        return requeue(job_id, **kwargs)

    monkeypatch.setattr(service.database, "requeue_job_deduplicated", delete_before_requeue)
    with pytest.raises(JobStateError, match="任务已删除"):
        service.retry_job(job.id)
    assert service.database.get_job(job.id).status == JobStatus.FAILED
    assert service.database.get_job(job.id).artifacts["user_deleted"]
    assert service.database.claim_next_job() is None
    with pytest.raises(JobStateError, match="任务已删除"):
        service.database.requeue_job(job.id)


async def test_running_cancel_wins_late_error_and_retry_clears_stale_flag(service, monkeypatch):
    job = service.database.create_job(
        CaptureRequest(share_text=f"https://www.douyin.com/video/{A}")
    )
    job = service.database.claim_next_job()

    async def fail_after_cancel(_job):
        service.cancel_job(job.id)
        raise RuntimeError("下载失败")

    monkeypatch.setattr(service, "process_capture", fail_after_cancel)
    cancelled = await service.process_claimed_job(job)
    assert cancelled.status == JobStatus.CANCELLED
    with service.database.connect() as conn:
        assert (
            conn.execute("SELECT lock_owner FROM jobs WHERE id=?", (job.id,)).fetchone()[0] is None
        )
    with pytest.raises(JobCancelledError):
        service.database.update_job(job.id, status=JobStatus.COMPLETED)
    failed = service.database.create_job(
        CaptureRequest(share_text=f"https://www.douyin.com/video/{A}")
    )
    failed = service.database.update_job(failed.id, status=JobStatus.FAILED)
    service.database.update_job(failed.id, artifacts={"cancel_requested": True})
    retried = service.retry_job(failed.id)
    assert retried.status == JobStatus.QUEUED
    assert not retried.artifacts.get("cancel_requested")


def test_short_link_duplicate_and_active_job_are_deduplicated(tmp_path):
    config, service = _web_fixture(tmp_path)
    calls = []

    def resolve(request):
        calls.append(str(request.url))
        if request.url.host == "v.douyin.com":
            return httpx.Response(302, headers={"location": f"https://www.douyin.com/video/{A}"})
        return httpx.Response(200)

    service.resolver.transport = httpx.MockTransport(resolve)
    app = create_app(config, service=service, start_watcher=False)
    with local_client(app) as client:
        short = "https://v.douyin.com/abc/"
        first = client.post("/api/captures", json={"share_text": short})
        second = client.post(
            "/api/captures", json={"share_text": f"https://www.douyin.com/video/{A}"}
        )
        assert first.status_code == 202
        assert second.status_code == 200
        assert second.json()["active"]
        assert "尚未保存" in second.json()["message"]
        assert first.json()["id"] == second.json()["job_id"]
        entry = service.database.get_entry("dy-123").model_copy(
            update={"id": f"dy-{A}", "video_id": A}
        )
        service.database.upsert_entry(entry, service.database.get_entry_data("dy-123"))
        duplicate = client.post("/api/captures", json={"share_text": short})
        assert duplicate.status_code == 200
        assert duplicate.json()["duplicate"]
        assert (
            client.post("/api/creators/inventory", json={"source_text": short}).status_code == 400
        )
        assert calls
        service.trash_entry(entry.id, confirmed=True)
        in_trash = client.post("/api/captures", json={"share_text": short})
        assert in_trash.status_code == 200
        assert in_trash.json()["in_trash"]
        assert len(service.database.list_jobs()) == 1


def test_empty_inspiration_preserves_real_quotes():
    quote = "## 灵感\n\n> 实际灵感引用\n\n## 一句话\n总结\n"
    assert _strip_empty_inspiration(quote) == quote
    prefixed = "## 灵感来源\n\n未填写\n"
    assert _strip_empty_inspiration(prefixed) == prefixed
    assert "## 灵感" not in _strip_empty_inspiration("## 灵感\n\n- 未填写\n\n## 一句话\n总结\n")


async def test_notifications_preserve_mixed_origins_and_context_isolation():
    notifier = ChangeNotifier()
    token = _change_origin.set("tab-one")
    try:
        await notifier.publish()
    finally:
        _change_origin.reset(token)
    await notifier.publish()
    assert set(notifier.library_payload(0)["origins"]) == {"tab-one", None}
    assert notifier.library_payload(1)["origins"] == [None]
    token = _change_origin.set("tab-two")
    try:
        await asyncio.to_thread(lambda: None)
        await notifier.publish("jobs")
    finally:
        _change_origin.reset(token)
    assert notifier.library_payload(2)["origins"] == []


def test_operation_lock_is_reentrant_without_releasing_outer_lock(tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    outer = VaultWriter(tmp_path)
    other = VaultWriter(tmp_path)
    entered = threading.Event()

    def competitor():
        with other.entry_operations_locked():
            entered.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with outer.entry_operations_locked():
            with other.entry_operations_locked():
                future = pool.submit(competitor)
                assert not entered.wait(0.02)
            assert not entered.wait(0.02)
        future.result(timeout=2)
    assert entered.is_set()


def test_undo_write_failure_rolls_back_and_can_retry(tmp_path, monkeypatch):
    config, service = _web_fixture(tmp_path)
    first = source(service, "1000", ["源标签"])
    second = source(service, "1001", ["源标签"])
    app = create_app(config, service=service, start_watcher=False)
    with local_client(app, raise_server_exceptions=False) as client:
        merged = client.post(
            "/api/tags/merge", json={"names": ["源标签", "咖啡"], "target": "咖啡"}
        ).json()
        contents = {path: path.read_text() for path in (first, second)}
        original = service.vault._atomic_write
        calls = 0

        def fail_once(path, content):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("模拟写入失败")
            return original(path, content)

        monkeypatch.setattr(service.vault, "_atomic_write", fail_once)
        payload = {"operation_id": merged["operation_id"]}
        assert client.post("/api/tags/undo", json=payload).status_code == 500
        assert {path: path.read_text() for path in contents} == contents
        assert service.tags.resolve(["源标签"]) == ["咖啡"]
        assert client.post("/api/tags/undo", json=payload).status_code == 200
        assert all("源标签" in path.read_text() for path in contents)


async def test_worker_recovery_finishes_creator_parent_of_cancelled_child(service):
    from datetime import UTC, datetime, timedelta

    from douyin_wiki.worker import Worker

    child = service.database.create_job(
        CaptureRequest(share_text=f"https://www.douyin.com/video/{A}")
    )
    child = service.database.claim_next_job(lease_seconds=30)
    service.cancel_job(child.id)
    parent = service.database.create_job(
        CaptureRequest(share_text="https://www.douyin.com/user/creator"),
        kind="creator_import",
        status=JobStatus.MONITORING,
    )
    service.database.update_job(parent.id, result={"child_job_ids": [child.id]})
    expired = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    with service.database.connect() as conn:
        conn.execute("UPDATE jobs SET lease_expires_at=? WHERE id=?", (expired, child.id))
    assert await Worker(service).run_once() is None
    assert service.database.get_job(child.id).status == JobStatus.CANCELLED
    assert service.database.get_job(parent.id).status == JobStatus.COMPLETED_WITH_WARNINGS


async def test_cancel_while_waiting_for_download_does_not_start_download(service, monkeypatch):
    entered = False

    async def download(*args):
        nonlocal entered
        entered = True
        raise AssertionError("不应开始下载")

    monkeypatch.setattr(service.downloader, "download", download)
    job = service.database.create_job(
        CaptureRequest(share_text=f"https://www.douyin.com/video/{A}")
    )
    job = service.database.claim_next_job()
    # All slots are held while the claimed job reaches its download stage.
    semaphore = service.download_semaphore = asyncio.Semaphore(0)
    task = asyncio.create_task(service.process_claimed_job(job))
    for _ in range(100):
        if service.database.get_job(job.id).status == JobStatus.DOWNLOADING:
            break
        await asyncio.sleep(0.005)
    assert service.database.get_job(job.id).status == JobStatus.DOWNLOADING
    service.cancel_job(job.id)
    semaphore.release()
    outcome = await asyncio.wait_for(task, timeout=2)
    assert outcome.status == JobStatus.CANCELLED
    assert not entered


def test_overview_uses_cache_without_starting_an_auth_probe(tmp_path, monkeypatch):
    config, service = _web_fixture(tmp_path)

    async def forbidden_probe(*args, **kwargs):
        raise AssertionError("概览不应触发授权检查")

    monkeypatch.setattr(service, "check_auth_scope", forbidden_probe)
    app = create_app(config, service=service, start_watcher=False)
    with local_client(app) as client:
        for _ in range(2):
            response = client.get("/api/overview")
            assert response.status_code == 200
            assert response.json()["auth"]["pending"]


def test_cleanup_refuses_unreadable_sources_without_erasing_tags(tmp_path):
    config, service = _web_fixture(tmp_path)
    app = create_app(config, service=service, start_watcher=False)
    with local_client(app) as client:
        client.get("/api/tags")
        path = config.vault_path / service.database.get_entry("dy-123").source_path
        broken = path.read_text().replace("2026-08-23T09:00:00+08:00", "invalid-date")
        path.write_text(broken)
        response = client.post("/api/tags/cleanup", json={"confirmed": True})
        assert response.status_code == 409
        assert path.read_text() == broken
        assert service.database.get_entry("dy-123").tags == ["咖啡"]


@pytest.mark.parametrize(
    "damage", ["missing-frontmatter", "missing-file", "wrong-type", "mismatched-tags"]
)
def test_cleanup_checks_managed_sources_and_database_references(tmp_path, damage):
    from douyin_wiki.webapp.catalog import split_frontmatter

    config, service = _web_fixture(tmp_path)
    with local_client(create_app(config, service=service, start_watcher=False)) as client:
        client.get("/api/tags")
        entry = service.database.get_entry("dy-123")
        data = service.database.get_entry_data(entry.id)
        path = config.vault_path / entry.source_path
        metadata, body = split_frontmatter(path.read_text())
        if damage == "missing-file":
            path.unlink()
        elif damage == "missing-frontmatter":
            path.write_text(body)
        else:
            if damage == "wrong-type":
                metadata["type"] = "topic"
            else:
                metadata["tags"] = []
            path.write_text("---\n" + yaml.safe_dump(metadata, allow_unicode=True) + "---\n" + body)
        damaged = path.read_text() if path.exists() else None
        for confirmed in (False, True):
            response = client.post("/api/tags/cleanup", json={"confirmed": confirmed})
            assert response.status_code == 409, response.text
            assert service.database.get_entry(entry.id).tags == entry.tags
            assert service.database.get_entry_data(entry.id) == data
            assert (path.read_text() if path.exists() else None) == damaged
        with service.database.connect() as conn:
            assert conn.execute("SELECT 1 FROM tags WHERE name='咖啡'").fetchone()


@pytest.mark.parametrize("collision", ["standard", "alias"])
def test_undo_rejects_restored_namespace_collisions_without_changing_later_data(
    tmp_path, collision
):
    config, service = _web_fixture(tmp_path)
    with local_client(create_app(config, service=service, start_watcher=False)) as client:
        client.get("/api/tags")
        with service.database.connect() as conn:
            conn.execute("INSERT INTO tag_aliases VALUES ('Coffee', '咖啡')")
        deleted = client.post("/api/tags/delete", json={"names": ["咖啡"]}).json()
        source(service, "1000", ["Coffee"] if collision == "standard" else ["咖啡", "茶"])
        before = client.get("/api/tags").json()
        if collision == "alias":
            response = client.post(
                "/api/tags/merge", json={"names": ["咖啡", "茶"], "target": "茶"}
            )
            assert response.status_code == 200, response.text
            before = response.json()
        files = {p: p.read_text() for p in config.vault_path.rglob("*.md")}
        response = client.post("/api/tags/undo", json={"operation_id": deleted["operation_id"]})
        assert response.status_code == 409, response.text
        assert "重名" in response.json()["detail"]
        after = client.get("/api/tags").json()
        assert after["tags"] == before["tags"]
        assert after["aliases"] == before["aliases"]
        assert {p: p.read_text() for p in files} == files
        assert service.database.get_entry("dy-123").tags == []
        assert service.tags.resolve(["Coffee" if collision == "standard" else "咖啡"]) == [
            "Coffee" if collision == "standard" else "茶"
        ]
        with service.database.connect() as conn:
            assert conn.execute(
                "SELECT undone_at FROM tag_undo WHERE id=?", (deleted["operation_id"],)
            ).fetchone()[0] is None


async def test_file_watcher_does_not_lose_background_changes_when_api_refreshed(
    tmp_path, monkeypatch
):
    import importlib

    from douyin_wiki.webapp.app import _change_base, _watch_catalog

    config, service = _web_fixture(tmp_path)
    app = create_app(config, service=service, start_watcher=False)
    catalog = app.state.catalog
    notifier = ChangeNotifier(catalog.compute_fingerprint)
    path = config.vault_path / service.database.get_entry("dy-123").source_path

    async def changes(*args, **kwargs):
        # A local mutation is published before watchfiles delivers its file event.
        origin_token = _change_origin.set("tab")
        base_token = _change_base.set(catalog.compute_fingerprint())
        try:
            path.write_text(path.read_text() + "\n本页改动\n")
            catalog.refresh()
            await notifier.publish()
        finally:
            _change_origin.reset(origin_token)
            _change_base.reset(base_token)
        yield set()
        assert notifier.version == 1
        # A GET refresh consumes a worker write before watchfiles notices it.
        path.write_text(path.read_text() + "\n后台改动\n")
        catalog.refresh()
        yield set()
        assert notifier.version == 2

    module = importlib.import_module("douyin_wiki.webapp.app")
    monkeypatch.setattr(module, "awatch", changes)
    await _watch_catalog(catalog, notifier, asyncio.Event())
    assert set(notifier.library_payload(0)["origins"]) == {"tab", None}


async def test_multiple_local_mutations_are_acknowledged_but_background_is_not():
    from douyin_wiki.webapp.app import _change_base

    state = {"fingerprint": ()}
    notifier = ChangeNotifier(lambda: state["fingerprint"])
    origin = _change_origin.set("tab")
    try:
        for fingerprint in (("one",), ("two",)):
            base = _change_base.set(state["fingerprint"])
            state["fingerprint"] = fingerprint
            await notifier.publish()
            _change_base.reset(base)
        assert notifier.already_published((), ("two",))
        state["fingerprint"] = ("background",)
        base = _change_base.set(state["fingerprint"])
        state["fingerprint"] = ("three",)
        await notifier.publish()
        _change_base.reset(base)
        assert not notifier.already_published(("two",), ("three",))
    finally:
        _change_origin.reset(origin)


async def test_request_spans_acknowledge_only_changes_under_the_mutation_lock(tmp_path):
    from douyin_wiki.vault import entry_operation_observer
    from douyin_wiki.webapp.app import _change_spans

    writer = VaultWriter(tmp_path)
    path = tmp_path / "source.md"
    path.write_text("original")

    def fingerprint():
        return (path.read_text(),)

    notifier = ChangeNotifier(fingerprint)
    spans = []
    span_token = _change_spans.set(spans)
    origin_token = _change_origin.set("tab")

    def observe():
        before = fingerprint()
        return lambda: spans.append((before, fingerprint()))

    observer_token = entry_operation_observer.set(observe)
    try:
        # A worker write between request arrival and lock acquisition is not local.
        path.write_text("background")

        def mutate():
            with writer.entry_operations_locked(), writer.entry_operations_locked():
                path.write_text("local")

        await asyncio.to_thread(mutate)
        await notifier.publish()
        assert spans == [(("background",), ("local",))]
        assert notifier.already_published(("background",), ("local",))
        assert not notifier.already_published(("original",), ("local",))
    finally:
        entry_operation_observer.reset(observer_token)
        _change_spans.reset(span_token)
        _change_origin.reset(origin_token)
