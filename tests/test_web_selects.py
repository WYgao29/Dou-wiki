"""Shared select controls retain native values, events, forms and keyboard behavior."""

from playwright.async_api import expect

from scripts.verify_review_web_races import browser_fixture


async def test_select_menu_commits_once_and_tracks_existing_value_updates():
    async with browser_fixture() as (page, _topics, _gates):
        native = page.locator("#sort-select")
        trigger = page.get_by_role("combobox", name="排序方式", exact=True)
        await native.evaluate(
            "e => { window.selectChanges = 0; "
            "e.addEventListener('change', () => window.selectChanges++); }"
        )
        await trigger.click()
        menu = page.get_by_role("listbox", name="排序方式")
        await expect(menu).to_be_visible()
        await expect(menu.get_by_role("option", name="相关度", exact=True)).to_have_count(0)
        await menu.get_by_role("option", name="标题", exact=True).click()
        await expect(menu).to_be_hidden()
        await expect(trigger).to_have_text("标题")
        assert await native.input_value() == "title_asc"
        assert await page.evaluate("window.selectChanges") == 1
        assert "sort=title_asc" in page.url

        await native.evaluate("e => { e.value = 'author_asc'; }")
        await expect(trigger).to_have_text("博主")
        assert await page.evaluate("window.selectChanges") == 1
        await native.select_option("captured_desc")
        await expect(trigger).to_have_text("最近加入")
        assert await page.evaluate("window.selectChanges") == 2


async def test_select_keyboard_escape_tab_and_application_shortcuts():
    async with browser_fixture() as (page, _topics, _gates):
        trigger = page.get_by_role("combobox", name="排序方式", exact=True)
        native = page.locator("#sort-select")
        before = await page.evaluate("state.focusedEntryId")
        await native.focus()
        await expect(trigger).to_be_focused()
        await page.keyboard.press("j")
        await expect(page.get_by_role("listbox")).to_be_visible()
        assert await page.evaluate("state.focusedEntryId") == before
        await page.keyboard.press("ArrowDown")
        await page.keyboard.press("Escape")
        await expect(trigger).to_be_focused()
        assert await native.input_value() == "captured_desc"

        await page.keyboard.press("ArrowDown")
        await page.keyboard.press("ArrowDown")
        await page.keyboard.press("Enter")
        assert await native.input_value() == "published_desc"
        await expect(trigger).to_have_text("发布时间：从新到旧")
        await page.keyboard.press("Space")
        await page.keyboard.press("Tab")
        await expect(page.get_by_role("listbox")).to_be_hidden()
        await expect(trigger).not_to_be_focused()


async def test_dynamic_select_in_modal_preserves_constraints_and_form_reset():
    async with browser_fixture() as (page, _topics, _gates):
        await page.evaluate("""() => {
          const dialog = document.createElement('dialog');
          dialog.id = 'select-test-dialog';
          dialog.innerHTML = `<form id="select-test-form">
            <label for="dynamic-select">选择项目</label>
            <select id="dynamic-select" name="target" required>
              <option value="">请选择</option><option value="alpha">Alpha</option>
              <option value="disabled" disabled>Disabled</option>
              <option value="hidden" hidden>Hidden</option><option value="beta">Beta</option>
            </select><button type="reset">重置</button></form>`;
          document.body.append(dialog); dialog.showModal();
        }""")
        trigger = page.get_by_role("combobox", name="选择项目", exact=True)
        await expect(trigger).to_be_visible()
        await trigger.click()
        menu = page.get_by_role("listbox", name="选择项目", exact=True)
        await expect(menu).to_be_visible()
        await expect(menu.get_by_role("option", name="Hidden", exact=True)).to_have_count(0)
        await expect(menu.get_by_role("option", name="Disabled", exact=True)).to_have_attribute(
            "aria-disabled", "true"
        )
        await menu.get_by_role("option", name="Beta", exact=True).click()
        assert await page.evaluate(
            "new FormData(document.querySelector('#select-test-form')).get('target')"
        ) == "beta"
        await page.get_by_role("button", name="重置", exact=True).click()
        await expect(trigger).to_have_text("请选择")
        assert not await page.evaluate(
            "document.querySelector('#select-test-form').reportValidity()"
        )
        await expect(trigger).to_be_focused()
        await expect(trigger).to_have_attribute("aria-invalid", "true")
        await page.locator("#dynamic-select").evaluate("e => { e.disabled = true; }")
        await expect(trigger).to_be_disabled()


async def test_select_closes_on_outside_click_and_when_its_view_is_hidden():
    async with browser_fixture() as (page, _topics, _gates):
        trigger = page.get_by_role("combobox", name="排序方式", exact=True)
        button = await trigger.element_handle()
        await trigger.click()
        await page.locator("#view-title").click()
        await expect(page.get_by_role("listbox")).to_be_hidden()
        await trigger.click()
        await page.evaluate("window.Douku.navigate('/jobs')")
        await expect(page.get_by_role("listbox")).to_be_hidden()
        assert await button.get_attribute("aria-expanded") == "false"
