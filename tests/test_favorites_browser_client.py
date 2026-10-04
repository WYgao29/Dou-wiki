"""Exercise the real page bridge; every browser request is intercepted offline."""

from __future__ import annotations

import asyncio
import importlib
import json
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.async_api import async_playwright

from douyin_wiki.errors import ExternalToolError

DIRECTORY = "/aweme/v1/web/collects/list/"
FOLDER = "/aweme/v1/web/collects/video/list/"
ORIGIN = "https://www.douyin.com"


def client(page):
    try:
        module = importlib.import_module("douyin_wiki.adapters.favorites_browser")
    except ModuleNotFoundError:
        pytest.fail("authenticated favorites browser client has not been implemented")
    return module.FavoritesBrowserClient(page)


BRIDGE = r"""
const api = {
  async directory({cursor=0,offset=12}={}) {
    return fetch('/aweme/v1/web/collects/list/?cursor='+cursor+'&count='+offset).then(r=>r.json());
  },
  async folder({collectsId='',cursor=0,offset=0}={}) {
    return fetch('/aweme/v1/web/collects/video/list/?collects_id='+collectsId+
      '&cursor='+cursor+'&count='+offset).then(r=>r.json());
  },
  async unknown() { throw new Error('unknown export must not be invoked'); }
};
const req = () => api;
req.m = {fixture: function(){
  return ['/aweme/v1/web/collects/list/', '/aweme/v1/web/collects/video/list/'];
}};
window.webpackChunkdouyin_web = [];
window.webpackChunkdouyin_web.push = function(v){ Array.prototype.push.call(this,v); return req; };
window.fixtureAPI = api;
"""


@pytest.fixture
async def browser_fixture():
    calls = []
    state = {
        "status": 200,
        "body": None,
        "bootstrap": False,
        "bridge": True,
        "delay": 0,
        "noise": False,
    }
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()

        async def handle(route):
            request = route.request
            url = urlsplit(request.url)
            if url.path == "/":
                script = BRIDGE if state["bridge"] else ""
                if state["bootstrap"]:
                    script += (
                        "fixtureAPI.directory({cursor:'0',offset:20}).then(()=>window.booted=true);"
                    )
                await route.fulfill(body=f"<script>{script}</script>", content_type="text/html")
                return
            calls.append(
                {
                    "origin": url.netloc,
                    "path": url.path,
                    "method": request.method,
                    "query": parse_qs(url.query),
                }
            )
            if state["delay"]:
                await asyncio.sleep(state["delay"])
            if state["body"] is not None:
                body = state["body"]
            else:
                field = "collects_list" if url.path == DIRECTORY else "aweme_list"
                body = json.dumps(
                    {
                        "status_code": 0,
                        "has_more": 0,
                        "cursor": 0,
                        field: [
                            {
                                "collects_id": 7000000000000000123,
                                "aweme_id": "7000000000000000123",
                                "video": {},
                            }
                        ],
                    }
                )
            await route.fulfill(
                status=state["status"],
                body=body,
                headers={"Access-Control-Allow-Origin": "*"},
                content_type="application/json",
            )

        await context.route("**/*", handle)
        page = await context.new_page()
        try:
            yield page, calls, state
        finally:
            await browser.close()


async def test_listener_precedes_bootstrap_navigation(browser_fixture):
    page, calls, state = browser_fixture
    state["bootstrap"] = True
    async with client(page) as api:
        await page.goto(ORIGIN)
        await page.wait_for_function("window.booted")
        result = await api.read_page(resource="directory", count=20)
    assert len(calls) == 1
    assert json.loads(result.body)["collects_list"][0]["collects_id"] == 7000000000000000123


async def test_read_page_calls_native_api_with_exact_folder_id(browser_fixture):
    page, calls, _ = browser_fixture
    async with client(page) as api:
        await page.goto(ORIGIN)
        result = await api.read_page(
            resource="folder",
            folder_id="7000000000000000123",
            cursor="2",
            count=2,
            use_cached=False,
        )
    assert result.folder_id == "7000000000000000123"
    assert calls == [
        {
            "origin": "www.douyin.com",
            "path": FOLDER,
            "method": "GET",
            "query": {"collects_id": ["7000000000000000123"], "cursor": ["2"], "count": ["2"]},
        }
    ]


async def test_response_matching_ignores_wrong_folder_cursor_and_origin(browser_fixture):
    page, _, _ = browser_fixture
    async with client(page) as api:
        await page.goto(ORIGIN)
        for url in (
            ORIGIN + FOLDER + "?collects_id=99&cursor=0&count=20",
            ORIGIN + FOLDER + "?collects_id=12&cursor=99&count=20",
            "https://evil.test" + FOLDER + "?collects_id=12&cursor=0&count=20",
        ):
            await page.evaluate("url => fetch(url)", url)
        result = await api.read_page(resource="folder", folder_id="12", use_cached=True)
    assert result.folder_id == "12"
    assert result.request_cursor == "0"
    assert result.request_count == 20


async def test_force_read_does_not_use_prefetched_response(browser_fixture):
    page, calls, state = browser_fixture
    state["bootstrap"] = True
    async with client(page) as api:
        await page.goto(ORIGIN)
        await page.wait_for_function("window.booted")
        state["body"] = '{"status_code":0,"has_more":0,"collects_list":[{"collects_id":13}]}'
        result = await api.read_page(resource="directory", use_cached=False)
    assert len(calls) == 2
    assert json.loads(result.body)["collects_list"][0]["collects_id"] == 13


async def test_native_api_unavailable_does_not_fallback_to_unsigned_http(browser_fixture):
    page, calls, state = browser_fixture
    state["bridge"] = False
    async with client(page) as api:
        await page.goto(ORIGIN)
        with pytest.raises(ExternalToolError):
            await api.read_page(resource="directory")
    assert not calls


async def test_cancel_closes_listener_without_late_result(browser_fixture):
    page, _, state = browser_fixture
    state["delay"] = 0.2
    async with client(page) as api:
        await page.goto(ORIGIN)
        task = asyncio.create_task(api.read_page(resource="directory"))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    await page.wait_for_timeout(250)
    with pytest.raises(ExternalToolError):
        await api.read_page(resource="directory")


@pytest.mark.parametrize("status,body", [(403, "{}"), (200, "not json")])
async def test_non_retryable_errors_are_not_retried(browser_fixture, status, body):
    page, calls, state = browser_fixture
    state.update(status=status, body=body)
    async with client(page) as api:
        await page.goto(ORIGIN)
        with pytest.raises(ExternalToolError):
            await api.read_page(resource="directory")
    assert len(calls) == 1


async def test_retryable_status_has_only_two_retries(browser_fixture, monkeypatch):
    page, calls, state = browser_fixture
    api = client(page)
    module = importlib.import_module("douyin_wiki.adapters.favorites_browser")
    monkeypatch.setattr(module, "_BACKOFF", (0.001, 0.002))
    state["status"] = 429
    async with api:
        await page.goto(ORIGIN)
        with pytest.raises(ExternalToolError):
            await api.read_page(resource="directory")
    assert len(calls) == 3


async def test_total_budget_includes_retries(browser_fixture, monkeypatch):
    page, calls, state = browser_fixture
    api = client(page)
    module = importlib.import_module("douyin_wiki.adapters.favorites_browser")
    monkeypatch.setattr(module, "_READ_BUDGET", 0.07)
    monkeypatch.setattr(module, "_ATTEMPT_TIMEOUT", 0.03)
    monkeypatch.setattr(module, "_BACKOFF", (0.001, 0.002))
    state["delay"] = 0.3
    async with api:
        await page.goto(ORIGIN)
        start = asyncio.get_running_loop().time()
        with pytest.raises(ExternalToolError):
            await api.read_page(resource="directory")
        assert asyncio.get_running_loop().time() - start < 0.2
    assert len(calls) <= 3
