"""Searching must continue when changing sections within the library."""

import asyncio

import pytest

from scripts.verify_review_web_races import block, browser_fixture, entered


@pytest.mark.parametrize("boundary", ["debounce", "pending", "return-from-imports"])
async def test_library_section_navigation_preserves_current_search(boundary):
    async with browser_fixture() as (page, _topics, gates):
        query = block(gates, "/api/library?q=%E4%B8%8D%E5%AD%98%E5%9C%A8")
        await page.locator("#search-input").fill("不存在")
        if boundary == "pending":
            await entered(query)
        elif boundary == "return-from-imports":
            await page.evaluate("()=>window.Douku.navigate('/imports')")
        await page.locator(".nav-item[data-section='recent']").click()
        await entered(query)
        query["release"].set()
        await page.wait_for_function("()=>state.items.length===0")
        assert await page.evaluate("()=>state.query") == "不存在"
        assert await page.locator("#search-input").input_value() == "不存在"
        assert await page.locator("#app-shell").get_attribute("data-page") == "library"
        assert await page.locator("#library-error").is_hidden()


async def test_returning_to_library_replaces_unfinished_initial_request():
    startup = {"entered": asyncio.Event(), "release": asyncio.Event()}
    async with browser_fixture(startup_gate=startup) as (page, _topics, _gates):
        await page.evaluate("()=>window.Douku.navigate('/imports')")
        await page.locator(".nav-item[data-section='all']").click()
        startup["release"].set()
        await page.wait_for_function("()=>state.libraryLoaded && state.items.length===2")
        assert await page.evaluate("()=>state.libraryRequestId") >= 2
        assert await page.locator("#app-shell").get_attribute("data-page") == "library"


@pytest.mark.parametrize("initial_route", ["topic", "article"])
async def test_initial_deep_link_does_not_override_navigation_during_loading(initial_route):
    startup = {"entered": asyncio.Event(), "release": asyncio.Event()}
    async with browser_fixture(startup_gate=startup, initial_route=initial_route) as (
        page,
        _topics,
        _gates,
    ):
        await page.evaluate("()=>window.Douku.navigate('/imports')")
        startup["release"].set()
        await page.wait_for_function("()=>window.__reviewEventsReady")
        assert await page.locator("#app-shell").get_attribute("data-page") == "imports"
        assert await page.evaluate("()=>location.pathname") == "/imports"
