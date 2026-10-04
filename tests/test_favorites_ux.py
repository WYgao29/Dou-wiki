"""Favorites UX contracts using synthetic inventory and the real store."""

from douyin_wiki.models import JobStatus
from tests.test_favorites import A, B, FakeFavoritesAdapter, snapshot


async def read_inventory(service, **options):
    service.favorites.adapter = FakeFavoritesAdapter(snapshot(kinds=[(A, "video"), (B, "video")]))
    parent = service.favorites.start(**options)
    await service.process_claimed_job(parent)
    return parent.id


async def test_new_inventory_requires_explicit_selection(service):
    job_id = await read_inventory(service)
    data = service.favorites.get(job_id)
    assert data["summary"]["selected"] == 0
    assert not any(item["selected"] for item in data["items"])
    assert [job.kind for job in service.database.list_jobs()] == ["favorites_import"]


async def test_incremental_preserves_choices_but_full_starts_unselected(service):
    first = await read_inventory(service)
    service.favorites.select(first, selected=True, work_ids=[A])
    second = await read_inventory(service)
    assert {i["work_id"] for i in service.favorites.get(second)["items"] if i["selected"]} == {A}
    full = await read_inventory(service, update_mode="full")
    assert service.favorites.get(full)["summary"]["selected"] == 0


async def test_select_filtered_applies_to_all_matching_pages(service):
    job_id = await read_inventory(service)
    result = service.favorites.select_filtered(job_id, selected=True, query=B[-4:])
    assert {i["work_id"] for i in result["items"] if i["selected"]} == {B}
    assert result["summary"]["selected"] == 1
    assert service.favorites.get(job_id, query=A[-4:], only_unimported=True)["total"] == 1


async def test_cancelled_inventory_keeps_choices_for_new_incremental_read(service):
    first = await read_inventory(service)
    service.favorites.select(first, selected=True, work_ids=[B])
    service.cancel_job(first)
    assert service.database.get_job(first).status == JobStatus.CANCELLED
    second = await read_inventory(service)
    assert {i["work_id"] for i in service.favorites.get(second)["items"] if i["selected"]} == {B}


async def test_confirmation_preview_counts_new_tasks_and_reused_work(service):
    service.capture_douyin(f"https://www.douyin.com/video/{A}")
    job_id = await read_inventory(service)
    service.favorites.select(job_id, selected=True)
    data = service.favorites.get(job_id)
    assert data["confirmation_preview"] == {
        "selected": 2,
        "new_tasks": 1,
        "reused_tasks": 1,
        "already_imported": 0,
        "source_kinds": {"video": 2},
    }
    assert data["created_at"] and data["updated_at"]


async def test_filtered_selection_does_not_truncate_at_a_page_boundary(service):
    service.favorites.adapter = FakeFavoritesAdapter(
        snapshot(kinds=[(str(800000 + i), "video") for i in range(1001)])
    )
    parent = service.favorites.start()
    await service.process_claimed_job(parent)
    result = service.favorites.select_filtered(parent.id, selected=True, query="测试作品")
    assert result["summary"]["selected"] == 1001


async def test_filtered_selection_respects_new_only_and_folder_scope(service):
    service.favorites.adapter = FakeFavoritesAdapter(snapshot(kinds=[(A, "video")]))
    first = service.favorites.start()
    await service.process_claimed_job(first)
    second = await read_inventory(service)
    data = service.favorites.select_filtered(
        second, selected=True, only_new=True, folder_id="folder-1"
    )
    assert {i["work_id"] for i in data["items"] if i["selected"]} == {B}


async def test_web_filters_skip_imported_work_and_preserve_the_original_api(tmp_path):
    from douyin_wiki.webapp.app import create_app
    from tests.support.web import _web_fixture, local_client

    config, service = _web_fixture(tmp_path)
    service.favorites.adapter = FakeFavoritesAdapter(
        snapshot(kinds=[("123", "video"), ("456", "video")])
    )
    parent = service.favorites.start()
    await service.process_claimed_job(parent)
    with local_client(create_app(config, service=service, start_watcher=False)) as client:
        url = f"/api/favorites/imports/{parent.id}"
        assert client.get(url).json()["total"] == 2
        filtered = client.get(url, params={"only_unimported": True}).json()
        assert [i["work_id"] for i in filtered["items"]] == ["456"]
        selected = client.post(
            url + "/selection",
            json={"selected": True, "filters": {"only_unimported": True, "folder_id": "folder-1"}},
        ).json()
        assert selected["summary"]["selected"] == 1
        assert {i["work_id"] for i in selected["items"] if i["selected"]} == {"456"}
        invalid = client.post(
            url + "/selection", json={"selected": True, "work_ids": ["456"], "filters": {}}
        )
        assert invalid.status_code == 422


async def test_batch_library_link_includes_skipped_selected_entries_and_excludes_others(tmp_path):
    from douyin_wiki.webapp.app import create_app
    from tests.support.web import _web_fixture, local_client

    config, service = _web_fixture(tmp_path)
    extra = tmp_path / "vault/wiki/sources/另一篇_999.md"
    extra.write_text(
        "---\ntype: source\nvideo_id: '999'\nsource_kind: video\n"
        "captured_at: '2026-10-04T19:00:00+08:00'\n---\n# 另一篇\n"
    )
    service.favorites.adapter = FakeFavoritesAdapter(snapshot(kinds=[("123", "video")]))
    parent = service.favorites.start()
    await service.process_claimed_job(parent)
    app = create_app(config, service=service, start_watcher=False)
    with local_client(app) as client:
        assert client.get("/api/library").json()["total"] == 2
        assert client.get("/api/library", params={"favorites_job": parent.id}).json()["total"] == 0
        service.favorites.select(parent.id, selected=True)
        service.favorites.confirm(parent.id)
        batch = client.get("/api/library", params={"favorites_job": parent.id}).json()
        assert [i["entry_id"] for i in batch["items"]] == ["dy-123"]
        detail = client.get(f"/api/jobs/{parent.id}").json()
        assert detail["imported_entry_ids"] == ["dy-123"]
        assert detail["batch"]["summary"]["in_library"] == 1
        # A published source still belongs to this batch after its raw cache is removed.
        (config.vault_path / "raw/测试记录.md").unlink()
        assert service.favorites.get(parent.id)["imported_entry_ids"] == ["dy-123"]
        batch = client.get("/api/library", params={"favorites_job": parent.id}).json()
        assert [i["entry_id"] for i in batch["items"]] == ["dy-123"]
        assert client.get("/api/library?favorites_job=missing").status_code == 404
