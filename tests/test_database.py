from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from douyin_wiki.database import Database
from douyin_wiki.errors import JobLeaseLostError
from douyin_wiki.models import (
    CaptureRequest,
    CreatorRecord,
    CreatorWorkAvailability,
    CreatorWorkDecision,
    CreatorWorkRecord,
    EntryRecord,
    GatewayContext,
    InspirationInput,
    JobStatus,
    ResearchTopic,
    RetentionPolicy,
    SourceKind,
    TopicArtifact,
    TopicSource,
)


def test_empty_schema_has_no_chat_tables(tmp_path):
    database = Database(tmp_path / "fresh.sqlite3")
    database.initialize()
    database.initialize()
    with database.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 16
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert not {"web_chat_sessions", "web_chat_messages"}.intersection(tables)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(topic_artifacts)")}
        assert not {"model", "prompt_tokens", "completion_tokens", "total_tokens"}.intersection(
            columns
        )


@pytest.mark.parametrize("version", [0, 1, 15, 17])
def test_legacy_state_is_rejected_without_migration(tmp_path, version):
    from douyin_wiki.errors import JobStateError

    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE legacy_business(id TEXT PRIMARY KEY, payload TEXT)")
        conn.execute("INSERT INTO legacy_business VALUES ('old', '需要保留的旧数据')")
        conn.execute(f"PRAGMA user_version={version}")
        before = list(conn.iterdump())
    with pytest.raises(JobStateError, match="状态库"):
        Database(path).initialize()
    with sqlite3.connect(path) as conn:
        assert list(conn.iterdump()) == before
        assert conn.execute("PRAGMA user_version").fetchone()[0] == version


def _initialize_fresh_gateway_database(path, start, results, share_text):
    assert start.wait(timeout=10)
    database = Database(Path(path))
    database.initialize()
    job = database.create_job(CaptureRequest(share_text=share_text))
    results.put(job.id)


def test_concurrent_fresh_initialization_keeps_schema_16(tmp_path):
    import multiprocessing

    context = multiprocessing.get_context("spawn")
    start, results = context.Event(), context.Queue()
    path = tmp_path / "concurrent.sqlite3"
    processes = [
        context.Process(
            target=_initialize_fresh_gateway_database,
            args=(str(path), start, results, f"https://www.douyin.com/video/{index}"),
        )
        for index in (1, 2)
    ]
    try:
        for process in processes:
            process.start()
        start.set()
        for process in processes:
            process.join(timeout=20)
            assert process.exitcode == 0
        job_ids = {results.get(timeout=2) for _ in processes}
        assert len(job_ids) == 2
        database = Database(path)
        assert {job.id for job in database.list_jobs()} == job_ids
        with database.connect() as conn:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 16
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        results.close()


def test_queue_claim_and_recovery(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()
    job = database.create_job(
        CaptureRequest(
            share_text="https://v.douyin.com/test/",
            inspirations=[InspirationInput(text="测试")],
        )
    )
    claimed = database.claim_next_job()
    assert claimed and claimed.id == job.id
    assert database.claim_next_job() is None
    assert database.recover_expired_jobs(datetime.now(UTC) + timedelta(minutes=10)) == 1
    assert database.claim_next_job() is not None


def test_replace_knowledge_cache_rolls_back_every_projection_on_restore_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()
    now = datetime.now(UTC)

    old_entry = EntryRecord(
        id="dy-111111111111",
        video_id="111111111111",
        title="旧资料",
        original_url="https://www.douyin.com/video/111111111111",
        canonical_url="https://www.douyin.com/video/111111111111",
        raw_path="raw/old.md",
        source_path="wiki/sources/old.md",
        status="active",
        media_status="present",
        retention=RetentionPolicy.KEEP,
        created_at=now,
        updated_at=now,
    )
    old_data = {
        "analysis": {
            "analysis_version": 2,
            "title": "旧资料",
            "one_liner": "旧摘要",
            "relevance_to_inspiration": "旧关联",
            "takeaways": ["旧要点"],
            "content_type": "other",
            "content_card": {"kind": "other"},
            "tags": [],
        }
    }
    database.persist_entry_bundle(
        old_entry,
        old_data,
        [{"id": "old-chunk", "kind": "summary", "text": "旧片段", "embedding": [1.0]}],
        [],
        [],
    )
    creator = CreatorRecord(
        id="dyc-old",
        sec_uid="old-sec-uid-123456",
        canonical_url="https://www.douyin.com/user/old-sec-uid-123456",
        original_url="https://v.douyin.com/old/",
        nickname="旧博主",
        folder_path="creators/旧博主",
        created_at=now,
        updated_at=now,
    )
    work = CreatorWorkRecord(
        creator_id=creator.id,
        work_id="111111111111",
        source_kind=SourceKind.VIDEO,
        canonical_url=old_entry.canonical_url,
        original_url=old_entry.original_url,
        title=old_entry.title,
        decision=CreatorWorkDecision.IMPORTED,
        availability=CreatorWorkAvailability.AVAILABLE,
        entry_id=old_entry.id,
        first_seen_at=now,
        last_seen_at=now,
    )
    database.restore_creator_bundle(creator, [work])
    topic = ResearchTopic(
        id="topic-old",
        title="旧专题",
        sources=[
            TopicSource(
                entry_id=old_entry.id,
                position=1,
                source_revision=now,
            )
        ],
        source_revision="old-revision",
        created_at=now,
        updated_at=now,
    )
    artifact = TopicArtifact(
        id="artifact-old",
        topic_id=topic.id,
        kind="note",
        title="旧成果",
        content_markdown="旧内容",
        source_revision="old-revision",
        created_at=now,
        updated_at=now,
        user_authored=True,
    )
    database.restore_topic_bundle(topic, [artifact])
    database.set_index_metadata("embedding_signature", "old-signature")
    job = database.create_job(
        CaptureRequest(
            share_text="https://www.douyin.com/video/111111111111",
            gateway_context=GatewayContext(gateway="test"),
        )
    )
    database.update_job(job.id, status=JobStatus.COMPLETED)
    database.create_creator_run_items(job.id, creator.id, [work.work_id])
    database.attach_creator_child_job(creator.id, work.work_id, job.id)
    database.record_maintenance("weekly", {"status": "保留"})

    with database.connect() as conn:
        before = {
            table: [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]
            for table in (
                "entries",
                "chunks",
                "creators",
                "creator_works",
                "research_topics",
                "topic_sources",
                "topic_artifacts",
                "index_metadata",
                "jobs",
                "job_events",
                "creator_run_items",
                "maintenance_runs",
            )
        }

    replacement = old_entry.model_copy(update={"id": "dy-222222222222", "video_id": "222222222222"})
    replacement_data = {**old_data, "analysis": {**old_data["analysis"], "title": "新资料"}}
    replacement_topic = topic.model_copy(
        update={
            "sources": [
                TopicSource(
                    entry_id=replacement.id,
                    position=1,
                    source_revision=now,
                )
            ]
        }
    )

    def fail_restore(*args, **kwargs):
        raise RuntimeError("restore failed")

    monkeypatch.setattr(database, "_restore_creator_bundle_conn", fail_restore)
    with pytest.raises(RuntimeError, match="restore failed"):
        database.replace_knowledge_cache(
            entries=[
                (
                    replacement,
                    replacement_data,
                    [{"id": "new-chunk", "kind": "summary", "text": "新片段", "embedding": [1.0]}],
                    [],
                    [],
                )
            ],
            creators=[(creator, [work])],
            topics=[(replacement_topic, [artifact])],
            embedding_signature="new-signature",
        )

    with database.connect() as conn:
        after = {
            table: [dict(row) for row in conn.execute(f"SELECT * FROM {table}")] for table in before
        }
    assert after == before

    monkeypatch.undo()
    database.replace_knowledge_cache(
        entries=[
            (
                replacement,
                replacement_data,
                [{"id": "new-chunk", "kind": "summary", "text": "新片段", "embedding": [1.0]}],
                [],
                [],
            )
        ],
        creators=[(creator, [work])],
        topics=[(replacement_topic, [artifact])],
        embedding_signature="new-signature",
    )
    with database.connect() as conn:
        preserved_after_success = {
            table: [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]
            for table in (
                "jobs",
                "job_events",
                "creator_run_items",
                "maintenance_runs",
            )
        }
    assert preserved_after_success == {table: before[table] for table in preserved_after_success}
    with database.connect() as conn:
        restored_work = conn.execute(
            "SELECT entry_id, last_job_id FROM creator_works WHERE creator_id=? AND work_id=?",
            (creator.id, work.work_id),
        ).fetchone()
    assert restored_work["entry_id"] is None
    assert restored_work["last_job_id"] == job.id


def test_list_jobs_accepts_none_limit_for_all_matching_jobs(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()
    for index in range(55):
        job = database.create_job(CaptureRequest(share_text=f"https://v.douyin.com/test-{index}/"))
        database.update_job(
            job.id,
            status=JobStatus.NEEDS_AUTH,
            result={"auth_scope": "video"},
        )

    jobs = database.list_jobs(status=JobStatus.NEEDS_AUTH, limit=None)

    assert len(jobs) == 55


def test_recovery_requeues_job_after_mid_stage_crash(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()
    job = database.create_job(CaptureRequest(share_text="https://v.douyin.com/test/"))
    assert database.claim_next_job(worker_id="worker-a", lease_seconds=60)
    database.update_job(job.id, status=JobStatus.DOWNLOADING)

    assert database.recover_expired_jobs(datetime.now(UTC) + timedelta(minutes=2)) == 1
    recovered = database.claim_next_job(worker_id="worker-b")
    assert recovered and recovered.id == job.id


def test_live_worker_lease_is_not_stolen(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()
    job = database.create_job(CaptureRequest(share_text="https://v.douyin.com/test/"))
    assert database.claim_next_job(worker_id="worker-a", lease_seconds=300)
    database.update_job(job.id, status=JobStatus.TRANSCRIBING)

    assert database.recover_expired_jobs(datetime.now(UTC)) == 0
    assert database.claim_next_job(worker_id="worker-b") is None


def test_legacy_purposes_payload_migrates_to_inspirations() -> None:
    request = CaptureRequest.model_validate(
        {
            "share_text": "https://v.douyin.com/test/",
            "purposes": [{"text": "旧字段内容"}],
        }
    )
    payload = request.model_dump(mode="json")
    assert request.inspirations[0].text == "旧字段内容"
    assert payload["inspirations"][0]["text"] == "旧字段内容"
    assert "purposes" not in payload


def test_entry_favorite_round_trips_through_database(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()
    now = datetime.now(UTC)
    entry = EntryRecord(
        id="dy-123456789012",
        video_id="123456789012",
        title="收藏测试",
        original_url="https://www.douyin.com/video/123456789012",
        canonical_url="https://www.douyin.com/video/123456789012",
        raw_path="raw/收藏测试.md",
        source_path="wiki/sources/收藏测试.md",
        status="active",
        media_status="present",
        retention=RetentionPolicy.KEEP,
        favorite=True,
        created_at=now,
        updated_at=now,
    )

    database.upsert_entry(entry, {})

    assert database.get_entry(entry.id).favorite is True


def test_expired_media_query_never_returns_favorite_entry(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()
    now = datetime.now(UTC)
    entry = EntryRecord(
        id="dy-inconsistent",
        video_id="inconsistent",
        title="收藏保护",
        original_url="https://example.com/inconsistent",
        canonical_url="https://example.com/inconsistent",
        raw_path="raw/inconsistent.md",
        source_path="wiki/sources/inconsistent.md",
        status="active",
        media_status="present",
        retention=RetentionPolicy.TEMPORARY,
        favorite=True,
        media_expires_at=now - timedelta(days=1),
        created_at=now,
        updated_at=now,
    )
    database.upsert_entry(entry, {})

    assert database.entries_with_expired_media(now) == []


def test_stale_worker_cannot_update_or_unlock_new_owner(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()
    job = database.create_job(CaptureRequest(share_text="https://v.douyin.com/test/"))
    assert database.claim_next_job(worker_id="worker-a", lease_seconds=30)
    assert database.recover_expired_jobs(datetime.now(UTC) + timedelta(minutes=1)) == 1
    claimed = database.claim_next_job(worker_id="worker-b", lease_seconds=300)
    assert claimed and claimed.id == job.id

    with database.claimed_job_updates(job.id, "worker-a"), pytest.raises(JobLeaseLostError):
        database.update_job(job.id, status=JobStatus.COMPLETED, unlock=True)

    current = database.get_job(job.id)
    assert current.status == JobStatus.QUEUED
    with database.connect() as connection:
        owner = connection.execute("SELECT lock_owner FROM jobs WHERE id=?", (job.id,)).fetchone()
    assert owner["lock_owner"] == "worker-b"


def test_not_imported_means_no_library_entry(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()
    now = datetime.now(UTC)

    def entry(entry_id: str, video_id: str) -> EntryRecord:
        return EntryRecord(
            id=entry_id,
            video_id=video_id,
            title=video_id,
            original_url=f"https://www.douyin.com/video/{video_id}",
            canonical_url=f"https://www.douyin.com/video/{video_id}",
            raw_path=f"raw/{video_id}.md",
            source_path=f"wiki/sources/{video_id}.md",
            status="active",
            media_status="present",
            retention=RetentionPolicy.KEEP,
            created_at=now,
            updated_at=now,
        )

    kept = entry("dy-kept", "1001")
    selected_with_entry = entry("dy-selected", "1002")
    database.upsert_entry(kept, {})
    database.upsert_entry(selected_with_entry, {})
    creator = CreatorRecord(
        id="dyc-filter",
        sec_uid="sec-filter",
        canonical_url="https://www.douyin.com/user/sec-filter",
        original_url="https://v.douyin.com/filter/",
        nickname="筛选博主",
        folder_path="creators/筛选博主",
        created_at=now,
        updated_at=now,
    )

    def work(
        work_id: str, decision: CreatorWorkDecision, entry_id: str | None
    ) -> CreatorWorkRecord:
        return CreatorWorkRecord(
            creator_id=creator.id,
            work_id=work_id,
            source_kind=SourceKind.VIDEO,
            canonical_url=f"https://www.douyin.com/video/{work_id}",
            original_url=f"https://www.douyin.com/video/{work_id}",
            title=work_id,
            decision=decision,
            availability=CreatorWorkAvailability.AVAILABLE,
            entry_id=entry_id,
            first_seen_at=now,
            last_seen_at=now,
        )

    database.restore_creator_bundle(
        creator,
        [
            work("1001", CreatorWorkDecision.IMPORTED, kept.id),
            work("1002", CreatorWorkDecision.SELECTED, selected_with_entry.id),
            work("1003", CreatorWorkDecision.SKIPPED, None),
            work("1004", CreatorWorkDecision.SELECTED, None),
            work("1005", CreatorWorkDecision.PENDING, None),
            work("1006", CreatorWorkDecision.IMPORTED, "dy-missing"),
        ],
    )
    job = database.create_job(
        CaptureRequest(share_text="https://www.douyin.com/user/sec-filter"),
        kind="creator_import",
    )
    database.create_creator_run_items(
        job.id, creator.id, ["1001", "1002", "1003", "1004", "1005", "1006"]
    )
    with database.connect() as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(
            """UPDATE creator_works SET entry_id=?
               WHERE creator_id=? AND work_id=?""",
            ("dy-dangling", creator.id, "1005"),
        )

    hidden, _ = database.list_creator_inventory(job.id, limit=20, not_imported=False)
    assert [item.work.work_id for item in hidden] == [
        "1001",
        "1002",
        "1003",
        "1004",
        "1005",
        "1006",
    ]
    items, total = database.list_creator_inventory(job.id, limit=20, not_imported=True)
    assert total == 4
    assert [item.work.work_id for item in items] == ["1003", "1004", "1005", "1006"]
    by_id = {item.work.work_id: item.work for item in items}
    assert by_id["1003"].decision == CreatorWorkDecision.SKIPPED
    assert by_id["1004"].decision == CreatorWorkDecision.SELECTED
    assert by_id["1005"].entry_id == "dy-dangling"
    assert by_id["1006"].decision == CreatorWorkDecision.IMPORTED
    assert by_id["1006"].entry_id is None
