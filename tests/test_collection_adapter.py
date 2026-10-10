from __future__ import annotations

import importlib
import importlib.util
import json

import pytest

from douyin_wiki.adapters.collection_api import RawCollectionPage
from douyin_wiki.collection_models import CollectionProfile
from douyin_wiki.config import MediaSettings
from douyin_wiki.errors import BrowserAuthRequiredError, ExternalToolError, JobLeaseLostError

MIX = "7651028428363139126"


def adapter(tmp_path):
    name = "douyin_wiki.adapters.collection"
    assert importlib.util.find_spec(name), "collection inventory adapter is missing"
    return importlib.import_module(name).DouyinCollectionAdapter(
        MediaSettings(), tmp_path / "profile"
    )


def data(ids, cursor, more):
    return {
        "status_code": 0,
        "cursor": cursor,
        "has_more": more,
        "aweme_list": None
        if ids is None
        else [{"aweme_id": value, "video": {"duration": 1000}} for value in ids],
    }


class Pages:
    def __init__(self, values):
        self.values, self.calls = values, []

    async def read_page(self, *, resource, mix_id, cursor="0", count=20, **_):
        self.calls.append(cursor)
        value = self.values[cursor]
        if isinstance(value, BaseException):
            raise value
        return RawCollectionPage(resource, mix_id, cursor, count, 200, json.dumps(value).encode())


async def scan(subject, client, **kwargs):
    return await subject.inventory_with_client(
        client, profile=CollectionProfile(mix_id=MIX), account_id="session-fingerprint", **kwargs
    )


async def test_collection_pages_preserve_first_seen_order(tmp_path):
    client = Pages({"0": data(["30", "10"], 2, 1), "2": data(["10", "20"], 4, 0)})
    snapshots = []

    async def checkpoint(value):
        snapshots.append(value.model_dump())

    result = await scan(adapter(tmp_path), client, on_checkpoint=checkpoint)
    assert [w.work_id for w in result.works] == ["30", "10", "20"]
    assert [w.position for w in result.works] == [1, 2, 3]
    assert result.complete
    assert len(snapshots) == 2
    assert client.calls == ["0", "2"]


async def test_collection_null_page_advances_but_remains_partial(tmp_path):
    client = Pages({"0": data(["30"], 2, 1), "2": data(None, 4, 1), "4": data(["10"], 5, 0)})
    result = await scan(adapter(tmp_path), client)
    assert client.calls == ["0", "2", "4"]
    assert [w.work_id for w in result.works] == ["30", "10"]
    assert not result.complete
    assert result.warnings


async def test_collection_cursor_cycle_keeps_partial_inventory(tmp_path):
    result = await scan(adapter(tmp_path), Pages({"0": data(["30"], 0, 1)}))
    assert [w.work_id for w in result.works] == ["30"]
    assert not result.complete
    assert result.warnings


async def test_collection_midway_failure_preserves_seen_works(tmp_path):
    result = await scan(
        adapter(tmp_path), Pages({"0": data(["30"], 2, 1), "2": ExternalToolError("网络超时")})
    )
    assert [w.work_id for w in result.works] == ["30"]
    assert not result.complete


async def test_collection_first_page_failure_is_not_empty_success(tmp_path):
    with pytest.raises(ExternalToolError):
        await scan(adapter(tmp_path), Pages({"0": ExternalToolError("签名拒绝")}))


async def test_collection_checkpoint_lost_lease_stops_reading(tmp_path):
    client = Pages({"0": data(["30"], 2, 1), "2": data(["10"], 4, 0)})

    async def checkpoint(_):
        raise JobLeaseLostError("租约失效")

    with pytest.raises(JobLeaseLostError):
        await scan(adapter(tmp_path), client, on_checkpoint=checkpoint)
    assert client.calls == ["0"]


async def test_collection_reported_count_mismatch_is_partial(tmp_path):
    result = await adapter(tmp_path).inventory_with_client(
        Pages({"0": data(["30"], 1, 0)}),
        profile=CollectionProfile(mix_id=MIX, reported_count=2),
        account_id="session",
    )
    assert not result.complete
    assert result.warnings


async def test_collection_auth_failure_is_not_partial_network_error(tmp_path):
    with pytest.raises(BrowserAuthRequiredError):
        await scan(
            adapter(tmp_path),
            Pages({"0": data(["30"], 2, 1), "2": BrowserAuthRequiredError("账号变化")}),
        )
