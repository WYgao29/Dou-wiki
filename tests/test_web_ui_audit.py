"""Desktop UI audit: retired import and model surfaces, tag undo, job titles."""

from __future__ import annotations

from douyin_wiki.models import CaptureRequest, JobStatus
from douyin_wiki.tags import snapshot_still_undoable
from douyin_wiki.webapp.app import create_app
from tests.support.web import _web_fixture, local_client
from tests.test_tags import source


def test_shell_hides_retired_model_page_and_offers_agent_commands(tmp_path):
    config, service = _web_fixture(tmp_path)
    app = create_app(config, service=service, start_watcher=False)
    with local_client(app) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "去 Agent 里导入" in page.text
        assert (
            'data-copy-command="请导入这条抖音，并继续校正和分析：https://v.douyin.com/"'
            in page.text
        )
        assert 'data-copy-command="请读取我的抖音收藏，列出新增作品，确认后再导入。"' in page.text
        assert 'class="destination-row hidden" data-import-kind="collections"' in page.text
        assert 'id="model-view"' not in page.text
        assert "共用模型" not in page.text
        assert 'id="topics-create-button"' in page.text
        assert "去资料库选择" not in page.text
        model = client.get("/settings/model")
        assert model.status_code == 404
        assert "<title>页面不存在 · 抖库</title>" in model.text
        assert 'data-missing="1"' in model.text
        assert 'id="not-found-view"' in model.text
        assert "model-settings.js" not in model.text


def test_child_warning_preview_uses_article_title(tmp_path):
    config, service = _web_fixture(tmp_path)
    child = service.database.create_job(
        CaptureRequest(share_text="🔥抖音原文案\nhttps://www.douyin.com/video/1"),
        status=JobStatus.COMPLETED_WITH_WARNINGS,
        progress=1,
        artifacts={"metadata": {"title": "🔥抖音原文案"}},
    )
    service.database.update_job(
        child.id,
        result={"warnings": ["字幕有缺口"], "entry_id": "dy-123"},
        unlock=True,
    )
    parent = service.database.create_job(
        CaptureRequest(share_text="https://www.douyin.com/user/batch"),
        kind="creator_import",
        status=JobStatus.COMPLETED_WITH_WARNINGS,
        progress=1,
    )
    service.database.update_job(
        parent.id,
        result={"warnings": [], "child_job_ids": [child.id], "creator_name": "批次博主"},
        unlock=True,
    )
    app = create_app(config, service=service, start_watcher=False)
    with local_client(app) as client:
        listed = next(
            item for item in client.get("/api/jobs").json()["items"] if item["id"] == parent.id
        )
        detail = client.get(f"/api/jobs/{parent.id}").json()
    assert listed["state_label"] == "完成 · 有提示"
    assert "字幕有缺口" in listed["result"]["warnings"]
    assert listed["result"]["warnings_from_children"] is True
    child_row = next(item for item in detail["children"] if item["id"] == child.id)
    assert child_row["display_title"] == "单条采集 · 测试文章"
    assert "🔥" not in child_row["display_title"]
    assert detail["imported_entries"] == [{"entry_id": "dy-123", "title": "测试文章"}]


def test_job_detail_loads_only_the_first_five_imported_titles(tmp_path):
    config, service = _web_fixture(tmp_path)
    entry_ids = []
    children = []
    for offset in range(6):
        work_id = str(3000 + offset)
        source(service, work_id, ["咖啡"], managed=True)
        entry_ids.append(f"dy-{work_id}")
        child = service.database.create_job(
            CaptureRequest(share_text=f"https://www.douyin.com/video/{work_id}"),
            status=JobStatus.COMPLETED,
            progress=1,
        )
        service.database.update_job(child.id, result={"entry_id": f"dy-{work_id}"}, unlock=True)
        children.append(child.id)
    parent = service.database.create_job(
        CaptureRequest(share_text="https://www.douyin.com/user/batch"),
        kind="creator_import",
        status=JobStatus.COMPLETED,
        progress=1,
    )
    service.database.update_job(
        parent.id, result={"child_job_ids": children, "creator_name": "批次博主"}, unlock=True
    )
    app = create_app(config, service=service, start_watcher=False)
    with local_client(app) as client:
        detail = client.get(f"/api/jobs/{parent.id}").json()
    assert [item["entry_id"] for item in detail["imported_entries"]] == entry_ids[:5]
    assert entry_ids[5] in detail["imported_entry_ids"]
    assert entry_ids[5] not in {item["entry_id"] for item in detail["imported_entries"]}


def test_library_does_not_check_undo_and_tags_reuse_the_result(tmp_path, monkeypatch):
    config, service = _web_fixture(tmp_path)
    calls = {"n": 0}

    def wrapped(*args, **kwargs):
        calls["n"] += 1
        return snapshot_still_undoable(*args, **kwargs)

    monkeypatch.setattr("douyin_wiki.tags.snapshot_still_undoable", wrapped)
    app = create_app(config, service=service, start_watcher=False)
    with local_client(app) as client:
        assert client.get("/api/library").status_code == 200
        assert client.get("/api/library").status_code == 200
        assert calls["n"] == 0
        renamed = client.post("/api/tags/rename", json={"name": "咖啡", "target": "饮品"})
        assert renamed.status_code == 200, renamed.text
        assert calls["n"] >= 1
        seen = calls["n"]
        fresh = client.get("/api/tags").json()
        operation = renamed.json()["operation_id"]
        assert fresh["undoable_by_alias"]["咖啡"] == operation
        assert calls["n"] == seen
        assert client.get("/api/tags").status_code == 200
        assert calls["n"] == seen


def test_tag_merge_reports_articles_and_hides_stale_undo(tmp_path):
    config, service = _web_fixture(tmp_path)
    source(service, "1000", ["AI Agent"], managed=True)
    source(service, "1001", ["AI 代理"], managed=True)
    source(service, "1002", ["AI Agent", "AI 代理"], managed=True)
    path = config.vault_path / service.database.get_entry("dy-123").source_path
    app = create_app(config, service=service, start_watcher=False)
    with local_client(app) as client:
        overview = client.get("/api/tags").json()
        group = next(item for item in overview["duplicates"] if "AI Agent" in item["names"])
        assert "AI 代理" in group["names"]
        assert len(group["names"]) <= 5
        assert group["article_count"] == 3
        agent = next(row for row in overview["tags"] if row["name"] == "AI Agent")
        assert set(agent["entry_ids"]) == {"dy-1000", "dy-1002"}
        renamed = client.post("/api/tags/rename", json={"name": "咖啡", "target": "饮品"})
        assert renamed.status_code == 200
        fresh = client.get("/api/tags").json()
        operation = renamed.json()["operation_id"]
        assert fresh["undo_by_alias"]["咖啡"] == operation
        assert fresh["undoable_by_alias"]["咖啡"] == operation
        path.write_text(path.read_text() + "\n后续编辑\n")
        stale = client.get("/api/tags").json()
        assert stale["undo_by_alias"]["咖啡"] == operation
        assert "咖啡" not in stale["undoable_by_alias"]
        undo = client.post("/api/tags/undo", json={"operation_id": operation})
        assert undo.status_code == 409
