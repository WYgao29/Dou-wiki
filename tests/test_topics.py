from __future__ import annotations

import shutil
import threading
from datetime import UTC, datetime, timedelta

import pytest

from douyin_wiki.models import EntryRecord, RetentionPolicy


@pytest.mark.parametrize(
    "kind", ["overview", "comparison", "evidence_map", "consensus", "decision_brief", "faq"]
)
async def test_retired_model_features_service_generation_is_disabled(service, kind):
    from types import SimpleNamespace

    from douyin_wiki import errors

    def forbidden(*args, **kwargs):
        raise AssertionError("已停用生成不应读取专题或调用模型")

    service.database.get_topic = forbidden
    before = {job.id for job in service.database.list_jobs()}
    with pytest.raises(errors.DouyinWikiError, match="已停用") as result:
        await service.generate_topic_artifact(
            "missing-topic", kind, provider=SimpleNamespace(stream=forbidden)
        )
    assert isinstance(result.value, errors.FeatureDisabledError)
    assert result.value.code == "feature_disabled"
    assert {job.id for job in service.database.list_jobs()} == before
    with service.database.connect() as conn:
        assert not conn.execute("SELECT id FROM topic_artifacts").fetchall()


def test_topic_note_roundtrip_and_rebuild_without_chat_tables(service):
    entry = _add_entry(service, "dy-1234567890123456789", "专题来源", "保留来源证据")
    data = service.database.get_entry_data(entry.id)
    data["metadata"] = {"source_kind": "video"}
    service.vault.write_entry(entry, data)
    topic_id = service.create_topic("笔记专题", [entry.id])["topic"]["id"]
    literal = "## 我的笔记\n\n逐字保留 **判断**。\n\n<script>alert('x')</script>"
    note = service.save_topic_note(topic_id, literal, confirmed=True)
    assert note["kind"] == "note" and note["content_markdown"] == literal
    assert note["model"] is None and note["total_tokens"] is None
    updated = entry.model_copy(update={"updated_at": entry.updated_at + timedelta(minutes=1)})
    service.database.upsert_entry(updated, data)
    service.vault.write_entry(updated, data)
    assert service.get_topic(topic_id)["artifacts"][0]["content_markdown"] == literal
    with service.database.connect() as conn:
        assert not conn.execute(
            "SELECT name FROM sqlite_master WHERE name IN ('web_chat_sessions','web_chat_messages')"
        ).fetchall()
    service.rebuild_database_from_vault(apply=True)
    restored = service.get_topic(topic_id)["artifacts"][0]
    assert restored["id"] == note["id"] and restored["content_markdown"] == literal
    assert restored["kind"] == "note"


def _analysis(title: str, statement: str, atom_id: str) -> dict:
    return {
        "analysis_version": 2,
        "title": title,
        "one_liner": statement,
        "relevance_to_inspiration": "与专题研究目标直接相关。",
        "takeaways": [statement],
        "content_type": "explanation",
        "content_card": {"kind": "explanation", "question": title},
        "chapters": [
            {
                "start_ms": 5000,
                "title": title,
                "summary": statement,
                "evidence": [
                    {
                        "timestamp_ms": 5000,
                        "quote": statement,
                        "evidence_type": "audio",
                    }
                ],
            }
        ],
        "knowledge_atoms": [
            {
                "id": atom_id,
                "statement": statement,
                "atom_type": "fact",
                "provenance": "audio",
                "timestamp_ms": 5000,
                "quote": statement,
                "confidence": 0.95,
            }
        ],
        "tags": ["测试"],
    }


def _add_entry(service, entry_id: str, title: str, statement: str) -> EntryRecord:
    now = datetime.now(UTC)
    work_id = entry_id.removeprefix("dy-")
    entry = EntryRecord(
        id=entry_id,
        video_id=work_id,
        title=title,
        original_url=f"https://www.douyin.com/video/{work_id}",
        canonical_url=f"https://www.douyin.com/video/{work_id}",
        raw_path=f"raw/{work_id}.md",
        source_path=f"wiki/sources/{work_id}.md",
        status="active",
        media_status="present",
        retention=RetentionPolicy.KEEP,
        summary=statement,
        tags=["测试"],
        created_at=now,
        updated_at=now,
    )
    data = {"analysis": _analysis(title, statement, f"atom-{work_id}")}
    service.database.upsert_entry(entry, data)
    service.indexer.index_entry(entry, data)
    return entry


def _start_topic_mutation_after_artifact_save(
    service,
    mutation,
    original_save,
):
    started = threading.Event()
    finished = threading.Event()
    errors: list[BaseException] = []

    def run_mutation() -> None:
        started.set()
        try:
            mutation()
        except BaseException as exc:  # pragma: no cover - surfaced by the test below
            errors.append(exc)
        finally:
            finished.set()

    worker = threading.Thread(target=run_mutation)

    def save_with_mutation(artifact):
        worker.start()
        assert started.wait(timeout=2)
        # The old implementation has no entry-operation fence here, so the
        # mutation completes before the stale topic snapshot is persisted.
        # The fixed implementation deliberately waits until finalization ends.
        finished.wait(timeout=2)
        return original_save(artifact)

    service.database.save_topic_artifact = save_with_mutation
    return worker, finished, errors


def test_topic_search_is_filtered_before_ranking_and_source_disable_is_immediate(service) -> None:
    ai_one = _add_entry(service, "dy-ai-1", "企业 AI 部署", "企业 AI 部署需要先做流程梳理")
    ai_two = _add_entry(service, "dy-ai-2", "本地模型", "本地模型量化会影响显存占用")
    coffee = _add_entry(service, "dy-coffee", "聪明杯教程", "聪明杯使用十五克咖啡粉冲煮")
    topic = service.create_topic("企业 AI 决策", [ai_one.id, ai_two.id], goal="选择部署方案")
    topic_id = topic["topic"]["id"]

    assert service.search_topic(topic_id, "聪明杯怎么冲") == []
    assert all(
        item.entry_id in {ai_one.id, ai_two.id}
        for item in service.search_topic(topic_id, "AI 部署")
    )
    assert service.search_knowledge("聪明杯", entry_ids=[coffee.id])[0].entry_id == coffee.id
    assert service.search_knowledge("聪明杯", entry_ids=[]) == []

    service.set_topic_sources(
        topic_id,
        [
            {"entry_id": ai_one.id, "enabled": False},
            {"entry_id": ai_two.id, "enabled": True},
        ],
    )
    assert all(item.entry_id == ai_two.id for item in service.search_topic(topic_id, "本地模型"))


@pytest.mark.asyncio
async def test_topic_note_provenance_staleness_and_literal_note(service) -> None:
    entry = _add_entry(service, "dy-ai", "AI 文章", "企业应先验证一个高价值流程")
    topic_id = service.create_topic("决策专题", [entry.id], goal="决定试点范围")["topic"]["id"]
    artifact = service.save_topic_note(topic_id, "逐字笔记", confirmed=True)
    assert artifact["status"] == "current"
    assert artifact["total_tokens"] is None
    artifact_path = (
        service.config.vault_path
        / "topics"
        / topic_id
        / "artifacts"
        / f"{artifact['id']}.md"
    )
    assert artifact_path.exists()
    assert "逐字笔记" in artifact_path.read_text(encoding="utf-8")

    data = service.database.get_entry_data(entry.id)
    changed = entry.model_copy(update={"updated_at": entry.updated_at + timedelta(minutes=1)})
    service.database.upsert_entry(changed, data)
    refreshed = service.get_topic(topic_id)
    assert refreshed["artifacts"][0]["status"] == "current"
    assert refreshed["artifacts"][0]["content_markdown"] == "逐字笔记"
    assert "当前版本" in artifact_path.read_text(encoding="utf-8")

    before = len(refreshed["artifacts"])
    with pytest.raises(ValueError, match="明确确认"):
        service.save_topic_note(topic_id, "逐字笔记", confirmed=False)
    assert len(service.get_topic(topic_id)["artifacts"]) == before
    note = service.save_topic_note(topic_id, "逐字笔记", confirmed=True)
    assert note["content_markdown"] == "逐字笔记"
    assert note["user_authored"] is True
    assert (service.config.vault_path / "topics" / topic_id / ".data" / "topic.md").exists()


def test_rebuild_reports_invalid_topic_sidecar_without_clearing_database(service) -> None:
    entry = _add_entry(service, "dy-topic-rebuild", "专题来源", "专题来源内容")
    topic_id = service.create_topic("专题预检", [entry.id])["topic"]["id"]
    topic_path = service.config.vault_path / "topics" / topic_id / ".data" / "topic.md"
    topic_path.write_text(
        "---\n"
        "type: research_topic_data\n"
        "id: [invalid]\n"
        "title: 专题预检\n"
        "source_revision: broken\n"
        "created_at: 2026-09-01T00:00:00+00:00\n"
        "updated_at: 2026-09-01T00:00:00+00:00\n"
        "sources: []\n"
        "---\n\n# 专题机器数据\n",
        encoding="utf-8",
    )

    report = service.rebuild_database_from_vault(apply=False)
    relative_path = str(topic_path.relative_to(service.config.vault_path))
    assert any(item["path"] == relative_path for item in report["topic_errors"])
    with pytest.raises(Exception, match="topic_errors"):
        service.rebuild_database_from_vault(apply=True)
    assert service.database.get_entry(entry.id).id == entry.id
    assert service.database.get_topic(topic_id).id == topic_id


@pytest.mark.asyncio
async def test_rebuild_reports_missing_topic_artifact_without_clearing_database(service) -> None:
    entry = _add_entry(service, "dy-artifact-rebuild", "成果来源", "成果来源内容")
    topic_id = service.create_topic("成果预检", [entry.id])["topic"]["id"]
    artifact = service.save_topic_note(topic_id, "逐字笔记", confirmed=True)
    artifact_path = (
        service.config.vault_path
        / "topics"
        / topic_id
        / "artifacts"
        / f"{artifact['id']}.md"
    )
    artifact_path.unlink()

    report = service.rebuild_database_from_vault(apply=False)
    relative_path = str(artifact_path.relative_to(service.config.vault_path))
    assert any(item["path"] == relative_path for item in report["artifact_errors"])
    with pytest.raises(Exception, match="artifact_errors"):
        service.rebuild_database_from_vault(apply=True)
    assert service.database.get_entry(entry.id).id == entry.id
    assert service.database.get_topic(topic_id).id == topic_id


@pytest.mark.asyncio
async def test_rebuild_reports_invalid_topic_artifact_frontmatter_without_clearing_database(
    service,
) -> None:
    entry = _add_entry(service, "dy-artifact-frontmatter", "成果元数据来源", "成果元数据内容")
    entry_data = service.database.get_entry_data(entry.id)
    entry_data["metadata"] = {"source_kind": "video"}
    service.vault.write_entry(entry, entry_data)
    topic_id = service.create_topic("成果元数据预检", [entry.id])["topic"]["id"]
    artifact = service.save_topic_note(topic_id, "逐字笔记", confirmed=True)
    artifact_path = (
        service.config.vault_path
        / "topics"
        / topic_id
        / "artifacts"
        / f"{artifact['id']}.md"
    )
    lines = artifact_path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if line.startswith("created_at:"):
            lines[index] = "created_at: [invalid]"
            break
    artifact_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    report = service.rebuild_database_from_vault(apply=False)
    relative_path = str(artifact_path.relative_to(service.config.vault_path))

    assert any(item["path"] == relative_path for item in report["artifact_errors"])
    with pytest.raises(Exception, match="artifact_errors"):
        service.rebuild_database_from_vault(apply=True)
    assert service.database.get_entry(entry.id).id == entry.id
    assert service.database.get_topic(topic_id).id == topic_id


@pytest.mark.asyncio
async def test_source_update_at_finalization_does_not_revert_vault_topic(service) -> None:
    original = _add_entry(service, "dy-final-original", "最终化原始文章", "原始证据")
    replacement = _add_entry(service, "dy-final-replacement", "最终化替换文章", "替换证据")
    topic_id = service.create_topic("最终化交错专题", [original.id])["topic"]["id"]
    original_save = service.database.save_topic_artifact

    worker, finished, errors = _start_topic_mutation_after_artifact_save(
        service,
        lambda: service.set_topic_sources(
            topic_id,
            [{"entry_id": replacement.id, "enabled": True}],
        ),
        original_save,
    )

    artifact = service.save_topic_note(topic_id, "逐字笔记", confirmed=True)
    worker.join(timeout=2)

    assert finished.is_set()
    assert errors == []
    assert artifact["status"] == "current"
    topic_index = (
        service.config.vault_path / "topics" / topic_id / "index.md"
    ).read_text(encoding="utf-8")
    assert replacement.source_path.removesuffix(".md") in topic_index
    assert original.source_path.removesuffix(".md") not in topic_index


@pytest.mark.asyncio
async def test_topic_delete_at_finalization_is_serialized_without_sqlite_error(service) -> None:
    entry = _add_entry(service, "dy-final-delete", "最终化待删除专题来源", "专题证据")
    topic_id = service.create_topic("最终化删除交错专题", [entry.id])["topic"]["id"]
    topic_dir = service.config.vault_path / "topics" / topic_id
    original_save = service.database.save_topic_artifact

    def delete_topic() -> None:
        with service.vault.entry_operations_locked():
            with service.database.connect() as conn:
                conn.execute("DELETE FROM research_topics WHERE id=?", (topic_id,))
            shutil.rmtree(topic_dir)

    worker, finished, errors = _start_topic_mutation_after_artifact_save(
        service, delete_topic, original_save
    )

    artifact = service.save_topic_note(topic_id, "逐字笔记", confirmed=True)
    worker.join(timeout=2)

    assert finished.is_set()
    assert errors == []
    assert artifact["status"] == "current"
    with service.database.connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM research_topics WHERE id=?", (topic_id,)
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM topic_artifacts WHERE topic_id=?", (topic_id,)
        ).fetchone()[0] == 0
    assert not topic_dir.exists()
