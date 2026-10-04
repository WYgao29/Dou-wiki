from __future__ import annotations

import importlib
import json

import pytest

from douyin_wiki.errors import BrowserAuthRequiredError, ExternalToolError


def parse(payload, *, resource="directory", folder_id=None, status=200):
    # Missing production module is an explicit failing behavior, not an environment error.
    try:
        api = importlib.import_module("douyin_wiki.adapters.favorites_api")
    except ModuleNotFoundError:
        pytest.fail("favorites API parser has not been implemented")
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return api.parse_favorite_page(api.RawFavoritePage(resource, folder_id, "0", 20, status, body))


def directory(rows, **extra):
    return {"status_code": 0, "has_more": 0, "cursor": 0, "collects_list": rows, **extra}


def folder(rows, **extra):
    return {"status_code": 0, "has_more": 0, "cursor": 0, "aweme_list": rows, **extra}


def test_folder_id_preserves_int64_precision():
    page = parse(
        directory(
            [
                {
                    "collects_id": 7000000000000000123,
                    "collects_id_str": "7000000000000000123",
                    "collects_name": "测试收藏夹",
                    "total_number": 7,
                }
            ]
        )
    )
    assert page.valid
    assert page.folders[0].id == "7000000000000000123"
    assert page.folders[0].reported_count == 7


@pytest.mark.parametrize("value", [True, 7.0, "1e10", "１２３", "name", "1" * 33])
def test_invalid_id_does_not_create_a_folder(value):
    page = parse(directory([{"collects_id": value, "collects_name": "测试"}]))
    assert not page.valid
    assert not page.folders
    assert page.warnings


def test_conflicting_ids_are_not_trusted():
    page = parse(
        directory([{"collects_id": 7000000000000000123, "collects_id_str": "7000000000000000000"}])
    )
    assert not page.valid
    assert not page.folders


def test_empty_is_distinct_from_missing_or_null_list():
    assert parse(directory([])).valid
    for payload in ({"status_code": 0, "has_more": 0}, directory(None)):
        assert not parse(payload).valid


@pytest.mark.parametrize(
    "value, expected",
    [
        (False, False),
        (0, False),
        ("0", False),
        (True, True),
        (1, True),
        ("1", True),
        (None, None),
        (2, None),
        ("false", None),
    ],
)
def test_has_more_requires_explicit_supported_value(value, expected):
    page = parse(directory([], has_more=value))
    assert page.has_more is expected
    assert page.valid is (expected is not None)


def test_folder_total_is_not_work_total():
    page = parse(directory([{"collects_id_str": "12", "total_number": 7}], total_number=4))
    assert page.reported_count == 4
    assert page.folders[0].reported_count == 7


def test_supported_work_shapes_keep_exact_ids_and_membership():
    page = parse(
        folder(
            [
                {
                    "aweme_id": "7000000000000000123",
                    "video": {"play_addr": {}},
                    "desc": "测试视频",
                    "author": {"nickname": "作者"},
                },
                {"aweme_id": "23", "images": [{"url_list": ["https://example.test/image"]}]},
                {"aweme_id": "24", "image_post_info": {"images": [{}]}},
                {"aweme_id": "25", "share_url": "https://www.douyin.com/article/25"},
            ]
        ),
        resource="folder",
        folder_id="12",
    )
    assert page.valid
    assert [w.source_kind for w in page.works] == ["video", "image_note", "image_note", "article"]
    assert [w.canonical_url for w in page.works] == [
        "https://www.douyin.com/video/7000000000000000123",
        "https://www.douyin.com/note/23",
        "https://www.douyin.com/note/24",
        "https://www.douyin.com/article/25",
    ]
    assert page.works[0].author == "作者"
    assert all(w.folder_ids == ["12"] for w in page.works)


def test_unknown_work_kind_remains_unavailable():
    page = parse(folder([{"aweme_id": "23", "aweme_type": 999}]), resource="folder", folder_id="12")
    assert not page.valid
    assert page.works[0].work_id == "23"
    assert not page.works[0].available
    assert page.warnings


def test_invalid_record_keeps_valid_records_without_claiming_completion():
    page = parse(
        folder([{"aweme_id": "23", "video": {}}, {"aweme_id": False}]),
        resource="folder",
        folder_id="12",
    )
    assert not page.valid
    assert [w.work_id for w in page.works] == ["23"]


def test_explicit_login_denial_requires_authentication():
    with pytest.raises(BrowserAuthRequiredError):
        parse({"status_code": 8, "status_msg": "请先登录"})


@pytest.mark.parametrize(
    "payload,status", [(b"not json", 200), (b"[]", 200), (b"{}", 403), (b"{}", 503)]
)
def test_invalid_response_is_not_a_successful_empty_page(payload, status):
    with pytest.raises(ExternalToolError):
        parse(payload, status=status)


@pytest.mark.parametrize("code", [None, False, 1, "0"])
def test_status_requires_explicit_integer_success(code):
    assert not parse(directory([], status_code=code)).valid


def test_nested_collects_info_and_opaque_max_cursor():
    page = parse(
        directory(
            [{"collects_info": {"collects_id": 12, "collects_name": "测试"}}],
            cursor=None,
            max_cursor="page:next",
            has_more=True,
        )
    )
    assert page.folders[0].id == "12"
    assert page.next_cursor == "page:next"


def test_rejected_response_does_not_supply_trusted_pagination():
    page = parse(directory([], status_code=1001, has_more=1, cursor="2"))
    assert not page.valid
    assert page.has_more is None
    assert page.next_cursor is None
