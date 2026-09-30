"""Exercise responsive shell, routing and focus against an isolated SPA fixture."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from douyin_wiki.webapp.app import create_app  # noqa: E402
from tests.test_web import FakeChatProvider, _web_fixture, local_client  # noqa: E402


async def verify():
    out = ROOT / "docs/screenshots/redesign/phase-1"
    out.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="douku-shell-check-") as tmp:
        config, service = _web_fixture(Path(tmp))
        app = create_app(
            config, service=service, chat_provider=FakeChatProvider(), start_watcher=False
        )
        with local_client(app) as client:
            async with async_playwright() as p:
                browser = await p.chromium.launch()
                page = await browser.new_page(
                    viewport={"width": 1440, "height": 900}, device_scale_factor=2
                )
                page.set_default_timeout(8000)
                errors, external, failed = [], [], []
                page.on("pageerror", lambda error: errors.append(str(error)))

                async def route(request_route):
                    request = request_route.request
                    url = urlsplit(request.url)
                    if url.hostname != "testserver":
                        external.append(request.url)
                        await request_route.abort()
                        return
                    if url.path.endswith("/events"):
                        await request_route.fulfill(
                            content_type="text/event-stream", body=": fixture\n\n"
                        )
                        return
                    response = client.request(
                        request.method,
                        url.path + (f"?{url.query}" if url.query else ""),
                        content=request.post_data,
                        headers={
                            "content-type": request.headers.get("content-type", "application/json")
                        },
                    )
                    if response.status_code >= 500:
                        failed.append((url.path, response.status_code))
                    await request_route.fulfill(
                        status=response.status_code,
                        headers={"content-type": response.headers.get("content-type", "")},
                        body=response.content,
                    )

                await page.route("**/*", route)

                async def navigate(path):
                    await page.evaluate("path => window.Douku.navigate(path)", path)
                    await page.wait_for_function(
                        "() => document.querySelectorAll('.view-header #chat-toggle').length === 1"
                        " && document.querySelector('#chat-toggle').getClientRects().length > 0"
                    )

                async def press(key):
                    await page.keyboard.press(key)
                    await page.wait_for_timeout(30)

                async def fresh(width):
                    await page.set_viewport_size({"width": width, "height": 900})
                    await page.goto("http://testserver/")
                    await page.wait_for_selector("#library-results:not(.hidden)")
                    await page.evaluate("document.fonts.ready")

                async def assert_header_controls(*, drawer=False, sidebar_open=True):
                    assert await page.locator("#nav-toggle").is_visible() == drawer
                    assert await page.locator("#sidebar-restore").is_visible() == (
                        not drawer and not sidebar_open
                    )
                    assert await page.locator("#sidebar-collapse").is_visible() == (
                        not drawer and sidebar_open
                    )

                async def assert_shell(width, *, sidebar_open, panel_open):
                    await assert_header_controls(sidebar_open=sidebar_open)
                    assert await page.locator("#library-sidebar").evaluate("e => e.inert") == (
                        not sidebar_open
                    )
                    assert await page.locator("#side-panel").evaluate("e => e.inert") == (
                        not panel_open
                    )
                    main = await page.locator("#main-content").bounding_box()
                    left = 240 if sidebar_open else 0
                    right = 360 if panel_open and width >= 1280 else 0
                    assert main and main["x"] == left and main["width"] == width - left - right, (
                        width,
                        sidebar_open,
                        panel_open,
                        main,
                    )

                await fresh(1440)
                await assert_header_controls()
                widths = await page.evaluate("""() => Object.fromEntries(
                  ['library-sidebar','main-content','side-panel'].map(id =>
                    [id, document.getElementById(id).getBoundingClientRect().width]))""")
                assert widths == {
                    "library-sidebar": 240,
                    "main-content": 840,
                    "side-panel": 360,
                }, widths
                assert (
                    await page.locator("#panel-tab-info").get_attribute("aria-selected") == "true"
                )
                await page.click("#panel-tab-chat")
                await page.reload()
                await page.wait_for_selector("#panel-tab-chat[aria-selected=true]")
                await page.click("#panel-tab-chat")
                await press("ArrowLeft")
                assert (
                    await page.locator("#panel-tab-info").get_attribute("aria-selected") == "true"
                )
                await page.locator("#main-content").focus()
                await press("[")
                assert await page.locator("#library-sidebar").evaluate("e => e.inert")
                await press("]")
                assert await page.locator("#side-panel").evaluate("e => e.inert")
                await page.reload()
                await page.wait_for_selector("#library-results:not(.hidden)")
                assert await page.locator("#app-shell").evaluate(
                    "e => e.classList.contains('is-sidebar-collapsed')"
                    " && e.classList.contains('is-panel-collapsed')"
                )
                await page.locator("#main-content").focus()
                await press("[")
                await press("]")
                await navigate("/settings")
                assert await page.locator("#side-panel").evaluate("e => e.inert")
                assert (
                    await page.evaluate("localStorage.getItem('douyin-wiki.chat-collapsed')")
                    == "false"
                )
                await navigate("/")
                assert not await page.locator("#side-panel").evaluate("e => e.inert")
                # Every route moves the same controls to its own header.
                for path, view in [
                    ("/topics", "topics"),
                    ("/trash", "trash"),
                    ("/imports", "imports"),
                    ("/imports/single", "imports-single"),
                    ("/imports/creators", "imports-creators"),
                    ("/imports/favorites", "imports-favorites"),
                    ("/jobs", "jobs"),
                    ("/settings/auth", "auth"),
                    ("/settings/analysis", "analysis"),
                    ("/settings/model", "model"),
                    ("/settings/system", "system"),
                    ("/no-such-page", "not-found"),
                ]:
                    await navigate(path)
                    await page.wait_for_selector(f"#{view}-view:not(.hidden) #chat-toggle")
                    await assert_header_controls()
                await navigate("/")
                await page.locator("#main-content").focus()
                await press("?")
                assert await page.locator("#shortcuts-dialog").evaluate("e => e.open")
                await press("Escape")
                await press("g")
                await press("j")
                await page.wait_for_selector("#jobs-view:not(.hidden)")
                await page.locator("#main-content").focus()
                await press("Control+i")
                await page.wait_for_selector("#imports-popover:not(.hidden)")
                await press("ArrowDown")
                assert (
                    await page.evaluate("document.activeElement.dataset.route") == "/imports/single"
                )
                await press("Enter")
                await page.wait_for_selector("#imports-single-view:not(.hidden)")
                await navigate("/")
                for theme in ["light", "dark"]:
                    await page.select_option("[data-theme-select]", theme)
                    await page.evaluate("document.activeElement?.blur()")
                    await page.mouse.move(0, 0)
                    await page.screenshot(path=out / f"library-{theme}.png", animations="disabled")
                await navigate("/articles/dy-123")
                await page.wait_for_selector(".article-body h2")
                await assert_header_controls()
                assert await page.locator("#panel-tabs [role=tab]").count() == 3
                await page.click("#panel-tab-inspiration")
                await press("ArrowRight")
                assert await page.locator("#chat-panel").is_visible()
                await page.fill("#chat-input", "这篇文章的核心收获是什么？")
                await page.click("#send-button")
                await page.wait_for_function(
                    "() => document.querySelector('.message.assistant') || "
                    "!document.querySelector('#chat-persistent-error').classList.contains('hidden')"
                )
                await page.wait_for_function(
                    "() => !document.querySelector('#send-button').disabled"
                )
                answer = await page.locator("#chat-messages").inner_text()
                assert "这是测试回答" in answer, (
                    answer,
                    await page.locator("#chat-persistent-error").inner_text(),
                    errors,
                    failed,
                )
                for theme in ["light", "dark"]:
                    await page.select_option("[data-theme-select]", theme)
                    await page.evaluate("document.activeElement?.blur()")
                    await page.mouse.move(0, 0)
                    await page.screenshot(path=out / f"article-{theme}.png", animations="disabled")
                await page.locator("#main-content").focus()
                await press("Escape")
                await page.wait_for_selector("#library-view:not(.hidden)")

                for width in [1280, 1100, 800, 390]:
                    await fresh(width)
                    await assert_header_controls(drawer=width < 960)
                    assert await page.evaluate(
                        "document.documentElement.scrollWidth <= innerWidth"
                    ), width
                    if width == 1280:
                        assert (
                            await page.locator("#main-content").evaluate(
                                "e => e.getBoundingClientRect().width"
                            )
                            == 680
                        )
                        continue
                    assert await page.locator("#side-panel").evaluate("e => e.inert")
                    before = await page.locator("#main-content").bounding_box()
                    await page.click("#chat-toggle")
                    await page.wait_for_timeout(260)
                    after = await page.locator("#main-content").bounding_box()
                    assert before == after
                    if width == 1100:
                        assert not await page.locator("#drawer-backdrop").is_visible()
                        await page.click("#view-title")
                        assert await page.locator("#side-panel").evaluate("e => e.inert")
                        await page.click("#chat-toggle")
                    else:
                        assert await page.locator("#main-content").evaluate("e => e.inert")
                        # Focus trap covers tabs and close button, excludes hidden chat controls.
                        await page.locator("#info-pane .info-actions a").focus()
                        await press("Tab")
                        assert await page.evaluate("document.activeElement.id") == "panel-tab-info"
                        await press("Shift+Tab")
                        assert await page.evaluate(
                            "document.activeElement.closest('#info-pane .info-actions') !== null"
                        )
                        await page.click("#panel-tab-chat")
                        await page.locator("#chat-input").focus()
                        await press("]")
                        assert not await page.locator("#side-panel").evaluate("e => e.inert")
                        await page.click("#panel-tab-info")
                    await press("Escape")
                    assert await page.evaluate("document.activeElement.id") == "chat-toggle"
                    assert not await page.locator("#main-content").evaluate("e => e.inert")
                    if width < 960:
                        await page.click("#nav-toggle")
                        await page.wait_for_timeout(260)
                        await page.locator("#sidebar-search").focus()
                        await press("?")
                        await press("Escape")
                        assert await page.evaluate("document.activeElement.id") == "sidebar-search"
                        await press("Escape")
                        assert await page.evaluate("document.activeElement.id") == "nav-toggle"
                    if width == 390:
                        await page.click("#search-toggle")
                        assert await page.locator("#search-input").is_visible()
                        await press("Escape")
                        await page.click("#topic-select-toggle")
                        await page.wait_for_selector("#library-selection-bar:not(.hidden)")
                        await page.locator(
                            "#library-results a[href='/articles/dy-123']"
                        ).first.click()
                        assert await page.locator("#library-selection-next").is_enabled()
                        await page.click("#library-selection-next")
                        assert await page.locator("#topic-dialog").evaluate("e => e.open")
                        await press("Escape")
                        await page.evaluate("document.activeElement?.blur()")
                        await page.mouse.move(0, 0)
                        footer = await page.locator("#library-selection-bar").bounding_box()
                        assert footer and footer["y"] + footer["height"] <= 900
                        if await page.locator("#toast.show").count():
                            notice = await page.locator("#toast").bounding_box()
                            assert notice and notice["y"] + notice["height"] <= footer["y"]
                        await page.screenshot(
                            path=out / "mobile-selection.png", animations="disabled"
                        )
                        await page.click("#library-selection-next")
                        await page.fill("#topic-title-input", "阅读清单")
                        await page.click("#confirm-topic")
                        await page.wait_for_selector("#topic-view:not(.hidden) #chat-toggle")
                        assert await page.locator("#panel-tabs [role=tab]").count() == 2
                        assert (
                            await page.locator("#panel-tab-chat").get_attribute("aria-selected")
                            == "true"
                        )

                # Check all four collapse states at both sides of the desktop breakpoint.
                collapse_widths = [1440, 1280, 1279, 1100, 960]
                for width in collapse_widths:
                    await fresh(width)
                    await page.evaluate("window.Douku.panel.close()")
                    await assert_shell(width, sidebar_open=True, panel_open=False)
                    await page.click("#chat-toggle")
                    await assert_shell(width, sidebar_open=True, panel_open=True)
                    await page.click("#sidebar-collapse")
                    await assert_shell(width, sidebar_open=False, panel_open=True)
                    await page.click("#chat-close")
                    await assert_shell(width, sidebar_open=False, panel_open=False)
                    # Reopening an overlay must not restore an empty sidebar track.
                    await page.click("#chat-toggle")
                    await assert_shell(width, sidebar_open=False, panel_open=True)
                    await page.click("#chat-close")
                    await assert_shell(width, sidebar_open=False, panel_open=False)
                    if width == 1100:
                        for theme in ["light", "dark"]:
                            await page.evaluate("theme => window.DoukuTheme.set(theme)", theme)
                            await page.evaluate("document.activeElement?.blur()")
                            await page.mouse.move(0, 0)
                            await page.screenshot(
                                path=out / f"collapsed-sidebar-1100-{theme}.png",
                                animations="disabled",
                            )
                    if width != collapse_widths[-1]:
                        await page.click("#sidebar-restore")
                        await assert_shell(width, sidebar_open=True, panel_open=False)

                # Resize from a collapsed desktop sidebar into a mobile drawer and back.
                for width in [800, 390]:
                    await page.set_viewport_size({"width": width, "height": 900})
                    await page.wait_for_function(
                        "() => document.querySelector('#nav-toggle').getClientRects().length > 0"
                    )
                    await assert_header_controls(drawer=True, sidebar_open=False)
                    await page.click("#nav-toggle")
                    assert not await page.locator("#library-sidebar").evaluate("e => e.inert")
                    assert await page.locator("#main-content").evaluate("e => e.inert")
                    await press("Escape")
                    assert await page.evaluate("document.activeElement.id") == "nav-toggle"
                    await page.set_viewport_size({"width": 1440, "height": 900})
                    await page.wait_for_function(
                        "() => document.querySelector('#sidebar-restore')"
                        ".getClientRects().length > 0"
                    )
                    await assert_shell(1440, sidebar_open=False, panel_open=False)
                await page.click("#sidebar-restore")
                await assert_shell(1440, sidebar_open=True, panel_open=False)
                assert not errors, errors
                assert not failed, failed
                assert not external, external
                report = {
                    "desktopWidths": widths,
                    "responsiveWidths": [1280, 1100, 800, 390],
                    "collapseWidths": collapse_widths,
                    "checks": [
                        "tab preference",
                        "collapse preference",
                        "four collapse states and overlay geometry",
                        "header control visibility and mobile resize",
                        "route headers",
                        "shortcuts",
                        "overlay dismissal",
                        "drawer focus trap",
                        "mobile topic selection",
                        "article chat send",
                        "topic creation and tabs",
                    ],
                    "pageErrors": errors,
                    "failedRequests": failed,
                    "externalRequests": external,
                }
                (out / "verification.json").write_text(
                    json.dumps(report, ensure_ascii=False, indent=2) + "\n"
                )
                print(json.dumps(report, ensure_ascii=False))
                await browser.close()


if __name__ == "__main__":
    asyncio.run(verify())
