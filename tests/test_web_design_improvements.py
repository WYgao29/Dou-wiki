"""Search explanations, recovery, evidence navigation and batch quality warnings."""

import pytest
from playwright.async_api import expect

from douyin_wiki.models import CaptureRequest, JobStatus
from douyin_wiki.webapp.app import create_app
from scripts.verify_readwise_reader import png
from scripts.verify_review_web_races import block, browser_fixture, entered
from tests.support.web import _web_fixture, local_client


@pytest.mark.parametrize(
    ("query", "match", "field", "approximate"),
    [
        ("缓存", "缓存", "body", False),
        ("STRASSE", "Straße", "body", False),
        ("测文", "", "title", True),
    ],
)
def test_keyword_results_explain_literal_and_fuzzy_matches(
    tmp_path, query, match, field, approximate
):
    config, service = _web_fixture(tmp_path)
    source = config.vault_path / "wiki/sources/测试文章_123.md"
    original = source.read_text() + "\n## 方法\n\n😀 Straße 的离线缓存可以重复使用。\n"
    source.write_text(original)
    app = create_app(config, service=service, start_watcher=False)
    with local_client(app) as client:
        item = client.get("/api/library", params={"q": query, "fuzzy": True}).json()["items"][0]
        explanation = item["search_match"]
        assert explanation["field"] == field
        assert explanation["match"] == match
        assert explanation["approximate"] is approximate
        snippet = explanation["before"] + explanation["match"] + explanation["after"]
        assert len(snippet) <= 162
        if field == "body":
            assert "## 方法" not in snippet
        assert "body_markdown" not in item
        assert "search_match" not in client.get("/api/library").json()["items"][0]
    assert source.read_text() == original


async def test_command_search_distinguishes_wait_error_retry_and_empty():
    async with browser_fixture() as (page, _topics, gates):
        gate = block(gates, "/api/library?q=%E6%B5%8B%E8%AF%95&limit=8&fuzzy=true", status=500)
        await page.get_by_role("button", name="搜索 K", exact=True).click()
        query = page.get_by_role("combobox", name="搜索文章、博主、标签或灵感", exact=True)
        await query.fill("测试")
        await entered(gate)
        status = page.locator("#command-status")
        await expect(status).to_have_text("正在搜索文章…")
        await expect(page.locator("#command-results")).to_have_attribute("aria-busy", "true")
        gate["release"].set()
        await expect(status).to_contain_text("暂时无法搜索文章，请重试。")
        await expect(query).to_have_value("测试")
        gate["status"] = None
        await page.get_by_role("button", name="重试搜索", exact=True).click()
        await expect(page.locator("#command-results .command-result")).to_have_count(1)
        await expect(status).to_be_hidden()
        await expect(page.locator("#command-results .search-match mark")).to_have_text("测试")
        await query.fill("绝不存在的资料")
        await expect(status).to_have_text("没有找到匹配的文章或命令。")
        await query.press("Escape")
        await expect(page.locator("#sidebar-search")).to_be_focused()


async def test_command_search_ignores_a_failed_older_query():
    async with browser_fixture() as (page, _topics, gates):
        old = block(gates, "/api/library?q=%E6%B5%8B%E8%AF%95&limit=8&fuzzy=true", status=500)
        await page.locator("#sidebar-search").click()
        query = page.locator("#command-input")
        await query.fill("测试")
        await entered(old)
        await query.fill("另一篇")
        await expect(page.locator("#command-results .command-result strong")).to_have_text(
            "另一篇文章"
        )
        old["release"].set()
        await page.wait_for_load_state("networkidle")
        await expect(page.locator("#command-status")).to_be_hidden()
        await expect(page.locator("#command-results .command-result strong")).to_have_text(
            "另一篇文章"
        )


async def test_keyword_match_is_visible_in_gallery_and_command_without_html_execution():
    def prepare(config, _service):
        source = config.vault_path / "wiki/sources/测试文章_123.md"
        source.write_text(
            source.read_text() + "\n正文含有 <script>缓存</script>，仅作为文字展示。\n"
        )

    async with browser_fixture(prepare=prepare) as (page, _topics, _gates):
        await page.get_by_role("searchbox", name="搜索知识库", exact=True).fill("缓存")
        await expect(page.locator("#library-results .search-match mark")).to_have_text("缓存")
        await page.locator("#view-gallery").click()
        await expect(page.locator(".gallery-copy .search-match")).to_contain_text("<script>")
        assert await page.locator(".search-match script").count() == 0
        await page.locator("#sidebar-search").click()
        await expect(page.locator("#command-results .search-match mark")).to_have_text("缓存")
        assert await page.locator("#command-results script").count() == 0


async def test_evidence_directory_opens_and_locates_collapsed_materials():
    def prepare(config, service):
        entry = service.database.get_entry("dy-123")
        data = service.database.get_entry_data("dy-123")
        data["transcript_corrected"] = [
            {"id": index, "start_ms": index * 1000, "end_ms": (index + 1) * 1000, "text": text}
            for index, text in enumerate(["第一段原文", "第二段原文"])
        ]
        frames = config.vault_path / "raw/assets/123/frames"
        frames.mkdir()
        frame = frames / "frame-0000000000.png"
        frame.write_bytes(png(80, 60))
        data["frames"] = [{"path": "raw/assets/123/frames/" + frame.name, "timestamp_ms": 0}]
        data["frame_manifest_version"] = 1
        service.database.upsert_entry(entry, data)

    async with browser_fixture(prepare=prepare) as (page, _topics, _gates):
        await page.locator('#library-results a[href="/articles/dy-123"]').click()
        transcript = page.locator(".article-transcript")
        frames = page.locator(".article-keyframes")
        await expect(transcript).not_to_have_attribute("open", "")
        await page.get_by_role("link", name="逐字稿 · 2段", exact=True).click()
        await expect(transcript).to_have_attribute("open", "")
        await expect(page.get_by_role("link", name="逐字稿 · 2段", exact=True)).to_have_attribute(
            "aria-current", "location"
        )
        await expect(transcript.get_by_text("第一段原文", exact=True)).to_be_in_viewport()
        await expect(page.locator("#main-content")).to_be_focused()
        await page.get_by_role("link", name="关键帧 · 1张", exact=True).click()
        await expect(frames).to_have_attribute("open", "")
        await expect(page.get_by_role("link", name="关键帧 · 1张", exact=True)).to_have_attribute(
            "aria-current", "location"
        )
        await expect(frames.locator("img")).to_be_in_viewport()
        await transcript.locator("summary").click()
        await page.get_by_role("link", name="逐字稿 · 2段", exact=True).click()
        await expect(transcript).to_have_attribute("open", "")


async def test_batch_warning_filter_counts_all_pages_and_keeps_failure_distinct():
    created = {}

    def prepare(_config, service):
        children = []
        for index in range(53):
            warning = index in {0, 51, 52}
            status = JobStatus.FAILED if index == 17 else (
                JobStatus.COMPLETED_WITH_WARNINGS if warning else JobStatus.COMPLETED
            )
            child = service.database.create_job(
                CaptureRequest(share_text=f"https://www.douyin.com/video/{1000 + index}"),
                status=status,
                progress=1,
            )
            service.database.update_job(
                child.id,
                result={"warnings": ["逐字稿仅有粗略窗口时间"] if warning else []},
                unlock=True,
            )
            children.append(child.id)
        parent = service.database.create_job(
            CaptureRequest(share_text="https://www.douyin.com/user/quality-check"),
            kind="creator_import",
            status=JobStatus.COMPLETED_WITH_WARNINGS,
            progress=1,
        )
        service.database.update_job(
            parent.id, result={"child_job_ids": children, "creator_name": "质量检查"}, unlock=True
        )
        created["parent"] = parent.id

    async with browser_fixture(prepare=prepare) as (page, _topics, _gates):
        await page.goto("http://testserver/jobs/" + created["parent"])
        await expect(page.locator(".job-children-header")).to_contain_text("有提示 3")
        await expect(page.locator(".job-warnings")).to_contain_text("不等于导入失败")
        assert await page.locator(".job-warnings").evaluate(
            "e => Boolean(e.compareDocumentPosition(document.querySelector('#job-children')) & 4)"
        )
        await page.get_by_role("button", name="下一页子任务", exact=True).click()
        await expect(page.locator(".job-child")).to_have_count(3)
        await page.locator("#job-children select").select_option("warnings")
        await expect(page.locator(".job-child")).to_have_count(3)
        await expect(page.locator("#job-children .fav-pager")).to_contain_text("1 / 1 页")
        await page.locator("#job-children select").select_option("failed")
        await expect(page.locator(".job-child")).to_have_count(1)
        await expect(page.locator(".job-child")).to_contain_text("失败")
