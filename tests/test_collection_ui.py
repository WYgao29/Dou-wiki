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
from tests.test_collection_flow import MIX, A, B, FakeCollectionAdapter, snapshot

COVER = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='320' height='180'"
    "%3E%3Crect width='320' height='180' fill='%23375378'/%3E%3Ctext x='28' y='104' "
    "fill='white' font-size='26'%3EPROJECT STORY%3C/text%3E%3C/svg%3E"
)


@pytest.fixture
async def collection_ui(config, service):
    service.resolver = DouyinShareResolver()
    inventory = snapshot(tuple(str(7000000000000000000 + n) for n in range(105)))
    for index, work in enumerate(inventory.works):
        work.title = f"第 {index + 1} 集：项目经理如何拆分工作并协调团队成员"
        work.author = "项目经理故事汇"
        work.thumbnail_url = COVER
        work.duration_seconds = 92
    service.collections.adapter = FakeCollectionAdapter(inventory)
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


async def test_collection_ui_existing_batch_all_pages_confirm_and_reload(collection_ui, tmp_path):
    ui, page = collection_ui, collection_ui.page
    parent_id = ui.service.capture_douyin_collection(f"https://www.douyin.com/collection/{MIX}").id
    await page.goto(ui.url + f"/imports/collections?job={parent_id}")
    await expect(page.locator("#collection-form, #import-paste-form")).to_have_count(0)
    await expect(page.locator("#collection-summary")).to_contain_text("已读 105", timeout=12000)
    await expect(page.locator("#collection-status")).to_contain_text("待选择作品")
    await expect(page.locator("#collection-history option:checked")).to_contain_text(
        "待选择作品 · 105 件"
    )
    await page.locator("[data-theme-select]").select_option("light")
    await expect(page.locator("html")).to_have_attribute("data-theme", "light")
    await page.screenshot(path=str(tmp_path / "collection-desktop-light.png"))
    await expect(page.locator("#collection-confirm")).to_be_disabled()
    await expect(page.locator("#collection-result")).to_contain_text("预计新建 0")
    await expect(page.locator("#collection-result")).to_contain_text("复用任务 0")
    cover = page.locator("#collection-items img").first
    await expect(cover).to_have_attribute("src", COVER)
    assert await cover.evaluate("image => image.complete && image.naturalWidth > 0")
    await expect(page.locator("#collection-items").first).to_contain_text("1:32")
    await page.locator("#collection-select-all").click()
    await expect(page.locator("#collection-summary")).to_contain_text("已选 105")
    await expect(page.locator("#collection-result")).to_contain_text("预计新建 105")
    await expect(page.locator("#collection-confirm")).to_be_enabled()
    await page.locator("#collection-next").click()
    await expect(page.locator("#collection-page")).to_contain_text("第 2 页")
    assert await page.locator("#collection-items input:checked").count() == 50
    await page.locator("#collection-confirm").click()
    await expect(page.locator("#collection-status")).to_contain_text("正在处理所选作品")
    assert len([j for j in ui.service.database.list_jobs(limit=None) if j.kind == "capture"]) == 105
    await page.reload()
    await expect(page.locator("#collection-result")).to_contain_text("新任务 105")
    parent = next(
        j for j in ui.service.database.list_jobs(limit=None) if j.kind == "collection_import"
    )
    await page.locator("#collection-history").select_option("")
    await expect(page.locator("#collection-summary")).to_have_text("")
    await expect(page.locator("#collection-result")).to_have_text("")
    await expect(page.locator("#collection-task")).to_be_hidden()
    assert await page.locator("#collection-items input").count() == 0
    await page.locator("#collection-history").select_option(parent.id)
    await expect(page.locator("#collection-result")).to_contain_text("新任务 105")
    assert ui.errors == []


async def test_collection_ui_preview_counts_and_partial_retry(collection_ui, tmp_path):
    from douyin_wiki.collection_models import CollectionWork
    from tests.test_collection_vault import import_entry

    ui, page = collection_ui, collection_ui.page
    await import_entry(ui.service)
    ui.service.resolver = DouyinShareResolver()
    ui.service.capture_douyin(f"https://www.douyin.com/video/{B}")
    c, d, e, new = (str(7000000000000000200 + n) for n in range(4))
    inventory = snapshot((A, B, c, d, e), complete=False)
    inventory.works[2].duration_seconds = 3661
    inventory.works[2].thumbnail_url = COVER
    inventory.works[3] = CollectionWork(work_id=d, position=4, source_kind="article")
    inventory.works[4].available = False
    ui.service.collections.adapter = FakeCollectionAdapter(inventory)
    parent_id = ui.service.capture_douyin_collection(f"https://www.douyin.com/collection/{MIX}").id
    await page.goto(ui.url + f"/imports/collections?job={parent_id}")
    await expect(page.locator("#collection-summary")).to_contain_text("已读 5", timeout=12000)
    await expect(page.locator("#collection-result")).to_contain_text("不可导入 2")
    await expect(page.locator("#collection-summary")).to_contain_text("已在资料库 1")
    await page.locator("#collection-select-all").click()
    await expect(page.locator("#collection-result")).to_contain_text("预计新建 1")
    await expect(page.locator("#collection-result")).to_contain_text("复用任务 1")
    await expect(page.locator("#collection-items")).to_contain_text("61:01")
    await expect(page.locator("#collection-confirm")).to_be_disabled()
    await expect(page.locator("#collection-retry")).to_have_text("重新清点")
    await expect(page.locator("#collection-retry")).to_be_enabled()
    await page.screenshot(path=str(tmp_path / "collection-preview-partial.png"))
    inventory = inventory.model_copy(
        update={
            "complete": True,
            "warnings": [],
            "works": [*inventory.works, CollectionWork(work_id=new, position=6)],
        }
    )
    ui.service.collections.adapter = FakeCollectionAdapter(inventory)
    await page.locator("#collection-retry").click()
    await expect(page.locator("#collection-summary")).to_contain_text("已读 6", timeout=12000)
    await expect(page.locator("#collection-summary")).to_contain_text("待选择 1")
    assert await page.locator("#collection-items input:checked").count() == 3
    await expect(page.locator("#collection-confirm")).to_be_disabled()
    await expect(page.locator("#collection-retry")).to_be_hidden()
    assert ui.errors == []


async def test_collection_ui_waits_for_save_and_retries_error(collection_ui, tmp_path):
    ui, page = collection_ui, collection_ui.page
    parent_id = ui.service.capture_douyin_collection(f"https://www.douyin.com/collection/{MIX}").id
    await page.goto(ui.url + f"/imports/collections?job={parent_id}")
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
    await page.locator("[data-theme-select]").select_option("dark")
    await expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    await page.screenshot(path=str(tmp_path / "collection-desktop-dark.png"))
    await expect(
        page.locator("#collection-items .favorites-item").last.get_by_text("1:32", exact=True)
    ).to_be_visible()
    await page.locator("#collection-items input").first.focus()
    await page.keyboard.press("x")
    await expect(page.locator("#collection-summary")).to_contain_text("已选 104")
    assert ui.errors == []


@pytest.mark.parametrize("pause", [JobStatus.AWAITING_AGENT_ANALYSIS, JobStatus.NEEDS_AUTH])
@pytest.mark.parametrize("width,theme", [(1440, "light"), (1280, "dark")])
async def test_shared_capture_ui_links_executor_from_list_and_detail(
    collection_ui, tmp_path, pause, width, theme
):
    from tests.test_capture_sharing_web import shared_pair

    ui, page = collection_ui, collection_ui.page
    owner, follower = await shared_pair(ui.service, pause)
    message = "视频下载授权已失效" if pause == JobStatus.NEEDS_AUTH else "需要外部 Agent 接续"
    await page.goto(ui.url + "/jobs")
    await page.locator("[data-theme-select]").select_option(theme)
    await page.set_viewport_size({"width": width, "height": 900})
    row = page.locator(f'#jobs-list [data-job-id="{follower}"]')
    await expect(row).to_contain_text(message)
    action = row.get_by_text("查看执行任务", exact=True)
    await expect(action).to_be_visible()
    await action.click(trial=True)
    await page.screenshot(path=str(tmp_path / "shared-list.png"), animations="disabled")
    await action.click()
    await expect(page).to_have_url(f"{ui.url}/jobs/{owner}")
    await expect(page.locator("#job-detail-content")).to_contain_text(message)

    await page.goto(f"{ui.url}/jobs/{follower}")
    detail = page.locator("#job-detail-content")
    await expect(detail).to_contain_text(message)
    action = detail.get_by_role("button", name="查看执行任务", exact=True)
    await expect(action).to_be_visible()
    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    await page.screenshot(path=str(tmp_path / "shared-detail.png"), animations="disabled")
    await action.click()
    await expect(page).to_have_url(f"{ui.url}/jobs/{owner}")
    await expect(detail).to_contain_text(message)

    await page.goto(f"{ui.url}/jobs/{follower}")
    await detail.get_by_role("button", name="取消", exact=True).click()
    await page.locator("#confirm-destructive").click()
    await expect(detail).to_contain_text("此任务已取消。")
    await expect(detail.get_by_text("查看执行任务", exact=True)).to_have_count(0)
    assert ui.service.get_job(follower).status == JobStatus.CANCELLED
    assert ui.service.get_job(owner).status == pause
    assert ui.errors == []
