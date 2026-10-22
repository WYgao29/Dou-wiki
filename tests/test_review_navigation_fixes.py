"""Delayed responses must not change the page or the target of its actions."""

import asyncio
import base64
import json
import re
from urllib.parse import urlsplit

import pytest
from playwright.async_api import expect

from scripts.verify_review_web_races import block, browser_fixture, entered


@pytest.mark.parametrize("view", ["list", "gallery"])
async def test_escape_leaves_sidebar_navigation_and_keeps_tab_focus_visible(view):
    async with browser_fixture() as (page, _topics, _gates):
        if view == "gallery":
            await page.locator("#view-gallery").click()
        all_items = page.locator(".nav-item[data-section='all']")
        await all_items.click()
        before_url = page.url
        await page.keyboard.press("Escape")
        await expect(page.locator("#main-content")).to_be_focused()
        assert not await all_items.evaluate("e => e.matches(':focus-visible')")
        assert page.url == before_url
        assert await page.locator("#library-results").evaluate(
            "e => e.classList.contains('gallery')"
        ) == (view == "gallery")

        await all_items.focus()
        await page.keyboard.press("Tab")
        recent = page.locator(".nav-item[data-section='recent']")
        await expect(recent).to_be_focused()
        assert await recent.evaluate("e => e.matches(':focus-visible')")
        assert await recent.evaluate("e => getComputedStyle(e).outlineWidth") == "2px"
        await page.keyboard.press("Escape")
        await expect(page.locator("#main-content")).to_be_focused()

        await page.locator("#filter-toggle").click()
        await page.keyboard.press("Escape")
        await expect(page.locator("#filter-toggle")).to_be_focused()
        await expect(page.locator("#filter-popover")).to_be_hidden()

        await page.locator("#search-input").focus()
        await page.keyboard.press("Escape")
        await expect(page.locator("#search-input")).to_be_focused()


@pytest.mark.parametrize("old_status", [200, 404, 500])
@pytest.mark.parametrize("destination", ["/jobs/new", "/imports"])
async def test_job_detail_discards_response_after_navigation(old_status, destination):
    async with browser_fixture() as (page, _topics, _gates):
        gate = {"entered": asyncio.Event(), "release": asyncio.Event()}
        deleted = []

        async def job_response(route):
            job_id = urlsplit(route.request.url).path.rsplit("/", 1)[-1]
            if route.request.method == "DELETE":
                deleted.append(job_id)
                await route.fulfill(content_type="application/json", body="{}")
                return
            status = 200
            if job_id == "old":
                gate["entered"].set()
                await gate["release"].wait()
                status = old_status
            data = {
                "id": job_id,
                "display_title": job_id,
                "kind": "capture",
                "status": "failed",
                "stage_label": "完成",
                "message_for_user": job_id,
                "progress": 0,
                "detail": "过期任务请求失败",
            }
            await route.fulfill(
                status=status, content_type="application/json", body=json.dumps(data)
            )

        await page.route("**/api/jobs/old", job_response)
        await page.route("**/api/jobs/new", job_response)
        await page.evaluate("()=>window.Douku.navigate('/jobs/old')")
        await entered(gate)
        await page.evaluate("path=>window.Douku.navigate(path)", destination)
        if destination == "/jobs/new":
            await expect(page.locator("#job-detail-title")).to_have_text("new")
        else:
            await expect(page.locator("#imports-view")).to_be_visible()
        gate["release"].set()
        await page.wait_for_timeout(120)
        assert urlsplit(page.url).path == destination
        assert await page.locator("#app-shell").get_attribute("data-page") == (
            "job-detail" if destination == "/jobs/new" else "imports"
        )
        assert "过期任务请求失败" not in await page.locator("#toast").inner_text()
        if destination == "/jobs/new":
            await expect(page.locator("#job-detail-title")).to_have_text("new")
            await page.get_by_role("button", name="删除", exact=True).click()
            await page.locator("#destructive-dialog button.danger-button").click()
            await page.wait_for_timeout(50)
            assert deleted == ["new"]


async def test_newer_refresh_of_same_job_wins():
    async with browser_fixture() as (page, _topics, _gates):
        gate = {"entered": asyncio.Event(), "release": asyncio.Event()}
        requests = 0

        async def detail(route):
            nonlocal requests
            requests += 1
            title = "旧状态" if requests == 1 else "新状态"
            if requests == 1:
                gate["entered"].set()
                await gate["release"].wait()
            await route.fulfill(
                content_type="application/json",
                body=json.dumps({
                    "id": "same", "kind": "capture", "display_title": title,
                    "status": "failed", "progress": 0,
                }),
            )

        await page.route("**/api/jobs/same", detail)
        await page.evaluate("()=>window.Douku.navigate('/jobs/same')")
        await entered(gate)
        await page.evaluate("()=>window.Douku.navigate('/jobs/same')")
        await expect(page.locator("#job-detail-title")).to_have_text("新状态")
        gate["release"].set()
        await page.wait_for_timeout(120)
        await expect(page.locator("#job-detail-title")).to_have_text("新状态")


async def test_job_list_discards_previous_filter_response():
    async with browser_fixture() as (page, _topics, _gates):
        gate = {"entered": asyncio.Event(), "release": asyncio.Event()}

        async def jobs(route):
            filtered = "status=failed" in route.request.url
            if not filtered:
                gate["entered"].set()
                await gate["release"].wait()
            await route.fulfill(
                content_type="application/json",
                body=json.dumps({
                    "total": 1,
                    "items": [{
                        "id": "filtered" if filtered else "old", "kind": "capture",
                        "display_title": "筛选后的任务" if filtered else "旧筛选结果",
                        "status": "failed", "progress": 0,
                    }],
                }),
            )

        await page.route(re.compile(r"/api/jobs(?:\?.*)?$"), jobs)
        await page.evaluate("()=>window.Douku.navigate('/jobs')")
        await entered(gate)
        await page.evaluate("()=>window.Douku.navigate('/jobs?status=failed')")
        await expect(page.locator(".job-row h2")).to_have_text("筛选后的任务")
        gate["release"].set()
        await page.wait_for_timeout(120)
        await expect(page.locator(".job-row h2")).to_have_text("筛选后的任务")
        await expect(page.locator("#jobs-filter-action")).to_have_value("failed")


@pytest.mark.parametrize("save_status", [None, 500])
@pytest.mark.parametrize("destination", ["imports", "other-topic"])
async def test_topic_source_save_does_not_reopen_departed_topic(save_status, destination):
    async with browser_fixture() as (page, topics, gates):
        first, second = topics
        await page.evaluate("id=>window.Douku.navigate('/topics/'+id)", first)
        await page.wait_for_selector("#topic-sources-pane input")
        gate = block(gates, f"/api/topics/{first}/sources", status=save_status)
        await page.locator("#topic-sources-pane input").first.uncheck()
        await entered(gate)
        path = "/imports" if destination == "imports" else f"/topics/{second}"
        await page.evaluate("path=>window.Douku.navigate(path)", path)
        if destination == "other-topic":
            await expect(page.locator("#topic-header-title")).to_have_text("第二个专题")
        gate["release"].set()
        await page.wait_for_timeout(150)
        assert urlsplit(page.url).path == path
        assert await page.locator("#app-shell").get_attribute("data-page") == (
            "imports" if destination == "imports" else "topic"
        )
        if destination == "other-topic":
            await expect(page.locator("#topic-header-title")).to_have_text("第二个专题")


async def test_collection_https_cover_loads_with_real_csp_headers():
    async with browser_fixture() as (page, _topics, _gates):
        image_url = "https://cdn.example.test/cover.png"
        fetched = []

        async def image_response(route):
            fetched.append(route.request.url)
            assert "referer" not in route.request.headers
            await route.fulfill(
                content_type="image/png",
                body=base64.b64decode(
                    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
                    "/x8AAwMCAO+aX1sAAAAASUVORK5CYII="
                ),
            )

        async def collection_response(route):
            data = {
                "job_id": "cover-fixture", "status": "needs_selection", "complete": True,
                "profile": {"name": "封面测试"}, "summary": {"discovered": 1, "undecided": 1},
                "total": 1, "works": [{
                    "work_id": "123", "title": "作品", "ordinal": 1, "can_import": True,
                    "decision": "pending", "disposition": "pending", "source_kind": "video",
                    "canonical_url": "https://www.douyin.com/video/123",
                    "thumbnail_url": image_url,
                }],
            }
            if urlsplit(route.request.url).path == "/api/collection-imports":
                data = [data]
            await route.fulfill(content_type="application/json", body=json.dumps(data))

        await page.route("https://cdn.example.test/**", image_response)
        await page.route("**/api/collection-imports**", collection_response)
        await page.evaluate(
            "()=>window.Douku.navigate('/imports/collections?job=cover-fixture')"
        )
        cover = page.locator("#collection-items img").first
        await expect(cover).to_have_attribute("src", image_url)
        await expect(cover).to_have_js_property("naturalWidth", 1)
        assert fetched == [image_url]
