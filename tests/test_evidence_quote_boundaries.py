"""Evidence quotes must consume complete original Unicode characters."""

import pytest

from douyin_wiki.errors import JobStateError
from douyin_wiki.models import AnalysisResult, TranscriptSegment

KINDS = [
    "audio",
    "audio+ocr",
    "ocr",
    "image_ocr",
    "post_text",
    "post_text+image_ocr",
    "reminder",
]


def citation(kind, text, quote, *, actual_quote_later=False):
    image = kind in {"image_ocr", "post_text", "post_text+image_ocr"}
    metadata = {"source_kind": "image_note" if image else "video", "duration_seconds": 60}
    if image:
        metadata["image_paths"] = ["one.png", "two.png"]
        metadata["post_text"] = text if kind == "post_text" else "无关正文"
        transcript = []
        ocr = [{"image_index": 1, "text": text}]
        if actual_quote_later:
            ocr.append({"image_index": 2, "text": quote})
    else:
        transcript = [{"start_ms": 0, "end_ms": 1000, "text": text}]
        ocr = [{"timestamp_ms": 0, "text": text}]
        if actual_quote_later:
            transcript.append({"start_ms": 10000, "end_ms": 11000, "text": quote})
            ocr.append({"timestamp_ms": 10000, "text": quote})
    context = {"metadata": metadata, "transcript_corrected": transcript, "ocr": ocr}
    if kind == "reminder":
        analysis = AnalysisResult(
            title="引用",
            reminders=[
                {
                    "id": "r",
                    "title": "提醒",
                    "reason": "原话",
                    "source_quote": quote,
                    "confidence": 0.8,
                }
            ],
        )
    else:
        atom = {"id": "a", "statement": "原话", "provenance": kind, "quote": quote}
        if image:
            if kind != "post_text":
                atom["image_index"] = 1
        else:
            atom["timestamp_ms"] = 0
        analysis = AnalysisResult(title="引用", knowledge_atoms=[atom])
    return analysis, context


def chapter_citation(kind, text, quote, *, actual_quote_later=False):
    _, context = citation(kind, text, quote, actual_quote_later=actual_quote_later)
    analysis = AnalysisResult(
        title="章节",
        chapters=[
            {
                "start_ms": 0,
                "end_ms": 20000,
                "title": "章节",
                "summary": "原话依据",
                "evidence": [{"timestamp_ms": 0, "quote": quote, "evidence_type": kind}],
            }
        ],
    )
    return analysis, context


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("text,quote", [("Straße", "Stras"), ("oﬃce", "fice")])
def test_partial_expanded_character_cannot_support_atom_or_reminder(service, kind, text, quote):
    analysis, context = citation(kind, text, quote)
    with pytest.raises(JobStateError):
        service.validate_analysis_evidence(analysis, context)
    cleaned, removed = service.prune_unverified_analysis_evidence(analysis, context)
    assert removed == 1
    assert cleaned.knowledge_atoms == []
    assert cleaned.reminders == []


@pytest.mark.parametrize("kind", ["audio", "audio+ocr", "ocr"])
@pytest.mark.parametrize("text,quote", [("Straße", "Stras"), ("oﬃce", "fice")])
def test_partial_expanded_character_cannot_support_chapter(service, kind, text, quote):
    analysis, context = chapter_citation(kind, text, quote)
    with pytest.raises(JobStateError, match="指定时间戳"):
        service.validate_analysis_evidence(analysis, context)
    cleaned, removed = service.prune_unverified_analysis_evidence(analysis, context)
    assert removed == 2  # The invalid evidence and its now unsupported chapter.
    assert cleaned.chapters == []


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("text,quote", [("Straße", "STRASSE"), ("oﬃce", "OFFICE")])
def test_complete_casefold_equivalent_quote_is_retained_verbatim(service, kind, text, quote):
    analysis, context = citation(kind, text, quote)
    service.validate_analysis_evidence(analysis, context)
    cleaned, removed = service.prune_unverified_analysis_evidence(analysis, context)
    assert removed == 0
    assert cleaned == analysis


@pytest.mark.parametrize("kind", ["audio", "ocr", "image_ocr"])
def test_partial_expansion_does_not_validate_wrong_time_or_image(service, kind):
    analysis, context = citation(kind, "Straße", "Stras", actual_quote_later=True)
    with pytest.raises(JobStateError, match="指定时间戳/图片"):
        service.validate_analysis_evidence(analysis, context)
    cleaned, removed = service.prune_unverified_analysis_evidence(analysis, context)
    if kind == "image_ocr":
        assert removed == 1
        assert cleaned.knowledge_atoms == []
        corrected = AnalysisResult(
            title="引用",
            knowledge_atoms=[
                analysis.knowledge_atoms[0].model_copy(update={"image_index": 2}),
            ],
        )
        service.validate_analysis_evidence(corrected, context)
    else:
        assert removed == 0
        assert len(cleaned.knowledge_atoms) == 1
        assert cleaned.knowledge_atoms[0].timestamp_ms == 10000
        assert cleaned.knowledge_atoms[0].quote == "Stras"


@pytest.mark.parametrize("kind", ["audio", "audio+ocr", "ocr"])
def test_chapter_time_repairs_to_complete_actual_quote(service, kind):
    analysis, context = chapter_citation(kind, "Straße", "Stras", actual_quote_later=True)
    with pytest.raises(JobStateError, match="指定时间戳"):
        service.validate_analysis_evidence(analysis, context)
    cleaned, removed = service.prune_unverified_analysis_evidence(analysis, context)
    assert removed == 0
    assert len(cleaned.chapters) == 1
    assert cleaned.chapters[0].evidence[0].timestamp_ms == 10000
    assert cleaned.chapters[0].evidence[0].quote == "Stras"


@pytest.mark.parametrize("kind", ["audio", "audio+ocr"])
@pytest.mark.parametrize("models", [False, True])
def test_overlapping_matches_skip_partial_expansions_and_keep_real_starts(service, kind, models):
    segments = [
        {"id": 0, "start_ms": 0, "end_ms": 1000, "text": "s"},
        {"id": 1, "start_ms": 1000, "end_ms": 1000, "text": "，_。"},
        {"id": 2, "start_ms": 10000, "end_ms": 11000, "text": "ß"},
        {"id": 3, "start_ms": 20000, "end_ms": 21000, "text": "ß"},
    ]
    context = {
        "metadata": {"source_kind": "video", "duration_seconds": 60},
        "transcript_corrected": (
            [TranscriptSegment(**item) for item in segments] if models else segments
        ),
        "ocr": [],
    }
    analysis, _ = citation(kind, "unused", "SS")
    for timestamp in [10000, 20000]:
        expected = AnalysisResult(
            title="引用",
            knowledge_atoms=[
                analysis.knowledge_atoms[0].model_copy(update={"timestamp_ms": timestamp}),
            ],
        )
        service.validate_analysis_evidence(expected, context)
        cleaned, removed = service.prune_unverified_analysis_evidence(expected, context)
        assert removed == 0
        assert cleaned == expected
    with pytest.raises(JobStateError):
        service.validate_analysis_evidence(analysis, context)
    cleaned, removed = service.prune_unverified_analysis_evidence(analysis, context)
    assert removed == 0
    assert cleaned.knowledge_atoms[0].timestamp_ms == 10000
    partial = AnalysisResult(
        title="引用",
        knowledge_atoms=[
            analysis.knowledge_atoms[0].model_copy(update={"timestamp_ms": 10000, "quote": "S"}),
        ],
    )
    with pytest.raises(JobStateError):
        service.validate_analysis_evidence(partial, context)
    cleaned, removed = service.prune_unverified_analysis_evidence(partial, context)
    assert removed == 0
    assert cleaned.knowledge_atoms[0].timestamp_ms == 0
    assert cleaned.knowledge_atoms[0].quote == "S"


@pytest.mark.parametrize("kind", ["audio", "reminder"])
def test_quote_with_no_retained_text_is_rejected(service, kind):
    analysis, context = citation(kind, "原文", "，_。")
    with pytest.raises(JobStateError):
        service.validate_analysis_evidence(analysis, context)
    cleaned, removed = service.prune_unverified_analysis_evidence(analysis, context)
    assert removed == 1
    assert cleaned.knowledge_atoms == []
    assert cleaned.reminders == []
