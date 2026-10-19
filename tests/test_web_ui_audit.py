"""Desktop UI audit: retired import and model surfaces, tag undo, job titles."""

from __future__ import annotations

from douyin_wiki.models import CaptureRequest, JobStatus
from douyin_wiki.webapp.app import WEB_VERSION, create_app
from tests.support.web import _web_fixture, local_client
from tests.test_tags import source


def test_shell_hides_retired_model_page_and_offers_agent_commands(tmp_path):
    config, service = _web_fixture(tmp_path)
    app = create_app(config, service=service, start_watcher=False)
    with local_client(app) as client:
        page = client.get("/").text
        assert "去 Agent 里导入" in page
        assert 'data-copy-command="请导入这条抖音，并继续校正和分析：https://v.douyin.com/"' in page
        assert 'data-copy-command="请读取我的抖音收藏，列出新增作品，确认后再导入。"' in page
        assert 'class="destination-row hidden" data-import-kind="collections"' in page
        assert 'id="model-view"' not in page
        assert "共用模型" not in page
        assert 'id="topics-create-button"' in page
        assert "去资料库选择" not in page
        model = client.get("/settings/model")
        assert model.status_code == 200
        assert "<title>页面不存在 · 抖库</title>" in model.text
        assert 'data-missing="1"' in model.text
        script = client.get(f"/static/model-settings.js?v={WEB_VERSION}")
        assert script.status_code == 200
        assert "showMissing" in script.text
        assert f"/static/model-settings.js?v={WEB_VERSION}" not in model.text
        assets = {
            name: client.get(f"/static/{name}?v={WEB_VERSION}").text
            for name in ("app.js", "jobs.js", "auth.js", "tags.js", "shared.js", "app.css")
        }
    assert 'title: "重新交给 Agent 分析这篇？"' in assets["app.js"]
    assert 'tone: "normal"' in assets["app.js"]
    assert "会调用模型" not in assets["app.js"]
    assert "消耗 Token" not in assets["app.js"]
    assert "link.title = heading.textContent.trim()" in assets["app.js"]
    assert "去资料库选择" not in assets["app.js"]
    assert "打开《${title}》" in assets["jobs.js"]
    assert "子任务在下方" in assets["jobs.js"]
    assert "has-warning" in assets["jobs.js"]
    assert "外部 Agent" in assets["jobs.js"]
    assert "复制诊断信息" in assets["auth.js"]
    assert "Playwright" not in assets["auth.js"]
    assert "yt-dlp" not in assets["auth.js"]
    assert "undoableByAlias" in assets["tags.js"]
    assert "会影响 ${count} 篇文章" in assets["tags.js"]
    assert 'action === "merge" || action === "delete"' in assets["tags.js"]
    assert 'tone === "normal"' in assets["shared.js"]
    assert ".search-box:has(:focus-visible)" in assets["app.css"]
    assert ".theme-control select:focus-visible" in assets["app.css"]
    assert "white-space: nowrap" in assets["app.css"]
    assert ".tag-duplicate-list" in assets["app.css"]


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
