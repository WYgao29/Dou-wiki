from __future__ import annotations

import json

import pytest

from douyin_wiki.webapp.app import create_app
from tests.support.web import _web_fixture, local_client


def topic_fixture(tmp_path):
    config, service = _web_fixture(tmp_path)
    entry = service.database.get_entry("dy-123")
    service.vault.write_entry(entry, service.database.get_entry_data(entry.id))
    topic = service.create_topic("研究专题", [entry.id], goal="比较方法", instructions="保留出处")
    topic_id = topic["topic"]["id"]
    literal = "## 我的判断\n\n逐字保留 **想法** 和来源。"
    note = service.save_topic_note(topic_id, literal, confirmed=True)
    return config, service, topic_id, note


def test_topic_web_trash_roundtrip_preserves_notes_and_source_files(tmp_path):
    config, service, topic_id, note = topic_fixture(tmp_path)
    other = service.create_topic("另一个专题", ["dy-123"])["topic"]["id"]
    entry = service.database.get_entry("dy-123")
    source = config.vault_path / entry.source_path
    original = source.read_bytes()
    topic_dir = config.vault_path / "topics" / topic_id
    with local_client(create_app(config, service=service, start_watcher=False)) as client:
        assert client.request("DELETE", f"/api/topics/{topic_id}", json={}).status_code == 400
        deleted = client.request(
            "DELETE", f"/api/topics/{topic_id}", json={"confirmed": True}
        )
        assert deleted.status_code == 200, deleted.text
        trash_id = deleted.json()["trash_id"]
        assert client.get(f"/api/topics/{topic_id}").status_code == 404
        assert not topic_dir.exists()
        assert source.read_bytes() == original
        assert service.database.get_entry("dy-123").id == "dy-123"
        assert service.database.get_topic(other).id == other
        trashed = next(row for row in client.get("/api/trash").json()["items"]
                       if row["trash_id"] == trash_id)
        assert (trashed["kind"], trashed["source_count"], trashed["note_count"]) == (
            "topic", 1, 1
        )
        service.rebuild_database_from_vault(apply=True)
        with pytest.raises(KeyError):
            service.database.get_topic(topic_id)
        restored = client.post(f"/api/trash/{trash_id}/restore", json={"confirmed": True})
        assert restored.status_code == 200, restored.text
        assert restored.json()["topic_id"] == topic_id
        current = client.get(f"/api/topics/{topic_id}").json()
        assert current["topic"]["goal"] == "比较方法"
        assert current["topic"]["instructions"] == "保留出处"
        assert current["topic"]["sources"][0]["enabled"] is True
        assert current["artifacts"][0]["content_markdown"] == note["content_markdown"]
        assert source.read_bytes() == original
        service.rebuild_database_from_vault(apply=True)
        assert service.get_topic(topic_id)["artifacts"][0]["id"] == note["id"]
        assert client.get("/api/trash").json()["items"] == []


def test_topic_delete_rolls_back_when_projection_delete_fails(tmp_path, monkeypatch):
    config, service, topic_id, note = topic_fixture(tmp_path)
    topic_dir = config.vault_path / "topics" / topic_id
    before = (topic_dir / "index.md").read_bytes()

    def fail(*args):
        raise OSError("database unavailable")

    monkeypatch.setattr(service.database, "delete_topic_projection", fail)
    with pytest.raises(OSError, match="database unavailable"):
        service.trash_topic(topic_id, confirmed=True)
    assert (topic_dir / "index.md").read_bytes() == before
    assert service.get_topic(topic_id)["artifacts"][0]["id"] == note["id"]
    assert service.list_trashed_topics() == []


@pytest.mark.parametrize("phase", ["deleting", "restoring"])
def test_topic_interrupted_operations_recover_without_losing_notes(tmp_path, monkeypatch, phase):
    config, service, topic_id, note = topic_fixture(tmp_path)
    if phase == "deleting":
        original = service.database.delete_topic_projection

        def interrupt(*args, **kwargs):
            original(*args, **kwargs)
            raise KeyboardInterrupt()

        with monkeypatch.context() as patch:
            patch.setattr(service.database, "delete_topic_projection", interrupt)
            with pytest.raises(KeyboardInterrupt):
                service.trash_topic(topic_id, confirmed=True)
        report = service.recover_entry_trash_operations()
        assert not report["warnings"], report
        deleted = service.list_trashed_topics()[0]
        service.restore_trashed_entry(deleted["trash_id"], confirmed=True)
    else:
        deleted = service.trash_topic(topic_id, confirmed=True)
        original = service.database.restore_topic_bundle

        def interrupt(*args, **kwargs):
            original(*args, **kwargs)
            raise KeyboardInterrupt()

        with monkeypatch.context() as patch:
            patch.setattr(service.database, "restore_topic_bundle", interrupt)
            with pytest.raises(KeyboardInterrupt):
                service.restore_trashed_entry(deleted["trash_id"], confirmed=True)
        report = service.recover_entry_trash_operations()
        assert not report["warnings"], report
    assert service.list_trashed_topics() == []
    assert (
        service.get_topic(topic_id)["artifacts"][0]["content_markdown"]
        == note["content_markdown"]
    )
    assert (config.vault_path / "topics" / topic_id / "index.md").exists()


def test_mixed_trash_restore_orders_articles_before_topics_and_purge_is_scoped(tmp_path):
    config, service, topic_id, note = topic_fixture(tmp_path)
    deleted_topic = service.trash_topic(topic_id, confirmed=True)
    deleted_entry = service.trash_entry("dy-123", confirmed=True)
    result = service.restore_trashed_entries(
        [deleted_topic["trash_id"], deleted_entry["trash_id"]], confirmed=True
    )
    assert result["failed"] == [] and result["warnings"] == []
    assert service.get_topic(topic_id)["topic"]["sources"][0]["entry_id"] == "dy-123"
    deleted_topic = service.trash_topic(topic_id, confirmed=True)
    with pytest.raises(ValueError, match="确认"):
        service.permanently_delete_trashed_entry(deleted_topic["trash_id"])
    service.permanently_delete_trashed_entries([deleted_topic["trash_id"]], confirmed=True)
    assert service.list_trashed_topics() == []
    assert service.database.get_entry("dy-123").id == "dy-123"
    assert (config.vault_path / service.database.get_entry("dy-123").source_path).exists()


def test_topic_trash_rejects_symlinks_and_corrupt_manifests(tmp_path):
    config, service, topic_id, _ = topic_fixture(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("preserve")
    link = config.vault_path / "topics" / topic_id / "external.txt"
    link.symlink_to(outside)
    with pytest.raises(ValueError, match="符号链接"):
        service.trash_topic(topic_id, confirmed=True)
    assert outside.read_text() == "preserve"
    link.unlink()
    deleted = service.trash_topic(topic_id, confirmed=True)
    manifest = service._topic_trash_item_dir(deleted["trash_id"]) / "manifest.json"
    manifest.write_text(json.dumps(["invalid"]))
    assert service.list_trashed_topics() == []
    with pytest.raises(ValueError, match="格式无效"):
        service.restore_trashed_entry(deleted["trash_id"], confirmed=True)
