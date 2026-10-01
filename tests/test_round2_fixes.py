"""Regression coverage for the four repair-induced round-two findings."""

import json
from contextlib import contextmanager
from datetime import timedelta

import pytest

from douyin_wiki.errors import JobStateError
from douyin_wiki.models import AnalysisResult, InspirationInput
from douyin_wiki.time_utils import utc_now
from tests.test_quality_baseline_fixes import capture


@pytest.mark.parametrize("expiry_kind", ["expired", "missing", "future"])
async def test_reacquired_media_renews_expired_retention_and_preserves_valid_expiry(
    service, expiry_kind
):
    entry = await capture(service)
    expiry = {
        "expired": utc_now() - timedelta(days=1),
        "missing": None,
        "future": utc_now() + timedelta(days=7),
    }[expiry_kind]
    service.database.upsert_entry(
        entry.model_copy(update={"media_expires_at": expiry}),
        service.database.get_entry_data(entry.id),
    )
    service.database.mark_media_removed(entry.id)
    before = utc_now()
    await capture(service)
    current = service.database.get_entry(entry.id)
    assert current.media_status == "present"
    if expiry_kind == "future":
        assert current.media_expires_at == expiry
    else:
        duration = timedelta(days=service.config.media.retention_days)
        assert before + duration <= current.media_expires_at <= utc_now() + duration
    assert entry.id not in [row.id for row in service.database.entries_with_expired_media()]
    report = service.run_maintenance(apply=True)
    assert entry.id not in report["media_removed"]
    assert service.database.get_entry(entry.id).media_status == "present"
    assert service.vault.load_entries()[0][0].media_expires_at == current.media_expires_at


def audio_context():
    return {
        "metadata": {"source_kind": "video", "duration_seconds": 60},
        "transcript_corrected": [
            {"start_ms": 0, "end_ms": 4000, "text": "第一步倒入30克咖啡"},
            {"start_ms": 4000, "end_ms": 8000, "text": "然后加水200毫升"},
            {"start_ms": 40000, "end_ms": 41000, "text": "最后分享其他技巧"},
        ],
        "ocr": [],
    }


def quoted_analysis(kind, shape, timestamp=0):
    quote = "第一步倒入30克咖啡然后加水200毫升"
    if shape == "atom":
        return AnalysisResult(
            title="咖啡教程",
            knowledge_atoms=[
                {
                    "id": "coffee",
                    "statement": "30克咖啡加200毫升水",
                    "provenance": kind,
                    "timestamp_ms": timestamp,
                    "quote": quote,
                }
            ],
        )
    return AnalysisResult(
        title="咖啡教程",
        chapters=[
            {
                "start_ms": 0,
                "end_ms": 8000,
                "title": "冲泡咖啡",
                "summary": "配比与冲泡步骤",
                "evidence": [{"timestamp_ms": timestamp, "quote": quote, "evidence_type": kind}],
            }
        ],
    )


@pytest.mark.parametrize("kind", ["audio", "audio+ocr"])
@pytest.mark.parametrize("shape", ["atom", "chapter"])
def test_contiguous_asr_quote_is_retained_with_its_start_locator(service, kind, shape):
    analysis = quoted_analysis(kind, shape)
    service.validate_analysis_evidence(analysis, audio_context())
    cleaned, removed = service.prune_unverified_analysis_evidence(analysis, audio_context())
    assert removed == 0
    assert cleaned == analysis


@pytest.mark.parametrize("kind", ["audio", "audio+ocr"])
def test_cross_segment_quote_rejects_wrong_locator_and_can_repair_it(service, kind):
    analysis = quoted_analysis(kind, "atom", timestamp=40000)
    with pytest.raises(JobStateError, match="指定时间戳"):
        service.validate_analysis_evidence(analysis, audio_context())
    cleaned, removed = service.prune_unverified_analysis_evidence(analysis, audio_context())
    assert removed == 0
    assert len(cleaned.knowledge_atoms) == 1
    assert cleaned.knowledge_atoms[0].timestamp_ms == 0


@pytest.mark.parametrize("timestamp", [0, 40000])
def test_repeated_cross_segment_quote_accepts_each_actual_start(service, timestamp):
    context = audio_context()
    context["transcript_corrected"][2:] = [
        {"start_ms": 40000, "end_ms": 44000, "text": "第一步倒入30克咖啡"},
        {"start_ms": 44000, "end_ms": 48000, "text": "然后加水200毫升"},
    ]
    service.validate_analysis_evidence(quoted_analysis("audio", "atom", timestamp), context)


def test_asr_quote_cannot_skip_intervening_text(service):
    context = audio_context()
    context["transcript_corrected"].insert(
        1, {"start_ms": 4000, "end_ms": 5000, "text": "请先准备滤纸"}
    )
    with pytest.raises(JobStateError, match="指定时间戳"):
        service.validate_analysis_evidence(quoted_analysis("audio", "atom"), context)


async def test_post_commit_read_failure_keeps_committed_documents(service, monkeypatch):
    entry = await capture(service)
    real_get = service.database.get_entry

    def fail_after_commit(entry_id):
        value = real_get(entry_id)
        if value.inspirations:
            raise OSError("injected post-commit read failure")
        return value

    monkeypatch.setattr(service.database, "get_entry", fail_after_commit)
    with pytest.raises(OSError, match="post-commit"):
        service.add_inspiration(entry.id, InspirationInput(text="已提交的新灵感"))
    monkeypatch.setattr(service.database, "get_entry", real_get)
    stored = service.database.get_entry(entry.id)
    restored = service.vault.load_entries()[0][0]
    assert [item.text for item in stored.inspirations] == ["已提交的新灵感"]
    assert stored.inspirations == restored.inspirations
    service.rebuild_database_from_vault(apply=True)
    assert service.database.get_entry(entry.id).inspirations == stored.inspirations


@pytest.mark.parametrize("failure", ["result_decode", "commit"])
async def test_publication_failure_before_commit_restores_database_and_documents(
    service, monkeypatch, failure
):
    entry = await capture(service)
    root = service.config.vault_path
    before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*.md")}
    if failure == "result_decode":
        original = service.database._entry_from_row

        def fail_new_result(row):
            if json.loads(row["purposes_json"]):
                raise OSError("injected result_decode failure")
            return original(row)

        monkeypatch.setattr(service.database, "_entry_from_row", fail_new_result)
    else:
        original = service.database.connect

        @contextmanager
        def fail_before_commit():
            with original() as conn:
                yield conn
                if conn.in_transaction:
                    raise OSError("injected commit failure")

        monkeypatch.setattr(service.database, "connect", fail_before_commit)
    with pytest.raises(OSError, match=f"injected {failure} failure"):
        service.add_inspiration(entry.id, InspirationInput(text="提交前失败不能残留"))
    assert service.database.get_entry(entry.id).inspirations == []
    assert service.vault.load_entries()[0][0].inspirations == []
    assert {path.relative_to(root): path.read_bytes() for path in root.rglob("*.md")} == before


@pytest.mark.parametrize("operation", ["initialize", "rebuild", "upsert"])
@pytest.mark.parametrize("spelling", ["ai agent", "AIAGENT", " ＡＩ　Ａｇｅｎｔ "])
async def test_tag_history_uses_existing_normalized_alias_resolution(service, operation, spelling):
    entry = await capture(service)
    with service.database.connect() as conn:
        conn.execute(
            "INSERT INTO tags(name,created_at) VALUES (?,?)", ("AI 代理", utc_now().isoformat())
        )
        conn.execute(
            "INSERT INTO tag_aliases(alias,canonical) VALUES (?,?)", ("AI Agent", "AI 代理")
        )
    assert service.tags.resolve([spelling]) == ["AI 代理"]
    data = service.database.get_entry_data(entry.id)
    data["analysis"]["tags"] = [spelling]
    updated = entry.model_copy(update={"tags": [spelling]})
    if operation == "initialize":
        with service.database.connect() as conn:
            conn.execute(
                "UPDATE entries SET tags_json=?,data_json=? WHERE id=?",
                (json.dumps([spelling]), json.dumps(data), entry.id),
            )
        service.database.initialize()
    elif operation == "rebuild":
        service.write_entry_documents(
            updated, data, action="manual", log_summary="修改合法标签", commit_message=None
        )
        service.rebuild_database_from_vault(apply=True)
    else:
        service.database.upsert_entry(updated, data)
    assert service.tags.resolve([spelling]) == ["AI 代理"]
    with service.database.connect() as conn:
        assert (
            conn.execute("SELECT 1 FROM tags WHERE name=?", (spelling.strip(),)).fetchone() is None
        )
        row = conn.execute("SELECT last_used_at FROM tags WHERE name='AI 代理'").fetchone()
        assert row["last_used_at"] == entry.created_at.isoformat()
