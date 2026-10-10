"""Regression checks for stale library/topic responses; all browser HTTP is intercepted."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import urlsplit

from playwright.async_api import async_playwright

from douyin_wiki.webapp.app import create_app
from tests.support.web import _web_fixture, local_client


@asynccontextmanager
async def browser_fixture(*, startup_gate=None, initial_route=None):
    with (
        TemporaryDirectory(prefix="douku-web-races-") as tmp,
        patch.dict("os.environ", {"DOUYIN_WIKI_BROWSER_PROFILE": str(Path(tmp) / "profile")}),
    ):
        config, service = _web_fixture(Path(tmp))
        (config.vault_path / "wiki" / "sources" / "另一篇_456.md").write_text(
            "---\ntype: source\nvideo_id: '456'\nstatus: 正常\n---\n# 另一篇文章\n\n其他内容。\n",
            encoding="utf-8",
        )
        topics = [
            service.create_topic(title, ["dy-123"])["topic"]["id"]
            for title in ["第一个专题", "第二个专题"]
        ]
        app = create_app(
            config, service=service,  start_watcher=False
        )
        with local_client(app) as client:
            async with async_playwright() as p:
                browser = await p.chromium.launch()
                page = await browser.new_page(viewport={"width": 1440, "height": 900})
                page.set_default_timeout(4000)
                errors, external = [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                await page.add_init_script(
                    "window.EventSource=class extends EventTarget{"
                    "constructor(url){super();this.url=url;window.__reviewEventsReady=true}"
                    " close(){}}"
                )
                gates = {"/api/library": startup_gate} if startup_gate is not None else {}

                async def route(r):
                    request = r.request
                    url = urlsplit(request.url)
                    if url.hostname != "testserver":
                        external.append(request.url)
                        await r.abort()
                        return
                    key = url.path + ("?" + url.query if url.query else "")
                    gate = gates.get(key)
                    if gate:
                        gate["entered"].set()
                        await gate["release"].wait()
                        if gate.get("status"):
                            await r.fulfill(
                                status=gate["status"],
                                content_type="application/json",
                                body='{"detail":"模拟过期请求失败"}',
                            )
                            return
                    response = client.request(
                        request.method,
                        key,
                        content=request.post_data,
                        headers={
                            "content-type": request.headers.get("content-type", "application/json")
                        },
                    )
                    await r.fulfill(
                        status=response.status_code,
                        headers=dict(response.headers),
                        body=response.content,
                    )

                await page.route("**/*", route)
                initial_path = "/"
                if initial_route == "topic":
                    initial_path = f"/topics/{topics[0]}"
                elif initial_route == "article":
                    initial_path = "/articles/dy-123"
                await page.goto(f"http://testserver{initial_path}")
                if startup_gate is None:
                    await page.wait_for_function(
                        "()=>state.libraryLoaded && state.topics.length===2"
                    )
                else:
                    await entered(startup_gate)
                try:
                    yield page, topics, gates
                    assert not errors and not external, {"errors": errors, "external": external}
                finally:
                    for gate in gates.values():
                        gate["release"].set()
                    await browser.close()


def block(gates, path, *, status=None):
    gate = {"entered": asyncio.Event(), "release": asyncio.Event(), "status": status}
    gates[path] = gate
    return gate


async def entered(gate):
    await asyncio.wait_for(gate["entered"].wait(), 3)


async def library_case(page, gates, case):
    # Use the real input event/debounce, but hold HTTP responses deterministically.
    old = block(
        gates,
        "/api/library?q=%E6%B5%8B%E8%AF%95",
        status=500 if case == "library-stale-error" else None,
    )
    await page.locator("#search-input").fill("测试")
    await entered(old)
    if case == "library-leave-imports":
        await page.evaluate("()=>window.Douku.navigate('/imports')")
        old["release"].set()
        await page.wait_for_timeout(100)
        assert await page.locator("#app-shell").get_attribute("data-page") == "imports"
        assert await page.locator("#library-error").is_hidden()
        assert await page.evaluate("()=>state.items.length") == 2
        return
    new = block(gates, "/api/library?q=%E4%B8%8D%E5%AD%98%E5%9C%A8")
    await page.locator("#search-input").fill("不存在")
    if case == "library-query-before-debounce":
        old["release"].set()
        await page.wait_for_timeout(80)
        # The old response must not repaint while the new query is being debounced.
        assert await page.evaluate("()=>state.items.length") == 2
        assert await page.evaluate("()=>state.query") == "不存在"
    await entered(new)
    if case == "library-old-first":
        old["release"].set()
        await page.wait_for_timeout(80)
        assert await page.evaluate("()=>state.items.length") == 2
    new["release"].set()
    await page.wait_for_function("()=>state.items.length===0")
    old["release"].set()
    await page.wait_for_timeout(100)
    assert await page.evaluate("()=>({query:state.query,ids:state.items.map(x=>x.entry_id)})") == {
        "query": "不存在",
        "ids": [],
    }
    assert await page.locator("#search-input").input_value() == "不存在"
    assert await page.locator("#library-error").is_hidden()


async def topic_case(page, topics, gates, case):
    first, second = topics
    old = block(gates, f"/api/topics/{first}", status=404 if case == "topic-stale-404" else None)
    await page.evaluate("id=>{window.oldTopic=openTopic(id)}", first)
    await entered(old)
    if case in {"topic-leave-imports", "topic-stale-404"}:
        await page.evaluate("()=>window.Douku.navigate('/imports')")
        old["release"].set()
        await page.evaluate("()=>window.oldTopic")
        assert await page.locator("#app-shell").get_attribute("data-page") == "imports"
        assert urlsplit(page.url).path == "/imports"
        return
    if case == "topic-popstate":
        await page.evaluate(
            "()=>{history.pushState({},'', '/imports');"
            "history.pushState({},'', '/topics');history.back()}"
        )
        await page.wait_for_function("()=>location.pathname==='/imports'")
        old["release"].set()
        await page.evaluate("()=>window.oldTopic")
        assert await page.locator("#app-shell").get_attribute("data-page") == "imports"
        assert urlsplit(page.url).path == "/imports"
        return
    new = block(gates, f"/api/topics/{second}")
    await page.evaluate("id=>{window.newTopic=openTopic(id)}", second)
    await entered(new)
    if case == "topic-old-first":
        old["release"].set()
        await page.evaluate("()=>window.oldTopic")
        assert await page.evaluate("()=>state.currentTopic") == ""
    new["release"].set()
    await page.evaluate("()=>window.newTopic")
    old["release"].set()
    await page.evaluate("()=>window.oldTopic")
    assert await page.evaluate("()=>state.currentTopic") == second
    assert await page.locator("#topic-header-title").inner_text() == "第二个专题"
    assert urlsplit(page.url).path == f"/topics/{second}"


async def verify_case(case):
    async with browser_fixture() as (page, topics, gates):
        if case.startswith("library-"):
            await library_case(page, gates, case)
        else:
            await topic_case(page, topics, gates, case)


async def main():
    for case in [
        "library-new-first",
        "library-old-first",
        "library-query-before-debounce",
        "library-leave-imports",
        "library-stale-error",
        "topic-new-first",
        "topic-old-first",
        "topic-leave-imports",
        "topic-popstate",
        "topic-stale-404",
    ]:
        await verify_case(case)
        print(f"PASS {case}")


if __name__ == "__main__":
    asyncio.run(main())
