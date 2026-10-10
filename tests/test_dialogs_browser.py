from pathlib import Path

import pytest
from playwright.async_api import async_playwright


@pytest.fixture
async def offline_page():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        try:
            yield page
        finally:
            await browser.close()


async def test_single_import_displays_disabled_explanation_without_form(offline_page):
    page = offline_page
    root = Path(__file__).parents[1] / "src/douyin_wiki/webapp"
    template = (root / "templates/app.html").read_text()
    begin = template.index('<section id="imports-single-view"')
    end = template.index('<section id="imports-creators-view"', begin)
    await page.set_content(template[begin:end])
    await page.locator("#imports-single-view").evaluate("node => node.classList.remove('hidden')")
    assert "已停用" in await page.locator("#imports-single-view").inner_text()
    assert await page.locator("form,textarea,input").count() == 0
    assert await page.get_by_role("link", name="查看已有任务").get_attribute("href") == "/jobs"
