"""Offline Chromium checks against the real shell, JavaScript and local API."""

from __future__ import annotations

import asyncio
import json
import re
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


async def test_agent_command_copy_failure_does_not_show_success(ui):
    page, _service, _client, _calls = ui
    await page.goto("http://testserver/imports")
    await page.evaluate("""Object.defineProperty(navigator, 'clipboard', {
        configurable: true, value: {writeText: async () => {
            throw new DOMException('Clipboard denied', 'NotAllowedError');
        }}
    })""")
    buttons = page.locator("#imports-view [data-copy-command]")
    await buttons.nth(0).click()
    await expect(page.locator("#toast.show")).to_have_text("复制失败")
    assert not await buttons.nth(0).locator(".copy-check").is_visible()
    assert await buttons.nth(0).locator(".copy-icon").is_visible()
    await page.evaluate("window.Douku.toast('其他通知')")
    await buttons.nth(1).focus()
    assert not await buttons.nth(1).locator(".copy-check").is_visible()


async def test_agent_command_copy_feedback_expires_for_each_button(ui):
    page, _service, _client, _calls = ui
    await page.goto("http://testserver/imports")
    await page.evaluate("""Object.defineProperty(navigator, 'clipboard', {
        configurable: true, value: {writeText: async value => {window.lastCopied=value;}}
    })""")
    await page.clock.install()
    buttons = page.locator("#imports-view [data-copy-command]")
    first, second = buttons.nth(0), buttons.nth(1)
    await first.click()
    assert await page.evaluate("window.lastCopied") == await first.get_attribute(
        "data-copy-command"
    )
    await expect(first.locator(".copy-check")).to_be_visible()
    await page.clock.fast_forward(1000)
    await second.click()
    await expect(first.locator(".copy-check")).to_be_visible()
    await expect(second.locator(".copy-check")).to_be_visible()
    await page.clock.fast_forward(500)
    await expect(first.locator(".copy-check")).not_to_be_visible()
    await expect(second.locator(".copy-check")).to_be_visible()
    await page.clock.fast_forward(1000)
    await expect(second.locator(".copy-check")).not_to_be_visible()
    await expect(page.locator("#toast.show")).to_have_text("已复制")


async def test_agent_command_repeated_copy_restarts_feedback(ui):
    page, _service, _client, _calls = ui
    await page.goto("http://testserver/imports")
    await page.evaluate("""Object.defineProperty(navigator, 'clipboard', {
        configurable: true, value: {writeText: async () => {}}
    })""")
    await page.clock.install()
    button = page.locator("#imports-view [data-copy-command]").first
    await button.click()
    await page.clock.fast_forward(1000)
    await button.click()
    await page.clock.fast_forward(500)
    await expect(button.locator(".copy-check")).to_be_visible()
    await page.clock.fast_forward(1000)
    await expect(button.locator(".copy-check")).not_to_be_visible()


async def test_command_search_closes_with_one_escape_and_keeps_query(ui):
    page, _service, _client, _calls = ui
    await page.keyboard.press("Meta+k")
    await page.locator("#command-input").fill("jev")
    await page.keyboard.press("Escape")
    assert not await page.locator("#command-dialog").evaluate("dialog => dialog.open")
    assert await page.locator("#command-input").input_value() == "jev"


@pytest.mark.parametrize(("second_key", "destination"), [
    ("l", "/"), ("r", "/?section=recent"), ("f", "/?section=favorite"),
    ("i", "/?section=inspiration"), ("j", "/jobs"), ("g", "/tags"),
    ("t", "/topics"), ("x", "/trash"), ("s", "/settings"),
])
async def test_navigation_shortcuts_run_as_key_sequences(ui, second_key, destination):
    page, _service, _client, _calls = ui
    await page.locator("#main-content").focus()
    await page.keyboard.press("g")
    await page.keyboard.press(second_key)
    await expect(page).to_have_url(f"http://testserver{destination}")


async def test_holding_g_does_not_trigger_the_tags_shortcut(ui):
    page, _service, _client, _calls = ui
    await page.locator("#main-content").focus()
    await page.keyboard.down("g")
    await page.keyboard.down("g")  # OS key repeat, without releasing the first key.
    await page.keyboard.up("g")
    await expect(page).to_have_url("http://testserver/")
    await expect(page.locator("#library-view")).to_be_visible()


@pytest.mark.parametrize("cancel", ["escape", "timeout", "command_palette"])
async def test_cancelled_navigation_sequence_releases_tag_list_keys(ui, cancel):
    page, _service, _client, _calls = ui
    await page.goto("http://testserver/tags")
    rows = page.locator("#tags-rows [data-tag-name]")
    await rows.first.focus()
    await page.keyboard.press("g")
    if cancel == "timeout":
        await page.wait_for_timeout(1100)
    elif cancel == "command_palette":
        await page.keyboard.press("Meta+k")
        await expect(page.locator("#command-dialog")).to_have_js_property("open", True)
        await page.keyboard.press("Escape")
    else:
        await page.keyboard.press("Escape")
    await page.keyboard.press("j")
    await expect(page).to_have_url("http://testserver/tags")
    await expect(rows.nth(1)).to_be_focused()


async def test_command_palette_explains_sequences_and_keeps_typing_as_search(ui):
    page, _service, _client, _calls = ui
    await page.locator("#main-content").focus()
    await page.keyboard.press("Meta+k")
    await expect(page.locator('[data-command-id="jobs"] kbd')).to_have_text("G → J")
    await expect(page.locator("#command-shortcut-help")).to_contain_text("先关闭面板")
    await page.keyboard.type("gj")
    await expect(page.locator("#command-input")).to_have_value("gj")
    await expect(page.locator("#command-dialog")).to_have_js_property("open", True)
    await expect(page).to_have_url("http://testserver/")
    await page.keyboard.press("Escape")
    await page.locator("#main-content").focus()
    await page.keyboard.press("g")
    await page.keyboard.press("j")
    await expect(page).to_have_url("http://testserver/jobs")


async def test_pointer_dialog_enters_and_exits_without_delaying_native_close(ui):
    page, service, _client, calls = ui
    await page.goto("http://testserver/articles/dy-123")
    before = len(service.database.list_jobs())
    for _ in range(2):
        await page.locator("#reader-actions .entry-more").click()
        await page.get_by_role("menuitem", name="重新分析", exact=True).click()
        dialog = page.locator("#destructive-dialog")
        await expect(dialog).to_have_js_property("open", True)
        assert await dialog.evaluate(
            "node => node.getAnimations().some(animation => animation.playState === 'running')"
        )
        await dialog.get_by_role("button", name="取消", exact=True).click()
        # The native close and business state finish before the exit paint.
        await expect(dialog).to_have_js_property("open", False)
        assert await dialog.evaluate("node => getComputedStyle(node).pointerEvents") == "none"
        await page.wait_for_function(
            "getComputedStyle(document.querySelector('#destructive-dialog')).display === 'none'"
        )
    assert len(service.database.list_jobs()) == before
    assert not any(method == "POST" and path.endswith("/reanalyze") for method, path in calls)


async def test_keyboard_reader_and_command_palette_skip_motion(ui):
    page, _service, _client, _calls = ui
    await page.locator("#view-gallery").click()
    await page.evaluate("""() => {
        window.transitionCalls = 0;
        const original = document.startViewTransition.bind(document);
        document.startViewTransition = (...args) => {
            window.transitionCalls++; return original(...args);
        };
    }""")
    await page.locator('.gallery-card[data-entry-id="dy-123"] .gallery-card-link').focus()
    await page.keyboard.press("Enter")
    await expect(page.locator("#article-view")).to_be_visible()
    await expect(page.locator(".article-body h2")).not_to_have_count(0)
    assert await page.evaluate("window.transitionCalls") == 0
    await page.keyboard.press("Meta+k")
    dialog = page.locator("#command-dialog")
    await expect(dialog).to_have_js_property("open", True)
    assert await dialog.evaluate("node => node.getAnimations().length") == 0
    await page.keyboard.press("Escape")
    await expect(dialog).to_have_js_property("open", False)
    assert await dialog.evaluate("node => getComputedStyle(node).display") == "none"


async def test_pointer_cover_transition_returns_after_keyboard_use(ui):
    page, _service, _client, _calls = ui
    await page.keyboard.press("Meta+k")
    await page.keyboard.press("Escape")
    await page.locator("#view-gallery").click()
    await page.evaluate("""() => {
        window.transitionCalls = 0;
        const original = document.startViewTransition.bind(document);
        document.startViewTransition = (...args) => {
            window.transitionCalls++; return original(...args);
        };
    }""")
    await page.locator('.gallery-card[data-entry-id="dy-123"] .gallery-card-link').click()
    await expect(page.locator(".article-body h2")).not_to_have_count(0)
    assert await page.evaluate("window.transitionCalls") == 1
    await page.wait_for_function(
        "!document.querySelector('.article-cover').style.viewTransitionName"
    )


@pytest.mark.parametrize("reduced", [False, True])
async def test_overlay_motion_does_not_move_content_and_escape_closes_immediately(ui, reduced):
    page, _service, _client, _calls = ui
    await page.set_viewport_size({"width": 1100, "height": 900})
    await page.emulate_media(reduced_motion="reduce" if reduced else "no-preference")
    main_width = await page.locator("#main-content").evaluate("node => node.clientWidth")
    for _ in range(2):
        await page.locator("#chat-toggle").click()
        panel = page.locator("#side-panel")
        await expect(panel).to_have_js_property("inert", False)
        current_width = await page.locator("#main-content").evaluate("node => node.clientWidth")
        assert current_width == main_width
        if reduced:
            assert await panel.evaluate("node => node.getAnimations().length") == 0
        await page.keyboard.press("Escape")
        await expect(panel).to_have_js_property("inert", True)
        assert await panel.evaluate("node => getComputedStyle(node).visibility") == "hidden"
        assert await panel.evaluate("node => node.getAnimations().length") == 0
        await expect(page.locator("#chat-toggle")).to_be_focused()


async def test_closing_popovers_and_toasts_cannot_keep_hidden_controls_focusable(ui):
    page, _service, _client, _calls = ui
    await page.locator("#filter-toggle").click()
    popover = page.locator("#filter-popover")
    await expect(popover).to_have_js_property("inert", False)
    await page.keyboard.press("Escape")
    await expect(popover).to_have_js_property("inert", True)
    await expect(page.locator("#filter-toggle")).to_be_focused()
    await page.locator("#filter-toggle").click()
    await expect(popover).to_have_js_property("inert", False)
    await expect(popover).to_be_visible()
    await page.locator("#apply-filters").click()
    await expect(popover).to_have_js_property("inert", True)
    await page.evaluate("""() => {
        window.Douku.toastAction('已完成测试', '撤销', () => {});
        window.Douku.hideToast();
    }""")
    toast = page.locator("#toast")
    await expect(toast.locator(".toast-action")).to_have_js_property("inert", True)
    await toast.locator(".toast-action").focus()
    await expect(toast.locator(".toast-action")).not_to_be_focused()
    assert await toast.evaluate("node => getComputedStyle(node).pointerEvents") == "none"
    await page.evaluate("window.Douku.toast('再次显示提示')")
    await expect(toast).to_have_attribute("role", "status")
    await expect(toast).to_contain_text("再次显示提示")


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
    await page.goto("http://testserver/?section=inspiration&view=list")
    row = page.locator('#library-results [data-entry-id="dy-123"]')
    await expect(page.locator("#library-results [data-entry-id]")).to_have_count(1)
    await expect(row.locator("h2")).to_have_text("测试文章")
    await expect(row.locator(".item-inspiration span")).to_have_text(
        ["保留这段方法", "保留时间定位"]
    )
    await expect(row.locator(".item-summary")).to_have_count(0)
    await expect(row).not_to_contain_text("这是一条本地资料。")
    await page.locator("#view-gallery").click()
    await expect(row.locator(".item-inspiration span")).to_have_text(
        ["保留这段方法", "保留时间定位"]
    )
    await expect(row.locator(".gallery-card-link")).to_have_accessible_name(
        re.compile("灵感：保留这段方法，灵感：保留时间定位")
    )
    await page.locator('.nav-item[data-section="all"]').click()
    await page.locator("#view-list").click()
    await expect(row.locator(".item-summary")).to_have_text("这是一条本地资料。")
    await expect(row.locator(".item-inspirations")).to_have_count(0)


@pytest.mark.parametrize("previous_scope", [
    "section=inspiration",
    "section=inspiration&view=gallery&sort=title_asc",
    "section=favorite",
    "section=recent",
    "q=本地&author=测试博主&type=tutorial&tag=咖啡&source=video&has_inspiration=1",
    "q=本地&search_mode=semantic&section=inspiration",
    "favorites_batch",
])
async def test_topics_source_picker_starts_from_the_whole_library(ui, previous_scope):
    page, service, _client, calls = ui
    if previous_scope == "favorites_batch":
        batch = await scan(service, snapshot(kinds=[(A, "video")]))
        previous_scope = f"favorites_job={batch.id}"
    await page.goto(f"http://testserver/?{previous_scope}")
    await expect(page.locator("#view-title")).not_to_be_empty()
    await page.locator("#topics-nav").click()
    await expect(page.locator("#topics-view")).to_be_visible()
    await page.locator("#topics-create-button").click()
    await expect(page.locator("#view-title")).to_have_text("资料库")
    await expect(page.locator('.nav-item[data-section="all"]')).to_have_class(
        re.compile(r"\bactive\b")
    )
    await expect(page.locator("#search-input")).to_have_value("")
    await expect(page.locator("#applied-filters")).to_be_hidden()
    await expect(page.locator("#library-results [data-entry-id]")).to_have_count(3)
    await expect(page.locator("#library-selection-count")).to_have_text("已选 0 篇")
    params = urlsplit(page.url).query
    assert not any(key in params for key in (
        "section=", "q=", "author=", "type=", "tag=", "source=", "has_inspiration=",
        "favorites_job=",
    )), params
    await page.locator(
        '#library-results [data-entry-id="dy-1000"] .topic-selection-indicator'
    ).check()
    await page.locator("#library-selection-next").click()
    await expect(page.locator("#topic-dialog")).to_have_js_property("open", True)
    await expect(page.locator("#topic-selection-summary")).to_contain_text("已选择 1 篇")
    assert ("POST", "/api/topics") not in calls


async def test_library_source_selection_keeps_current_filters(ui):
    page, _service, _client, _calls = ui
    await page.goto("http://testserver/?tag=AI%20代理")
    await expect(page.locator("#library-results [data-entry-id]")).to_have_count(1)
    previous_url = page.url
    await page.locator("#topic-select-toggle").click()
    assert page.url == previous_url
    await expect(page.locator("#library-results [data-entry-id]")).to_have_count(1)
    await expect(page.locator("#applied-filters")).to_contain_text("AI 代理")
    await expect(page.locator(".topic-selection-indicator")).to_be_visible()


@pytest.mark.parametrize("width", [1440, 960])
async def test_topic_guidance_and_delete_restore(ui, width):
    page, service, _client, calls = ui
    topic = service.create_topic("引导与删除测试", ["dy-123", "dy-1000"], goal="比较方法")
    topic_id = topic["topic"]["id"]
    await page.set_viewport_size({"width": width, "height": 1000})
    await page.goto(f"http://testserver/topics/{topic_id}")
    await expect(page.locator(".topic-guide > summary")).to_have_text("接下来怎么做")
    assert await page.locator(".topic-guide").evaluate("node => node.open")
    await expect(page.locator("#topic-delete-empty")).to_be_visible()
    await expect(page.locator("#topic-delete")).to_be_hidden()
    prompt = page.locator("#topic-research-prompt")
    assert topic_id in await prompt.input_value()
    await prompt.fill("只比较来源中的方法，保留我自己写的研究问题。")
    await page.evaluate("""Object.defineProperty(navigator, 'clipboard', {configurable:true,
        value:{writeText:async value => {window.copiedTopicPrompt=value}}})""")
    await page.get_by_role("button", name="复制研究指令", exact=True).click()
    assert await page.evaluate("window.copiedTopicPrompt") == await prompt.input_value()
    await page.locator(".topic-guide-sources").click()
    checks = page.locator("#topic-sources-pane input")
    await checks.nth(1).uncheck()
    await expect(page.locator(".topic-guide-sources")).to_contain_text("1 / 2")
    assert await prompt.input_value() == "只比较来源中的方法，保留我自己写的研究问题。"
    await checks.nth(0).uncheck()
    await expect(page.get_by_role("button", name="复制研究指令", exact=True)).to_be_disabled()
    await expect(page.locator("#topic-delete-empty")).to_be_enabled()
    await checks.nth(0).check()
    await expect(page.get_by_role("button", name="复制研究指令", exact=True)).to_be_enabled()
    if width == 960:
        await page.keyboard.press("Escape")
    service.save_topic_note(topic_id, "需要随专题恢复的逐字笔记", confirmed=True)
    await page.locator("#topic-delete-empty").click()
    await expect(page.locator("#destructive-dialog")).to_contain_text("来源文章、视频和灵感保留")
    await page.locator("#destructive-dialog").get_by_role("button", name="取消", exact=True).click()
    assert service.database.get_topic(topic_id).id == topic_id
    await page.locator("#topic-delete-empty").click()
    await page.locator("#confirm-destructive").click()
    await expect(page).to_have_url("http://testserver/topics")
    await expect(page.locator("#topics-list")).not_to_contain_text("引导与删除测试")
    await page.locator("#trash-nav").click()
    row = page.locator(".trash-item", has_text="引导与删除测试")
    await expect(row).to_contain_text("专题 · 2 篇来源 · 1 篇笔记")
    await row.get_by_role("button", name="恢复", exact=True).click()
    await page.locator("#confirm-destructive").click()
    await expect(page).to_have_url(f"http://testserver/topics/{topic_id}")
    await expect(page.locator(".topic-artifacts")).to_contain_text("需要随专题恢复的逐字笔记")
    assert not await page.locator(".topic-guide").evaluate("node => node.open")
    await expect(page.locator("#topic-delete")).to_be_visible()
    await expect(page.locator("#topic-delete-empty")).to_have_count(0)
    assert service.database.get_entry("dy-123").id == "dy-123"
    assert service.database.get_entry("dy-1000").id == "dy-1000"
    assert ("DELETE", f"/api/topics/{topic_id}") in calls
    assert not any(method == "POST" and path.endswith("/artifacts") for method, path in calls)
    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")


@pytest.mark.parametrize("width", [1440])
async def test_video_detail_has_collapsed_complete_transcript_and_local_video_link(ui, width):
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
    await expect(folded.locator("summary")).to_contain_text("Transcript")
    await expect(folded.locator("summary")).to_contain_text("Corrected · 31 segments")
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


@pytest.mark.parametrize("width", [1440, 1100])
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
    if width == 1100:
        assert await page.locator("#app-shell").evaluate(
            "element => element.classList.contains('panel-overlay-open')"
        )
        await page.locator("#article-content h1").first.click(position={"x": 8, "y": 8})
        assert not await page.locator("#app-shell").evaluate(
            "element => element.classList.contains('panel-overlay-open')"
        )
        await page.locator("#chat-toggle").click()
        await page.keyboard.press("Escape")
        assert await page.evaluate("document.activeElement.id") == "chat-toggle"
        assert not await page.locator("#app-shell").evaluate(
            "element => element.classList.contains('panel-overlay-open')"
        )
        await page.locator("#chat-toggle").click()
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
    await expect(page.locator("#command-status")).to_have_text("没有找到匹配的文章或命令。")
    await page.keyboard.press("Enter")
    assert await page.locator("#command-dialog").evaluate("node => node.open")
    await page.fill("#command-input", "测文章")
    await page.locator("#command-results [data-entry-id='dy-123']").wait_for()


async def test_tag_views_keep_records_first_and_scope_selection(ui):
    page, service, _client, calls = ui
    source(service, "1002", ["旧标签"], managed=True)
    service.tags.rename("旧标签", "新标签")
    await page.goto("http://testserver/tags")
    await expect(page.locator("#tags-tab-all")).to_have_attribute("aria-selected", "true")
    await expect(page.locator("#tags-all-panel")).to_be_visible()
    await expect(page.locator("#tag-aliases")).to_be_hidden()
    await expect(page.locator("#tag-duplicates")).to_be_hidden()
    await expect(page.locator("#tags-rename")).to_be_hidden()

    await page.get_by_role("checkbox", name="选择 AI Agent", exact=True).check()
    await expect(page.locator("#tags-rename")).to_be_visible()
    await page.locator("#tags-tab-aliases").click()
    await expect(page.locator("#tag-aliases")).to_be_visible()
    await expect(page.locator("#tag-aliases")).to_contain_text("旧标签")
    await expect(page.locator("#tag-aliases")).to_contain_text("新标签")
    await expect(page.locator("#tags-all-panel")).to_be_hidden()
    await page.locator("#tags-search").fill("没有这个标签")
    await expect(page.locator("#tag-aliases")).to_contain_text("没有符合条件的别名")
    await page.locator("#tags-search").fill("新标签")
    await expect(page.locator(".tag-alias-row")).to_have_count(1)
    await page.locator("#tags-search").fill("")

    await page.locator("#tags-tab-aliases").focus()
    await page.keyboard.press("Home")
    await expect(page.locator("#tags-tab-all")).to_be_focused()
    await expect(page.locator("#tags-tab-all")).to_have_attribute("aria-selected", "true")
    await expect(page.get_by_role("checkbox", name="选择 AI Agent", exact=True)).not_to_be_checked()
    await expect(page.locator("#tags-rename")).to_be_hidden()
    assert not any(method == "POST" and path.startswith("/api/tags/") for method, path in calls)


@pytest.mark.parametrize("width", [838, 960, 1440])
@pytest.mark.parametrize("theme", ["light", "dark"])
async def test_tag_actions_stay_inside_visible_main_column(ui, width, theme):
    page, service, _client, calls = ui
    old_name = "原标签名称较长时也应完整保留操作入口" * 3
    new_name = "当前标签名称较长时不应挤出右侧按钮" * 3
    source(service, "1002", [old_name], managed=True)
    service.tags.rename(old_name, new_name)
    await page.set_viewport_size({"width": width, "height": 868})
    await page.goto("http://testserver/tags")
    await page.locator("select[data-theme-select]").select_option(theme)
    await expect(page.locator("#tags-rows tr")).to_have_count(4)

    async def assert_controls_inside(selector):
        bounds = await page.evaluate(
            """selector => {
            const main = document.querySelector('#main-content');
            const left = main.getBoundingClientRect().left;
            const right = Math.min(document.documentElement.clientWidth, left + main.clientWidth);
            return [...document.querySelectorAll(selector)]
                .filter(node => node.getClientRects().length)
                .map(node => {
                    const rect = node.getBoundingClientRect();
                    return {label: node.textContent || node.getAttribute('aria-label'),
                        left: rect.left, right: rect.right, visibleLeft: left, visibleRight: right,
                        client: node.clientWidth, scroll: node.scrollWidth};
                });
        }""",
            selector,
        )
        assert bounds, selector
        for control in bounds:
            assert control["left"] >= control["visibleLeft"] + 4, control
            assert control["right"] <= control["visibleRight"] - 4, control
            assert control["scroll"] <= control["client"] + 1, control

    await page.get_by_role("checkbox", name="选择 AI Agent", exact=True).check()
    await assert_controls_inside(
        ".tags-toolbar button:not(.hidden), .tag-table input, .tag-table button"
    )
    await page.locator("#tags-tab-duplicates").click()
    await expect(page.locator(".tag-duplicate-group")).to_have_count(1)
    await assert_controls_inside(".tag-duplicate-group select, .tag-duplicate-group button")
    await page.locator("#tags-tab-aliases").click()
    await expect(page.locator(".tag-alias-actions button")).to_have_count(2)
    await assert_controls_inside(".tag-alias-actions button, .view-header .chat-toggle-button")
    await page.get_by_role("button", name="撤销操作", exact=True).focus()
    await page.keyboard.press("Tab")
    await expect(page.get_by_role("button", name="解除别名", exact=True)).to_be_focused()
    assert await page.locator(".tag-alias-heading").evaluate(
        "node => node.lastElementChild.getBoundingClientRect().right"
        " <= document.documentElement.clientWidth"
    )
    assert await page.locator(".app-shell").evaluate(
        "node => node.getBoundingClientRect().width >= 960"
    )
    if width == 838:
        await page.locator("#sidebar-collapse").click()
        await assert_controls_inside(".tag-alias-actions button, .view-header .chat-toggle-button")
    assert not any(method == "POST" and path.startswith("/api/tags/") for method, path in calls)


async def test_duplicate_merge_excludes_unchecked_member(ui):
    page, service, _client, _calls = ui
    source(service, "1002", ["智能体"], managed=True)
    await page.goto("http://testserver/tags")
    await page.locator("#tags-tab-duplicates").click()
    group = page.locator(".tag-duplicate-group").filter(has_text="智能体")
    await expect(group).to_be_visible()
    await group.get_by_label("纳入合并：智能体").uncheck()
    await group.get_by_role("button", name="合并", exact=True).click()
    description = page.locator("#destructive-description")
    await expect(description).to_contain_text("2 个标签")
    await expect(description).not_to_contain_text("智能体")
    await page.locator("#confirm-destructive").click()
    await expect(page.locator("#tags-rows")).to_contain_text("智能体")
    await expect(page.locator("#tags-rows")).not_to_contain_text("AI 代理")


@pytest.mark.parametrize("refresh", ["sort", "search", "reload"])
async def test_duplicate_merge_keeps_exclusions_and_target_after_refresh(ui, refresh):
    page, service, _client, _calls = ui
    source(service, "1002", ["智能体"], managed=True)
    await page.goto("http://testserver/tags")
    await page.locator("#tags-tab-duplicates").click()
    group = page.locator(".tag-duplicate-group").filter(has_text="智能体")
    excluded = group.get_by_label("纳入合并：智能体", exact=True)
    await excluded.uncheck()
    await group.locator("select").select_option("AI 代理")
    if refresh == "sort":
        await page.locator("#tags-tab-all").click()
        await page.locator("[data-tag-sort=name]").click()
        await page.locator("#tags-tab-duplicates").click()
    elif refresh == "search":
        await page.locator("#tags-search").fill("AI")
    else:
        await page.evaluate("window.Douku.tags.load()")
    await expect(excluded).not_to_be_checked()
    await expect(group.locator("select")).to_have_value("AI 代理")
    await group.get_by_role("button", name="合并", exact=True).click()
    await expect(page.locator("#destructive-description")).to_contain_text("2 个标签")
    await page.locator("#confirm-destructive").click()
    await expect(page.locator("#tags-rows")).not_to_contain_text("AI Agent")
    assert service.database.get_entry("dy-1002").tags == ["智能体"]


@pytest.mark.parametrize("failure", ["history", "detail"])
async def test_collection_initial_failure_shows_error_and_can_reload(ui, failure):
    from tests.test_collection_flow import ready

    page, service, _client, _calls = ui
    job_id = await ready(service)
    failed_path = "/api/collection-imports" + (f"/{job_id}" if failure == "detail" else "")
    failed_once = False

    async def fail_once(route):
        nonlocal failed_once
        if urlsplit(route.request.url).path == failed_path and not failed_once:
            failed_once = True
            await route.fulfill(status=503, json={"detail": "合集记录暂时无法读取"})
        else:
            await route.fallback()

    await page.route("**/api/collection-imports**", fail_once)
    await page.goto(f"http://testserver/imports/collections?job={job_id}")
    error = page.locator("#collection-error")
    await expect(error).to_be_visible()
    await expect(error).to_have_text("合集记录暂时无法读取")
    retry = page.get_by_role("button", name="重新加载", exact=True)
    await expect(retry).to_be_enabled()
    await retry.click()
    await expect(page.locator("#collection-summary")).to_contain_text("已读 2 件")
    await expect(page.locator("#collection-items input")).to_have_count(2)
    await expect(error).to_have_text("")


async def test_empty_library_hides_source_tabs_and_restores_them_after_updates(ui):
    page, _service, _client, _calls = ui
    empty = True

    async def catalog(route):
        if empty:
            await route.fulfill(json={"items": [], "facets": {}})
        else:
            await route.fallback()

    await page.route("**/api/library", catalog)
    await page.goto("http://testserver/?source=image_note")
    await expect(page.locator("#result-count")).to_have_text("0")
    await expect(page.locator(".source-tab[data-source='image_note']")).to_be_hidden()
    await expect(page.locator(".source-tab[data-source='video']")).to_be_hidden()
    await expect(page.locator(".source-tab[data-source='']")).to_have_attribute(
        "aria-pressed", "true"
    )
    assert "source=" not in page.url

    async def notify():
        await page.wait_for_function("Boolean(window.libraryEvents)")
        await page.evaluate(
            "window.libraryEvents.dispatchEvent(new MessageEvent('library',"
            " {data:JSON.stringify({origins:[window.Douku.clientId]})}));"
        )

    empty = False
    await notify()
    await expect(page.locator(".source-tab[data-source='video']")).to_be_visible()
    empty = True
    await notify()
    await expect(page.locator("#result-count")).to_have_text("0")
    await expect(page.locator(".source-tab[data-source='video']")).to_be_hidden()


@pytest.mark.parametrize(
    "stage,label",
    [("failed", "授权失败"), ("timeout", "授权超时"), ("profile_locked", "专用浏览器正被占用")],
)
async def test_auth_session_keeps_technical_errors_in_copy_only(ui, stage, label):
    page, service, _client, _calls = ui
    session = service.database.create_auth_session(channel="video", scope="video", stage=stage)
    diagnostic = "Playwright BrowserType.launch: browser process failed"
    service.database.update_auth_session(session["id"], error_summary=diagnostic)

    async def authorized(route):
        def channel(key):
            return {
                "channel": key,
                "channel_label": "视频下载授权" if key == "video" else "抖音账号授权",
                "purpose": "本机授权",
                "user_state": "authorized",
                "user_state_label": "已授权",
            }

        await route.fulfill(json={"channels": {key: channel(key) for key in ("video", "douyin")}})

    async def start(route):
        await route.fulfill(status=202, json={"id": session["id"]})

    await page.route("**/api/auth/status*", authorized)
    await page.route("**/api/auth/sessions", start)
    await page.evaluate("""Object.defineProperty(navigator, 'clipboard', {value: {
      writeText: async text => { window.copiedAuthDiagnostic = text; }
    }});""")
    await page.evaluate("window.Douku.navigate('/settings/auth')")
    await page.locator("#auth-video-card").get_by_role("button", name="重新授权").click()
    panel = page.locator("#auth-session")
    await expect(panel).to_contain_text(label)
    await expect(panel).not_to_contain_text("Playwright")
    await expect(panel).not_to_contain_text("failed")
    await expect(page.locator("#toast")).not_to_contain_text("Playwright")
    await panel.get_by_role("button", name="复制诊断信息").click()
    copied = await page.evaluate("window.copiedAuthDiagnostic")
    assert diagnostic in copied
    assert stage in copied
    if stage == "failed":
        await page.wait_for_timeout(2700)
        await expect(panel).to_be_visible()


@pytest.mark.parametrize("failure", ["status", "start", "session"])
async def test_auth_request_failure_uses_chinese_copy_and_keeps_diagnostics(ui, failure):
    page, _service, _client, _calls = ui
    diagnostic = "Playwright transport failed: ECONNRESET"

    async def auth(route):
        path = urlsplit(route.request.url).path
        failed_path = {
            "status": "/api/auth/status",
            "start": "/api/auth/sessions",
            "session": "/api/auth/sessions/failed-request",
        }[failure]
        if path == failed_path:
            await route.fulfill(status=503, json={"detail": diagnostic})
        elif path == "/api/auth/status":
            await route.fulfill(
                json={
                    "channels": {
                        key: {
                            "channel": key,
                            "channel_label": "本机授权",
                            "user_state": "authorized",
                            "user_state_label": "已授权",
                        }
                        for key in ("video", "douyin")
                    }
                }
            )
        else:
            await route.fulfill(status=202, json={"id": "failed-request"})

    await page.route("**/api/auth/**", auth)
    await page.evaluate("""Object.defineProperty(navigator, 'clipboard', {value: {
      writeText: async text => { window.copiedAuthDiagnostic = text; }
    }});""")
    await page.evaluate("window.Douku.navigate('/settings/auth')")
    if failure != "status":
        await page.locator("#auth-video-card").get_by_role("button", name="重新授权").click()
    panel = page.locator("#auth-session")
    await expect(panel).to_contain_text("请稍后重试")
    await expect(panel).not_to_contain_text("Playwright")
    await expect(page.locator("#toast")).not_to_contain_text("ECONNRESET")
    await panel.get_by_role("button", name="复制诊断信息").click()
    assert diagnostic in await page.evaluate("window.copiedAuthDiagnostic")


async def test_search_keeps_selected_image_note_source(ui):
    page, service, client, _calls = ui
    note = service.config.vault_path / "wiki" / "sources" / "图文_2001.md"
    note.write_text(
        "---\n"
        "type: source\n"
        'video_id: "2001"\n'
        "title: 图文笔记\n"
        "source_kind: image_note\n"
        "tags: []\n"
        "captured: 2026-09-30T09:00:00+08:00\n"
        "---\n# 图文笔记\n\n正文。\n",
        encoding="utf-8",
    )
    library = client.get("/api/library").json()["items"]
    assert any(
        item["title"] == "图文笔记" and item["source_kind"] == "image_note" for item in library
    )
    search = client.get("/api/library", params={"q": "测试文章"}).json()["items"]
    assert search
    assert all(item["source_kind"] != "image_note" for item in search)
    await page.goto("http://testserver/?q=测试文章&source=image_note")
    tab = page.locator(".source-tab[data-source='image_note']")
    await expect(tab).to_be_visible()
    await expect(tab).to_have_attribute("aria-pressed", "true")
    assert "source=image_note" in page.url


@pytest.mark.parametrize("image_count", [1, 3])
@pytest.mark.parametrize(("width", "theme"), [(1440, "light"), (960, "dark")])
async def test_image_note_body_shows_all_originals_in_order(
    ui, tmp_path, image_count, width, theme
):
    from scripts.verify_readwise_reader import png

    page, service, client, _calls = ui
    await page.set_viewport_size({"width": width, "height": 1000})
    image_dir = service.config.vault_path / "raw" / "images" / "2001"
    image_dir.mkdir(parents=True)
    for index in range(1, image_count + 1):
        (image_dir / f"{index:03d}.png").write_bytes(png(400, 800))
    images = [f"![抖音图文第 {i} 张](../../raw/images/2001/{i:03d}.png)"
              for i in range(1, image_count + 1)]
    body = f"# 图文笔记\n\n{images[0]}\n\n## 一句话\n\n图片内容摘要。\n"
    if image_count > 1:
        body += "\n## 原图\n\n" + "\n".join(images[1:]) + "\n"
    body += "\n## 来源\n\n[打开原作品](https://www.douyin.com/note/2001)\n"
    source = service.config.vault_path / "wiki" / "sources" / "图文_2001.md"
    source.write_text(
        "---\ntype: source\nvideo_id: '2001'\nsource_kind: image_note\n"
        "cover_image: raw/images/2001/001.png\n---\n" + body,
        encoding="utf-8",
    )
    client.get("/api/library")
    await page.goto("http://testserver/articles/dy-2001")
    await page.evaluate("theme => document.documentElement.dataset.theme = theme", theme)
    figures = page.locator(".article-body .article-original-image")
    await expect(figures).to_have_count(image_count)
    await expect(figures.locator("figcaption")).to_have_text(
        [f"{i} / {image_count}" for i in range(1, image_count + 1)]
    )
    await expect(page.locator(".article-body > h2")).to_have_text(
        ["Summary", "Original Images", "Sources"]
    )
    await expect(page.locator("#reader-toc")).to_contain_text("Original Images")
    for index in range(image_count):
        image = figures.nth(index).locator("img")
        await image.scroll_into_view_if_needed()
        await expect(image).to_have_attribute("loading", "lazy")
        await expect(image).to_have_attribute("alt", f"抖音图文第 {index + 1} 张")
        await expect(image).to_have_attribute("src", f"/media/raw/images/2001/{index + 1:03d}.png")
        await expect(image).to_have_js_property("naturalWidth", 400)
        assert await image.evaluate("img => Math.abs(img.height / img.width - 2) < 0.01")
        await expect(figures.nth(index).locator("a")).to_have_attribute("target", "_blank")
    await expect(page.locator(".article-body img")).to_have_count(image_count)
    original_images_link = page.locator("#reader-toc").get_by_role(
        "link", name="Original Images", exact=True
    )
    await original_images_link.click()
    await page.keyboard.press("j")
    await expect(page.locator(".article-original-image.is-reading-focus")).to_have_count(1)
    await page.screenshot(path=str(tmp_path / f"original-images-{image_count}-{width}-{theme}.png"))
    assert source.read_text(encoding="utf-8").endswith(body)


async def test_reanalyze_confirm_explains_without_repeating_the_title(ui):
    page, _service, _client, calls = ui
    await page.goto("http://testserver/articles/dy-123")
    await page.locator("#reader-actions .entry-more").click()
    await page.get_by_role("menuitem", name="重新分析", exact=True).click()
    await expect(page.locator("#destructive-title")).to_have_text("重新交给 Agent 分析这篇？")
    description = page.locator("#destructive-description")
    await expect(description).to_have_text("确认后任务进入队列，你仍留在这篇文章。")
    await expect(description).not_to_contain_text("重新交给 Agent 分析这篇")
    await expect(page.locator("#confirm-destructive")).to_have_text("重新交给 Agent 分析这篇")
    await page.keyboard.press("Escape")
    await page.wait_for_function("!document.querySelector('#destructive-dialog').open")
    assert not any(path.endswith("/reanalyze") for _method, path in calls)


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
    await page.locator("#tags-tab-aliases").click()
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
    page, service, client, calls = ui
    source_path = service.config.vault_path / service.database.get_entry("dy-123").source_path
    source_text = source_path.read_text().replace(
        "## 来源",
        "## 时间轴图解\n\n### ▶ 00:01　一、整理资料\n\n章节原文保留。\n\n"
        "## 内容卡片 · 清单\n\n- 清单项目保留。\n\n"
        "```markdown\n## 一句话\n```\n\n## 下一步\n\n- 按原文执行下一项行动。\n\n## 来源",
    )
    source_path.write_text(source_text)
    client.app.state.catalog.refresh()
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
    await expect(page.locator(".article-body h2.article-section-title")).to_have_text(
        ["Summary", "Key Takeaways", "Chapters", "Quick Reference · List", "Next Steps", "Sources"]
    )
    await expect(page.locator("#reader-toc .toc-h2")).to_have_text(
        [
            "灵感", "Summary", "Key Takeaways", "Chapters", "Quick Reference · List",
            "Next Steps", "逐字稿 · 0段", "关键帧 · 0张", "Sources",
        ]
    )
    await expect(page.locator(".article-transcript .article-section-title")).to_have_text(
        "Transcript"
    )
    await expect(page.locator(".article-keyframes .article-section-title")).to_have_text(
        "Video Snapshots"
    )
    await expect(page.locator(".article-body pre")).to_have_text("## 一句话\n")
    assert source_path.read_text() == source_text
    for selector, expected in (
        (".article-body h2.article-section-title", ["Inter", "20px", "600", "28px"]),
        (".article-transcript > summary", ["Inter", "16px", "500", "24px"]),
        (".article-keyframes > summary", ["Inter", "16px", "500", "24px"]),
    ):
        typography = await page.locator(selector).first.evaluate("""node => {
            const style = getComputedStyle(node);
            return [style.fontFamily.split(',')[0].replaceAll('"', ''),
                style.fontSize, style.fontWeight, style.lineHeight];
        }""")
        assert typography == expected, typography
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
