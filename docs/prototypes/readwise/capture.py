"""Run from the repository root: uv run python docs/prototypes/readwise/capture.py."""

import asyncio
import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT.parents[1] / "screenshots/redesign/prototype"


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


async def capture(url):
    OUTPUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(
            viewport={"width": 1440, "height": 900}, device_scale_factor=2
        )
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        await page.goto(url)
        await page.evaluate("document.fonts.ready")
        await page.screenshot(path=OUTPUT / "library-light.png")
        geometry = await page.evaluate("""() => ({
          columns: getComputedStyle(document.querySelector('.shell')).gridTemplateColumns,
          rowHeight: document.querySelector('.entry').getBoundingClientRect().height,
          horizontalOverflow: document.documentElement.scrollWidth > innerWidth,
          fontLoaded: document.fonts.check('14px Inter')
        })""")
        assert geometry["rowHeight"] == 88, geometry
        assert not geometry["horizontalOverflow"], geometry
        await page.click("#open-reader")
        await page.evaluate("document.fonts.ready")
        await page.screenshot(path=OUTPUT / "reader-light.png")
        assert await page.locator("#toc").is_visible()
        assert await page.locator("#reader").is_visible()
        assert not await page.locator("#library").is_visible()
        assert (
            await page.locator(".article-body").evaluate("e => getComputedStyle(e).fontSize")
            == "17px"
        )
        await page.click("#theme-toggle")
        await page.screenshot(path=OUTPUT / "reader-dark.png")
        await page.click("#back")
        await page.screenshot(path=OUTPUT / "library-dark.png")
        await page.click("#theme-toggle")
        await page.fill("#search", "咖啡")
        assert await page.locator(".entry").count() == 1
        await page.fill("#search", "")
        await page.click("#gallery-mode")
        assert await page.locator("#entries").evaluate("e => getComputedStyle(e).display") == "grid"
        await page.click("#list-mode")
        for width in [1280, 1100, 800, 390]:
            await page.set_viewport_size({"width": width, "height": 900})
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
        assert not errors, errors
        (OUTPUT / "verification.json").write_text(
            json.dumps(
                {
                    "viewport": "1440×900 CSS px; screenshots at 2x",
                    "geometry": geometry,
                    "checked": [
                        "reader navigation",
                        "light/dark",
                        "search",
                        "gallery toggle",
                        "horizontal overflow at 1280/1100/800/390",
                    ],
                    "pageErrors": errors,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
        print(json.dumps(geometry, ensure_ascii=False))
        await browser.close()


if __name__ == "__main__":
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(QuietHandler, directory=str(ROOT.parents[2]))
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        asyncio.run(capture(f"http://127.0.0.1:{server.server_port}/docs/prototypes/readwise/"))
    finally:
        server.shutdown()
