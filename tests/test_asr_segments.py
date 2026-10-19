"""Model-free tests for recognition windows, source anchors, and display spans."""

from __future__ import annotations

import ast
import inspect
import math
import subprocess
import sys
import types
from pathlib import Path

import numpy as np
import pytest

from douyin_wiki.adapters.asr_segments import (
    ANCHOR_MAX_TOKENS,
    SAMPLE_RATE,
    AlignedToken,
    AnchorMatch,
    AsrSegmentationError,
    AudioSplitter,
    BoundaryTrace,
    DisplaySegment,
    OperationalToken,
    QwenEnergySplitter,
    RecognitionResult,
    RecognitionWindow,
    TimeLimit,
    WindowSegmentation,
    bound_alignment_times,
    choose_shared_boundary,
    group_display_segments,
    iter_recognition_windows,
    locate_anchor,
    map_source_spans,
    nominal_boundary_ms,
    samples_to_ms,
    segment_recognition_window,
    time_limit_summary,
)
from douyin_wiki.adapters.asr_text import render_normalized_slices

WINDOW_SAMPLES = 600 * SAMPLE_RATE
TAIL_SAMPLES = SAMPLE_RATE


def _marker(index: int) -> np.float32:
    return np.float32((index % 10007) / np.float32(10007))


def _marked(start: int, count: int) -> np.ndarray:
    indexes = np.arange(start, start + count)
    return ((indexes % 10007) / np.float32(10007)).astype(np.float32)


class MemoryWave:
    """SoundFile stand-in. `read()` without a positive count is rejected."""

    def __init__(
        self,
        samples: np.ndarray | None = None,
        *,
        frames: int | None = None,
        samplerate: int = SAMPLE_RATE,
        channels: int = 1,
        synthetic: bool = False,
    ) -> None:
        if synthetic:
            self.frames = int(frames or 0)
            self._samples = None
        else:
            audio = np.zeros(0, dtype=np.float32) if samples is None else samples
            self._samples = np.asarray(audio, dtype=np.float32)
            self.frames = int(len(self._samples) if frames is None else frames)
        self.samplerate = samplerate
        self.channels = channels
        self.reads: list[int] = []
        self._pos = 0

    def __enter__(self) -> MemoryWave:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def read(self, frames: int = -1, dtype: str = "float32", always_2d: bool = False) -> np.ndarray:
        if frames is None or int(frames) < 1:
            raise AssertionError(f"unbounded read {frames!r}")
        count = int(frames)
        self.reads.append(count)
        available = self.frames - self._pos
        count = min(count, available)
        start = self._pos
        self._pos += count
        if self._samples is None:
            return _marked(start, count)
        return np.asarray(self._samples[start : start + count], dtype=np.float32)


class CutSplitter:
    def __init__(self, tail: int = TAIL_SAMPLES, *, pieces: int = 2) -> None:
        self.tail = tail
        self.pieces = pieces
        self.calls: list[tuple[int, float]] = []

    def split_audio(
        self, audio: np.ndarray, *, window_seconds: float
    ) -> list[tuple[np.ndarray, int]]:
        self.calls.append((len(audio), window_seconds))
        if self.pieces == 2:
            cut = len(audio) - self.tail
            return [(audio[:cut], 0), (audio[cut:], cut)]
        size = len(audio) // self.pieces
        chunks = []
        cursor = 0
        for index in range(self.pieces):
            end = len(audio) if index == self.pieces - 1 else cursor + size
            chunks.append((audio[cursor:end], cursor))
            cursor = end
        return chunks


def _opener(wave: MemoryWave):
    def open_path(_path: object) -> MemoryWave:
        return wave

    return open_path


def _windows(wave: MemoryWave, splitter: AudioSplitter | None = None) -> list[RecognitionWindow]:
    return list(
        iter_recognition_windows("memory.wav", splitter or CutSplitter(), opener=_opener(wave))
    )


def _assert_coverage(windows: list[RecognitionWindow], total: int) -> None:
    cursor = 0
    for window in windows:
        assert window.start_sample == cursor
        assert 0 < len(window.audio) <= WINDOW_SAMPLES
        assert window.audio.dtype == np.float32
        assert window.audio.ndim == 1
        assert window.audio[0] == _marker(cursor)
        assert window.audio[-1] == _marker(cursor + len(window.audio) - 1)
        cursor += len(window.audio)
    assert cursor == total


def _token(
    text: str,
    start: int,
    end: int,
    *,
    raw: tuple[int, int] | None = None,
    native: tuple[int, int] | None = None,
) -> AlignedToken:
    raw_start, raw_end = (start, end) if raw is None else raw
    native_start, native_end = (start, end) if native is None else native
    return AlignedToken(text, raw_start, raw_end, native_start, native_end)


def _operational(
    source: str,
    texts: list[str],
    times: list[tuple[int, int]],
) -> tuple[OperationalToken, ...]:
    spans = map_source_spans(source, texts)
    assert len(spans) == len(times)
    tokens = []
    for (char_start, char_end), (start_ms, end_ms) in zip(spans, times, strict=True):
        tokens.append(
            OperationalToken(
                source[char_start:char_end],
                start_ms,
                end_ms,
                start_ms,
                end_ms,
                start_ms,
                end_ms,
                char_start,
                char_end,
            )
        )
    return tuple(tokens)


def _even_alignment(text: str, duration_ms: int) -> list[AlignedToken]:
    count = max(1, math.ceil(duration_ms / 30_000))
    if count > len(text):
        raise AssertionError(f"cannot split {text!r} into {count} positive pieces")
    sizes = [len(text) // count] * count
    for index in range(len(text) % count):
        sizes[index] += 1
    edges = [round(index * duration_ms / count) for index in range(count + 1)]
    tokens = []
    cursor = 0
    for size, start_ms, end_ms in zip(sizes, edges[:-1], edges[1:], strict=True):
        tokens.append(_token(text[cursor : cursor + size], start_ms, end_ms))
        cursor += size
    assert cursor == len(text)
    return tokens


class ScriptedRecognizer:
    def __init__(self, queries: list[str]) -> None:
        self.queries = list(queries)
        self.calls: list[tuple[int, int]] = []

    def recognize(self, audio: np.ndarray, *, max_tokens: int) -> RecognitionResult:
        self.calls.append((len(audio), max_tokens))
        if not self.queries:
            raise AssertionError("unexpected recognition")
        return RecognitionResult(self.queries.pop(0))


class ScriptedAligner:
    def __init__(self, anchor_tokens: dict[str, list[AlignedToken]] | None = None) -> None:
        self.anchor_tokens = anchor_tokens or {}
        self.calls: list[tuple[int, str]] = []

    def align(self, audio: np.ndarray, text: str) -> list[AlignedToken]:
        self.calls.append((len(audio), text))
        if text in self.anchor_tokens:
            return self.anchor_tokens[text]
        return _even_alignment(text, samples_to_ms(len(audio)))


def test_import_does_not_load_soundfile_or_mlx() -> None:
    # Use a separate interpreter: replacing sys.modules here leaves the other
    # tests holding functions from a different module than monkeypatch targets.
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import douyin_wiki.adapters.asr_segments; "
            "assert not any(name in sys.modules for name in ('soundfile', 'mlx', 'mlx_audio'))",
        ],
        check=True,
    )
    source_path = Path(__file__).parents[1] / "src/douyin_wiki/adapters/asr_segments.py"
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        assert all(not name.startswith(("mlx", "soundfile")) for name in names)


def test_windows_zero_samples_yield_nothing() -> None:
    wave = MemoryWave(np.zeros(0, dtype=np.float32))
    splitter = CutSplitter()
    assert _windows(wave, splitter) == []
    assert wave.reads == []
    assert splitter.calls == []


def test_windows_exactly_600_seconds_stay_one_window() -> None:
    total = WINDOW_SAMPLES
    wave = MemoryWave(frames=total, synthetic=True)
    splitter = CutSplitter()
    windows = _windows(wave, splitter)
    assert splitter.calls == []
    assert [len(item.audio) for item in windows] == [total]
    assert windows[0].start_sample == 0
    _assert_coverage(windows, total)
    assert all(count <= WINDOW_SAMPLES for count in wave.reads)


def test_windows_tail_and_cross_window_samples_are_covered_once() -> None:
    extra = 5 * SAMPLE_RATE
    total = WINDOW_SAMPLES + extra
    wave = MemoryWave(frames=total, synthetic=True)
    splitter = CutSplitter(tail=TAIL_SAMPLES)
    windows = _windows(wave, splitter)
    assert [len(item.audio) for item in windows] == [
        WINDOW_SAMPLES - TAIL_SAMPLES,
        TAIL_SAMPLES + extra,
    ]
    assert windows[1].start_sample == WINDOW_SAMPLES - TAIL_SAMPLES
    _assert_coverage(windows, total)
    assert splitter.calls == [(WINDOW_SAMPLES, 600)]
    assert all(length <= WINDOW_SAMPLES for length, _seconds in splitter.calls)
    assert all(count >= 1 for count in wave.reads)


def test_windows_keep_only_the_uncommitted_splitter_tail() -> None:
    extra = 10 * SAMPLE_RATE
    total = WINDOW_SAMPLES + extra
    wave = MemoryWave(frames=total, synthetic=True)
    splitter = CutSplitter(pieces=3)
    windows = _windows(wave, splitter)
    first = WINDOW_SAMPLES // 3
    second = WINDOW_SAMPLES // 3
    assert [item.start_sample for item in windows] == [0, first, first + second]
    assert [len(item.audio) for item in windows] == [
        first,
        second,
        total - first - second,
    ]
    _assert_coverage(windows, total)


@pytest.mark.parametrize(
    "total",
    [120 * 60 * SAMPLE_RATE, 120 * 60 * SAMPLE_RATE + 1, 180 * 60 * SAMPLE_RATE],
    ids=["two_hours", "past_two_hours", "three_hours"],
)
def test_long_audio_windows_keep_reads_bounded_and_cover_entire_source(total: int) -> None:
    wave = MemoryWave(frames=total, synthetic=True)
    splitter = CutSplitter(tail=TAIL_SAMPLES)
    covered = 0
    window_count = 0
    for window in iter_recognition_windows("memory.wav", splitter, opener=_opener(wave)):
        assert window.start_sample == covered
        assert 0 < len(window.audio) <= WINDOW_SAMPLES
        assert window.audio[0] == _marker(covered)
        assert window.audio[-1] == _marker(covered + len(window.audio) - 1)
        if window_count == 0:
            assert len(window.audio) == WINDOW_SAMPLES - TAIL_SAMPLES
        covered += len(window.audio)
        window_count += 1
    assert covered == total
    assert window_count > 1
    assert all(count <= WINDOW_SAMPLES for count in wave.reads)
    assert all(length <= WINDOW_SAMPLES for length, seconds in splitter.calls)
    assert {seconds for _length, seconds in splitter.calls} == {600}


def test_windows_reject_bad_containers() -> None:
    splitter = CutSplitter()
    with pytest.raises(AsrSegmentationError) as bad_rate:
        _windows(MemoryWave(np.zeros(SAMPLE_RATE, dtype=np.float32), samplerate=8000), splitter)
    assert bad_rate.value.reason == "sample_rate"

    stereo = MemoryWave(np.zeros(SAMPLE_RATE, dtype=np.float32), channels=2)
    with pytest.raises(AsrSegmentationError) as bad_channels:
        _windows(stereo, splitter)
    assert bad_channels.value.reason == "channels"
    assert stereo.reads == []

    negative_length = MemoryWave(frames=-1, synthetic=True)
    with pytest.raises(AsrSegmentationError) as bad_length:
        _windows(negative_length, splitter)
    assert bad_length.value.reason == "length"
    assert negative_length.reads == []

    short = MemoryWave(np.zeros(10, dtype=np.float32), frames=1000)
    with pytest.raises(AsrSegmentationError) as truncated:
        _windows(short, splitter)
    assert truncated.value.reason == "length"


def test_windows_reject_unreadable_and_real_header_errors(tmp_path: Path) -> None:
    def explode(_path: object) -> MemoryWave:
        raise RuntimeError("bad header")

    with pytest.raises(AsrSegmentationError) as opened:
        list(iter_recognition_windows("missing.wav", CutSplitter(), opener=explode))
    assert opened.value.reason == "unreadable"

    garbage = tmp_path / "broken.wav"
    garbage.write_bytes(b"not a wav")
    with pytest.raises(AsrSegmentationError) as native:
        list(iter_recognition_windows(garbage, CutSplitter()))
    assert native.value.reason == "unreadable"


def test_windows_reject_splitter_that_breaks_the_limit_or_coverage() -> None:
    total = WINDOW_SAMPLES + SAMPLE_RATE
    wave = MemoryWave(frames=total, synthetic=True)

    class LongSplitter:
        def split_audio(
            self, audio: np.ndarray, *, window_seconds: float
        ) -> list[tuple[np.ndarray, int]]:
            assert window_seconds == 600
            longer = np.concatenate([audio, np.zeros(SAMPLE_RATE, dtype=np.float32)])
            return [(longer, 0)]

    with pytest.raises(AsrSegmentationError) as too_long:
        _windows(wave, LongSplitter())
    assert too_long.value.reason == "window_too_long"

    class GapSplitter:
        def split_audio(
            self, audio: np.ndarray, *, window_seconds: float
        ) -> list[tuple[np.ndarray, int]]:
            return [(audio[:SAMPLE_RATE], 0)]

    with pytest.raises(AsrSegmentationError) as gap:
        _windows(MemoryWave(frames=total, synthetic=True), GapSplitter())
    assert gap.value.reason == "splitter_coverage"

    class RewrittenSplitter:
        def split_audio(
            self, audio: np.ndarray, *, window_seconds: float
        ) -> list[tuple[np.ndarray, int]]:
            changed = np.array(audio, copy=True)
            changed[0] = np.float32(changed[0] + 1)
            return [(changed, 0)]

    with pytest.raises(AsrSegmentationError) as rewritten:
        _windows(MemoryWave(frames=total, synthetic=True), RewrittenSplitter())
    assert rewritten.value.reason == "splitter_samples"


def test_windows_default_opener_reads_a_real_wav_without_full_read(tmp_path: Path) -> None:
    soundfile = pytest.importorskip("soundfile")
    path = tmp_path / "mono.wav"
    audio = _marked(0, SAMPLE_RATE)
    soundfile.write(path, audio, SAMPLE_RATE, format="WAV", subtype="FLOAT")
    splitter = CutSplitter()
    windows = list(iter_recognition_windows(path, splitter))
    assert splitter.calls == []
    assert len(windows) == 1
    assert windows[0].start_sample == 0
    assert len(windows[0].audio) == SAMPLE_RATE
    assert np.allclose(windows[0].audio, audio)

    stereo = tmp_path / "stereo.wav"
    soundfile.write(stereo, np.zeros((SAMPLE_RATE, 2), dtype=np.float32), SAMPLE_RATE)
    with pytest.raises(AsrSegmentationError) as channels:
        list(iter_recognition_windows(stereo, splitter))
    assert channels.value.reason == "channels"

    wrong_rate = tmp_path / "8k.wav"
    soundfile.write(wrong_rate, np.zeros(8000, dtype=np.float32), 8000)
    with pytest.raises(AsrSegmentationError) as rate:
        list(iter_recognition_windows(wrong_rate, splitter))
    assert rate.value.reason == "sample_rate"


def test_energy_splitter_uses_fixed_upstream_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_split(wav, sr, chunk_duration, min_chunk_duration, search_expand_sec, min_window_ms):
        captured.update(
            {
                "sr": sr,
                "chunk_duration": chunk_duration,
                "min_chunk_duration": min_chunk_duration,
                "search_expand_sec": search_expand_sec,
                "min_window_ms": min_window_ms,
                "length": len(wav),
            }
        )
        mid = len(wav) // 2
        return [(wav[:mid], 0.0), (wav[mid:], 123.456)]

    leaf_name = "mlx_audio.stt.models.qwen3_asr.qwen3_asr"
    previous = {name: sys.modules.get(name) for name in _mlx_audio_names(leaf_name)}
    leaf = _install_fake_module(leaf_name)
    leaf.split_audio_into_chunks = fake_split
    try:
        audio = np.arange(8, dtype=np.float32)
        pieces = QwenEnergySplitter().split_audio(audio, window_seconds=600)
    finally:
        for name, module in previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
    assert captured == {
        "sr": SAMPLE_RATE,
        "chunk_duration": 599,
        "min_chunk_duration": 0,
        "search_expand_sec": 1,
        "min_window_ms": 20,
        "length": 8,
    }
    assert pieces[0][1] == 0
    assert pieces[1][1] == 4
    assert np.array_equal(pieces[0][0], audio[:4])
    assert np.array_equal(pieces[1][0], audio[4:])
    assert "mlx" not in sys.modules


def _mlx_audio_names(leaf: str) -> list[str]:
    names = []
    parts = leaf.split(".")
    for index in range(len(parts)):
        names.append(".".join(parts[: index + 1]))
    return names


def _install_fake_module(leaf: str) -> types.ModuleType:
    parent = None
    module = None
    for name in _mlx_audio_names(leaf):
        module = types.ModuleType(name)
        sys.modules[name] = module
        if parent is not None:
            setattr(parent, name.rsplit(".", 1)[-1], module)
        parent = module
    assert module is not None
    return module


def test_match_ignores_case_space_and_punctuation_but_returns_source() -> None:
    head = "甲" * 36
    source = f"前{head}So，  Fable\t5。尾"
    query = f"{head}so fable 5"
    match = locate_anchor(source, query)
    assert isinstance(match, AnchorMatch)
    assert source[match.source_start : match.source_end] == f"{head}So，  Fable\t5"
    assert "so fable" not in source[match.source_start : match.source_end]
    assert match.distance == 0
    assert match.canonical_chars == len(head) + len("sofable5")


def test_match_casefold_expansion_uses_source_offsets() -> None:
    head = "甲" * 20
    tail = "乙" * 20
    source = f"前{head}Straße{tail}后"
    match = locate_anchor(source, f"{head}strasse{tail}")
    assert source[match.source_start : match.source_end] == f"{head}Straße{tail}"
    dotted = "İ"
    assert len(dotted.casefold()) == 2
    source = f"前{head}{dotted}{tail}后"
    match = locate_anchor(source, f"{head}{dotted.casefold()}{tail}")
    assert source[match.source_start : match.source_end] == f"{head}{dotted}{tail}"
    assert match.source_end - match.source_start == len(head) + 1 + len(tail)


def test_match_rejects_short_distant_ambiguous_and_missing_queries() -> None:
    with pytest.raises(AsrSegmentationError) as short:
        locate_anchor("甲" * 39, "甲" * 39)
    assert short.value.reason == "anchor_too_short"

    with pytest.raises(AsrSegmentationError) as distant:
        locate_anchor("甲" * 40, "乙" * 5 + "甲" * 35)
    assert distant.value.reason == "anchor_distance"

    with pytest.raises(AsrSegmentationError) as missing:
        locate_anchor("甲" * 40, "乙" * 40)
    assert missing.value.reason == "anchor_no_match"

    phrase = "甲" * 40
    with pytest.raises(AsrSegmentationError) as ambiguous:
        locate_anchor(phrase + "乙" + phrase, phrase)
    assert ambiguous.value.reason == "anchor_ambiguous"

    near = locate_anchor("乙" * 4 + "甲" * 36, "甲" * 40)
    assert near.distance == 4
    assert near.canonical_chars == 40


def test_match_accepts_exact_rate_and_rejects_a_nonoverlapping_rival() -> None:
    exact = locate_anchor("甲" * 100, "乙" * 12 + "甲" * 88)
    assert exact.distance == 12
    assert exact.canonical_chars == 100
    with pytest.raises(AsrSegmentationError) as over:
        locate_anchor("甲" * 100, "乙" * 13 + "甲" * 87)
    assert over.value.reason == "anchor_distance"

    rival = "甲" * 100 + "分隔" + ("甲" * 92 + "丁" * 8)
    with pytest.raises(AsrSegmentationError) as close:
        locate_anchor(rival, "甲" * 100)
    assert close.value.reason == "anchor_ambiguous"

    accepted = locate_anchor("甲" * 100 + "分隔" + ("甲" * 91 + "丁" * 9), "甲" * 100)
    assert accepted.distance == 0
    assert accepted.source_start == 0
    assert accepted.source_end == 100


def test_match_overlapping_second_window_is_not_a_rival() -> None:
    match = locate_anchor("甲" * 40 + "乙" * 40, "甲" * 40)
    assert match.source_start == 0
    assert match.source_end == 40
    assert match.distance == 0


def test_match_does_not_normalize_numbers(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(_text: str) -> str:
        raise AssertionError("anchor matching normalized text")

    monkeypatch.setattr("douyin_wiki.adapters.asr_text.normalize_zh_numbers", forbidden)
    monkeypatch.setattr("douyin_wiki.adapters.asr_text.normalize_asr_text", forbidden)
    source = "百分之五十" + "甲" * 40
    match = locate_anchor(source, "百分之五十" + "甲" * 40)
    assert source[match.source_start : match.source_end] == source


@pytest.mark.parametrize(
    ("duration_ms", "expected"),
    [
        (369_779, (150_000, 300_000)),
        (397_038, (150_000, 300_000)),
        (200_000, (150_000,)),
        (165_000, ()),
        (180_000, ()),
        (180_001, (150_000,)),
        (164_999, ()),
        (600_000, (150_000, 300_000, 450_000)),
    ],
)
def test_align_nominal_boundaries(duration_ms: int, expected: tuple[int, ...]) -> None:
    assert nominal_boundary_ms(duration_ms) == expected


def test_align_maps_words_in_order_and_keeps_punctuation() -> None:
    source = "So，Fable 5。"
    spans = map_source_spans(source, ["So", "Fable", "5"])
    assert "".join(source[start:end] for start, end in spans) == source
    assert [source[start:end] for start, end in spans] == ["So，", "Fable ", "5。"]

    repeated = "甲。甲"
    assert map_source_spans(repeated, ["甲", "甲"]) == ((0, 2), (2, 3))
    assert map_source_spans("，你好。", ["你好"]) == ((0, 4),)

    with pytest.raises(AsrSegmentationError) as skipped:
        map_source_spans("甲乙丙", ["甲", "丙"])
    assert skipped.value.reason == "kept_character_skipped"
    with pytest.raises(AsrSegmentationError) as missing:
        map_source_spans("甲乙", ["丙"])
    assert missing.value.reason == "token_missing"


def test_align_repeated_text_uses_token_times_not_character_ratio() -> None:
    source = "甲" * 4
    tokens = _operational(source, ["甲甲", "甲甲"], [(0, 1000), (1000, 9000)])
    # 8s keeps the 8s token at the cap and stops it merging with the 1s token.
    # A character-ratio cut of this 9s source would land at 4500ms.
    segments = group_display_segments(source, tokens, segment_seconds=8, duration_ms=9000)
    assert [(item.start_ms, item.end_ms, item.text) for item in segments] == [
        (0, 1000, "甲甲"),
        (1000, 9000, "甲甲"),
    ]
    assert all(item.end_ms != 4500 for item in segments)


def test_align_shared_cut_stays_near_nominal_and_prefers_punctuation() -> None:
    source = "甲" * 10 + "乙" * 10 + "。" + "丙" * 10
    tokens = (
        OperationalToken("甲" * 10, 0, 149_000, 0, 149_000, 0, 149_000, 0, 10),
        OperationalToken(
            "乙" * 10 + "。", 149_000, 151_000, 149_000, 151_000, 149_000, 151_000, 10, 21
        ),
        OperationalToken("丙" * 10, 151_000, 160_000, 151_000, 160_000, 151_000, 160_000, 21, 31),
    )
    chosen = choose_shared_boundary(
        source,
        tokens,
        nominal_ms=150_000,
        previous_sample=0,
        audio_end_sample=200 * SAMPLE_RATE,
    )
    assert chosen.nominal_ms == 150_000
    assert chosen.delta_ms == 1_000
    assert chosen.preference == "punctuation"
    assert chosen.char_index == 21
    assert chosen.sample == 151_000 * SAMPLE_RATE // 1000

    closer = (
        OperationalToken("甲" * 10, 0, 150_000, 0, 150_000, 0, 150_000, 0, 10),
        tokens[1],
        tokens[2],
    )
    nearer = choose_shared_boundary(
        source,
        closer,
        nominal_ms=150_000,
        previous_sample=0,
        audio_end_sample=200 * SAMPLE_RATE,
    )
    assert nearer.delta_ms == 0
    assert nearer.preference == "time"
    assert nearer.char_index == 10


def test_align_pause_breaks_a_time_tie() -> None:
    source = "甲乙丙"
    tokens = (
        OperationalToken("甲", 0, 149_000, 0, 149_000, 0, 149_000, 0, 1),
        OperationalToken("乙", 149_000, 151_000, 149_000, 151_000, 149_080, 151_000, 1, 2),
        OperationalToken("丙", 151_000, 160_000, 151_000, 160_000, 151_000, 160_000, 2, 3),
    )
    # Gap before 乙 is 80 ms; gap before 丙 is 0. Both cuts are 1000 ms from 150000.
    chosen = choose_shared_boundary(
        source,
        tokens,
        nominal_ms=150_000,
        previous_sample=0,
        audio_end_sample=200 * SAMPLE_RATE,
    )
    assert chosen.preference == "pause"
    assert chosen.char_index == 1
    assert chosen.delta_ms == 1_000


def test_align_cut_cannot_bisect_a_protected_span_or_exceed_180_seconds() -> None:
    source = "甲" * 10 + "五点六" + "乙" * 10
    tokens = (
        OperationalToken("甲" * 10, 0, 140_000, 0, 140_000, 0, 140_000, 0, 10),
        OperationalToken("五", 140_000, 150_000, 140_000, 150_000, 140_000, 150_000, 10, 11),
        OperationalToken("点六", 150_000, 160_000, 150_000, 160_000, 150_000, 160_000, 11, 13),
        OperationalToken("乙" * 10, 160_000, 170_000, 160_000, 170_000, 160_000, 170_000, 13, 23),
    )
    chosen = choose_shared_boundary(
        source,
        tokens,
        nominal_ms=150_000,
        protected_spans=((10, 13),),
        previous_sample=0,
        audio_end_sample=200 * SAMPLE_RATE,
    )
    assert chosen.char_index == 10
    assert source[chosen.char_index : chosen.char_index + 3] == "五点六"

    with pytest.raises(AsrSegmentationError) as blocked:
        choose_shared_boundary(
            source,
            tokens[1:3],
            nominal_ms=150_000,
            protected_spans=((0, len(source)),),
            previous_sample=0,
            audio_end_sample=200 * SAMPLE_RATE,
        )
    assert blocked.value.reason == "no_legal_cut"

    long_source = "甲乙"
    long_tokens = (
        OperationalToken("甲", 0, 180_001, 0, 180_001, 0, 180_001, 0, 1),
        OperationalToken("乙", 180_001, 200_000, 180_001, 200_000, 180_001, 200_000, 1, 2),
    )
    with pytest.raises(AsrSegmentationError) as too_wide:
        choose_shared_boundary(
            long_source,
            long_tokens,
            nominal_ms=180_001,
            previous_sample=0,
            audio_end_sample=200 * SAMPLE_RATE,
        )
    assert too_wide.value.reason == "no_legal_cut"

    allowed = choose_shared_boundary(
        long_source,
        (
            OperationalToken("甲", 0, 180_000, 0, 180_000, 0, 180_000, 0, 1),
            OperationalToken("乙", 180_000, 200_000, 180_000, 200_000, 180_000, 200_000, 1, 2),
        ),
        nominal_ms=180_000,
        previous_sample=0,
        audio_end_sample=200 * SAMPLE_RATE,
    )
    assert allowed.delta_ms == 0
    assert allowed.sample == 180_000 * SAMPLE_RATE // 1000


def test_align_window_uses_source_text_and_fails_without_partial_segments() -> None:
    phrase = "AbCde" * 10
    source = ("前" * 40) + phrase + "。" + ("后" * 40)
    audio = np.zeros(200 * SAMPLE_RATE, dtype=np.float32)
    recognizer = ScriptedRecognizer([phrase.casefold()])
    aligner = ScriptedAligner(
        {
            phrase: [
                _token(phrase[:25], 0, 15_000),
                _token(phrase[25:], 15_000, 30_000),
            ]
        }
    )
    result = segment_recognition_window(
        audio,
        source,
        recognizer=recognizer,
        aligner=aligner,
        segment_seconds=30,
    )
    assert isinstance(result, WindowSegmentation)
    assert recognizer.calls == [(30 * SAMPLE_RATE, ANCHOR_MAX_TOKENS)]
    assert recognizer.queries == []
    anchor_calls = [text for _length, text in aligner.calls if text == phrase]
    assert anchor_calls == [phrase]
    assert phrase.casefold() not in {text for _length, text in aligner.calls}
    assert result.boundaries[0].nominal_ms == 150_000
    assert result.boundaries[0].delta_ms == 0
    assert abs(result.boundaries[0].sample - 150_000 * SAMPLE_RATE // 1000) == 0
    assert max(length for length, _text in aligner.calls) <= 180 * SAMPLE_RATE
    assert "".join(segment.text for segment in result.segments) == source
    assert result.segments[0].char_start == 0
    assert result.segments[-1].char_end == len(source)
    for segment in result.segments:
        assert isinstance(segment, DisplaySegment)
        assert type(segment.start_ms) is int
        assert type(segment.end_ms) is int
        assert 0 <= segment.start_ms < segment.end_ms <= 200_000
        assert segment.end_ms - segment.start_ms <= 30_000
        assert segment.text
        assert source[segment.char_start : segment.char_end] == segment.text

    missed = ScriptedRecognizer(["丙" * 40])
    missed_aligner = ScriptedAligner()
    with pytest.raises(AsrSegmentationError) as no_match:
        segment_recognition_window(
            audio,
            source,
            recognizer=missed,
            aligner=missed_aligner,
        )
    assert no_match.value.reason == "anchor_no_match"
    assert missed_aligner.calls == []

    repeated = phrase + "中" + phrase
    with pytest.raises(AsrSegmentationError) as ambiguous:
        segment_recognition_window(
            audio,
            repeated,
            recognizer=ScriptedRecognizer([phrase.casefold()]),
            aligner=ScriptedAligner(),
        )
    assert ambiguous.value.reason == "anchor_ambiguous"

    single = ScriptedAligner({phrase: [_token(phrase, 0, 30_000)]})
    with pytest.raises(AsrSegmentationError) as no_cut:
        segment_recognition_window(
            audio,
            source,
            recognizer=ScriptedRecognizer([phrase.casefold()]),
            aligner=single,
        )
    assert no_cut.value.reason == "no_legal_cut"
    assert len(single.calls) == 1


def test_align_second_anchor_failure_does_not_align_the_remainder() -> None:
    audio = np.zeros(320 * SAMPLE_RATE, dtype=np.float32)
    phrase = "锚" * 50
    source = phrase + "后" * 80
    recognizer = ScriptedRecognizer([phrase, "丙" * 40])
    aligner = ScriptedAligner(
        {phrase: [_token(phrase[:25], 0, 15_000), _token(phrase[25:], 15_000, 30_000)]}
    )
    with pytest.raises(AsrSegmentationError) as failed:
        segment_recognition_window(audio, source, recognizer=recognizer, aligner=aligner)
    assert failed.value.reason == "anchor_no_match"
    assert len(recognizer.calls) == 2
    assert all(length == 30 * SAMPLE_RATE for length, _tokens in recognizer.calls)
    assert [text for _length, text in aligner.calls] == [phrase]


def test_distant_anchor_can_retry_adjacent_audio_without_loosening_match() -> None:
    phrase = "".join(chr(0x4E80 + i) for i in range(60))
    source = "前" * 40 + phrase + "后" * 40
    distant = "嚓" * 10 + phrase[10:]
    recognizer = ScriptedRecognizer([distant, phrase])
    aligner = ScriptedAligner(
        {
            phrase: [
                _token(phrase[:20], 0, 10_000),
                _token(phrase[20:40], 10_000, 20_000),
                _token(phrase[40:], 20_000, 30_000),
            ]
        }
    )
    result = segment_recognition_window(
        np.zeros(200 * SAMPLE_RATE),
        source,
        recognizer=recognizer,
        aligner=aligner,
    )
    assert "".join(s.text for s in result.segments) == source
    assert recognizer.calls == [(30 * SAMPLE_RATE, ANCHOR_MAX_TOKENS)] * 2
    assert result.boundaries[0].anchor_shift_ms == 5_000
    assert result.boundaries[0].sample == 150 * SAMPLE_RATE
    assert result.boundaries[0].delta_ms == 0
    recognizer = ScriptedRecognizer([distant] * 3)
    with pytest.raises(AsrSegmentationError) as failed:
        segment_recognition_window(
            np.zeros(200 * SAMPLE_RATE),
            source,
            recognizer=recognizer,
            aligner=aligner,
        )
    assert failed.value.reason == "anchor_distance"
    assert len(recognizer.calls) == 3


def test_align_short_window_offsets_public_time_without_an_anchor() -> None:
    text = "你好，世界。"
    audio = np.zeros(10 * SAMPLE_RATE, dtype=np.float32)
    recognizer = ScriptedRecognizer([])
    aligner = ScriptedAligner()
    result = segment_recognition_window(
        audio,
        text,
        recognizer=recognizer,
        aligner=aligner,
        start_sample=600 * SAMPLE_RATE,
        segment_seconds=30,
    )
    assert recognizer.calls == []
    assert aligner.calls == [(10 * SAMPLE_RATE, text)]
    assert len(result.boundaries) == 0
    assert [(item.start_ms, item.end_ms, item.text) for item in result.segments] == [
        (600_000, 610_000, text)
    ]


def _anomalous_alignment():
    source = "".join(chr(0x4E80 + i) for i in range(120))
    good = []
    for i, char in enumerate(source):
        start = i * 500 if i < 55 else 32_000 + (i - 55) * 500
        good.append(_token(char, start, start + 500))
    good[54] = _token(source[54], 27_120, 27_440)
    broken = list(good)
    broken[54] = _token(source[54], 30_320, 30_320, raw=(30_320, 27_040))
    query = source[40:80]
    local = [
        _token(t.text, t.native_start_ms - 15_320, t.native_end_ms - 15_320) for t in good[40:80]
    ]
    return source, broken, query, local


def test_gross_prediction_is_replaced_by_new_local_alignment_not_interpolation() -> None:
    source, broken, query, local = _anomalous_alignment()
    recognizer = ScriptedRecognizer([query])
    aligner = ScriptedAligner({source: broken, query: local})
    result = segment_recognition_window(
        np.zeros(150 * SAMPLE_RATE), source, recognizer=recognizer, aligner=aligner
    )
    assert "".join(s.text for s in result.segments) == source
    assert recognizer.calls == [(30 * SAMPLE_RATE, ANCHOR_MAX_TOKENS)]
    assert aligner.calls == [(150 * SAMPLE_RATE, source), (30 * SAMPLE_RATE, query)]
    assert len(result.retries) == 1
    retry = result.retries[0]
    assert retry.original == broken[54]
    assert (retry.replacement.native_start_ms, retry.replacement.native_end_ms) == (27_120, 27_440)
    assert retry.audio_start_sample == 15_320 * SAMPLE_RATE // 1000


@pytest.mark.parametrize(
    ("side", "expected_neighbors", "expected_times"),
    [
        ("left", [53], [(26_500, 26_880)]),
        ("right", [55], [(32_040, 32_440)]),
        ("right_chain", [55, 56], [(32_040, 32_640), (32_640, 32_960)]),
    ],
)
def test_local_realign_updates_conflicting_neighbors_from_the_same_prediction(
    side: str, expected_neighbors: list[int], expected_times: list[tuple[int, int]]
) -> None:
    source, broken, query, local = _anomalous_alignment()
    if side == "left":
        local[13] = _token(source[53], 11_180, 11_560)
        local[14] = _token(source[54], 11_560, 12_120)
    else:
        # The new word is 40ms after its old neighbor, as in the real failure.
        local[14] = _token(source[54], 16_720, 16_720)
        local[15] = _token(source[55], 16_720, 17_120)
        if side == "right_chain":
            local[15] = _token(source[55], 16_720, 17_320)
            local[16] = _token(source[56], 17_320, 17_640)
    result = segment_recognition_window(
        np.zeros(150 * SAMPLE_RATE),
        source,
        recognizer=ScriptedRecognizer([query]),
        aligner=ScriptedAligner({source: broken, query: local}),
    )
    assert "".join(segment.text for segment in result.segments) == source
    assert all(
        left.end_ms <= right.start_ms
        for left, right in zip(result.segments, result.segments[1:], strict=False)
    )
    assert len(result.retries) == 1
    retry = result.retries[0]
    assert retry.original == broken[54]
    assert [item.token_index for item in retry.neighbor_replacements] == expected_neighbors
    assert [
        (item.replacement.native_start_ms, item.replacement.native_end_ms)
        for item in retry.neighbor_replacements
    ] == expected_times
    assert [item.original for item in retry.neighbor_replacements] == [
        broken[index] for index in expected_neighbors
    ]
    assert [item.char_start for item in retry.neighbor_replacements] == expected_neighbors


def test_local_realign_does_not_repeat_a_neighbor_already_repaired_in_the_group() -> None:
    source, broken, query, local = _anomalous_alignment()
    broken[55] = _token(source[55], 32_000, 32_500, raw=(32_000, 28_000))
    local[14] = _token(source[54], 16_720, 16_720)
    local[15] = _token(source[55], 16_720, 17_120)
    result = segment_recognition_window(
        np.zeros(150 * SAMPLE_RATE),
        source,
        recognizer=ScriptedRecognizer([query]),
        aligner=ScriptedAligner({source: broken, query: local}),
    )
    assert "".join(segment.text for segment in result.segments) == source
    assert len(result.retries) == 1
    neighbor = result.retries[0].neighbor_replacements[0]
    assert neighbor.token_index == 55
    assert (neighbor.replacement.native_start_ms, neighbor.replacement.native_end_ms) == (
        32_040,
        32_440,
    )


@pytest.mark.parametrize("missing_neighbor", ["outside_window", "different_token"])
@pytest.mark.parametrize("side", ["left", "right"])
def test_local_realign_rejects_conflicts_without_a_matching_neighbor_prediction(
    side: str,
    missing_neighbor: str,
) -> None:
    source, broken, query, local = _anomalous_alignment()
    if side == "left" and missing_neighbor == "outside_window":
        query = source[54:94]
        local = [_token(char, 12_000 + i * 400, 12_200 + i * 400) for i, char in enumerate(query)]
        local[0] = _token(source[54], 11_560, 12_120)
    elif side == "left":
        local[14] = _token(source[54], 11_560, 12_120)
        local[12:14] = [_token(source[52:54], 10_680, 11_560)]
    elif missing_neighbor == "outside_window":
        query = source[15:55]
        local = [_token(char, i * 400, i * 400 + 200) for i, char in enumerate(query)]
        local[-1] = _token(source[54], 16_720, 16_720)
    else:
        local[14] = _token(source[54], 16_720, 16_720)
        local[15:17] = [_token(source[55:57], 16_720, 17_640)]
    with pytest.raises(AsrSegmentationError) as failed:
        segment_recognition_window(
            np.zeros(150 * SAMPLE_RATE),
            source,
            recognizer=ScriptedRecognizer([query]),
            aligner=ScriptedAligner({source: broken, query: local}),
        )
    assert failed.value.reason == "disordered"
    assert failed.value.details["token_index"] == 54
    assert failed.value.details["side"] == side
    assert failed.value.details["missing_neighbor_index"] == (53 if side == "left" else 55)
    assert failed.value.details["piece_start_sample"] == 0


@pytest.mark.parametrize("failure", ["unmatched", "still_invalid", "overlapping"])
def test_local_realign_failure_keeps_the_whole_task_failed(failure: str) -> None:
    source, broken, query, local = _anomalous_alignment()
    if failure == "unmatched":
        query = "丙" * 40
    elif failure == "still_invalid":
        local[14] = _token(source[54], 15_000, 15_000, raw=(15_000, 11_000))
    else:
        local[14] = _token(source[54], 10_000, 10_500)
    predictions = {source: broken, query: local}
    if failure == "still_invalid":
        context = source[46:63]
        context_tokens = [_token(char, i * 500, i * 500 + 400) for i, char in enumerate(context)]
        context_tokens[8] = _token(source[54], 4000, 4400, raw=(4000, 0))
        predictions[context] = context_tokens
    with pytest.raises(AsrSegmentationError) as failed:
        segment_recognition_window(
            np.zeros(150 * SAMPLE_RATE),
            source,
            recognizer=ScriptedRecognizer([query]),
            aligner=ScriptedAligner(predictions),
        )
    expected = {
        "unmatched": "anchor_no_match",
        "still_invalid": "gross_repair",
        "overlapping": "disordered",
    }
    assert failed.value.reason == expected[failure]


def test_local_realign_budget_is_bounded_and_invalid_values_do_not_retry() -> None:
    source = "".join(chr(0x4E80 + i) for i in range(600))
    broken = [_token(char, i * 240, i * 240 + 200) for i, char in enumerate(source)]
    predictions = {source: broken}
    queries = []
    for index in (80, 190, 300, 410, 520):
        offset = index * 240 - 15000
        query = source[index - 20 : index + 20]
        queries.append(query)
        predictions[query] = [
            _token(source[i], i * 240 - offset, i * 240 - offset + 200)
            for i in range(index - 20, index + 20)
        ]
        broken[index] = _token(source[index], index * 240, index * 240 + 200, raw=(index * 240, 0))
    recognizer = ScriptedRecognizer(queries)
    with pytest.raises(AsrSegmentationError) as failed:
        segment_recognition_window(
            np.zeros(150 * SAMPLE_RATE),
            source,
            recognizer=recognizer,
            aligner=ScriptedAligner(predictions),
        )
    assert failed.value.reason == "realign_budget"
    assert len(recognizer.calls) == 4
    broken[0] = _token(source[0], 0, 0, raw=(-1, 0))
    recognizer = ScriptedRecognizer([])
    with pytest.raises(AsrSegmentationError) as failed:
        segment_recognition_window(
            np.zeros(150 * SAMPLE_RATE),
            source,
            recognizer=recognizer,
            aligner=ScriptedAligner({source: broken}),
        )
    assert failed.value.reason == "negative"
    assert recognizer.calls == []


def test_time_keeps_raw_and_native_and_limits_one_grid() -> None:
    spans = ((0, 1),)
    kept, limits = bound_alignment_times(
        [_token("甲", 0, 10_000, raw=(0, 10_040), native=(0, 10_040))],
        spans,
        window_limit_ms=10_000,
    )
    assert kept[0].raw_start_ms == 0
    assert kept[0].raw_end_ms == 10_040
    assert kept[0].native_start_ms == 0
    assert kept[0].native_end_ms == 10_040
    assert kept[0].start_ms == 0
    assert kept[0].end_ms == 10_000
    assert type(kept[0].end_ms) is int
    assert limits == (TimeLimit(0, "end", 10_040, 10_000, -40, 80, 0),)

    _kept, limits = bound_alignment_times(
        [_token("甲", 0, 10_000, raw=(0, 10_080), native=(0, 10_080))],
        spans,
        window_limit_ms=10_000,
    )
    assert limits[0].original_ms == 10_080
    assert limits[0].bounded_ms == 10_000
    assert limits[0].delta_ms == -80

    for native_end in (10_081, 10_160):
        with pytest.raises(AsrSegmentationError) as overflow:
            bound_alignment_times(
                [_token("甲", 0, native_end, raw=(0, native_end), native=(0, native_end))],
                spans,
                window_limit_ms=10_000,
            )
        assert overflow.value.reason == "overflow"


@pytest.mark.parametrize(
    ("bad", "reason"),
    [
        (_token("甲", 0, 1000, raw=(0, 1000), native=(0, float("nan"))), "nonfinite"),
        (_token("甲", 0, 1000, raw=(float("inf"), 1000), native=(0, 1000)), "nonfinite"),
        (_token("甲", 0, 1000, raw=(0, 1000), native=(float("-inf"), 1000)), "nonfinite"),
        (_token("甲", -1, 1000), "negative"),
        (_token("甲", 1000, 500), "disordered"),
        (_token("甲", 0, 1000, raw=(0, 0), native=(0, 2001)), "gross_repair"),
    ],
)
def test_time_rejects_nonfinite_negative_reversed_and_gross_repairs(
    bad: AlignedToken, reason: str
) -> None:
    with pytest.raises(AsrSegmentationError) as rejected:
        bound_alignment_times([bad], ((0, 1),), window_limit_ms=10_000)
    assert rejected.value.reason == reason


def test_time_allows_a_2000_ms_repair_and_rejects_2001() -> None:
    kept, limits = bound_alignment_times(
        [_token("甲", 0, 5_000, raw=(0, 3_000), native=(0, 5_000))],
        ((0, 1),),
        window_limit_ms=10_000,
    )
    assert kept[0].raw_end_ms == 3_000
    assert kept[0].native_end_ms == 5_000
    assert kept[0].end_ms == 5_000
    assert limits == ()
    with pytest.raises(AsrSegmentationError) as gross:
        bound_alignment_times(
            [_token("甲", 0, 5_000, raw=(0, 2_999), native=(0, 5_000))],
            ((0, 1),),
            window_limit_ms=10_000,
        )
    assert gross.value.reason == "gross_repair"


def test_time_rejects_overlap_and_nonintegral_values() -> None:
    with pytest.raises(AsrSegmentationError) as overlap:
        bound_alignment_times(
            [_token("甲", 0, 1000), _token("乙", 999, 2000)],
            ((0, 1), (1, 2)),
            window_limit_ms=10_000,
        )
    assert overlap.value.reason == "disordered"
    with pytest.raises(AsrSegmentationError) as fraction:
        bound_alignment_times(
            [_token("甲", 0, 1000.5)],
            ((0, 1),),
            window_limit_ms=10_000,
        )
    assert fraction.value.reason == "nonintegral"


def test_time_zero_length_tokens_join_neighbors_and_cannot_form_a_segment() -> None:
    source = "甲。乙"
    tokens = (
        OperationalToken("甲", 0, 4_000, 0, 4_000, 0, 4_000, 0, 1),
        OperationalToken("。", 4_000, 4_000, 4_000, 4_000, 4_000, 4_000, 1, 2),
        OperationalToken("乙", 4_000, 8_000, 4_000, 8_000, 4_000, 8_000, 2, 3),
    )
    segments = group_display_segments(source, tokens, segment_seconds=5, duration_ms=8_000)
    assert [(item.start_ms, item.end_ms, item.text) for item in segments] == [
        (0, 4_000, "甲。"),
        (4_000, 8_000, "乙"),
    ]
    assert all(item.end_ms > item.start_ms for item in segments)

    leading = (
        OperationalToken("。", 0, 0, 0, 0, 0, 0, 0, 1),
        OperationalToken("甲", 0, 1_000, 0, 1_000, 0, 1_000, 1, 2),
    )
    joined = group_display_segments("。甲", leading, segment_seconds=30, duration_ms=1_000)
    assert joined[0].text == "。甲"
    assert joined[0].start_ms < joined[0].end_ms

    with pytest.raises(AsrSegmentationError) as alone:
        group_display_segments(
            "。",
            (OperationalToken("。", 1_000, 1_000, 1_000, 1_000, 1_000, 1_000, 0, 1),),
            segment_seconds=30,
            duration_ms=2_000,
        )
    assert alone.value.reason == "zero_length_segment"


@pytest.mark.parametrize("segment_seconds", [5, 30, 60])
def test_zero_duration_numeric_prefix_stays_with_the_whole_percentage(
    segment_seconds: int,
) -> None:
    source = "涨幅百分之九十"
    limit_ms = segment_seconds * 1000
    prefix_end = ((limit_ms - 1200) // 80) * 80
    middle = (prefix_end // 2 // 80) * 80
    digit_start = (limit_ms // 80) * 80
    duration_ms = digit_start + 2000
    times = [
        (0, middle),
        (middle, prefix_end),
        (prefix_end, prefix_end),
        (prefix_end, prefix_end),
        (prefix_end, prefix_end),
        (digit_start, digit_start + 800),
        (digit_start + 800, duration_ms),
    ]
    aligned = [_token(char, start, end) for char, (start, end) in zip(source, times, strict=True)]
    tokens, limits = bound_alignment_times(
        aligned, map_source_spans(source, list(source)), window_limit_ms=duration_ms
    )

    segments = group_display_segments(
        source, tokens, segment_seconds=segment_seconds, duration_ms=duration_ms
    )

    assert limits == ()
    assert [(item.text, item.start_ms, item.end_ms) for item in segments] == [
        ("涨幅", 0, prefix_end),
        ("百分之九十", prefix_end, duration_ms),
    ]
    spans = tuple((item.char_start, item.char_end) for item in segments)
    assert spans == ((0, 2), (2, len(source)))
    assert render_normalized_slices(source, spans, itn=True) == ("涨幅", "90%")
    assert render_normalized_slices(source, spans, itn=False) == ("涨幅", "百分之九十")
    assert all(0 < item.end_ms - item.start_ms <= limit_ms for item in segments)


@pytest.mark.parametrize(
    ("expression", "words", "normalized"),
    [("五点六", ["五", "点", "六"], "5.6"), ("Fable五", ["Fable", "五"], "Fable 5")],
)
def test_zero_duration_words_stay_inside_a_decimal_or_version(
    expression: str, words: list[str], normalized: str
) -> None:
    source = "甲" + expression
    times = [(0, 4_000)] + [(4_000, 4_000)] * (len(words) - 1) + [(4_000, 6_000)]
    tokens = _operational(source, ["甲", *words], times)

    segments = group_display_segments(source, tokens, segment_seconds=5, duration_ms=6_000)

    assert [(item.text, item.start_ms, item.end_ms) for item in segments] == [
        ("甲", 0, 4_000),
        (expression, 4_000, 6_000),
    ]
    spans = tuple((item.char_start, item.char_end) for item in segments)
    assert render_normalized_slices(source, spans, itn=True) == ("甲", normalized)


@pytest.mark.parametrize(
    ("source", "times", "expected"),
    [
        (
            "甲百分之九十乙",
            [(0, 4_000)] + [(4_000, 4_000)] * 5 + [(4_000, 8_000)],
            [("甲90%", 0, 4_000), ("乙", 4_000, 8_000)],
        ),
        (
            "百分之九十甲",
            [(0, 0)] * 5 + [(0, 4_000)],
            [("90%甲", 0, 4_000)],
        ),
        (
            "甲百分之九十",
            [(0, 4_000)] + [(4_000, 4_000)] * 5,
            [("甲90%", 0, 4_000)],
        ),
    ],
)
def test_entirely_zero_duration_number_joins_one_timed_neighbor(
    source: str, times: list[tuple[int, int]], expected: list[tuple[str, int, int]]
) -> None:
    tokens = _operational(source, list(source), times)
    segments = group_display_segments(source, tokens, segment_seconds=5, duration_ms=times[-1][1])
    spans = tuple((item.char_start, item.char_end) for item in segments)
    assert "".join(item.text for item in segments) == source
    rendered = render_normalized_slices(source, spans, itn=True)
    assert [
        (text, item.start_ms, item.end_ms) for text, item in zip(rendered, segments, strict=True)
    ] == expected


def test_time_public_bounds_are_integers_inside_the_source() -> None:
    assert samples_to_ms(0) == 0
    assert samples_to_ms(8) == 1
    assert samples_to_ms(16) == 1
    assert samples_to_ms(24) == 2
    source = "甲乙"
    tokens = _operational(source, ["甲", "乙"], [(0, 3_000), (3_000, 6_000)])
    segments = group_display_segments(source, tokens, segment_seconds=5, duration_ms=6_000)
    assert segments[0].end_ms == segments[1].start_ms
    for segment in segments:
        assert type(segment.start_ms) is int
        assert type(segment.end_ms) is int
        assert 0 <= segment.start_ms < segment.end_ms <= 6_000
    summary = time_limit_summary(
        (
            TimeLimit(0, "end", 10_040, 10_000, -40, 80, 0),
            TimeLimit(1, "end", 10_080, 10_000, -80, 80, 16000),
        )
    )
    assert summary == {"count": 2, "max_abs_delta_ms": 80}
    assert time_limit_summary(()) == {"count": 0, "max_abs_delta_ms": 0}


@pytest.mark.parametrize("segment_seconds", [5, 30, 60])
def test_display_respects_5_30_and_60_second_limits(segment_seconds: int) -> None:
    source = "甲乙丙丁"
    times = [(0, 4_000), (4_000, 8_000), (8_000, 12_000), (12_000, 16_000)]
    tokens = _operational(source, list(source), times)
    segments = group_display_segments(
        source,
        tokens,
        segment_seconds=segment_seconds,
        duration_ms=16_000,
    )
    assert "".join(item.text for item in segments) == source
    assert all(item.end_ms - item.start_ms <= segment_seconds * 1000 for item in segments)
    if segment_seconds == 5:
        assert [item.text for item in segments] == list(source)
    else:
        assert [item.text for item in segments] == [source]


def test_display_keeps_a_short_tail_and_original_english() -> None:
    source = "甲乙丙"
    tokens = _operational(source, list(source), [(0, 4_000), (4_000, 8_000), (8_000, 10_000)])
    segments = group_display_segments(source, tokens, segment_seconds=5, duration_ms=10_000)
    assert [(item.text, item.end_ms - item.start_ms) for item in segments] == [
        ("甲", 4_000),
        ("乙", 4_000),
        ("丙", 2_000),
    ]

    english = "Hello, WORLD!"
    spans = map_source_spans(english, ["Hello", "WORLD"])
    tokens = tuple(
        OperationalToken(english[start:end], 0, 1_000, 0, 1_000, begin, finish, start, end)
        for (start, end), (begin, finish) in zip(spans, [(0, 1_000), (1_000, 2_000)], strict=True)
    )
    shown = group_display_segments(english, tokens, segment_seconds=30, duration_ms=2_000)
    assert shown[0].text == english
    assert shown[0].char_start == 0
    assert shown[0].char_end == len(english)


def test_display_does_not_split_numbers_versions_or_existing_decimals() -> None:
    samples = [
        (
            "甲五点六后",
            ["甲", "五", "点", "六", "后"],
            [4_000, 500, 500, 500, 4_000],
            ["甲", "五点六", "后"],
        ),
        ("值5.6元", ["值", "5", "6", "元"], [4_000, 1_000, 1_000, 4_000], ["值", "5.6", "元"]),
        (
            "看到百分之五十了",
            ["看到", "百", "分", "之", "五", "十", "了"],
            [4_000, 1_000, 1_000, 1_000, 1_000, 1_000, 4_000],
            ["看到", "百分之五十", "了"],
        ),
        (
            "用Fable五吧",
            ["用", "Fable", "五", "吧"],
            [4_000, 1_000, 1_000, 4_000],
            ["用", "Fable五", "吧"],
        ),
    ]
    for source, texts, widths, expected in samples:
        spans = map_source_spans(source, texts)
        cursor = 0
        tokens = []
        for (start, end), width in zip(spans, widths, strict=True):
            tokens.append(
                OperationalToken(
                    source[start:end],
                    cursor,
                    cursor + width,
                    cursor,
                    cursor + width,
                    cursor,
                    cursor + width,
                    start,
                    end,
                )
            )
            cursor += width
        segments = group_display_segments(
            source, tuple(tokens), segment_seconds=5, duration_ms=cursor
        )
        assert [item.text for item in segments] == expected
        assert "".join(item.text for item in segments) == source


def test_display_rejects_an_unsplittable_expression_and_a_long_token() -> None:
    source = "五点六"
    tokens = _operational(source, ["五", "点", "六"], [(0, 2_000), (2_000, 4_000), (4_000, 6_001)])
    with pytest.raises(AsrSegmentationError) as blocked:
        group_display_segments(source, tokens, segment_seconds=5, duration_ms=6_001)
    assert blocked.value.reason == "protected_span_too_long"

    with pytest.raises(AsrSegmentationError) as long_token:
        group_display_segments(
            "你好",
            _operational("你好", ["你好"], [(0, 5_001)]),
            segment_seconds=5,
            duration_ms=5_001,
        )
    assert long_token.value.reason == "token_too_long"

    for bad_limit in (4, 61, True, 30.0):
        with pytest.raises(AsrSegmentationError) as rejected:
            group_display_segments(
                "甲",
                _operational("甲", ["甲"], [(0, 1_000)]),
                segment_seconds=bad_limit,  # type: ignore[arg-type]
                duration_ms=1_000,
            )
        assert rejected.value.reason == "segment_limit"


def test_display_window_asks_the_text_module_for_spans(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def spy(text: str) -> tuple[tuple[int, int], ...]:
        seen.append(text)
        return ((40, 43),)

    monkeypatch.setattr("douyin_wiki.adapters.asr_segments.protected_number_spans", spy)
    phrase = "锚" * 50
    source = ("前" * 40) + "五点六" + phrase
    # Force the only in-tolerance cut to avoid the protected span by giving two boundaries,
    # one inside 五点六 and one before it. The anchor phrase is the trailing 锚 run.
    audio = np.zeros(200 * SAMPLE_RATE, dtype=np.float32)
    anchor = phrase
    recognizer = ScriptedRecognizer([anchor])
    aligner = ScriptedAligner(
        {
            anchor: [
                _token(anchor[:10], 0, 14_000),
                _token(anchor[10:], 14_000, 30_000),
            ]
        }
    )
    result = segment_recognition_window(audio, source, recognizer=recognizer, aligner=aligner)
    assert seen == [source]
    assert result.boundaries
    assert all(not (40 < item.char_index < 43) for item in result.boundaries)


def test_public_signatures_match_the_task4_contract() -> None:
    window = inspect.signature(iter_recognition_windows)
    assert list(window.parameters) == ["audio_path", "splitter", "opener"]
    assert window.parameters["opener"].kind is inspect.Parameter.KEYWORD_ONLY

    splitter = inspect.signature(AudioSplitter.split_audio)
    assert "window_seconds" in splitter.parameters
    assert splitter.parameters["window_seconds"].kind is inspect.Parameter.KEYWORD_ONLY

    segment = inspect.signature(segment_recognition_window)
    assert list(segment.parameters) == [
        "audio",
        "text",
        "recognizer",
        "aligner",
        "start_sample",
        "segment_seconds",
        "anchor_max_tokens",
    ]
    for name in ("recognizer", "aligner", "start_sample", "segment_seconds", "anchor_max_tokens"):
        assert segment.parameters[name].kind is inspect.Parameter.KEYWORD_ONLY

    recognize = inspect.signature(ScriptedRecognizer.recognize)
    assert recognize.parameters["max_tokens"].kind is inspect.Parameter.KEYWORD_ONLY
    fields = (
        (RecognitionWindow, ("start_sample", "audio")),
        (
            AlignedToken,
            ("text", "raw_start_ms", "raw_end_ms", "native_start_ms", "native_end_ms"),
        ),
        (
            BoundaryTrace,
            (
                "nominal_ms",
                "sample",
                "char_index",
                "delta_ms",
                "preference",
                "token_index",
                "anchor_shift_ms",
            ),
        ),
        (DisplaySegment, ("text", "start_ms", "end_ms", "char_start", "char_end")),
        (
            TimeLimit,
            (
                "token_index",
                "edge",
                "original_ms",
                "bounded_ms",
                "delta_ms",
                "threshold_ms",
                "audio_start_sample",
            ),
        ),
    )
    for data_type, expected_fields in fields:
        assert tuple(data_type.__dataclass_fields__) == expected_fields
