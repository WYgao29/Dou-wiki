"""Deleted sources remain valid history while rebuilding the knowledge cache."""
from __future__ import annotations

import json

import pytest

from douyin_wiki import service_trash
from douyin_wiki.errors import EntryNotFoundError, JobStateError
from douyin_wiki.models import SourceRevision
from tests.test_topics import _add_entry


def _entry(service, entry_id, title):
    entry = _add_entry(service, entry_id, title, f"{title}的证据")
    data = service.database.get_entry_data(entry.id)
    data["metadata"] = {"source_kind": "video"}
    service.database.upsert_entry(entry, data)
    service.vault.write_entry(entry, data)
    return entry


def _relate(service, source, target_id, *, confidence=0.9):
    data = service.database.get_entry_data(source.id)
    data["relations"] = [{
        "target_entry_id": target_id,
        "relation_type": "supplement",
        "reason": "保留来源之间的证据关联",
        "confidence": confidence,
    }]
    service.database.upsert_entry(source, data)
    service.vault.write_entry(source, data)
    if any(entry.id == target_id for entry in service.database.list_entries()):
        service.database.replace_relations(source.id, data["relations"])
    return data["relations"]


def _assert_rebuildable(service):
    preview = service.rebuild_database_from_vault()
    for key in (
        "entry_errors", "creator_errors", "collection_errors", "topic_errors", "artifact_errors"
    ):
        assert preview[key] == [], preview
    assert service.rebuild_database_from_vault(apply=True)["status"] == "rebuilt"


def _revisions(note):
    return [SourceRevision.model_validate(value) for value in note["source_revisions"]]


@pytest.mark.parametrize("delete_all", [False, True])
def test_rebuild_keeps_note_history_after_source_deletion_and_restore(service, delete_all):
    first = _entry(service, "dy-1234567890123456789", "第一篇")
    second = _entry(service, "dy-1234567890123456790", "第二篇")
    first_relations = _relate(service, first, second.id)
    second_relations = _relate(service, second, first.id)
    topic_id = service.create_topic("保留用户笔记", [first.id, second.id])["topic"]["id"]
    literal = "## 我的判断\n\n删除来源后仍须逐字保留 **证据与判断**。"
    note = service.save_topic_note(topic_id, literal, confirmed=True)
    original_revisions = _revisions(note)
    _assert_rebuildable(service)

    deleted = [service.trash_entry(first.id, confirmed=True)]
    if delete_all:
        deleted.append(service.trash_entry(second.id, confirmed=True))
    assert all(item["warnings"] == [] for item in deleted)
    _assert_rebuildable(service)
    saved_note = service.get_topic(topic_id)["artifacts"][0]
    assert saved_note["id"] == note["id"]
    assert saved_note["content_markdown"] == literal
    assert _revisions(saved_note) == original_revisions
    assert [item.entry_id for item in service.database.get_topic(topic_id).sources] == (
        [] if delete_all else [second.id]
    )
    if not delete_all:
        assert service.database.get_relations(second.id) == []
        assert service.vault.load_entries()[0][1]["relations"] == second_relations

    for item in reversed(deleted):
        assert service.restore_trashed_entry(item["trash_id"], confirmed=True)["warnings"] == []
    _assert_rebuildable(service)
    restored_note = service.get_topic(topic_id)["artifacts"][0]
    assert restored_note["content_markdown"] == literal
    assert _revisions(restored_note) == original_revisions
    assert [item.entry_id for item in service.database.get_topic(topic_id).sources] == [
        first.id, second.id
    ]
    by_id = {entry.id: data for entry, data in service.vault.load_entries()}
    assert by_id[first.id]["relations"] == first_relations
    assert by_id[second.id]["relations"] == second_relations
    assert {item["target_entry_id"] for item in service.database.get_relations(first.id)} == {
        second.id
    }
    assert {item["target_entry_id"] for item in service.database.get_relations(second.id)} == {
        first.id
    }


def test_permanent_delete_preserves_only_identity_proof_and_rebuilds_history(service):
    first = _entry(service, "dy-1234567890123456789", "第一篇")
    second = _entry(service, "dy-1234567890123456790", "第二篇")
    relations = _relate(service, second, first.id)
    topic_id = service.create_topic("永久删除后的笔记", [first.id, second.id])["topic"]["id"]
    note = service.save_topic_note(topic_id, "用户写入的笔记须保留", confirmed=True)
    deleted = service.trash_entry(first.id, confirmed=True)

    result = service.permanently_delete_trashed_entry(deleted["trash_id"], confirmed=True)

    assert result["warnings"] == []
    assert service.list_trashed_entries() == []
    assert not service._trash_item_dir(deleted["trash_id"]).exists()
    proof_root = service.config.vault_path / "wiki" / ".data" / "deleted-entries"
    proofs = list(proof_root.glob("*.json"))
    assert len(proofs) == 1
    proof = json.loads(proofs[0].read_text())
    assert proof == {
        "version": 1,
        "entry_id": first.id,
        "video_id": first.video_id,
        "trash_id": deleted["trash_id"],
        "deleted_at": deleted["deleted_at"],
    }
    _assert_rebuildable(service)
    assert _revisions(service.get_topic(topic_id)["artifacts"][0]) == _revisions(note)
    assert service.vault.load_entries()[0][1]["relations"] == relations
    assert service.database.get_relations(second.id) == []
    with pytest.raises(EntryNotFoundError):
        service.restore_trashed_entry(deleted["trash_id"], confirmed=True)

    # The same work may be captured again; its current files still receive normal validation.
    recaptured = _entry(service, first.id, "重新采集的第一篇")
    assert not proofs[0].exists()
    _assert_rebuildable(service)
    assert service.database.get_entry(recaptured.id).title == "重新采集的第一篇"
    source = service.config.vault_path / recaptured.source_path
    source.write_text("---\ntype: broken\n---\n", encoding="utf-8")
    assert service.rebuild_database_from_vault()["entry_errors"]
    with pytest.raises(JobStateError):
        service.rebuild_database_from_vault(apply=True)


def test_rebuild_rejects_missing_history_without_valid_deletion_proof(service):
    first = _entry(service, "dy-1234567890123456789", "第一篇")
    second = _entry(service, "dy-1234567890123456790", "第二篇")
    _relate(service, second, first.id)
    topic_id = service.create_topic("来源损坏", [first.id, second.id])["topic"]["id"]
    note = service.save_topic_note(topic_id, "不能误将损坏识别成合法删除", confirmed=True)
    # Simulate lost documents instead of using the authorized deletion operation.
    for path in service._entry_managed_paths(first):
        path.unlink()
    service.database.delete_entry_projection(first.id)
    service.refresh_topic(topic_id)

    report = service.rebuild_database_from_vault()

    assert any("关系目标不存在" in item["error"] for item in report["entry_errors"])
    assert any("成果关联资料不存在" in item["error"] for item in report["artifact_errors"])
    with pytest.raises(JobStateError):
        service.rebuild_database_from_vault(apply=True)
    assert service.get_topic(topic_id)["artifacts"][0]["id"] == note["id"]


def test_deleted_relation_still_requires_valid_confidence(service):
    first = _entry(service, "dy-1234567890123456789", "第一篇")
    second = _entry(service, "dy-1234567890123456790", "第二篇")
    service.trash_entry(first.id, confirmed=True)
    _relate(service, second, first.id, confidence=2)

    report = service.rebuild_database_from_vault()

    assert any("confidence 超出范围" in item["error"] for item in report["entry_errors"])
    with pytest.raises(JobStateError):
        service.rebuild_database_from_vault(apply=True)


@pytest.mark.parametrize("damage", [
    "not_object", "version", "entry_id", "video_id", "trash_id", "deleted_at", "missing_id"
])
def test_invalid_deletion_receipt_cannot_allow_missing_history(service, damage):
    first = _entry(service, "dy-1234567890123456789", "第一篇")
    second = _entry(service, "dy-1234567890123456790", "第二篇")
    _relate(service, second, first.id)
    topic_id = service.create_topic("删除凭据损坏", [first.id, second.id])["topic"]["id"]
    service.save_topic_note(topic_id, "保留笔记并拒绝不可信删除记录", confirmed=True)
    deleted = service.trash_entry(first.id, confirmed=True)
    service.permanently_delete_trashed_entry(deleted["trash_id"], confirmed=True)
    receipt_path = service.vault.deleted_entry_receipts_root / f"{first.id}.json"
    receipt = json.loads(receipt_path.read_text())
    if damage == "not_object":
        receipt = [receipt]
    elif damage == "missing_id":
        receipt.pop("entry_id")
    else:
        receipt[damage] = {
            "version": 2,
            "entry_id": "dy-another-entry",
            "video_id": "../outside",
            "trash_id": "invalid",
            "deleted_at": "invalid",
        }[damage]
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    report = service.rebuild_database_from_vault()

    assert any("关系目标不存在" in item["error"] for item in report["entry_errors"])
    assert any("成果关联资料不存在" in item["error"] for item in report["artifact_errors"])
    with pytest.raises(JobStateError):
        service.rebuild_database_from_vault(apply=True)


@pytest.mark.parametrize("phase", ["prepared", "files_moved", "restoring", "restored"])
def test_incomplete_delete_or_restored_journal_does_not_allow_missing_history(service, phase):
    first = _entry(service, "dy-1234567890123456789", "第一篇")
    second = _entry(service, "dy-1234567890123456790", "第二篇")
    _relate(service, second, first.id)
    deleted = service.trash_entry(first.id, confirmed=True)
    item_dir, manifest = service._read_trash_manifest(deleted["trash_id"])
    manifest["phase"] = phase
    service._write_trash_manifest(item_dir, manifest)

    assert service.rebuild_database_from_vault()["entry_errors"]
    with pytest.raises(JobStateError):
        service.rebuild_database_from_vault(apply=True)


def test_legacy_completed_trash_journal_allows_missing_history(service):
    first = _entry(service, "dy-1234567890123456789", "第一篇")
    second = _entry(service, "dy-1234567890123456790", "第二篇")
    _relate(service, second, first.id)
    deleted = service.trash_entry(first.id, confirmed=True)
    item_dir, manifest = service._read_trash_manifest(deleted["trash_id"])
    manifest.pop("phase")
    service._write_trash_manifest(item_dir, manifest)

    _assert_rebuildable(service)
    assert service.restore_trashed_entry(deleted["trash_id"], confirmed=True)["warnings"] == []
    _assert_rebuildable(service)


def test_deletion_proof_does_not_allow_missing_current_topic_source(service):
    first = _entry(service, "dy-1234567890123456789", "第一篇")
    second = _entry(service, "dy-1234567890123456790", "第二篇")
    topic_id = service.create_topic("当前来源仍需完整", [first.id, second.id])["topic"]["id"]
    original_topic = service.database.get_topic(topic_id)
    service.trash_entry(first.id, confirmed=True)
    service.vault.write_topic(
        original_topic, [], {entry.id: entry for entry in service.database.list_entries()}
    )

    report = service.rebuild_database_from_vault()

    assert any("专题来源资料不存在" in item["error"] for item in report["topic_errors"])
    with pytest.raises(JobStateError):
        service.rebuild_database_from_vault(apply=True)


def test_receipt_write_failure_leaves_permanent_delete_fully_restorable(service, monkeypatch):
    entry = _entry(service, "dy-1234567890123456789", "完整保留待恢复")
    deleted = service.trash_entry(entry.id, confirmed=True)
    item_dir, original_manifest = service._read_trash_manifest(deleted["trash_id"])
    original_write = service.vault._atomic_write

    def fail_receipt(path, content):
        if path.parent == service.vault.deleted_entry_receipts_root:
            raise OSError("receipt unavailable")
        original_write(path, content)

    monkeypatch.setattr(service.vault, "_atomic_write", fail_receipt)
    with pytest.raises(OSError, match="receipt unavailable"):
        service.permanently_delete_trashed_entry(deleted["trash_id"], confirmed=True)
    assert service._read_trash_manifest(deleted["trash_id"])[1] == original_manifest
    assert all((item_dir / "files" / item["path"]).exists() for item in original_manifest["files"])
    assert service.restore_trashed_entry(deleted["trash_id"], confirmed=True)["warnings"] == []
    _assert_rebuildable(service)


@pytest.mark.parametrize("remove_receipt", [False, True])
def test_purge_recovery_persists_identity_before_removing_recoverable_files(
    service, monkeypatch, remove_receipt
):
    first = _entry(service, "dy-1234567890123456789", "第一篇")
    second = _entry(service, "dy-1234567890123456790", "第二篇")
    _relate(service, second, first.id)
    deleted = service.trash_entry(first.id, confirmed=True)
    item_dir = service._trash_item_dir(deleted["trash_id"])
    receipt_path = service.vault.deleted_entry_receipts_root / f"{first.id}.json"
    original_remove = service_trash.shutil.rmtree

    def crash_before_remove(path, *args, **kwargs):
        if path == item_dir:
            assert receipt_path.is_file()
            raise SystemExit("crash before purged files are removed")
        return original_remove(path, *args, **kwargs)

    monkeypatch.setattr(service_trash.shutil, "rmtree", crash_before_remove)
    with pytest.raises(SystemExit):
        service.permanently_delete_trashed_entry(deleted["trash_id"], confirmed=True)
    assert service._read_trash_manifest(deleted["trash_id"])[1]["phase"] == "purging"
    if remove_receipt:
        # An older runtime may have recorded purging without the optional receipt.
        receipt_path.unlink()

    def remove_with_proof(path, *args, **kwargs):
        if path == item_dir:
            assert receipt_path.is_file()
        return original_remove(path, *args, **kwargs)

    monkeypatch.setattr(service_trash.shutil, "rmtree", remove_with_proof)
    report = service.recover_entry_trash_operations()

    assert report["warnings"] == []
    assert report["completed"] == [deleted["trash_id"]]
    assert not item_dir.exists()
    assert service.list_trashed_entries() == []
    _assert_rebuildable(service)
