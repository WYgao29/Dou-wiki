"""Real browser and HTTP API against an isolated SQLite/Vault, with synthetic inventory."""

import asyncio
import socket
from types import SimpleNamespace

import pytest
import uvicorn
from playwright.async_api import async_playwright, expect

from douyin_wiki.adapters.share import DouyinShareResolver
from douyin_wiki.models import JobStatus
from douyin_wiki.webapp.app import create_app
from tests.test_collection_flow import MIX, FakeCollectionAdapter, snapshot


@pytest.fixture
async def collection_ui(config, service):
    service.resolver = DouyinShareResolver()
    service.collections.adapter = FakeCollectionAdapter(
        snapshot(tuple(str(7000000000000000000 + n) for n in range(105)))
    )
    app = create_app(config, service=service, start_watcher=False)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    server_task = asyncio.create_task(server.serve(sockets=[sock]))
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.02)
    assert server.started

    async def inventory_worker():
        while True:
            for job in service.database.list_jobs(
                status=JobStatus.QUEUED, kinds={"collection_import"}, limit=None
            ):
                await service.process_claimed_job(job)
            await asyncio.sleep(0.05)

    worker = asyncio.create_task(inventory_worker())
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            yield SimpleNamespace(
                page=page, url=f"http://127.0.0.1:{port}", service=service, errors=errors
            )
        finally:
            await browser.close()
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            server.should_exit = True
            await server_task
            sock.close()


async def test_collection_ui_paste_all_pages_confirm_and_reload(collection_ui):
    ui, page = collection_ui, collection_ui.page
    await page.goto(ui.url + "/imports")
    await page.locator("#import-paste").fill(f"https://www.douyin.com/collection/{MIX}")
    await expect(page.locator("#import-paste-kind")).to_contain_text("抖音合集")
    await page.locator("#import-paste-form button[type=submit]").click()
    await expect(page.locator("#collection-source")).to_have_value(
        f"https://www.douyin.com/collection/{MIX}"
    )
    assert ui.service.database.list_jobs() == []
    await page.locator("#collection-form button[type=submit]").click()
    await expect(page.locator("#collection-summary")).to_contain_text("已读 105", timeout=12000)
    await expect(page.locator("#collection-confirm")).to_be_disabled()
    await page.locator("#collection-select-all").click()
    await expect(page.locator("#collection-summary")).to_contain_text("已选 105")
    await expect(page.locator("#collection-confirm")).to_be_enabled()
    await page.locator("#collection-next").click()
    await expect(page.locator("#collection-page")).to_contain_text("第 2 页")
    assert await page.locator("#collection-items input:checked").count() == 50
    await page.locator("#collection-confirm").click()
    await expect(page.locator("#collection-status")).to_contain_text("正在处理所选作品")
    assert len([j for j in ui.service.database.list_jobs(limit=None) if j.kind == "capture"]) == 105
    await page.reload()
    await expect(page.locator("#collection-result")).to_contain_text("新任务 105")
    assert ui.errors == []


async def test_collection_ui_waits_for_save_and_retries_error(collection_ui):
    ui, page = collection_ui, collection_ui.page
    await page.goto(ui.url + "/imports/collections")
    await page.locator("#collection-source").fill(f"https://www.douyin.com/collection/{MIX}")
    await page.locator("#collection-form button[type=submit]").click()
    await expect(page.locator("#collection-summary")).to_contain_text("已读 105", timeout=12000)
    gate = asyncio.Event()

    async def delayed(route):
        await gate.wait()
        await route.fulfill(status=500, json={"detail": "模拟保存失败"})

    await page.route("**/selection", delayed)
    await page.locator("#collection-select-all").click()
    await expect(page.locator("#collection-confirm")).to_be_disabled()
    gate.set()
    await expect(page.locator("#collection-save-retry")).to_be_visible()
    await expect(page.locator("#collection-confirm")).to_be_disabled()
    await page.unroute("**/selection", delayed)
    await page.locator("#collection-save-retry").click()
    await expect(page.locator("#collection-confirm")).to_be_enabled()
    await page.set_viewport_size({"width": 390, "height": 844})
    await page.emulate_media(color_scheme="dark")
    await page.locator("#collection-confirm").scroll_into_view_if_needed()
    await expect(page.locator("#collection-confirm")).to_be_in_viewport()
    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    await page.locator("#collection-items input").first.focus()
    await page.keyboard.press("x")
    await expect(page.locator("#collection-summary")).to_contain_text("已选 104")
    assert ui.errors == []
