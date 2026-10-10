from __future__ import annotations

import importlib
import importlib.util
import json

import pytest

from douyin_wiki.errors import BrowserAuthRequiredError, ExternalToolError

MIX = "7651028428363139126"
WORK = "7644758067783852901"


def parse(data, *, resource="list", status=200):
    name = "douyin_wiki.adapters.collection_api"
    assert importlib.util.find_spec(name) is not None, "collection raw JSON parser is missing"
    api = importlib.import_module(name)
    body = data if isinstance(data, bytes) else json.dumps(data).encode()
    return api.parse_collection_page(api.RawCollectionPage(resource, MIX, "0", 20, status, body))


def work(**changes):
    return {
        "aweme_id": WORK,
        "desc": "第一集",
        "aweme_type": 0,
        "video": {"duration": 210836, "play_addr": {"url_list": ["https://example.com/video"]}},
        "mix_info": {"mix_id": MIX, "statis": {"current_episode": 1}},
        "author": {"nickname": "小吕泽哥"},
        **changes,
    }


def page(rows, **changes):
    return {"status_code": 0, "has_more": 0, "cursor": 18, "aweme_list": rows, **changes}


def test_collection_json_preserves_large_integer_id():
    raw = b'{"status_code":0,"has_more":0,"cursor":1,"aweme_list":[{"aweme_id":7644758067783852901,"video":{"duration":1000}}]}'
    result = parse(raw)
    assert result.works[0].work_id == "7644758067783852901"
    assert result.works[0].duration_seconds == 1


@pytest.mark.parametrize("bad_id", [True, 7644758067783852901.0, "1e19", "１２３", "1" * 33])
def test_collection_invalid_id_is_not_published(bad_id):
    result = parse(page([work(aweme_id=bad_id)]))
    assert result.works == []
    assert result.warnings
    assert not result.valid


def test_collection_conflicting_id_fields_make_page_partial():
    result = parse(page([work(aweme_id_str="7644758067783852902")]))
    assert result.works == []
    assert not result.valid


def test_collection_contrary_membership_is_excluded():
    result = parse(page([work(mix_info={"mix_id": "7651028428363139127"})]))
    assert result.works == []
    assert result.warnings


@pytest.mark.parametrize("status", [403, 404, 500])
def test_collection_http_error_is_not_empty_success(status):
    with pytest.raises(ExternalToolError):
        parse(page([]), status=status)


@pytest.mark.parametrize("status", [True, 8, None])
def test_collection_nonzero_or_missing_success_status_rejects_records(status):
    with pytest.raises(ExternalToolError):
        parse(page([work()], status_code=status))


def test_collection_explicit_login_error_requires_auth():
    with pytest.raises(BrowserAuthRequiredError):
        parse({"status_code": 2483, "status_msg": "请重新登录"})


def test_collection_null_array_keeps_trusted_pagination_but_is_partial():
    result = parse(page(None, has_more=1, cursor=2))
    assert result.works == []
    assert result.has_more is True
    assert result.next_cursor == "2"
    assert not result.valid


def test_collection_missing_has_more_does_not_mean_finished():
    data = page([work()])
    del data["has_more"]
    result = parse(data)
    assert result.has_more is None
    assert not result.valid


def test_collection_episode_mapping_and_media_types():
    result = parse(
        page(
            [
                work(),
                work(
                    aweme_id="7646239420807358282",
                    images=[{"url_list": ["https://example.com/image"]}],
                ),
                work(aweme_id="7650097901481364778", video=None),
            ],
            item_id_to_episode='{"7644758067783852901":1,"7646239420807358282":2}',
        )
    )
    assert [w.source_kind for w in result.works] == ["video", "image_note", "unknown"]
    assert [w.episode_label for w in result.works[:2]] == ["1", "2"]
    assert result.works[2].available is False
    assert result.works[0].canonical_url == "https://www.douyin.com/video/7644758067783852901"
    assert result.works[1].canonical_url == "https://www.douyin.com/note/7646239420807358282"


def test_updated_episode_is_not_a_reported_work_count():
    result = parse(
        {
            "status_code": 0,
            "mix_info": {
                "mix_id": MIX,
                "mix_name": "项目经理故事汇",
                "author": {"nickname": "小吕泽哥"},
                "statis": {"updated_to_episode": 18, "play_vv": 123456},
            },
        },
        resource="detail",
    )
    assert result.profile.mix_id == MIX
    assert result.profile.name == "项目经理故事汇"
    assert result.profile.reported_count is None
    assert result.profile.updated_to_episode == 18


def test_collection_detail_must_match_requested_mix():
    with pytest.raises(ExternalToolError):
        parse({"status_code": 0, "mix_info": {"mix_id": "7651028428363139127"}}, resource="detail")
