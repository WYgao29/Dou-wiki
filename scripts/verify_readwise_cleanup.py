"""Audit final tokens and capture all redesigned views against an isolated real API."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from douyin_wiki.models import CaptureRequest, JobStatus  # noqa: E402
from douyin_wiki.webapp.app import WEB_VERSION, create_app  # noqa: E402
from scripts.verify_readwise_library import populate  # noqa: E402
from scripts.verify_readwise_reader import reader_fixture  # noqa: E402
from tests.support.web import _web_fixture, local_client  # noqa: E402
from tests.test_creator import FakeCreatorAdapter, inventory_result  # noqa: E402
from tests.test_favorites import scan, snapshot  # noqa: E402
from tests.test_web_operation import FakeAuthDownloader, FakeDouyinAuth  # noqa: E402


def audit_tokens():
    css = (ROOT / "src/douyin_wiki/webapp/static/app.css").read_text()
    assert not re.search(r"--(?:color-|font-size-|leading-|tile\b)", css)
    spacing = sorted(set(re.findall(r"var\(--space-([\d-]+)\)", css)), key=int)
    assert spacing == ["1", "2", "3", "4", "6", "8", "12", "16"], spacing
    sizes = sorted(set(re.findall(r"(?<![-\w])font-size:\s*([^;]+)", css)))
    allowed = {f"var(--fs-{n})" for n in [12, 13, 14, 16, 20, 28, 40, "reading"]}
    assert set(sizes) <= allowed | {".875em"}, sizes
    radii = sorted(set(re.findall(r"(?<![-\w])border-radius:\s*([^;]+)", css)))
    assert set(radii) <= {"0", "50%", "inherit", "var(--radius)", "var(--radius-sm)"}, radii
    components = re.sub(r":root(?:\[data-theme=\"dark\"\])?\s*\{[^}]+\}", "", css)
    assert not re.search(r"#[\da-fA-F]{3,8}\b|rgba?\(|\b(?:white|black)\s*[;)]", components)
    assert not re.search(r"linear-gradient|backdrop-filter", css)
    assert all(.95 <= float(value) <= 1 for value in re.findall(r"scale\(([\d.]+)\)", css))
    declared = set(re.findall(r"(--[\w-]+)\s*:", css))
    used = set(re.findall(r"var\((--[\w-]+)", css))
    dynamic = {"--filter-popover-top", "--filter-popover-left", "--popover-origin"}
    assert not used - declared - dynamic, used - declared - dynamic
    return {"css_bytes": len(css.encode()), "spacing": spacing, "font_sizes": sizes, "radii": radii}


def contrast(first, second):
    def luminance(rgb):
        components = [float(n) / 255 for n in re.findall(r"[\d.]+", rgb)[:3]]
        linear = [n / 12.92 if n <= 0.04045 else ((n + 0.055) / 1.055) ** 2.4 for n in components]
        return sum(n * weight for n, weight in zip(linear, [0.2126, 0.7152, 0.0722], strict=True))

    light, dark = sorted([luminance(first), luminance(second)], reverse=True)
    return round((light + 0.05) / (dark + 0.05), 3)


async def verify():
    out = (
        Path(
            os.environ.get(
                "DOUKU_VERIFY_OUTPUT_DIR", str(ROOT / "docs/screenshots/mcp-gateway-simplification")
            )
        )
        / "cleanup"
    )
    out.mkdir(parents=True, exist_ok=True)
    audit = audit_tokens()
    with TemporaryDirectory(prefix="douku-cleanup-check-") as tmp:
        config, service = _web_fixture(Path(tmp))
        populate(config, service)
        reader_fixture(config, service)
        inventory = inventory_result(*[str(7000000000000000101 + i) for i in range(3)])
        service.creator_adapter = FakeCreatorAdapter(inventory)
        creator = service.capture_douyin_creator(inventory.profile.canonical_url)
        creator = await service.process_claimed_job(creator)
        favorites = await scan(service, snapshot())
        service.downloader = FakeAuthDownloader("ready", ok=True, verified=True)
        service.image_note_downloader = FakeDouyinAuth(ok=True)
        topic = service.create_topic("清理后的研究工作台", ["dy-201", "dy-202"])["topic"]["id"]
        service.save_topic_note(
            topic, "## 记录与理解\n\n保留原文证据，再组织自己的判断。", confirmed=True
        )
        service.trash_entry("dy-208", confirmed=True)
        job = service.database.create_job(
            CaptureRequest(share_text="https://www.douyin.com/video/7000000000000000999"),
            status=JobStatus.ANALYZING,
            progress=0.62,
        )
        app = create_app(
            config,
            service=service,
            config_path=Path(tmp) / "config.toml",
            start_watcher=False,
        )
        pages = [
            ("library", "/?view=list", "#library-results .library-item"),
            ("gallery", "/?view=gallery", ".gallery-card"),
            ("article", "/articles/dy-123", "#article-view:not(.hidden)"),
            ("imports", "/imports", "#imports-view:not(.hidden)"),
            ("single", "/imports/single", "#imports-single-view:not(.hidden)"),
            ("creators", f"/imports/creators?job={creator.id}", "#creator-items input"),
            ("favorites", f"/imports/favorites?job={favorites.id}", "#favorites-items input"),
            ("jobs", "/jobs", ".job-row"),
            ("job", f"/jobs/{job.id}", "#job-detail-view .progress-track"),
            ("topics", "/topics", ".topic-list-card"),
            ("topic", f"/topics/{topic}", ".topic-artifact-card"),
            ("trash", "/trash", ".trash-item"),
            ("settings", "/settings", "#settings-view:not(.hidden)"),
            ("auth", "/settings/auth", "#auth-video-card .status-pill"),
            ("analysis", "/settings/analysis", "#analysis-status-title"),
            ("model", "/settings/model", "#not-found-view:not(.hidden)"),
            ("system", "/settings/system", ".system-metric"),
            ("not-found", "/no-such-page", "#not-found-view:not(.hidden)"),
        ]
        with local_client(app) as client:
            async with async_playwright() as p:
                browser = await p.chromium.launch()
                page = await browser.new_page(viewport={"width": 1440, "height": 900})
                await page.emulate_media(reduced_motion="reduce")
                errors, external, server_errors, measurements = [], [], [], []
                page.on("pageerror", lambda error: errors.append(str(error)))

                async def route(r):
                    req = r.request
                    url = urlsplit(req.url)
                    if url.hostname != "testserver":
                        external.append(req.url)
                        await r.abort()
                        return
                    if url.path.endswith("/events"):
                        await r.fulfill(content_type="text/event-stream", body=":fixture\n\n")
                        return
                    response = client.request(
                        req.method,
                        url.path + ("?" + url.query if url.query else ""),
                        content=req.post_data,
                        headers={
                            "content-type": req.headers.get("content-type", "application/json")
                        },
                    )
                    if response.status_code >= 500:
                        server_errors.append([url.path, response.status_code])
                    await r.fulfill(
                        status=response.status_code,
                        body=response.content,
                        headers={"content-type": response.headers.get("content-type", "")},
                    )

                await page.route("**/*", route)
                contrasts = {}
                for width in [1440, 1280, 1100, 960]:
                    await page.set_viewport_size({"width": width, "height": 900})
                    for theme in ["light", "dark"]:
                        for name, path, ready in pages:
                            await page.goto("http://testserver" + path)
                            await page.wait_for_selector(ready)
                            await page.evaluate("document.fonts.ready")
                            await page.evaluate("t => window.DoukuTheme.set(t)", theme)
                            await page.wait_for_timeout(200)
                            if width == 1440 and name in {"library", "gallery"}:
                                shortcut = page.locator("#library-view .search-box kbd")
                                assert await shortcut.evaluate(
                                    "e => getComputedStyle(e).display"
                                ) in {"inline-flex", "flex"}
                                assert (await shortcut.bounding_box())["height"] == 20
                            dimensions = await page.evaluate("""() => ({
                              viewport: innerWidth, page: document.documentElement.scrollWidth,
                              main: document.querySelector('main').clientWidth,
                              content: document.querySelector('main').scrollWidth,
                              sidebar: document.querySelector('.library-sidebar').clientWidth,
                              panel: document.querySelector('#side-panel').clientWidth,
                              secondary: getComputedStyle(document.documentElement)
                                .getPropertyValue('--text-2').trim(),
                              bg: getComputedStyle(document.body).backgroundColor
                            })""")
                            assert dimensions["page"] <= width, (name, width, dimensions)
                            assert dimensions["content"] <= dimensions["main"] + 1, (
                                name,
                                width,
                                dimensions,
                            )
                            if width == 1440 and name in {"library", "gallery", "article", "topic"}:
                                assert dimensions["sidebar"] == 239, dimensions
                                assert dimensions["panel"] == 359, dimensions
                            # Convert the custom property to browser-normalized rgb for luminance.
                            rgb = await page.evaluate("""() => {
                              const probe=document.createElement('span');
                              probe.style.color='var(--text-2)';document.body.append(probe);
                              const color=getComputedStyle(probe).color;probe.remove();return color;
                            }""")
                            ratio = contrast(rgb, dimensions["bg"])
                            assert ratio >= 4.5, (name, theme, ratio)
                            contrasts[theme] = ratio
                            measurements.append({"page": name, "width": width, "theme": theme})
                            if width == 1440:
                                await page.screenshot(
                                    path=out / f"{name}-{theme}.png", animations="disabled"
                                )
                assert not errors and not external and not server_errors, (
                    errors,
                    external,
                    server_errors,
                )
                report = {
                    "web_version": WEB_VERSION,
                    "tokens": audit,
                    "contrast_text_2_on_bg": contrasts,
                    "routes": len(pages),
                    "measurements": measurements,
                    "page_errors": errors,
                    "external_requests": external,
                    "server_errors": server_errors,
                }
                (out / "verification.json").write_text(
                    json.dumps(report, ensure_ascii=False, indent=2) + "\n"
                )
                print(json.dumps({**report, "measurements": len(measurements)}, ensure_ascii=False))
                await browser.close()


if __name__ == "__main__":
    asyncio.run(verify())
