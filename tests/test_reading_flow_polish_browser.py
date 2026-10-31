"""Search hierarchy and keyboard continuity from results to original evidence."""

import pytest
from playwright.async_api import expect

from scripts.verify_readwise_reader import png
from scripts.verify_review_web_races import block, browser_fixture, entered


def _search_copy(config, _service):
    folder = config.vault_path / "wiki/sources"
    source = folder / "测试文章_123.md"
    source.write_text(source.read_text().replace(
        "# 测试文章", "# 缓存与上下文 Straße <script>保留为文字</script>"
    ))
    (folder / "另一篇_456.md").write_text(
        "---\ntype: source\nvideo_id: '456'\ntitle: 另一篇文章\n---\n"
        "# 另一篇文章\n\n## 一句话\n\n缓存与 Straße 都应保留原始上下文。\n"
    )


@pytest.mark.parametrize(("query", "match"), [("缓存", "缓存"), ("STRASSE", "Straße")])
async def test_search_uses_one_explanation_before_metadata_in_all_views(query, match):
    async with browser_fixture(prepare=_search_copy) as (page, _topics, _gates):
        await expect(page.locator("#library-results .item-summary")).to_have_count(2)
        await page.locator("#search-input").fill(query)
        await expect(page.locator("#library-results .library-item")).to_have_count(2)
        title_hit = page.locator('#library-results [data-entry-id="dy-123"]')
        summary_hit = page.locator('#library-results [data-entry-id="dy-456"]')
        await expect(title_hit.locator("h2 mark")).to_have_text(match)
        await expect(title_hit.locator(".search-match")).to_have_count(0)
        await expect(title_hit.locator(".item-summary")).to_have_text("这是一条本地资料。")
        await expect(summary_hit.locator(".item-summary")).to_have_count(0)
        await expect(summary_hit.locator(".search-match mark")).to_have_text(match)
        assert await summary_hit.locator(".search-match").evaluate(
            "e => e.getBoundingClientRect().bottom <= "
            "e.parentElement.querySelector('.item-metadata').getBoundingClientRect().top"
        )
        assert await title_hit.locator("script").count() == 0

        await page.locator("#view-gallery").click()
        await expect(title_hit.locator("h2 mark")).to_have_text(match)
        await expect(title_hit.locator(".search-match")).to_have_count(0)
        assert await summary_hit.locator(".search-match").evaluate(
            "e => e.getBoundingClientRect().bottom <= "
            "e.parentElement.querySelector('.item-metadata').getBoundingClientRect().top"
        )
        await expect(summary_hit.locator("a")).to_have_attribute(
            "aria-describedby", await summary_hit.locator(".search-match").get_attribute("id")
        )

        await page.locator("#sidebar-search").click()
        title_option = page.locator('#command-results [data-entry-id="dy-123"]')
        summary_option = page.locator('#command-results [data-entry-id="dy-456"]')
        await expect(title_option.locator("strong mark")).to_have_text(match)
        await expect(title_option.locator(".search-match")).to_have_count(0)
        assert await summary_option.locator(".search-match").evaluate(
            "e => e.nextElementSibling?.tagName === 'SPAN'"
        )
        assert await page.locator("#command-results script").count() == 0
        await page.locator("#command-input").fill("缓存下")
        await expect(title_option.locator(".search-match")).to_contain_text("近似匹配")
        await expect(title_option.locator("strong mark")).to_have_count(0)


def _reading_evidence(config, service):
    folder = config.vault_path / "wiki/sources"
    source = folder / "测试文章_123.md"
    source.write_text(source.read_text().replace(
        "## 来源", "\n\n".join(f"缓存实践 {i}：核对原话和适用条件。" for i in range(18))
        + "\n\n## 来源"
    ))
    for index in range(22):
        (folder / f"检索资料_{5000 + index}.md").write_text(
            f"---\ntype: source\nvideo_id: '{5000 + index}'\ntitle: 检索资料 {index}\n"
            "source_kind: video\ncaptured: '2026-10-31T08:00:00+08:00'\n---\n"
            f"# 检索资料 {index}\n\n## 一句话\n\n缓存用于保留研究材料。\n"
        )
    entry = service.database.get_entry("dy-123")
    data = service.database.get_entry_data("dy-123")
    data["transcript_corrected"] = [
        {"id": 0, "start_ms": 0, "end_ms": 30000, "text": "缓存需要核对原话。"},
        {"id": 1, "start_ms": 30000, "end_ms": 60000, "text": "保留适用条件。"},
    ]
    frames = config.vault_path / "raw/assets/123/frames"
    frames.mkdir()
    (frames / "frame-0000000000.png").write_bytes(png(160, 90))
    data["frames"] = [{"path": "raw/assets/123/frames/frame-0000000000.png", "timestamp_ms": 0}]
    data["frame_manifest_version"] = 1
    service.database.upsert_entry(entry, data)


@pytest.mark.parametrize("width", [960, 1440])
async def test_evidence_keyboard_focus_and_return_restore_search_context(width):
    async with browser_fixture(prepare=_reading_evidence) as (page, _topics, _gates):
        await page.set_viewport_size({"width": width, "height": 800})
        await page.locator("select[data-theme-select]").select_option(
            "dark" if width == 960 else "light"
        )
        await page.locator("#search-input").fill("缓存")
        await expect(page.locator("#result-count")).to_have_text("23")
        await page.locator('[data-source="video"]').click()
        await page.locator("#view-gallery").click()
        row = page.locator('#library-results a[href="/articles/dy-123"]')
        await row.scroll_into_view_if_needed()
        await row.focus()
        main = page.locator("#main-content")
        saved_scroll = await main.evaluate("e => e.scrollTop")
        saved_url = page.url
        assert saved_scroll > 0
        await row.press("Enter")
        await page.get_by_role("link", name="Summary", exact=True).press("Enter")
        await expect(page.locator('.article-body h2.is-reading-focus')).to_be_focused()
        await page.keyboard.press("j")
        await expect(page.locator('.article-body p.is-reading-focus')).to_be_focused()
        await page.evaluate("openArticle('dy-123', false)")
        await expect(page.locator('.article-body p.is-reading-focus')).to_be_focused()

        await page.get_by_role("link", name="逐字稿 · 2段", exact=True).press("Enter")
        await expect(page.locator("#article-transcript-summary")).to_be_focused()
        await page.keyboard.press("Tab")
        await expect(page.locator("#article-keyframes-summary")).to_be_focused()
        await page.keyboard.press("Space")
        await expect(page.locator(".article-keyframes")).to_have_attribute("open", "")
        await page.keyboard.press("Tab")
        await expect(page.locator(".keyframe-grid a").first).to_be_focused()
        await page.keyboard.press("Escape")
        await expect(row).to_be_focused()
        await expect(page.locator("#search-input")).to_have_value("缓存")
        await expect(page.locator("#view-gallery")).to_have_attribute("aria-pressed", "true")
        assert page.url == saved_url
        assert abs(await main.evaluate("e => e.scrollTop") - saved_scroll) <= 1


async def test_failed_article_keeps_error_keyboard_accessible_and_returns_to_query():
    async with browser_fixture() as (page, _topics, gates):
        await page.locator("#search-input").fill("测试")
        await expect(page.locator("#result-count")).to_have_text("1")
        await page.locator("#view-gallery").click()
        row = page.locator('#library-results a[href="/articles/dy-123"]')
        failure = block(gates, "/api/articles/dy-123", status=500)
        await row.press("Enter")
        await entered(failure)
        failure["release"].set()
        await expect(page.locator("#article-content .error-state")).to_be_visible()
        await expect(page.locator("#main-content")).to_be_focused()
        back = page.get_by_role("button", name="返回资料库", exact=True)
        await back.press("Enter")
        await expect(row).to_be_focused()
        await expect(page.locator("#search-input")).to_have_value("测试")
        await expect(page.locator("#view-gallery")).to_have_attribute("aria-pressed", "true")
