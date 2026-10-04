from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Iterable
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from ..config import MediaSettings
from ..errors import BrowserAuthRequiredError, ExternalToolError
from ..favorites_models import FavoriteFolder, FavoriteInventory, FavoriteWork
from ..models import AuthCheckResult
from .favorites_api import Resource, parse_favorite_page
from .favorites_browser import FavoritesBrowserClient
from .image_note import (
    PlaywrightImageNoteDownloader,
    _page_auth_blocked,
    _usable_auth_cookies,
)

Checkpoint = Callable[[FavoriteInventory], Awaitable[None]]
_STABLE_FOLDER_ID = re.compile(r"^[0-9]{1,32}$")

_ACCOUNT_SNAPSHOT_JS = r"""
() => { // douyin-wiki:account
  const roots = [
    document.querySelector('[data-e2e="user-info"]'),
    document.querySelector('[data-e2e="profile-user-info"]'),
    document.querySelector('header'),
  ].filter(Boolean);
  for (const root of roots) {
    const identified = root.matches('[data-sec-uid]') ? root : root.querySelector('[data-sec-uid]');
    const hrefNode = root.querySelector('a[href*="/user/"]');
    const hrefMatch = hrefNode?.getAttribute('href')?.match(/\/user\/([^/?#]+)/);
    const accountId = identified?.getAttribute('data-sec-uid') || hrefMatch?.[1] || '';
    if (!accountId || accountId === 'self') continue;
    const nickname = root.querySelector('[data-e2e="user-title"], h1, h2')
        ?.textContent?.trim() || '';
    return {account_id: accountId, nickname};
  }
  const heading = document.querySelector('h1, [data-e2e="user-title"]');
  let root = heading?.parentElement;
  for (let i = 0; root && i < 5; i++, root = root.parentElement) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const node = walker.currentNode;
      if (!node.parentElement?.getClientRects().length) continue;
      if (!/抖音号[：:]/.test(node.textContent)) continue;
      let match = node.textContent.match(/抖音号[：:]\s*([a-zA-Z0-9_.-]+)/);
      if (!match && /^\s*抖音号[：:]\s*$/.test(node.textContent)) {
        let sibling = node.nextSibling || node.parentElement.nextSibling;
        while (sibling && !sibling.textContent.trim()) sibling = sibling.nextSibling;
        if (sibling?.nodeType === Node.ELEMENT_NODE && !sibling.getClientRects().length) continue;
        match = sibling?.textContent?.match(/^\s*([a-zA-Z0-9_.-]+)\s*$/);
      }
      if (match) return {account_id:'douyin:'+match[1], nickname:heading.textContent.trim()};
    }
  }
  const ownProfile = location.pathname.match(/^\/user\/([^/?#]+)/);
  return {account_id: ownProfile?.[1] === 'self' ? '' : ownProfile?.[1] || '', nickname: ''};
}
"""

_LISTING_SNAPSHOT_JS = r"""
() => { // douyin-wiki:listing
  const roots = [...document.querySelectorAll(
    'ul.cPDrcaOY.QhXy7t32, [data-e2e="favorite-list"]'
  )].filter(node => node.getClientRects().length);
  const root = roots.length === 1 ? roots[0] : null;
  if (!root) return {records: [], terminal: false, reported_count: null, boundary_found: false};
  const records = [...root.querySelectorAll('a[href]')].map(anchor => {
    const card = anchor.closest('[data-e2e="favorite-item"], li, article') || anchor;
    const alt = anchor.querySelector('img[alt]')?.getAttribute('alt') || '';
    const separator = alt.indexOf('：');
    return {
      scope: 'favorites-list',
      href: anchor.getAttribute('href') || '',
      source_kind: [...card.querySelectorAll('span, div')].some(
        node => !node.childElementCount && node.textContent.trim() === '文章') ? 'article' : '',
      title: card.querySelector('[data-e2e="video-desc"], [data-e2e="work-title"]')
        ?.textContent?.trim()
        || anchor.getAttribute('title')?.trim()
        || (separator >= 0 ? alt.slice(separator + 1) : alt)
        || anchor.innerText?.trim() || '',
      author: card.querySelector('[data-e2e="video-author"], [data-e2e="work-author"]')
        ?.textContent?.trim()
        || (separator >= 0 ? alt.slice(0, separator) : ''),
      unavailable: card.matches('[aria-disabled="true"], [data-unavailable="true"]'),
      folder_ids: (card.getAttribute('data-folder-ids') || '').split(',').filter(Boolean),
    };
  });
  const countText = root.getAttribute('data-total-count');
  const end = root.nextElementSibling;
  const terminal = Boolean(root.querySelector(
    '[data-e2e="favorite-list-end"], [data-e2e="list-end"]'))
    || Boolean(end && /^(暂时没有更多了|没有更多了)$/.test(end.textContent.trim()));
  return {
    records,
    terminal,
    reported_count: /^\d+$/.test(countText || '') ? Number(countText) : null,
    boundary_found: true,
  };
}
"""

_SCROLL_LISTING_JS = r"""
() => { // douyin-wiki:scroll
  const roots = [...document.querySelectorAll(
    'ul.cPDrcaOY.QhXy7t32, [data-e2e="favorite-list"]'
  )].filter(node => node.getClientRects().length);
  const root = roots.length === 1 ? roots[0] : null;
  if (!root) return false;
  const scrollParent = root.closest('.route-scroll-container, [data-e2e="scroll-container"]');
  if (scrollParent) scrollParent.scrollTop = scrollParent.scrollHeight;
  else root.lastElementChild?.scrollIntoView({block: 'end'});
  return true;
}
"""


def _optional_count(value: Any) -> int | None:
    if isinstance(value, int) and value >= 0:
        return value
    text = str(value or "").strip()
    return int(text) if text.isascii() and text.isdigit() else None


def _folder_ids(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return list(
        dict.fromkeys(
            str(value).strip()
            for value in values
            if _STABLE_FOLDER_ID.fullmatch(str(value).strip())
        )
    )


def _works_from_dom(
    records: Iterable[dict[str, Any]], *, folder_id: str | None = None
) -> tuple[list[FavoriteWork], list[str]]:
    works: list[FavoriteWork] = []
    warnings: list[str] = []
    for record in records:
        if record.get("scope") != "favorites-list":
            continue
        href = str(record.get("href") or "").strip()
        href = urljoin("https://www.douyin.com/", href)
        parsed = urlsplit(href)
        match = re.fullmatch(r"/(video|note|article)/(\d+)/?", parsed.path)
        if match is None:
            continue
        route, work_id = match.groups()
        source_kind = {"video": "video", "note": "image_note", "article": "article"}[route]
        if route == "note" and record.get("source_kind") == "article":
            source_kind = "article"
        memberships = _folder_ids(record.get("folder_ids"))
        if folder_id and folder_id not in memberships:
            memberships.append(folder_id)
        try:
            works.append(
                FavoriteWork(
                    work_id=work_id,
                    canonical_url=href,
                    title=str(record.get("title") or "抖音作品"),
                    author=str(record.get("author") or ""),
                    source_kind=source_kind,
                    folder_ids=memberships,
                    available=not bool(record.get("unavailable")),
                )
            )
        except ValueError:
            warnings.append(f"作品 {work_id} 的链接不是规范抖音来源，已忽略")
    return _merge_works(works), warnings


def _merge_works(works: Iterable[FavoriteWork]) -> list[FavoriteWork]:
    merged: dict[str, FavoriteWork] = {}
    for work in works:
        existing = merged.get(work.work_id)
        if existing is None:
            merged[work.work_id] = work.model_copy(deep=True)
            continue
        if existing.canonical_url != work.canonical_url:
            continue
        if existing.source_kind != work.source_kind:
            if "article" in {existing.source_kind, work.source_kind}:
                existing = existing.model_copy(update={"source_kind": "article"})
            else:
                continue
        folder_ids = list(dict.fromkeys([*existing.folder_ids, *work.folder_ids]))
        merged[work.work_id] = existing.model_copy(
            update={
                "folder_ids": folder_ids,
                "available": existing.available or work.available,
                "title": work.title if existing.title == "抖音作品" else existing.title,
                "author": existing.author or work.author,
            }
        )
    return list(merged.values())


class DouyinFavoritesAdapter(PlaywrightImageNoteDownloader):
    """Read favorites through the isolated persistent browser profile."""

    def __init__(self, settings: MediaSettings, profile_dir: Path) -> None:
        super().__init__(settings, profile_dir)

    async def check_auth(self) -> AuthCheckResult:
        async with self._locked_profile():
            result = await self._check_auth_locked()
        messages = {
            "missing": "收藏清点使用的专用浏览器会话尚未初始化",
            "unavailable": "未安装 Playwright，无法验证收藏清点会话",
            "needs_login": "收藏清点使用的专用浏览器需要重新登录抖音",
            "ready": "专用浏览器已登录；收藏页面访问将在清点时验证",
        }
        return result.model_copy(
            update={"scope": "favorites", "message": messages.get(result.state, result.message)}
        )

    async def inventory(
        self,
        *,
        folder_ids: list[str] | None = None,
        directory_only: bool = False,
        expected_account_id: str | None = None,
        on_checkpoint: Checkpoint | None = None,
    ) -> FavoriteInventory:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise ExternalToolError("未安装 Playwright；请运行 uv sync") from exc

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        async with self._locked_profile(), async_playwright() as playwright:
            context = await playwright.chromium.launch_persistent_context(
                **self._launch_options(headless=True)
            )
            try:
                page = context.pages[0] if context.pages else await context.new_page()
                cookies = await context.cookies("https://www.douyin.com/")
                return await self._inventory_page(
                    page,
                    cookies=cookies,
                    folder_ids=folder_ids,
                    directory_only=directory_only,
                    expected_account_id=expected_account_id,
                    on_checkpoint=on_checkpoint,
                )
            finally:
                await context.close()

    async def _inventory_page(
        self,
        page: Any,
        *,
        cookies: list[dict[str, Any]],
        folder_ids: list[str] | None,
        directory_only: bool,
        expected_account_id: str | None,
        on_checkpoint: Checkpoint | None,
    ) -> FavoriteInventory:
        async with FavoritesBrowserClient(page) as client:
            return await self._inventory_with_client(
                client,
                cookies=cookies,
                folder_ids=folder_ids,
                directory_only=directory_only,
                expected_account_id=expected_account_id,
                on_checkpoint=on_checkpoint,
            )

    async def _inventory_with_client(
        self,
        client: FavoritesBrowserClient,
        *,
        cookies: list[dict[str, Any]],
        folder_ids: list[str] | None,
        directory_only: bool,
        expected_account_id: str | None,
        on_checkpoint: Checkpoint | None,
    ) -> FavoriteInventory:
        page = client.page
        response = await page.goto(
            "https://www.douyin.com/user/self?showTab=favorite_collection&showSubTab=favorite_folder",
            wait_until="domcontentloaded",
            timeout=60_000,
        )
        await page.wait_for_timeout(750)
        body = (await page.locator("body").inner_text(timeout=10_000))[:20_000]
        if not _usable_auth_cookies(cookies) or _page_auth_blocked(
            body, response.status if response else None, page.url
        ):
            raise BrowserAuthRequiredError(
                "收藏清点需要专用浏览器登录",
                details={"action": "douyin-wiki auth douyin"},
            )
        account = await page.evaluate(_ACCOUNT_SNAPSHOT_JS)
        account_id = str((account or {}).get("account_id") or "").strip()
        nickname = str((account or {}).get("nickname") or "").strip()
        if not account_id or account_id == "self":
            raise BrowserAuthRequiredError("无法确认收藏所属账号，请登录专用浏览器后重试")
        if expected_account_id and account_id != expected_account_id:
            raise BrowserAuthRequiredError(
                "专用浏览器登录账号已变化；请恢复原账号或新建收藏导入任务",
                details={
                    "action": "douyin-wiki auth douyin",
                    "expected_account_id": expected_account_id,
                    "actual_account_id": account_id,
                },
            )
        try:
            directory = await self._scan_api_resource(
                client,
                resource="directory",
                account_id=account_id,
                nickname=nickname,
                folders=[],
                folders_complete=False,
                folder_id=None,
                existing=[],
                inherited_warnings=[],
                on_checkpoint=on_checkpoint,
            )
        except ExternalToolError as exc:
            if directory_only or folder_ids is not None:
                raise
            directory = FavoriteInventory(
                account_id=account_id, nickname=nickname, warnings=[str(exc)]
            )
        if directory_only:
            return directory
        folders, folders_complete = directory.folders, directory.folders_complete
        warnings = list(directory.warnings)
        if folder_ids is None:
            if folders:
                warnings.append("全部收藏清点未遍历各收藏夹，作品的收藏夹归属可能不完整")
            if not (
                await self._click_tab(page, '[data-e2e="favorite_collection"]')
                and await self._click_tab(page, '[data-e2e="video"]')
            ):
                warnings.append("未找到 video 收藏作品子页")
                return directory.model_copy(update={"complete": False, "warnings": warnings})
            works, complete, scan_warnings = await self._scan_listing(
                page,
                account_id=account_id,
                nickname=nickname,
                folders=folders,
                folders_complete=folders_complete,
                existing=[],
                folder_id=None,
                inherited_warnings=warnings,
                on_checkpoint=on_checkpoint,
            )
            return FavoriteInventory(
                account_id=account_id,
                nickname=nickname,
                folders=folders,
                works=works,
                complete=complete,
                folders_complete=folders_complete,
                warnings=list(dict.fromkeys([*warnings, *scan_warnings])),
            )
        requested = list(dict.fromkeys(folder_ids))
        unknown = set(requested) - {folder.id for folder in folders}
        if unknown:
            raise ValueError(f"未知或无稳定标识的收藏夹 ID：{', '.join(sorted(unknown))}")
        works, complete = [], folders_complete
        for folder_id in requested:
            inventory = await self._scan_api_resource(
                client,
                resource="folder",
                account_id=account_id,
                nickname=nickname,
                folders=folders,
                folders_complete=folders_complete,
                folder_id=folder_id,
                existing=works,
                inherited_warnings=warnings,
                on_checkpoint=on_checkpoint,
            )
            works, warnings = inventory.works, inventory.warnings
            complete = complete and inventory.complete
        return FavoriteInventory(
            account_id=account_id,
            nickname=nickname,
            folders=folders,
            works=works,
            complete=complete,
            folders_complete=folders_complete,
            warnings=warnings,
        )

    async def _assert_account(self, page: Any, account_id: str) -> None:
        body = (await page.locator("body").inner_text(timeout=10_000))[:20_000]
        current = await page.evaluate(_ACCOUNT_SNAPSHOT_JS)
        if (
            _page_auth_blocked(body, None, page.url)
            or (current or {}).get("account_id") != account_id
        ):
            raise BrowserAuthRequiredError("清点期间登录失效或账号已变化，请恢复原账号后重试")

    async def _scan_api_resource(
        self,
        client: FavoritesBrowserClient,
        *,
        resource: Resource,
        account_id: str,
        nickname: str,
        folders: list[FavoriteFolder],
        folders_complete: bool,
        folder_id: str | None,
        existing: list[FavoriteWork],
        inherited_warnings: list[str],
        on_checkpoint: Checkpoint | None,
    ) -> FavoriteInventory:
        catalog = {folder.id: folder for folder in folders}
        works = _merge_works(existing)
        warnings = list(inherited_warnings)
        cursor, seen_cursors = "0", set()
        terminal, valid, reported = False, True, None
        trusted = bool(existing) or (resource == "folder" and bool(folders))
        for _ in range(500):
            await self._assert_account(client.page, account_id)
            seen_cursors.add(cursor)
            try:
                raw = await client.read_page(resource=resource, folder_id=folder_id, cursor=cursor)
                parsed = parse_favorite_page(raw)
            except ExternalToolError as exc:
                await self._assert_account(client.page, account_id)
                if not trusted:
                    raise
                warnings.append(str(exc))
                break
            await self._assert_account(client.page, account_id)
            trusted = trusted or parsed.valid or bool(parsed.folders or parsed.works)
            if not trusted:
                raise ExternalToolError("；".join(parsed.warnings) or "收藏接口没有可信结果")
            warnings.extend(parsed.warnings)
            valid = valid and parsed.valid
            for folder in parsed.folders:
                catalog[folder.id] = folder
            works = _merge_works([*works, *parsed.works])
            if parsed.reported_count is not None:
                reported = parsed.reported_count
            if on_checkpoint:
                await on_checkpoint(
                    FavoriteInventory(
                        account_id=account_id,
                        nickname=nickname,
                        folders=list(catalog.values()),
                        works=works,
                        complete=False,
                        folders_complete=folders_complete if resource == "folder" else False,
                        warnings=list(dict.fromkeys(warnings)),
                    )
                )
            if parsed.has_more is False:
                terminal = True
                break
            if parsed.has_more is None:
                break
            if not (parsed.folders if resource == "directory" else parsed.works):
                warnings.append("收藏接口返回空页但仍有下一页，清点不完整")
                break
            if not parsed.next_cursor or parsed.next_cursor in seen_cursors:
                warnings.append("收藏接口分页 cursor 缺失、不推进或循环，清点不完整")
                break
            cursor = parsed.next_cursor
        else:
            warnings.append("收藏清点达到安全扫描上限，结果不完整")
        complete = terminal and valid
        if resource == "directory":
            if reported is not None and reported != len(catalog):
                warnings.append(f"收藏夹目录显示 {reported} 个，实际读取 {len(catalog)} 个")
                complete = False
            folders_complete = complete
        else:
            folder = catalog[folder_id]
            observed = sum(folder_id in work.folder_ids for work in works)
            if folder.reported_count is not None and folder.reported_count != observed:
                warnings.append(
                    f"收藏夹「{folder.name}」显示 {folder.reported_count} 条，"
                    f"实际读取 {observed} 条，数量不一致"
                )
                complete = False
        if not terminal:
            warnings.append("未取得可信的收藏分页结束信号")
        return FavoriteInventory(
            account_id=account_id,
            nickname=nickname,
            folders=list(catalog.values()),
            works=works,
            complete=complete,
            folders_complete=folders_complete,
            warnings=list(dict.fromkeys(warnings)),
        )

    async def _scan_listing(
        self,
        page: Any,
        *,
        account_id: str,
        nickname: str,
        folders: list[FavoriteFolder],
        folders_complete: bool,
        existing: list[FavoriteWork],
        folder_id: str | None,
        inherited_warnings: list[str],
        on_checkpoint: Checkpoint | None,
    ) -> tuple[list[FavoriteWork], bool, list[str]]:
        works = _merge_works(existing)
        warnings: list[str] = []
        previous_count = len(works)
        idle_rounds = 0
        terminal = False
        for _ in range(500):
            body = (await page.locator("body").inner_text(timeout=10_000))[:20_000]
            current = await page.evaluate(_ACCOUNT_SNAPSHOT_JS)
            if (
                _page_auth_blocked(body, None, page.url)
                or (current or {}).get("account_id") != account_id
            ):
                raise BrowserAuthRequiredError("清点期间登录失效或账号已变化，请恢复原账号后重试")
            snapshot = await page.evaluate(_LISTING_SNAPSHOT_JS)
            if not snapshot.get("boundary_found", True):
                warnings.append("未找到收藏作品列表边界；未扫描全页链接")
                break
            batch, batch_warnings = _works_from_dom(
                snapshot.get("records") or [], folder_id=folder_id
            )
            warnings.extend(batch_warnings)
            works = _merge_works([*works, *batch])
            terminal = snapshot.get("terminal") is True

            if on_checkpoint:
                checkpoint_warnings = list(dict.fromkeys([*inherited_warnings, *warnings]))
                await on_checkpoint(
                    FavoriteInventory(
                        account_id=account_id,
                        nickname=nickname,
                        folders=folders,
                        works=works,
                        complete=False,
                        folders_complete=folders_complete,
                        warnings=checkpoint_warnings,
                    )
                )
            if terminal:
                break

            if len(works) == previous_count:
                idle_rounds += 1
            else:
                idle_rounds = 0
                previous_count = len(works)
            if idle_rounds >= 5:
                break
            scrolled = await page.evaluate(_SCROLL_LISTING_JS)
            if not scrolled:
                break
            await page.wait_for_timeout(750)
        else:
            warnings.append("收藏清点达到安全扫描上限，结果不完整")

        reported = _optional_count(snapshot.get("reported_count"))
        observed = sum(folder_id is None or folder_id in work.folder_ids for work in works)
        if reported is not None and reported != observed:
            terminal = False
            warnings.append(f"作品列表显示 {reported} 条，实际识别 {observed} 条")
        if warnings:
            terminal = False
        if not terminal:
            warnings.append("未取得可信的收藏作品分页结束信号")
        return works, terminal, list(dict.fromkeys(warnings))

    async def _click_tab(self, page: Any, selector: str) -> bool:
        tab_ids = {
            '[data-e2e="favorite_collection"]': "#semiTabfavorite_collection",
            '[data-e2e="favorite_folder"]': "#semiTabfavorite_folder",
            '[data-e2e="video"]': "#semiTabvideo",
        }
        for candidate in (tab_ids.get(selector, selector), selector):
            locator = page.locator(candidate).first
            if await locator.count() == 0:
                continue
            with suppress(Exception):
                await locator.click(timeout=10_000)
                await page.wait_for_timeout(500)
                return True
        return False
