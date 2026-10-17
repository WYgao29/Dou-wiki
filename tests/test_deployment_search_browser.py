"""Search responses cannot replace newer query, scope or mode selections."""

import asyncio
import json
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.async_api import expect

from scripts.verify_review_web_races import browser_fixture, entered


@pytest.mark.parametrize("change", ["query", "filter", "mode"])
@pytest.mark.parametrize("old_status", [200, 500])
async def test_semantic_search_discards_old_results_and_errors(change, old_status):
    async with browser_fixture() as (page, _topics, _gates):
        item = await page.evaluate("state.items.find(item => item.entry_id === 'dy-123')")
        gate = {"entered": asyncio.Event(), "release": asyncio.Event()}
        completed = asyncio.Event()
        requests = []

        async def response(route):
            params = parse_qs(urlsplit(route.request.url).query)
            requests.append(params)
            first = len(requests) == 1
            if first:
                gate["entered"].set()
                await gate["release"].wait()
            payload = {
                "items": [
                    {
                        **item,
                        "title": "旧结果" if first else "当前结果",
                        "search_evidence": [
                            {"snippet": "摘要", "timestamp_ms": None},
                            {"snippet": "支持结果的原话。", "timestamp_ms": 1200},
                        ],
                    }
                ],
                "facets": {"authors": [item["author"]], "tags": item["tags"]},
                "notice": "旧请求提示" if first else "当前范围的相关证据",
            }
            await route.fulfill(
                status=old_status if first else 200,
                content_type="application/json",
                body=json.dumps(payload),
            )
            if first:
                completed.set()

        await page.route("**/api/library/search**", response)
        await page.locator("#search-mode").select_option("semantic")
        await page.locator("#search-input").fill("旧问题")
        await entered(gate)
        if change == "query":
            await page.locator("#search-input").fill("当前问题")
        elif change == "filter":
            await page.evaluate("state.tags = ['咖啡']; renderLibrary();")
        else:
            await page.locator("#search-mode").select_option("keyword")
        if change == "mode":
            await expect(page.locator("#empty-results")).to_be_visible()
        else:
            await expect(page.locator("#library-results h2")).to_have_text(["当前结果"])
            await expect(page.locator(".search-evidence")).to_have_text("00:01 · 支持结果的原话。")
            if change == "filter":
                assert requests[-1]["tag"] == ["咖啡"]
        gate["release"].set()
        await asyncio.wait_for(completed.wait(), 3)
        await page.wait_for_timeout(120)
        await expect(page.locator("#library-error")).to_be_hidden()
        assert "旧请求提示" not in await page.locator("#search-capability").inner_text()
        if change == "mode":
            await expect(page.locator("#empty-results")).to_be_visible()
        else:
            await expect(page.locator("#library-results h2")).to_have_text(["当前结果"])


async def test_failed_semantic_search_can_reload_after_scope_changes():
    async with browser_fixture() as (page, _topics, _gates):
        item = await page.evaluate("state.items.find(item => item.entry_id === 'dy-123')")
        attempts = 0

        async def response(route):
            nonlocal attempts
            attempts += 1
            await route.fulfill(
                status=500 if attempts == 1 else 200,
                content_type="application/json",
                body=json.dumps({"items": [item], "facets": {}, "notice": "检索已恢复"}),
            )

        await page.route("**/api/library/search**", response)
        await page.locator("#search-mode").select_option("semantic")
        await page.locator("#search-input").fill("问题")
        await expect(page.locator("#library-error")).to_be_visible()
        await page.evaluate("state.tags = ['咖啡']; renderLibrary();")
        await expect(page.locator("#search-capability")).to_have_text("检索已恢复")
        await page.evaluate("state.tags = []; renderLibrary();")
        await expect(page.locator("#library-results h2")).to_have_text(item["title"])
        await page.wait_for_function("state.pendingLibraryScope === null")
        assert attempts == 3


@pytest.mark.parametrize("width", [320, 375])
async def test_search_toolbar_remains_accessible_on_narrow_screens(width):
    async with browser_fixture() as (page, _topics, _gates):
        await page.emulate_media(reduced_motion="reduce")
        await page.set_viewport_size({"width": width, "height": 812})
        await page.evaluate("closeDrawers(false)")
        await page.locator("#search-mode").select_option("semantic")
        await page.locator("#topic-select-toggle").click()
        await expect(page.locator("#library-selection-bar")).to_be_visible()
        assert await page.locator(".library-toolbar").evaluate("""toolbar =>
            [...toolbar.querySelectorAll('button, select')]
              .filter(node => node.getClientRects().length)
              .every(node => {
                const box = node.getBoundingClientRect();
                return box.left >= 0 && box.right <= innerWidth;
              })
        """)
