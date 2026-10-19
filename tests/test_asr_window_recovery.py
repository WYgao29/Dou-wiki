"""Retain complete recognition without promoting unreliable alignment times."""

import json

import numpy as np
import pytest

from douyin_wiki.adapters.asr_checkpoints import AsrCheckpoints
from douyin_wiki.adapters.asr_segments import (
    AsrSegmentationError,
    RecognitionWindow,
    bound_alignment_times,
    locate_anchor,
)
from douyin_wiki.adapters.qwen3_asr import Qwen3Transcriber
from douyin_wiki.config import MediaSettings
from douyin_wiki.errors import ExternalToolError
from tests.test_asr_segments import _token
from tests.test_qwen3_asr import FakeRuntime


@pytest.mark.parametrize("reason", ["anchor_distance", "disordered", "overflow"])
@pytest.mark.parametrize("policy", ["fail", "retain_window"])
async def test_verified_time_failures_keep_text_only_in_window_mode(
    tmp_path, monkeypatch, reason, policy
):
    windows = [RecognitionWindow(0, np.ones(16000)), RecognitionWindow(16000, np.ones(8000))]
    monkeypatch.setattr(
        "douyin_wiki.adapters.qwen3_asr.iter_recognition_windows", lambda *_: iter(windows)
    )

    def fail(*args, **kwargs):
        raise AsrSegmentationError("时间不可用", reason=reason)

    monkeypatch.setattr("douyin_wiki.adapters.qwen3_asr.segment_recognition_window", fail)
    engine = Qwen3Transcriber(
        MediaSettings(alignment_failure_policy=policy), runtime=FakeRuntime(text="完整原话。")
    )
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"original audio")
    if policy == "fail":
        with pytest.raises(ExternalToolError, match=reason):
            await engine.transcribe(audio, tmp_path / "cache")
        return
    rows = await engine.transcribe(audio, tmp_path / "cache")
    assert [(r.text, r.start_ms, r.end_ms) for r in rows] == [
        ("完整原话。", 0, 1000),
        ("完整原话。", 1000, 1500),
    ]
    assert all(r.timestamp_precision == "window" and r.alignment_reason == reason for r in rows)
    assert len(engine.last_trace) == 2


async def test_window_fallback_never_publishes_earlier_text_if_a_later_window_fails(
    tmp_path, monkeypatch
):
    def windows(*_):
        yield RecognitionWindow(0, np.ones(16000))
        raise AsrSegmentationError("后半段读取失败", reason="unreadable")

    monkeypatch.setattr("douyin_wiki.adapters.qwen3_asr.iter_recognition_windows", windows)

    def fail(*args, **kwargs):
        raise AsrSegmentationError("时间不可用", reason="overflow")

    monkeypatch.setattr("douyin_wiki.adapters.qwen3_asr.segment_recognition_window", fail)
    engine = Qwen3Transcriber(
        MediaSettings(alignment_failure_policy="retain_window"), runtime=FakeRuntime()
    )
    with pytest.raises(ExternalToolError, match="unreadable"):
        await engine.transcribe(tmp_path / "audio.wav", tmp_path / "cache")
    assert engine.provenance == {} and engine.last_trace == ()


@pytest.mark.parametrize("policy", ["fail", "retain_window"])
async def test_alignment_failure_diagnostics_survive_failure_or_degradation(
    tmp_path, monkeypatch, policy
):
    monkeypatch.setattr(
        "douyin_wiki.adapters.qwen3_asr.iter_recognition_windows",
        lambda *_: iter([RecognitionWindow(16000, np.ones(16000))]),
    )

    def fail(*args, **kwargs):
        raise AsrSegmentationError(
            "模型时间越界", reason="overflow", details={"token_index": 7, "overflow_ms": 160}
        )

    monkeypatch.setattr("douyin_wiki.adapters.qwen3_asr.segment_recognition_window", fail)
    engine = Qwen3Transcriber(MediaSettings(alignment_failure_policy=policy), runtime=FakeRuntime())
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"original audio")
    if policy == "fail":
        with pytest.raises(ExternalToolError, match="overflow"):
            await engine.transcribe(audio, tmp_path / "cache")
    else:
        await engine.transcribe(audio, tmp_path / "cache")
    paths = list((tmp_path / "cache").rglob("alignment_failure-*.json"))
    assert len(paths) == 1
    diagnostic = json.loads(paths[0].read_text())["payload"]
    assert diagnostic["reason"] == "overflow"
    assert diagnostic["start_sample"] == 16000 and diagnostic["samples"] == 16000
    assert diagnostic["details"] == {"token_index": 7, "overflow_ms": 160}


def test_overflow_diagnostic_identifies_word_and_actual_slice():
    with pytest.raises(AsrSegmentationError) as failed:
        bound_alignment_times(
            [_token("面", 1480, 1720)],
            [(0, 1)],
            window_limit_ms=1560,
            audio_start_sample=4787840,
        )
    assert failed.value.reason == "overflow"
    assert failed.value.details == {
        "token_index": 0,
        "edge": "end",
        "predicted_ms": 1720,
        "limit_ms": 1560,
        "overflow_ms": 160,
        "grid_ms": 80,
        "audio_start_sample": 4787840,
        "token_text": "面",
        "char_start": 0,
        "char_end": 1,
    }


def test_anchor_distance_diagnostic_keeps_match_distance():
    source = "".join(chr(0x4E80 + i) for i in range(100))
    query = source[:20] + "甲" * 10 + source[30:50]
    with pytest.raises(AsrSegmentationError) as failed:
        locate_anchor(source, query)
    assert failed.value.reason == "anchor_distance"
    assert failed.value.details["distance"] == 10
    assert failed.value.details["canonical_chars"] == 50


@pytest.mark.parametrize(
    "reason",
    [
        "coverage",
        "nonfinite",
        "negative",
        "length",
        "anchor_no_match",
        "anchor_ambiguous",
        "token_missing",
        "recognizer_result",
        "unreadable",
    ],
)
async def test_non_time_failures_still_fail_in_window_mode(tmp_path, monkeypatch, reason):
    monkeypatch.setattr(
        "douyin_wiki.adapters.qwen3_asr.iter_recognition_windows",
        lambda *_: iter([RecognitionWindow(0, np.ones(16000))]),
    )

    def fail(*args, **kwargs):
        raise AsrSegmentationError("无法确认原材料完整", reason=reason)

    monkeypatch.setattr("douyin_wiki.adapters.qwen3_asr.segment_recognition_window", fail)
    engine = Qwen3Transcriber(
        MediaSettings(alignment_failure_policy="retain_window"), runtime=FakeRuntime()
    )
    with pytest.raises(ExternalToolError, match=reason):
        await engine.transcribe(tmp_path / "audio.wav", tmp_path / "cache")
    assert engine.provenance == {}


@pytest.mark.parametrize("policy", ["fail", "retain_window"])
async def test_diagnostic_write_failure_preserves_original_result(tmp_path, monkeypatch, policy):
    monkeypatch.setattr(
        "douyin_wiki.adapters.qwen3_asr.iter_recognition_windows",
        lambda *_: iter([RecognitionWindow(0, np.ones(16000))]),
    )

    def fail(*args, **kwargs):
        raise AsrSegmentationError("模型时间越界", reason="overflow")

    original = AsrCheckpoints.write

    def write(self, stage, *args, **kwargs):
        if stage == "alignment_failure":
            raise OSError("诊断目录不可写")
        return original(self, stage, *args, **kwargs)

    monkeypatch.setattr("douyin_wiki.adapters.qwen3_asr.segment_recognition_window", fail)
    monkeypatch.setattr(AsrCheckpoints, "write", write)
    engine = Qwen3Transcriber(MediaSettings(alignment_failure_policy=policy), runtime=FakeRuntime())
    if policy == "fail":
        with pytest.raises(ExternalToolError, match="overflow"):
            await engine.transcribe(tmp_path / "audio.wav", tmp_path / "cache")
    else:
        rows = await engine.transcribe(tmp_path / "audio.wav", tmp_path / "cache")
        assert rows[0].timestamp_precision == "window"
