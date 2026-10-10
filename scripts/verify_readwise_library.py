"""Verify library navigation and mutations using a temporary Vault, without external calls."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from douyin_wiki.models import EntryRecord, RetentionPolicy  # noqa: E402
from douyin_wiki.webapp.app import create_app  # noqa: E402
from tests.support.web import _web_fixture, local_client  # noqa: E402


def populate(config, service):
    now = datetime.now(UTC)
    titles = [
        "把零散资料整理成可复用的知识",
        "为什么大模型竞争集中于中美：一套产业链解释",
        "用三步建立自己的阅读与复盘流程",
        "向自然借一张配色表：纸、树叶与墨水",
        "笔记越多，越需要一条清楚的线索",
        "如何用问题串联一组视频与文章",
        "把一次灵感变成持续的创作练习",
        "工作台整理：减少切换，留出思考空间",
        "只读资料：从 Markdown 开始积累",
    ]
    source = config.vault_path / "wiki/sources/测试文章_123.md"
    source.write_text(source.read_text().replace("2026-08-23T09:00:00+08:00", now.isoformat()))
    for index, title in enumerate(titles, 1):
        work_id = str(200 + index)
        kind = "image_note" if index % 2 else "video"
        captured = now - timedelta(hours=index)
        author = f"笔记作者 {index}"
        summary = "从记录、回顾到使用，让每一条值得保存的资料都有机会进入下一次思考。"
        relative = f"wiki/sources/资料_{work_id}.md"
        (config.vault_path / relative).write_text(
            f"---\ntype: source\nvideo_id: '{work_id}'\ntitle: {title}\n"
            f"author: {author}\npublished: '{captured.isoformat()}'\n"
            f"captured: '{captured.isoformat()}'\nsource_kind: {kind}\n"
            "content_type: tutorial\nstatus: 正常\ntags: [知识管理, 阅读]\n"
            f"original_url: https://www.douyin.com/video/{work_id}\n---\n"
            f"# {title}\n\n## 一句话\n\n{summary}\n\n## 核心收获\n\n- 留出思考的空间。\n"
        )
        if index == 9:
            continue  # A readable source with no database entry.
        service.database.upsert_entry(
            EntryRecord(
                id=f"dy-{work_id}",
                video_id=work_id,
                title=title,
                original_url=f"https://www.douyin.com/video/{work_id}",
                canonical_url=f"https://www.douyin.com/video/{work_id}",
                raw_path="raw/测试记录.md",
                source_path=relative,
                status="active",
                media_status="present",
                retention=RetentionPolicy.KEEP,
                summary=summary,
                tags=["知识管理", "阅读"],
                created_at=captured,
                updated_at=captured,
            ),
            {
                "metadata": {
                    "video_id": work_id,
                    "title": title,
                    "author": author,
                    "source_kind": kind,
                },
                "analysis": {
                    "analysis_version": 2,
                    "title": title,
                    "one_liner": summary,
                    "takeaways": ["留出思考的空间。"],
                    "chapters": [],
                    "knowledge_atoms": [],
                },
            },
        )


async def verify():
    out = (
        Path(
            os.environ.get(
                "DOUKU_VERIFY_OUTPUT_DIR", str(ROOT / "docs/screenshots/mcp-gateway-simplification")
            )
        )
        / "library"
    )
    out.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="douku-library-check-") as tmp:
        config, service = _web_fixture(Path(tmp))
        populate(config, service)
        app = create_app(config, service=service, start_watcher=False)
        with local_client(app) as client:
            async with async_playwright() as p:
                browser = await p.chromium.launch()
                page = await browser.new_page(
                    viewport={"width": 1440, "height": 900}, device_scale_factor=2
                )
                page.set_default_timeout(8000)
                errors, external, mutations = [], [], []
                mode = "normal"
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
                    if url.path == "/api/library" and mode == "loading":
                        await asyncio.sleep(0.5)
                    if url.path == "/api/library" and mode == "error":
                        await request_route.fulfill(
                            status=503,
                            content_type="application/json",
                            body='{"detail":"模拟载入失败"}',
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
                    if request.method in {"PUT", "POST", "DELETE"}:
                        mutations.append((url.path, response.status_code, request.post_data))
                    if url.path == "/api/library" and mode == "empty":
                        await request_route.fulfill(
                            content_type="application/json",
                            body=json.dumps(
                                {
                                    "items": [],
                                    "facets": {"authors": [], "tags": [], "content_types": []},
                                }
                            ),
                        )
                        return
                    await request_route.fulfill(
                        status=response.status_code,
                        body=response.content,
                        headers={"content-type": response.headers.get("content-type", "")},
                    )

                await page.route("**/*", route)

                async def fresh(path="/"):
                    await page.goto("http://testserver" + path)
                    await page.wait_for_selector(
                        "#library-results:not(.hidden), #empty-results:not(.hidden)"
                    )
                    await page.evaluate("document.fonts.ready")

                async def key(value):
                    await page.keyboard.press(value)
                    await page.wait_for_timeout(30)

                async def focused():
                    return await page.locator("#library-results .is-focused").get_attribute(
                        "data-entry-id"
                    )

                await fresh()
                assert await page.locator("#view-list").get_attribute("aria-pressed") == "true"
                assert await page.locator("#library-results .library-item").count() == 10
                assert await page.evaluate("document.activeElement.tagName") == "BODY"
                dimensions = await page.locator(".library-item").first.evaluate("""e => {
                    const image = e.querySelector('.item-thumbnail').getBoundingClientRect();
                    return [e.getBoundingClientRect().height, image.width, image.height];
                }""")
                assert dimensions == [88, 48, 64], dimensions
                assert await focused() == "dy-123"
                await page.locator("#main-content").focus()
                await key("j")
                assert await focused() == "dy-201"
                assert await page.locator("#info-pane").get_attribute("data-entry-id") == "dy-201"
                assert await page.locator(".library-item-link[tabindex='0']").count() == 1
                await page.locator(".library-item[data-entry-id='dy-201']").hover()
                assert (
                    await page.locator(".library-item[data-entry-id='dy-201'] .item-date").evaluate(
                        "e => getComputedStyle(e).visibility"
                    )
                    == "hidden"
                )
                assert (
                    await page.locator(
                        ".library-item[data-entry-id='dy-201'] .entry-more"
                    ).evaluate("e => e.getBoundingClientRect().width")
                    == 28
                )
                await key("ArrowDown")
                assert await focused() == "dy-202"
                await key("k")
                await key("ArrowUp")
                assert await focused() == "dy-123"
                await key("f")
                await page.wait_for_selector(
                    ".library-item[data-entry-id='dy-123'] .favorite-button.active"
                )
                assert "媒体会一直保留在本机。" in await page.locator("#info-pane").inner_text()
                saved_entry = service.database.get_entry("dy-123")
                assert saved_entry.favorite is True
                assert saved_entry.retention == RetentionPolicy.KEEP
                await key("End")
                assert await focused() == "dy-209"
                before = len(mutations)
                await key("f")
                await key("x")
                assert len(mutations) == before
                assert await page.locator("#library-selection-bar").is_hidden()
                await key("Enter")
                await page.wait_for_selector("#article-view:not(.hidden)")
                assert "只读 Markdown" in await page.locator("#info-pane").inner_text()
                assert not await page.locator("#info-pane .favorite-button").count()
                await page.locator("#main-content").focus()
                await key("Escape")
                await page.wait_for_selector("#library-view:not(.hidden)")
                await page.locator("#main-content").focus()
                await key("Home")
                await key("Shift+f")
                assert await page.locator("#filter-popover").is_visible()
                assert (
                    await page.locator("#filter-popover").evaluate(
                        "e => e.getBoundingClientRect().width"
                    )
                    == 320
                )
                assert not await page.locator("#source-filters").count()
                await page.fill("#author-filter-search", "作者 3")
                assert await page.locator("#author-filters label").count() == 1
                await page.locator("#author-filters input").check()
                assert await page.locator(".library-item").count() == 1
                assert await page.evaluate("document.activeElement.type") == "checkbox"
                await page.click("#clear-filters")
                await key("Escape")
                await page.click("[data-source=video]")
                assert "source=video" in page.url
                assert await page.locator(".library-item").count() == 5
                await page.click("[data-source='']")
                await page.locator("#main-content").focus()
                await key("v")
                assert await page.locator("#view-gallery").get_attribute("aria-pressed") == "true"
                await page.locator("#main-content").focus()
                await key("Home")
                await key("ArrowRight")
                assert await focused() == "dy-201"
                await key("ArrowDown")
                assert await focused() == "dy-205"  # Four columns in the 840px main pane.
                await page.reload()
                await page.wait_for_selector("#library-results.gallery")
                await page.click("#view-list")
                await page.reload()
                await page.wait_for_selector("#library-results.list")
                await fresh("/?view=gallery")
                assert (
                    await page.evaluate("localStorage.getItem('douyin-wiki.library-view')")
                    == "gallery"
                )
                await page.evaluate("localStorage.setItem('douyin-wiki.ui-version','2')")
                await fresh()
                assert await page.locator("#view-list").get_attribute("aria-pressed") == "true"

                # A menu must dismiss before leaving the page, returning focus to its trigger.
                await page.locator(".library-item").first.hover()
                await page.locator(".entry-more").first.click()
                assert await page.locator("#entry-menu [role=menuitem]").count() == 3
                await key("End")
                assert await page.evaluate("document.activeElement.textContent") == "删除文章"
                await key("Escape")
                assert await page.evaluate(
                    "document.activeElement.classList.contains('entry-more')"
                )
                assert await page.locator("#library-view").is_visible()

                # Preserve three selected sources across source filters, then create a real topic.
                await page.click("#topic-select-toggle")
                await page.locator(".library-item[data-entry-id='dy-123'] input").check()
                await page.locator(".library-item[data-entry-id='dy-201'] input").check()
                await page.locator(".library-item[data-entry-id='dy-202'] input").check()
                assert await page.locator(
                    ".library-item[data-entry-id='dy-209'] input"
                ).is_disabled()
                await page.click("[data-source=image_note]")
                await page.click("#library-selection-next")
                assert "已选择 3 篇" in await page.locator("#topic-selection-summary").inner_text()
                await page.fill("#topic-title-input", "跨筛选阅读清单")
                await page.click("#confirm-topic")
                await page.wait_for_selector("#topic-view:not(.hidden)")
                payload = json.loads(
                    next(
                        body
                        for path, status, body in reversed(mutations)
                        if path == "/api/topics" and status < 300
                    )
                )
                assert set(payload["entry_ids"]) == {"dy-123", "dy-201", "dy-202"}, payload
                assert await page.locator(".topic-source-row").count() == 3

                await fresh()
                for theme in ["light", "dark"]:
                    await page.select_option("[data-theme-select]", theme)
                    await page.evaluate("document.activeElement?.blur()")
                    await page.mouse.move(0, 0)
                    await page.screenshot(path=out / f"library-{theme}.png", animations="disabled")
                await page.click("#view-gallery")
                await page.screenshot(path=out / "gallery-dark.png", animations="disabled")
                await page.evaluate("window.Douku.navigate('/articles/dy-123')")
                await page.wait_for_selector("#article-view:not(.hidden)")
                for theme in ["light", "dark"]:
                    await page.select_option("[data-theme-select]", theme)
                    await page.evaluate("document.activeElement?.blur()")
                    await page.mouse.move(0, 0)
                    await page.screenshot(path=out / f"article-{theme}.png", animations="disabled")

                for width in [1280, 1100, 800, 390, 320]:
                    await page.set_viewport_size({"width": width, "height": 900})
                    await fresh("/?view=list")
                    assert await page.evaluate(
                        "document.documentElement.scrollWidth <= innerWidth"
                    ), width
                    controls = await page.locator(".library-toolbar > *").evaluate_all(
                        """nodes => nodes.map(e => {
                        const r=e.getBoundingClientRect(); return [r.left,r.right];
                    })"""
                    )
                    assert all(left >= 0 and right <= width for left, right in controls), (
                        width,
                        controls,
                    )
                    assert all(
                        controls[i][1] <= controls[i + 1][0] for i in range(len(controls) - 1)
                    ), (width, controls)
                    row = await page.locator(".library-item").first.bounding_box()
                    assert row["height"] == (64 if width < 640 else 88), (width, row)
                    if width == 390:
                        await page.evaluate("window.DoukuTheme.set('light')")
                        await page.click("#topic-select-toggle")
                        await page.locator(".library-item[data-entry-id='dy-123'] input").check()
                        footer = await page.locator("#library-selection-bar").bounding_box()
                        assert footer["y"] + footer["height"] <= 900
                        await page.screenshot(
                            path=out / "mobile-selection.png", animations="disabled"
                        )

                # Opening tag management from mobile navigation must release main inert.
                await page.set_viewport_size({"width": 390, "height": 900})
                await fresh("/?view=list")
                await page.click("#nav-toggle")
                await page.click("#tags-nav")
                assert not await page.locator("#main-content").evaluate("e => e.inert")
                await page.wait_for_selector("#tags-view:not(.hidden) #tags-cards .tag-card")
                assert await page.locator("#tags-search").is_visible()
                await page.locator("#tags-cards .tag-card a").first.click()
                await page.wait_for_selector("#library-view:not(.hidden)")
                assert await page.locator("#view-title").inner_text() != "资料库"
                await page.set_viewport_size({"width": 1440, "height": 900})
                await fresh("/?q=不存在的关键词")
                assert await page.locator("#empty-results").is_visible()
                assert "资料库概览" in await page.locator("#info-pane").inner_text()
                assert "10" in await page.locator(".library-statistics").inner_text()
                await fresh()
                await page.locator(".library-item[data-entry-id='dy-208']").hover()
                await page.locator(".library-item[data-entry-id='dy-208'] .entry-more").click()
                await page.locator("#entry-menu button", has_text="删除文章").click()
                assert await page.locator("#destructive-dialog").evaluate("e => e.open")
                await page.click("#confirm-destructive")
                await page.wait_for_function(
                    "() => document.querySelector('#result-count').textContent === '9'"
                )
                assert not await page.locator(".library-item[data-entry-id='dy-208']").count()
                mode = "error"
                await page.goto("http://testserver/")
                await page.wait_for_selector("#library-error:not(.hidden)")
                mode = "normal"
                await page.click("#library-retry")
                await page.wait_for_selector("#library-results:not(.hidden)")
                mode = "empty"
                await page.goto("http://testserver/")
                await page.wait_for_selector("#empty-library:not(.hidden)")
                assert await page.locator(".empty-import-links a").count() == 0
                assert "Agent" in await page.locator("#empty-library").inner_text()
                mode = "loading"
                await page.goto("http://testserver/", wait_until="domcontentloaded")
                assert await page.locator("#library-status .skeleton-row").count() == 6
                await page.wait_for_selector("#library-results:not(.hidden)")
                assert not errors, errors
                assert not external, external
                assert all(status < 300 for _, status, _ in mutations), mutations
                (out / "verification.json").write_text(
                    json.dumps(
                        {
                            "row_dimensions": dimensions,
                            "widths": [1440, 1280, 1100, 800, 390, 320],
                            "checks": [
                                "list preference migration",
                                "roving focus and synchronized info",
                                "keyboard and grid navigation",
                                "favorite and readonly guards",
                                "source URL and author search",
                                "menu focus return",
                                "cross-filter topic creation",
                                "delete to trash",
                                "mobile tag management and filter",
                                "empty/error/retry/loading",
                                "responsive controls and mobile selection",
                            ],
                            "topic_entry_ids": payload["entry_ids"],
                            "errors": errors,
                            "external_requests": external,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n"
                )
                await browser.close()
    print(f"Library verification passed: {out}")


if __name__ == "__main__":
    asyncio.run(verify())
