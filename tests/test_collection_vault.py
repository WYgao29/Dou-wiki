from __future__ import annotations

import re
from urllib.parse import unquote

import pytest

from douyin_wiki.adapters.share import DouyinShareResolver
from douyin_wiki.collection_models import CollectionProfile
from douyin_wiki.errors import JobStateError
from douyin_wiki.models import CaptureRequest, JobStatus
from tests.conftest import FakeResolver
from tests.support.gateway import complete_gateway_job
from tests.test_collection_flow import MIX, A, B, FakeCollectionAdapter, snapshot


async def read(service, inventory):
    service.resolver = DouyinShareResolver()
    service.collections.adapter = FakeCollectionAdapter(inventory)
    parent = service.collections.start(inventory.profile.canonical_url)
    result = await service.process_claimed_job(parent)
    assert result.status == JobStatus.NEEDS_SELECTION
    return parent.id


async def import_entry(service):
    service.resolver = FakeResolver()
    result = await service.process_claimed_job(
        service.database.create_job(CaptureRequest(share_text=f"https://www.douyin.com/video/{A}"))
    )
    if result.status == JobStatus.AWAITING_AGENT_ANALYSIS:
        result = await complete_gateway_job(service, result.id)
    assert result.status == JobStatus.COMPLETED
    return service.database.get_entry(result.result["entry_id"])


async def test_collection_links_existing_entry_without_copying_knowledge(service):
    entry = await import_entry(service)
    parent = await read(service, snapshot((A,)))
    # Existing intact knowledge can be associated without a new selection or download.
    result = service.collections.confirm(parent)
    assert result.status == JobStatus.COMPLETED
    collection = service.collections.get_collection(MIX)
    assert collection["works"][0]["entry_id"] == entry.id
    assert len(service.database.list_entries()) == 1
    index = service.config.vault_path / collection["profile"]["folder_path"] / "index.md"
    target = re.search(r"\[抖音作品\]\((.*?)\)", index.read_text()).group(1)
    assert (index.parent / unquote(target)).resolve() == (
        service.config.vault_path / entry.source_path
    ).resolve()
    sidecar = index.parent / ".data" / "collection.md"
    assert "collection-machine-data" in sidecar.read_text()
    assert "account_id" not in sidecar.read_text()


async def test_collection_rename_and_partial_read_preserve_complete_membership(service):
    await read(service, snapshot())
    first = service.collections.get_collection(MIX)
    partial = snapshot((B, "7650097901481364778"), complete=False)
    partial.profile.name = "改名后的合集"
    await read(service, partial)
    current = service.collections.get_collection(MIX)
    assert current["profile"]["folder_path"] == first["profile"]["folder_path"]
    assert current["profile"]["name"] == "改名后的合集"
    assert [
        (w["work_id"], w["position"]) for w in current["works"] if w["present_in_last_complete"]
    ] == [(A, 1), (B, 2)]
    assert not current["works"][-1]["present_in_last_complete"]
    index = service.config.vault_path / current["profile"]["folder_path"] / "index.md"
    assert "部分读取发现" in index.read_text()


async def test_collection_document_failure_restores_files_and_projection(service, monkeypatch):
    await read(service, snapshot())
    original = service.collections.get_collection(MIX)
    index = service.config.vault_path / original["profile"]["folder_path"] / "index.md"
    before = index.read_bytes()
    writer = service.vault.write_collection

    def fail(*args, **kwargs):
        writer(*args, **kwargs)
        raise OSError("simulated disk failure")

    monkeypatch.setattr(service.vault, "write_collection", fail)
    service.collections.adapter = FakeCollectionAdapter(snapshot((B,)))
    parent = service.collections.start(f"https://www.douyin.com/collection/{MIX}")
    result = await service.process_claimed_job(parent)
    assert result.status == JobStatus.FAILED
    assert index.read_bytes() == before
    assert service.collections.get_collection(MIX) == original


async def test_collection_sidecar_error_preserves_existing_database(service):
    entry = await import_entry(service)
    parent = await read(service, snapshot((A,)))
    service.collections.confirm(parent)
    data = service.collections.get_collection(MIX)
    sidecar = service.config.vault_path / data["profile"]["folder_path"] / ".data" / "collection.md"
    sidecar.write_text(sidecar.read_text().replace(f"work_id: '{A}'", "work_id: ../bad"))
    report = service.rebuild_database_from_vault()
    assert report["collection_errors"]
    with pytest.raises(JobStateError):
        service.rebuild_database_from_vault(apply=True)
    assert service.database.get_entry(entry.id).id == entry.id
    assert service.collections.get_collection(MIX) == data


async def test_collection_rebuild_rejects_foreign_entry_and_active_inventory(service):
    entry = await import_entry(service)
    await read(service, snapshot((A,)))
    with pytest.raises(JobStateError, match="未完成的合集"):
        service.rebuild_database_from_vault(apply=True)
    data = service.collections.get_collection(MIX)
    sidecar = service.config.vault_path / data["profile"]["folder_path"] / ".data" / "collection.md"
    sidecar.write_text(sidecar.read_text().replace(f"entry_id: {entry.id}", "entry_id: dy-missing"))
    assert service.rebuild_database_from_vault()["collection_errors"]
    with pytest.raises(JobStateError, match="无法解析"):
        service.rebuild_database_from_vault(apply=True)
    assert service.database.get_entry(entry.id).id == entry.id


async def test_collection_projection_rebuilds_from_sidecar(service):
    entry = await import_entry(service)
    parent = await read(service, snapshot((A,)))
    service.collections.confirm(parent)
    before = service.collections.get_collection(MIX)
    report = service.rebuild_database_from_vault(apply=True)
    assert report["collection_count"] == 1
    assert service.collections.get_collection(MIX) == before
    assert service.database.get_entry(entry.id).id == entry.id


async def test_restore_entry_reconnects_all_collections(service):
    entry = await import_entry(service)
    for mid in (MIX, "7651028428363139127"):
        inventory = snapshot((A,))
        inventory.profile = CollectionProfile(mix_id=mid, name="测试合集")
        parent = await read(service, inventory)
        service.collections.confirm(parent)
    deleted = service.trash_entry(entry.id, confirmed=True)
    for collection in service.collections.list_collections():
        assert collection["works"][0]["entry_id"] is None
    service.restore_trashed_entry(deleted["trash_id"], confirmed=True)
    for collection in service.collections.list_collections():
        assert collection["works"][0]["entry_id"] == entry.id
