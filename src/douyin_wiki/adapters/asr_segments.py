"""Bounded recognition windows, source anchors, and display segments.

SoundFile and the upstream energy splitter are imported only when a window is
read or a Qwen split is requested. Public times are file-absolute integers.
Raw and native alignment times stay on the aligner's own slice.
"""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from douyin_wiki.adapters.asr_text import protected_number_spans

SAMPLE_RATE = 16_000
RECOGNITION_WINDOW_SECONDS = 600
ALIGNMENT_TARGET_MS = 150_000
ALIGNMENT_SLACK_MS = 15_000
ALIGNMENT_LIMIT_MS = 180_000
ANCHOR_MAX_TOKENS = 2048
MIN_ANCHOR_CHARS = 40
MAX_REPAIR_MS = 2_000
MAX_LOCAL_REALIGNS = 4
TIMESTAMP_GRID_MS = 80
MIN_SEGMENT_SECONDS = 5
MAX_SEGMENT_SECONDS = 60
DEFAULT_SEGMENT_SECONDS = 30
MAX_SOURCE_SECONDS = 7_200
ENERGY_CHUNK_SECONDS = 599
ENERGY_SEARCH_EXPAND_SECONDS = 1
ENERGY_MIN_WINDOW_MS = 20
ENERGY_MIN_CHUNK_SECONDS = 0

_WINDOW_SAMPLES = RECOGNITION_WINDOW_SECONDS * SAMPLE_RATE
_SOURCE_SAMPLES = MAX_SOURCE_SECONDS * SAMPLE_RATE
_LIMIT_SAMPLES = ALIGNMENT_LIMIT_MS * SAMPLE_RATE // 1000
_ANCHOR_SAMPLES = (ALIGNMENT_SLACK_MS * 2) * SAMPLE_RATE // 1000
_BOUNDARY_PUNCTUATION = frozenset("，。、；：！？…,.!?;:\n")


class AsrSegmentationError(Exception):
    """Segmentation failed. ``reason`` is the stable code; nothing partial is returned."""

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class RecognitionWindow:
    start_sample: int
    audio: np.ndarray

    def __post_init__(self) -> None:
        if isinstance(self.start_sample, (bool, np.bool_)) or not isinstance(
            self.start_sample, (int, np.integer)
        ):
            raise AsrSegmentationError("窗口起点无效", reason="coverage")
        start = int(self.start_sample)
        if start < 0:
            raise AsrSegmentationError("窗口起点无效", reason="coverage")
        audio = np.asarray(self.audio)
        if audio.ndim != 1:
            raise AsrSegmentationError("窗口音频必须是一维数组", reason="audio_shape")
        if audio.size == 0:
            raise AsrSegmentationError("窗口音频为空", reason="length")
        if audio.size > _WINDOW_SAMPLES:
            raise AsrSegmentationError("窗口超过 600 秒", reason="window_too_long")
        object.__setattr__(self, "start_sample", start)
        object.__setattr__(self, "audio", np.ascontiguousarray(audio, dtype=np.float32))


@dataclass(frozen=True)
class AnchorMatch:
    source_start: int
    source_end: int
    distance: int
    canonical_chars: int


@dataclass(frozen=True)
class AlignedToken:
    text: str
    raw_start_ms: int
    raw_end_ms: int
    native_start_ms: int
    native_end_ms: int


@dataclass(frozen=True)
class OperationalToken:
    text: str
    raw_start_ms: int
    raw_end_ms: int
    native_start_ms: int
    native_end_ms: int
    start_ms: int
    end_ms: int
    char_start: int
    char_end: int


@dataclass(frozen=True)
class TimeLimit:
    token_index: int
    edge: str
    original_ms: int
    bounded_ms: int
    delta_ms: int
    threshold_ms: int
    audio_start_sample: int


@dataclass(frozen=True)
class BoundaryTrace:
    nominal_ms: int
    sample: int
    char_index: int
    delta_ms: int
    preference: str
    token_index: int
    anchor_shift_ms: int = 0


@dataclass(frozen=True)
class DisplaySegment:
    text: str
    start_ms: int
    end_ms: int
    char_start: int
    char_end: int


@dataclass(frozen=True)
class RecognitionResult:
    text: str


@dataclass(frozen=True)
class AlignmentRetry:
    token_index: int
    char_start: int
    audio_start_sample: int
    samples: int
    original: AlignedToken
    replacement: AlignedToken


@dataclass(frozen=True)
class WindowSegmentation:
    segments: tuple[DisplaySegment, ...]
    boundaries: tuple[BoundaryTrace, ...]
    time_limits: tuple[TimeLimit, ...]
    retries: tuple[AlignmentRetry, ...] = ()


class AudioSplitter(Protocol):
    def split_audio(
        self, audio: np.ndarray, *, window_seconds: float
    ) -> Sequence[tuple[np.ndarray, int]]: ...


class Recognizer(Protocol):
    def recognize(self, audio: np.ndarray, *, max_tokens: int) -> RecognitionResult: ...


class Aligner(Protocol):
    def align(self, audio: np.ndarray, text: str) -> Sequence[AlignedToken]: ...


class QwenEnergySplitter:
    """Lazy wrapper around upstream ``split_audio_into_chunks(599, 0, 1, 20)``."""

    def split_audio(
        self, audio: np.ndarray, *, window_seconds: float
    ) -> list[tuple[np.ndarray, int]]:
        if window_seconds != RECOGNITION_WINDOW_SECONDS:
            raise AsrSegmentationError("低能量切分只接受 600 秒窗口", reason="window_seconds")
        waveform = np.asarray(audio)
        if waveform.ndim != 1:
            raise AsrSegmentationError("低能量切分需要一维音频", reason="audio_shape")
        try:
            from mlx_audio.stt.models.qwen3_asr.qwen3_asr import split_audio_into_chunks

            produced = split_audio_into_chunks(
                waveform,
                SAMPLE_RATE,
                ENERGY_CHUNK_SECONDS,
                ENERGY_MIN_CHUNK_SECONDS,
                ENERGY_SEARCH_EXPAND_SECONDS,
                ENERGY_MIN_WINDOW_MS,
            )
        except AsrSegmentationError:
            raise
        except Exception as exc:
            raise AsrSegmentationError("低能量切分失败", reason="splitter_failed") from exc
        return _pieces_ignoring_time_offsets(waveform, produced)


def iter_recognition_windows(
    audio_path: object,
    splitter: AudioSplitter,
    *,
    opener: object = None,
) -> Iterator[RecognitionWindow]:
    """Yield at most 600s of mono 16 kHz audio. Reads always pass a frame count."""
    open_audio = _open_soundfile if opener is None else opener
    try:
        handle = open_audio(audio_path)  # type: ignore[operator]
    except AsrSegmentationError:
        raise
    except Exception as exc:
        raise AsrSegmentationError("音频无法读取", reason="unreadable") from exc
    with handle:
        yield from _iter_open_audio(handle, splitter)


def samples_to_ms(samples: int) -> int:
    """Round a sample count to milliseconds, half up."""
    if isinstance(samples, (bool, np.bool_)) or not isinstance(samples, (int, np.integer)):
        raise AsrSegmentationError("采样数必须是整数", reason="length")
    count = int(samples)
    if count < 0:
        raise AsrSegmentationError("采样数不能为负", reason="length")
    return (count * 1000 + SAMPLE_RATE // 2) // SAMPLE_RATE


def nominal_boundary_ms(duration_ms: int) -> tuple[int, ...]:
    """Return k*150s while that boundary still leaves a 15s tail."""
    bounds: list[int] = []
    step = 1
    while True:
        boundary = step * ALIGNMENT_TARGET_MS
        if boundary + ALIGNMENT_SLACK_MS <= duration_ms:
            bounds.append(boundary)
            step += 1
            continue
        return tuple(bounds)


def locate_anchor(source: str, query: str) -> AnchorMatch:
    """Find one source span for ``query``. Case, space, and punctuation are ignored."""
    source_key, owners = _canonical_key(source)
    query_key, _query_owners = _canonical_key(query)
    canonical = len(query_key)
    if canonical < MIN_ANCHOR_CHARS:
        raise AsrSegmentationError("锚点短于 40 个有效字符", reason="anchor_too_short")
    distance, start, end = _best_anchor(source_key, query_key)
    if not _distance_accepted(distance, canonical):
        reason = "anchor_no_match" if distance >= canonical else "anchor_distance"
        raise AsrSegmentationError("锚点未能唯一对上原文", reason=reason)
    if _has_rival(source_key, query_key, distance, start, end):
        raise AsrSegmentationError("锚点存在另一个接近的匹配", reason="anchor_ambiguous")
    if end <= start:
        raise AsrSegmentationError("锚点未能唯一对上原文", reason="anchor_no_match")
    return AnchorMatch(owners[start], owners[end - 1] + 1, distance, canonical)


def map_source_spans(source: str, token_texts: Sequence[str]) -> tuple[tuple[int, int], ...]:
    """Map tokens in order, retaining punctuation without skipping kept characters."""
    raw: list[tuple[int, int]] = []
    cursor = 0
    for token in token_texts:
        if token == "":
            raise AsrSegmentationError("对齐词为空", reason="empty_token")
        found = source.find(token, cursor)
        if found < 0:
            raise AsrSegmentationError("对齐词不在原文中", reason="token_missing")
        if any(_is_match_char(char) for char in source[cursor:found]):
            raise AsrSegmentationError("对齐跳过了有效字符", reason="kept_character_skipped")
        raw.append((found, found + len(token)))
        cursor = found + len(token)
    if any(_is_match_char(char) for char in source[cursor:]):
        raise AsrSegmentationError("原文仍有未对齐的有效字符", reason="kept_character_skipped")
    extended: list[tuple[int, int]] = []
    for index, (start, _end) in enumerate(raw):
        char_start = 0 if index == 0 else start
        char_end = raw[index + 1][0] if index + 1 < len(raw) else len(source)
        extended.append((char_start, char_end))
    return tuple(extended)


def bound_alignment_times(
    tokens: Sequence[AlignedToken],
    spans: Sequence[tuple[int, int]],
    *,
    window_limit_ms: int,
    grid_ms: int = TIMESTAMP_GRID_MS,
    audio_start_sample: int = 0,
) -> tuple[tuple[OperationalToken, ...], tuple[TimeLimit, ...]]:
    """Keep raw and native times. Clamp at most one 80ms grid past the slice end."""
    token_list = tuple(tokens)
    span_list = tuple(spans)
    if len(token_list) != len(span_list):
        raise AsrSegmentationError("对齐词和原文区间数量不一致", reason="token_missing")
    limit = _as_milliseconds(window_limit_ms)
    grid = _as_milliseconds(grid_ms)
    origin = _as_sample(audio_start_sample)
    operational: list[OperationalToken] = []
    limits: list[TimeLimit] = []
    native_points: list[int] = []
    public_points: list[int] = []
    for index, (token, span) in enumerate(zip(token_list, span_list, strict=True)):
        char_start, char_end = _char_span(span)
        raw_start = _as_milliseconds(token.raw_start_ms)
        raw_end = _as_milliseconds(token.raw_end_ms)
        native_start = _as_milliseconds(token.native_start_ms)
        native_end = _as_milliseconds(token.native_end_ms)
        if native_end < native_start:
            raise AsrSegmentationError("对齐时间倒序", reason="disordered")
        if (
            abs(native_start - raw_start) > MAX_REPAIR_MS
            or abs(native_end - raw_end) > MAX_REPAIR_MS
        ):
            raise AsrSegmentationError("上游修正超过 2000 毫秒", reason="gross_repair")
        start_ms, start_limit = _clamp_edge(index, "start", native_start, limit, grid, origin)
        end_ms, end_limit = _clamp_edge(index, "end", native_end, limit, grid, origin)
        if end_ms < start_ms:
            raise AsrSegmentationError("对齐时间倒序", reason="disordered")
        if start_limit is not None:
            limits.append(start_limit)
        if end_limit is not None:
            limits.append(end_limit)
        operational.append(
            OperationalToken(
                token.text,
                raw_start,
                raw_end,
                native_start,
                native_end,
                start_ms,
                end_ms,
                char_start,
                char_end,
            )
        )
        native_points.extend((native_start, native_end))
        public_points.extend((start_ms, end_ms))
    if any(
        native_points[index] > native_points[index + 1] for index in range(len(native_points) - 1)
    ):
        raise AsrSegmentationError("对齐时间倒序", reason="disordered")
    if any(
        public_points[index] > public_points[index + 1] for index in range(len(public_points) - 1)
    ):
        raise AsrSegmentationError("对齐时间倒序", reason="disordered")
    return tuple(operational), tuple(limits)


def choose_shared_boundary(
    source: str,
    tokens: Sequence[OperationalToken],
    *,
    nominal_ms: int,
    protected_spans: Sequence[tuple[int, int]] = (),
    previous_sample: int = 0,
    audio_end_sample: int,
) -> BoundaryTrace:
    """Pick an edge within 15s, preferring punctuation or pause when equally close."""
    sequence = tuple(tokens)
    nominal = int(nominal_ms)
    previous = _as_sample(previous_sample)
    audio_end = _as_sample(audio_end_sample)
    best: tuple[int, int, int, int, int, str] | None = None
    for index in range(len(sequence) - 1):
        left = sequence[index]
        right = sequence[index + 1]
        end_ms = int(left.end_ms)
        delta_ms = abs(end_ms - nominal)
        if delta_ms > ALIGNMENT_SLACK_MS:
            continue
        char_index = int(right.char_start)
        if any(start < char_index < end for start, end in protected_spans):
            continue
        cut_sample = end_ms * SAMPLE_RATE // 1000
        if cut_sample <= previous or cut_sample >= audio_end:
            continue
        if cut_sample - previous > _LIMIT_SAMPLES or audio_end - cut_sample > _LIMIT_SAMPLES:
            continue
        preference, rank = _cut_preference(source, left, right)
        candidate = (delta_ms, rank, index, cut_sample, char_index, preference)
        if best is None or candidate[:3] < best[:3]:
            best = candidate
    if best is None:
        raise AsrSegmentationError("没有落在 15 秒内且短于 180 秒的切点", reason="no_legal_cut")
    delta_ms, _rank, index, cut_sample, char_index, preference = best
    return BoundaryTrace(nominal, cut_sample, char_index, delta_ms, preference, index)


def group_display_segments(
    source: str,
    tokens: Sequence[OperationalToken],
    *,
    segment_seconds: int,
    duration_ms: int,
    protected_spans: Sequence[tuple[int, int]] | None = None,
) -> tuple[DisplaySegment, ...]:
    """Pack atomic blocks; keep protected numbers and zero-length tokens whole."""
    limit_ms = _segment_limit_ms(segment_seconds)
    if isinstance(duration_ms, bool) or not isinstance(duration_ms, int) or duration_ms < 0:
        raise AsrSegmentationError("展示时长无效", reason="time_bounds")
    sequence = tuple(tokens)
    if not sequence:
        if source:
            raise AsrSegmentationError("原文没有被展示段覆盖", reason="coverage")
        return ()
    spans = protected_number_spans(source) if protected_spans is None else tuple(protected_spans)
    atoms = _display_atoms(sequence, spans)
    _reject_overlong_atoms(atoms, limit_ms)
    segments = _pack_atoms(source, atoms, limit_ms, duration_ms)
    _check_display_coverage(source, segments)
    return segments


def segment_recognition_window(
    audio: np.ndarray,
    text: str,
    *,
    recognizer: Recognizer,
    aligner: Aligner,
    start_sample: int = 0,
    segment_seconds: int = DEFAULT_SEGMENT_SECONDS,
    anchor_max_tokens: int = ANCHOR_MAX_TOKENS,
) -> WindowSegmentation:
    """Anchor every 150s, align each piece at most 180s, then build display segments."""
    if text == "":
        return WindowSegmentation((), (), ())
    origin = _as_sample(start_sample, reason="coverage")
    waveform = _window_audio(audio)
    _segment_limit_ms(segment_seconds)
    if not any(_is_match_char(char) for char in text):
        raise AsrSegmentationError("原文没有可对齐的字符", reason="no_kept_characters")
    protected = tuple(protected_number_spans(text))
    duration_ms = samples_to_ms(len(waveform))
    nominals = nominal_boundary_ms(duration_ms)
    boundaries, anchor_limits = _cut_anchors(
        waveform,
        text,
        nominals,
        protected,
        recognizer,
        aligner,
        anchor_max_tokens,
        origin,
    )
    retries: list[AlignmentRetry] = []
    display_tokens, piece_limits = _align_pieces(
        waveform,
        text,
        boundaries,
        aligner,
        origin,
        recognizer=recognizer,
        anchor_max_tokens=anchor_max_tokens,
        retries=retries,
    )
    segments = group_display_segments(
        text,
        display_tokens,
        segment_seconds=segment_seconds,
        duration_ms=samples_to_ms(origin + len(waveform)),
        protected_spans=protected,
    )
    file_nominal = samples_to_ms(origin)
    shifted = tuple(
        BoundaryTrace(
            boundary.nominal_ms + file_nominal,
            boundary.sample + origin,
            boundary.char_index,
            boundary.delta_ms,
            boundary.preference,
            boundary.token_index,
            boundary.anchor_shift_ms,
        )
        for boundary in boundaries
    )
    return WindowSegmentation(segments, shifted, anchor_limits + piece_limits, tuple(retries))


def time_limit_summary(limits: Sequence[TimeLimit]) -> dict[str, int]:
    deltas = [abs(int(item.delta_ms)) for item in limits]
    if not deltas:
        return {"count": 0, "max_abs_delta_ms": 0}
    return {"count": len(deltas), "max_abs_delta_ms": max(deltas)}


def _open_soundfile(audio_path: object) -> object:
    import soundfile as sf

    return sf.SoundFile(audio_path)


def _iter_open_audio(handle: object, splitter: AudioSplitter) -> Iterator[RecognitionWindow]:
    rate = getattr(handle, "samplerate", None)
    channels = getattr(handle, "channels", None)
    frames = getattr(handle, "frames", None)
    if rate != SAMPLE_RATE:
        raise AsrSegmentationError("采样率必须是 16000", reason="sample_rate")
    if channels != 1:
        raise AsrSegmentationError("音频必须是单声道", reason="channels")
    if isinstance(frames, (bool, np.bool_)) or not isinstance(frames, (int, np.integer)):
        raise AsrSegmentationError("音频长度无效", reason="length")
    total = int(frames)
    if total < 0:
        raise AsrSegmentationError("音频长度无效", reason="length")
    if total > _SOURCE_SAMPLES:
        raise AsrSegmentationError("音频超过 120 分钟", reason="too_long")
    if total == 0:
        return
    pending = np.zeros(0, dtype=np.float32)
    file_pos = 0
    emitted = 0
    while True:
        remaining = total - file_pos
        if remaining > 0:
            room = _WINDOW_SAMPLES - int(pending.size)
            if room < 1:
                raise AsrSegmentationError("读取缓冲超过 600 秒", reason="window_too_long")
            request = min(room, remaining)
            chunk = _read_frames(handle, request)
            if chunk.ndim != 1 or len(chunk) != request:
                raise AsrSegmentationError("音频长度与采样数不一致", reason="length")
            pending = chunk if pending.size == 0 else np.concatenate([pending, chunk])
            file_pos += len(chunk)
        if file_pos < total:
            if len(pending) != _WINDOW_SAMPLES:
                raise AsrSegmentationError("读取未能填满 600 秒窗口", reason="length")
            pieces = _pieces_from_splitter(splitter, pending)
            if len(pieces) == 1:
                yielded = pieces[0][0]
                yield RecognitionWindow(emitted, yielded)
                emitted += len(yielded)
                pending = np.zeros(0, dtype=np.float32)
            else:
                for yielded, _offset in pieces[:-1]:
                    yield RecognitionWindow(emitted, yielded)
                    emitted += len(yielded)
                pending = np.array(pieces[-1][0], dtype=np.float32, copy=True)
            continue
        if pending.size:
            yield RecognitionWindow(emitted, pending)
            emitted += len(pending)
        break
    if emitted != total:
        raise AsrSegmentationError("窗口没有覆盖全部采样", reason="coverage")


def _read_frames(handle: object, request: int) -> np.ndarray:
    try:
        chunk = handle.read(int(request), dtype="float32", always_2d=False)  # type: ignore[attr-defined]
    except AsrSegmentationError:
        raise
    except Exception as exc:
        raise AsrSegmentationError("音频无法读取", reason="unreadable") from exc
    return np.asarray(chunk, dtype=np.float32)


def _pieces_from_splitter(
    splitter: AudioSplitter, buffer: np.ndarray
) -> list[tuple[np.ndarray, int]]:
    try:
        produced = splitter.split_audio(buffer, window_seconds=RECOGNITION_WINDOW_SECONDS)
    except AsrSegmentationError:
        raise
    except Exception as exc:
        raise AsrSegmentationError("切分器失败", reason="splitter_failed") from exc
    pieces = _pair_list(produced)
    cursor = 0
    validated: list[tuple[np.ndarray, int]] = []
    for chunk, offset in pieces:
        if isinstance(offset, (bool, np.bool_)) or not isinstance(offset, (int, np.integer)):
            raise AsrSegmentationError("切分偏移无效", reason="splitter_offset")
        start = int(offset)
        if start != cursor:
            raise AsrSegmentationError("切分偏移不连续", reason="splitter_offset")
        array = _chunk_matching(buffer, chunk, cursor)
        validated.append((array, start))
        cursor += len(array)
    if cursor != len(buffer):
        raise AsrSegmentationError("切分没有覆盖缓冲", reason="splitter_coverage")
    return validated


def _pieces_ignoring_time_offsets(
    waveform: np.ndarray, produced: object
) -> list[tuple[np.ndarray, int]]:
    """Upstream offsets are seconds and are not sample positions."""
    pieces = _pair_list(produced)
    cursor = 0
    validated: list[tuple[np.ndarray, int]] = []
    for chunk, _offset_seconds in pieces:
        array = _chunk_matching(waveform, chunk, cursor)
        validated.append((array, cursor))
        cursor += len(array)
    if cursor != len(waveform):
        raise AsrSegmentationError("低能量切分没有覆盖缓冲", reason="splitter_coverage")
    return validated


def _pair_list(produced: object) -> list[tuple[object, object]]:
    if isinstance(produced, (str, bytes)) or not isinstance(produced, Iterable):
        raise AsrSegmentationError("切分结果为空", reason="splitter_empty")
    pieces = list(produced)
    if not pieces:
        raise AsrSegmentationError("切分结果为空", reason="splitter_empty")
    pairs: list[tuple[object, object]] = []
    for item in pieces:
        if not isinstance(item, tuple) or len(item) != 2:
            raise AsrSegmentationError("切分结果形状无效", reason="splitter_shape")
        pairs.append((item[0], item[1]))
    return pairs


def _chunk_matching(buffer: np.ndarray, chunk: object, cursor: int) -> np.ndarray:
    array = np.asarray(chunk)
    if array.ndim != 1 or array.size < 1:
        raise AsrSegmentationError("切分块形状无效", reason="splitter_shape")
    if int(array.size) > _WINDOW_SAMPLES:
        raise AsrSegmentationError("切分块超过 600 秒", reason="window_too_long")
    end = cursor + int(array.size)
    if end > len(buffer):
        raise AsrSegmentationError("切分块超出缓冲", reason="splitter_coverage")
    if not np.array_equal(array, buffer[cursor:end]):
        raise AsrSegmentationError("切分块改变了采样", reason="splitter_samples")
    return np.ascontiguousarray(array, dtype=np.float32)


def _is_match_char(char: str) -> bool:
    if char == "'":
        return True
    category = unicodedata.category(char)
    return category.startswith(("L", "N"))


def _canonical_key(text: str) -> tuple[str, list[int]]:
    """One source index per casefold character, so ß and İ keep a single source offset."""
    chars: list[str] = []
    owners: list[int] = []
    for index, char in enumerate(text):
        if not _is_match_char(char):
            continue
        for piece in char.casefold():
            # Casefold marks (İ → i + dot) are not match characters and must not
            # shift the following source offsets.
            if _is_match_char(piece):
                chars.append(piece)
                owners.append(index)
    return "".join(chars), owners


def _distance_accepted(distance: int, canonical: int) -> bool:
    return distance * 25 <= canonical * 3


def _within_margin(distance: int, best: int, canonical: int) -> bool:
    return (distance - best) * 25 <= canonical * 2


def _best_anchor(source_key: str, query_key: str) -> tuple[int, int, int]:
    """Semi-global distance. The pattern is consumed; the source start is free."""
    full = len(source_key)
    pattern = len(query_key)
    previous_distance = [0] * (full + 1)
    previous_start = list(range(full + 1))
    for index in range(1, pattern + 1):
        current_distance = [0] * (full + 1)
        current_start = [0] * (full + 1)
        current_distance[0] = index
        current_start[0] = 0
        wanted = query_key[index - 1]
        for column in range(1, full + 1):
            cost = 0 if wanted == source_key[column - 1] else 1
            options = (
                (previous_distance[column - 1] + cost, previous_start[column - 1]),
                (previous_distance[column] + 1, previous_start[column]),
                (current_distance[column - 1] + 1, current_start[column - 1]),
            )
            chosen = options[0]
            for candidate in options[1:]:
                if candidate[0] < chosen[0] or (
                    candidate[0] == chosen[0] and candidate[1] < chosen[1]
                ):
                    chosen = candidate
            current_distance[column] = chosen[0]
            current_start[column] = chosen[1]
        previous_distance = current_distance
        previous_start = current_start
    candidates = [
        (previous_distance[end], previous_start[end], end)
        for end in range(full + 1)
        if previous_start[end] <= end
    ]
    best_distance = min(item[0] for item in candidates)
    pool = [item for item in candidates if item[0] == best_distance]
    pool.sort(key=lambda item: (item[1], item[2] - item[1], item[2]))
    distance, start, end = pool[0]
    return distance, start, end


def _has_rival(source_key: str, query_key: str, best: int, start: int, end: int) -> bool:
    full = len(source_key)
    pattern = len(query_key)
    previous_distance = [0] * (full + 1)
    previous_start = list(range(full + 1))
    for index in range(1, pattern + 1):
        current_distance = [0] * (full + 1)
        current_start = [0] * (full + 1)
        current_distance[0] = index
        current_start[0] = 0
        wanted = query_key[index - 1]
        for column in range(1, full + 1):
            cost = 0 if wanted == source_key[column - 1] else 1
            options = (
                (previous_distance[column - 1] + cost, previous_start[column - 1]),
                (previous_distance[column] + 1, previous_start[column]),
                (current_distance[column - 1] + 1, current_start[column - 1]),
            )
            chosen = options[0]
            for candidate in options[1:]:
                if candidate[0] < chosen[0] or (
                    candidate[0] == chosen[0] and candidate[1] < chosen[1]
                ):
                    chosen = candidate
            current_distance[column] = chosen[0]
            current_start[column] = chosen[1]
        previous_distance = current_distance
        previous_start = current_start
    for column in range(full + 1):
        distance = previous_distance[column]
        candidate_start = previous_start[column]
        if candidate_start > column or not _within_margin(distance, best, pattern):
            continue
        if column <= start or end <= candidate_start:
            return True
    return False


def _as_milliseconds(value: object) -> int:
    if isinstance(value, (bool, np.bool_)):
        raise AsrSegmentationError("时间必须是整数", reason="nonintegral")
    if isinstance(value, (int, np.integer)):
        number = int(value)
    elif isinstance(value, (float, np.floating)):
        if not math.isfinite(float(value)):
            raise AsrSegmentationError("时间不是有限数", reason="nonfinite")
        if not float(value).is_integer():
            raise AsrSegmentationError("时间必须是整数", reason="nonintegral")
        number = int(value)
    else:
        raise AsrSegmentationError("时间必须是整数", reason="nonintegral")
    if number < 0:
        raise AsrSegmentationError("时间不能为负", reason="negative")
    return number


def _as_sample(value: object, *, reason: str = "coverage") -> int:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)):
        raise AsrSegmentationError("采样位置无效", reason=reason)
    number = int(value)
    if number < 0:
        raise AsrSegmentationError("采样位置无效", reason=reason)
    return number


def _char_span(span: tuple[int, int]) -> tuple[int, int]:
    start, end = span
    if isinstance(start, bool) or isinstance(end, bool):
        raise AsrSegmentationError("字符区间无效", reason="coverage")
    if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end:
        raise AsrSegmentationError("字符区间无效", reason="coverage")
    return start, end


def _clamp_edge(
    index: int,
    edge: str,
    value: int,
    limit: int,
    grid: int,
    origin: int,
) -> tuple[int, TimeLimit | None]:
    if value <= limit:
        return value, None
    overflow = value - limit
    if overflow > grid:
        raise AsrSegmentationError("时间超出音频末端一个网格以上", reason="overflow")
    return limit, TimeLimit(index, edge, value, limit, limit - value, grid, origin)


def _cut_preference(
    source: str, left: OperationalToken, right: OperationalToken
) -> tuple[str, int]:
    trimmed = source[left.char_start : left.char_end].rstrip(" \t")
    if trimmed and trimmed[-1] in _BOUNDARY_PUNCTUATION:
        return "punctuation", 0
    if int(right.start_ms) - int(left.end_ms) >= TIMESTAMP_GRID_MS:
        return "pause", 0
    return "time", 1


def _segment_limit_ms(segment_seconds: object) -> int:
    if (
        type(segment_seconds) is not int
        or not MIN_SEGMENT_SECONDS <= segment_seconds <= MAX_SEGMENT_SECONDS
    ):
        raise AsrSegmentationError("展示段上限必须是 5 到 60 秒", reason="segment_limit")
    return segment_seconds * 1000


def _window_audio(audio: object) -> np.ndarray:
    try:
        waveform = np.array(np.asarray(audio), dtype=np.float32, copy=True)
    except (TypeError, ValueError) as exc:
        raise AsrSegmentationError("音频形状无效", reason="audio_shape") from exc
    if waveform.ndim != 1:
        raise AsrSegmentationError("音频必须是一维数组", reason="audio_shape")
    if waveform.size > _WINDOW_SAMPLES:
        raise AsrSegmentationError("识别窗口超过 600 秒", reason="window_too_long")
    if waveform.size == 0:
        raise AsrSegmentationError("识别窗口没有采样", reason="length")
    return waveform


@dataclass(frozen=True)
class _Atom:
    tokens: tuple[OperationalToken, ...]

    @property
    def start_ms(self) -> int:
        return min(int(token.start_ms) for token in self.tokens)

    @property
    def end_ms(self) -> int:
        return max(int(token.end_ms) for token in self.tokens)

    @property
    def char_start(self) -> int:
        return int(self.tokens[0].char_start)

    @property
    def char_end(self) -> int:
        return int(self.tokens[-1].char_end)


def _display_atoms(
    tokens: Sequence[OperationalToken], spans: Sequence[tuple[int, int]]
) -> tuple[_Atom, ...]:
    count = len(tokens)
    parent = list(range(count))
    has_duration = [int(token.start_ms) < int(token.end_ms) for token in tokens]

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root
            has_duration[left_root] = has_duration[left_root] or has_duration[right_root]

    def overlaps(token: OperationalToken, span: tuple[int, int]) -> bool:
        return int(token.char_start) < span[1] and int(token.char_end) > span[0]

    for token in tokens:
        if int(token.start_ms) > int(token.end_ms):
            raise AsrSegmentationError("展示时间倒序", reason="time_bounds")
    for span in spans:
        members = [index for index, token in enumerate(tokens) if overlaps(token, span)]
        for other in members[1:]:
            union(members[0], other)
    for index in range(count):
        # Zero-time words inside a timed numeral already have a valid display group.
        if has_duration[find(index)]:
            continue
        if any(
            int(tokens[earlier].start_ms) < int(tokens[earlier].end_ms) for earlier in range(index)
        ):
            union(index, index - 1)
            continue
        follower = next(
            (
                item
                for item in range(index + 1, count)
                if int(tokens[item].start_ms) < int(tokens[item].end_ms)
            ),
            None,
        )
        if follower is None:
            raise AsrSegmentationError("零长度词不能单独成段", reason="zero_length_segment")
        union(index, follower)
    groups: dict[int, list[int]] = {}
    for index in range(count):
        groups.setdefault(find(index), []).append(index)
    ordered = sorted(groups.values(), key=lambda item: item[0])
    return tuple(_Atom(tuple(tokens[index] for index in indexes)) for indexes in ordered)


def _reject_overlong_atoms(atoms: Sequence[_Atom], limit_ms: int) -> None:
    for atom in atoms:
        duration = atom.end_ms - atom.start_ms
        positive = sum(int(token.start_ms) < int(token.end_ms) for token in atom.tokens)
        if positive == 0 or duration <= 0:
            raise AsrSegmentationError("零长度词不能单独成段", reason="zero_length_segment")
        if duration <= limit_ms:
            continue
        if positive == 1:
            raise AsrSegmentationError("单个对齐词超过展示上限", reason="token_too_long")
        raise AsrSegmentationError("受保护的数词超过展示上限", reason="protected_span_too_long")


def _pack_atoms(
    source: str,
    atoms: Sequence[_Atom],
    limit_ms: int,
    duration_ms: int,
) -> tuple[DisplaySegment, ...]:
    packed: list[list[_Atom]] = []
    current: list[_Atom] = []
    for atom in atoms:
        if current and atom.end_ms - current[0].start_ms > limit_ms:
            packed.append(current)
            current = [atom]
            continue
        current.append(atom)
    if current:
        packed.append(current)
    segments: list[DisplaySegment] = []
    for group in packed:
        char_start = group[0].char_start
        char_end = group[-1].char_end
        text = source[char_start:char_end]
        start_ms = group[0].start_ms
        end_ms = group[-1].end_ms
        if text == "":
            raise AsrSegmentationError("展示段文本为空", reason="empty_segment")
        if type(start_ms) is not int or type(end_ms) is not int:
            raise AsrSegmentationError("展示时间必须是整数", reason="time_bounds")
        if not 0 <= start_ms < end_ms <= duration_ms:
            raise AsrSegmentationError("展示时间超出音频", reason="time_bounds")
        segments.append(DisplaySegment(text, start_ms, end_ms, char_start, char_end))
    return tuple(segments)


def _check_display_coverage(source: str, segments: Sequence[DisplaySegment]) -> None:
    cursor = 0
    previous_end: int | None = None
    for segment in segments:
        if segment.char_start != cursor or segment.char_end < segment.char_start:
            raise AsrSegmentationError("展示段没有覆盖原文", reason="coverage")
        if previous_end is not None and segment.start_ms < previous_end:
            raise AsrSegmentationError("展示段时间重叠", reason="overlap")
        cursor = segment.char_end
        previous_end = segment.end_ms
    if cursor != len(source):
        raise AsrSegmentationError("展示段没有覆盖原文", reason="coverage")


def _cut_anchors(
    waveform: np.ndarray,
    text: str,
    nominals: Sequence[int],
    protected: Sequence[tuple[int, int]],
    recognizer: Recognizer,
    aligner: Aligner,
    anchor_max_tokens: int,
    origin: int,
) -> tuple[tuple[BoundaryTrace, ...], tuple[TimeLimit, ...]]:
    traces: list[BoundaryTrace] = []
    limits: list[TimeLimit] = []
    previous_sample = 0
    nominal_list = tuple(nominals)
    for index, nominal in enumerate(nominal_list):
        anchor_start, clip, match, shift = _locate_anchor_audio(
            waveform, text, nominal, recognizer, anchor_max_tokens
        )
        source_slice = text[match.source_start : match.source_end]
        aligned = aligner.align(np.array(clip, dtype=np.float32, copy=True), source_slice)
        spans = map_source_spans(source_slice, [item.text for item in aligned])
        operational, anchor_limits = bound_alignment_times(
            aligned,
            spans,
            window_limit_ms=samples_to_ms(len(clip)),
            audio_start_sample=origin + anchor_start,
        )
        limits.extend(anchor_limits)
        window_tokens = tuple(
            OperationalToken(
                token.text,
                token.raw_start_ms,
                token.raw_end_ms,
                token.native_start_ms,
                token.native_end_ms,
                _ms_on_slice(anchor_start, token.start_ms, len(waveform)),
                _ms_on_slice(anchor_start, token.end_ms, len(waveform)),
                match.source_start + token.char_start,
                match.source_start + token.char_end,
            )
            for token in operational
        )
        if index + 1 < len(nominal_list):
            horizon_ms = nominal_list[index + 1] + ALIGNMENT_SLACK_MS
            horizon = min(len(waveform), horizon_ms * SAMPLE_RATE // 1000)
        else:
            horizon = len(waveform)
        trace = choose_shared_boundary(
            text,
            window_tokens,
            nominal_ms=nominal,
            protected_spans=protected,
            previous_sample=previous_sample,
            audio_end_sample=horizon,
        )
        traces.append(
            BoundaryTrace(
                trace.nominal_ms,
                trace.sample,
                trace.char_index,
                trace.delta_ms,
                trace.preference,
                trace.token_index,
                shift,
            )
        )
        previous_sample = trace.sample
    return tuple(traces), tuple(limits)


def _locate_anchor_audio(
    waveform: np.ndarray,
    text: str,
    nominal: int,
    recognizer: Recognizer,
    max_tokens: int,
) -> tuple[int, np.ndarray, AnchorMatch, int]:
    """Retry poor-distance queries nearby, keeping the same match and cut guards."""
    last_error = None
    for shift in (0, 5_000, -5_000):
        start = (nominal - ALIGNMENT_SLACK_MS + shift) * SAMPLE_RATE // 1000
        end = start + _ANCHOR_SAMPLES
        if start < 0 or end > len(waveform):
            continue
        clip = np.array(waveform[start:end], dtype=np.float32, copy=True)
        result = recognizer.recognize(clip, max_tokens=max_tokens)
        query = getattr(result, "text", None)
        if not isinstance(query, str):
            raise AsrSegmentationError("识别结果缺少文本", reason="recognizer_result")
        try:
            match = locate_anchor(text, query)
        except AsrSegmentationError as exc:
            if exc.reason != "anchor_distance":
                raise
            last_error = exc
            continue
        return start, clip, match, shift
    if last_error is not None:
        raise last_error
    raise AsrSegmentationError("锚点超出音频", reason="alignment_window")


def _align_pieces(
    waveform: np.ndarray,
    text: str,
    boundaries: Sequence[BoundaryTrace],
    aligner: Aligner,
    origin: int,
    *,
    recognizer: Recognizer | None = None,
    anchor_max_tokens: int = ANCHOR_MAX_TOKENS,
    retries: list[AlignmentRetry] | None = None,
) -> tuple[tuple[OperationalToken, ...], tuple[TimeLimit, ...]]:
    sample_edges = [0, *[boundary.sample for boundary in boundaries], len(waveform)]
    char_edges = [0, *[boundary.char_index for boundary in boundaries], len(text)]
    file_end = origin + len(waveform)
    display: list[OperationalToken] = []
    limits: list[TimeLimit] = []
    retry_records = [] if retries is None else retries
    pieces = zip(sample_edges[:-1], sample_edges[1:], char_edges[:-1], char_edges[1:], strict=True)
    for sample_start, sample_end, char_start, char_end in pieces:
        if sample_end <= sample_start or sample_end - sample_start > _LIMIT_SAMPLES:
            raise AsrSegmentationError("主对齐窗口超过 180 秒", reason="alignment_window")
        if char_end <= char_start:
            raise AsrSegmentationError("主对齐窗口没有原文", reason="empty_window")
        piece = waveform[sample_start:sample_end]
        slice_text = text[char_start:char_end]
        aligned = aligner.align(np.array(piece, dtype=np.float32, copy=True), slice_text)
        spans = map_source_spans(slice_text, [item.text for item in aligned])
        try:
            operational, piece_limits = bound_alignment_times(
                aligned,
                spans,
                window_limit_ms=samples_to_ms(len(piece)),
                audio_start_sample=origin + sample_start,
            )
        except AsrSegmentationError as exc:
            if exc.reason != "gross_repair" or recognizer is None:
                raise
            aligned, local_limits = _realign_anomalies(
                piece,
                slice_text,
                aligned,
                spans,
                recognizer,
                aligner,
                anchor_max_tokens,
                origin + sample_start,
                char_start,
                retry_records,
            )
            operational, piece_limits = bound_alignment_times(
                aligned,
                spans,
                window_limit_ms=samples_to_ms(len(piece)),
                audio_start_sample=origin + sample_start,
            )
            limits.extend(local_limits)
        limits.extend(
            TimeLimit(
                item.token_index,
                item.edge,
                item.original_ms,
                item.bounded_ms,
                item.delta_ms,
                item.threshold_ms,
                item.audio_start_sample,
            )
            for item in piece_limits
        )
        for token in operational:
            display.append(
                OperationalToken(
                    slice_text[token.char_start : token.char_end],
                    token.raw_start_ms,
                    token.raw_end_ms,
                    token.native_start_ms,
                    token.native_end_ms,
                    _ms_on_slice(origin + sample_start, token.start_ms, file_end),
                    _ms_on_slice(origin + sample_start, token.end_ms, file_end),
                    char_start + token.char_start,
                    char_start + token.char_end,
                )
            )
    return tuple(display), tuple(limits)


def _realign_anomalies(
    audio: np.ndarray,
    text: str,
    tokens: Sequence[AlignedToken],
    spans: Sequence[tuple[int, int]],
    recognizer: Recognizer,
    aligner: Aligner,
    max_tokens: int,
    origin: int,
    char_origin: int,
    retries: list[AlignmentRetry],
) -> tuple[tuple[AlignedToken, ...], tuple[TimeLimit, ...]]:
    """Reject gross predictions; obtain independent local predictions once per word.

    The anchor still locates the unchanged source. Only identical word positions
    may receive a new prediction. All local and combined times pass the original
    guards; no interpolation or threshold relaxation is performed.
    """
    anomalies = []
    for index, token in enumerate(tokens):
        raw_start, raw_end, start, end = (
            _as_milliseconds(value)
            for value in (
                token.raw_start_ms,
                token.raw_end_ms,
                token.native_start_ms,
                token.native_end_ms,
            )
        )
        if end < start:
            raise AsrSegmentationError("对齐时间倒序", reason="disordered")
        if max(abs(raw_start - start), abs(raw_end - end)) > MAX_REPAIR_MS:
            anomalies.append(index)
    if len(anomalies) + len(retries) > MAX_LOCAL_REALIGNS:
        raise AsrSegmentationError("局部对齐重试预算耗尽", reason="realign_budget")
    if len(audio) < _ANCHOR_SAMPLES:
        raise AsrSegmentationError("异常片段不足 30 秒，无法局部对齐", reason="gross_repair")
    replacements = list(tokens)
    limits: list[TimeLimit] = []
    for index in anomalies:
        original = tokens[index]
        center = min(
            max(int(original.native_start_ms), ALIGNMENT_SLACK_MS),
            len(audio) * 1000 // SAMPLE_RATE - ALIGNMENT_SLACK_MS,
        )
        offset_ms = center - ALIGNMENT_SLACK_MS
        offset = offset_ms * SAMPLE_RATE // 1000
        clip = np.array(audio[offset : offset + _ANCHOR_SAMPLES], dtype=np.float32, copy=True)
        query = getattr(recognizer.recognize(clip, max_tokens=max_tokens), "text", None)
        if not isinstance(query, str):
            raise AsrSegmentationError("识别结果缺少文本", reason="recognizer_result")
        match = locate_anchor(text, query)
        local_text = text[match.source_start : match.source_end]
        local = aligner.align(clip, local_text)
        local_spans = map_source_spans(local_text, [item.text for item in local])
        _, local_limits = bound_alignment_times(
            local,
            local_spans,
            window_limit_ms=samples_to_ms(len(clip)),
            audio_start_sample=origin + offset,
        )
        candidates = [
            token
            for token, span in zip(local, local_spans, strict=True)
            if match.source_start + span[0] == spans[index][0] and token.text == original.text
        ]
        if len(candidates) != 1:
            raise AsrSegmentationError("局部对齐没有定位异常词", reason="anchor_token_missing")
        token = candidates[0]
        replacement = AlignedToken(
            token.text,
            int(token.raw_start_ms) + offset_ms,
            int(token.raw_end_ms) + offset_ms,
            int(token.native_start_ms) + offset_ms,
            int(token.native_end_ms) + offset_ms,
        )
        replacements[index] = replacement
        retries.append(
            AlignmentRetry(
                index,
                char_origin + spans[index][0],
                origin + offset,
                len(clip),
                original,
                replacement,
            )
        )
        limits.extend(local_limits)
    return tuple(replacements), tuple(limits)


def _ms_on_slice(origin_sample: int, local_ms: int, end_sample: int) -> int:
    """Convert one aligner millisecond to an absolute sample, then round once."""
    sample = origin_sample + int(local_ms) * SAMPLE_RATE // 1000
    if sample > end_sample:
        sample = end_sample
    if sample < 0:
        sample = 0
    return samples_to_ms(sample)
