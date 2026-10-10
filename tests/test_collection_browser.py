from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.async_api import async_playwright

from douyin_wiki.errors import ExternalToolError

MIX = "7651028428363139126"
DETAIL = "/aweme/v1/web/mix/detail/"
LIST = "/aweme/v1/web/mix/aweme/"
ORIGIN = "https://www.douyin.com"
BRIDGE = r"""
const api={
  async detail({mixId}) {
    return fetch('/aweme/v1/web/mix/detail/?mix_id='+mixId).then(r=>r.json());
  },
  async list({mixId,cursor,count,mixScene}) {
    return fetch('/aweme/v1/web/mix/aweme/?mix_id='+mixId+
      '&cursor='+cursor+'&count='+count).then(r=>r.json());
  },
  async legacy({mixId,cursor,count}) {
    throw Error('Unverified legacy export must never be called: /aweme/v1/web/mix/aweme/');
  }
};
const req=()=>api;
req.m={fixture:function(){return ['/aweme/v1/web/mix/detail/','/aweme/v1/web/mix/aweme/'];}};
window.webpackChunkdouyin_web=[];
window.webpackChunkdouyin_web.push=function(v){Array.prototype.push.call(this,v);return req;};
window.fixtureAPI=api;
"""


def client(page):
    name = "douyin_wiki.adapters.collection_browser"
    assert importlib.util.find_spec(name), "collection native browser client is missing"
    return importlib.import_module(name).CollectionBrowserClient(page)


@pytest.fixture
async def website():
    calls, state = [], {"bootstrap": False, "bridge": True, "status": 200, "delay": 0}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()

        async def handle(route):
            parsed = urlsplit(route.request.url)
            if parsed.path == "/":
                script = BRIDGE if state["bridge"] else ""
                if state["bootstrap"]:
                    script += (
                        "fixtureAPI.list({mixId:'" + MIX + "',cursor:0,count:20})"
                        ".then(()=>window.booted=true);"
                    )
                await route.fulfill(body=f"<script>{script}</script>", content_type="text/html")
                return
            calls.append((parsed.netloc, parsed.path, parse_qs(parsed.query)))
            if state["delay"]:
                await asyncio.sleep(state["delay"])
            await route.fulfill(
                status=state["status"],
                content_type="application/json",
                headers={"Access-Control-Allow-Origin": "*"},
                body=json.dumps(
                    {
                        "status_code": 0,
                        "has_more": 0,
                        "cursor": 2,
                        "aweme_list": [
                            {"aweme_id": 7644758067783852901, "video": {"duration": 1000}}
                        ],
                        "mix_info": {"mix_id": MIX},
                    }
                ),
            )

        await context.route("**/*", handle)
        page = await context.new_page()
        try:
            yield page, calls, state
        finally:
            await browser.close()


async def test_collection_native_call_uses_exact_id_cursor_and_count(website):
    page, calls, _ = website
    async with client(page) as api:
        await page.goto(ORIGIN)
        raw = await api.read_page(resource="list", mix_id=MIX, cursor="2", count=2)
    assert calls == [("www.douyin.com", LIST, {"mix_id": [MIX], "cursor": ["2"], "count": ["2"]})]
    assert json.loads(raw.body)["aweme_list"][0]["aweme_id"] == 7644758067783852901


async def test_collection_listener_captures_prefetched_matching_page(website):
    page, calls, state = website
    state["bootstrap"] = True
    async with client(page) as api:
        await page.goto(ORIGIN)
        await page.wait_for_function("window.booted")
        raw = await api.read_page(resource="list", mix_id=MIX)
    assert raw.mix_id == MIX
    assert len(calls) == 1


async def test_collection_rejects_other_mix_and_late_epoch_response(website):
    page, calls, state = website
    async with client(page) as api:
        await page.goto(ORIGIN)
        await page.evaluate("url=>fetch(url)", ORIGIN + LIST + "?mix_id=99&cursor=0&count=20")
        raw = await api.read_page(resource="list", mix_id=MIX)
        assert raw.mix_id == MIX
        state["delay"] = 0.2
        pending = asyncio.create_task(api.read_page(resource="list", mix_id=MIX, cursor="2"))
        await page.wait_for_timeout(30)
        await page.goto(ORIGIN + "/?new=1")
        with pytest.raises(ExternalToolError):
            await pending
    assert calls[0][2]["mix_id"] == ["99"]


async def test_collection_client_closes_listeners_and_rejects_more_reads(website):
    page, _, _ = website
    api = client(page)
    async with api:
        await page.goto(ORIGIN)
        await api.read_page(resource="detail", mix_id=MIX)
    with pytest.raises(ExternalToolError):
        await api.read_page(resource="list", mix_id=MIX)


async def test_collection_missing_native_bridge_never_uses_unsigned_http(website, monkeypatch):
    page, calls, state = website
    api = client(page)
    module = importlib.import_module("douyin_wiki.adapters.collection_browser")
    monkeypatch.setattr(module, "_BRIDGE_WAIT_BUDGET", 0.01)
    state["bridge"] = False
    async with api:
        await page.goto(ORIGIN)
        with pytest.raises(ExternalToolError):
            await api.read_page(resource="list", mix_id=MIX)
    assert calls == []


async def test_collection_retryable_status_has_three_attempts(website, monkeypatch):
    page, calls, state = website
    api = client(page)
    module = importlib.import_module("douyin_wiki.adapters.collection_browser")
    monkeypatch.setattr(module, "_BACKOFF", (0.001, 0.002))
    state["status"] = 429
    async with api:
        await page.goto(ORIGIN)
        with pytest.raises(ExternalToolError):
            await api.read_page(resource="list", mix_id=MIX)
    assert len(calls) == 3
