"""Verify retired model routes, topic notes and the preserved reader information panel."""

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

from douyin_wiki.webapp.app import WEB_VERSION, create_app  # noqa: E402
from scripts.verify_readwise_library import populate  # noqa: E402
from scripts.verify_readwise_reader import reader_fixture  # noqa: E402
from tests.support.web import _web_fixture, local_client  # noqa: E402


async def verify():
    out = ROOT / "docs/screenshots/redesign/phase-4"
    out.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="douku-note-check-") as tmp:
        config, service = _web_fixture(Path(tmp))
        populate(config, service)
        reader_fixture(config, service)
        topic_id = service.create_topic("阅读与知识管理", ["dy-208", "dy-201"])["topic"]["id"]
        note = service.save_topic_note(
            topic_id, "## 我的判断\n\n保留来源与逐字笔记。", confirmed=True
        )
        app = create_app(config, service=service, start_watcher=False)
        with local_client(app) as client:
            for method, path in (
                ("GET", "/api/chat/sessions"),
                ("POST", "/api/chat/sessions"),
                ("GET", "/api/chat/sessions/missing"),
                ("DELETE", "/api/chat/sessions/missing"),
                ("POST", "/api/chat/sessions/missing/messages"),
                ("GET", "/api/settings/model"),
                ("POST", "/api/settings/model"),
                ("POST", "/api/settings/model/test"),
                ("GET", "/api/system/model-health"),
                ("POST", f"/api/topics/{topic_id}/artifacts"),
            ):
                response = client.request(method, path, json={})
                assert response.status_code == 410, (method, path, response.text)
            saved = client.get(f"/api/topics/{topic_id}").json()["artifacts"][0]
            assert saved["id"] == note["id"] and "<h2>我的判断</h2>" in saved["html"]
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch()
                page = await browser.new_page(viewport={"width": 1440, "height": 900})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                await page.add_init_script(
                    "window.EventSource = class extends EventTarget { close() {} };"
                )

                async def route_handler(route):
                    request = route.request
                    url = urlsplit(request.url)
                    assert url.hostname == "testserver", request.url
                    response = await asyncio.to_thread(
                        client.request, request.method,
                        url.path + (f"?{url.query}" if url.query else ""),
                        content=request.post_data,
                        headers={key: value for key, value in request.headers.items()
                                 if key in {"content-type", "x-douku-client"}},
                    )
                    await route.fulfill(
                        status=response.status_code, body=response.content,
                        headers={"content-type": response.headers.get("content-type", "")},
                    )

                await page.route("**/*", route_handler)
                await page.goto("http://testserver/articles/dy-208")
                await page.locator("#article-content .article-body").wait_for()
                assert await page.locator("#chat-toggle").count() == 1
                await page.locator("#panel-tab-info").click()
                await page.screenshot(path=str(out / "note-reader-desktop.png"))
                assert not errors, errors
                await browser.close()
    print(json.dumps(
        {"web_version": WEB_VERSION, "retired_endpoints": 10,
         "topic_note": "PASS", "reader_information_panel": "PASS"}, ensure_ascii=False,
    ))


if __name__ == "__main__":
    asyncio.run(verify())
