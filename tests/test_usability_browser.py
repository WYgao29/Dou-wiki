"""Offline Chromium checks against the real shell, JavaScript and local API."""

from __future__ import annotations

import asyncio
import json
from urllib.parse import urlsplit

import pytest
from playwright.async_api import async_playwright, expect

from douyin_wiki import mcp_server
from douyin_wiki.models import CaptureRequest, InspirationInput, JobStatus
from douyin_wiki.webapp.app import create_app
from tests.support.web import _web_fixture, local_client
from tests.test_favorites import A, FakeFavoritesAdapter, scan, snapshot
from tests.test_tags import source


@pytest.fixture
async def ui(tmp_path):
    config, service = _web_fixture(tmp_path)
    source(service, "1000", ["AI 代理"], managed=True)
    source(service, "1001", ["AI Agent"], managed=True)
    app = create_app(config, service=service, start_watcher=False)
    with local_client(app) as client:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            page = await browser.new_page(viewport={"width": 1440, "height": 1000})
            page.set_default_timeout(5000)
            errors, calls = [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.add_init_script("""window.EventSource = class extends EventTarget {
                constructor(url) { super();
                    if (url === '/api/library/events') window.libraryEvents = this; }
                close() {}
            };""")

            async def route_handler(route):
                request = route.request
                url = urlsplit(request.url)
                assert url.hostname == "testserver", request.url
                calls.append((request.method, url.path))
                response = await asyncio.to_thread(
                    client.request,
                    request.method,
                    url.path + (f"?{url.query}" if url.query else ""),
                    content=request.post_data,
                    headers={
                        key: value
                        for key, value in request.headers.items()
                        if key in {"content-type", "x-douku-client"}
                    },
                )
                await route.fulfill(
                    status=response.status_code,
                    body=response.content,
                    headers={"content-type": response.headers.get("content-type", "")},
                )

            await page.route("**/*", route_handler)
            await page.goto("http://testserver/")
            await page.wait_for_selector("#library-results [data-entry-id]")
            await page.wait_for_function("Boolean(window.libraryEvents)")
            try:
                yield page, service, client, calls
                assert not errors, errors
            finally:
                await browser.close()


async def test_command_search_closes_with_one_escape_and_keeps_query(ui):
    page, _service, _client, _calls = ui
    await page.keyboard.press("Meta+k")
    await page.locator("#command-input").fill("jev")
    await page.keyboard.press("Escape")
    assert not await page.locator("#command-dialog").evaluate("dialog => dialog.open")
    assert await page.locator("#command-input").input_value() == "jev"


async def test_existing_mcp_batch_remains_manageable_after_web_creation_is_removed(ui, monkeypatch):
    page, service, _client, calls = ui
    service.favorites.adapter = FakeFavoritesAdapter(snapshot(kinds=[(A, "video")]))
    monkeypatch.setattr(mcp_server, "_SERVICE", service)
    result = await mcp_server.mcp.call_tool("scan_favorites", {})
    parent_id = json.loads(result.content[0].text)["job_id"]
    await service.process_claimed_job(service.database.get_job(parent_id))
    await page.goto(f"http://testserver/imports/favorites?job={parent_id}")
    await expect(page.locator("#favorites-scan")).to_have_count(0)
    await page.locator("#favorites-items .favorites-item").wait_for()
    await page.locator("#favorites-select-page").click()
    await expect(page.locator("#favorites-selected-count")).to_contain_text("1")
    await page.locator("#favorites-confirm").click()
    await page.locator("#favorites-confirm-submit").click()
    await expect(page).to_have_url(f"http://testserver/jobs/{parent_id}")
    assert service.favorites.get(parent_id)["confirmed"] is True
    assert len([job for job in service.database.list_jobs() if job.kind == "capture"]) == 1
    assert ("POST", "/api/favorites/imports") not in calls
    assert ("POST", f"/api/favorites/imports/{parent_id}/selection") in calls
    assert ("POST", f"/api/favorites/imports/{parent_id}/confirm") in calls


async def test_timestamped_inspiration_remains_readable_without_chat(ui):
    page, service, _client, _calls = ui
    service.add_inspiration(
        "dy-123", InspirationInput(text="保留时间定位", start_ms=1000, end_ms=2000)
    )
    await page.goto("http://testserver/articles/dy-123")
    await page.locator("#panel-tab-inspiration").click()
    await expect(page.locator("#inspiration-pane")).to_contain_text("保留时间定位")
    await expect(page.locator("#inspiration-pane")).to_contain_text("00:01")


@pytest.mark.parametrize("width", [1440])
async def test_video_detail_has_collapsed_complete_transcript_and_copyable_video_url(ui, width):
    page, service, _client, _calls = ui
    video = service.config.vault_path / "raw/assets/123/本地 视频.mp4"
    video.write_bytes(b"local video")
    entry = service.database.get_entry("dy-123")
    data = service.database.get_entry_data(entry.id)
    data["metadata"]["media_path"] = str(video)
    rows = [
        {"id": i, "start_ms": i * 2000, "end_ms": (i + 1) * 2000, "text": f"第 {i} 段全文"}
        for i in range(31)
    ]
    rows[0]["text"] = '<img src=x onerror="window.transcriptExecuted=true">校正后的原话'
    data["transcript_raw"] = [{**rows[0], "text": "旧识别文字"}]
    data["transcript_corrected"] = rows
    service.database.upsert_entry(entry, data)
    await page.set_viewport_size({"width": width, "height": 900})
    await page.goto("http://testserver/articles/dy-123")
    folded = page.locator(".article-transcript")
    await expect(folded.locator("summary")).to_contain_text("校正稿 · 31 段")
    assert not await folded.evaluate("node => node.open")
    await expect(folded.locator(".transcript-text").first).not_to_be_visible()
    await folded.locator("summary").click()
    await expect(folded.locator(".transcript-segment")).to_have_count(31)
    await expect(folded.locator(".transcript-text").last).to_have_text("第 30 段全文")
    await expect(folded.locator("img")).to_have_count(0)
    assert not await page.evaluate("Boolean(window.transcriptExecuted)")
    await expect(page.locator(".reader-local-video")).to_have_attribute(
        "href", "/api/articles/dy-123/video"
    )
    await page.keyboard.press("Shift+i")
    await expect(page.locator(".local-video-url a")).to_have_text(
        "http://testserver/api/articles/dy-123/video"
    )
    await page.evaluate("""Object.defineProperty(navigator, 'clipboard', {
        configurable: true, value: {writeText: async text => {window.copiedVideoUrl = text}}
    })""")
    await page.locator(".copy-video-url").click()
    await page.wait_for_function(
        "window.copiedVideoUrl === 'http://testserver/api/articles/dy-123/video'"
    )
    assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


async def test_video_detail_handles_missing_transcript_and_video(ui):
    page, _service, _client, _calls = ui
    await page.goto("http://testserver/articles/dy-123")
    folded = page.locator(".article-transcript")
    await folded.locator("summary").click()
    await expect(folded).to_contain_text("暂无可用逐字稿")
    await expect(page.locator(".reader-local-video")).to_have_count(0)
    await page.keyboard.press("Shift+i")
    await expect(page.locator("#info-pane")).to_contain_text("文件不存在或已清理")
    await expect(page.locator(".copy-video-url")).to_have_count(0)
    await expect(page.locator("#chat-panel")).to_have_count(0)


@pytest.mark.parametrize("width", [1440])
@pytest.mark.parametrize("theme", ["light", "dark"])
async def test_retired_links_and_preserved_right_panel(ui, width, theme):
    page, service, _client, calls = ui
    await page.set_viewport_size({"width": width, "height": 1000})
    await page.evaluate("theme => window.DoukuTheme.set(theme)", theme)
    for path, view, text in (
        ("/imports/single", "imports-single-view", "去 Agent 里导入"),
        ("/settings/model", "not-found-view", "页面不存在"),
    ):
        await page.goto(f"http://testserver{path}")
        await expect(page.locator(f"#{view}")).to_contain_text(text)
    await expect(
        page.locator("#single-form, #model-settings-form, #imports-toggle, #chat-form")
    ).to_have_count(0)
    await page.goto("http://testserver/settings/analysis")
    await expect(page.locator("#analysis-view")).to_contain_text("外部 Agent")
    await expect(page.locator("input[name='analysis-mode']")).to_have_count(0)
    await page.goto("http://testserver/articles/dy-123")
    await page.locator("#article-content h1").first.wait_for()
    if await page.locator("#chat-toggle").get_attribute("aria-expanded") != "true":
        await page.locator("#chat-toggle").click()
    await expect(page.locator("#panel-tab-info")).to_be_visible()
    await expect(page.locator("#panel-tab-inspiration")).to_be_visible()
    await expect(page.locator("#panel-tab-chat")).to_have_count(0)
    await page.locator("#panel-tab-inspiration").click()
    await expect(page.locator("#inspiration-pane")).to_contain_text("保留这段方法")
    topic = service.create_topic("浏览器专题", ["dy-123"])
    service.save_topic_note(
        topic["topic"]["id"], "浏览器笔记正文", title="保留笔记", confirmed=True
    )
    await page.goto(f"http://testserver/topics/{topic['topic']['id']}")
    await expect(page.locator("#topic-content")).to_contain_text("浏览器笔记正文")
    if await page.locator("#chat-toggle").get_attribute("aria-expanded") != "true":
        await page.locator("#chat-toggle").click()
    await expect(page.locator("#panel-tab-sources")).to_be_visible()
    await expect(page.locator("#topic-sources-pane")).to_contain_text("测试文章")
    await expect(page.locator(".topic-artifact-actions button")).to_have_count(0)
    await page.keyboard.press("Escape")
    await page.keyboard.press("Meta+k")
    await expect(
        page.locator(
            "[data-command-id='single'], [data-command-id='creators'], "
            "[data-command-id='collections'], [data-command-id='favorites'], "
            "[data-command-id='chat']"
        )
    ).to_have_count(0)
    await page.keyboard.press("Escape")
    url = page.url
    await page.keyboard.press("Meta+i")
    await page.evaluate(
        """() => {
          const data = new DataTransfer();
          data.setData('text/plain', 'https://www.douyin.com/video/1234567890123456789');
          document.dispatchEvent(new ClipboardEvent('paste', {bubbles:true, clipboardData:data}));
        }"""
    )
    assert page.url == url
    assert not any(
        path.startswith("/api/chat/") or path == "/api/settings/model" for _method, path in calls
    )


@pytest.mark.parametrize("work_id,matching", [(A, 0), ("123", 1)])
async def test_direct_batch_library_keeps_global_counts(ui, work_id, matching):
    page, service, _client, _calls = ui
    parent = await scan(service, snapshot(kinds=[(work_id, "video")]))
    service.favorites.confirm(parent.id)
    await page.goto(f"http://testserver/?favorites_job={parent.id}")
    await expect(page.locator(".nav-item[data-section='all'] .nav-count")).to_have_text("3")
    assert await page.locator("#library-results [data-entry-id]").count() == matching
    if not matching:
        await expect(page.locator("#info-pane .library-statistics dd").first).to_have_text("3")
    await page.reload()
    await expect(page.locator(".nav-item[data-section='all'] .nav-count")).to_have_text("3")


@pytest.mark.parametrize("state", ["cancelled", "deleted", "failed"])
async def test_favorites_retry_button_follows_parent_and_child_state(ui, state):
    page, service, _client, calls = ui
    parent = await scan(service, snapshot(kinds=[(A, "video")]))
    service.favorites.confirm(parent.id)
    child_id = service.favorites.store.rows(parent.id)[0]["child_id"]
    service.database.update_job(child_id, status=JobStatus.FAILED, unlock=True)
    if state == "cancelled":
        service.cancel_job(parent.id)
    elif state == "deleted":
        service.delete_job(child_id)
    await page.evaluate(
        "path => window.Douku.navigate(path)", f"/imports/favorites?job={parent.id}"
    )
    await page.locator("#favorites-items .favorites-item").wait_for()
    retry = page.locator("#favorites-retry")
    if state != "failed":
        assert await retry.is_disabled()
        assert not any(path.endswith("/retry") for _method, path in calls)
    else:
        await page.wait_for_function("!document.querySelector('#favorites-retry').disabled")
        await retry.click()
        await page.wait_for_function(
            "document.querySelector('#favorites-summary').textContent.includes('处理中 1')"
        )
        assert service.database.get_job(child_id).status == JobStatus.QUEUED
        assert await retry.is_disabled()


async def test_cancelled_delete_does_not_run_when_reanalyzing_and_dialog_reuses(ui):
    page, service, _client, calls = ui
    await page.evaluate("window.Douku.navigate('/articles/dy-123')")
    await page.locator("#article-view:not(.hidden)").wait_for()
    await page.click("#panel-tab-info")
    await page.get_by_role("button", name="删除文章", exact=True).click()
    await page.keyboard.press("Escape")
    await page.get_by_role("button", name="重新分析", exact=True).click()
    await page.locator("#destructive-dialog[open]").wait_for()
    assert await page.locator("#confirm-destructive").is_enabled()
    await page.click("#confirm-destructive")
    await page.wait_for_function("!document.querySelector('#destructive-dialog').open")
    assert service.database.get_entry("dy-123")
    assert ("DELETE", "/api/articles/dy-123") not in calls
    await page.evaluate(
        "window.confirmResult = window.Douku.confirmDanger({title:'再次确认', description:'测试',"
        " confirmLabel:'确认'}); void 0;"
    )
    assert await page.locator("#confirm-destructive").is_enabled()
    await page.keyboard.press("Escape")
    assert await page.evaluate("window.confirmResult") is False


async def test_scope_changes_and_command_query_clears_old_results(ui):
    page, _service, _client, _calls = ui
    await page.evaluate("window.Douku.navigate('/articles/dy-123')")
    await page.locator("#article-view:not(.hidden)").wait_for()
    await page.evaluate("window.Douku.navigate('/settings')")
    await page.keyboard.press("Control+k")
    await page.fill("#command-input", "本地资料")
    await page.locator("#command-results [data-entry-id='dy-123']").wait_for()
    await page.fill("#command-input", "zzzzzzzz")
    assert await page.locator("#command-results [data-entry-id]").count() == 0
    await page.locator("#command-results").get_by_text("无匹配", exact=True).wait_for()
    await page.keyboard.press("Enter")
    assert await page.locator("#command-dialog").evaluate("node => node.open")
    await page.fill("#command-input", "测文章")
    await page.locator("#command-results [data-entry-id='dy-123']").wait_for()


async def test_tag_controls_and_real_merge_undo(ui, tmp_path):
    page, service, _client, _calls = ui
    out = tmp_path / "usability"
    out.mkdir(parents=True, exist_ok=True)
    await page.set_viewport_size({"width": 1440, "height": 900})
    await page.evaluate("window.Douku.navigate('/tags')")
    await page.locator("#tags-rows tr").first.wait_for()
    for theme in ("light", "dark"):
        await page.evaluate("theme => window.DoukuTheme.set(theme)", theme)
        await page.screenshot(path=out / f"tags-desktop-{theme}.png")
        await page.locator("#library-sidebar").screenshot(path=out / f"sidebar-{theme}.png")
    await page.locator("[data-tag-sort=name]").click()
    assert await page.locator("[data-tag-sort=name]").inner_text() == "标签名 ↑"
    assert await page.locator("#tags-select-all").is_visible()
    await page.check("#tags-select-all")
    assert await page.locator("#tags-selected").inner_text() == "已选 3 个"
    await page.uncheck("#tags-select-all")
    for name in ("AI 代理", "AI Agent"):
        await page.get_by_role("checkbox", name=f"选择 {name}", exact=True).check()
    await page.click("#tags-merge")
    await page.select_option("#tag-target", "AI Agent")
    await page.click("#tag-edit-submit")
    await page.locator("#toast .toast-action").wait_for()
    assert service.database.get_entry("dy-1000").tags == ["AI Agent"]
    await page.locator("#tag-aliases").get_by_role("button", name="撤销操作", exact=True).click()
    await page.wait_for_function("document.querySelector('#toast').textContent.includes('已恢复')")
    assert service.database.get_entry("dy-1000").tags == ["AI 代理"]
    assert await page.locator("#tags-rows time").count() == 3
    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")


async def test_retired_import_and_events_distinguish_background(ui):
    page, _service, _client, calls = ui
    await page.evaluate("window.Douku.navigate('/imports/single')")
    await expect(page.locator("#imports-single-view")).to_contain_text("去 Agent 里导入")
    await expect(page.locator("#single-form")).to_have_count(0)
    assert ("POST", "/api/captures") not in calls
    await page.evaluate(
        "window.Douku.toast('已收藏');"
        " window.libraryEvents.dispatchEvent(new MessageEvent('library',"
        " {data:JSON.stringify({origins:[window.Douku.clientId]})}));"
    )
    await page.wait_for_timeout(200)
    assert await page.locator("#toast").inner_text() == "已收藏"
    await page.evaluate(
        "window.libraryEvents.dispatchEvent(new MessageEvent('library',"
        " {data:JSON.stringify({origins:[window.Douku.clientId,null]})}));"
    )
    await page.wait_for_function("document.querySelector('#toast').textContent === '资料库已更新'")


async def test_reader_shows_window_precision_in_transcript(ui):
    page, service, _client, _calls = ui
    entry = service.database.get_entry("dy-1000")
    data = service.database.get_entry_data(entry.id)
    data["transcript_raw"] = [
        {
            "id": 0,
            "start_ms": 120000,
            "end_ms": 180000,
            "text": "窗口内保留的完整原话。",
            "timestamp_precision": "window",
        }
    ]
    data["transcript_corrected"] = data["transcript_raw"]
    service.database.upsert_entry(entry, data)
    await page.goto("http://testserver/articles/dy-1000")
    await page.locator(".article-transcript > summary").click()
    await expect(page.locator(".transcript-time")).to_contain_text("时间未经精确对齐")
    await expect(page.locator(".transcript-text")).to_have_text("窗口内保留的完整原话。")


async def test_reader_keyframes_and_semantic_search_fallback(ui):
    page, service, _client, _calls = ui
    import base64

    entry = service.database.get_entry("dy-1000")
    frame = service.config.vault_path / "raw/assets/1000/frames/frame-0000001200.png"
    frame.parent.mkdir(parents=True)
    frame.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jVf0AAAAASUVORK5CYII="
        )
    )
    data = service.database.get_entry_data(entry.id)
    data["frames"] = [
        {"path": frame.relative_to(service.config.vault_path).as_posix(), "timestamp_ms": 1200}
    ]
    data["media_policy"] = {"keep_keyframes": True}
    service.database.upsert_entry(entry, data)
    await page.goto("http://testserver/articles/dy-1000")
    await page.locator(".article-keyframes > summary").click()
    image = page.locator(".keyframe-grid img")
    await expect(image).to_have_count(1)
    await expect(page.locator(".keyframe-grid figcaption")).to_contain_text("00:01")
    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    await page.goto("http://testserver/")
    await page.locator("#search-mode").select_option("semantic")
    await page.locator("#search-input").fill("原文保留")
    await expect(page.locator("#search-capability")).to_contain_text("关键词结果")
    assert "/api/library/search" in " ".join(path for method, path in _calls)


async def test_reader_focus_fonts_and_selection_guidance(ui, tmp_path):
    page, _service, _client, calls = ui
    assert not any(path.endswith("/reading.css") or "/fonts/reading/" in path for _, path in calls)
    await page.evaluate("window.Douku.navigate('/topics')")
    await page.click("#topics-create-button")
    await page.locator("#library-selection-bar:not(.hidden) .selection-hint").wait_for()
    assert "下一步" in await page.locator(".selection-hint").inner_text()
    await page.click("#library-selection-cancel")
    row = page.locator("#library-results [data-entry-id='dy-123']").first
    await row.locator("[data-entry-action='open']").focus()
    await page.keyboard.press("Enter")
    await page.locator("#article-view:not(.hidden)").wait_for()
    await page.wait_for_function("document.activeElement.id === 'main-content'")
    assert (
        await page.locator("#main-content").evaluate("node => getComputedStyle(node).outlineWidth")
        == "0px"
    )
    assert await page.locator("body").evaluate("node => getComputedStyle(node).fontSize") == "14px"
    out = tmp_path / "usability"
    out.mkdir(parents=True, exist_ok=True)
    for theme in ("light", "dark"):
        await page.evaluate("theme => window.DoukuTheme.set(theme)", theme)
        await page.evaluate("document.fonts.ready")
        await page.screenshot(path=out / f"article-{theme}.png")


@pytest.mark.parametrize(
    "status",
    [
        JobStatus.NEEDS_AUTH,
        JobStatus.NEEDS_REVIEW,
        JobStatus.NEEDS_SELECTION,
        JobStatus.WAITING_CONFIRMATION,
        JobStatus.AWAITING_AGENT_ANALYSIS,
    ],
)
async def test_paused_job_can_be_cancelled_from_detail(ui, status):
    page, service, client, _calls = ui
    job = service.database.create_job(
        CaptureRequest(share_text="https://www.douyin.com/video/1234567890123")
    )
    service.database.update_job(job.id, status=status, unlock=True)
    response = await asyncio.to_thread(client.get, "/api/jobs")
    assert next(item for item in response.json()["items"] if item["id"] == job.id)["can_cancel"]
    await page.evaluate("id => window.Douku.navigate(`/jobs/${id}`)", job.id)
    root = page.locator("#job-detail-view:not(.hidden)")
    cancel = root.get_by_role("button", name="取消", exact=True)
    await cancel.click()
    await page.click("#confirm-destructive")
    await page.wait_for_function("document.querySelector('#toast').textContent === '已取消'")
    assert service.database.get_job(job.id).status == JobStatus.CANCELLED
    response = await asyncio.to_thread(client.get, f"/api/jobs/{job.id}")
    assert response.json()["can_cancel"] is False
    await cancel.wait_for(state="hidden")
