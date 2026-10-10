"""Offline Chromium checks against the real shell, JavaScript and local API."""

from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.async_api import async_playwright, expect

from douyin_wiki.models import CaptureRequest, JobStatus
from douyin_wiki.webapp.app import create_app
from tests.support.web import _web_fixture, local_client
from tests.test_favorites import A, scan, snapshot
from tests.test_tags import source


@pytest.fixture
async def ui(tmp_path):
    config, service = _web_fixture(tmp_path)
    source(service, "1000", ["AI 代理"], managed=True)
    source(service, "1001", ["AI Agent"], managed=True)
    app = create_app(config, service=service,  start_watcher=False)
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


async def test_mobile_tag_controls_and_real_merge_undo(ui):
    page, service, _client, _calls = ui
    out = Path(__file__).parents[1] / "docs/screenshots/redesign/usability"
    out.mkdir(parents=True, exist_ok=True)
    await page.evaluate("window.Douku.navigate('/tags')")
    await page.locator("#tags-rows tr").first.wait_for()
    for theme in ("light", "dark"):
        await page.evaluate("theme => window.DoukuTheme.set(theme)", theme)
        await page.screenshot(path=out / f"tags-desktop-{theme}.png")
        await page.locator("#library-sidebar").screenshot(path=out / f"sidebar-{theme}.png")
    await page.set_viewport_size({"width": 390, "height": 844})
    await page.evaluate("window.Douku.navigate('/tags')")
    await page.locator("#tags-cards .tag-card").first.wait_for()
    await page.select_option("#tags-mobile-sort", "name:1")
    assert await page.locator("#tags-mobile-select-all").is_visible()
    await page.check("#tags-mobile-select-all")
    assert await page.locator("#tags-selected").inner_text() == "已选 3 个"
    await page.uncheck("#tags-mobile-select-all")
    for name in ("AI 代理", "AI Agent"):
        await (
            page.get_by_role("checkbox", name=f"选择 {name}", exact=True)
            .filter(visible=True)
            .check()
        )
    await page.click("#tags-merge")
    await page.select_option("#tag-target", "AI Agent")
    await page.click("#tag-edit-submit")
    await page.locator("#toast .toast-action").wait_for()
    assert service.database.get_entry("dy-1000").tags == ["AI Agent"]
    await page.locator("#tag-aliases").get_by_role("button", name="撤销操作", exact=True).click()
    await page.wait_for_function("document.querySelector('#toast').textContent.includes('已恢复')")
    assert service.database.get_entry("dy-1000").tags == ["AI 代理"]
    assert await page.locator("#tags-cards time").count() == 3
    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    out = Path(__file__).parents[1] / "docs/screenshots/redesign/usability"
    out.mkdir(parents=True, exist_ok=True)
    for theme in ("light", "dark"):
        await page.evaluate("theme => window.DoukuTheme.set(theme)", theme)
        await page.screenshot(path=out / f"tags-mobile-{theme}.png")


async def test_invalid_time_stops_submission_and_events_distinguish_background(ui):
    page, _service, _client, calls = ui
    await page.evaluate("window.Douku.navigate('/imports/single')")
    await page.fill("#single-share", "https://www.douyin.com/video/7604970362420645155")
    await page.locator("#single-form details").evaluate("node => node.open = true")
    await page.fill("#single-start", "2:00")
    await page.fill("#single-end", "1:00")
    await page.click("#single-form button[type=submit]")
    assert "结束时间不能早于" in await page.locator("#single-error").inner_text()
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


async def test_reader_focus_fonts_and_selection_guidance(ui):
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
    out = Path(__file__).parents[1] / "docs/screenshots/redesign/usability"
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
