
import sqlite3

import pytest

from douyin_wiki.errors import JobLeaseLostError
from douyin_wiki.models import JobStatus
from douyin_wiki.worker import Worker
from tests.support.gateway import complete_gateway_job


@pytest.mark.asyncio
async def test_lost_lease_cannot_publish_entry(service, monkeypatch):
    job = service.capture_douyin("https://v.douyin.com/uvHsRpXIn8s/")
    original = service.persist_entry_documents_and_bundle

    def steal(*args, **kwargs):
        with service.database.connect() as conn:
            conn.execute("UPDATE jobs SET lock_owner='replacement' WHERE id=?", (job.id,))
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "persist_entry_documents_and_bundle", steal)
    await Worker(service).run_once()
    with pytest.raises(JobLeaseLostError):
        await complete_gateway_job(service, job.id)
    assert service.database.list_entries() == []
    assert service.vault.load_entries() == []


@pytest.mark.asyncio
async def test_publication_holds_database_writer_lock(service, monkeypatch):
    service.capture_douyin("https://v.douyin.com/uvHsRpXIn8s/")
    original = service.write_entry_documents
    checked = False

    def publish(*args, **kwargs):
        nonlocal checked
        # Another connection cannot recover or claim the task after validation.
        conn = sqlite3.connect(service.database.path, timeout=0)
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                conn.execute("BEGIN IMMEDIATE")
        finally:
            conn.close()
        checked = True
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "write_entry_documents", publish)
    done = await Worker(service).run_once()
    if done.status == JobStatus.AWAITING_AGENT_ANALYSIS:
        done = await complete_gateway_job(service, done.id)
    assert checked
    assert done.status == JobStatus.COMPLETED
    assert len(service.database.list_entries()) == 1
