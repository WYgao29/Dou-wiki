"""Usability fixes: in-app 404, pin limit, duplicate import, clocks, and cancel."""

from __future__ import annotations

from datetime import UTC, datetime

from douyin_wiki.webapp.app import create_app
from douyin_wiki.webapp.catalog import LibraryCatalog
from douyin_wiki.webapp.rendering import render_article
from tests.test_web import _web_fixture, local_client

LONG_ID = "7604970362420645155"
LONG_URL = f"https://www.douyin.com/video/{LONG_ID}"


def _client(tmp_path):
    config, service = _web_fixture(tmp_path)
    app = create_app(config, service=service, start_watcher=False)
    return config, service, app


def test_missing_article_and_topic_pages_render_shell(tmp_path) -> None:
    _config, _service, app = _client(tmp_path)
    with local_client(app) as client:
        for path in ("/articles/does-not-exist", "/topics/does-not-exist", "/jobs/does-not-exist"):
            page = client.get(path)
            assert page.status_code == 404
            assert "text/html" in page.headers["content-type"]
            assert "app-shell" in page.text
            assert "not-found-view" in page.text
            assert "返回资料库" in page.text
            assert "在废纸篓查找" in page.text
            assert 'id="library-view" class="content-view hidden"' in page.text
            assert '{"detail"' not in page.text
        api = client.get("/api/articles/does-not-exist")
        assert api.status_code == 404
        assert api.headers["content-type"].startswith("application/json")
        assert api.json()["detail"] == "文章不存在"
        known = client.get("/articles/dy-123")
        assert known.status_code == 200
        assert "data-missing" not in known.text


def test_ninth_pinned_tag_returns_422(tmp_path) -> None:
    _config, service, app = _client(tmp_path)
    now = datetime.now(UTC).isoformat()
    with service.database.connect() as conn:
        for index in range(8):
            conn.execute(
                "INSERT INTO tags(name, pinned_at, position, created_at) VALUES (?, ?, ?, ?)",
                (f"固定{index}", now, index + 1, now),
            )
        conn.execute("INSERT INTO tags(name, created_at) VALUES (?, ?)", ("第九个", now))
    with local_client(app) as client:
        kept = client.post("/api/tags/pin", json={"name": "固定0", "pinned": True})
        assert kept.status_code == 200
        blocked = client.post("/api/tags/pin", json={"name": "第九个", "pinned": True})
        assert blocked.status_code == 422
        assert "最多固定 8 个" in blocked.json()["detail"]


def test_duplicate_import_does_not_create_job(tmp_path) -> None:
    _config, service, app = _client(tmp_path)
    entry = service.database.get_entry("dy-123").model_copy(
        update={
            "id": "dy-long",
            "video_id": LONG_ID,
            "title": "已在库中",
            "original_url": LONG_URL,
            "canonical_url": LONG_URL,
        }
    )
    service.database.upsert_entry(entry, service.database.get_entry_data("dy-123"))
    with local_client(app) as client:
        before = client.get("/api/jobs").json()["total"]
        response = client.post("/api/captures", json={"share_text": LONG_URL})
        assert response.status_code == 200
        body = response.json()
        assert body["duplicate"] is True
        assert body["in_trash"] is False
        assert body["entry_id"] == "dy-long"
        assert body["href"] == "/articles/dy-long"
        assert "已在资料库" in body["message"]
        assert client.get("/api/jobs").json()["total"] == before


def test_trashed_duplicate_import_does_not_create_job(tmp_path) -> None:
    _config, service, app = _client(tmp_path)
    url = "https://www.douyin.com/video/7604970362420645156"
    with service.database.connect() as conn:
        conn.execute(
            "UPDATE entries SET video_id=?, original_url=?, canonical_url=? WHERE id=?",
            ("7604970362420645156", url, url, "dy-123"),
        )
    service.trash_entry("dy-123", confirmed=True)
    with local_client(app) as client:
        before = client.get("/api/jobs").json()["total"]
        response = client.post("/api/captures", json={"share_text": url})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["duplicate"] is True
        assert body["in_trash"] is True
        assert body["href"] == "/trash"
        assert client.get("/api/jobs").json()["total"] == before


def test_creator_import_rejects_non_creator_links(tmp_path) -> None:
    _config, _service, app = _client(tmp_path)
    with local_client(app) as client:
        foreign = client.post("/api/creators/inventory", json={"source_text": "https://example.com/user/abc"})
        assert foreign.status_code == 400
        video = client.post(
            "/api/creators/inventory",
            json={"source_text": "https://www.douyin.com/video/7604970362420645155"},
        )
        assert video.status_code == 400
        assert "博主" in video.json()["detail"]
        home = client.post(
            "/api/creators/inventory",
            json={"source_text": "https://www.douyin.com/user/MS4wLjABAAAAabcdefgh"},
        )
        assert home.status_code == 202
        short = client.post("/api/creators/inventory", json={"source_text": "https://v.douyin.com/abc/"})
        assert short.status_code == 202


def test_single_import_saves_time_range_and_rejects_bad_clock(tmp_path) -> None:
    _config, _service, app = _client(tmp_path)
    with local_client(app) as client:
        created = client.post(
            "/api/captures",
            json={
                "share_text": "https://www.douyin.com/video/7604970362420645001",
                "inspirations": [
                    {"text": "", "quote": "原句", "start_ms": "1:05", "end_ms": "1:20"}
                ],
            },
        )
        assert created.status_code == 202, created.text
        inspiration = created.json()["inspirations"][0]
        assert inspiration["text"] == ""
        assert inspiration["quote"] == "原句"
        assert inspiration["start_ms"] == 65_000
        assert inspiration["end_ms"] == 80_000
        rejected = client.post(
            "/api/captures",
            json={
                "share_text": "https://www.douyin.com/video/7604970362420645002",
                "inspirations": [
                    {"text": "有灵感", "quote": "原句", "start_ms": "1:99", "end_ms": "2:00"}
                ],
            },
        )
        assert rejected.status_code == 400
        assert "60" in rejected.json()["detail"]


def test_cancel_queued_job(tmp_path) -> None:
    _config, service, app = _client(tmp_path)
    job = service.capture_douyin("https://www.douyin.com/video/7604970362420645999")
    with local_client(app) as client:
        cancelled = client.post(f"/api/jobs/{job.id}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"
        queued = client.get("/api/jobs", params={"status": "queued"}).json()
        assert job.id not in {item["id"] for item in queued["items"]}
        removed = client.delete(f"/api/jobs/{job.id}")
        assert removed.status_code == 200
        assert removed.json()["deleted"] == job.id
        missing = client.get(f"/api/jobs/{job.id}")
        assert missing.status_code == 404
        assert missing.json()["detail"] == "任务不存在"


def test_empty_inspiration_section_is_omitted_from_reader(tmp_path) -> None:
    config, service = _web_fixture(tmp_path)
    catalog = LibraryCatalog(config.vault_path, service.database)
    catalog.refresh()
    markdown = (
        "## 灵感\n\n- 未填写\n\n"
        "> [!note] AI 推断：与灵感的关系\n"
        "> 这段说明不该出现。\n\n"
        "## 一句话\n\n正文还在。\n"
    )
    rendered = render_article(
        markdown,
        source_path="wiki/sources/测试文章_123.md",
        catalog=catalog,
    )
    assert "未填写" not in rendered
    assert "与灵感的关系" not in rendered
    assert "这段说明不该出现" not in rendered
    assert "正文还在" in rendered
