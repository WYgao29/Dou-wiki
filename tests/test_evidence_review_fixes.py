"""Regression cases for evidence precision across chapter, legacy and search outputs."""

from copy import deepcopy

import pytest

from douyin_wiki.models import AnalysisResult, TimestampQuality
from tests.support.web import _web_fixture


def transcript_row(index, start, end, text, precision="aligned"):
    return {
        "id": index,
        "start_ms": start,
        "end_ms": end,
        "text": text,
        "timestamp_precision": precision,
        "alignment_reason": "gross_repair" if precision == "window" else None,
    }


@pytest.mark.parametrize(
    "start,end,evidence_indices,expected_range",
    [
        (0, 60000, [0, 1], (0, 60000)),
        (10000, 50000, [1], (10000, 60000)),
        (40000, 90000, [1, 2], (30000, 90000)),
        (0, None, [0, 1], (0, 60000)),
        (30000, None, [1, 2], (30000, 80000)),
    ],
)
def test_coarse_chapter_heading_covers_declared_range_and_all_evidence(
    tmp_path, start, end, evidence_indices, expected_range
):
    _, service = _web_fixture(tmp_path)
    rows = [
        transcript_row(0, 0, 30000, "第一步使用精确材料。"),
        transcript_row(1, 30000, 60000, "第二步使用粗略材料。", "window"),
        transcript_row(2, 60000, 90000, "最后检查精确材料。"),
    ]
    timestamps = [0, 30000, 80000]
    analysis = AnalysisResult.model_validate(
        {
            "title": "完整步骤",
            "chapters": [
                {
                    "start_ms": start,
                    "end_ms": end,
                    "title": "章节",
                    "summary": "按顺序进行",
                    "evidence": [
                        {"timestamp_ms": timestamps[index], "quote": rows[index]["text"]}
                        for index in evidence_indices
                    ],
                }
            ],
        }
    )
    service.validate_analysis_evidence(analysis, {"transcript_corrected": rows})
    chapter = analysis.chapters[0]
    assert (chapter.start_ms, chapter.end_ms) == (start, end)
    assert (chapter.timestamp_start_ms, chapter.timestamp_end_ms) == expected_range
    assert chapter.timestamp_precision == "window"
    entry = service.database.get_entry("dy-123")
    rendered = service.vault._render_source(entry, {"analysis": analysis.model_dump(mode="json")})
    clocks = [f"{value // 60000:02d}:{value // 1000 % 60:02d}" for value in expected_range]
    assert f"### ▶ 约 {clocks[0]}–{clocks[1]}（时间未经精确对齐）" in rendered


def test_precise_chapter_does_not_gain_a_coarse_label(tmp_path):
    _, service = _web_fixture(tmp_path)
    row = transcript_row(0, 0, 30000, "精确材料。")
    analysis = AnalysisResult.model_validate(
        {
            "title": "资料",
            "chapters": [
                {
                    "start_ms": 0,
                    "title": "章节",
                    "summary": "摘要",
                    "timestamp_precision": "window",
                    "timestamp_end_ms": 90000,
                    "evidence": [{"timestamp_ms": 0, "quote": row["text"]}],
                }
            ],
        }
    )
    service.validate_analysis_evidence(analysis, {"transcript_corrected": [row]})
    chapter = analysis.chapters[0]
    assert chapter.timestamp_precision == "unknown"
    assert chapter.timestamp_start_ms is chapter.timestamp_end_ms is None
    entry = service.database.get_entry("dy-123")
    rendered = service.vault._render_source(entry, {"analysis": analysis.model_dump(mode="json")})
    assert "### ▶ 00:00" in rendered
    assert "时间未经精确对齐" not in rendered


def test_legacy_claim_metadata_survives_upgrade_and_reparse():
    quality = {
        "timestamp_precision": "window",
        "timestamp_start_ms": 0,
        "timestamp_end_ms": 60000,
        "alignment_reason": "gross_repair",
    }
    payload = {
        "title": "旧协议资料",
        "claims": [
            {"id": "a", "text": "主张", "source_quote": "原话", "start_ms": 30000, **quality}
        ],
    }
    analysis = AnalysisResult.model_validate(payload)
    for item in [analysis.knowledge_atoms[0], analysis.claims[0]]:
        assert item.model_dump(include=set(quality)) == quality
    assert (
        AnalysisResult.model_validate(analysis.model_dump()).model_dump() == analysis.model_dump()
    )
    for name in quality:
        payload["claims"][0].pop(name)
    old = AnalysisResult.model_validate(payload)
    for item in [old.knowledge_atoms[0], old.claims[0]]:
        assert item.model_dump(include=set(quality)) == TimestampQuality().model_dump()


@pytest.mark.parametrize("legacy_only", [True, False])
def test_server_synchronizes_claim_quality_and_preserves_legacy_content(service, legacy_only):
    from tests.support.gateway import make_gateway_job

    row = transcript_row(0, 0, 60000, "完整原话。", "window")
    claim = {
        "id": "a",
        "text": "原有主张文字",
        "source_quote": row["text"],
        "start_ms": 30000,
        "valid_until": "2030-01-01T00:00:00Z",
        "review_after": "2029-01-01T00:00:00Z",
        "stale": True,
        "timestamp_precision": "aligned",
        "timestamp_start_ms": 30000,
        "timestamp_end_ms": 30001,
    }
    payload = {"title": "资料", "claims": [claim]}
    if not legacy_only:
        payload["knowledge_atoms"] = [
            {
                "id": "a",
                "statement": "知识原子文字",
                "quote": row["text"],
                "timestamp_ms": 30000,
            }
        ]
    job = make_gateway_job(service, phase="analysis", artifacts={"transcript_corrected": [row]})
    result = service.submit_gateway_analysis(job.id, payload, producer="test")
    saved = result.artifacts["analysis"]
    actual = saved["claims"][0]
    assert actual["timestamp_precision"] == "window"
    assert actual["timestamp_start_ms"] == 0
    assert actual["timestamp_end_ms"] == 60000
    assert actual["alignment_reason"] == "gross_repair"
    for field in ("id", "text", "source_quote", "start_ms", "valid_until", "review_after", "stale"):
        assert actual[field] == claim[field]
    assert service.database.get_job(job.id).artifacts["analysis"] == saved
    assert AnalysisResult.model_validate(saved).model_dump(mode="json") == saved


@pytest.mark.parametrize("mismatch", ["id", "source_quote", "start_ms"])
def test_unmatched_claim_cannot_inherit_or_submit_aligned_quality(service, mismatch):
    row = transcript_row(0, 0, 60000, "完整原话。")
    claim = {"id": "a", "text": "主张", "source_quote": row["text"], "start_ms": 30000}
    claim[mismatch] = {"id": "other", "source_quote": "另一段原话", "start_ms": 1}[mismatch]
    analysis = AnalysisResult.model_validate(
        {
            "title": "资料",
            "claims": [{**claim, "timestamp_precision": "aligned", "timestamp_end_ms": 60000}],
            "knowledge_atoms": [
                {"id": "a", "statement": "主张", "quote": row["text"], "timestamp_ms": 30000}
            ],
        }
    )
    service.validate_analysis_evidence(analysis, {"transcript_corrected": [row]})
    assert analysis.knowledge_atoms[0].timestamp_precision == "aligned"
    assert analysis.claims[0].model_dump(include=set(TimestampQuality.model_fields)) == (
        TimestampQuality().model_dump()
    )
    assert analysis.claims[0].model_dump(include=set(claim)) == claim


def test_claim_quality_sync_does_not_modify_a_shallow_copy_parent(service):
    row = transcript_row(0, 0, 60000, "完整原话。", "window")
    analysis = AnalysisResult.model_validate(
        {
            "title": "资料",
            "knowledge_atoms": [
                {"id": "a", "statement": "主张", "quote": row["text"], "timestamp_ms": 30000}
            ],
        }
    )
    service.validate_analysis_evidence(analysis, {"transcript_corrected": [row]})
    before = deepcopy(analysis.model_dump())
    candidate = analysis.model_copy(update={"knowledge_atoms": []})
    service.validate_analysis_evidence(candidate, {"transcript_corrected": [row]})
    assert candidate.claims[0].timestamp_precision == "unknown"
    assert analysis.claims[0].timestamp_precision == "window"
    assert analysis.model_dump() == before


@pytest.mark.parametrize("atom_id", ["normal-id", "part:atom:one"])
@pytest.mark.parametrize("audio_first", [True, False])
def test_search_matches_complete_atom_id_with_a_conflicting_tail(tmp_path, atom_id, audio_first):
    _, service = _web_fixture(tmp_path)
    entry = service.database.get_entry("dy-123")
    row = transcript_row(0, 0, 9000, "粗略窗口说明窗户宽度是两米。", "window")
    audio_atom = {
        "id": atom_id,
        "statement": "窗户宽度是两米",
        "quote": row["text"],
        "timestamp_ms": 0,
    }
    ocr_atom = {
        "id": "one",
        "statement": "窗户数量是三扇",
        "quote": "窗户数量是三扇。",
        "provenance": "ocr",
        "timestamp_ms": 0,
    }
    analysis = AnalysisResult.model_validate(
        {
            "title": "尺寸",
            "knowledge_atoms": [audio_atom, ocr_atom] if audio_first else [ocr_atom, audio_atom],
        }
    )
    data = {
        **service.database.get_entry_data(entry.id),
        "transcript_corrected": [row],
        "ocr": [{"timestamp_ms": 0, "text": ocr_atom["quote"]}],
    }
    service.validate_analysis_evidence(analysis, data)
    data["analysis"] = analysis.model_dump(mode="json")
    service.database.upsert_entry(entry, data)
    chunks = service.indexer.index_entry(entry, data)
    for atom, precision in [(audio_atom, "window"), (ocr_atom, "unknown")]:
        matches = service.search_knowledge(atom["statement"], entry_ids=[entry.id], limit=30)
        selected = next(
            item
            for item in matches
            if item.relation_reason == "知识原子与问题匹配" and atom["statement"] in item.snippet
        )
        assert selected.timestamp_precision == precision
        if precision == "window":
            assert (selected.timestamp_start_ms, selected.timestamp_end_ms) == (0, 9000)
            assert "约 00:00–00:09（时间未经精确对齐）" in selected.snippet
        else:
            assert "时间未经精确对齐" not in selected.snippet
    target = next(chunk for chunk in chunks if chunk["id"] == f"{entry.id}:atom:{atom_id}")
    related = service.indexer.find_related_claims(target["text"], limit=30)
    assert any(
        item["claim_id"] == atom_id and item["source_quote"] == row["text"] for item in related
    )
