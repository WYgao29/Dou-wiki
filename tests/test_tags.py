from __future__ import annotations

import re

import pytest
import yaml

from douyin_wiki.models import CaptureRequest, JobStatus
from douyin_wiki.tags import duplicate_groups
from douyin_wiki.webapp.app import create_app
from douyin_wiki.webapp.catalog import split_frontmatter
from tests.support.web import _web_fixture, local_client
from tests.test_web_shell import ShellMarkup


def source(service, work_id, tags, *, managed=False):
    path = service.config.vault_path / "wiki/sources" / f"标签资料_{work_id}.md"
    path.write_text(
        "---\n"
        + yaml.safe_dump(
            {
                "type": "source",
                "video_id": work_id,
                "title": f"资料 {work_id}",
                "tags": tags,
                "captured": "2026-09-30T09:00:00+08:00",
            },
            allow_unicode=True,
        )
        + "---\n# 资料\n\n原文保留。\n"
    )
    if managed:
        entry = service.database.get_entry("dy-123").model_copy(
            update={
                "id": f"dy-{work_id}",
                "video_id": work_id,
                "tags": tags,
                "source_path": str(path.relative_to(service.config.vault_path)),
            }
        )
        data = service.database.get_entry_data("dy-123")
        data["analysis"]["tags"] = tags
        service.database.upsert_entry(entry, data)
        service.indexer.index_entry(entry, data)
    return path


def test_sidebar_top_eight_counts_and_zero_hidden(tmp_path):
    config, service = _web_fixture(tmp_path)
    for i in range(12):
        source(service, str(1000 + i), [f"标签 {i}", "高频"])
    app = create_app(config, service=service, start_watcher=False)
    with local_client(app) as client:
        data = client.get("/api/tags").json()
        assert len(data["sidebar"]) == 8
        assert data["sidebar"][0]["name"] == "高频"
        assert data["sidebar"][0]["count"] == 12
        assert len(data["tags"][0]["recent"]) == 5
        html = client.get("/").text
        markup = ShellMarkup(html)
        rows = [
            attrs
            for _, attrs, parents in markup.elements
            if ("div", "sidebar-tags") in parents and attrs.get("class") == "sidebar-tag"
        ]
        assert len(rows) == 8
        sidebar = re.search(r'id="sidebar-tags".*?</div>', html, re.S).group()
        assert re.findall(r'class="nav-count">(.*?)</span>', sidebar)[0] == "12"
        assert 'class="tag-hash" aria-hidden="true">#</span>' in sidebar
        assert markup.element("all-tags-button")[1]["href"] == "/tags"
        # Retain a vocabulary entry even when its last source is removed externally.
        (config.vault_path / "wiki/sources/标签资料_1000.md").unlink()
        data = client.get("/api/tags").json()
        assert next(row for row in data["tags"] if row["name"] == "标签 0")["count"] == 0
        assert "标签 0" not in {row["name"] for row in data["sidebar"]}
        assert next(row for row in data["tags"] if row["name"] == "标签 0")["last_used_at"]
        client.post("/api/tags/pin", json={"name": "标签 0"})
        assert client.get("/api/tags").json()["sidebar"] == []


def test_pins_override_frequency_and_order_persists(tmp_path):
    config, service = _web_fixture(tmp_path)
    source(service, "1000", ["低频", "另一个"])
    app = create_app(config, service=service, start_watcher=False)
    with local_client(app) as client:
        client.get("/api/tags")
        for name in ["另一个", "低频"]:
            assert client.post("/api/tags/pin", json={"name": name}).status_code == 200
        data = client.get("/api/tags").json()
        assert [row["name"] for row in data["sidebar"]] == ["另一个", "低频"]
        assert (
            client.post("/api/tags/reorder", json={"names": ["低频", "另一个"]}).status_code == 200
        )
        assert [row["name"] for row in client.get("/api/tags").json()["sidebar"]] == [
            "低频",
            "另一个",
        ]
        assert client.post("/api/tags/reorder", json={"names": ["低频"]}).status_code == 422
    with local_client(create_app(config, service=service, start_watcher=False)) as client:
        assert [row["name"] for row in client.get("/api/tags").json()["sidebar"]] == [
            "低频",
            "另一个",
        ]


def test_merge_updates_sources_database_index_and_ai_alias(tmp_path):
    config, service = _web_fixture(tmp_path)
    managed = source(service, "1000", ["AI Agent", "AI 代理", "其他"], managed=True)
    readonly = source(service, "1001", ["AI Agent"])
    app = create_app(config, service=service, start_watcher=False)
    with local_client(app) as client:
        response = client.post(
            "/api/tags/merge",
            json={
                "names": ["AI Agent", "AI 代理"],
                "target": "AI 代理",
            },
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert "AI Agent" not in {row["name"] for row in data["tags"]}
        assert data["aliases"]["AI Agent"] == "AI 代理"
        assert service.database.get_entry("dy-1000").tags == ["AI 代理", "其他"]
        assert service.database.get_entry_data("dy-1000")["analysis"]["tags"] == ["AI 代理", "其他"]
        assert split_frontmatter(managed.read_text())[0]["tags"] == ["AI 代理", "其他"]
        assert split_frontmatter(readonly.read_text())[0]["tags"] == ["AI 代理"]
        assert split_frontmatter(readonly.read_text())[1] == "# 资料\n\n原文保留。\n"
        with service.database.connect() as conn:
            assert {
                row[0]
                for row in conn.execute(
                    "SELECT tags_text FROM chunks WHERE entry_id=?", ("dy-1000",)
                )
            } == {"AI 代理 其他"}
        assert service.tags.resolve(["ai agent", "AI Agent", "AI 代理"]) == ["AI 代理"]
        analysis = service.database.get_entry_data("dy-123")["analysis"]
        analysis["tags"] = ["AI Agent", "AI 代理"]
        updated = service.submit_analysis("dy-123", analysis, producer="test")
        assert updated.tags == ["AI 代理"]
        assert "AI Agent" not in service.tags.standard_names()
        assert client.get("/api/library?tag=AI%20Agent").json()["total"] == 3


def test_rename_flattens_alias_and_conflicts_do_not_change_data(tmp_path):
    config, service = _web_fixture(tmp_path)
    path = source(service, "1000", ["AI Agent", "AI 代理", "其他"])
    with local_client(create_app(config, service=service, start_watcher=False)) as client:
        client.post("/api/tags/merge", json={"names": ["AI Agent", "AI 代理"], "target": "AI 代理"})
        metadata, body = split_frontmatter(path.read_text())
        metadata["tags"] = ["ai agent", "其他"]
        path.write_text("---\n" + yaml.safe_dump(metadata, allow_unicode=True) + "---\n" + body)
        result = client.post("/api/tags/rename", json={"name": "AI 代理", "target": "智能体"})
        assert split_frontmatter(path.read_text())[0]["tags"] == ["智能体", "其他"]
        assert client.get("/api/library?tag=智能体").json()["total"] == 1
        assert result.status_code == 200, result.text
        assert service.tags.resolve(["AI Agent", "AI 代理"]) == ["智能体"]
        conflict = client.post("/api/tags/rename", json={"name": "智能体", "target": "咖啡"})
        assert conflict.status_code == 422
        assert service.tags.resolve(["AI Agent"]) == ["智能体"]
        assert (
            client.post("/api/tags/rename", json={"name": "智能体", "target": " \n "}).status_code
            == 422
        )
        assert (
            client.post(
                "/api/tags/merge", json={"names": ["咖啡", "未知"], "target": "咖啡"}
            ).status_code
            == 404
        )
        # A destination can be an existing tag outside the selected source set.
        response = client.post(
            "/api/tags/merge",
            json={
                "names": ["智能体", "其他"],
                "target": "咖啡",
            },
        )
        assert response.status_code == 200, response.text
        assert service.tags.resolve(["AI Agent", "AI 代理", "智能体", "其他"]) == ["咖啡"]


def test_delete_only_unlinks_tags(tmp_path):
    config, service = _web_fixture(tmp_path)
    path = source(service, "1000", ["删除我", "保留"], managed=True)
    before = path.read_text().split("---\n", 2)[-1]
    with local_client(create_app(config, service=service, start_watcher=False)) as client:
        result = client.post("/api/tags/delete", json={"names": ["删除我"]})
        assert result.status_code == 200, result.text
        assert service.database.get_entry("dy-1000").tags == ["保留"]
        assert path.exists() and path.read_text().split("---\n", 2)[-1] == before
        assert client.get("/api/articles/dy-1000").status_code == 200
        assert "删除我" not in {row["name"] for row in client.get("/api/tags").json()["tags"]}


def test_multi_file_failure_rolls_back_files_registry_and_entry(tmp_path, monkeypatch):
    config, service = _web_fixture(tmp_path)
    paths = [source(service, "1000", ["源", "目标"], managed=True), source(service, "1001", ["源"])]
    with local_client(create_app(config, service=service, start_watcher=False)) as client:
        client.get("/api/tags")
    previous = {path: path.read_bytes() for path in paths}
    original = service.vault._atomic_write
    calls = 0

    def fail_once(path, content):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated publication failure")
        return original(path, content)

    monkeypatch.setattr(service.vault, "_atomic_write", fail_once)
    with pytest.raises(OSError):
        service.tags.merge(["源", "目标"], "目标")
    assert {path: path.read_bytes() for path in paths} == previous
    assert service.database.get_entry("dy-1000").tags == ["源", "目标"]
    assert service.tags.resolve(["源"]) == ["源"]
    assert "源" in service.tags.standard_names()


def test_tags_page_and_gateway_vocabulary(tmp_path):
    config, service = _web_fixture(tmp_path)
    with local_client(create_app(config, service=service, start_watcher=False)) as client:
        response = client.get("/tags")
        assert response.status_code == 200
        markup = ShellMarkup(response.text)
        assert ("main", "main-content") in markup.element("tags-view")[2]
        assert markup.element("app-shell")[1]["data-page"] == "tags"
        assert 'class="tag-table"' in response.text
        assert 'id="reading-fonts"' not in response.text
        assert client.get("/api/library?tag=%20").status_code == 200
        job = service.database.create_job(
            CaptureRequest(share_text="https://www.douyin.com/video/123")
        )
        service.database.update_job(job.id, status=JobStatus.AWAITING_AGENT_ANALYSIS)
        context = service.get_analysis_context(job.id)
        assert "咖啡" in context["existing_tags"]
        assert any("existing_tags" in rule for rule in context["rules"])


def test_duplicate_rules_are_candidates_only():
    rows = [
        {"name": name, "count": 1}
        for name in ["AI Agent", "aiagent", "AI 代理", "AI 代理应用", "咖啡"]
    ]
    assert duplicate_groups(rows) == [["AI Agent", "aiagent", "AI 代理", "AI 代理应用"]]


def test_merge_preserves_pin_and_manual_source_tags(tmp_path):
    config, service = _web_fixture(tmp_path)
    path = source(service, "1000", ["源", "目标"], managed=True)
    metadata, body = split_frontmatter(path.read_text())
    metadata["tags"].append("手工标签")
    path.write_text("---\n" + yaml.safe_dump(metadata, allow_unicode=True) + "---\n" + body)
    with local_client(create_app(config, service=service, start_watcher=False)) as client:
        client.post("/api/tags/pin", json={"name": "源"})
        response = client.post("/api/tags/merge", json={"names": ["源", "目标"], "target": "目标"})
        assert response.status_code == 200, response.text
        assert [row["name"] for row in response.json()["sidebar"]] == ["目标"]
        assert service.database.get_entry("dy-1000").tags == ["目标", "手工标签"]


def test_ai_publication_resolves_alias_at_final_write(tmp_path):
    config, service = _web_fixture(tmp_path)
    source(service, "1000", ["源", "目标"], managed=True)
    entry = service.database.get_entry("dy-1000")
    data = service.database.get_entry_data(entry.id)
    chunks, relations, reminders = service.prepare_entry_bundle(entry, data)
    with local_client(create_app(config, service=service, start_watcher=False)) as client:
        client.post("/api/tags/merge", json={"names": ["源", "目标"], "target": "目标"})
    # The prepared AI result predates the user's merge.
    saved = service.persist_entry_documents_and_bundle(
        entry,
        data,
        chunks,
        relations,
        reminders,
        action="test",
        log_summary="test",
        commit_message="test",
    )
    assert saved.tags == ["目标"]
    assert service.database.get_entry_data(entry.id)["analysis"]["tags"] == ["目标"]
    with service.database.connect() as conn:
        assert {
            row[0]
            for row in conn.execute("SELECT tags_text FROM chunks WHERE entry_id=?", (entry.id,))
        } == {"目标"}
