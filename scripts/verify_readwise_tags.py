"""Verify real tag mutations, responsive navigation and light/dark screenshots."""

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
from tests.support.web import _web_fixture, local_client  # noqa: E402
from tests.test_tags import source  # noqa: E402


async def verify():
    out = ROOT / "docs/screenshots/redesign/tags"
    out.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="douku-tags-check-") as tmp:
        config, service = _web_fixture(Path(tmp))
        names = [
            "知识管理",
            "AI 代理",
            "AI Agent",
            "手冲咖啡",
            "效率工具",
            "笔记",
            "产品设计",
            "阅读",
            "模型评测",
            "信息整理",
            "学习方法",
        ]
        for index in range(12):
            source(
                service,
                str(1000 + index),
                ["知识管理", names[index % len(names)], "咖啡"],
                managed=index < 2,
            )
        source(service, "1100", ["AI Agent", "AI 代理"], managed=True)
        app = create_app(config, service=service, start_watcher=False)
        with local_client(app) as client:
            async with async_playwright() as p:
                browser = await p.chromium.launch()
                page = await browser.new_page(
                    viewport={"width": 1440, "height": 1100}, device_scale_factor=2
                )
                page.set_default_timeout(8000)
                errors, failed, external = [], [], []
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
                await page.goto("http://testserver/")
                await page.wait_for_selector("#sidebar-tags .sidebar-tag")
                assert await page.locator("#sidebar-tags .sidebar-tag").count() == 8
                assert await page.locator("#sidebar-tags .nav-count").first.inner_text() == "13"
                for theme in ["light", "dark"]:
                    await page.evaluate("theme => window.DoukuTheme.set(theme)", theme)
                    await page.evaluate("document.fonts.ready")
                    await page.locator("#library-sidebar").screenshot(
                        path=out / f"sidebar-{theme}.png", animations="disabled"
                    )
                await page.click("#all-tags-button")
                await page.wait_for_selector("#tags-view:not(.hidden) #tags-rows tr")
                assert await page.locator("#panel-tabs [role=tab]").count() == 1
                counts = await page.locator("#tags-rows .tag-count").all_text_contents()
                assert [int(value) for value in counts] == sorted(
                    [int(value) for value in counts], reverse=True
                )
                await page.locator("#main-content").focus()
                await page.keyboard.press("/")
                assert await page.evaluate("document.activeElement.id") == "tags-search"
                await page.fill("#tags-search", "AI")
                assert await page.locator("#tags-rows tr").count() == 2
                await page.fill("#tags-search", "")

                def row(name):
                    return page.locator("#tags-rows tr").filter(
                        has=page.get_by_role("link", name=name, exact=True)
                    )

                async def idle():
                    await page.wait_for_function(
                        "() => !document.querySelector('#tags-rows .tag-pin')?.disabled"
                    )

                for name in ["咖啡", "AI 代理"]:
                    await row(name).locator(".tag-pin").click()
                    await idle()
                assert await page.locator("#sidebar-tags .tag-name").all_text_contents() == [
                    "咖啡",
                    "AI 代理",
                ]
                await row("AI 代理").locator("[data-tag-action=up]").click()
                await idle()
                assert await page.locator("#sidebar-tags .tag-name").all_text_contents() == [
                    "AI 代理",
                    "咖啡",
                ]
                await row("咖啡").drag_to(row("AI 代理"))
                await idle()
                assert await page.locator("#sidebar-tags .tag-name").all_text_contents() == [
                    "咖啡",
                    "AI 代理",
                ]
                await row("咖啡").locator("input").check()
                assert "# 咖啡" in await page.locator("#info-pane").inner_text()
                assert await page.locator("#info-pane .tag-recent-list li").count() == 5
                await page.wait_for_function(
                    "() => !document.querySelector('#toast').classList.contains('show')"
                )
                for theme in ["light", "dark"]:
                    await page.evaluate("theme => window.DoukuTheme.set(theme)", theme)
                    await page.evaluate("document.activeElement?.blur()")
                    await page.mouse.move(0, 0)
                    await page.screenshot(path=out / f"tags-{theme}.png", animations="disabled")
                await row("咖啡").locator("input").uncheck()
                await row("AI Agent").locator("input").check()
                await row("AI 代理").locator("input").check()
                assert "已选 2 个" in await page.locator("#info-pane").inner_text()
                await page.click("#tags-merge")
                await page.select_option("#tag-target", "AI 代理")
                await page.click("#tag-edit-submit")
                await page.wait_for_function(
                    "() => !document.querySelector('#tag-edit-dialog').open"
                )
                assert await row("AI Agent").count() == 0
                assert service.tags.resolve(["AI Agent"]) == ["AI 代理"]
                await page.click("#tags-rename")
                await page.fill("#tag-new-name", "智能体")
                await page.click("#tag-edit-submit")
                await page.wait_for_function(
                    "() => !document.querySelector('#tag-edit-dialog').open"
                )
                assert await row("智能体").count() == 1
                assert service.tags.resolve(["AI Agent", "AI 代理"]) == ["智能体"]
                await page.click("#tags-delete")
                await page.locator("#destructive-dialog button[value=cancel]").last.click()
                assert await row("智能体").count() == 1
                await page.click("#tags-delete")
                await page.click("#confirm-destructive")
                await page.wait_for_function(
                    "() => !document.querySelector('#tags-rows .tag-pin')?.disabled"
                )
                assert await row("智能体").count() == 0
                assert client.get("/api/articles/dy-1100").status_code == 200
                assert await page.locator(".tag-duplicate-group").count() > 0
                await (
                    page.locator(".tag-duplicate-group")
                    .filter(has_text="手冲咖啡")
                    .get_by_role("button", name="合并", exact=True)
                    .click()
                )
                await idle()
                assert await row("手冲咖啡").count() == 0
                await page.click("[data-tag-sort=name]")
                assert await page.locator("[data-tag-sort=name]").inner_text() == "标签名 ↑"
                await page.click("[data-tag-sort=last_used_at]")
                assert (
                    await page.locator("[data-tag-sort=last_used_at]").inner_text()
                    == "最近使用时间 ↓"
                )
                await row("咖啡").get_by_role("link", name="咖啡", exact=True).click()
                await page.wait_for_selector("#library-view:not(.hidden)")
                assert await page.locator("#view-title").inner_text() == "# 咖啡 · 13 条"
                assert await page.locator("#tag-manage-link").is_visible()
                assert await page.locator(".nav-item[data-section].active").count() == 0
                await page.click("#tag-manage-link")
                await page.wait_for_selector("#tags-view:not(.hidden) #tags-rows tr")
                for width in [1100, 800, 390, 1440]:
                    await page.set_viewport_size({"width": width, "height": 900})
                    await page.wait_for_timeout(100)
                    assert await page.evaluate(
                        "document.documentElement.scrollWidth <= innerWidth"
                    ), width
                    await page.locator("#main-content").focus()
                    await page.keyboard.press("/")
                    assert await page.evaluate("document.activeElement.id") == "tags-search"
                    if width == 390:
                        await page.screenshot(path=out / "tags-mobile.png", animations="disabled")
                await page.reload()
                await page.wait_for_selector("#tags-view:not(.hidden) #tags-rows tr")
                assert await page.locator("#sidebar-tags .tag-name").all_text_contents() == ["咖啡"]
                assert not await page.locator("#reading-fonts").count()
                assert not errors, errors
                assert not failed, failed
                assert not external, external
                report = {
                    "checks": [
                        "sidebar cap and counts",
                        "pin and reorder with drag",
                        "search and sorts",
                        "single and multi selection panel",
                        "rename and merge aliases",
                        "confirmed delete preserves article",
                        "duplicate candidate click",
                        "tag filter header and manage link",
                        "responsive and reload",
                    ],
                    "widths": [1440, 1100, 800, 390],
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
