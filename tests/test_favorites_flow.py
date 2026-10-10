"""Real Web-to-store integration with synthetic inventory; never downloads media."""

from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient

from douyin_wiki.adapters.favorites_api import RawFavoritePage, parse_favorite_page
from douyin_wiki.favorites_models import FavoriteInventory
from douyin_wiki.webapp.app import create_app
from tests.test_favorites import A, B, FakeFavoritesAdapter, snapshot


def test_web_inventory_to_selection_to_explicit_queue(config, service):
    service.favorites.adapter = FakeFavoritesAdapter(snapshot())
    with TestClient(
        create_app(config, service=service, start_watcher=False),
        headers={"Origin": "http://testserver"},
    ) as client:
        parent_id = service.favorites.start().id
        assert [j.kind for j in service.database.list_jobs()] == ["favorites_import"]
        asyncio.run(service.process_claimed_job(service.database.get_job(parent_id)))
        detail = client.get(f"/api/favorites/imports/{parent_id}").json()
        assert detail["summary"]["discovered"] == 3
        assert detail["summary"]["selected"] == 0
        assert detail["summary"]["unsupported"] == 1
        assert service.downloader.calls == 0
        client.post(f"/api/favorites/imports/{parent_id}/selection", json={"selected": True})
        result = client.post(f"/api/favorites/imports/{parent_id}/confirm", json={})
        assert result.status_code == 202
        client.post(f"/api/favorites/imports/{parent_id}/confirm", json={})
        children = [j for j in service.database.list_jobs() if j.kind == "capture"]
        assert len(children) == 1
        assert children[0].request.share_text == f"https://www.douyin.com/video/{A}"
        assert service.downloader.calls == 0
        assert client.get("/api/favorites/imports").json()[0]["job_id"] == parent_id


def test_web_unproven_inventory_requires_acceptance_and_rejects_unknown_selection(config, service):
    service.favorites.adapter = FakeFavoritesAdapter(snapshot(complete=False))
    with TestClient(
        create_app(config, service=service, start_watcher=False),
        headers={"Origin": "http://testserver"},
    ) as client:
        parent_id = service.favorites.start().id
        asyncio.run(service.process_claimed_job(service.database.get_job(parent_id)))
        result = client.post(
            f"/api/favorites/imports/{parent_id}/selection",
            json={"selected": True, "work_ids": [B]},
        )
        assert result.status_code == 409  # Images were not enabled.
        rejected = client.post(f"/api/favorites/imports/{parent_id}/confirm", json={})
        assert rejected.status_code == 409
        assert len(service.database.list_jobs()) == 1
        client.post(f"/api/favorites/imports/{parent_id}/selection", json={"selected": True})
        accepted = client.post(
            f"/api/favorites/imports/{parent_id}/confirm", json={"accept_partial": True}
        )
        assert accepted.status_code == 202
        assert len(service.database.list_jobs()) == 2
        assert service.downloader.calls == 0


def test_web_seven_reported_four_read_requires_explicit_partial_confirmation(config, service):
    directory = parse_favorite_page(
        RawFavoritePage(
            "directory",
            None,
            "0",
            20,
            200,
            json.dumps(
                {
                    "status_code": 0,
                    "has_more": 0,
                    "total_number": 1,
                    "collects_list": [
                        {"collects_id_str": "12", "collects_name": "测试夹", "total_number": 7}
                    ],
                }
            ).encode(),
        )
    )
    works = parse_favorite_page(
        RawFavoritePage(
            "folder",
            "12",
            "0",
            20,
            200,
            json.dumps(
                {
                    "status_code": 0,
                    "has_more": 0,
                    "aweme_list": [{"aweme_id": str(wid), "video": {}} for wid in range(101, 105)],
                }
            ).encode(),
        )
    )
    service.favorites.adapter = FakeFavoritesAdapter(
        FavoriteInventory(
            account_id="test-account",
            folders=directory.folders,
            works=works.works,
            complete=False,
            folders_complete=True,
            warnings=["收藏夹「测试夹」显示 7 条，实际读取 4 条，数量不一致"],
        )
    )
    with TestClient(
        create_app(config, service=service, start_watcher=False),
        headers={"Origin": "http://testserver"},
    ) as client:
        parent = service.favorites.start(folder_ids=["12"]).id
        asyncio.run(service.process_claimed_job(service.database.get_job(parent)))
        detail = client.get(f"/api/favorites/imports/{parent}").json()
        assert detail["folders"][0]["reported_count"] == 7
        assert detail["summary"]["discovered"] == 4
        client.post(f"/api/favorites/imports/{parent}/selection", json={"selected": True})
        assert not detail["complete"] and detail["folders_complete"]
        assert len(service.database.list_jobs()) == 1
        assert client.post(f"/api/favorites/imports/{parent}/confirm", json={}).status_code == 409
        assert len(service.database.list_jobs()) == 1
        assert (
            client.post(
                f"/api/favorites/imports/{parent}/confirm", json={"accept_partial": True}
            ).status_code
            == 202
        )
        client.post(f"/api/favorites/imports/{parent}/confirm", json={"accept_partial": True})
        children = [j for j in service.database.list_jobs() if j.kind == "capture"]
        assert len(children) == 4
        assert {j.request.share_text for j in children} == {
            "https://www.douyin.com/video/101",
            "https://www.douyin.com/video/102",
            "https://www.douyin.com/video/103",
            "https://www.douyin.com/video/104",
        }
        assert service.downloader.calls == 0
