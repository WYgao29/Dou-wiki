"""Exercise reader lifecycle, nested paragraph navigation and notes in an isolated Vault."""

from __future__ import annotations

import asyncio
import json
import os
import struct
import sys
import zlib
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from douyin_wiki.webapp.app import create_app  # noqa: E402
from scripts.verify_readwise_library import populate  # noqa: E402
from tests.support.web import _web_fixture, local_client  # noqa: E402


def png(width=480, height=240):
    """A local diagram fixture; no remote image or production media is used."""

    def chunk(kind, data):
        return (
            struct.pack("!I", len(data))
            + kind
            + data
            + struct.pack("!I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    pixels = bytearray()
    for y in range(height):
        pixels.append(0)
        for x in range(width):
            color = (239, 236, 229)
            if 32 < y < height - 32 and 32 < x < width - 32:
                color = (180, 203, 185) if x < width // 2 else (213, 205, 184)
            pixels.extend(color)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack("!2I5B", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(pixels))
        + chunk(b"IEND", b"")
    )


def reader_fixture(config, service):
    asset = config.vault_path / "raw/covers/reader.png"
    asset.write_bytes(png())
    source = config.vault_path / "wiki/sources/资料_208.md"
    front = source.read_text().split("---", 2)[1]
    front += "cover_image: raw/covers/reader.png\n"
    body = """# 工作台整理：减少切换，留出思考空间

![封面](../../raw/covers/reader.png)

## 灵感

保留自己提出的问题，让下一次阅读有明确的起点。

## 一句话

把资料按问题串联，在阅读、回顾与实践之间建立一条清楚的线索。

## 核心收获

- 父级方法：从一个问题开始。
  - 嵌套方法 A：保存有价值的原句。
  - 嵌套方法 B：记录下一步行动。
    1. 深层方法：用一次小实验验证。
- 第二个方法：定期回顾。

> 普通引用保持原样。
>
> 引用中的段落属于同一个导航块。

> [!note] AI 推断：与灵感的关系
> 这段推断需要在实践中验证。

> [!note] AI 推断：与灵感的关系
> 无

## 时间轴图解

### ▶ 01:05　一、从问题开始

章节原句：阅读的目标是把一个问题想清楚。

![正文图](../../raw/covers/reader.png)

### ▶ 03:40　二、整理证据

把观点与证据放在一起，再决定哪些内容值得保留。

```javascript
const notes = ["问题", "证据", "行动"];
notes.forEach(note => console.log(note));
```

## 对比表

| 阶段 | 问题 | 输出 | 检查 |
| --- | --- | --- | --- |
| 阅读 | 我想理解什么 | 原句与背景 | 来源可追溯 |
| 回顾 | 哪些内容有关联 | 比较与线索 | 区分事实和推断 |
| 实践 | 下一步做什么 | 一次小实验 | 记录结果 |

## 实践

"""
    body += "\n\n".join(
        f"实践记录 {i}：先把问题写清楚，再挑选相关资料。留出一段不被打断的时间，"
        "把读到的内容与自己的经验联系起来，并记下还需要验证的地方。"
        for i in range(1, 17)
    )
    body += "\n\n## 来源\n\n[打开原作品](https://www.douyin.com/video/208)\n"
    source.write_text(f"---{front}---\n{body}")
    entry = service.database.get_entry("dy-208")
    data = service.database.get_entry_data("dy-208")
    data["cover_path"] = "raw/covers/reader.png"
    data["metadata"]["published_at"] = entry.created_at.isoformat()
    data["analysis"]["content_type"] = "tutorial"
    data["analysis"]["chapters"] = [
        {
            "start_ms": 65000,
            "end_ms": 150000,
            "title": "从问题开始",
            "summary": body.split("### ▶ 01:05　一、从问题开始\n\n", 1)[1].split(
                "### ▶ 03:40　二、整理证据", 1
            )[0],
            "key_points": [],
        },
        {
            "start_ms": 220000,
            "end_ms": 300000,
            "title": "整理证据",
            "summary": body.split("### ▶ 03:40　二、整理证据\n\n", 1)[1].split("\n\n## 来源", 1)[0],
            "key_points": [],
        },
    ]
    service.database.upsert_entry(entry, data)


async def verify():
    out = (
        Path(
            os.environ.get(
                "DOUKU_VERIFY_OUTPUT_DIR", str(ROOT / "docs/screenshots/mcp-gateway-simplification")
            )
        )
        / "reader"
    )
    out.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="douku-reader-check-") as tmp:
        config, service = _web_fixture(Path(tmp))
        populate(config, service)
        reader_fixture(config, service)
        app = create_app(config, service=service, start_watcher=False)
        with local_client(app) as client:
            async with async_playwright() as p:
                browser = await p.chromium.launch()
                page = await browser.new_page(
                    viewport={"width": 1440, "height": 720},
                    device_scale_factor=2,
                    reduced_motion="reduce",
                )
                page.set_default_timeout(8000)
                errors, external, mutations = [], [], []
                mode = "normal"
                page.on("pageerror", lambda error: errors.append(str(error)))
                await page.add_init_script("""Object.defineProperty(navigator, 'clipboard', {
                    value: {writeText: async text => {window.fixtureClipboard = text}}
                });""")

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
                    if url.path == "/api/articles/dy-208" and mode == "error":
                        await request_route.fulfill(
                            status=503,
                            content_type="application/json",
                            body='{"detail":"模拟阅读器载入失败"}',
                        )
                        return
                    if url.path == "/api/articles/dy-208" and mode == "slow":
                        await asyncio.sleep(0.8)
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
                    await request_route.fulfill(
                        status=response.status_code,
                        body=response.content,
                        headers={"content-type": response.headers.get("content-type", "")},
                    )

                await page.route("**/*", route)

                async def library():
                    await page.goto("http://testserver/?view=list")
                    await page.wait_for_selector(".library-item")
                    await page.evaluate("document.fonts.ready")

                async def article(entry_id="dy-208"):
                    await page.evaluate("id => window.Douku.navigate('/articles/' + id)", entry_id)
                    await page.wait_for_selector("#article-content .article-body")
                    await page.wait_for_function(
                        "id => document.title.includes(id)",
                        arg="工作台整理" if entry_id == "dy-208" else "Markdown",
                    )
                    await page.evaluate("document.fonts.ready")

                async def key(value):
                    await page.keyboard.press(value)
                    await page.wait_for_timeout(30)

                await library()
                await page.locator("#main-content").evaluate("e => e.scrollTop = 200")
                await page.locator(".library-item[data-entry-id='dy-208'] a").click()
                await page.wait_for_selector("#article-content .article-body h3")
                assert await page.locator(".sidebar-body").is_hidden()
                assert await page.locator("#reader-toc").is_visible()
                assert await page.locator("#reader-toc .toc-item").count() == 9
                assert await page.locator(".article-body img").count() == 1
                assert await page.locator(".article-hero .article-cover img").count() == 1
                assert await page.locator(".article-body blockquote.callout").count() == 1
                callout = page.locator(".article-body blockquote.callout")
                assert await callout.inner_text() == (
                    "AI 推断 · 与灵感的关系\n\n这段推断需要在实践中验证。"
                )
                assert await callout.locator("p").count() == 2
                for theme in ["light", "dark"]:
                    await page.evaluate("t => window.DoukuTheme.set(t)", theme)
                    await callout.screenshot(
                        path=out / f"callout-{theme}.png", animations="disabled"
                    )
                await page.evaluate("window.DoukuTheme.set('light')")
                assert await page.locator(".article-body blockquote:not(.callout)").count() == 1
                assert "[!note]" not in await page.locator(".article-body").inner_text()
                assert await page.locator(".article-body .chapter-time").all_text_contents() == [
                    "01:05",
                    "03:40",
                ]
                assert "▶" not in await page.locator(".article-body h3").first.inner_text()
                typography = await page.locator(".article-body").evaluate("""e => {
                    const s = getComputedStyle(e);
                    return [s.fontSize, s.lineHeight, e.getBoundingClientRect().width,
                            getComputedStyle(document.querySelector('.article-hero h1')).fontSize];
                }""")
                assert typography == ["17px", "29.75px", 612, "28px"], typography
                assert not await page.locator(
                    ".article-actions, #info-pane .info-cover, #info-pane .info-title"
                ).count()

                # Keyboard visits each nested list item once; quotes, code and tables remain atomic.
                visited = []
                for _ in range(24):
                    await key("ArrowDown")
                    visited.append(
                        await page.locator(".article-body .is-reading-focus").evaluate(
                            "e => [e.tagName, e.textContent]"
                        )
                    )
                text = [value for _, value in visited]
                for label in ["嵌套方法 A", "嵌套方法 B", "深层方法"]:
                    assert sum(value.startswith(label) for value in text) == 1, (label, text)
                assert sum(tag == "PRE" for tag, _ in visited) == 1
                assert sum(tag == "TABLE" for tag, _ in visited) == 1
                assert sum(tag == "BLOCKQUOTE" for tag, _ in visited) == 2
                await page.get_by_text("嵌套方法 A：保存有价值的原句。", exact=True).click()
                assert (await page.locator(".is-reading-focus").inner_text()).startswith(
                    "嵌套方法 A"
                )
                await key("k")
                assert (await page.locator(".is-reading-focus").inner_text()).startswith("父级方法")

                await page.locator("#reader-toc .toc-item", has_text="01:05").click()
                heading = await page.locator(".article-body h3").first.bounding_box()
                assert heading["y"] >= 63, heading
                assert (
                    "01:05"
                    in await page.locator("#reader-toc [aria-current=location]").inner_text()
                )
                await page.locator("#main-content").evaluate("e => e.scrollTop = e.scrollHeight")
                await page.wait_for_function(
                    "() => document.querySelector('.reading-progress')"
                    ".getAttribute('aria-valuenow') === '100'"
                )
                assert (
                    await page.locator("#reader-toc [aria-current=location]").inner_text()
                    == "Sources"
                )
                await page.locator("#main-content").focus()
                await key("u")
                await page.wait_for_selector("#library-view:not(.hidden)")
                assert await page.locator("#main-content").evaluate("e => e.scrollTop") == 200
                assert (
                    await page.evaluate(
                        "document.activeElement.closest('[data-entry-id]').dataset.entryId"
                    )
                    == "dy-208"
                )

                # Removed import shortcut leaves the reader and focus unchanged.
                await key("Enter")
                await page.wait_for_selector(".article-body h3")
                await key("Control+i")
                assert await page.locator("#imports-popover").count() == 0
                assert await page.evaluate("document.activeElement.id") == "main-content"
                await key("u")
                # Roving order and boundary buttons follow the current filtered catalogue.
                await key("Enter")
                await page.wait_for_selector(".article-body h3")
                await key("p")
                await page.wait_for_function("() => document.title.includes('创作练习')")
                await key("n")
                await page.wait_for_selector(".article-body h3")
                await key("Shift+i")
                assert (
                    await page.locator("#panel-tab-info").get_attribute("aria-selected") == "true"
                )
                await key("Shift+c")
                assert await page.locator("#chat-input").count() == 0
                await page.locator("#main-content").focus()
                await key("Shift+i")
                await page.click(".source-file")
                assert await page.evaluate("window.fixtureClipboard") == "wiki/sources/资料_208.md"
                await page.locator("#reader-actions .entry-more").click()
                await page.locator("#entry-menu button", has_text="复制链接").click()
                assert (
                    await page.evaluate("window.fixtureClipboard")
                    == "http://testserver/articles/dy-208"
                )
                await page.locator("#main-content").focus()
                await key("Shift+f")
                assert not await page.locator("#reader-actions .favorite-button.active").count()
                await key("f")
                await page.wait_for_selector("#reader-actions .favorite-button.active")
                await page.locator("#reader-toc .toc-item", has_text="01:05").click()
                reading_top = await page.locator("#main-content").evaluate("e => e.scrollTop")
                await key("Shift+n")
                await page.wait_for_selector("#inspiration-dialog[open]")
                await page.fill("#inspiration-text", "保留问题与证据之间的线索")
                await page.fill("#inspiration-quote", "阅读的目标是把一个问题想清楚")
                await page.fill("#inspiration-start", "1:05")
                await page.fill("#inspiration-end", "2:30")
                await page.click("#confirm-inspiration")
                try:
                    await page.locator(".inspiration-card", has_text="保留问题").wait_for()
                except Exception:
                    (out / "inspiration-failure.json").write_text(
                        json.dumps(
                            {
                                "errors": errors,
                                "state": await page.evaluate(
                                    """() => ({
                                      page:state.panelPage, tab:state.panelTab,
                                      entry:state.currentEntry,
                                      dialog:document.querySelector('#inspiration-dialog').open,
                                      error:document.querySelector('#inspiration-error').textContent,
                                      pane:document.querySelector('#inspiration-pane').textContent,
                                    })"""
                                ),
                            },
                            ensure_ascii=False,
                            indent=2,
                        )
                    )
                    await page.screenshot(path=out / "inspiration-failure.png")
                    raise
                assert (
                    await page.locator("#main-content").evaluate("e => e.scrollTop") == reading_top
                )
                assert "对应原句" in await page.locator(".inspiration-card").inner_text()
                await page.locator(".inspiration-jump").click()
                await page.wait_for_selector("mark.citation-highlight")
                assert await page.locator("mark.citation-highlight").inner_text() == "01:05"
                await page.wait_for_selector("mark.citation-highlight.settled")
                await page.locator("#main-content").focus()
                await key("Shift+c")
                assert await page.locator("#chat-input").count() == 0
                await key("Shift+n")
                await page.wait_for_selector("#inspiration-dialog[open]")
                await page.fill("#inspiration-text", "j n p f 不触发阅读快捷键")
                assert "工作台整理" in await page.title()
                await key("Escape")
                await page.locator("#main-content").focus()
                await key("Escape")
                assert await page.locator("#library-view").is_visible()

                # A stale response cannot reopen a reader after returning to the library.
                mode = "slow"
                await page.evaluate("window.Douku.navigate('/articles/dy-208')")
                await page.wait_for_selector(".reader-loading")
                assert await page.locator(".reader-skeleton-body").count() == 8
                await page.locator("#main-content").focus()
                await key("Escape")
                await page.wait_for_timeout(1000)
                assert await page.locator("#library-view").is_visible()
                mode = "error"
                await page.evaluate("window.Douku.navigate('/articles/dy-208')")
                await page.wait_for_selector("#article-content .error-state")
                assert (
                    "这篇资料无法打开"
                    in await page.locator("#article-content .error-state").inner_text()
                )
                await page.locator("#article-content .error-state button").click()
                assert await page.locator("#library-view").is_visible()
                mode = "normal"
                await page.evaluate("window.Douku.navigate('/articles/no-such-entry')")
                await page.wait_for_selector("#not-found-view:not(.hidden)")
                assert "页面不存在" in await page.title()
                await page.evaluate("window.Douku.navigate('/')")
                await page.wait_for_selector("#library-view:not(.hidden)")

                await article("dy-209")
                assert not await page.locator("#reader-actions .favorite-button").count()
                assert await page.locator("#reader-next").is_disabled()
                await page.locator("#main-content").focus()
                before = len(mutations)
                await key("f")
                await key("Shift+n")
                assert not await page.locator("#inspiration-dialog").evaluate("e => e.open")
                assert len(mutations) == before
                assert (
                    "只读 Markdown 文章不能保存灵感"
                    in await page.locator("#inspiration-pane").inner_text()
                )

                # Album cover transition still maps to the hero cover.
                await library()
                await page.emulate_media(reduced_motion="no-preference")
                await page.click("#view-gallery")
                await page.evaluate("""() => {
                    const start = document.startViewTransition.bind(document);
                    document.startViewTransition = callback => {
                        window.fixtureTransition = true; return start(callback);
                    };
                }""")
                await page.locator(".gallery-card[data-entry-id='dy-208'] a").click()
                await page.wait_for_selector(".article-body h3")
                assert await page.evaluate("window.fixtureTransition")
                await page.wait_for_function(
                    "() => !document.querySelector('.article-cover').style.viewTransitionName"
                )

                await page.locator("#main-content").focus()
                await key("u")
                mode = "slow"
                await page.locator(".gallery-card[data-entry-id='dy-208'] a").click()
                await page.locator("#main-content").focus()
                await key("Escape")
                await page.wait_for_timeout(1000)
                assert await page.locator("#library-view").is_visible()
                mode = "normal"
                for width in [1440, 1280, 1100, 960]:
                    await page.set_viewport_size({"width": width, "height": 900})
                    await article()
                    assert await page.evaluate(
                        "document.documentElement.scrollWidth <= innerWidth"
                    ), width
                    controls = await page.locator(
                        ".reader-header > div:not(.reading-progress), #back-library"
                    ).evaluate_all(
                        """nodes => nodes.filter(e => e.getClientRects().length).map(e => {
                        const r = e.getBoundingClientRect(); return [r.left, r.right];
                    })"""
                    )
                    assert all(left >= 0 and right <= width for left, right in controls), (
                        width,
                        controls,
                    )
                    assert all(
                        controls[i][1] <= controls[i + 1][0] for i in range(len(controls) - 1)
                    ), (width, controls)
                    if width == 1440:
                        for theme in ["light", "dark"]:
                            await page.select_option("[data-theme-select]", theme)
                            await page.locator("#main-content").evaluate("e => e.scrollTop = 0")
                            await page.evaluate("document.activeElement?.blur()")
                            await page.mouse.move(0, 0)
                            await page.screenshot(
                                path=out / f"article-{theme}.png", animations="disabled"
                            )
                await page.set_viewport_size({"width": 1440, "height": 900})
                await library()
                for theme in ["light", "dark"]:
                    await page.select_option("[data-theme-select]", theme)
                    await page.evaluate("document.activeElement?.blur()")
                    await page.mouse.move(0, 0)
                    await page.screenshot(path=out / f"library-{theme}.png", animations="disabled")
                assert not errors, errors
                assert not external, external
                assert all(status < 300 for _, status, _ in mutations), mutations
                note = next(
                    json.loads(body)
                    for path, _, body in mutations
                    if path == "/api/inspirations/confirm"
                )
                assert (
                    note["entry_id"] == "dy-208"
                    and note["start_ms"] == 65000
                    and note["end_ms"] == 150000
                )
                (out / "verification.json").write_text(
                    json.dumps(
                        {
                            "typography": typography,
                            "widths": [1440, 1280, 1100, 960],
                            "checks": [
                                "toc and sticky heading offsets",
                                "chapter times and callouts",
                                "body image retained",
                                "nested paragraphs and atomic blocks",
                                "reading progress",
                                "scroll and focus restored",
                                "adjacent articles",
                                "copy actions",
                                "favorite",
                                "real inspiration save and refresh",
                                "citation highlight and fade",
                                "readonly guards",
                                "stale response guard",
                                "loading/error",
                                "album cover transition",
                                "responsive header",
                            ],
                            "errors": errors,
                            "external_requests": external,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n"
                )
                await browser.close()
    print(f"Reader verification passed: {out}")


if __name__ == "__main__":
    asyncio.run(verify())
