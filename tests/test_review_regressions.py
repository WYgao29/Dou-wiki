"""Regression checks for lock contention, search relevance and tag preservation."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event, Thread
from time import monotonic, sleep

import pytest

from douyin_wiki.models import JobStatus
from douyin_wiki.webapp.app import create_app
from douyin_wiki.webapp.catalog import split_frontmatter
from tests.support.web import _web_fixture, local_client
from tests.test_favorites import A, scan, snapshot
from tests.test_tags import source


def test_pages_and_library_do_not_write_tag_vocabulary(tmp_path):
    config, service = _web_fixture(tmp_path)
    app = create_app(config, service=service, start_watcher=False)
    with service.database.connect() as conn:
        before = [tuple(row) for row in conn.execute("SELECT * FROM tags ORDER BY name")]
    with local_client(app) as client:
        assert "咖啡" in client.get("/").text
        assert client.get("/api/library").json()["facets"]["sidebar_tags"][0]["name"] == "咖啡"
        with service.database.connect() as conn:
            assert [
                tuple(row) for row in conn.execute("SELECT * FROM tags ORDER BY name")
            ] == before


def test_worker_lock_does_not_block_pages_health_or_library_with_watcher(tmp_path):
    config, service = _web_fixture(tmp_path)
    app = create_app(config, service=service, start_watcher=True)
    acquired, release = Event(), Event()

    def worker_write():
        with service.vault.entry_operations_locked():
            acquired.set()
            path = config.vault_path / service.database.get_entry("dy-123").source_path
            path.write_text(path.read_text().replace("# 测试文章", "# Worker pending title"))
            assert release.wait(5)

    with local_client(app) as client:
        worker = Thread(target=worker_write)
        worker.start()
        try:
            assert acquired.wait(1)
            # Allow watchfiles to deliver the pending publication while its lock is held.
            sleep(0.7)
            start = monotonic()
            with ThreadPoolExecutor(max_workers=3) as pool:
                requests = [
                    pool.submit(client.get, path)
                    for path in ("/", "/api/system/health", "/api/library")
                ]
                responses = [future.result(timeout=1) for future in requests]
            assert monotonic() - start < 1
            assert all(response.status_code == 200 for response in responses)
            assert responses[2].json()["items"][0]["title"] == "测试文章"
        finally:
            release.set()
            worker.join(5)
        deadline = monotonic() + 2
        while client.get("/api/library").json()["items"][0]["title"] != "Worker pending title":
            assert monotonic() < deadline, "后台目录未在解锁后发布新快照"
            sleep(0.02)


def test_fuzzy_search_ranks_title_and_excludes_body_subsequences(tmp_path):
    config, service = _web_fixture(tmp_path)
    catalog = create_app(config, service=service, start_watcher=False).state.catalog
    base = catalog.list_items()[0]
    now = base.captured_at
    specifications = [
        ("new", "普通文章", "作者", [], "project example video", 4),
        ("fuzzy", "Java enterprise version", "作者", [], "", 3),
        ("body", "正文提及", "作者", [], "Jev is a tool", 2),
        ("tag", "带标签", "作者", ["Jev"], "", 1),
        ("exact", "Jev 教程", "作者", [], "", 0),
        ("cross-field", "Java", "every", ["video"], "", 5),
    ]
    catalog._items = {
        entry_id: base.model_copy(
            update={
                "entry_id": entry_id,
                "title": title,
                "author": author,
                "tags": tags,
                "body_markdown": body,
                "summary": "",
                "inspirations": [],
                "captured_at": now + timedelta(seconds=offset),
            }
        )
        for entry_id, title, author, tags, body, offset in specifications
    }
    assert [item.entry_id for item in catalog.filter(query="jev", fuzzy=True)] == [
        "exact",
        "tag",
        "body",
        "fuzzy",
    ]
    assert [item.entry_id for item in catalog.filter(query="jev")] == ["body", "tag", "exact"]


def test_cleanup_requires_unchanged_preview_and_keeps_new_unused_tags(tmp_path):
    config, service = _web_fixture(tmp_path)
    with local_client(create_app(config, service=service, start_watcher=False)) as client:
        source(service, "1000", ["预览中的标签"])
        client.get("/api/tags")
        (config.vault_path / "wiki/sources/标签资料_1000.md").unlink()
        preview = client.post("/api/tags/cleanup", json={"confirmed": False}).json()
        assert preview["names"] == ["预览中的标签"]
        with service.database.connect() as conn:
            conn.execute("INSERT INTO tags(name,created_at) VALUES ('后来新增', '2026-01-01')")
        missing = client.post("/api/tags/cleanup", json={"confirmed": True})
        assert missing.status_code == 422
        stale = client.post(
            "/api/tags/cleanup", json={"confirmed": True, "names": preview["names"]}
        )
        assert stale.status_code == 409
        assert {"预览中的标签", "后来新增"}.issubset(service.tags.standard_names())
        fresh = client.post("/api/tags/cleanup", json={"confirmed": False}).json()
        done = client.post("/api/tags/cleanup", json={"confirmed": True, "names": fresh["names"]})
        assert done.status_code == 200
        assert set(done.json()["deleted"]) == set(fresh["names"])


@pytest.mark.parametrize(
    "value",
    [
        "[\"咖啡\", '其他']",
        "\n  - \"咖啡\" # first\n  - '其他'",
        "\n- \"咖啡\" # first\n- '其他'",
        "*original",
        "&shared [\"咖啡\", '其他']",
    ],
)
def test_tag_rename_preserves_other_frontmatter_and_undo_bytes(tmp_path, value):
    config, service = _web_fixture(tmp_path)
    path = source(service, "1000", ["咖啡", "其他"])
    prefix = (
        "---\n# handwritten comment\ntype: \"source\"\nvideo_id: '1000'\n"
        "original: &original [\"咖啡\", '其他']\ntags: "
    )
    suffix = (
        " # tag comment\n# keep this comment\ntitle: 'Quoted: title'\n"
        "description: |\n  Keep this text.\n---\n# 原文\n\n正文不改。\n"
    )
    before = prefix + value + suffix
    path.write_text(before)
    with local_client(create_app(config, service=service, start_watcher=False)) as client:
        result = client.post("/api/tags/rename", json={"name": "咖啡", "target": "茶"})
        assert result.status_code == 200, result.text
        changed = path.read_text()
        assert changed.startswith(prefix)
        assert changed.endswith(suffix)
        assert split_frontmatter(changed)[0]["tags"] == ["茶", "其他"]
        assert split_frontmatter(changed)[0]["original"] == ["咖啡", "其他"]
        undone = client.post("/api/tags/undo", json={"operation_id": result.json()["operation_id"]})
        assert undone.status_code == 200, undone.text
        assert path.read_text() == before


async def test_cancelled_favorites_parent_keeps_fresh_stats_without_changing_status(service):
    parent = await scan(service, snapshot(kinds=[(A, "video")]))
    service.favorites.confirm(parent.id)
    child = service.favorites.store.rows(parent.id)[0]["child_id"]
    cancelled = service.cancel_job(parent.id)
    assert cancelled.status == JobStatus.CANCELLED
    assert cancelled.result["child_status_counts"] == {"cancelled": 1}
    service.delete_job(child)
    persisted = service.database.get_job(parent.id)
    assert persisted.status == JobStatus.CANCELLED
    assert persisted.result["summary"]["failed"] == 1
    assert persisted.result["child_status_counts"] == {"cancelled": 1}
