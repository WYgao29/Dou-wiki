"""Verify chat with real local SSE, fake model and temporary Vault; no external traffic."""

from __future__ import annotations

import asyncio
import json
import socket
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

import httpx
import uvicorn
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from douyin_wiki.errors import ModelConfigurationError  # noqa: E402
from douyin_wiki.webapp.app import WEB_VERSION, create_app  # noqa: E402
from douyin_wiki.webapp.chat import ChatChunk  # noqa: E402
from scripts.verify_readwise_library import populate  # noqa: E402
from scripts.verify_readwise_reader import reader_fixture  # noqa: E402
from tests.support.web import _web_fixture  # noqa: E402

ANSWER = (
    "核心结论：用问题串联资料，在回顾中形成自己的判断。\n\n"
    "- 保留作品原话，方便核验。\n- 把 AI 推断与证据分开。\n\n"
    "继续追问：哪些做法适合自己的工作流？"
)


class SlowProvider:
    model = "本地测试模型"
    configured = True
    mode = "normal"

    def __init__(self):
        self.prompts = []
        self.release = asyncio.Event()
        self.release.set()

    async def stream(self, messages):
        self.prompts.append(messages)
        if not self.configured:
            raise ModelConfigurationError("尚未配置模型")
        if self.mode == "error":
            raise httpx.ReadError("模拟流式服务失败")
        yield ChatChunk(text=ANSWER[:26])
        await self.release.wait()
        await asyncio.sleep(0.08)
        yield ChatChunk(text=ANSWER[26:])
        yield ChatChunk(usage={"prompt_tokens": 45, "completion_tokens": 35, "total_tokens": 80})


async def verify():
    out = ROOT / "docs/screenshots/redesign/phase-4"
    out.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="douku-chat-check-") as tmp:
        config, service = _web_fixture(Path(tmp))
        populate(config, service)
        reader_fixture(config, service)
        for entry in service.database.list_entries():
            service.indexer.index_entry(entry, service.get_entry(entry.id)["data"])
        topic_id = service.create_topic("阅读与知识管理", ["dy-208", "dy-201"])["topic"]["id"]
        provider = SlowProvider()
        app = create_app(config, service=service, chat_provider=provider, start_watcher=False)
        with socket.socket() as server_socket:
            server_socket.bind(("127.0.0.1", 0))
            port = server_socket.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
            task = asyncio.create_task(server.serve(sockets=[server_socket]))
            base = f"http://127.0.0.1:{port}"
            try:
                while not server.started:
                    if task.done():
                        await task
                    await asyncio.sleep(0.01)
                async with (
                    httpx.AsyncClient(base_url=base, headers={"Origin": base}) as client,
                    async_playwright() as p,
                ):
                    browser = await p.chromium.launch()
                    page = await browser.new_page(
                        viewport={"width": 1440, "height": 900}, device_scale_factor=2
                    )
                    page.set_default_timeout(8000)
                    errors, external, writes = [], [], []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.on(
                        "request",
                        lambda request: (
                            writes.append(request.url)
                            if request.method in {"POST", "PUT", "DELETE"}
                            else None
                        ),
                    )

                    async def route(r):
                        url = urlsplit(r.request.url)
                        if url.hostname != "127.0.0.1":
                            external.append(r.request.url)
                            await r.abort()
                        elif url.path.endswith("/events"):
                            await r.fulfill(content_type="text/event-stream", body=": fixture\n\n")
                        else:
                            await r.continue_()

                    await page.route("**/*", route)

                    async def navigate(path):
                        await page.evaluate("path => window.Douku.navigate(path)", path)
                        if path.startswith("/articles/"):
                            await page.wait_for_selector("#article-content .article-body p")
                        elif path.startswith("/topics/"):
                            await page.wait_for_selector(".topic-artifacts")
                        else:
                            await page.wait_for_selector("#library-results:not(.hidden)")
                        await page.click("#panel-tab-chat")
                        await page.wait_for_selector("#chat-input")

                    async def send(text):
                        await page.fill("#chat-input", text)
                        await page.press("#chat-input", "Enter")
                        await page.wait_for_function("() => state.sending")
                        await page.wait_for_function("() => !state.sending")
                        assert await page.locator(".message.assistant.error").count() == 0

                    async def session():
                        sid = await page.locator("#session-select").input_value()
                        result = await client.get(f"/api/chat/sessions/{sid}")
                        result.raise_for_status()
                        return result.json()

                    async def screenshot(name):
                        await page.evaluate("document.fonts.ready")
                        await page.screenshot(path=out / f"{name}.png", animations="disabled")

                    await page.goto(base)
                    await page.wait_for_selector("#library-results:not(.hidden)")
                    await page.click("#panel-tab-chat")
                    await page.wait_for_selector(".chat-examples button")
                    assert await page.locator(".chat-examples button").count() == 3
                    initial_writes = len(writes)
                    await page.locator(".chat-examples button").first.click()
                    assert "最近一周" in await page.locator("#chat-input").input_value()
                    assert len(writes) == initial_writes
                    assert await page.evaluate("document.activeElement.id") == "chat-input"
                    await screenshot("welcome-light")
                    await page.fill("#chat-input", "阅读 问题 思考")
                    provider.release.clear()
                    await page.press("#chat-input", "Enter")
                    await page.wait_for_selector(".message.is-streaming")
                    await page.wait_for_function(
                        "text => document.querySelector('.is-streaming')"
                        ".textContent.includes(text)",
                        arg=ANSWER[:26],
                    )
                    cursor = await page.locator(".is-streaming").evaluate("""e => {
                      const s=getComputedStyle(e,'::after');
                      return {width:s.width,height:s.height,animation:s.animationName};
                    }""")
                    assert cursor == {
                        "width": "8px",
                        "height": "16px",
                        "animation": "chat-cursor",
                    }, cursor
                    assert await page.locator("#send-button").is_disabled()
                    assert await page.locator("#session-select").is_disabled()
                    assert await page.locator(".is-streaming .message-actions").count() == 0
                    await page.fill("#chat-input", "还未发送的草稿")
                    await page.press("#chat-input", "Enter")
                    assert await page.locator("#chat-input").input_value() == "还未发送的草稿"
                    await screenshot("streaming-light")
                    await page.emulate_media(reduced_motion="reduce")
                    assert (
                        await page.locator(".is-streaming").evaluate(
                            "e=>getComputedStyle(e,'::after').animationName"
                        )
                        == "none"
                    )
                    await page.emulate_media(reduced_motion="no-preference")
                    provider.release.set()
                    await page.wait_for_function("() => !state.sending")
                    library = await session()
                    assert library["session"]["scope"] == "library"
                    assert library["messages"][-1]["content"] == ANSWER
                    assert "80 token" in await page.locator("#token-label").inner_text()
                    assert await page.locator(".citation-number").first.inner_text() == "[1]"
                    assert (
                        await page.locator(".citation-preview").first.evaluate(
                            "e=>getComputedStyle(e).opacity"
                        )
                        == "1"
                    )
                    citation_styles = await page.locator(".citation-preview").first.evaluate(
                        """e => {
                      const s=getComputedStyle(e);
                      return {fontSize:s.fontSize,color:s.color,clamp:s.webkitLineClamp};
                    }"""
                    )
                    assert citation_styles == {
                        "fontSize": "13px",
                        "color": "rgb(102, 102, 102)",
                        "clamp": "2",
                    }, citation_styles
                    assert await page.locator(".message.user .message-actions").count() == 0
                    styles = await page.evaluate("""() => {
                      const user=getComputedStyle(document.querySelector('.message.user'));
                      const assistantNode=document.querySelector('.message.assistant');
                      const assistant=getComputedStyle(assistantNode);
                      return {userBG:user.backgroundColor,assistantBG:assistant.backgroundColor,
                        assistantBorder:assistant.borderTopWidth,
                        paragraphColor:getComputedStyle(assistantNode.querySelector('p')).color,
                        assistantColor:assistant.color,
                        actions:getComputedStyle(document.querySelector('.message-actions')).opacity};
                    }""")
                    assert (
                        styles["assistantBG"] == "rgba(0, 0, 0, 0)"
                        and styles["assistantBorder"] == "0px"
                    ), styles
                    assert styles["userBG"] == "rgb(244, 242, 238)", styles
                    assert styles["actions"] == "1", styles
                    assert styles["paragraphColor"] == styles["assistantColor"], styles
                    await page.fill("#chat-input", "")
                    await screenshot("library-light")
                    await send("再比较一次资料里的共同线索")
                    await page.mouse.move(30, 30)
                    await page.locator("#chat-menu-toggle").focus()
                    await page.wait_for_timeout(150)
                    assert (
                        await page.locator(".message.assistant .message-actions").first.evaluate(
                            "e=>getComputedStyle(e).opacity"
                        )
                        == "0"
                    )
                    await page.locator(".message.assistant .save-inspiration").first.focus()
                    await page.wait_for_timeout(150)
                    assert (
                        await page.locator(".message.assistant .message-actions").first.evaluate(
                            "e=>getComputedStyle(e).opacity"
                        )
                        == "1"
                    )
                    await page.locator("#main-content").focus()
                    await navigate("/articles/dy-208")
                    assert "当前文章" in await page.locator("#chat-context").inner_text()
                    await page.click("#new-session")
                    await page.wait_for_selector(".chat-examples button")
                    assert (
                        "核心论据" in await page.locator(".chat-examples button").first.inner_text()
                    )
                    await send("这篇的核心论据是什么？")
                    article = await session()
                    assert article["session"]["context_entry_id"] == "dy-208"
                    assert "当前文章（优先）" in "\n".join(
                        x["content"] for x in provider.prompts[-1]
                    )
                    alternate_response = await client.post(
                        "/api/chat/sessions",
                        json={"scope": "entry", "context_entry_id": "dy-208"},
                    )
                    alternate_response.raise_for_status()
                    alternate_id = alternate_response.json()["id"]
                    await page.evaluate("loadSessions(false)")
                    assert not await page.locator(
                        f"#session-select option[value='{library['session']['id']}']"
                    ).count()

                    async def delayed_history(r):
                        await asyncio.sleep(0.3)
                        await r.continue_()

                    history_route = f"**/api/chat/sessions/{alternate_id}"
                    await page.route(history_route, delayed_history)
                    await page.select_option("#session-select", alternate_id)
                    await page.select_option("#session-select", article["session"]["id"])
                    await page.wait_for_timeout(400)
                    assert (
                        await page.locator(".message.user").last.inner_text()
                        == "这篇的核心论据是什么？"
                    )
                    await page.unroute(history_route, delayed_history)
                    await screenshot("article-light")
                    await page.locator(".save-inspiration", has_text="保存为灵感").last.click()
                    await page.wait_for_selector("#inspiration-dialog[open]")
                    assert await page.locator("#inspiration-text").input_value() == ANSWER
                    await page.click("#confirm-inspiration")
                    await page.wait_for_selector("#inspiration-dialog[open]", state="hidden")
                    article_data = (await client.get("/api/articles/dy-208")).json()
                    assert any(n["text"] == ANSWER for n in article_data["item"]["inspirations"])
                    await page.wait_for_selector(".toast:not(.show)", state="attached")
                    assert await page.evaluate(
                        "Boolean(document.activeElement.closest('.message-actions'))"
                    )
                    await page.click("#panel-tab-chat")
                    citation = article["messages"][-1]["citations"][0]
                    await page.locator(".citation-title").first.click()
                    await page.wait_for_selector("mark.citation-highlight")
                    assert citation["entry_id"] == "dy-208"
                    await page.click("#panel-tab-chat")
                    await screenshot("citation-light")
                    for theme in ("dark", "light"):
                        await page.evaluate(
                            "theme => document.documentElement.dataset.theme=theme", theme
                        )
                        await page.locator("#main-content").evaluate("e => e.scrollTop=0")
                        await page.wait_for_timeout(150)
                        await screenshot(f"article-{theme}")
                        await navigate("/")
                        await screenshot(f"library-{theme}")
                        await navigate("/articles/dy-208")
                    await navigate(f"/topics/{topic_id}")
                    await page.click("#new-session")
                    await page.wait_for_selector(".chat-examples button")
                    assert (
                        "主要分歧" in await page.locator(".chat-examples button").first.inner_text()
                    )
                    assert "layers" in await page.locator("#chat-context use").get_attribute("href")
                    await send("阅读 问题 思考")
                    topic = await session()
                    assert topic["session"]["context_topic_id"] == topic_id
                    assert set(c["entry_id"] for c in topic["messages"][-1]["citations"]) <= {
                        "dy-208",
                        "dy-201",
                    }
                    assert "当前专题边界" in "\n".join(x["content"] for x in provider.prompts[-1])
                    await screenshot("topic-light")
                    await page.locator(".save-inspiration", has_text="保存为专题笔记").last.click()
                    await page.wait_for_selector("#destructive-dialog[open]")
                    await page.click("#confirm-destructive")
                    await page.wait_for_selector(".toast", state="visible")
                    await page.wait_for_function(
                        "() => document.querySelector('.topic-artifacts')"
                        ".textContent.includes('对话笔记')"
                    )
                    topic_data = (await client.get(f"/api/topics/{topic_id}")).json()
                    assert any(n["content_markdown"] == ANSWER for n in topic_data["artifacts"])
                    await navigate("/articles/dy-209")
                    assert "只读 Markdown" in await page.locator("#chat-context").inner_text()
                    assert await page.locator(".save-inspiration").count() == 0
                    await send("只读资料使用什么范围？")
                    assert (await session())["session"]["scope"] == "library"
                    assert await page.locator(".save-inspiration").count() == 0
                    provider.mode = "error"
                    await page.fill("#chat-input", "模拟服务失败")
                    await page.press("#chat-input", "Enter")
                    await page.wait_for_selector(".message.assistant.error")
                    await page.wait_for_function("() => !state.sending")
                    await page.wait_for_timeout(200)
                    assert await page.locator(".message.assistant.error").count() == 1
                    assert await page.locator(".message.is-streaming").count() == 0
                    assert (
                        await page.locator(".message.assistant.error .message-actions").count() == 0
                    )
                    await screenshot("error-light")
                    provider.mode = "normal"
                    await send("服务恢复后的重试")
                    assert await page.locator(".message.assistant.error").count() == 0

                    async def incomplete_stream(r):
                        await r.fulfill(
                            content_type="text/event-stream",
                            body='event: delta\ndata: {"text":"未完整收到的回答"}\n\n',
                        )

                    stream_route = "**/api/chat/sessions/*/messages"
                    await page.route(stream_route, incomplete_stream)
                    await page.fill("#chat-input", "模拟流式连接中断")
                    await page.press("#chat-input", "Enter")
                    await page.wait_for_selector(".message.assistant.error")
                    assert "回答中断" in await page.locator(".message.assistant.error").inner_text()
                    await page.unroute(stream_route, incomplete_stream)
                    await send("连接恢复后的重试")
                    sizes = []
                    for width in (1440, 1280, 1100, 800, 390, 320):
                        await page.set_viewport_size({"width": width, "height": 900})
                        await page.evaluate("window.Douku.navigate('/articles/dy-209')")
                        await page.wait_for_selector("#article-view:not(.hidden) .article-body")
                        await page.locator("#main-content").focus()
                        await page.keyboard.press("Shift+c")
                        await page.wait_for_selector("#chat-input")
                        metrics = await page.evaluate("""() => {
                          const box=document.querySelector('#chat-panel');
                          const input=document.querySelector('#chat-input');
                          const sendNode=document.querySelector('#send-button');
                          const send=sendNode.getBoundingClientRect();
                          return {overflow:box.scrollWidth>box.clientWidth,
                            button:[send.width,send.height],focus:document.activeElement.id,
                            hint:document.querySelector('.composer-hint').getBoundingClientRect().width};
                        }""")
                        assert not metrics["overflow"] and metrics["button"] == [28, 28], metrics
                        assert metrics["focus"] == "chat-input", {"width": width, **metrics}
                        await page.fill("#chat-input", "一行\n" * 10)
                        assert (
                            await page.locator("#chat-input").evaluate(
                                "e=>e.getBoundingClientRect().height"
                            )
                            == 160
                        )
                        await page.fill("#chat-input", "")
                        await page.press("#chat-input", "Shift+Enter")
                        assert await page.locator("#chat-input").input_value() == "\n"
                        sizes.append({"width": width, **metrics})
                        await page.press("#chat-input", "Escape")
                    await page.set_viewport_size({"width": 390, "height": 900})
                    await page.goto(base + "/articles/dy-208")
                    await page.wait_for_selector("#article-content .article-body")
                    await page.locator("#main-content").focus()
                    await page.keyboard.press("Shift+c")
                    await page.select_option("#session-select", article["session"]["id"])
                    await page.wait_for_selector(".citation-title")
                    await screenshot("mobile-chat")
                    provider.configured = False
                    await page.set_viewport_size({"width": 1440, "height": 900})
                    await page.goto(base)
                    await page.wait_for_selector("#library-results:not(.hidden)")
                    await page.click("#panel-tab-chat")
                    await page.wait_for_selector("#chat-persistent-error a")
                    assert (
                        await page.locator("#chat-persistent-error").get_attribute("role")
                        == "status"
                    )
                    assert await page.locator("#chat-persistent-error a").inner_text() == "去设置"
                    await screenshot("unconfigured-light")
                    await page.click("#chat-persistent-error a")
                    await page.wait_for_selector("#model-view:not(.hidden)")
                    assert not errors and not external, (errors, external)
                    (out / "verification.json").write_text(
                        json.dumps(
                            {
                                "web_version": WEB_VERSION,
                                "browser": "Chromium",
                                "transport": "real local HTTP/SSE",
                                "scope_checks": [
                                    "library",
                                    "entry",
                                    "topic",
                                    "readonly-library",
                                ],
                                "checks": [
                                    "examples-fill-only",
                                    "streaming-cursor",
                                    "reduced-motion",
                                    "preserve-unsent-draft",
                                    "usage",
                                    "citation-highlight",
                                    "actions-hover-focus-last",
                                    "save-verbatim-inspiration",
                                    "save-focus-return",
                                    "save-verbatim-topic-note",
                                    "persistent-stream-error",
                                    "retry",
                                    "incomplete-stream",
                                    "history-switch-race",
                                    "textarea-resize",
                                    "shift-enter",
                                    "unconfigured-settings",
                                ],
                                "sizes": sizes,
                                "page_errors": errors,
                                "external_requests": external,
                            },
                            ensure_ascii=False,
                            indent=2,
                        )
                    )
                    print(
                        json.dumps(
                            {"result": "passed", "screenshots": str(out), "widths": len(sizes)},
                            ensure_ascii=False,
                        )
                    )
                    await browser.close()
            finally:
                server.should_exit = True
                await task


if __name__ == "__main__":
    asyncio.run(verify())
