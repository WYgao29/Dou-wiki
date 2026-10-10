"""Read one public collection through the authenticated browser environment."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from pathlib import Path

from ..collection_models import CollectionInventory, CollectionProfile, stable_id
from ..config import MediaSettings
from ..errors import BrowserAuthRequiredError, ExternalToolError
from .collection_api import parse_collection_page
from .collection_browser import CollectionBrowserClient
from .image_note import PlaywrightImageNoteDownloader, _page_auth_blocked, _usable_auth_cookies

Checkpoint = Callable[[CollectionInventory], Awaitable[None]]


def _account_fingerprint(cookies):
    usable = _usable_auth_cookies(cookies)
    # Prefer the stable session token; sid_guard includes expiry metadata.
    tokens = [c for c in usable if c["name"] in {"sessionid", "sessionid_ss"}] or usable
    if not tokens:
        return ""
    value = "\n".join(sorted(f"{c['name']}:{c['value']}" for c in tokens))
    return hashlib.sha256(value.encode()).hexdigest()


class DouyinCollectionAdapter(PlaywrightImageNoteDownloader):
    def __init__(self, settings: MediaSettings, profile_dir: Path):
        super().__init__(settings, profile_dir)

    async def check_auth(self):
        result = await super().check_auth()
        return result.model_copy(update={"scope": "collection"})

    async def inventory(
        self,
        *,
        mix_id: str,
        original_url: str,
        expected_account_id: str | None = None,
        on_checkpoint: Checkpoint | None = None,
        count: int = 20,
    ) -> CollectionInventory:
        if stable_id(mix_id) is None or type(count) is not int or not 1 <= count <= 1000:
            raise ValueError("合集编号或每页数量不合法")
        from playwright.async_api import async_playwright

        async with self._locked_profile(), async_playwright() as playwright:
            context = await playwright.chromium.launch_persistent_context(
                **self._launch_options(headless=True)
            )
            try:
                page = context.pages[0] if context.pages else await context.new_page()
                cookies = await context.cookies("https://www.douyin.com/")
                account_id = _account_fingerprint(cookies)
                if not account_id:
                    raise BrowserAuthRequiredError("合集清点需要专用浏览器登录抖音")
                if expected_account_id and expected_account_id != account_id:
                    raise BrowserAuthRequiredError("合集清点账号已变化，请恢复原账号或新建批次")
                async with CollectionBrowserClient(page) as client:
                    response = await page.goto(
                        f"https://www.douyin.com/collection/{mix_id}",
                        wait_until="domcontentloaded",
                        timeout=60_000,
                    )
                    await page.wait_for_timeout(750)
                    body = (await page.locator("body").inner_text(timeout=10_000))[:20_000]
                    if (response and response.status == 401) or _page_auth_blocked(
                        body, None, page.url
                    ):
                        raise BrowserAuthRequiredError("合集页面要求登录或安全验证")
                    if response and response.status >= 400:
                        raise ExternalToolError(f"合集页面 HTTP 状态异常：{response.status}")
                    detail = parse_collection_page(
                        await client.read_page(resource="detail", mix_id=mix_id)
                    )
                    profile = detail.profile.model_copy(update={"original_url": original_url})

                    async def checkpoint(snapshot):
                        current = _account_fingerprint(
                            await context.cookies("https://www.douyin.com/")
                        )
                        if current != account_id:
                            raise BrowserAuthRequiredError("读取期间专用浏览器账号已变化")
                        if on_checkpoint:
                            await on_checkpoint(snapshot)

                    return await self.inventory_with_client(
                        client,
                        profile=profile,
                        account_id=account_id,
                        on_checkpoint=checkpoint,
                        count=count,
                    )
            finally:
                await context.close()

    async def inventory_with_client(
        self,
        client,
        *,
        profile: CollectionProfile,
        account_id: str,
        on_checkpoint: Checkpoint | None = None,
        count: int = 20,
    ) -> CollectionInventory:
        works, warnings, seen_cursors = {}, [], set()
        cursor, complete = "0", False
        snapshot = CollectionInventory(profile=profile, account_id=account_id)
        for _ in range(500):
            seen_cursors.add(cursor)
            try:
                raw = await client.read_page(
                    resource="list", mix_id=profile.mix_id, cursor=cursor, count=count
                )
                page = parse_collection_page(raw)
            except BrowserAuthRequiredError:
                raise
            except ExternalToolError as exc:
                if not works:
                    raise
                warnings.append(str(exc))
                break
            warnings.extend(page.warnings)
            for work in page.works:
                if work.work_id not in works:
                    works[work.work_id] = work.model_copy(update={"position": len(works) + 1})
            finished = page.has_more is False
            bad_cursor = page.has_more and (
                page.next_cursor is None or page.next_cursor in seen_cursors
            )
            if bad_cursor:
                warnings.append("合集 cursor 缺失或形成循环，已停止读取")
            if (
                finished
                and profile.reported_count is not None
                and profile.reported_count != len(works)
            ):
                warnings.append(f"合集报告 {profile.reported_count} 项，实际读到 {len(works)} 项")
            complete = finished and not warnings
            snapshot = CollectionInventory(
                profile=profile,
                account_id=account_id,
                works=list(works.values()),
                complete=complete,
                warnings=list(dict.fromkeys(warnings)),
                request_cursor=cursor,
                next_cursor=page.next_cursor,
            )
            if on_checkpoint:
                await on_checkpoint(snapshot)
            if finished or bad_cursor or page.has_more is None:
                break
            cursor = page.next_cursor
        else:
            warnings.append("合集读取达到 500 页上限")
        if not complete:
            snapshot = snapshot.model_copy(
                update={
                    "complete": False,
                    "warnings": list(dict.fromkeys(warnings)),
                }
            )
        return snapshot
