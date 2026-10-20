"""Exercise operation pages against a real API in an isolated Vault, without external calls."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from douyin_wiki.models import CaptureRequest, JobStatus  # noqa: E402
from douyin_wiki.webapp.app import WEB_VERSION, create_app  # noqa: E402
from scripts.verify_readwise_library import populate  # noqa: E402
from tests.support.web import _web_fixture, local_client  # noqa: E402
from tests.test_creator import FakeCreatorAdapter, inventory_result  # noqa: E402
from tests.test_favorites import scan, snapshot  # noqa: E402
from tests.test_web_operation import FakeAuthDownloader, FakeDouyinAuth  # noqa: E402


async def verify(*, output_dir: Path | None = None):
    out = (
        output_dir
        or Path(
            os.environ.get(
                "DOUKU_VERIFY_OUTPUT_DIR", str(ROOT / "docs/screenshots/mcp-gateway-simplification")
            )
        )
        / "operations"
    )
    out.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="douku-operations-check-") as tmp:
        config, service = _web_fixture(Path(tmp))
        populate(config, service)
        work_ids = [str(7000000000000000101 + index) for index in range(5)]
        inventory = inventory_result(*work_ids, complete=False)
        service.creator_adapter = FakeCreatorAdapter(inventory)
        creator = service.capture_douyin_creator(inventory.profile.canonical_url)
        creator = await service.process_claimed_job(creator)
        favorites = await scan(
            service,
            snapshot(
                complete=False,
                kinds=[
                    (work_ids[0], "video"),
                    (work_ids[1], "video"),
                    (work_ids[2], "article"),
                    (work_ids[3], "video"),
                    (work_ids[4], "image_note"),
                ],
            ),
        )
        service.favorites.select(favorites.id, selected=False)
        imported_favorites = await scan(
            service, snapshot(kinds=[("123", "video")]), update_mode="full", select=False
        )
        service.favorites.select(imported_favorites.id, selected=True)
        service.favorites.confirm(imported_favorites.id)
        service.downloader = FakeAuthDownloader("ready", ok=True, verified=True)
        service.image_note_downloader = FakeDouyinAuth(ok=True)
        running = service.database.create_job(
            CaptureRequest(share_text="https://www.douyin.com/video/7000000000000000200"),
            status=JobStatus.ANALYZING,
            progress=0.62,
        )
        failed = service.database.create_job(
            CaptureRequest(share_text="https://www.douyin.com/video/7000000000000000201"),
            status=JobStatus.FAILED,
        )
        service.database.update_job(failed.id, error_message="测试失败，可重试")
        child = service.database.create_job(
            CaptureRequest(share_text="https://www.douyin.com/video/7000000000000000202"),
            status=JobStatus.COMPLETED_WITH_WARNINGS,
            progress=1,
        )
        service.database.update_job(child.id, result={"warnings": ["字幕有缺口"]})
        parent = service.database.create_job(
            CaptureRequest(share_text=inventory.profile.canonical_url),
            kind="creator_import",
            status=JobStatus.COMPLETED_WITH_WARNINGS,
            progress=1,
        )
        service.database.update_job(
            parent.id,
            result={"warnings": [], "child_job_ids": [child.id], "creator_name": "已完成批次"},
        )
        topic = service.create_topic(
            "让资料进入下一次思考",
            ["dy-201", "dy-202"],
            goal="串联阅读与复盘",
            instructions="优先保留原文证据。",
        )
        service.save_topic_note(
            topic["topic"]["id"],
            "\n\n".join(
                f"第 {index} 段：记录只是开始，把一条线索放回上下文，才有机会形成自己的理解。"
                for index in range(1, 25)
            ),
            title="从阅读到使用",
            confirmed=True,
        )
        for entry in ["dy-206", "dy-207", "dy-208"]:
            service.trash_entry(entry, confirmed=True)
        app = create_app(
            config,
            config_path=Path(tmp) / "config.toml",
            service=service,
            start_watcher=False,
        )
        with local_client(app) as client:
            async with async_playwright() as p:
                browser = await p.chromium.launch()
                page = await browser.new_page(
                    viewport={"width": 1440, "height": 900}, device_scale_factor=2
                )
                await page.add_init_script("""window.fixtureEventSources=[];
                    window.EventSource=class extends EventTarget {
                      constructor(url) {
                        super(); this.url=url; window.fixtureEventSources.push(this);
                      }
                      close() { this.closed=true; }
                    };""")
                page.set_default_timeout(8000)
                errors, external, mutations, server_errors = [], [], [], []
                page.on("pageerror", lambda error: errors.append(str(error)))

                async def route(r):
                    request = r.request
                    url = urlsplit(request.url)
                    if url.hostname != "testserver":
                        external.append(request.url)
                        await r.abort()
                        return
                    if url.path.endswith("/events"):
                        await r.fulfill(content_type="text/event-stream", body=": fixture\n\n")
                        return
                    if url.path == "/api/imports/classify":
                        assert request.method == "POST"
                        assert json.loads(request.post_data)["text"] == (
                            "看看这个视频 https://v.douyin.com/test/"
                        )
                        await r.fulfill(content_type="application/json", body='{"kind":"work"}')
                        return
                    response = client.request(
                        request.method,
                        url.path + ("?" + url.query if url.query else ""),
                        content=request.post_data,
                        headers={
                            "content-type": request.headers.get("content-type", "application/json")
                        },
                    )
                    if request.method in {"POST", "PUT", "DELETE"}:
                        mutations.append(
                            {
                                "path": url.path,
                                "status": response.status_code,
                                "body": request.post_data,
                            }
                        )
                    if response.status_code >= 500:
                        server_errors.append((url.path, response.status_code, response.text))
                    await r.fulfill(
                        status=response.status_code,
                        body=response.content,
                        headers={"content-type": response.headers.get("content-type", "")},
                    )

                await page.route("**/*", route)

                async def fresh(path, ready):
                    await page.goto("http://testserver" + path)
                    try:
                        await page.wait_for_selector(ready)
                    except Exception:
                        (out / "failure.json").write_text(
                            json.dumps(
                                {
                                    "path": path,
                                    "ready": ready,
                                    "errors": errors,
                                    "server_errors": server_errors,
                                    "view": await page.evaluate("""() => ({
                                      panel: state.panelPage, items: state.items.map(i=>i.entry_id),
                                      batch: state.favoritesJob, section: state.section,
                                      error: document.querySelector('#library-error').textContent,
                                      body: document.body.innerText.slice(0, 5000)
                                    })"""),
                                },
                                ensure_ascii=False,
                                indent=2,
                            )
                            + "\n"
                        )
                        await page.screenshot(path=out / "failure.png")
                        raise
                    await page.evaluate("document.fonts.ready")
                    await page.wait_for_timeout(100)

                async def shot(name):
                    await page.evaluate("document.activeElement?.blur()")
                    await page.mouse.move(0, 0)
                    await page.screenshot(path=out / f"{name}.png", animations="disabled")

                async def wait_count(selector, text):
                    await page.wait_for_function(
                        "([s,t]) => document.querySelector(s).textContent.includes(t)",
                        arg=[selector, text],
                    )

                async def no_overflow(label):
                    bad = await page.evaluate("""() =>
                      [...document.querySelectorAll('#main-content *')]
                      .filter(e => e.getClientRects().length
                        && getComputedStyle(e).position !== 'absolute')
                      .filter(e => { const r=e.getBoundingClientRect();
                        return r.left < -1 || r.right > innerWidth+1; })
                      .map(e => [e.tagName,e.id,e.className])""")
                    assert await page.evaluate(
                        "document.documentElement.scrollWidth <= innerWidth"
                    ), (label, bad)
                    assert not bad, (label, bad)

                await fresh("/imports", "#imports-view:not(.hidden)")
                await page.wait_for_selector(
                    "#imports-view .destination-row[data-import-kind='creators']:not(.hidden)"
                )
                await page.wait_for_selector(
                    "#imports-view .destination-row[data-import-kind='favorites']:not(.hidden)"
                )
                visible_rows = page.locator("#imports-view .destination-row:not(.hidden)")
                assert await visible_rows.count() == 2
                assert (
                    await page.locator(
                        "#imports-view .destination-row[data-import-kind='collections'].hidden"
                    ).count()
                    == 1
                )
                assert await page.locator(".recent-import-row").count() <= 5
                assert (
                    await visible_rows.first.evaluate("e=>e.getBoundingClientRect().height") == 56
                )
                before = len(mutations)
                assert await page.locator("#import-paste-form").count() == 0
                await page.locator(".destination-row[href='/imports/creators']").click()
                await page.wait_for_selector("#imports-creators-view:not(.hidden)")
                assert await page.locator("#creator-form").count() == 0
                assert len(mutations) == before
                await fresh("/imports/single", "#imports-single-view:not(.hidden)")
                assert "去 Agent 里导入" in await page.locator("#imports-single-view").inner_text()
                assert await page.locator("#single-form").count() == 0
                assert len(mutations) == before
                await shot("single-light")

                await fresh(f"/imports/creators?job={creator.id}", "#creator-items input")
                assert await page.locator("#creator-confirm").is_disabled()
                checks = page.locator("#creator-items input")
                await checks.nth(0).click()
                await wait_count("#creator-selected-count", "已选 1")
                await checks.nth(2).click(modifiers=["Shift"])
                await wait_count("#creator-selected-count", "已选 3")
                last = next(m for m in reversed(mutations) if m["path"].endswith("/selection"))
                assert json.loads(last["body"])["work_ids"] == work_ids[:3], last
                assert await page.locator("#creator-confirm").is_disabled()
                await page.check("#creator-partial")
                assert await page.locator("#creator-confirm").is_enabled()
                await checks.nth(2).focus()
                await page.keyboard.press("j")
                assert await page.evaluate("document.activeElement.dataset.work") == work_ids[3]
                await page.keyboard.press("x")
                await wait_count("#creator-selected-count", "已选 4")
                assert await page.evaluate("document.activeElement.dataset.work") == work_ids[3]
                await shot("creator-selection-light")

                await fresh(f"/imports/favorites?job={favorites.id}", "#favorites-items input")
                assert await page.locator("#favorites-confirm").is_disabled()
                checks = page.locator("#favorites-items input")
                assert await checks.nth(2).is_disabled()
                assert await checks.nth(4).is_disabled()
                await checks.nth(0).click()
                await wait_count("#favorites-selected-count", "将导入 1 件")
                await checks.nth(3).click(modifiers=["Shift"])
                await wait_count("#favorites-selected-count", "将导入 3 件")
                await wait_count("#favorites-save-hint", "选择已保存")
                last = next(m for m in reversed(mutations) if m["path"].endswith("/selection"))
                assert json.loads(last["body"])["work_ids"] == [
                    work_ids[0],
                    work_ids[1],
                    work_ids[3],
                ], last
                assert await page.locator("#favorites-confirm").is_disabled()
                await page.check("#favorites-partial")
                await page.wait_for_function(
                    "() => !document.querySelector('#favorites-confirm').disabled"
                )
                assert await page.locator("#favorites-confirm").is_enabled()
                await shot("favorites-selection-light")
                assert (
                    await page.locator(".favorites-item-content").first.evaluate(
                        "e => getComputedStyle(e).display"
                    )
                    == "grid"
                )
                before = len(mutations)
                await page.click("#favorites-confirm")
                await page.wait_for_function(
                    "() => document.querySelector('#favorites-confirm-dialog').open"
                )
                assert (
                    "将处理 3 件"
                    in await page.locator("#favorites-confirm-description").inner_text()
                )
                await shot("favorites-confirm-light")
                await page.click("#favorites-confirm-form button[value='cancel']")
                assert len(mutations) == before
                assert service.favorites.get(favorites.id)["confirmed"] is False

                batch_url = f"/?favorites_job={imported_favorites.id}"
                await fresh(batch_url, "#library-results .library-item")
                assert await page.locator("#library-results .library-item").evaluate_all(
                    "nodes => nodes.map(node => node.dataset.entryId)"
                ) == ["dy-123"]
                assert await page.get_by_role("button", name="移除筛选：本批收藏").is_visible()
                await page.reload()
                await page.wait_for_selector("#library-results .library-item")
                assert await page.locator("#library-results .library-item").count() == 1
                assert "favorites_job=" in page.url
                await shot("favorites-batch-library-light")
                await page.get_by_role("button", name="移除筛选：本批收藏").click()
                await page.wait_for_function(
                    "() => document.querySelectorAll('#library-results .library-item').length > 1"
                )
                assert "favorites_job=" not in page.url

                await fresh("/settings/auth?channel=douyin", "#auth-douyin-card .status-pill")
                assert await page.locator("#auth-douyin-card").evaluate(
                    "e => e.classList.contains('auth-channel-focus')"
                )
                await shot("favorites-douyin-auth-light")

                await fresh("/jobs", "#jobs-list .job-row")
                track = page.locator(f".job-row[data-job-id='{running.id}'] .job-row-progress")
                assert await track.evaluate("e=>e.getBoundingClientRect().height") == 2
                await page.locator(f".job-row[data-job-id='{running.id}']").focus()
                await page.keyboard.press("k")
                assert await page.evaluate("document.activeElement.dataset.jobId") != running.id
                await page.keyboard.press("j")
                assert await page.evaluate("document.activeElement.dataset.jobId") == running.id
                await page.evaluate(
                    "window.fixtureEventSources.filter(e=>!e.closed).forEach(e=>"
                    "e.dispatchEvent(new MessageEvent('jobs',{data:'{}'})))"
                )
                await page.wait_for_timeout(150)
                assert await page.evaluate("document.activeElement.dataset.jobId") == running.id
                await shot("jobs-light")
                await page.locator(f".job-row[data-job-id='{running.id}']").focus()
                await page.keyboard.press("Enter")
                await page.wait_for_selector("#job-detail-view:not(.hidden) .progress-track")
                assert (
                    await page.locator("#job-detail-content .progress-track").evaluate(
                        "e=>e.getBoundingClientRect().height"
                    )
                    == 4
                )
                await shot("job-detail-light")
                await fresh("/jobs", "#jobs-list .job-row")
                await page.locator(f".job-row[data-job-id='{failed.id}']").focus()
                await page.keyboard.press("r")
                await page.wait_for_function(
                    "id => document.querySelector(`[data-job-id='${id}'] .status-pill`)"
                    ".dataset.state === 'queued'",
                    arg=failed.id,
                )
                assert any(m["path"] == f"/api/jobs/{failed.id}/retry" for m in mutations)

                await fresh(f"/jobs/{parent.id}", ".job-child")
                assert await page.locator(".job-child").first.evaluate("e=>e.children.length") == 3
                assert "字幕有缺口" in await page.locator(".job-warnings").inner_text()
                await shot("job-children-light")
                await fresh("/topics", ".topic-list-card")
                await shot("topics-light")
                await fresh(f"/topics/{topic['topic']['id']}", ".topic-artifact-card")
                assert await page.locator("#topic-content .topic-source-row").count() == 0
                await page.click("#panel-tab-sources")
                assert await page.locator("#topic-sources-pane .topic-source-row").count() == 2
                assert await page.locator(".artifact-full").is_visible()
                await page.locator(".artifact-full summary").click()
                await page.wait_for_selector(".topic-artifact-card.is-expanded")
                await page.locator(".artifact-full summary").click()
                await page.locator("#topic-sources-pane input").first.uncheck()
                await wait_count("#topic-sources-pane h2", "1 / 2")
                assert not client.get(f"/api/topics/{topic['topic']['id']}").json()["topic"][
                    "sources"
                ][0]["enabled"]
                await page.locator("#main-content").evaluate("e=>e.scrollTop=0")
                await shot("topic-light")

                await fresh("/settings", "#settings-view:not(.hidden)")
                await page.wait_for_function(
                    "()=>document.querySelector('#settings-value-analysis').textContent !== '—'"
                )
                assert await page.locator("#settings-view .destination-row").count() == 3
                await shot("settings-light")
                for path, ready, name in [
                    ("/settings/auth", "#auth-video-card .status-pill", "auth"),
                    ("/settings/analysis", "#analysis-status-title", "analysis"),
                    ("/settings/model", "#not-found-view:not(.hidden)", "model"),
                    ("/settings/system", ".system-metric", "system"),
                ]:
                    await fresh(path, ready)
                    assert (
                        await page.locator(".operation-section").first.evaluate(
                            "e=>getComputedStyle(e).borderRadius"
                        )
                        == "0px"
                    )
                    badges = await page.locator(
                        ".content-view:not(.hidden) .status-pill"
                    ).evaluate_all("nodes => nodes.map(e => getComputedStyle(e).backgroundColor)")
                    assert all(color == "rgba(0, 0, 0, 0)" for color in badges), badges
                    await shot(name + "-light")
                assert (
                    await page.locator("#system-overview").evaluate(
                        "e=>getComputedStyle(e).gridTemplateColumns.split(' ').length"
                    )
                    == 4
                )
                assert await page.locator("#system-maintenance-preview").count() == 1

                await fresh("/trash", ".trash-item")
                row = page.locator(".trash-item").first
                await row.focus()
                await page.keyboard.press("Shift+J")
                await page.keyboard.press("Shift+J")
                assert await page.locator("#trash-list input:checked").count() == 3
                await shot("trash-light")
                await page.click("#trash-clear-selection")
                await row.focus()
                await page.keyboard.press("x")
                assert await page.locator("#trash-list input:checked").count() == 1
                await page.click("#trash-restore-selected")
                await page.click("#confirm-destructive")
                await page.wait_for_function(
                    "()=>document.querySelectorAll('.trash-item').length===2"
                )

                await fresh("/", "#library-results .library-item")
                for theme in ["light", "dark"]:
                    await page.evaluate("theme=>window.DoukuTheme.set(theme)", theme)
                    await shot("library-" + theme)
                await page.evaluate("window.Douku.navigate('/articles/dy-123')")
                await page.wait_for_selector("#article-view:not(.hidden)")
                for theme in ["light", "dark"]:
                    await page.evaluate("theme=>window.DoukuTheme.set(theme)", theme)
                    await shot("article-" + theme)
                await page.keyboard.press("Meta+k")
                await page.fill("#command-input", ">导入")
                assert await page.locator(".command-result").count() == 1
                assert (
                    await page.locator("#command-dialog").evaluate(
                        "e=>e.getBoundingClientRect().width"
                    )
                    == 560
                )
                await shot("commands-dark")
                await page.locator("#command-input").focus()
                await page.keyboard.press("Enter")
                await page.wait_for_selector("#imports-view:not(.hidden)")
                await page.keyboard.press("Meta+k")
                await page.fill("#command-input", "没有匹配文章")
                await page.wait_for_selector("#command-results .command-help")
                assert await page.locator(".command-result").count() == 0
                await page.fill("#command-input", "")
                assert set(
                    await page.locator(".command-result").evaluate_all(
                        "nodes => nodes.map(node => node.dataset.commandId)"
                    )
                ) == {
                    "imports",
                    "jobs",
                    "tags",
                    "topics",
                    "trash",
                    "settings",
                    "theme",
                    "shortcuts",
                }
                await page.locator("[data-command-id='theme']").click()
                assert await page.evaluate("document.documentElement.dataset.theme") == "light"
                await page.keyboard.press("Meta+k")
                await page.fill("#command-input", ">快捷键")
                await page.locator("[data-command-id='shortcuts']").click()
                assert await page.locator("#shortcuts-dialog").evaluate("e=>e.open")
                await page.keyboard.press("Escape")
                await page.keyboard.press("Meta+k")
                await page.fill("#command-input", ">对话")
                await page.wait_for_selector("#command-results .command-help")
                assert await page.locator(".command-result").count() == 0
                assert await page.locator("#chat-form").count() == 0
                assert not any(m["path"] == "/api/chat/sessions" for m in mutations)
                await page.keyboard.press("Escape")

                for path, ready, name in [
                    ("/imports", "#imports-view:not(.hidden)", "imports"),
                    ("/jobs", ".job-row", "jobs"),
                    (f"/topics/{topic['topic']['id']}", ".topic-artifact-card", "topic"),
                    ("/settings", "#settings-view:not(.hidden)", "settings"),
                ]:
                    await fresh(path, ready)
                    for theme in ["light", "dark"]:
                        await page.evaluate("theme=>window.DoukuTheme.set(theme)", theme)
                        await shot(name + "-" + theme)
                await page.evaluate("window.DoukuTheme.set('light')")
                widths = [1440, 1280, 1100, 960]
                pages = [
                    ("/imports", "#imports-view:not(.hidden)"),
                    ("/imports/single", "#imports-single-view:not(.hidden)"),
                    (f"/imports/creators?job={creator.id}", "#creator-items input"),
                    (f"/imports/favorites?job={favorites.id}", "#favorites-items input"),
                    ("/jobs", ".job-row"),
                    (f"/jobs/{running.id}", "#job-detail-content .progress-track"),
                    ("/topics", ".topic-list-card"),
                    (f"/topics/{topic['topic']['id']}", ".topic-artifact-card"),
                    ("/settings", "#settings-view:not(.hidden)"),
                    ("/settings/auth", "#auth-video-card .status-pill"),
                    ("/settings/analysis", "#analysis-status-title"),
                    ("/settings/model", "#not-found-view:not(.hidden)"),
                    ("/settings/system", ".system-metric"),
                    ("/trash", ".trash-item"),
                ]
                for width in widths:
                    await page.set_viewport_size({"width": width, "height": 900})
                    for path, ready in pages:
                        await fresh(path, ready)
                        await no_overflow((width, path))
                assert not errors, errors
                assert not external, external
                assert not server_errors, server_errors
                assert all(m["status"] < 300 for m in mutations), mutations
                (out / "verification.json").write_text(
                    json.dumps(
                        {
                            "web_version": WEB_VERSION,
                            "widths": widths,
                            "operation_routes": len(pages),
                            "checks": [
                                "paste classification and prefill only",
                                "creator shift range and keyboard focus",
                                "favorites batched eligible range and partial guard",
                                "favorites confirmation cancellation creates no tasks",
                                "favorites batch library, reload and clear filter",
                                "favorites authorization targets the Douyin account",
                                "favorites title layout and confirmation",
                                "jobs refresh focus, progress, retry and Enter",
                                "topic source relocation, persistence and artifact expansion",
                                "live settings summary, flat sections and metric columns",
                                "trash continuous shift range and real restore",
                                "article and command search, commands and theme",
                                "all operation routes responsive without overflow",
                            ],
                            "mutations": mutations,
                            "errors": errors,
                            "external_requests": external,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n"
                )
                await browser.close()
    print(f"Operations verification passed: {out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    asyncio.run(verify(output_dir=parser.parse_args().output_dir))
