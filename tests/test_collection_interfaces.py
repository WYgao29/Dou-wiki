from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from douyin_wiki import cli, mcp_server
from douyin_wiki.models import JobStatus
from douyin_wiki.webapp.app import create_app
from tests.test_collection_flow import MIX, A, FakeCollectionAdapter, ready, snapshot


def test_cli_collection_is_disabled_without_creating_parent(service, monkeypatch):
    monkeypatch.setattr(cli, "_service", lambda _: service)
    result = CliRunner().invoke(
        cli.app, ["collection", "add", f"https://www.douyin.com/collection/{MIX}"]
    )
    assert result.exit_code == 2, result.output
    assert "capture_douyin_collection" in result.output
    assert service.database.list_jobs() == []
    assert service.downloader.calls == 0


async def test_mcp_collection_inventory_returns_all_by_default(service, monkeypatch):
    parent = await ready(service, ids=tuple(str(7000000000000000000 + n) for n in range(105)))
    monkeypatch.setattr(mcp_server, "_SERVICE", service)
    detail = mcp_server.get_collection_inventory(parent)
    assert len(detail["works"]) == 105
    assert detail["total"] == 105
    names = {tool.name for tool in await mcp_server.mcp.list_tools()}
    assert {
        "capture_douyin_collection",
        "get_collection_inventory",
        "set_collection_work_selection",
        "confirm_collection_import",
        "retry_collection_import",
        "get_collection",
        "list_collections",
        "list_collection_imports",
    } <= names
    mcp_server.set_collection_work_selection(parent, "selected", all_works=True)
    result = mcp_server.confirm_collection_import(parent)
    assert service.database.get_job(result["job_id"]).status == JobStatus.MONITORING


def test_web_collection_inventory_selection_and_queue(config, service):
    from douyin_wiki.adapters.share import DouyinShareResolver

    service.resolver = DouyinShareResolver()
    service.collections.adapter = FakeCollectionAdapter(snapshot((A,)))
    with TestClient(
        create_app(config, service=service, start_watcher=False),
        headers={"Origin": "http://testserver"},
    ) as client:
        parent = service.capture_douyin_collection(f"https://www.douyin.com/collection/{MIX}").id
        asyncio.run(service.process_claimed_job(service.database.get_job(parent)))
        assert client.get(f"/api/collection-imports/{parent}").json()["summary"]["selected"] == 0
        assert client.post(f"/api/collection-imports/{parent}/confirm", json={}).status_code == 409
        assert service.downloader.calls == 0
        assert (
            client.post(
                f"/api/collection-imports/{parent}/selection",
                json={"decision": "selected", "all_works": True},
            ).status_code
            == 200
        )
        assert client.post(f"/api/collection-imports/{parent}/confirm", json={}).status_code == 202
        assert client.post(f"/api/collection-imports/{parent}/confirm", json={}).status_code == 202
        assert len([j for j in service.database.list_jobs() if j.kind == "capture"]) == 1
        assert client.get(f"/api/collections/{MIX}").json()["profile"]["mix_id"] == MIX
        assert client.get("/api/collection-imports").json()[0]["job_id"] == parent
        page = client.get("/imports/collections")
        assert page.status_code == 200
        assert 'id="collection-source"' not in page.text
        assert 'id="collection-history"' in page.text
        assert "imports-collections.js" in page.text


def test_collection_web_errors_and_pagination(config, service):
    parent = asyncio.run(
        ready(service, ids=tuple(str(7000000000000000000 + n) for n in range(105)))
    )
    with TestClient(
        create_app(config, service=service, start_watcher=False),
        headers={"Origin": "http://testserver"},
    ) as client:
        assert (
            client.get(f"/api/collection-imports/{parent}?page=2&limit=50").json()["works"][0][
                "position"
            ]
            == 51
        )
        assert client.get(f"/api/collection-imports/{parent}?limit=1001").status_code == 422
        assert client.get("/api/collection-imports/missing").status_code == 404
        assert client.get("/api/collections/123").status_code == 404
        assert (
            client.post(
                "/api/collections/inventory",
                json={"source_text": f"https://www.douyin.com/video/{A}"},
            ).status_code
            == 410
        )
        conflict = client.post(
            f"/api/collection-imports/{parent}/selection",
            json={"decision": "selected", "all_works": True, "work_ids": [A]},
        )
        assert conflict.status_code == 422
        assert (
            client.post(
                f"/api/collection-imports/{parent}/selection",
                json={"decision": "selected", "work_ids": [A]},
            ).status_code
            == 409
        )
        assert service.downloader.calls == 0


@pytest.mark.parametrize(
    "command", ["inventory", "select", "confirm", "retry", "list", "show", "history"]
)
def test_cli_collection_commands_registered(command):
    result = CliRunner().invoke(cli.app, ["collection", command, "--help"])
    assert result.exit_code == 0
