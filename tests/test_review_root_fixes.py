from __future__ import annotations

import sqlite3
import tomllib
from datetime import timedelta

import pytest

from douyin_wiki.errors import JobLeaseLostError
from douyin_wiki.models import JobStatus
from douyin_wiki.setup import update_config_values
from douyin_wiki.time_utils import utc_now
from tests.test_creator import FakeCreatorAdapter, inventory_result


@pytest.mark.parametrize(
    ("original", "updates", "expected"),
    [
        (
            '[llm]\nmodel = "local-model"',
            {"llm": {"enabled": True}},
            {"llm": {"model": "local-model", "enabled": True}},
        ),
        ("[llm]", {"llm": {"enabled": True}}, {"llm": {"enabled": True}}),
        ("# user comment", {None: {"analysis_mode": "local"}}, {"analysis_mode": "local"}),
        (
            'analysis_mode = "local"',
            {None: {"worker_concurrency": 2}},
            {"analysis_mode": "local", "worker_concurrency": 2},
        ),
    ],
)
def test_config_patch_accepts_valid_toml_without_final_newline(
    tmp_path, original, updates, expected
):
    path = tmp_path / "config.toml"
    path.write_text(original, encoding="utf-8")
    update_config_values(path, updates)
    assert tomllib.loads(path.read_text(encoding="utf-8")) == expected
    if original.startswith("#"):
        assert path.read_text(encoding="utf-8").startswith("# user comment\n")


def _claimed_creator_job(service):
    service.creator_adapter = FakeCreatorAdapter(inventory_result("7676706580084141163"))
    job = service.capture_douyin_creator("https://www.douyin.com/user/review")
    claimed = service.database.claim_next_job(worker_id="old-worker", lease_seconds=180)
    assert claimed.id == job.id
    return claimed


@pytest.mark.asyncio
async def test_stale_creator_worker_cannot_publish_profile_inventory_or_media(service, monkeypatch):
    claimed = _claimed_creator_job(service)
    original_update = service.database.update_job

    def update_then_transfer(job_id, **kwargs):
        updated = original_update(job_id, **kwargs)
        if kwargs.get("status") == JobStatus.INVENTORYING:
            with service.database.connect() as conn:
                conn.execute("UPDATE jobs SET lock_owner=? WHERE id=?", ("new-worker", job_id))
        return updated

    monkeypatch.setattr(service.database, "update_job", update_then_transfer)
    with (
        service.database.claimed_job_updates(claimed.id, "old-worker"),
        pytest.raises(JobLeaseLostError),
    ):
        await service.process_creator_import(claimed)
    assert service.database.list_creators() == []
    assert service.database.creator_inventory_summary(claimed.id)["total"] == 0
    assert not list((service.config.vault_path / "creators").rglob("*.jpg"))
    assert not list((service.config.vault_path / "creators").rglob("*.md"))
    with service.database.connect() as conn:
        row = conn.execute("SELECT lock_owner FROM jobs WHERE id=?", (claimed.id,)).fetchone()
        assert row[0] == "new-worker"


@pytest.mark.asyncio
async def test_creator_media_publication_holds_writer_reservation(service, monkeypatch):
    claimed = _claimed_creator_job(service)
    original = service._persist_creator_previews
    blocked = False

    def check_reservation(inventory, folder):
        nonlocal blocked
        conn = sqlite3.connect(service.database.path, timeout=0)
        try:
            try:
                conn.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError:
                blocked = True
            else:
                conn.rollback()
        finally:
            conn.close()
        return original(inventory, folder)

    monkeypatch.setattr(service, "_persist_creator_previews", check_reservation)
    with service.database.claimed_job_updates(claimed.id, "old-worker"):
        done = await service.process_creator_import(claimed)
    assert blocked, "lease replacement must not acquire a writer during media publication"
    assert done.status == JobStatus.NEEDS_SELECTION
    assert len(service.database.list_creators()) == 1
    assert service.database.creator_inventory_summary(claimed.id)["total"] == 1


@pytest.mark.asyncio
async def test_creator_inventory_sql_failure_rolls_back_profile_and_media(service):
    claimed = _claimed_creator_job(service)
    with service.database.connect() as conn:
        conn.execute(
            "CREATE TRIGGER fail_creator_work BEFORE INSERT ON creator_works "
            "BEGIN SELECT RAISE(ABORT, 'inventory rejected'); END"
        )
    with (
        service.database.claimed_job_updates(claimed.id, "old-worker"),
        pytest.raises(sqlite3.IntegrityError, match="inventory rejected"),
    ):
        await service.process_creator_import(claimed)
    assert service.database.list_creators() == []
    assert service.database.creator_inventory_summary(claimed.id)["total"] == 0
    assert not list((service.config.vault_path / "creators").rglob("*.jpg"))


@pytest.mark.asyncio
async def test_lease_expiring_during_creator_media_rolls_back_publication(service, monkeypatch):
    claimed = _claimed_creator_job(service)
    original = service._persist_creator_previews
    expired_time = utc_now() + timedelta(days=1)

    def finish_after_expiry(inventory, folder):
        works = original(inventory, folder)
        monkeypatch.setattr("douyin_wiki.database.utc_now", lambda: expired_time)
        return works

    monkeypatch.setattr(service, "_persist_creator_previews", finish_after_expiry)
    with (
        service.database.claimed_job_updates(claimed.id, "old-worker"),
        pytest.raises(JobLeaseLostError),
    ):
        await service.process_creator_import(claimed)
    assert service.database.list_creators() == []
    assert service.database.creator_inventory_summary(claimed.id)["total"] == 0
    assert not list((service.config.vault_path / "creators").rglob("*.jpg"))


@pytest.mark.asyncio
async def test_favorite_preserves_existing_projections_when_embedding_model_is_unavailable(service):
    from douyin_wiki.worker import Worker

    class LocalModel:
        failed = False

        def encode(self, texts, **_kwargs):
            if self.failed:
                raise RuntimeError("model encode unavailable")
            return [[1.0] + [0.0] * 127 for _ in texts]

    model = LocalModel()
    service.embeddings._model = model
    service.embeddings._dimensions = 128
    service.embeddings.provider_name = "sentence-transformers:review-model"
    service.capture_douyin("https://v.douyin.com/uvHsRpXIn8s/")
    completed = await Worker(service).run_once()
    entry_id = completed.result["entry_id"]
    before_chunks = service.database.fetch_chunks(entry_ids=[entry_id])
    before_relations = service.database.get_relations(entry_id)
    before_reminder = service.database.get_reminder(entry_id, "reminder-1")
    model.failed = True

    result = service.set_entry_favorite(entry_id, True)

    assert result["entry"].favorite is True
    assert service.database.get_entry(entry_id).favorite is True
    assert service.vault.load_entries()[0][0].favorite is True
    assert service.database.fetch_chunks(entry_ids=[entry_id]) == before_chunks
    assert service.database.get_relations(entry_id) == before_relations
    assert service.database.get_reminder(entry_id, "reminder-1") == before_reminder
