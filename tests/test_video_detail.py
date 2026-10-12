from __future__ import annotations

import pytest

from douyin_wiki.webapp.app import create_app
from tests.support.web import _web_fixture, local_client

RAW_TRANSCRIPT = [
    {"id": 0, "start_ms": 1200, "end_ms": 4000, "text": "原始识别文字。"},
    {"id": 1, "start_ms": 4500, "end_ms": 8000, "text": "第二段完整保留。"},
]
CORRECTED_TRANSCRIPT = [
    {**RAW_TRANSCRIPT[0], "text": "校正后的专有名词。"},
    RAW_TRANSCRIPT[1],
]


def video_fixture(tmp_path, *, corrected=True):
    config, service = _web_fixture(tmp_path)
    video = config.vault_path / "raw/assets/123/本地 视频.mp4"
    video.write_bytes(bytes(range(256)) * 4)
    entry = service.database.get_entry("dy-123")
    data = service.database.get_entry_data(entry.id)
    data["metadata"]["media_path"] = str(video)
    data["transcript_raw"] = RAW_TRANSCRIPT
    if corrected:
        data["transcript_corrected"] = CORRECTED_TRANSCRIPT
    service.database.upsert_entry(entry, data)
    return config, service, video


@pytest.mark.parametrize("corrected", [True, False])
def test_article_exposes_complete_transcript_and_local_video(tmp_path, corrected):
    config, service, _video = video_fixture(tmp_path, corrected=corrected)
    with local_client(create_app(config, service=service, start_watcher=False)) as client:
        article = client.get("/api/articles/dy-123").json()
    assert article["transcript"] == {
        "source": "corrected" if corrected else "raw",
        "segments": CORRECTED_TRANSCRIPT if corrected else RAW_TRANSCRIPT,
    }
    assert article["item"]["local_video_url"] == "/api/articles/dy-123/video"
    assert str(config.vault_path) not in str(article)
    assert "metadata" not in article


def test_local_video_supports_playback_range_and_head(tmp_path):
    config, service, video = video_fixture(tmp_path)
    with local_client(create_app(config, service=service, start_watcher=False)) as client:
        complete = client.get("/api/articles/dy-123/video")
        partial = client.get("/api/articles/dy-123/video", headers={"Range": "bytes=20-49"})
        head = client.head("/api/articles/dy-123/video")
    assert complete.status_code == 200
    assert complete.headers["content-type"] == "video/mp4"
    assert complete.content == video.read_bytes()
    assert complete.headers["accept-ranges"] == "bytes"
    assert partial.status_code == 206
    assert partial.headers["content-range"] == "bytes 20-49/1024"
    assert partial.content == video.read_bytes()[20:50]
    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["content-length"] == "1024"


@pytest.mark.parametrize(
    "case", ["removed", "missing", "outside", "other_entry", "symlink", "non_video", "image_note"]
)
def test_video_access_is_limited_to_present_video_owned_by_entry(tmp_path, case):
    config, service, video = video_fixture(tmp_path)
    entry = service.database.get_entry("dy-123")
    data = service.database.get_entry_data(entry.id)
    if case == "removed":
        entry = entry.model_copy(update={"media_status": "removed"})
    elif case == "missing":
        video.unlink()
    elif case in {"outside", "other_entry", "symlink"}:
        target = (
            tmp_path / "private.mp4"
            if case != "other_entry"
            else config.vault_path / "raw/assets/456/private.mp4"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"not this entry's media")
        if case == "symlink":
            video.unlink()
            video.symlink_to(target)
        else:
            data["metadata"]["media_path"] = str(target)
    elif case == "non_video":
        data["metadata"]["media_path"] = str(video.with_name("cover.webp"))
    else:
        data["metadata"]["source_kind"] = "image_note"
        source = config.vault_path / entry.source_path
        source.write_text(
            source.read_text().replace("source_kind: video", "source_kind: image_note")
        )
    service.database.upsert_entry(entry, data)
    with local_client(create_app(config, service=service, start_watcher=False)) as client:
        article = client.get("/api/articles/dy-123").json()
        response = client.get("/api/articles/dy-123/video")
    assert article["item"]["local_video_url"] is None
    assert response.status_code == 404
    if case == "image_note":
        assert article["transcript"] is None
    else:
        assert article["transcript"]["segments"] == CORRECTED_TRANSCRIPT


def test_creator_video_and_relative_path_remain_accessible(tmp_path):
    config, service, video = video_fixture(tmp_path)
    entry = service.database.get_entry("dy-123")
    relative = "creators/测试博主/raw/assets/123/本地 视频.mp4"
    target = config.vault_path / relative
    target.parent.mkdir(parents=True)
    video.rename(target)
    data = service.database.get_entry_data(entry.id)
    data["metadata"]["media_path"] = relative
    service.database.upsert_entry(entry, data)
    with service.database.connect() as conn:
        conn.execute(
            "UPDATE entries SET raw_path=? WHERE id=?",
            ("creators/测试博主/raw/records/记录.md", entry.id),
        )
    with local_client(create_app(config, service=service, start_watcher=False)) as client:
        response = client.get("/api/articles/dy-123/video")
    assert response.status_code == 200
    assert response.content == target.read_bytes()


def test_missing_article_and_image_media_route_keep_their_boundaries(tmp_path):
    config, service, _video = video_fixture(tmp_path)
    with local_client(create_app(config, service=service, start_watcher=False)) as client:
        assert client.get("/api/articles/missing/video").status_code == 404
        assert client.get("/media/raw/assets/123/cover.webp").status_code == 200
        assert client.get("/media/raw/assets/123/本地 视频.mp4").status_code == 404
