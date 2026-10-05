"""Regression checks for legacy choices and stable read-only inventory baselines."""

import json
from contextlib import contextmanager

from tests.test_favorites import A, B
from tests.test_favorites_ux import read_inventory


def legacy_choices(service, job_id):
    """Emulate defaults persisted before explicit choice provenance was recorded."""
    with service.database.connect() as conn:
        conn.execute("UPDATE favorites_items SET selected=1 WHERE parent_id=?", (job_id,))


def forget_baseline(service, job_id):
    snapshot = service.favorites.store.load(job_id)["snapshot"]
    snapshot.pop("_baseline_work_ids", None)
    with service.database.connect() as conn:
        conn.execute(
            "UPDATE favorites_runs SET snapshot_json=? WHERE parent_id=?",
            (json.dumps(snapshot), job_id),
        )


async def test_incremental_does_not_inherit_unconfirmed_legacy_defaults(service):
    first = await read_inventory(service)
    legacy_choices(service, first)
    service.cancel_job(first)
    second = await read_inventory(service)
    data = service.favorites.get(second)
    assert data["summary"]["selected"] == 0
    assert service.favorites.get(first)["summary"]["selected"] == 2
    assert data["summary"]["new"] == 0
    assert data["selection_inheritance"]["ignored_legacy"] == 2


async def test_a_manual_choice_does_not_authorize_other_legacy_defaults(service):
    first = await read_inventory(service)
    legacy_choices(service, first)
    service.favorites.select(first, selected=True, work_ids=[A])
    service.cancel_job(first)
    second = await read_inventory(service)
    data = service.favorites.get(second)
    assert {i["work_id"] for i in data["items"] if i["selected"]} == {A}
    assert data["selection_inheritance"]["selected"] == 1
    assert data["selection_inheritance"]["ignored_legacy"] == 1


async def test_confirmed_legacy_choices_can_be_inherited(service):
    first = await read_inventory(service)
    legacy_choices(service, first)
    service.favorites.confirm(first)
    second = await read_inventory(service)
    assert {i["work_id"] for i in service.favorites.get(second)["items"] if i["selected"]} == {
        A,
        B,
    }


async def test_initialize_freezes_legacy_baseline_before_older_history_is_deleted(service):
    first = await read_inventory(service)
    second = await read_inventory(service)
    forget_baseline(service, second)
    service.database.initialize()
    assert service.favorites.store.load(second)["snapshot"].get("_baseline_work_ids") == [A, B]
    with service.database.connect() as conn:
        conn.execute("DELETE FROM favorites_runs WHERE parent_id=?", (first,))
    assert service.favorites.get(second)["summary"]["new"] == 0


async def test_legacy_inventory_detail_does_not_write_or_take_a_write_lock(service, monkeypatch):
    await read_inventory(service)
    second = await read_inventory(service)
    forget_baseline(service, second)
    statements = []
    original_connect = service.database.connect

    @contextmanager
    def traced_connect():
        with original_connect() as conn:
            conn.set_trace_callback(statements.append)
            yield conn

    monkeypatch.setattr(service.database, "connect", traced_connect)
    assert service.favorites.get(second)["summary"]["new"] == 0
    assert not any(
        sql.upper().startswith(("UPDATE FAVORITES_RUNS", "BEGIN IMMEDIATE")) for sql in statements
    )


async def test_schema_upgrade_preserves_legacy_choices_without_trusting_them(service):
    first = await read_inventory(service)
    legacy_choices(service, first)
    with service.database.connect() as conn:
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(favorites_items)")}
        if "selection_explicit" in columns:
            conn.execute("ALTER TABLE favorites_items DROP COLUMN selection_explicit")
        conn.execute("PRAGMA user_version=13")
    service.database.initialize()
    assert service.favorites.get(first)["summary"]["selected"] == 2
    second = await read_inventory(service)
    assert service.favorites.get(second)["summary"]["selected"] == 0


async def test_inheritance_notice_does_not_count_work_already_in_the_library(tmp_path):
    from tests.support.web import _web_fixture
    from tests.test_favorites import FakeFavoritesAdapter, snapshot

    _, service = _web_fixture(tmp_path)
    service.favorites.adapter = FakeFavoritesAdapter(
        snapshot(kinds=[("123", "video"), (A, "video")])
    )
    first = service.favorites.start()
    await service.process_claimed_job(first)
    legacy_choices(service, first.id)
    assert service.favorites.get(first.id)["summary"]["selected"] == 1
    second = service.favorites.start()
    await service.process_claimed_job(second)
    assert service.favorites.get(second.id)["selection_inheritance"]["ignored_legacy"] == 1
