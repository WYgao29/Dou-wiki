"""Storage regressions from the 2026-10-02 repository review."""
from __future__ import annotations

import copy
import json
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

from douyin_wiki.models import InspirationInput, JobStatus
from douyin_wiki.vault import VaultWriter
from douyin_wiki.worker import Worker
from tests.support.gateway import complete_gateway_job


async def _capture(service):
    service.capture_douyin("https://v.douyin.com/uvHsRpXIn8s/")
    job = await Worker(service).run_once()
    if job.status == JobStatus.AWAITING_AGENT_ANALYSIS:
        job = await complete_gateway_job(service, job.id)
    return service.database.get_entry(job.result["entry_id"])


def _documents(vault):
    return {path.relative_to(vault): path.read_bytes() for path in vault.rglob("*.md")}


def _abort_entry_update(service):
    with service.database.connect() as conn:
        conn.execute("""CREATE TRIGGER reject_entry_update BEFORE UPDATE ON entries
                        BEGIN SELECT RAISE(ABORT, 'publication rejected'); END""")


@pytest.mark.asyncio
async def test_favorite_sql_failure_preserves_all_documents(service):
    entry = await _capture(service)
    before = _documents(service.config.vault_path)
    _abort_entry_update(service)
    with pytest.raises(sqlite3.IntegrityError, match="publication rejected"):
        service.set_entry_favorite(entry.id, True)
    assert service.database.get_entry(entry.id).favorite is False
    assert _documents(service.config.vault_path) == before
    assert service.vault.load_entries()[0][0].favorite is False


@pytest.mark.asyncio
async def test_repair_sql_failure_rolls_back_generated_documents(service):
    entry = await _capture(service)
    source = service.config.vault_path / entry.source_path
    source.unlink()
    before = _documents(service.config.vault_path)
    _abort_entry_update(service)
    with pytest.raises(sqlite3.IntegrityError, match="publication rejected"):
        service._repair_entry_if_needed(entry)
    assert not source.exists()
    assert _documents(service.config.vault_path) == before
    assert service.database.get_entry(entry.id).id == entry.id


@pytest.mark.asyncio
async def test_note_and_source_change_preserve_latest_topic_on_disk(service, monkeypatch):
    first = await _capture(service)
    video_id = "7672717300746907079"
    second = first.model_copy(update={
        "id": f"dy-{video_id}", "video_id": video_id,
        "raw_path": first.raw_path.replace(first.video_id, video_id),
        "source_path": first.source_path.replace(first.video_id, video_id),
    })
    data = copy.deepcopy(service.database.get_entry_data(first.id))
    data["metadata"]["video_id"] = video_id
    chunks, relations, reminders = service.prepare_entry_bundle(second, data)
    service.persist_entry_documents_and_bundle(
        second, data, chunks, relations, reminders,
        action="test", log_summary="second source", commit_message="second source",
    )
    topic_id = service.create_topic("Sources", [first.id])["topic"]["id"]
    saved, release, changing, changed = Event(), Event(), Event(), Event()
    original = service.database.save_topic_artifact

    def pause_note(artifact):
        result = original(artifact)
        saved.set()
        assert release.wait(5)
        return result

    def change_sources():
        changing.set()
        result = service.set_topic_sources(topic_id, [{"entry_id": second.id, "enabled": True}])
        changed.set()
        return result

    monkeypatch.setattr(service.database, "save_topic_artifact", pause_note)
    with ThreadPoolExecutor(max_workers=2) as pool:
        note = pool.submit(service.save_topic_note, topic_id, "literal note", confirmed=True)
        try:
            assert saved.wait(2)
            selection = pool.submit(change_sources)
            assert changing.wait(2)
            assert not changed.wait(0.2)
        finally:
            release.set()
        note.result(timeout=3)
        selection.result(timeout=3)
    current_sources = service.database.get_topic(topic_id).sources
    assert [source.entry_id for source in current_sources] == [second.id]
    loaded_topic, artifacts = service.vault.load_topics()[0]
    assert [source.entry_id for source in loaded_topic.sources] == [second.id]
    assert artifacts[0].content_markdown == "literal note"
    service.rebuild_database_from_vault(apply=True)
    rebuilt_sources = service.database.get_topic(topic_id).sources
    assert [source.entry_id for source in rebuilt_sources] == [second.id]


@pytest.mark.asyncio
async def test_persisting_stale_topic_uses_current_source_selection(service):
    entry = await _capture(service)
    topic_id = service.create_topic("Sources", [entry.id])["topic"]["id"]
    stale = service.database.get_topic(topic_id)
    service.set_topic_sources(topic_id, [{"entry_id": entry.id, "enabled": False}])
    service.persist_topic(stale)
    loaded_topic, _ = service.vault.load_topics()[0]
    assert loaded_topic.sources[0].enabled is False


@pytest.mark.asyncio
async def test_topic_refresh_keeps_revision_from_concurrent_source_change(service, monkeypatch):
    entry = await _capture(service)
    topic_id = service.create_topic("Sources", [entry.id])["topic"]["id"]
    service.add_inspiration(entry.id, InspirationInput(text="updated after topic creation"))
    captured, release, changing, changed = Event(), Event(), Event(), Event()
    original_revision = service.topic_revision

    def pause_revision(entries):
        if entries[0][1]:
            captured.set()
            assert release.wait(5)
        return original_revision(entries)

    def change_sources():
        changing.set()
        result = service.set_topic_sources(topic_id, [{"entry_id": entry.id, "enabled": False}])
        changed.set()
        return result

    monkeypatch.setattr(service, "topic_revision", pause_revision)
    with ThreadPoolExecutor(max_workers=2) as pool:
        refresh = pool.submit(service.refresh_topic, topic_id)
        try:
            assert captured.wait(2)
            selection = pool.submit(change_sources)
            assert changing.wait(2)
            assert not changed.wait(0.2)
        finally:
            release.set()
        refresh.result(timeout=3)
        selection_result = selection.result(timeout=3)
    final_topic = service.database.get_topic(topic_id)
    assert final_topic.sources[0].enabled is False
    assert final_topic.source_revision == selection_result["topic"]["source_revision"]
    loaded_topic, _ = service.vault.load_topics()[0]
    assert loaded_topic.source_revision == final_topic.source_revision


@pytest.mark.asyncio
async def test_reminder_completion_and_delete_keep_system_id_in_restore(service, monkeypatch):
    entry = await _capture(service)
    creating, release, deleting, deleted = Event(), Event(), Event(), Event()

    def create(_candidate, **_kwargs):
        creating.set()
        assert release.wait(5)
        return "system-created-before-delete"

    def delete():
        deleting.set()
        result = service.trash_entry(entry.id, confirmed=True)
        deleted.set()
        return result

    monkeypatch.setattr(service.reminders, "create", create)
    with ThreadPoolExecutor(max_workers=2) as pool:
        confirmation = pool.submit(
            service.confirm_reminder, entry.id, "reminder-1", confirmed=True,
            due_at="2026-10-03T09:00:00+08:00",
        )
        try:
            assert creating.wait(2)
            deletion = pool.submit(delete)
            assert deleting.wait(2)
            assert not deleted.wait(0.2)
        finally:
            release.set()
        result = confirmation.result(timeout=3)
        trash = deletion.result(timeout=3)
    assert result["status"] == "created"
    assert result["system_id"] == "system-created-before-delete"
    service.restore_trashed_entry(trash["trash_id"], confirmed=True)
    assert service.database.get_reminder(entry.id, "reminder-1")[1:] == (
        "created", "system-created-before-delete",
    )
    loaded_state = service.vault.load_entries()[0][1]["reminder_states"][0]
    assert loaded_state["system_id"] == result["system_id"]


@pytest.mark.asyncio
async def test_restored_checkpoint_replays_navigation_before_journal_removal(service, monkeypatch):
    entry = await _capture(service)
    target = Path(entry.source_path).with_suffix("").as_posix()
    concept = next((service.config.vault_path / "wiki" / "concepts").glob("*.md"))
    trash = service.trash_entry(entry.id, confirmed=True)
    original = service._write_trash_manifest

    def crash_after_write(item_dir, manifest):
        original(item_dir, manifest)
        if manifest["phase"] == "restored":
            raise SystemExit("crash after durable restored checkpoint")

    monkeypatch.setattr(service, "_write_trash_manifest", crash_after_write)
    with pytest.raises(SystemExit):
        service.restore_trashed_entry(trash["trash_id"], confirmed=True)
    monkeypatch.setattr(service, "_write_trash_manifest", original)
    report = service.recover_entry_trash_operations()
    assert report["warnings"] == []
    assert trash["trash_id"] in report["completed"]
    assert target in (service.config.vault_path / "index.md").read_text()
    assert target in concept.read_text()
    assert not service._trash_item_dir(trash["trash_id"]).exists()


@pytest.mark.asyncio
async def test_restore_navigation_failure_retains_journal_until_success(service, monkeypatch):
    entry = await _capture(service)
    trash = service.trash_entry(entry.id, confirmed=True)
    original = service.vault.rebuild_index

    def broken_index(_entries):
        raise OSError("navigation unavailable")

    monkeypatch.setattr(service.vault, "rebuild_index", broken_index)
    result = service.restore_trashed_entry(trash["trash_id"], confirmed=True)
    item_dir = service._trash_item_dir(trash["trash_id"])
    assert result["warnings"]
    assert json.loads((item_dir / "manifest.json").read_text())["phase"] == "restored"
    report = service.recover_entry_trash_operations()
    assert report["warnings"]
    assert trash["trash_id"] not in report["completed"]
    assert item_dir.exists()
    monkeypatch.setattr(service.vault, "rebuild_index", original)
    report = service.recover_entry_trash_operations()
    assert report["warnings"] == []
    assert trash["trash_id"] in report["completed"]
    assert not item_dir.exists()
    assert entry.video_id in (service.config.vault_path / "index.md").read_text()


def _git(vault, *args):
    return subprocess.check_output(["git", *args], cwd=vault, text=True).strip()


def _git_vault(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Storage test")
    _git(tmp_path, "config", "user.email", "storage-test@example.invalid")
    return VaultWriter(tmp_path)


def _staged_patch(vault):
    return "\n".join(line for line in _git(vault, "diff", "--cached", "--unified=0").splitlines()
                     if not line.startswith("index "))


@pytest.mark.parametrize("target_staged", [False, True])
def test_git_commit_preserves_user_staging(tmp_path, target_staged):
    writer = _git_vault(tmp_path)
    note = tmp_path / "manual-note.md"
    generated = tmp_path / "index.md"
    base = "user base\n" + "\n" * 20 + "generated base\n"
    note.write_text("manual base\n")
    generated.write_text(base)
    _git(tmp_path, "add", "--", "manual-note.md", "index.md")
    _git(tmp_path, "commit", "-qm", "base")
    note.write_text("manual staged\n")
    if target_staged:
        generated.write_text(base.replace("user base", "user staged"))
    _git(tmp_path, "add", "--", "manual-note.md", "index.md")
    before = _staged_patch(tmp_path)
    generated.write_text(generated.read_text().replace("generated base", "generated new"))
    assert writer.commit([generated], "generated change") is True
    committed = _git(tmp_path, "show", "HEAD:index.md")
    assert "generated new" in committed
    assert "user staged" not in committed
    assert _git(tmp_path, "show", "HEAD:manual-note.md") == "manual base"
    assert _staged_patch(tmp_path) == before
    assert generated.read_text().endswith("generated new\n")


def test_git_commit_overlap_keeps_head_and_staging_unchanged(tmp_path):
    writer = _git_vault(tmp_path)
    generated = tmp_path / "index.md"
    generated.write_text("base\n")
    _git(tmp_path, "add", "--", "index.md")
    _git(tmp_path, "commit", "-qm", "base")
    generated.write_text("user staged\n")
    _git(tmp_path, "add", "--", "index.md")
    before_head, before_patch = _git(tmp_path, "rev-parse", "HEAD"), _staged_patch(tmp_path)
    generated.write_text("generated replacement\n")
    assert writer.commit([generated], "generated change") is False
    assert _git(tmp_path, "rev-parse", "HEAD") == before_head
    assert _staged_patch(tmp_path) == before_patch
    assert generated.read_text() == "generated replacement\n"


@pytest.mark.parametrize("existing_head", [False, True])
def test_git_index_publish_failure_restores_original_head(tmp_path, monkeypatch, existing_head):
    writer = _git_vault(tmp_path)
    note, generated = tmp_path / "manual-note.md", tmp_path / "index.md"
    note.write_text("manual base\n")
    generated.write_text("generated base\n")
    if existing_head:
        _git(tmp_path, "add", "--", "manual-note.md", "index.md")
        _git(tmp_path, "commit", "-qm", "base")
    note.write_text("manual staged\n")
    _git(tmp_path, "add", "--", "manual-note.md")
    original_index = (tmp_path / ".git" / "index").read_bytes()
    original_head = _git(tmp_path, "rev-parse", "HEAD") if existing_head else None
    generated.write_text("generated new\n")

    def fail_index_publish(_source, target):
        assert target == tmp_path / ".git" / "index"
        raise OSError("index replacement unavailable")

    monkeypatch.setattr("douyin_wiki.vault.os.replace", fail_index_publish)
    assert writer.commit([generated], "generated change") is False
    assert (tmp_path / ".git" / "index").read_bytes() == original_index
    if existing_head:
        assert _git(tmp_path, "rev-parse", "HEAD") == original_head
    else:
        head = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"], cwd=tmp_path, capture_output=True
        )
        assert head.returncode != 0
    assert generated.read_text() == "generated new\n"


@pytest.mark.parametrize("hook_rejects", [False, True])
def test_git_commit_runs_pre_commit_hook_and_preserves_user_stage(tmp_path, hook_rejects):
    writer = _git_vault(tmp_path)
    manual, generated = tmp_path / "manual.md", tmp_path / "index.md"
    manual.write_text("manual base\n")
    generated.write_text("generated base\n")
    _git(tmp_path, "add", "--", "manual.md", "index.md")
    _git(tmp_path, "commit", "-qm", "base")
    manual.write_text("manual staged\n")
    _git(tmp_path, "add", "--", "manual.md")
    generated.write_text("generated new\n")
    original_head, original_patch = _git(tmp_path, "rev-parse", "HEAD"), _staged_patch(tmp_path)
    hook = tmp_path / ".git" / "hooks" / "pre-commit"
    commands = "" if hook_rejects else (
        "printf 'hook addition\\n' > hook-added.md\ngit add -- hook-added.md\n"
    )
    hook.write_text(
        f"#!/bin/sh\ntouch .git/hook-ran\n{commands}exit {int(hook_rejects)}\n"
    )
    hook.chmod(0o755)

    assert writer.commit([generated], "generated change") is not hook_rejects
    assert (tmp_path / ".git" / "hook-ran").exists()
    assert _staged_patch(tmp_path) == original_patch
    assert _git(tmp_path, "show", "HEAD:manual.md") == "manual base"
    assert generated.read_text() == "generated new\n"
    if hook_rejects:
        assert _git(tmp_path, "rev-parse", "HEAD") == original_head
    else:
        assert _git(tmp_path, "show", "HEAD:index.md") == "generated new"
        assert _git(tmp_path, "show", "HEAD:hook-added.md") == "hook addition"


def test_git_signing_failure_preserves_head_and_original_index(tmp_path):
    writer = _git_vault(tmp_path)
    generated = tmp_path / "index.md"
    generated.write_text("base\n")
    _git(tmp_path, "add", "--", "index.md")
    _git(tmp_path, "commit", "-qm", "base")
    before_head = _git(tmp_path, "rev-parse", "HEAD")
    before_index = (tmp_path / ".git" / "index").read_bytes()
    generated.write_text("new\n")
    signer = tmp_path / ".git" / "fake-signer"
    signer.write_text("#!/bin/sh\ntouch .git/signing-ran\nexit 1\n")
    signer.chmod(0o755)
    _git(tmp_path, "config", "commit.gpgsign", "true")
    _git(tmp_path, "config", "gpg.program", str(signer))

    assert writer.commit([generated], "generated change") is False
    assert (tmp_path / ".git" / "signing-ran").exists()
    assert _git(tmp_path, "rev-parse", "HEAD") == before_head
    assert (tmp_path / ".git" / "index").read_bytes() == before_index
    assert generated.read_text() == "new\n"


def test_git_commit_keeps_post_commit_staging_with_original_user_stage(tmp_path):
    writer = _git_vault(tmp_path)
    manual, generated = tmp_path / "manual.md", tmp_path / "index.md"
    manual.write_text("manual base\n")
    generated.write_text("generated base\n")
    _git(tmp_path, "add", "--", "manual.md", "index.md")
    _git(tmp_path, "commit", "-qm", "base")
    manual.write_text("manual staged\n")
    _git(tmp_path, "add", "--", "manual.md")
    user_patch = _git(tmp_path, "diff", "--cached", "--", "manual.md")
    generated.write_text("generated new\n")
    hook = tmp_path / ".git" / "hooks" / "post-commit"
    hook.write_text(
        "#!/bin/sh\nprintf 'hook staged\\n' > hook-staged.md\ngit add -- hook-staged.md\n"
    )
    hook.chmod(0o755)

    assert writer.commit([generated], "generated change") is True
    assert _git(tmp_path, "show", ":hook-staged.md") == "hook staged"
    assert _git(tmp_path, "diff", "--cached", "--", "manual.md") == user_patch
    assert _git(tmp_path, "show", "HEAD:manual.md") == "manual base"
    assert _git(tmp_path, "show", "HEAD:index.md") == "generated new"
    assert _git(tmp_path, "diff", "--", "index.md") == ""


def test_git_hook_conflict_during_staging_rebase_rolls_back_commit(tmp_path):
    writer = _git_vault(tmp_path)
    manual, generated = tmp_path / "manual.md", tmp_path / "index.md"
    manual.write_text("manual base\n")
    generated.write_text("generated base\n")
    _git(tmp_path, "add", "--", "manual.md", "index.md")
    _git(tmp_path, "commit", "-qm", "base")
    manual.write_text("manual staged\n")
    _git(tmp_path, "add", "--", "manual.md")
    original_head = _git(tmp_path, "rev-parse", "HEAD")
    original_index = (tmp_path / ".git" / "index").read_bytes()
    generated.write_text("generated new\n")
    hook = tmp_path / ".git" / "hooks" / "pre-commit"
    hook.write_text(
        "#!/bin/sh\nprintf 'hook overwrite\\n' > manual.md\ngit add -- manual.md\n"
    )
    hook.chmod(0o755)

    assert writer.commit([generated], "generated change") is False
    assert _git(tmp_path, "rev-parse", "HEAD") == original_head
    assert (tmp_path / ".git" / "index").read_bytes() == original_index
    assert manual.read_text() == "hook overwrite\n"
    assert generated.read_text() == "generated new\n"


@pytest.mark.parametrize("external_source", [False, True])
def test_reversible_copy_restores_binary_files(tmp_path, external_source):
    vault = tmp_path / "vault"
    vault.mkdir()
    writer = VaultWriter(vault)
    source = (tmp_path if external_source else vault) / "source.png"
    existing, created = vault / "existing.png", vault / "new.png"
    source.write_bytes(b"\x89PNG\x00new")
    existing.write_bytes(b"\xff\x00old")
    with pytest.raises(OSError, match="publication failed"), writer.reversible_writes():
        writer.copy_file(source, existing)
        writer.copy_file(source, created)
        raise OSError("publication failed")
    assert existing.read_bytes() == b"\xff\x00old"
    assert not created.exists()
