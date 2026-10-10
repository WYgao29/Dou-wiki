"""Capture the real SPA against an isolated local fixture, without external requests."""

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

from douyin_wiki.models import CaptureRequest, JobStatus  # noqa: E402
from douyin_wiki.webapp.app import WEB_VERSION, create_app  # noqa: E402
from scripts.verify_readwise_cleanup import contrast  # noqa: E402
from tests.support.web import _web_fixture, local_client  # noqa: E402


async def verify():
    out = ROOT / "docs/screenshots/redesign/phase-0"
    out.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="douku-ui-check-") as tmp:
        config, service = _web_fixture(Path(tmp))
        service.database.create_job(
            CaptureRequest(share_text="https://www.douyin.com/video/7000000000000000999"),
            status=JobStatus.NEEDS_AUTH,
        )
        topic_id = service.create_topic("字体请求验证", ["dy-123"])["topic"]["id"]
        app = create_app(
            config, service=service,  start_watcher=False
        )
        with local_client(app) as client:
            async with async_playwright() as p:
                browser = await p.chromium.launch()
                page = await browser.new_page(
                    viewport={"width": 1440, "height": 900}, device_scale_factor=2
                )
                errors, failed, external = [], [], []
                page.on("pageerror", lambda err: errors.append(str(err)))

                async def route(request_route):
                    request = request_route.request
                    url = urlsplit(request.url)
                    if url.hostname != "testserver":
                        external.append(request.url)
                        await request_route.abort()
                        return
                    if url.path.endswith("/events"):
                        await request_route.fulfill(
                            status=200, content_type="text/event-stream", body=": fixture\n\n"
                        )
                        return
                    response = client.request(
                        request.method,
                        url.path + (f"?{url.query}" if url.query else ""),
                        content=request.post_data,
                    )
                    if response.status_code >= 500:
                        failed.append((url.path, response.status_code))
                    await request_route.fulfill(
                        status=response.status_code,
                        headers={
                            "content-type": response.headers.get(
                                "content-type", "application/octet-stream"
                            )
                        },
                        body=response.content,
                    )

                await page.route("**/*", route)
                await page.goto("http://testserver/")
                await page.wait_for_selector("#library-results:not(.hidden)")
                await page.evaluate("document.fonts.ready")
                base = await page.locator("body").evaluate(
                    "e => ({size:getComputedStyle(e).fontSize, "
                    "line:getComputedStyle(e).lineHeight})"
                )
                assert base == {"size": "14px", "line": "20px"}, base
                await page.wait_for_selector("#library-todo:not(.hidden)")
                todo = await page.locator("#library-todo").evaluate("""e => ({
                  size:getComputedStyle(e).fontSize,
                  dot:getComputedStyle(e,'::before').backgroundColor,
                  action:e.querySelector('.todo-banner-action').textContent
                })""")
                assert todo["size"] == "13px" and todo["action"] == "去任务中心", todo
                assert await page.locator("main #settings-view").count() == 1
                assert await page.locator("main #not-found-view").count() == 1
                for theme in ["light", "dark"]:
                    await page.select_option("[data-theme-select]", theme)
                    await page.screenshot(path=out / f"library-{theme}.png", animations="disabled")
                    await page.locator("#library-todo").screenshot(
                        path=out / f"todo-{theme}.png", animations="disabled"
                    )
                await page.locator(".library-item").first.focus()
                await page.keyboard.press("Enter")
                await page.wait_for_selector(".article-body h2")
                await page.wait_for_function("document.querySelector('#reading-fonts')?.sheet")
                focus = await page.locator("#main-content").evaluate("""e => ({
                  active:document.activeElement===e,
                  outline:getComputedStyle(e).outlineStyle, shadow:getComputedStyle(e).boxShadow
                })""")
                assert focus == {"active": True, "outline": "none", "shadow": "none"}, focus
                assert await page.locator(".sidebar-brand-row #reader-back").count() == 1
                assert await page.locator(".sidebar-brand-row .brand").is_hidden()
                await page.evaluate("document.fonts.ready")
                typography = await page.locator(".article-body").evaluate(
                    """e => ({size: getComputedStyle(e).fontSize,
                      lineHeight: getComputedStyle(e).lineHeight,
                      width: e.getBoundingClientRect().width,
                      font: getComputedStyle(e).fontFamily})"""
                )
                assert typography["size"] == "17px", typography
                assert typography["width"] == 612, typography
                for theme in ["light", "dark"]:
                    await page.select_option("[data-theme-select]", theme)
                    await page.screenshot(path=out / f"article-{theme}.png", animations="disabled")
                # The renderer owns cover removal. CSS must retain independent evidence images.
                image = await page.evaluate("""() => {
                  const body=document.querySelector('.article-body');
                  const para=document.createElement('p'), img=document.createElement('img');
                  img.alt='正文证据图'; para.append(img); body.prepend(para);
                  return getComputedStyle(img).display;
                }""")
                assert image != "none"
                fonts = {}
                for family, sample in [
                    ("Inter", "Hello"),
                    ("Doku Serif SC", "核心收获"),
                    ("Newsreader", "Reading"),
                ]:
                    fonts[family] = await page.evaluate(
                        """async ([family,sample]) =>
                          (await document.fonts.load(`17px "${family}"`,sample)).length > 0""",
                        [family, sample],
                    )
                    assert fonts[family]
                for path, selector in [
                    ("/settings", "#settings-view"),
                    ("/no-such-page", "#not-found-view"),
                ]:
                    await page.goto("http://testserver" + path)
                    await page.wait_for_selector(selector + ":not(.hidden)")
                    box = await page.locator(selector).bounding_box()
                    assert box and box["width"] > 0
                font_requests = {}
                for name, path, ready, reading in [
                    ("library", "/", "#library-results .library-item", False),
                    ("jobs", "/jobs", "#jobs-view:not(.hidden)", False),
                    ("settings", "/settings", "#settings-view:not(.hidden)", False),
                    ("topics", "/topics", ".topic-list-card", False),
                    ("topic", f"/topics/{topic_id}", ".topic-header", False),
                    ("article", "/articles/dy-123", ".article-body h2", True),
                ]:
                    probe = await browser.new_page(viewport={"width": 1440, "height": 900})
                    requests = []
                    probe.on(
                        "request",
                        lambda req, requests=requests: (
                            requests.append(req.url) if "/static/fonts/" in req.url else None
                        ),
                    )
                    probe.on("pageerror", lambda error: errors.append(str(error)))
                    await probe.route("**/*", route)
                    await probe.goto("http://testserver" + path)
                    await probe.wait_for_selector(ready)
                    await probe.evaluate("document.fonts.ready")
                    fonts_seen = sorted({urlsplit(url).path.rsplit("/", 1)[1] for url in requests})
                    serif_seen = [
                        name for name in fonts_seen if "Serif" in name or "Newsreader" in name
                    ]
                    assert bool(serif_seen) == reading, (name, fonts_seen)
                    font_requests[name] = fonts_seen
                    if name == "topic":
                        assert (
                            await probe.locator("#topic-header-title").inner_text()
                            == "字体请求验证"
                        )
                    await probe.close()
                palette = {}
                for theme in ["light", "dark"]:
                    await page.evaluate("t => window.DoukuTheme.set(t)", theme)
                    colors = await page.evaluate("""() => {
                      const e=document.createElement('span');document.body.append(e);
                      const result={};
                      for (const role of ['--text-2','--text-3','--bg','--surface',
                                          '--surface-sunken',
                                          '--surface-hover','--surface-selected']) {
                        e.style.color=`var(${role})`;result[role]=getComputedStyle(e).color;
                      }
                      e.remove();return result;
                    }""")
                    palette[theme] = {
                        role: {
                            bg: contrast(colors[role], colors[bg])
                            for bg in [
                                "--bg",
                                "--surface",
                                "--surface-sunken",
                                "--surface-hover",
                                "--surface-selected",
                            ]
                        }
                        for role in ["--text-2", "--text-3"]
                    }
                    assert all(
                        ratio >= 4.5
                        for ratios in palette[theme].values()
                        for ratio in ratios.values()
                    ), palette
                assert not errors, errors
                assert not failed, failed
                assert not external, external
                report = {
                    "web_version": WEB_VERSION,
                    "typography": typography,
                    "body": base,
                    "todo": todo,
                    "keyboard_open_focus": focus,
                    "font_requests_fresh_context": font_requests,
                    "text_contrast": palette,
                    "localFonts": fonts,
                    "pageErrors": errors,
                    "externalRequests": external,
                    "checks": [
                        "settings/404 inside main and reachable",
                        "evidence image remains visible",
                        "light/dark",
                        "local font loading",
                    ],
                }
                (out / "verification.json").write_text(
                    json.dumps(report, ensure_ascii=False, indent=2) + "\n"
                )
                print(json.dumps(report, ensure_ascii=False))
                await browser.close()


if __name__ == "__main__":
    asyncio.run(verify())
