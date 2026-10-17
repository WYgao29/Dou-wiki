"""Materials, retention snapshots and search boundaries from deployment feedback."""

import shutil
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from douyin_wiki.errors import ExternalToolError
from douyin_wiki.models import CaptureOptions, RetentionPolicy
from douyin_wiki.time_utils import utc_now
from douyin_wiki.webapp.app import create_app
from douyin_wiki.worker import Worker
from tests.support.web import local_client
from tests.test_service import complete_gateway_job
from tests.test_video_detail import video_fixture


def test_keyframe_policy_changes_capture_sharing_but_legacy_flags_do_not():
    regular = CaptureOptions(retention="temporary", keep_keyframes=False)
    preserve = CaptureOptions(retention="temporary", keep_keyframes=True)
    assert regular.processing_key() != preserve.processing_key()
    assert CaptureOptions(keep_keyframes=None).processing_key() == regular.processing_key()
    assert CaptureOptions(retention="keep", keep_keyframes=False).processing_key() == (
        CaptureOptions(retention="keep", keep_keyframes=True).processing_key()
    )


def test_new_capture_snapshots_configured_frame_policy(service):
    service.config.media.keep_keyframes = True
    saved = service.capture_douyin("https://www.douyin.com/video/1234567890123456789")
    assert saved.request.options.keep_keyframes is True
    service.config.media.keep_keyframes = False
    assert service.get_job(saved.id).request.options.keep_keyframes is True


def test_article_lists_only_existing_keyframes_owned_by_this_work(tmp_path):
    config, service, video = video_fixture(tmp_path)
    frames = video.parent / "frames"
    frames.mkdir()
    frame = frames / "frame-0000001200.jpg"
    frame.write_bytes(b"frame")
    other = config.vault_path / "raw/assets/456/frames/frame-0000001200.jpg"
    other.parent.mkdir(parents=True)
    other.write_bytes(b"other work")
    linked = frames / "frame-0000002400.jpg"
    linked.symlink_to(other)
    data = service.database.get_entry_data("dy-123")
    data["frames"] = [
        {"path": str(path.relative_to(config.vault_path)), "timestamp_ms": 1200}
        for path in [frame, other, linked, frames / "missing.jpg"]
    ]
    service.database.upsert_entry(service.database.get_entry("dy-123"), data)
    with local_client(create_app(config, service=service, start_watcher=False)) as client:
        result = client.get("/api/articles/dy-123").json()
    assert len(result["keyframes"]) == 1
    assert result["keyframes"][0]["timestamp_ms"] == 1200
    assert result["keyframes"][0]["url"].startswith("/media/raw/assets/123/frames/")
    assert str(config.vault_path) not in str(result["keyframes"])


@pytest.mark.parametrize("keep", [False, True])
def test_expired_video_retains_only_snapshotted_frames(tmp_path, monkeypatch, keep):
    config, service, video = video_fixture(tmp_path)
    frame = video.parent / "frames/frame-0000001200.jpg"
    frame.parent.mkdir()
    frame.write_bytes(b"frame")
    entry = service.database.get_entry("dy-123").model_copy(
        update={
            "retention": RetentionPolicy.TEMPORARY,
            "media_expires_at": utc_now() - timedelta(days=1),
        }
    )
    data = service.database.get_entry_data(entry.id)
    data["media_policy"] = {"keep_keyframes": keep}
    service.database.upsert_entry(entry, data)
    trash = tmp_path / "trash"
    trash.mkdir()
    monkeypatch.setattr(
        "douyin_wiki.service_maintenance.send2trash",
        lambda value: shutil.move(value, trash / Path(value).name),
    )
    preview = service.run_maintenance(apply=False)
    assert preview["media_candidates"] == [entry.id]
    assert preview["keyframes_retained"] == ([entry.id] if keep else [])
    with local_client(create_app(config, service=service, start_watcher=False)) as client:
        displayed = client.post("/api/system/maintenance", json={"confirmed": False}).json()
    assert ("关键帧" in " ".join(displayed["will_keep"])) == keep
    assert video.exists() and frame.exists()
    service.config.media.keep_keyframes = not keep
    service.run_maintenance(apply=True)
    assert not video.exists()
    assert frame.exists() == keep
    assert service.database.get_entry(entry.id).media_status == "removed"


def test_semantic_search_unavailable_returns_keyword_results_with_notice(tmp_path):
    config, service, _ = video_fixture(tmp_path)
    with local_client(create_app(config, service=service, start_watcher=False)) as client:
        response = client.get("/api/library/search", params={"q": "本地资料"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "keyword"
    assert payload["notice"]
    assert [item["entry_id"] for item in payload["items"]] == ["dy-123"]


@pytest.mark.parametrize(
    "failure",
    [
        ExternalToolError("模型不可用"),
        sqlite3.OperationalError("索引不可读"),
        ValueError("无效向量"),
    ],
)
def test_semantic_search_read_failure_returns_keyword_results(tmp_path, monkeypatch, failure):
    config, service, _ = video_fixture(tmp_path)
    service.embeddings.provider_name = "sentence-transformers:test"
    monkeypatch.setattr(service.embeddings, "signature", lambda: "test")

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(service, "search_knowledge", fail)
    with local_client(create_app(config, service=service, start_watcher=False)) as client:
        result = client.get("/api/library/search", params={"q": "本地资料"}).json()
    assert result["mode"] == "keyword"
    assert [item["entry_id"] for item in result["items"]] == ["dy-123"]
    assert result["notice"]


def test_semantic_search_applies_scope_before_ranking_and_never_expands_empty_topic(
    tmp_path, monkeypatch
):
    from douyin_wiki.models import Evidence
    from tests.test_tags import source

    config, service, _ = video_fixture(tmp_path)
    source(service, "1000", ["其他"], managed=True)
    service.embeddings.provider_name = "sentence-transformers:test"
    monkeypatch.setattr(service.embeddings, "signature", lambda: "test")
    calls = []

    def search(query, *, entry_ids, limit):
        calls.append(entry_ids)
        return [
            Evidence(
                entry_id=entry_id,
                snippet="窗边布局使房间明亮。",
                timestamp_ms=1200,
                original_url="https://www.douyin.com/video/123",
                status="active",
                confidence=0.9,
            )
            for entry_id in ["dy-1000", "dy-123"]
        ]

    monkeypatch.setattr(service, "search_knowledge", search)
    topic_id = service.create_topic("空范围", entry_ids=["dy-123"])["topic"]["id"]
    service.set_topic_sources(topic_id, [{"entry_id": "dy-123", "enabled": False}])
    with local_client(create_app(config, service=service, start_watcher=False)) as client:
        result = client.get("/api/library/search", params={"q": "自然采光", "tag": "咖啡"}).json()
        assert result["mode"] == "semantic"
        assert [item["entry_id"] for item in result["items"]] == ["dy-123"]
        assert result["items"][0]["search_evidence"][0]["timestamp_ms"] == 1200
        assert calls == [["dy-123"]]
        result = client.get(
            "/api/library/search", params={"q": "自然采光", "topic_id": topic_id}
        ).json()
        assert result["items"] == []
        assert len(calls) == 1


def test_legacy_frames_have_only_verified_filename_timestamps(tmp_path):
    config, service, video = video_fixture(tmp_path)
    root = video.parent / "frames"
    root.mkdir()
    (root / "frame-0000001200.jpg").write_bytes(b"frame")
    (root / "frame-001.jpg").write_bytes(b"frame")
    (root / "cover.jpg").write_bytes(b"cover")
    with local_client(create_app(config, service=service, start_watcher=False)) as client:
        frames = client.get("/api/articles/dy-123").json()["keyframes"]
    assert len(frames) == 2
    assert [frame["timestamp_ms"] for frame in frames] == [1200, None]


def test_creator_keyframes_remain_visible_when_the_video_has_expired(tmp_path):
    config, service, video = video_fixture(tmp_path)
    entry = service.database.get_entry("dy-123")
    creator = config.vault_path / "creators/作者_123"
    (creator / "sources").mkdir(parents=True)
    (creator / "raw/assets").mkdir(parents=True)
    source = creator / "sources/资料_123.md"
    (config.vault_path / entry.source_path).rename(source)
    assets = creator / "raw/assets/123"
    video.parent.rename(assets)
    (assets / video.name).unlink()
    frame = assets / "frames/frame-0000001200.png"
    frame.parent.mkdir()
    frame.write_bytes(b"frame")
    entry = entry.model_copy(
        update={
            "source_path": source.relative_to(config.vault_path).as_posix(),
            "raw_path": "creators/作者_123/raw/records/123.md",
            "media_status": "removed",
        }
    )
    data = service.database.get_entry_data(entry.id)
    data["frames"] = [
        {"path": frame.relative_to(config.vault_path).as_posix(), "timestamp_ms": 1200}
    ]
    service.database.upsert_entry(entry, data)
    with service.database.connect() as conn:
        conn.execute(
            "UPDATE entries SET raw_path=?, source_path=? WHERE id=?",
            (entry.raw_path, entry.source_path, entry.id),
        )
    with local_client(create_app(config, service=service, start_watcher=False)) as client:
        materials = client.get("/api/articles/dy-123").json()
    assert materials["item"]["local_video_url"] is None
    assert len(materials["keyframes"]) == 1
    assert "/creators/" in materials["keyframes"][0]["url"]


async def test_frame_policy_survives_publication_rebuild_deletion_and_restore(service):
    job = service.capture_douyin(
        "https://v.douyin.com/uvHsRpXIn8s/", options=CaptureOptions(keep_keyframes=True)
    )
    await Worker(service).run_once()
    completed = await complete_gateway_job(service, job.id, gateway_protocol=2)
    entry_id = completed.result["entry_id"]
    data = service.database.get_entry_data(entry_id)
    frame = service.config.vault_path / data["frames"][0]["path"]
    assert frame.exists()
    service.rebuild_database_from_vault(apply=True)
    rebuilt = service.database.get_entry_data(entry_id)
    assert rebuilt["frame_manifest_version"] == 1
    assert rebuilt["frames"] == data["frames"]
    assert rebuilt["media_policy"] == {"keep_keyframes": True}
    removed = service.trash_entry(entry_id, confirmed=True)
    assert not frame.exists()
    service.restore_trashed_entry(removed["trash_id"], confirmed=True)
    assert frame.exists()
    assert service.database.get_entry_data(entry_id)["media_policy"] == rebuilt["media_policy"]


@pytest.mark.parametrize("policy", [None, False, "true", {"keep_keyframes": "true"}])
def test_malformed_or_legacy_policy_never_extends_retention(tmp_path, policy):
    config, service, _ = video_fixture(tmp_path)
    entry = service.database.get_entry("dy-123").model_copy(
        update={"retention": RetentionPolicy.TEMPORARY}
    )
    data = service.database.get_entry_data(entry.id)
    data.update({"media_policy": policy, "frames": None, "frame_manifest_version": 1})
    service.database.upsert_entry(entry, data)
    assert not service._preserve_keyframes(entry)
    with local_client(create_app(config, service=service, start_watcher=False)) as client:
        material = client.get("/api/articles/dy-123").json()
    assert material["keyframes"] == []
    assert material["keep_keyframes"] is False


def test_preserving_frames_never_traverses_linked_asset_directory(tmp_path, monkeypatch):
    _config, service, video = video_fixture(tmp_path)
    external = tmp_path / "private-assets"
    video.parent.rename(external)
    video.parent.symlink_to(external, target_is_directory=True)
    removed = []
    monkeypatch.setattr("douyin_wiki.service_maintenance.send2trash", removed.append)
    entry = service.database.get_entry("dy-123").model_copy(
        update={"retention": RetentionPolicy.TEMPORARY}
    )
    with pytest.raises(ValueError):
        service.trash_assets(entry, preserve_keyframes=True)
    assert (external / video.name).exists()
    assert removed == []


@pytest.mark.parametrize("kind", ["creator", "favorites", "collection"])
async def test_batch_children_keep_creation_time_frame_policy(service, kind):
    from tests.test_capture_option_compatibility import confirm_batch, selected_batch
    from tests.test_collection_flow import A

    service.config.media.keep_keyframes = True
    job_id = await selected_batch(service, kind, (A,))
    service.config.media.keep_keyframes = False
    result = confirm_batch(service, kind, job_id)
    child = service.get_job(result.result["child_job_ids"][0])
    assert child.request.options.keep_keyframes is True


async def test_reacquisition_keeps_the_existing_entry_frame_policy(service):
    share = "https://v.douyin.com/uvHsRpXIn8s/"
    first = service.capture_douyin(share, options=CaptureOptions(keep_keyframes=False))
    await Worker(service).run_once()
    completed = await complete_gateway_job(service, first.id, gateway_protocol=2)
    entry_id = completed.result["entry_id"]
    entry = service.database.get_entry(entry_id).model_copy(update={"media_status": "removed"})
    service.database.upsert_entry(entry, service.database.get_entry_data(entry_id))
    service.config.media.keep_keyframes = True
    second = service.capture_douyin(share)
    assert second.request.options.keep_keyframes is True
    await Worker(service).run_once()
    await complete_gateway_job(service, second.id, gateway_protocol=2)
    assert service.database.get_entry_data(entry_id)["media_policy"] == {"keep_keyframes": False}
