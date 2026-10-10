"""Regression coverage for the fourteen full-code baseline findings."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from threading import Event, Thread

import pytest
import yaml

from douyin_wiki.errors import BrowserAuthRequiredError, EntryNotFoundError, JobStateError
from douyin_wiki.favorites_models import FavoriteInventory, FavoriteWork
from douyin_wiki.models import (
    AnalysisResult,
    CaptureRequest,
    CreatorInventoryWork,
    CreatorProfile,
    CreatorWorkDecision,
    InspirationInput,
    JobStatus,
    RetentionPolicy,
)
from douyin_wiki.time_utils import utc_now
from douyin_wiki.webapp.app import create_app
from douyin_wiki.worker import Worker
from tests.support.gateway import complete_gateway_job
from tests.support.web import _web_fixture, local_client
from tests.test_creator import DirectResolver
from tests.test_image_note import make_service


async def capture(service, work_id="7672717300746907078"):
    service.resolver = DirectResolver()
    service.capture_douyin(f"https://www.douyin.com/video/{work_id}")
    result = await Worker(service).run_once()
    if result.status == JobStatus.AWAITING_AGENT_ANALYSIS:
        result = await complete_gateway_job(service, result.id)
    assert result.status == JobStatus.COMPLETED, result.error_message
    return service.database.get_entry(result.result["entry_id"])


def creator_parent(service, size):
    profile = CreatorProfile(
        sec_uid="baseline-creator",
        nickname="基线测试",
        canonical_url="https://www.douyin.com/user/baseline-creator",
        original_url="https://www.douyin.com/user/baseline-creator",
    )
    creator = service.database.upsert_creator(
        profile, creator_id="dyc-baseline", folder_path="creators/baseline"
    )
    parent = service.capture_douyin_creator(profile.canonical_url)
    works = [
        CreatorInventoryWork(
            work_id=str(7672000000000000000 + index),
            title=f"作品 {index}",
            canonical_url=f"https://www.douyin.com/video/{7672000000000000000 + index}",
            original_url=f"https://www.douyin.com/video/{7672000000000000000 + index}",
        )
        for index in range(size)
    ]
    service.database.record_creator_inventory(
        parent.id, creator.id, works, complete=True, sync=False
    )
    service.database.update_job(
        parent.id, status=JobStatus.NEEDS_SELECTION, artifacts={"creator_id": creator.id}
    )
    service.database.set_creator_run_selection(parent.id, CreatorWorkDecision.SELECTED)
    service.refresh_creator_documents = lambda *args, **kwargs: None
    return parent


def inventory(account, work_ids):
    return FavoriteInventory(
        account_id=account,
        complete=True,
        folders_complete=True,
        works=[
            FavoriteWork(
                work_id=str(work_id), canonical_url=f"https://www.douyin.com/video/{work_id}"
            )
            for work_id in work_ids
        ],
    )


def scan(service, account, work_ids, *, mode="incremental"):
    parent = service.favorites.start(update_mode=mode)
    service.favorites.store.checkpoint(parent.id, inventory(account, work_ids))
    service.database.update_job(parent.id, status=JobStatus.NEEDS_SELECTION)
    return parent


async def test_video_reacquisition_preserves_latest_user_state(service, monkeypatch):
    entry = await capture(service)
    service.set_entry_favorite(entry.id, True)
    service.database.mark_media_removed(entry.id)
    original = service.downloader.download

    async def download(*args):
        service.add_inspiration(entry.id, InspirationInput(text="下载期间的新灵感"))
        return await original(*args)

    monkeypatch.setattr(service.downloader, "download", download)
    await capture(service)
    current = service.database.get_entry(entry.id)
    assert current.favorite and current.retention == RetentionPolicy.KEEP
    assert current.media_expires_at is None
    assert [item.text for item in current.inspirations] == ["下载期间的新灵感"]
    assert "下载期间的新灵感" in (service.config.vault_path / current.source_path).read_text()
    assert service.vault.load_entries()[0][0].favorite


async def test_image_reacquisition_preserves_concurrent_inspiration_and_favorite(
    tmp_path, monkeypatch
):
    service = make_service(tmp_path)
    service.capture_douyin("https://v.douyin.com/oH4K0gee_Ok/")
    first = await Worker(service).run_once()
    if first.status == JobStatus.AWAITING_AGENT_ANALYSIS:
        first = await complete_gateway_job(service, first.id)
    entry = service.database.get_entry(first.result["entry_id"])
    data = service.database.get_entry_data(entry.id)
    (service.config.vault_path / data["metadata"]["image_paths"][1]).unlink()
    original = service.image_note_downloader.download

    async def download(*args):
        service.set_entry_favorite(entry.id, True)
        service.add_inspiration(entry.id, InspirationInput(text="图文下载期间的新灵感"))
        return await original(*args)

    monkeypatch.setattr(service.image_note_downloader, "download", download)
    service.capture_douyin("https://v.douyin.com/oH4K0gee_Ok/")
    result = await Worker(service).run_once()
    if result.status == JobStatus.AWAITING_AGENT_ANALYSIS:
        result = await complete_gateway_job(service, result.id)
    assert result.status == JobStatus.COMPLETED
    current = service.database.get_entry(entry.id)
    assert current.favorite and current.retention == RetentionPolicy.KEEP
    assert [item.text for item in current.inspirations] == ["图文下载期间的新灵感"]


async def test_reacquisition_applies_latest_retention_to_media_cleanup(service, monkeypatch):
    entry = await capture(service)
    service.database.mark_media_removed(entry.id)
    original = service.downloader.download

    async def download(*args):
        current = service.database.get_entry(entry.id)
        service.database.upsert_entry(
            current.model_copy(
                update={"retention": RetentionPolicy.DISCARD, "media_expires_at": None}
            ),
            service.database.get_entry_data(entry.id),
        )
        return await original(*args)

    monkeypatch.setattr(service.downloader, "download", download)
    await capture(service)
    current = service.database.get_entry(entry.id)
    assert current.retention == RetentionPolicy.DISCARD
    assert current.media_status == "removed"
    assert not (service.config.vault_path / "raw" / "assets" / entry.video_id).exists()


async def test_reacquisition_does_not_resurrect_concurrently_deleted_entry(service, monkeypatch):
    entry = await capture(service)
    service.database.mark_media_removed(entry.id)
    original = service.downloader.download

    async def download(*args):
        service.trash_entry(entry.id, confirmed=True)
        return await original(*args)

    monkeypatch.setattr(service.downloader, "download", download)
    service.capture_douyin(entry.canonical_url)
    result = await Worker(service).run_once()
    assert result.status == JobStatus.FAILED
    with pytest.raises(EntryNotFoundError):
        service.database.get_entry(entry.id)
    assert not (service.config.vault_path / entry.source_path).exists()


@pytest.mark.parametrize("kind", ["audio", "ocr", "audio+ocr", "image_ocr", "post_text+image_ocr"])
def test_evidence_quote_must_match_its_locator(service, kind):
    image = kind in {"image_ocr", "post_text+image_ocr"}
    context = {
        "metadata": {
            "source_kind": "image_note" if image else "video",
            "image_paths": ["1.png", "2.png"],
            "duration_seconds": 60,
        },
        "transcript_corrected": [
            {"start_ms": 0, "end_ms": 1000, "text": "第一处内容"},
            {"start_ms": 40000, "end_ms": 41000, "text": "第二处参数为八"},
        ],
        "ocr": [
            {
                "timestamp_ms": None if image else 0,
                "image_index": 1 if image else None,
                "text": "第一处内容",
            },
            {
                "timestamp_ms": None if image else 40000,
                "image_index": 2 if image else None,
                "text": "第二处参数为八",
            },
        ],
    }
    atom = {
        "id": "located",
        "statement": "参数为八",
        "provenance": kind,
        "quote": "第二处参数为八",
        **({"image_index": 1} if image else {"timestamp_ms": 0}),
    }
    invalid = AnalysisResult(title="测试", knowledge_atoms=[atom])
    with pytest.raises(JobStateError, match="指定时间戳/图片"):
        service.validate_analysis_evidence(invalid, context)
    atom.update({"image_index": 2} if image else {"timestamp_ms": 40000})
    service.validate_analysis_evidence(
        AnalysisResult(title="测试", knowledge_atoms=[atom]), context
    )


async def test_concurrent_reminder_confirmation_creates_one_system_reminder(service, monkeypatch):
    entry = await capture(service)
    entered, release = Event(), Event()
    created = []

    def create(*args, **kwargs):
        created.append(kwargs["idempotency_key"])
        entered.set()
        assert release.wait(5)
        return "system-1"

    monkeypatch.setattr(service.reminders, "create", create)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            service.confirm_reminder,
            entry.id,
            "reminder-1",
            confirmed=True,
            due_at="2026-10-02T09:00:00+08:00",
        )
        try:
            assert entered.wait(2)
            second = pool.submit(service.confirm_reminder, entry.id, "reminder-1", confirmed=True)
            assert second.result(timeout=2)["status"] == "creating"
            assert len(created) == 1
        finally:
            release.set()
        assert first.result(timeout=2)["system_id"] == "system-1"
    assert (
        service.confirm_reminder(entry.id, "reminder-1", confirmed=True)["system_id"] == "system-1"
    )
    assert len(created) == 1


async def test_stale_reminder_creation_can_be_reclaimed(service):
    entry = await capture(service)
    candidate, _, _ = service.database.get_reminder(entry.id, "reminder-1")
    service.database.claim_reminder_creation(entry.id, candidate.id, candidate)
    with service.database.connect() as conn:
        conn.execute(
            "UPDATE reminders SET updated_at=? WHERE entry_id=?",
            ((utc_now() - timedelta(minutes=11)).isoformat(), entry.id),
        )
    result = service.confirm_reminder(
        entry.id, candidate.id, confirmed=True, due_at="2026-10-02T09:00:00+08:00"
    )
    assert result["status"] == "created"
    assert len(service.reminders.created) == 1


async def test_adding_inspiration_preserves_creating_reminder_and_documents(service):
    entry = await capture(service)
    candidate, _, _ = service.database.get_reminder(entry.id, "reminder-1")
    service.database.claim_reminder_creation(entry.id, candidate.id, candidate)
    result = service.add_inspiration(entry.id, InspirationInput(text="创建提醒期间的新灵感"))
    assert result.inspirations[0].text == "创建提醒期间的新灵感"
    assert service.database.get_reminder(entry.id, candidate.id)[1] == "creating"
    loaded, _ = service.vault.load_entries()[0]
    assert loaded.inspirations == result.inspirations


@pytest.mark.parametrize("phase", ["database", "log"])
@pytest.mark.parametrize("newlines", ["lf", "crlf"])
async def test_failed_inspiration_publication_rolls_back_all_documents_and_database(
    service, monkeypatch, phase, newlines
):
    entry = await capture(service)
    root = service.config.vault_path
    if newlines == "crlf":
        for path in root.rglob("*.md"):
            path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*.md")}
    if phase == "database":
        original = service.database._persist_entry_bundle_conn

        def fail(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError("injected database publication failure")

        monkeypatch.setattr(service.database, "_persist_entry_bundle_conn", fail)
    else:
        original = service.vault.append_log

        def fail(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError("injected log publication failure")

        monkeypatch.setattr(service.vault, "append_log", fail)
    with pytest.raises(OSError, match="injected"):
        service.add_inspiration(entry.id, InspirationInput(text="失败的灵感不能残留"))
    assert service.database.get_entry(entry.id).inspirations == []
    assert {path.relative_to(root): path.read_bytes() for path in root.rglob("*.md")} == before


async def test_reminder_completion_during_inspiration_publication_is_preserved(
    service, monkeypatch
):
    entry = await capture(service)
    original = service.prepare_entry_bundle
    completed = []

    def complete_reminder(*args, **kwargs):
        bundle = original(*args, **kwargs)
        completed.append(
            service.confirm_reminder(
                entry.id, "reminder-1", confirmed=True, due_at="2026-10-02T09:00:00+08:00"
            )
        )
        return bundle

    monkeypatch.setattr(service, "prepare_entry_bundle", complete_reminder)
    service.add_inspiration(entry.id, InspirationInput(text="提醒完成时的新灵感"))
    expected = {"id": "reminder-1", "status": "created", "system_id": completed[0]["system_id"]}
    assert service.database.get_entry_data(entry.id)["reminder_states"] == [expected]
    assert service.vault.load_entries()[0][1]["reminder_states"] == [expected]
    assert service.database.get_reminder(entry.id, "reminder-1")[1] == "created"


def test_creator_dispatch_includes_every_selected_work_above_5000(service):
    parent = creator_parent(service, 5001)
    result = service.confirm_creator_import(parent.id)
    assert result.result["selection"]["selected"] == 5001
    assert len(result.result["child_job_ids"]) == 5001
    assert all(work.last_job_id for work in service.database.list_creator_works("dyc-baseline"))


def test_creator_dispatch_failure_rolls_back_parent_children_and_work_links(service, monkeypatch):
    parent = creator_parent(service, 2)
    original = service.database._insert_job_conn
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        child_id = original(*args, **kwargs)
        calls += 1
        if calls == 2:
            raise OSError("injected dispatch interruption")
        return child_id

    monkeypatch.setattr(service.database, "_insert_job_conn", fail_second)
    with pytest.raises(OSError, match="injected"):
        service.confirm_creator_import(parent.id)
    assert service.database.get_job(parent.id).status == JobStatus.NEEDS_SELECTION
    assert service.database.list_jobs(kinds={"capture"}, limit=None) == []
    assert all(
        work.last_job_id is None for work in service.database.list_creator_works("dyc-baseline")
    )
    monkeypatch.setattr(service.database, "_insert_job_conn", original)
    assert len(service.confirm_creator_import(parent.id).result["child_job_ids"]) == 2


def test_legacy_unleased_dispatch_recovers_and_reuses_unattached_child(service):
    parent = creator_parent(service, 2)
    work = service.database.list_creator_inventory(parent.id)[0][0].work
    child = service.database.create_job(
        CaptureRequest(share_text=work.canonical_url),
        artifacts={
            "creator_context": {
                "id": "dyc-baseline",
                "parent_job_id": parent.id,
                "work_id": work.work_id,
            }
        },
    )
    service.database.update_job(parent.id, status=JobStatus.DISPATCHING)
    assert service.database.recover_expired_jobs() == 1
    assert service.database.get_job(parent.id).status == JobStatus.NEEDS_SELECTION
    dispatched = service.confirm_creator_import(parent.id)
    assert child.id in dispatched.result["child_job_ids"]
    assert len(service.database.list_jobs(kinds={"capture"}, limit=None)) == 2


async def test_rebuild_restores_forward_and_cyclic_relations(service):
    entries = [
        await capture(service, work_id)
        for work_id in ["7672717300746907078", "7672717300746907079"]
    ]
    for entry, target in [entries, entries[::-1]]:
        data = service.database.get_entry_data(entry.id)
        relation = {
            "target_entry_id": target.id,
            "target_title": target.title,
            "target_source_path": target.source_path,
            "relation_type": "semantic",
            "reason": "循环关联",
            "confidence": 0.9,
        }
        data["relations"] = [relation]
        service.database.persist_entry_bundle(entry, data, [], [relation], [])
        service.write_entry_documents(
            entry, data, action="test", log_summary="循环关联", commit_message=None
        )
    preview = service.rebuild_database_from_vault(apply=False)
    assert preview["entry_count"] == 2 and not preview["entry_errors"]
    service.rebuild_database_from_vault(apply=True)
    with service.database.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0] == 2


def test_cancelled_creator_history_does_not_block_rebuild(service):
    parent = service.capture_douyin_creator("https://www.douyin.com/user/baseline-creator")
    service.cancel_job(parent.id)
    service.rebuild_database_from_vault(apply=True)
    assert service.database.get_job(parent.id).status == JobStatus.CANCELLED


def test_favorites_new_counts_are_immutable_across_later_scans_and_history_deletion(service):
    first = scan(service, "account-A", [111, 222], mode="full")
    assert service.favorites.get(first.id)["summary"]["new"] == 2
    second = scan(service, "account-A", [111, 222, 333], mode="full")
    assert service.favorites.get(second.id, only_new=True)["total"] == 1
    scan(service, "account-A", [111, 222, 333, 444], mode="full")
    assert service.favorites.get(first.id, only_new=True)["total"] == 2
    assert service.favorites.get(second.id)["summary"]["new"] == 1
    service.cancel_job(first.id)
    service.delete_job(first.id)
    assert service.favorites.get(second.id)["summary"]["new"] == 1


def test_new_favorites_account_is_unbound_and_only_seeds_same_account(service):
    scan(service, "account-A", [111, 222], mode="full")
    second = service.favorites.start()
    assert service.favorites.store.load(second.id)["snapshot"].get("account_id") is None
    service.favorites.store.checkpoint(second.id, inventory("account-B", [333]))
    assert [row["work_id"] for row in service.favorites.store.rows(second.id)] == ["333"]
    with pytest.raises(BrowserAuthRequiredError):
        service.favorites.store.checkpoint(second.id, inventory("account-C", [444]))
    third = scan(service, "account-A", [555])
    assert {row["work_id"] for row in service.favorites.store.rows(third.id)} == {
        "111",
        "222",
        "555",
    }
    assert service.favorites.get(third.id)["summary"]["new"] == 1


def test_web_imports_all_501_previously_skipped_works(service):
    parent = creator_parent(service, 501)
    service.database.set_creator_run_selection(parent.id, CreatorWorkDecision.SKIPPED)
    ids = [work.work_id for work in service.database.list_creator_works("dyc-baseline")]
    with local_client(create_app(service.config, service=service, start_watcher=False)) as client:
        response = client.post("/api/creators/dyc-baseline/import-skipped", json={"work_ids": ids})
        assert response.status_code == 202, response.text
    assert len(service.database.list_jobs(kinds={"capture"}, limit=None)) == 501


@pytest.mark.parametrize(
    "tags",
    [
        "tags:\n  - |-\n    咖啡\n",
        "tags:\n  - >-\n    咖啡\n",
        "tags:\n  - &first 咖啡\nrelated: *first\n",
        "tags: &shared [咖啡]\nrelated: *shared\n",
    ],
)
def test_tag_rename_preserves_valid_yaml_and_external_anchor_values(tmp_path, tags):
    config, service = _web_fixture(tmp_path)
    path = config.vault_path / service.database.get_entry("dy-123").source_path
    before = "---\ntype: source\nvideo_id: '123'\n" + tags + "title: 测试文章\n---\n# 原文\n"
    original = yaml.safe_load(before.split("---\n", 2)[1])
    path.write_text(before)
    app = create_app(config, service=service, start_watcher=False)
    with local_client(app) as client:
        response = client.post("/api/tags/rename", json={"name": "咖啡", "target": "茶"})
        assert response.status_code == 200, response.text
        after = yaml.safe_load(path.read_text().split("---\n", 2)[1])
        assert after == {**original, "tags": ["茶"]}
        assert len(app.state.catalog.list_items()) == 1
        assert app.state.catalog.load_errors == []
        undone = client.post(
            "/api/tags/undo", json={"operation_id": response.json()["operation_id"]}
        )
        assert undone.status_code == 200 and path.read_text() == before


@pytest.mark.parametrize(
    "action",
    [
        "cancel",
        "delete",
        "trash",
        "favorite",
        "capture",
        "creator",
        "topic_create",
        "topic_note",
        "topic_get",
        "topic_list",
        "topic_sources",
    ],
)
def test_waiting_web_operations_leave_health_responsive(tmp_path, monkeypatch, action):
    config, service = _web_fixture(tmp_path)
    service.resolver = DirectResolver()
    job = service.database.create_job(CaptureRequest(share_text="https://www.douyin.com/video/456"))
    if action == "delete":
        service.database.update_job(job.id, status=JobStatus.CANCELLED)
    if action == "creator":
        job = creator_parent(service, 2)
    topic_id = None
    if action.startswith("topic_"):
        topic_id = service.create_topic("测试专题", ["dy-123"])["topic"]["id"]
        entry = service.database.get_entry("dy-123")
        service.database.upsert_entry(
            entry.model_copy(update={"updated_at": utc_now()}),
            service.database.get_entry_data(entry.id),
        )
    app = create_app(
        config, service=service,  start_watcher=False
    )
    acquired, attempted, release = Event(), Event(), Event()
    original = service.vault.entry_operations_locked

    def hold():
        with original():
            acquired.set()
            release.wait(5)

    @contextmanager
    def observe(**kwargs):
        if kwargs.get("blocking", True):
            attempted.set()
        with original(**kwargs) as locked:
            yield locked

    monkeypatch.setattr(service.vault, "entry_operations_locked", observe)
    methods = {
        "cancel": ("POST", f"/api/jobs/{job.id}/cancel", {}),
        "delete": ("DELETE", f"/api/jobs/{job.id}", {}),
        "trash": ("DELETE", "/api/articles/dy-123", {"confirmed": True}),
        "favorite": ("PUT", "/api/articles/dy-123/favorite", {"favorite": True}),
        "capture": ("POST", "/api/captures", {"share_text": "https://www.douyin.com/video/456"}),
        "creator": ("POST", f"/api/creator-imports/{job.id}/confirm", {}),
        "topic_create": ("POST", "/api/topics", {"title": "新专题", "entry_ids": ["dy-123"]}),
        "topic_note": (
            "POST",
            f"/api/topics/{topic_id}/notes",
            {"content": "原始笔记", "confirmed": True},
        ),
        "topic_get": ("GET", f"/api/topics/{topic_id}", {}),
        "topic_list": ("GET", "/api/topics", {}),
        "topic_sources": (
            "PUT",
            f"/api/topics/{topic_id}/sources",
            {"sources": [{"entry_id": "dy-123", "enabled": True}]},
        ),
    }
    method, url, payload = methods[action]
    worker = Thread(target=hold)
    with local_client(app) as client, ThreadPoolExecutor(max_workers=2) as pool:
        worker.start()
        try:
            assert acquired.wait(1)
            write = pool.submit(client.request, method, url, json=payload)
            assert attempted.wait(1)
            health = pool.submit(client.get, "/api/system/health")
            assert health.result(timeout=1).status_code == 200
        finally:
            release.set()
            worker.join(3)
        result = write.result(timeout=3)
        assert result.status_code in {200, 201, 202}, result.text


def test_read_only_browsing_and_last_article_deletion_retain_tag_history(tmp_path):
    config, service = _web_fixture(tmp_path)
    app = create_app(config, service=service, start_watcher=False)
    with service.database.connect() as conn:
        before = [tuple(row) for row in conn.execute("SELECT * FROM tags ORDER BY name")]
    with local_client(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/library").status_code == 200
        with service.database.connect() as conn:
            assert [
                tuple(row) for row in conn.execute("SELECT * FROM tags ORDER BY name")
            ] == before
        assert (
            client.request("DELETE", "/api/articles/dy-123", json={"confirmed": True}).status_code
            == 200
        )
        result = client.get("/api/tags").json()
        coffee = next(row for row in result["tags"] if row["name"] == "咖啡")
        assert coffee["count"] == 0 and coffee["last_used_at"]
        assert result["sidebar"] == []


def test_existing_entries_backfill_tag_history_during_initialization(tmp_path):
    _, service = _web_fixture(tmp_path)
    with service.database.connect() as conn:
        conn.execute("DELETE FROM tags")
    service.database.initialize()
    with service.database.connect() as conn:
        row = conn.execute("SELECT * FROM tags WHERE name='咖啡'").fetchone()
    assert row and row["last_used_at"]
