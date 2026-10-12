from __future__ import annotations

import numpy as np
import pytest

from douyin_wiki.adapters.asr_segments import (
    SAMPLE_RATE,
    AsrSegmentationError,
    OperationalToken,
    RecognitionWindow,
    choose_shared_boundary,
    segment_recognition_window,
)
from douyin_wiki.adapters.qwen3_asr import Qwen3Transcriber
from tests.test_asr_segments import (
    ScriptedAligner,
    ScriptedRecognizer,
    _anomalous_alignment,
    _token,
)
from tests.test_qwen3_asr import FakeRuntime, settings


def test_sparse_speech_under_alignment_limit_does_not_require_a_long_anchor():
    source = "上厕所。"
    recognizer = ScriptedRecognizer(["哦。"])
    aligner = ScriptedAligner({source: [_token("上", 1200, 1440), _token("厕所", 1440, 2000)]})
    result = segment_recognition_window(
        np.zeros(172 * SAMPLE_RATE, dtype=np.float32),
        source,
        recognizer=recognizer,
        aligner=aligner,
    )
    assert recognizer.calls == []
    assert result.boundaries == ()
    assert "".join(segment.text for segment in result.segments) == source


def test_one_sample_past_alignment_limit_still_splits_before_model_call():
    source = "".join(chr(0x4E80 + i) for i in range(400))
    query = source[200:240]
    aligner = ScriptedAligner(
        {query: [_token(char, i * 750, (i + 1) * 750) for i, char in enumerate(query)]}
    )
    result = segment_recognition_window(
        np.zeros(180 * SAMPLE_RATE + 1, dtype=np.float32),
        source,
        recognizer=ScriptedRecognizer([query]),
        aligner=aligner,
    )
    assert len(result.boundaries) == 1
    assert all(samples <= 180 * SAMPLE_RATE for samples, _ in aligner.calls)
    assert "".join(segment.text for segment in result.segments) == source


def test_short_local_anchor_can_expand_without_changing_source():
    source, broken, query, local = _anomalous_alignment()
    expanded = [_token(t.text, t.native_start_ms + 15000, t.native_end_ms + 15000) for t in local]
    recognizer = ScriptedRecognizer(["哦。", query])
    result = segment_recognition_window(
        np.zeros(150 * SAMPLE_RATE, dtype=np.float32),
        source,
        recognizer=recognizer,
        aligner=ScriptedAligner({source: broken, query: expanded}),
    )
    assert [samples for samples, _ in recognizer.calls] == [30 * SAMPLE_RATE, 60 * SAMPLE_RATE]
    assert len(result.retries) == 1
    assert "".join(segment.text for segment in result.segments) == source


def test_failed_local_prediction_can_realign_unchanged_short_source_context():
    source, broken, query, local = _anomalous_alignment()
    local[14] = _token(source[54], 15000, 15000, raw=(15000, 11000))
    context = source[46:63]
    offset = 22500
    good = [_token(source[i], i * 500 - offset, i * 500 - offset + 500) for i in range(46, 63)]
    good[8] = _token(source[54], 27120 - offset, 27440 - offset)
    for i in range(55, 63):
        start = 32000 + (i - 55) * 500 - offset
        good[i - 46] = _token(source[i], start, start + 500)
    recognizer = ScriptedRecognizer([query])
    aligner = ScriptedAligner({source: broken, query: local, context: good})
    result = segment_recognition_window(
        np.zeros(150 * SAMPLE_RATE, dtype=np.float32),
        source,
        recognizer=recognizer,
        aligner=aligner,
    )
    assert len(recognizer.calls) == 1
    assert aligner.calls[-1][1] == context
    assert result.retries[0].replacement.native_start_ms == 27120
    assert "".join(segment.text for segment in result.segments) == source


@pytest.mark.asyncio
async def test_sparse_alignment_keeps_full_recognition_and_absolute_audio_offset(
    monkeypatch, tmp_path
):
    class SparseRuntime(FakeRuntime):
        def __init__(self):
            super().__init__(text="上厕所。")
            self.recognition_samples = []
            self.alignment_samples = []

        def speech_ranges(self, audio, *, complete=False):
            return [(18 * SAMPLE_RATE, 21 * SAMPLE_RATE)]

        def recognize(self, audio, **kwargs):
            self.recognition_samples.append(len(audio))
            return super().recognize(audio, **kwargs)

        def align(self, audio, text, *, language):
            self.alignment_samples.append(len(audio))
            return super().align(audio, text, language=language)

    runtime = SparseRuntime()
    window = RecognitionWindow(600 * SAMPLE_RATE, np.ones(172 * SAMPLE_RATE))
    monkeypatch.setattr(
        "douyin_wiki.adapters.qwen3_asr.iter_recognition_windows", lambda *_: iter([window])
    )
    engine = Qwen3Transcriber(settings(), runtime=runtime)
    result = await engine.transcribe(tmp_path / "unused.wav", tmp_path)
    assert runtime.recognition_samples == [172 * SAMPLE_RATE]
    assert runtime.alignment_samples == [3 * SAMPLE_RATE]
    assert [(s.start_ms, s.end_ms, s.text) for s in result] == [(618000, 619000, "上厕所。")]
    assert engine.last_trace[0]["alignment_start_sample"] == 618 * SAMPLE_RATE


def test_main_alignment_can_prefer_sentence_gap_over_nearest_word_end():
    source = "甲乙。丙"
    tokens = (
        OperationalToken("甲", 0, 150000, 0, 150000, 0, 150000, 0, 1),
        OperationalToken("乙", 150000, 151000, 150000, 151000, 150000, 151000, 1, 3),
        OperationalToken("丙", 151400, 160000, 151400, 160000, 151400, 160000, 3, 4),
    )
    cut = choose_shared_boundary(
        source,
        tokens,
        nominal_ms=150000,
        audio_end_sample=200 * SAMPLE_RATE,
        prefer_pause=True,
    )
    assert cut.sample == 151200 * SAMPLE_RATE // 1000
    assert cut.char_index == 3
    assert cut.preference == "punctuation"


def test_one_local_prediction_repairs_a_group_of_more_than_four_bad_words():
    source, broken, query, local = _anomalous_alignment()
    for index in range(55, 60):
        good = broken[index]
        broken[index] = _token(
            good.text,
            good.native_start_ms,
            good.native_end_ms,
            raw=(good.raw_start_ms, 0),
        )
    recognizer = ScriptedRecognizer([query])
    result = segment_recognition_window(
        np.zeros(150 * SAMPLE_RATE, dtype=np.float32),
        source,
        recognizer=recognizer,
        aligner=ScriptedAligner({source: broken, query: local}),
    )
    assert len(recognizer.calls) == 1
    assert len(result.retries) == 1
    assert len(result.retries[0].neighbor_replacements) == 5
    assert "".join(segment.text for segment in result.segments) == source


def test_unrelated_bad_local_prediction_does_not_replace_or_block_a_good_source_word():
    source, broken, query, local = _anomalous_alignment()
    unrelated = local[2]
    local[2] = _token(
        unrelated.text,
        unrelated.native_start_ms,
        unrelated.native_end_ms,
        raw=(unrelated.raw_start_ms, unrelated.raw_end_ms + 4000),
    )
    result = segment_recognition_window(
        np.zeros(150 * SAMPLE_RATE, dtype=np.float32),
        source,
        recognizer=ScriptedRecognizer([query]),
        aligner=ScriptedAligner({source: broken, query: local}),
    )
    assert len(result.retries) == 1
    assert result.retries[0].neighbor_replacements == ()
    assert "".join(segment.text for segment in result.segments) == source


@pytest.fixture
def two_anomalous_groups():
    source, broken, query, local = _anomalous_alignment()
    broken[79] = _token(source[79], 44000, 44500, raw=(44000, 0))
    # Word 80 is outside this prediction, but needed to join the new word 79.
    local[-1] = _token(source[79], 44000 - 15320, 44600 - 15320)
    query2 = source[60:100]
    local2 = [
        _token(t.text, t.native_start_ms - 29000, t.native_end_ms - 29000)
        for t in broken[60:100]
    ]
    local2[19] = _token(source[79], 15000, 15600)
    local2[20] = _token(source[80], 15600, 16000)
    return source, broken, query, local, query2, local2


@pytest.mark.parametrize("secondary_failure", ["missing_neighbor", "overlapping"])
def test_unmergeable_extra_group_waits_for_its_own_local_prediction(
    secondary_failure, two_anomalous_groups
):
    source, broken, query, local, query2, local2 = two_anomalous_groups
    if secondary_failure == "overlapping":
        # Word 78 is present, but its new end overlaps word 79's new start.
        local[-1] = _token(source[79], 43000 - 15320, 44500 - 15320)
    recognizer = ScriptedRecognizer([query, query2])
    aligner = ScriptedAligner({source: broken, query: local, query2: local2})
    result = segment_recognition_window(
        np.zeros(150 * SAMPLE_RATE, dtype=np.float32),
        source,
        recognizer=recognizer,
        aligner=aligner,
    )
    assert len(recognizer.calls) == 2
    assert len(aligner.calls) == 3
    assert [retry.token_index for retry in result.retries] == [54, 79]
    assert result.retries[0].neighbor_replacements == ()
    assert result.retries[1].original == broken[79]
    assert [item.token_index for item in result.retries[1].neighbor_replacements] == [80]
    assert "".join(segment.text for segment in result.segments) == source


def test_deferred_extra_group_still_fails_if_its_own_prediction_cannot_join(two_anomalous_groups):
    source, broken, query, local, query2, local2 = two_anomalous_groups
    local2[19] = _token(source[79], 15000, 17000)
    recognizer = ScriptedRecognizer([query, query2])
    with pytest.raises(AsrSegmentationError) as failed:
        segment_recognition_window(
            np.zeros(150 * SAMPLE_RATE, dtype=np.float32),
            source,
            recognizer=recognizer,
            aligner=ScriptedAligner({source: broken, query: local, query2: local2}),
        )
    assert failed.value.reason == "disordered"
    assert len(recognizer.calls) == 2


@pytest.mark.parametrize(
    ("gap_start_ms", "gap_end_ms", "nominal_ms", "previous_sample", "audio_end_sample", "cut_ms"),
    [
        (164960, 169920, 150000, 0, 240 * SAMPLE_RATE, 165000),
        (130000, 136000, 150000, 0, 240 * SAMPLE_RATE, 135000),
        (179000, 185000, 180000, 1, 240 * SAMPLE_RATE, 180000),
        (159000, 162000, 150000, 0, 341 * SAMPLE_RATE + 1, 161001),
        (100000, 110000, 105000, 105 * SAMPLE_RATE, 200 * SAMPLE_RATE, 105001),
        (100000, 110000, 105000, 0, 105 * SAMPLE_RATE, 104999),
    ],
)
def test_pause_cut_uses_intersection_of_gap_and_all_time_limits(
    gap_start_ms, gap_end_ms, nominal_ms, previous_sample, audio_end_sample, cut_ms
):
    tokens = (
        OperationalToken("甲", 0, gap_start_ms, 0, gap_start_ms, 0, gap_start_ms, 0, 1),
        OperationalToken(
            "乙", gap_end_ms, gap_end_ms, gap_end_ms, gap_end_ms, gap_end_ms, gap_end_ms, 1, 2
        ),
    )
    cut = choose_shared_boundary(
        "甲乙",
        tokens,
        nominal_ms=nominal_ms,
        previous_sample=previous_sample,
        audio_end_sample=audio_end_sample,
        prefer_pause=True,
    )
    assert cut.sample == cut_ms * SAMPLE_RATE // 1000
    assert cut.delta_ms == abs(cut_ms - nominal_ms)
    assert cut.char_index == 1
    assert previous_sample < cut.sample < audio_end_sample
    assert cut.sample - previous_sample <= 180 * SAMPLE_RATE
    assert audio_end_sample - cut.sample <= 180 * SAMPLE_RATE


def test_shifted_anchor_keeps_a_legal_cut_when_pause_midpoint_exceeds_slack():
    query = "a" * 21 + "." + "b" * 21
    source = "甲" * 40 + query + "乙" * 40
    recognizer = ScriptedRecognizer([query.replace("a" * 10, "x" * 10, 1), query])
    aligner = ScriptedAligner(
        {query: [_token("a" * 21, 0, 24960), _token("b" * 21, 29920, 30000)]}
    )
    result = segment_recognition_window(
        np.zeros(240 * SAMPLE_RATE, dtype=np.float32),
        source,
        recognizer=recognizer,
        aligner=aligner,
    )
    assert len(recognizer.calls) == 2
    assert result.boundaries[0].anchor_shift_ms == 5000
    assert result.boundaries[0].sample == 165 * SAMPLE_RATE
    assert all(samples <= 180 * SAMPLE_RATE for samples, _ in aligner.calls)
    assert "".join(segment.text for segment in result.segments) == source


@pytest.mark.parametrize("blocked_by", ["slack", "window_length", "protected_span"])
def test_pause_cut_still_rejects_gaps_without_a_legal_intersection(blocked_by):
    start, end, audio_end = 164960, 169920, 240 * SAMPLE_RATE
    if blocked_by == "slack":
        start, end = 166000, 170000
    elif blocked_by == "window_length":
        audio_end = 346 * SAMPLE_RATE
    tokens = (
        OperationalToken("甲", 0, start, 0, start, 0, start, 0, 1),
        OperationalToken("乙", end, end, end, end, end, end, 1, 2),
    )
    with pytest.raises(AsrSegmentationError) as failed:
        choose_shared_boundary(
            "甲乙",
            tokens,
            nominal_ms=150000,
            audio_end_sample=audio_end,
            protected_spans=((0, 2),) if blocked_by == "protected_span" else (),
            prefer_pause=True,
        )
    assert failed.value.reason == "no_legal_cut"
