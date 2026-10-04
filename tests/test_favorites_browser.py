"""Offline browser tests: synthetic HTML only, all network access intercepted."""

from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from douyin_wiki.adapters.favorites import (
    _ACCOUNT_SNAPSHOT_JS,
    _LISTING_SNAPSHOT_JS,
    _works_from_dom,
)


@pytest.fixture
async def offline_page():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context()
        await context.route("**/*", lambda route: route.abort())
        page = await context.new_page()
        try:
            yield page
        finally:
            await browser.close()


@pytest.fixture
async def favorites_ui(offline_page):
    from urllib.parse import parse_qs, urlsplit

    page = offline_page
    root = Path(__file__).parents[1] / "src/douyin_wiki/webapp"
    template = (root / "templates/app.html").read_text()
    begin = template.index('      <section id="imports-favorites-view"')
    end = template.index('<section id="jobs-view"', begin)
    html = '<meta charset="utf-8"><style>' + (root / "static/app.css").read_text() + "</style>"
    html += template[begin:end]
    dialog_begin = template.index('  <dialog id="destructive-dialog"')
    dialog_end = template.index("</dialog>", dialog_begin) + len("</dialog>")
    html += template[dialog_begin:dialog_end]
    for script in ("shared.js", "imports-favorites.js"):
        html += "<script>" + (root / "static" / script).read_text() + "</script>"
    html += """<script>window.dispatchEvent(new CustomEvent('douku:route',
      {detail:{path:'/imports/favorites'}}));</script>"""
    directory = {
        "job_id": "fixture-directory",
        "status": "completed",
        "status_label": "已完成",
        "nickname": "模拟账号",
        "directory_only": True,
        "confirmed": False,
        "include_images": False,
        "complete": True,
        "warnings": [],
        "folders": [{"id": "12", "name": "重要", "reported_count": 1}],
        "analysis_mode": "gateway",
        "total": 0,
        "has_more": False,
        "summary": {"discovered": 0, "selected": 0, "failed": 0},
        "items": [],
    }
    works = {
        **directory,
        "job_id": "fixture-works",
        "status": "needs_selection",
        "status_label": "待选择作品",
        "directory_only": False,
        "total": 1,
        "summary": {"discovered": 1, "selected": 1, "failed": 0},
        "items": [
            {
                "work_id": "123456",
                "canonical_url": "https://www.douyin.com/video/123456",
                "title": "目标标题",
                "author": "模拟作者",
                "source_kind": "video",
                "selected": True,
                "disposition": "pending",
            }
        ],
    }
    calls = []
    removed = False

    async def handler(route):
        nonlocal removed
        parsed = urlsplit(route.request.url)
        if parsed.path == "/imports/favorites":
            await route.fulfill(body=html, content_type="text/html")
            return
        if parsed.path == "/api/system/health":
            await route.fulfill(json={"worker": {"running": True}})
            return
        if parsed.path == "/api/imports/fixture-works":
            assert route.request.method == "DELETE"
            removed = route.request.post_data_json["confirmed"]
            await route.fulfill(json={"will_delete": ["测试清单"], "will_keep": ["已入库资料"]})
            return
        if not parsed.path.startswith("/api/favorites/imports"):
            await route.abort()
            return
        query = parse_qs(parsed.query, keep_blank_values=True)
        calls.append((route.request.method, parsed.path, query))
        if parsed.path == "/api/favorites/imports":
            result = (
                {"job_id": "fixture-works", "status": "queued"}
                if route.request.method == "POST"
                else [directory]
                if removed
                else [works, directory]
            )
        else:
            result = works if parsed.path.endswith("fixture-works") else directory
            if query.get("query", [""])[0] and query["query"][0] not in "目标标题":
                result = {**result, "items": [], "total": 0}
        await route.fulfill(json=result)

    await page.route("http://fixture.test/**", handler)

    async def open_page(job_id):
        url = "http://fixture.test/imports/favorites"
        if job_id:
            url += "?job=" + job_id
        await page.goto(url, wait_until="domcontentloaded")
        await page.locator("#favorites-scan").wait_for(state="visible")
        await page.wait_for_function("!document.getElementById('favorites-scope').disabled")

    return page, open_page, calls


@pytest.mark.parametrize("job_id", [None, "fixture-directory"])
async def test_work_search_waits_for_work_inventory(favorites_ui, job_id):
    page, open_page, _ = favorites_ui
    await open_page(job_id)
    for control in ("search", "query", "filter", "only-new"):
        assert await page.locator("#favorites-" + control).is_disabled()
    assert "先点击上方“更新抖音收藏”" in await page.locator("#favorites-search-hint").inner_text()
    if job_id:
        assert "收藏夹目录已读取" in await page.locator("#favorites-status").inner_text()
        assert "尚未读取作品" in await page.locator("#favorites-summary").inner_text()


@pytest.mark.parametrize("action", ["create", "history"])
async def test_current_favorites_job_survives_page_reload(favorites_ui, action):
    from urllib.parse import parse_qs, urlsplit

    page, open_page, _ = favorites_ui
    await open_page("fixture-directory")
    if action == "create":
        await page.locator("#favorites-scope").select_option("all")
        await page.locator("#favorites-scan").click()
    else:
        await page.locator(".fav-advanced > summary").click()
        await page.locator("#favorites-history").select_option("fixture-works")
    await page.get_by_text("目标标题", exact=True).wait_for()
    assert parse_qs(urlsplit(page.url).query)["job"] == ["fixture-works"]
    await page.reload(wait_until="domcontentloaded")
    await page.get_by_text("目标标题", exact=True).wait_for()
    assert "待选择作品" in await page.locator("#favorites-status").inner_text()
    assert await page.locator("#favorites-search").is_enabled()


async def test_existing_work_inventory_can_search_again_after_no_matches(favorites_ui):
    page, open_page, calls = favorites_ui
    await open_page("fixture-works")
    await page.locator("#favorites-query").fill("不存在")
    async with page.expect_response(
        lambda response: "query=%E4%B8%8D%E5%AD%98%E5%9C%A8" in response.url
    ):
        await page.locator("#favorites-search").click()
    await page.get_by_text("当前筛选下暂无作品。", exact=True).wait_for()
    assert await page.locator("#favorites-search").is_enabled()
    assert await page.locator("#favorites-query").is_enabled()
    await page.locator("#favorites-query").fill("目标")
    await page.locator("#favorites-search").click()
    await page.get_by_text("目标标题", exact=True).wait_for()
    assert not any(method == "POST" for method, _, _ in calls)


async def test_deleted_favorites_job_is_not_reopened_by_reload(favorites_ui):
    from urllib.parse import parse_qs, urlsplit

    page, open_page, _ = favorites_ui
    await open_page("fixture-works")
    await page.locator(".fav-advanced > summary").click()
    await page.locator("#favorites-delete-history").click()
    await page.get_by_role("button", name="确认删除", exact=True).click()
    await page.get_by_text("已删除该导入记录，已入库资料仍保留。", exact=True).wait_for()
    assert "job" not in parse_qs(urlsplit(page.url).query)
    await page.reload(wait_until="domcontentloaded")
    await page.get_by_text("尚未开始更新", exact=True).wait_for()
    assert await page.locator("#favorites-search").is_disabled()


async def test_observed_dom_boundary_extracts_relative_links_and_ignores_footer(offline_page):
    page = offline_page
    await page.set_content("""<div><h1>模拟账号</h1><span>抖音号：fixture_123</span></div>
        <div id="semiTabPanelfavorite_collection"></div><div id="semiTabPanelvideo"></div>
        <ul class="cPDrcaOY QhXy7t32"><li><div><a href="/video/123456">
          <img alt="模拟作者：标题含 &lt;script&gt; 内容"></a></div></li>
        <li><div><a href="//www.douyin.com/note/234567"><img alt="作者乙：图文标题"></a></div></li>
        </ul><div>暂时没有更多了</div><footer><a href="/video/999999">无关推荐</a></footer>""")
    account = await page.evaluate(_ACCOUNT_SNAPSHOT_JS)
    snapshot = await page.evaluate(_LISTING_SNAPSHOT_JS)
    works, warnings = _works_from_dom(snapshot["records"])
    assert account == {"account_id": "douyin:fixture_123", "nickname": "模拟账号"}
    assert snapshot["terminal"] is True
    assert [work.work_id for work in works] == ["123456", "234567"]
    assert works[0].title == "标题含 <script> 内容"
    assert works[0].author == "模拟作者"
    assert works[1].source_kind == "image_note"
    assert not warnings
    await page.set_content(
        '<div id="semiTabPanelvideo"></div>'
        '<footer>暂时没有更多了<a href="/video/9">推荐</a></footer>'
    )
    assert (await page.evaluate(_LISTING_SNAPSHOT_JS))["boundary_found"] is False


@pytest.mark.parametrize(
    "account_markup",
    [
        "<span>抖音号：22127912455</span><span>36岁</span>",
        "<span>抖音号：</span><span>22127912455</span><span>36岁</span>",
        "<span>抖音号：</span><div><span>22127912455</span> <span>36岁</span></div>",
        "<span>抖音号：</span><span><span>22127912455</span>"
        '<span style="display:none">copy</span></span>',
        "<span>抖音号：</span><span>"
        '<span style="display:none">copy</span><span>22127912455</span><span>36岁</span></span>',
        "<span>抖音号：</span><span>"
        '<span style="visibility:hidden">copy</span><span>22127912455</span></span>',
        "<span>抖音号：</span><span hidden>copy</span><!-- decoration -->"
        "<span>22127912455</span><span>36岁</span>",
        "<span><span>抖音号：</span></span><div><span>22127912455</span> <span>36岁</span></div>",
        "<span>抖音号： <!-- hint --></span><span>22127912455</span><span>36岁</span>",
    ],
)
async def test_account_fallback_does_not_append_adjacent_age(offline_page, account_markup):
    await offline_page.set_content("<div><h1>测试账号</h1>" + account_markup + "</div>")
    account = await offline_page.evaluate(_ACCOUNT_SNAPSHOT_JS)
    assert account["account_id"] == "douyin:22127912455"


@pytest.mark.parametrize("style", ["display:none", "visibility:hidden"])
async def test_account_fallback_ignores_hidden_identity_text(offline_page, style):
    await offline_page.set_content(
        '<div><h1>测试账号</h1><span style="' + style + '">抖音号：wrong_account</span>'
        "<span>抖音号：fixture_123</span></div>"
    )
    assert (await offline_page.evaluate(_ACCOUNT_SNAPSHOT_JS))["account_id"] == "douyin:fixture_123"


async def test_account_fallback_does_not_use_hidden_id_or_age(offline_page):
    await offline_page.set_content(
        "<div><h1>测试账号</h1><span>抖音号：</span>"
        "<span><span hidden>22127912455</span><span>36岁</span></span></div>"
    )
    assert (await offline_page.evaluate(_ACCOUNT_SNAPSHOT_JS))["account_id"] == ""


async def test_inventory_reads_account_from_adjacent_container(offline_page, tmp_path):
    from urllib.parse import urlsplit

    from douyin_wiki.adapters.favorites import DouyinFavoritesAdapter
    from douyin_wiki.config import MediaSettings
    from tests.test_favorites_browser_client import BRIDGE

    async def handler(route):
        if route.request.is_navigation_request():
            await route.fulfill(
                content_type="text/html",
                body='<meta charset="utf-8"><div><h1>测试账号</h1><span>抖音号：</span>'
                "<div><span>fixture_123</span> <span>36岁</span></div></div><script>"
                + BRIDGE
                + "</script>",
            )
        elif urlsplit(route.request.url).path == "/aweme/v1/web/collects/list/":
            await route.fulfill(
                json={
                    "status_code": 0,
                    "has_more": 0,
                    "total_number": 1,
                    "collects_list": [{"collects_id_str": "12", "collects_name": "测试夹"}],
                }
            )
        else:
            await route.abort()

    await offline_page.route("**/*", handler)
    checkpoints = []

    async def checkpoint(snapshot):
        checkpoints.append(snapshot.account_id)

    result = await DouyinFavoritesAdapter(MediaSettings(), tmp_path / "profile")._inventory_page(
        offline_page,
        cookies=[{"name": "sessionid", "domain": ".douyin.com", "expires": -1}],
        folder_ids=None,
        directory_only=True,
        expected_account_id=None,
        on_checkpoint=checkpoint,
    )
    assert result.account_id == "douyin:fixture_123"
    assert result.complete and result.folders_complete
    assert [f.id for f in result.folders] == ["12"]
    assert checkpoints == ["douyin:fixture_123"]


async def test_inventory_waits_for_profile_identity_to_render(offline_page, tmp_path, monkeypatch):
    import json

    from douyin_wiki.adapters.favorites import DouyinFavoritesAdapter
    from douyin_wiki.adapters.favorites_api import RawFavoritePage
    from douyin_wiki.config import MediaSettings

    class EmptyDirectoryClient:
        def __init__(self, page):
            self.page = page

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def read_page(self, **_):
            return RawFavoritePage(
                "directory",
                None,
                "0",
                20,
                200,
                json.dumps(
                    {
                        "status_code": 0,
                        "has_more": 0,
                        "collects_list": [],
                        "total_number": 0,
                    }
                ).encode(),
            )

    monkeypatch.setattr(
        "douyin_wiki.adapters.favorites.FavoritesBrowserClient", EmptyDirectoryClient
    )
    await offline_page.route(
        "**/*",
        lambda route: route.fulfill(
            content_type="text/html",
            body="""
        <meta charset="utf-8"><div id="profile">正在加载</div><script>setTimeout(()=>{
          document.getElementById('profile').innerHTML=
            '<h1>测试账号</h1><span>抖音号：test_123</span>';
        }, 1100);</script>
    """,
        ),
    )
    result = await DouyinFavoritesAdapter(MediaSettings(), tmp_path / "profile")._inventory_page(
        offline_page,
        cookies=[{"name": "sessionid", "domain": ".douyin.com", "expires": -1}],
        folder_ids=None,
        directory_only=True,
        expected_account_id=None,
        on_checkpoint=None,
    )
    assert result.account_id == "douyin:test_123"
    assert result.complete


async def test_ui_inventory_requires_separate_confirm_and_partial_acceptance(
    offline_page, tmp_path
):
    page = offline_page
    root = Path(__file__).parents[1] / "src/douyin_wiki/webapp"
    template = (root / "templates/app.html").read_text()
    begin = template.index('      <section id="imports-favorites-view"')
    end = template.index('<section id="jobs-view"', begin)
    html = '<meta charset="utf-8">' + template[begin:end]
    data = {
        "job_id": "fixture-parent",
        "status": "needs_selection",
        "status_label": "待选择作品",
        "nickname": "模拟账号",
        "directory_only": False,
        "confirmed": False,
        "include_images": False,
        "complete": False,
        "warnings": ["模拟分页未完成"],
        "folders": [],
        "analysis_mode": "gateway",
        "total": 1,
        "has_more": False,
        "summary": {"discovered": 1, "selected": 1, "failed": 0},
        "items": [
            {
                "work_id": "123456",
                "canonical_url": "https://www.douyin.com/video/123456",
                "title": "<img src=x onerror=alert(1)>",
                "author": "测试作者",
                "source_kind": "video",
                "selected": True,
                "disposition": "pending",
            }
        ],
    }
    calls = []

    async def route_handler(route):
        path = route.request.url.split("http://fixture.test")[1].split("?")[0]
        body = route.request.post_data_json if route.request.method == "POST" else None
        calls.append((path, body))
        if path.endswith("/confirm"):
            assert body == {"accept_partial": True}
            data.update(status="monitoring", confirmed=True)
            result = {"job_id": "fixture-parent", "status": "monitoring"}
        elif path == "/api/favorites/imports":
            result = (
                {"job_id": "fixture-parent", "status": "queued"} if body is not None else [data]
            )
        else:
            result = data
        await route.fulfill(json=result)

    await page.route("http://fixture.test/**", route_handler)
    # A synthetic origin allows relative fetch without connecting to any service.
    await page.route(
        "http://fixture.test/", lambda route: route.fulfill(body=html, content_type="text/html")
    )
    await page.goto("http://fixture.test/")
    await page.add_style_tag(content=(root / "static/app.css").read_text())
    await page.add_script_tag(content=(root / "static/shared.js").read_text())
    await page.evaluate(
        "document.getElementById('imports-favorites-view').classList.remove('hidden')"
    )
    await page.add_script_tag(content=(root / "static/imports-favorites.js").read_text())
    await page.evaluate(
        "window.dispatchEvent(new CustomEvent('douku:route', {detail:{path:'/imports/favorites'}}))"
    )
    await page.locator("#favorites-scan").click()
    await page.locator("#favorites-items a").wait_for()
    assert await page.locator("#favorites-items img").count() == 0
    assert await page.locator("#favorites-confirm").is_disabled()
    await page.screenshot(path=str(tmp_path / "favorites-preview.png"), full_page=True)
    assert not any(path.endswith("/confirm") for path, _ in calls)
    await page.locator("#favorites-partial").check()
    await page.locator("#favorites-confirm").click()
    await page.locator("#favorites-confirm-dialog[open]").wait_for()
    assert not any(path.endswith("/confirm") for path, _ in calls)
    await page.locator("#favorites-confirm-submit").click()
    await page.wait_for_timeout(500)
    assert sum(path.endswith("/confirm") for path, _ in calls) == 1
