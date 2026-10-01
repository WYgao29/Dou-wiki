"""Unicode quote matching and ASR locator regression coverage."""

import pytest

from douyin_wiki.errors import JobStateError
from douyin_wiki.models import AnalysisResult, TranscriptSegment


def unicode_context(*, long_segments=False):
    boundary = 4000 if long_segments else 500
    return {
        "metadata": {"source_kind": "video", "duration_seconds": 60},
        "transcript_corrected": [
            {"start_ms": 0, "end_ms": boundary, "text": "ΟΣ"},
            {"start_ms": boundary, "end_ms": boundary * 2, "text": "Α"},
            {"start_ms": 40000, "end_ms": 41000, "text": "其他内容"},
        ],
        "ocr": [],
    }


def quoted_analysis(kind, *, shape="atom", quote="ΟΣΑ", timestamp=0):
    if shape == "atom":
        return AnalysisResult(
            title="原文",
            knowledge_atoms=[
                {
                    "id": "unicode",
                    "statement": "原文",
                    "provenance": kind,
                    "timestamp_ms": timestamp,
                    "quote": quote,
                }
            ],
        )
    return AnalysisResult(
        title="原文",
        chapters=[
            {
                "start_ms": 0,
                "end_ms": 8000,
                "title": "原文",
                "summary": "原文依据",
                "evidence": [{"timestamp_ms": timestamp, "quote": quote, "evidence_type": kind}],
            }
        ],
    )


@pytest.mark.parametrize("kind", ["audio", "audio+ocr"])
@pytest.mark.parametrize("shape", ["atom", "chapter"])
@pytest.mark.parametrize("long_segments", [False, True])
def test_contextual_unicode_case_does_not_drop_cross_segment_evidence(
    service, kind, shape, long_segments
):
    context = unicode_context(long_segments=long_segments)
    analysis = quoted_analysis(kind, shape=shape)
    service.validate_analysis_evidence(analysis, context)
    cleaned, removed = service.prune_unverified_analysis_evidence(analysis, context)
    assert removed == 0
    assert cleaned == analysis


@pytest.mark.parametrize("kind", ["audio", "audio+ocr"])
def test_unicode_quote_can_end_before_the_source_word_ends(service, kind):
    context = unicode_context(long_segments=True)
    analysis = quoted_analysis(kind, quote="ΟΣ")
    service.validate_analysis_evidence(analysis, context)
    cleaned, removed = service.prune_unverified_analysis_evidence(analysis, context)
    assert removed == 0
    assert cleaned == analysis


@pytest.mark.parametrize("kind", ["audio", "audio+ocr"])
def test_unicode_quote_rejects_wrong_time_and_repairs_to_actual_start(service, kind):
    context = unicode_context(long_segments=True)
    analysis = quoted_analysis(kind, timestamp=40000)
    with pytest.raises(JobStateError, match="指定时间戳"):
        service.validate_analysis_evidence(analysis, context)
    cleaned, removed = service.prune_unverified_analysis_evidence(analysis, context)
    assert removed == 0
    assert len(cleaned.knowledge_atoms) == 1
    assert cleaned.knowledge_atoms[0].quote == "ΟΣΑ"
    assert cleaned.knowledge_atoms[0].timestamp_ms == 0


@pytest.mark.parametrize("kind", ["audio", "audio+ocr"])
def test_unicode_case_matching_does_not_accept_different_alphabets(service, kind):
    analysis = quoted_analysis(kind, quote="OSA")
    with pytest.raises(JobStateError, match="指定时间戳"):
        service.validate_analysis_evidence(analysis, unicode_context())


@pytest.mark.parametrize("kind", ["audio", "audio+ocr"])
@pytest.mark.parametrize("models", [False, True])
def test_case_expansion_and_empty_segments_keep_each_quote_at_its_source_time(
    service, kind, models
):
    segments = [
        {"id": 0, "start_ms": 0, "end_ms": 1000, "text": "İß" * 3},
        {"id": 1, "start_ms": 1000, "end_ms": 1000, "text": "，。"},
        {"id": 2, "start_ms": 10000, "end_ms": 12000, "text": "ΟΣ"},
        {"id": 3, "start_ms": 12000, "end_ms": 14000, "text": "Α"},
        {"id": 4, "start_ms": 30000, "end_ms": 31000, "text": "ΟΣΑ"},
    ]
    context = {
        "metadata": {"source_kind": "video", "duration_seconds": 60},
        "transcript_corrected": (
            [TranscriptSegment(**item) for item in segments] if models else segments
        ),
        "ocr": [],
    }
    for timestamp in [10000, 30000]:
        analysis = quoted_analysis(kind, timestamp=timestamp)
        service.validate_analysis_evidence(analysis, context)
        cleaned, removed = service.prune_unverified_analysis_evidence(analysis, context)
        assert removed == 0
        assert cleaned == analysis
    service.validate_analysis_evidence(quoted_analysis(kind, quote="Α", timestamp=12000), context)
    for analysis in [
        quoted_analysis(kind, timestamp=0),
        quoted_analysis(kind, quote="Α", timestamp=10000),
    ]:
        with pytest.raises(JobStateError, match="指定时间戳"):
            service.validate_analysis_evidence(analysis, context)
