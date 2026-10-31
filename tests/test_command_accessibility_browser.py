"""Command search focus, keyboard visibility, and short desktop viewports."""

from urllib.parse import quote

import pytest
from playwright.async_api import expect

from scripts.verify_review_web_races import block, browser_fixture, entered


def _search_notes(config, _service):
    folder = config.vault_path / "wiki/sources"
    original = folder / "测试文章_123.md"
    original.write_text(original.read_text() + "\n缓存保留完整的研究上下文。\n")
    for index in range(7):
        title = f"第 {index + 1} 篇资料：保存较长的标题和搜索片段以便核对研究依据"
        (folder / f"键盘检索_{3000 + index}.md").write_text(
            f"---\ntype: source\nvideo_id: '{3000 + index}'\ntitle: {title}\n"
            "author: 验证资料\nsource_kind: video\n"
            "captured: '2026-10-30T10:00:00+08:00'\n---\n"
            f"# {title}\n\n## Summary\n\n"
            "缓存可以保留研究上下文，让下一次检索能继续核对原始资料和实践条件。\n"
        )


async def test_command_combobox_tracks_selection_and_clears_stale_references():
    async with browser_fixture(prepare=_search_notes) as (page, _topics, gates):
        await page.locator("#sidebar-search").click()
        query = page.get_by_role(
            "combobox", name="搜索文章、博主、标签或灵感", exact=True, include_hidden=True
        )
        await expect(query).to_have_attribute("aria-controls", "command-results")
        await expect(query).to_have_attribute("aria-autocomplete", "list")
        await expect(query).to_have_attribute("aria-expanded", "true")
        selected = page.locator('#command-results [aria-selected="true"]')
        first_id = await selected.get_attribute("id")
        assert first_id
        await expect(query).to_have_attribute("aria-activedescendant", first_id)
        await query.press("ArrowDown")
        next_id = await selected.get_attribute("id")
        assert next_id and next_id != first_id
        await expect(query).to_have_attribute("aria-activedescendant", next_id)
        await expect(query).to_be_focused()

        gate = block(gates, "/api/library?q=" + quote("缓存") + "&limit=8&fuzzy=true")
        await query.fill("缓存")
        await entered(gate)
        await expect(query).to_have_attribute("aria-expanded", "false")
        assert await query.get_attribute("aria-activedescendant") is None
        gate["release"].set()
        await expect(page.locator("#command-results [role=option]")).to_have_count(8)
        item_id = await selected.get_attribute("id")
        assert item_id
        await expect(query).to_have_attribute("aria-activedescendant", item_id)

        await query.fill("不存在的研究资料")
        await expect(page.locator("#command-status")).to_have_text("没有找到匹配的文章或命令。")
        await expect(query).to_have_attribute("aria-expanded", "false")
        assert await query.get_attribute("aria-activedescendant") is None
        await query.fill("缓存")
        await expect(page.locator("#command-results [role=option]")).to_have_count(8)
        await expect(query).to_have_attribute("aria-activedescendant", item_id)
        await query.press("Escape")
        await expect(query).to_have_attribute("aria-expanded", "false")
        assert await query.get_attribute("aria-activedescendant") is None
        await expect(page.locator("#sidebar-search")).to_be_focused()

        await page.locator("#sidebar-search").click()
        await expect(query).to_have_attribute("aria-expanded", "true")
        await query.fill(">任务")
        await expect(selected).to_contain_text("打开任务中心")
        await expect(query).to_have_attribute(
            "aria-activedescendant", await selected.get_attribute("id")
        )
        await query.press("Enter")
        await expect(page.locator("#jobs-view")).to_be_visible()
        await expect(query).to_have_attribute("aria-expanded", "false")
        assert await query.get_attribute("aria-activedescendant") is None


async def test_command_arrow_keys_keep_the_selected_result_visible_and_open_it():
    async with browser_fixture(prepare=_search_notes) as (page, _topics, _gates):
        await page.locator("#sidebar-search").click()
        query = page.locator("#command-input")
        await query.fill("缓存")
        await expect(page.locator("#command-results [role=option]")).to_have_count(8)
        selected = page.locator('#command-results [aria-selected="true"]')
        for _ in range(7):
            await query.press("ArrowDown")
        await expect(selected).to_be_in_viewport(ratio=1)
        last_id = await selected.get_attribute("id")
        await expect(query).to_be_focused()
        await page.set_viewport_size({"width": 960, "height": 402})
        await expect(selected).to_be_in_viewport(ratio=1)
        assert await selected.get_attribute("id") == last_id
        await query.press("ArrowDown")
        await expect(selected).to_be_in_viewport(ratio=1)
        assert await selected.get_attribute("id") != last_id
        await query.press("ArrowUp")
        await expect(selected).to_be_in_viewport(ratio=1)
        assert await selected.get_attribute("id") == last_id
        entry_id = await selected.get_attribute("data-entry-id")
        await query.press("Enter")
        await expect(page).to_have_url("http://testserver/articles/" + entry_id)
        await expect(page.locator("#main-content")).to_be_focused()


@pytest.mark.parametrize("size", [(960, 402), (1280, 536), (1440, 900)])
async def test_command_dialog_and_help_fit_short_viewports_in_success_and_error(size):
    async with browser_fixture(prepare=_search_notes) as (page, _topics, gates):
        await page.set_viewport_size({"width": size[0], "height": size[1]})
        await page.locator("#sidebar-search").click()
        query = page.locator("#command-input")
        await query.fill("缓存")
        await expect(page.locator("#command-results [role=option]")).to_have_count(8)

        async def assert_content_fits():
            bounds = await page.locator("#command-dialog").bounding_box()
            assert bounds["y"] >= 15
            assert bounds["y"] + bounds["height"] <= size[1] - 15
            for selector in ["#command-input", ".command-help"]:
                for element in await page.locator(selector).all():
                    await expect(element).to_be_in_viewport(ratio=1)

        await assert_content_fits()
        gate = block(
            gates, "/api/library?q=" + quote("故障演示") + "&limit=8&fuzzy=true", status=503
        )
        await query.fill("故障演示")
        await entered(gate)
        gate["release"].set()
        await expect(page.get_by_role("button", name="重试搜索", exact=True)).to_be_in_viewport(
            ratio=1
        )
        await assert_content_fits()
