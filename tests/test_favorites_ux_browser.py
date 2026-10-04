"""Offline browser checks for the favorites import experience."""

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.async_api import async_playwright


@pytest.fixture
async def ux_ui():
    root = Path(__file__).parents[1] / "src/douyin_wiki/webapp"
    template = (root / "templates/app.html").read_text()
    begin = template.index('      <section id="imports-favorites-view"')
    end = template.index('<section id="auth-view"', begin)
    dialog_begin = template.index('  <dialog id="destructive-dialog"')
    dialog_end = template.index("</dialog>", dialog_begin) + len("</dialog>")
    html = '<meta charset="utf-8"><style>' + (root / "static/app.css").read_text() + "</style>"
    html += template[begin:end] + template[dialog_begin:dialog_end] + '<div id="toast"></div>'
    for name in ["shared.js", "imports-favorites.js", "jobs.js"]:
        html += "<script>" + (root / "static" / name).read_text() + "</script>"
    html += (
        "<script>dispatchEvent(new CustomEvent('douku:route',"
        "{detail:{path:location.pathname}}));</script>"
    )

    def inventory(job_id, count=4, selected=1, status="needs_selection"):
        items = [
            {
                "work_id": str(10000 + i),
                "canonical_url": "https://www.douyin.com/video/" + str(10000 + i),
                "title": "目标标题" + str(i) + " 很长的正文 " + "#话题 " * 20,
                "author": "作者",
                "source_kind": "video",
                "folder_ids": ["12"],
                "disposition": "pending",
                "selected": i < selected,
                "entry_id": None,
                "is_new": True,
            }
            for i in range(count)
        ]
        return {
            "job_id": job_id,
            "status": status,
            "status_label": status,
            "nickname": "测试账号",
            "directory_only": False,
            "confirmed": status == "monitoring",
            "include_images": False,
            "complete": status != "inventorying",
            "folders_complete": True,
            "warnings": [],
            "folders": [{"id": "12", "name": "重要", "reported_count": count}],
            "analysis_mode": "gateway",
            "folder_ids": None,
            "update_mode": "incremental",
            "created_at": "2026-10-04T20:00:00+08:00",
            "updated_at": "2026-10-04T20:01:00+08:00",
            "first_read": True,
            "items": items,
            "total": count,
            "has_more": False,
            "summary": {"discovered": count, "selected": selected, "in_library": 0, "failed": 0},
            "imported_entry_ids": [],
        }

    states = {name: inventory(name) for name in ["works", "cancelled", "reading", "legacy"]}
    states["bulk"] = inventory("bulk", count=35, selected=35)
    states["many"] = inventory("many", count=125, selected=0)
    states["cancelled"]["status"] = "cancelled"
    states["reading"].update(status="inventorying", complete=False)
    states["legacy"].update(
        directory_only=True,
        status="completed_with_warnings",
        items=[],
        total=0,
        folders=[],
        warnings=["无法确认收藏夹目录分页结束"],
    )
    states["works"]["items"][-1].update(
        entry_id="dy-imported", disposition="imported", selected=False
    )
    states["works"]["summary"]["in_library"] = 1
    calls = []
    fail_selection = False

    async def handler(route):
        nonlocal fail_selection
        req = route.request
        url = urlsplit(req.url)
        query = parse_qs(url.query, keep_blank_values=True)
        body = req.post_data_json if req.method in {"POST", "DELETE"} else None
        calls.append({"method": req.method, "path": url.path, "query": query, "body": body})
        if url.path.startswith("/imports/favorites") or url.path.startswith("/jobs/"):
            await route.fulfill(body=html, content_type="text/html")
        elif url.path == "/api/system/health":
            await route.fulfill(json={"worker": {"running": True}})
        elif url.path == "/api/auth/status":
            await route.fulfill(json={"channels": {"douyin": {"user_state": "authorized"}}})
        elif url.path == "/api/favorites/imports":
            if req.method == "POST":
                await route.fulfill(json={"job_id": "works", "status": "queued"})
            else:
                await route.fulfill(json=list(states.values()))
        elif url.path.startswith("/api/favorites/imports/"):
            name = url.path.split("/")[4]
            data = states[name]
            if url.path.endswith("/confirm"):
                await route.fulfill(json={"job_id": name, "status": "dispatching"})
                return
            if url.path.endswith("/selection"):
                if fail_selection:
                    fail_selection = False
                    await route.fulfill(status=500, json={"detail": "模拟保存失败"})
                    return
                targets = body.get("work_ids")
                filters = body.get("filters") or {}
                for item in data["items"]:
                    if targets is not None and item["work_id"] not in targets:
                        continue
                    if filters.get("query") and filters["query"] not in item["title"]:
                        continue
                    if not item["entry_id"]:
                        item["selected"] = body["selected"]
                data["summary"]["selected"] = sum(
                    i["selected"] and not i["entry_id"] for i in data["items"]
                )
            result = deepcopy(data)
            result["items"] = [
                i
                for i in result["items"]
                if (not query.get("query") or query["query"][0] in i["title"])
                and (query.get("only_unimported") != ["true"] or not i["entry_id"])
            ]
            result["total"] = len(result["items"])
            limit = int(query.get("limit", ["50"])[0])
            offset = (int(query.get("page", ["1"])[0]) - 1) * limit
            result["items"] = result["items"][offset : offset + limit]
            result["has_more"] = offset + limit < result["total"]
            await route.fulfill(json=result)
        elif url.path.startswith("/api/imports/") and req.method == "DELETE":
            if body["confirmed"]:
                states.pop(url.path.rsplit("/", 1)[-1], None)
            await route.fulfill(json={"will_delete": ["此记录"], "will_keep": ["已入库资料"]})
        elif url.path.startswith("/api/jobs/"):
            name = url.path.split("/")[3]
            if req.method == "POST":
                states[name]["status"] = "cancelled"
                await route.fulfill(json={"status": "cancelled"})
            else:
                data = states[name]
                children = [
                    {
                        "id": str(i),
                        "kind": "capture",
                        "display_title": "子任务 " + str(i),
                        "status": "queued",
                        "state_label": "排队",
                        "message_for_user": "等待处理",
                    }
                    for i in range(120)
                ]
                await route.fulfill(
                    json={
                        "id": name,
                        "kind": "favorites_import",
                        "display_title": "收藏批次",
                        "status": data["status"],
                        "state_label": data["status"],
                        "stage_label": "完成",
                        "progress": 0,
                        "message_for_user": "测试状态",
                        "analysis_mode": "gateway",
                        "analysis_mode_label": "等待外部 Agent 接续",
                        "batch": {"summary": data["summary"], "confirmed": data["confirmed"]},
                        "children": children,
                        "child_stats": {"total": 120},
                        "timeline": [],
                        "result": {},
                        "imported_entry_ids": ["dy-one"],
                    }
                )
        else:
            await route.abort()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        await context.route("**/*", lambda route: route.abort())
        await context.route("http://fixture.test/**", handler)
        page = await context.new_page()
        page.set_default_timeout(3000)

        async def open_page(job_id="works", detail=False):
            await page.goto(
                "http://fixture.test/jobs/" + job_id
                if detail
                else "http://fixture.test/imports/favorites?job=" + job_id
            )
            if detail:
                await page.locator(".job-detail-summary").wait_for()
            else:
                await page.wait_for_function("!document.getElementById('favorites-scope').disabled")

        def fail_next():
            nonlocal fail_selection
            fail_selection = True

        yield SimpleNamespace(
            page=page, open=open_page, calls=calls, states=states, fail_next=fail_next
        )
        await browser.close()


async def test_directory_entry_is_visible_beside_folder_choices(ux_ui):
    await ux_ui.open()
    await ux_ui.page.locator("#favorites-scope").select_option("folders")
    assert await ux_ui.page.locator("#favorites-directory").is_visible()


async def test_confirmation_can_cancel_without_dispatching(ux_ui):
    await ux_ui.open()
    await ux_ui.page.locator("#favorites-confirm").click()
    assert await ux_ui.page.locator("#favorites-confirm-dialog").count() == 1
    await ux_ui.page.locator("#favorites-confirm-dialog[open]").wait_for()
    assert not any(c["path"].endswith("/confirm") for c in ux_ui.calls)
    await (
        ux_ui.page.locator("#favorites-confirm-dialog")
        .get_by_role("button", name="取消", exact=True)
        .click()
    )
    assert not any(c["path"].endswith("/confirm") for c in ux_ui.calls)


async def test_large_import_requires_exact_count_before_dispatch(ux_ui):
    await ux_ui.open("bulk")
    await ux_ui.page.locator("#favorites-confirm").click()
    assert await ux_ui.page.locator("#favorites-confirm-dialog").count() == 1
    await ux_ui.page.locator("#favorites-confirm-dialog[open]").wait_for()
    confirm = ux_ui.page.locator("#favorites-confirm-submit")
    assert await confirm.is_disabled()
    await ux_ui.page.locator("#favorites-confirm-count").fill("34")
    assert await confirm.is_disabled()
    await ux_ui.page.locator("#favorites-confirm-count").fill("35")
    await confirm.click()
    await ux_ui.page.wait_for_timeout(100)
    assert len([c for c in ux_ui.calls if c["path"].endswith("/confirm")]) == 1


async def test_fast_choices_are_batched_without_locking_the_page(ux_ui):
    await ux_ui.open()
    checks = ux_ui.page.locator("#favorites-items input[data-work]")
    await checks.nth(1).check()
    assert await ux_ui.page.locator("#favorites-query").is_enabled()
    await checks.nth(2).check()
    await ux_ui.page.wait_for_timeout(700)
    saves = [c for c in ux_ui.calls if c["path"].endswith("/selection")]
    assert len(saves) == 1
    assert saves[0]["body"]["work_ids"] == ["10001", "10002"]


async def test_inventory_hides_partial_confirmation_and_can_stop(ux_ui):
    await ux_ui.open("reading")
    assert await ux_ui.page.locator("#favorites-partial-row").is_hidden()
    assert await ux_ui.page.locator("#favorites-stop").is_visible()


async def test_delete_resets_the_list_summary_and_confirmation(ux_ui):
    await ux_ui.open()
    await ux_ui.page.locator(".fav-advanced > summary").click()
    await ux_ui.page.locator("#favorites-delete-history").click()
    await ux_ui.page.locator("#destructive-dialog[open]").wait_for()
    assert await ux_ui.page.locator("#destructive-title").inner_text() == "删除这条导入记录"
    await ux_ui.page.get_by_role("button", name="确认删除", exact=True).click()
    await ux_ui.page.wait_for_timeout(100)
    assert await ux_ui.page.locator("#favorites-items input").count() == 0
    assert "0" in await ux_ui.page.locator("#favorites-selected-count").inner_text()
    assert await ux_ui.page.locator("#favorites-confirm").is_disabled()


async def test_search_is_automatic_and_imported_rows_are_hidden_initially(ux_ui):
    await ux_ui.open()
    assert await ux_ui.page.locator("#favorites-only-unimported").count() == 1
    assert await ux_ui.page.locator("#favorites-items input[data-work]").count() == 3
    await ux_ui.page.locator("#favorites-query").fill("目标标题2")
    await ux_ui.page.wait_for_timeout(500)
    assert await ux_ui.page.locator("#favorites-items input[data-work]").count() == 1


async def test_cancelled_and_old_records_explain_how_to_continue(ux_ui):
    await ux_ui.open("cancelled")
    assert await ux_ui.page.locator("#favorites-cancelled").is_visible()
    assert await ux_ui.page.locator("#favorites-restart").is_visible()
    await ux_ui.open("legacy")
    assert any(
        "旧版本" in text
        for text in await ux_ui.page.locator("#favorites-history option").all_text_contents()
    )


async def test_job_detail_shows_batch_links_without_waiting_message_or_zero_progress(ux_ui):
    await ux_ui.open("cancelled", detail=True)
    root = ux_ui.page.locator("#job-detail-content")
    assert await root.get_by_role("link", name="查看收藏清单", exact=True).count() == 1
    assert await root.get_by_role("link", name="在资料库查看本批", exact=True).count() == 1
    assert "等待外部 Agent 接续" not in await root.inner_text()
    assert "进度 0%" not in await root.inner_text()
    assert await root.locator(".job-child").count() <= 50


async def test_selection_failure_blocks_confirm_until_retry_succeeds(ux_ui):
    await ux_ui.open()
    ux_ui.fail_next()
    await ux_ui.page.locator("#favorites-items input[data-work]").nth(1).check()
    await ux_ui.page.get_by_text("选择尚未保存，请重试后再导入。", exact=True).wait_for()
    assert await ux_ui.page.locator("#favorites-confirm").is_disabled()
    await ux_ui.page.locator("#favorites-save-retry").click()
    await ux_ui.page.wait_for_function("!document.getElementById('favorites-confirm').disabled")
    assert ux_ui.states["works"]["summary"]["selected"] == 2


async def test_switching_history_flushes_the_previous_jobs_pending_choices(ux_ui):
    await ux_ui.open()
    await ux_ui.page.locator("#favorites-items input[data-work]").nth(1).check()
    await ux_ui.page.locator(".fav-advanced > summary").click()
    await ux_ui.page.locator("#favorites-history").select_option("bulk")
    await ux_ui.page.wait_for_function("location.search.includes('bulk')")
    saves = [c for c in ux_ui.calls if c["path"].endswith("/selection")]
    assert saves[0]["path"] == "/api/favorites/imports/works/selection"
    assert ux_ui.states["works"]["summary"]["selected"] == 2
    assert ux_ui.states["bulk"]["summary"]["selected"] == 35


async def test_shift_selection_saves_one_range(ux_ui):
    await ux_ui.open()
    checks = ux_ui.page.locator("#favorites-items input[data-work]")
    await checks.nth(0).click()
    await checks.nth(2).click(modifiers=["Shift"])
    await ux_ui.page.wait_for_timeout(700)
    assert ux_ui.states["works"]["summary"]["selected"] == 3


async def test_select_page_and_filtered_results_have_different_scopes(ux_ui):
    await ux_ui.open("many")
    await ux_ui.page.locator("#favorites-select-page").click()
    await ux_ui.page.wait_for_timeout(700)
    assert ux_ui.states["many"]["summary"]["selected"] == 50
    await ux_ui.page.locator("#favorites-select-filtered").click()
    await ux_ui.page.wait_for_function(
        "document.getElementById('favorites-selected-count').textContent.includes('125')"
    )
    assert ux_ui.states["many"]["summary"]["selected"] == 125


async def test_stop_requires_confirmation_and_preserves_the_inventory(ux_ui):
    await ux_ui.open("reading")
    await ux_ui.page.locator("#favorites-stop").click()
    await ux_ui.page.locator("#destructive-dialog[open]").wait_for()
    assert not any(c["path"].endswith("/cancel") for c in ux_ui.calls)
    await ux_ui.page.get_by_role("button", name="确认停止", exact=True).click()
    await ux_ui.page.locator("#favorites-cancelled").wait_for(state="visible")
    assert len(ux_ui.states["reading"]["items"]) == 4


async def test_child_tasks_pagination_reaches_the_final_page(ux_ui):
    await ux_ui.open("works", detail=True)
    root = ux_ui.page.locator("#job-children")
    assert await root.locator(".job-child").count() == 50
    await root.get_by_role("button", name="下一页子任务").click()
    assert await root.locator(".job-child").count() == 50
    await root.get_by_role("button", name="下一页子任务").click()
    assert await root.locator(".job-child").count() == 20
