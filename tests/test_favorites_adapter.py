from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from douyin_wiki.adapters.favorites import (
    DouyinFavoritesAdapter,
    _merge_works,
    _works_from_dom,
)
from douyin_wiki.adapters.favorites_api import RawFavoritePage, parse_favorite_page
from douyin_wiki.config import MediaSettings
from douyin_wiki.errors import BrowserAuthRequiredError, ExternalToolError, JobLeaseLostError
from douyin_wiki.favorites_models import FavoriteWork

WORK_ID = "1234567890123456789"
SECOND_WORK_ID = "2234567890123456789"


def raw_page(resource, folder_id, cursor, data):
    return RawFavoritePage(resource, folder_id, cursor, 20, 200, json.dumps(data).encode())


class ScriptedApiClient:
    def __init__(self, page):
        self.page = page

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def read_page(self, *, resource, folder_id=None, cursor="0", **_):
        self.page.api_calls.append((resource, folder_id, cursor))
        value = self.page.api_pages.get((resource, folder_id, cursor))
        if value is None and resource == "directory":
            records = self.page.directory.get("records", [])
            value = {
                "status_code": 0,
                "has_more": 0,
                "collects_list": [
                    {
                        "collects_id_str": r.get("platform_id"),
                        "collects_name": r.get("name"),
                        "total_number": r.get("reported_count"),
                    }
                    for r in records
                ],
                "total_number": len(records),
            }
        if isinstance(value, BaseException):
            raise value
        if value is None:
            raise AssertionError(f"unexpected API call: {(resource, folder_id, cursor)}")
        return raw_page(resource, folder_id, cursor, value)


@pytest.fixture(autouse=True)
def scripted_client(monkeypatch):
    monkeypatch.setattr(
        "douyin_wiki.adapters.favorites.FavoritesBrowserClient", ScriptedApiClient, raising=False
    )


def test_work_rejects_external_source() -> None:
    with pytest.raises(ValidationError):
        FavoriteWork(
            work_id=WORK_ID,
            canonical_url=f"https://evil.example/video/{WORK_ID}",
        )


def test_work_normalizes_protocol_relative_note_url() -> None:
    work = FavoriteWork(
        work_id=WORK_ID,
        canonical_url=f"//www.douyin.com/note/{WORK_ID}?previous_page=web_code_link",
        source_kind="image_note",
    )

    assert work.canonical_url == f"https://www.douyin.com/note/{WORK_ID}"


def test_work_rejects_url_with_different_work_id() -> None:
    with pytest.raises(ValidationError):
        FavoriteWork(
            work_id=WORK_ID,
            canonical_url=f"https://www.douyin.com/video/{SECOND_WORK_ID}",
        )


def test_duplicate_work_merges_folder_memberships() -> None:
    merged = _merge_works(
        [
            FavoriteWork(
                work_id=WORK_ID,
                canonical_url=f"https://www.douyin.com/video/{WORK_ID}",
                folder_ids=["1001"],
            ),
            FavoriteWork(
                work_id=WORK_ID,
                canonical_url=f"https://www.douyin.com/video/{WORK_ID}",
                folder_ids=["1002", "1001"],
            ),
        ]
    )

    assert len(merged) == 1
    assert merged[0].folder_ids == ["1001", "1002"]


def test_dom_records_keep_articles_and_exclude_non_list_anchors() -> None:
    works, warnings = _works_from_dom(
        [
            {
                "scope": "favorites-list",
                "href": f"/article/{WORK_ID}?from=collection",
                "title": "一篇长文",
            },
            {
                "scope": "recommendation",
                "href": f"/video/{SECOND_WORK_ID}",
                "title": "页脚推荐",
            },
        ]
    )

    assert [(item.work_id, item.source_kind) for item in works] == [(WORK_ID, "article")]
    assert warnings == []


def test_folder_catalog_never_uses_display_name_as_identity():
    page = parse_favorite_page(
        raw_page(
            "directory",
            None,
            "0",
            {
                "status_code": 0,
                "has_more": 0,
                "collects_list": [
                    {"collects_name": "旅行", "share_url": "/user/self"},
                    {"collects_name": "AI", "collects_id_str": "2002", "total_number": 3},
                ],
                "total_number": 2,
            },
        )
    )
    assert [f.id for f in page.folders] == ["2002"]
    assert not page.valid
    assert any("稳定 ID" in w for w in page.warnings)


class FakeResponse:
    status = 200


class FakeLocator:
    def __init__(self, page: SyntheticFavoritesPage, selector: str) -> None:
        self.page = page
        self.selector = selector

    @property
    def first(self) -> FakeLocator:
        return self

    async def count(self) -> int:
        return int(self.selector in self.page.visible_selectors)

    async def click(self, **_: Any) -> None:
        self.page.clicked.append(self.selector)

    async def inner_text(self, **_: Any) -> str:
        return self.page.body


class SyntheticFavoritesPage:
    def __init__(
        self,
        *,
        account_id: str = "MS4wLjABAAAA-test-account",
        body: str = "我的抖音主页",
        listings: list[dict[str, Any]] | None = None,
        directory: dict[str, Any] | None = None,
    ) -> None:
        self.url = "https://www.douyin.com/"
        self.body = body
        self.account_id = account_id
        self.listings = listings or []
        self.directory = directory or {
            "records": [],
            "terminal": True,
            "reported_count": 0,
        }
        self.listing_index = 0
        self.clicked: list[str] = []
        self.api_pages = {}
        self.api_calls = []
        self.checkpoint_count = 0
        self.scroll_count = 0
        self.visible_selectors = {
            '[data-e2e="favorite_collection"]',
            '[data-e2e="favorite_folder"]',
            '[data-e2e="video"]',
        }

    async def goto(self, url: str, **_: Any) -> FakeResponse:
        self.url = url
        return FakeResponse()

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)

    async def wait_for_timeout(self, _: int) -> None:
        return None

    async def evaluate(self, script: str, arg: Any = None) -> Any:
        if "douyin-wiki:account" in script:
            return {"account_id": self.account_id, "nickname": "测试账号"}
        if "douyin-wiki:directory" in script:
            return self.directory
        if "douyin-wiki:open-folder" in script:
            return any(record.get("platform_id") == arg for record in self.directory["records"])
        if "douyin-wiki:listing" in script:
            if not self.listings:
                return {"records": [], "terminal": False}
            return self.listings[min(self.listing_index, len(self.listings) - 1)]
        if "douyin-wiki:scroll" in script:
            self.scroll_count += 1
            self.listing_index += 1
            return True
        raise AssertionError("unexpected evaluate script")


@pytest.mark.asyncio
async def test_inventory_raises_auth_required_for_login_wall(tmp_path: Path) -> None:
    adapter = DouyinFavoritesAdapter(MediaSettings(), tmp_path / "profile")
    page = SyntheticFavoritesPage(body="请登录后查看")

    with pytest.raises(BrowserAuthRequiredError):
        await adapter._inventory_page(
            page,
            cookies=[],
            folder_ids=None,
            directory_only=False,
            expected_account_id=None,
            on_checkpoint=None,
        )


@pytest.mark.asyncio
async def test_inventory_rejects_changed_account_on_resume(tmp_path: Path) -> None:
    adapter = DouyinFavoritesAdapter(MediaSettings(), tmp_path / "profile")
    page = SyntheticFavoritesPage(account_id="current-account")

    with pytest.raises(BrowserAuthRequiredError, match="账号已变化"):
        await adapter._inventory_page(
            page,
            cookies=[{"name": "sessionid", "domain": ".douyin.com", "expires": -1}],
            folder_ids=None,
            directory_only=True,
            expected_account_id="original-account",
            on_checkpoint=None,
        )


@pytest.mark.asyncio
async def test_inventory_checkpoints_cumulative_results_before_scrolling(tmp_path: Path) -> None:
    adapter = DouyinFavoritesAdapter(MediaSettings(), tmp_path / "profile")
    page = SyntheticFavoritesPage(
        listings=[
            {
                "records": [
                    {
                        "scope": "favorites-list",
                        "href": f"/video/{WORK_ID}",
                        "title": "第一条",
                    }
                ],
                "terminal": False,
            },
            {
                "records": [
                    {
                        "scope": "favorites-list",
                        "href": f"/video/{SECOND_WORK_ID}",
                        "title": "第二条",
                    }
                ],
                "terminal": True,
            },
        ]
    )
    checkpoints: list[list[str]] = []

    async def checkpoint(snapshot: Any) -> None:
        checkpoints.append([work.work_id for work in snapshot.works])
        page.checkpoint_count += 1

    result = await adapter._inventory_page(
        page,
        cookies=[{"name": "sessionid", "domain": ".douyin.com", "expires": -1}],
        folder_ids=None,
        directory_only=False,
        expected_account_id=None,
        on_checkpoint=checkpoint,
    )

    assert [ids for ids in checkpoints if ids] == [[WORK_ID], [WORK_ID, SECOND_WORK_ID]]
    assert result.complete is True
    assert page.scroll_count == 1
    assert page.checkpoint_count > page.scroll_count


@pytest.mark.asyncio
async def test_inventory_is_incomplete_when_listing_idles_without_terminal(tmp_path: Path) -> None:
    adapter = DouyinFavoritesAdapter(MediaSettings(), tmp_path / "profile")
    page = SyntheticFavoritesPage(
        listings=[
            {
                "records": [
                    {
                        "scope": "favorites-list",
                        "href": f"/video/{WORK_ID}",
                    }
                ],
                "terminal": False,
            }
        ]
    )

    result = await adapter._inventory_page(
        page,
        cookies=[{"name": "sessionid", "domain": ".douyin.com", "expires": -1}],
        folder_ids=None,
        directory_only=False,
        expected_account_id=None,
        on_checkpoint=None,
    )

    assert result.complete is False
    assert any("分页结束" in warning for warning in result.warnings)


def test_protocol_relative_records_and_article_badge_are_not_importable_notes():
    works, warnings = _works_from_dom(
        [
            {
                "scope": "favorites-list",
                "href": "//www.douyin.com/note/123456",
                "source_kind": "article",
            },
            {"scope": "favorites-list", "href": "/note/234567"},
        ]
    )
    assert [(work.work_id, work.source_kind) for work in works] == [
        ("123456", "article"),
        ("234567", "image_note"),
    ]
    assert not warnings


def test_folder_catalog_never_extracts_ids_from_external_links():
    page = parse_favorite_page(
        raw_page(
            "directory",
            None,
            "0",
            {
                "status_code": 0,
                "has_more": 0,
                "collects_list": [
                    {"collects_name": "外部", "share_url": "https://evil.test/collection/123"}
                ],
            },
        )
    )
    assert not page.folders
    assert not page.valid


def api_directory(ids=("12",), *, counts=None, **extra):
    counts = counts or {}
    return {
        "status_code": 0,
        "has_more": 0,
        "cursor": 0,
        "total_number": len(ids),
        "collects_list": [
            {"collects_id_str": fid, "collects_name": "夹" + fid, "total_number": counts.get(fid)}
            for fid in ids
        ],
        **extra,
    }


def api_works(ids, **extra):
    return {
        "status_code": 0,
        "has_more": 0,
        "cursor": 0,
        "aweme_list": [{"aweme_id": str(wid), "video": {}} for wid in ids],
        **extra,
    }


async def scan(tmp_path, page, *, folder_ids=None, directory_only=False, checkpoint=None):
    return await DouyinFavoritesAdapter(MediaSettings(), tmp_path / "profile")._inventory_page(
        page,
        cookies=[{"name": "sessionid", "domain": ".douyin.com", "expires": -1}],
        folder_ids=folder_ids,
        directory_only=directory_only,
        expected_account_id=None,
        on_checkpoint=checkpoint,
    )


async def test_api_directory_does_not_require_empty_tabpanel(tmp_path):
    page = SyntheticFavoritesPage()
    page.visible_selectors = set()
    page.api_pages[("directory", None, "0")] = api_directory(("12", "13", "14", "15"))
    result = await scan(tmp_path, page, directory_only=True)
    assert [f.id for f in result.folders] == ["12", "13", "14", "15"]
    assert result.complete and result.folders_complete


async def test_directory_only_never_requests_folder_pages(tmp_path):
    page = SyntheticFavoritesPage()
    page.api_pages[("directory", None, "0")] = api_directory()
    result = await scan(tmp_path, page, directory_only=True)
    assert not result.works
    assert page.api_calls == [("directory", None, "0")]


async def test_selected_folders_merge_duplicate_memberships(tmp_path):
    page = SyntheticFavoritesPage()
    page.api_pages = {
        ("directory", None, "0"): api_directory(("12", "13")),
        ("folder", "12", "0"): api_works([1, 2]),
        ("folder", "13", "0"): api_works([2, 3]),
    }
    result = await scan(tmp_path, page, folder_ids=["12", "13"])
    assert [w.work_id for w in result.works] == ["1", "2", "3"]
    assert result.works[1].folder_ids == ["12", "13"]
    assert result.complete


async def test_unselected_prefetch_does_not_enter_inventory(tmp_path):
    page = SyntheticFavoritesPage()
    page.api_pages = {
        ("directory", None, "0"): api_directory(("12", "13")),
        ("folder", "12", "0"): api_works([1]),
        ("folder", "13", "0"): api_works([999]),
    }
    result = await scan(tmp_path, page, folder_ids=["12"])
    assert [w.work_id for w in result.works] == ["1"]
    assert ("folder", "13", "0") not in page.api_calls


async def test_all_favorites_keeps_ungrouped_dom_items(tmp_path):
    page = SyntheticFavoritesPage(
        listings=[{"records": [{"scope": "favorites-list", "href": "/video/1"}], "terminal": True}]
    )
    page.api_pages[("directory", None, "0")] = api_directory()
    result = await scan(tmp_path, page)
    assert result.works[0].folder_ids == []
    assert result.complete
    assert page.api_calls == [("directory", None, "0")]


@pytest.mark.parametrize(
    "failure", [ExternalToolError("接口异常"), {"status_code": 0, "has_more": 0}]
)
async def test_all_favorites_survives_directory_protocol_failure(tmp_path, failure):
    page = SyntheticFavoritesPage(
        listings=[{"records": [{"scope": "favorites-list", "href": "/video/1"}], "terminal": True}]
    )
    page.api_pages[("directory", None, "0")] = failure
    result = await scan(tmp_path, page)
    assert [w.work_id for w in result.works] == ["1"]
    assert result.complete
    assert not result.folders_complete
    assert result.warnings


async def test_api_inventory_checkpoints_before_next_page(tmp_path):
    page = SyntheticFavoritesPage()
    page.api_pages = {
        ("directory", None, "0"): api_directory(),
        ("folder", "12", "0"): api_works([1], has_more=1, cursor="2"),
        ("folder", "12", "2"): api_works([2]),
    }
    checkpoints = []

    async def checkpoint(result):
        checkpoints.append([w.work_id for w in result.works])
        if len(result.works) == 1:
            assert ("folder", "12", "2") not in page.api_calls

    result = await scan(tmp_path, page, folder_ids=["12"], checkpoint=checkpoint)
    assert [w.work_id for w in result.works] == ["1", "2"]
    assert ["1"] in checkpoints and ["1", "2"] in checkpoints


async def test_api_inventory_handles_more_than_5000_works(tmp_path):
    page = SyntheticFavoritesPage()
    page.api_pages[("directory", None, "0")] = api_directory(counts={"12": 5121})
    for cursor in range(0, 5121, 20):
        end = min(cursor + 20, 5121)
        page.api_pages[("folder", "12", str(cursor))] = api_works(
            range(cursor + 1, end + 1), has_more=int(end < 5121), cursor=str(end)
        )
    result = await scan(tmp_path, page, folder_ids=["12"])
    assert len(result.works) == 5121
    assert result.works[-1].work_id == "5121"
    assert result.complete


@pytest.mark.parametrize(
    "second",
    [
        api_works([2], has_more=1, cursor="2"),
        api_works([], has_more=1, cursor="2"),
        api_works([2], has_more=None),
        ExternalToolError("分页失败"),
    ],
)
async def test_bad_next_page_preserves_discovered_works(tmp_path, second):
    page = SyntheticFavoritesPage()
    page.api_pages = {
        ("directory", None, "0"): api_directory(),
        ("folder", "12", "0"): api_works([1], has_more=1, cursor="2"),
        ("folder", "12", "2"): second,
    }
    result = await scan(tmp_path, page, folder_ids=["12"])
    assert result.works[0].work_id == "1"
    assert not result.complete
    assert result.warnings
    assert len(page.api_calls) == 3


async def test_page_limit_preserves_works(tmp_path):
    page = SyntheticFavoritesPage()
    page.api_pages[("directory", None, "0")] = api_directory()
    for cursor in range(500):
        page.api_pages[("folder", "12", str(cursor))] = api_works(
            [cursor + 1], has_more=1, cursor=str(cursor + 1)
        )
    result = await scan(tmp_path, page, folder_ids=["12"])
    assert len(result.works) == 500
    assert not result.complete
    assert len(page.api_calls) == 501


async def test_reported_seven_observed_four_is_partial(tmp_path):
    page = SyntheticFavoritesPage()
    page.api_pages = {
        ("directory", None, "0"): api_directory(counts={"12": 7}),
        ("folder", "12", "0"): api_works([1, 2, 3, 4]),
    }
    result = await scan(tmp_path, page, folder_ids=["12"])
    assert len(result.works) == 4
    assert result.folders_complete
    assert not result.complete
    assert any("显示 7 条" in w and "读取 4 条" in w for w in result.warnings)


async def test_first_directory_failure_is_not_empty_success(tmp_path):
    page = SyntheticFavoritesPage()
    page.api_pages[("directory", None, "0")] = ExternalToolError("接口异常")
    with pytest.raises(ExternalToolError):
        await scan(tmp_path, page, directory_only=True)


async def test_authentication_failure_does_not_fallback_to_all_favorites(tmp_path):
    page = SyntheticFavoritesPage()
    page.api_pages[("directory", None, "0")] = BrowserAuthRequiredError("登录失效")
    with pytest.raises(BrowserAuthRequiredError):
        await scan(tmp_path, page)


async def test_account_change_after_response_prevents_checkpoint(tmp_path, monkeypatch):
    page = SyntheticFavoritesPage()
    page.api_pages[("directory", None, "0")] = api_directory()
    original = ScriptedApiClient.read_page

    async def change(self, **kwargs):
        result = await original(self, **kwargs)
        self.page.account_id = "another-account"
        return result

    monkeypatch.setattr(ScriptedApiClient, "read_page", change)
    checkpoints = []

    async def checkpoint(snapshot):
        checkpoints.append(snapshot)

    with pytest.raises(BrowserAuthRequiredError):
        await scan(tmp_path, page, directory_only=True, checkpoint=checkpoint)
    assert not checkpoints


async def test_checkpoint_lease_failure_is_not_a_partial_success(tmp_path):
    page = SyntheticFavoritesPage()
    page.api_pages[("directory", None, "0")] = api_directory()

    async def checkpoint(_):
        raise JobLeaseLostError("lease lost")

    with pytest.raises(JobLeaseLostError):
        await scan(tmp_path, page, directory_only=True, checkpoint=checkpoint)


@pytest.mark.parametrize("empty_list", [[], None])
async def test_empty_folder_page_with_advancing_cursor_keeps_later_accessible_works(
    tmp_path, empty_list
):
    page = SyntheticFavoritesPage()
    page.api_pages = {
        ("directory", None, "0"): api_directory(counts={"12": 7}),
        ("folder", "12", "0"): api_works([1], has_more=1, cursor="2"),
        ("folder", "12", "2"): {
            "status_code": 0,
            "aweme_list": empty_list,
            "has_more": 1,
            "cursor": "4",
        },
        ("folder", "12", "4"): api_works([2, 3], has_more=1, cursor="6"),
        ("folder", "12", "6"): api_works([4]),
    }
    result = await scan(tmp_path, page, folder_ids=["12"])
    assert [w.work_id for w in result.works] == ["1", "2", "3", "4"]
    assert not result.complete
    assert result.folders_complete


@pytest.mark.parametrize("status", [403, 429])
async def test_generic_page_http_failure_is_not_a_login_denial(tmp_path, monkeypatch, status):
    monkeypatch.setattr(FakeResponse, "status", status)
    page = SyntheticFavoritesPage(body="服务暂时拒绝访问")
    with pytest.raises(ExternalToolError, match="HTTP"):
        await scan(tmp_path, page, directory_only=True)
