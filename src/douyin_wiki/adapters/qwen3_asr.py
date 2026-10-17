"""Task-serialized Qwen3 recognition, anchored alignment and existing transcript schema."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np

from ..config import MediaSettings
from ..errors import ExternalToolError
from ..models import TranscriptSegment
from .asr_checkpoints import AsrCheckpoints
from .asr_segments import (
    ALIGNMENT_LIMIT_MS,
    MIN_ANCHOR_CHARS,
    SAMPLE_RATE,
    AsrSegmentationError,
    DisplaySegment,
    RecognitionWindow,
    TimeLimit,
    WindowSegmentation,
    iter_recognition_windows,
    samples_to_ms,
    segment_recognition_window,
    time_limit_summary,
)
from .asr_text import clean_qwen_text, render_normalized_slices
from .media_models import BackendUnavailableError
from .qwen3_runtime import Qwen3Runtime, RuntimeRecognition

logger = logging.getLogger(__name__)


class _WindowRuntime:
    def __init__(self, runtime, language: str) -> None:
        self.runtime, self.language = runtime, language

    def recognize(self, audio, *, max_tokens):
        return self.runtime.recognize(audio, max_tokens=max_tokens, language=self.language)

    def align(self, audio, text):
        return self.runtime.align(audio, text, language=self.language)


def _sparse_alignment_window(window: RecognitionWindow, text: str, runtime) -> RecognitionWindow:
    """Keep recognition whole; trim only long outside silence before alignment."""
    if not (
        30 * SAMPLE_RATE < len(window.audio) <= ALIGNMENT_LIMIT_MS * SAMPLE_RATE // 1000
        and len(text) < MIN_ANCHOR_CHARS
    ):
        return window
    ranges = runtime.speech_ranges(window.audio, complete=True)
    if not ranges:
        raise ExternalToolError("Silero 完整扫描未找到已识别的语音")
    start, end = min(item[0] for item in ranges), max(item[1] for item in ranges)
    if end - start >= len(window.audio) // 2:
        return window
    return RecognitionWindow(window.start_sample + start, window.audio[start:end])


class Qwen3Transcriber:
    def __init__(self, settings: MediaSettings, *, runtime=None) -> None:
        self.settings = settings
        self.runtime = Qwen3Runtime(settings) if runtime is None else runtime
        self._lock = threading.Lock()
        self.provenance: dict = {}
        self.metrics: dict = {}
        self.last_trace: tuple[dict, ...] = ()

    async def transcribe(self, audio_path: Path, output_dir: Path) -> list[TranscriptSegment]:
        try:
            result, provenance, metrics, trace = await asyncio.to_thread(
                self._transcribe_sync,
                audio_path,
                output_dir,
            )
        except BaseException:
            self.provenance, self.metrics, self.last_trace = {}, {}, ()
            raise
        # No await here: metadata and result belong to this caller together.
        self.provenance, self.metrics, self.last_trace = provenance, metrics, trace
        return result

    def _transcribe_sync(self, audio_path: Path, output_dir: Path | None = None):
        with self._lock:
            checkpoints = AsrCheckpoints(audio_path, output_dir, self.settings)
            started = time.perf_counter()
            load_seconds = 0.0
            duration_samples = 0
            segments = []
            traces = []
            limits = []
            languages = []
            self.runtime.alignment_trace = []
            self.runtime.pending_import_seconds = 0.0
            try:
                ready = False
                for window in iter_recognition_windows(audio_path, self.runtime):
                    if not np.isfinite(window.audio).all():
                        raise ExternalToolError("Qwen3 输入包含非有限音频采样")
                    duration_samples = window.start_sample + len(window.audio)
                    if not ready:
                        loading = time.perf_counter()
                        self.runtime.ensure_ready()
                        load_seconds = (
                            time.perf_counter() - loading + self.runtime.pending_import_seconds
                        )
                        self.runtime.memory_stats(reset_peak=True)
                        ready = True
                    if not self.runtime.speech_ranges(window.audio):
                        traces.append(
                            {
                                "start_sample": window.start_sample,
                                "samples": len(window.audio),
                                "silent": True,
                            }
                        )
                        continue
                    cached = checkpoints.read("recognition", window.start_sample, len(window.audio))
                    full = None
                    if cached and isinstance(cached.get("text"), str) and cached["text"]:
                        try:
                            candidate = RuntimeRecognition(**cached)
                            if (
                                isinstance(candidate.language, str)
                                and candidate.language
                                and isinstance(candidate.generation_tokens, int)
                                and 0 < candidate.generation_tokens < 8192
                                and candidate.internal_segments == 1
                            ):
                                full = candidate
                        except TypeError:
                            pass
                    if full is None:
                        full = self.runtime.recognize(window.audio, max_tokens=8192)
                        checkpoints.write(
                            "recognition",
                            window.start_sample,
                            len(window.audio),
                            {
                                key: getattr(full, key)
                                for key in (
                                    "text",
                                    "language",
                                    "generation_tokens",
                                    "internal_segments",
                                )
                            },
                        )
                    text = clean_qwen_text(full.text)
                    first_segment = len(segments)
                    cached = checkpoints.read(
                        "alignment", window.start_sample, len(window.audio), text
                    )
                    if cached:
                        try:
                            restored = [
                                TranscriptSegment.model_validate(row) for row in cached["segments"]
                            ]
                            expected = "".join(
                                render_normalized_slices(
                                    text, ((0, len(text)),), itn=self.settings.asr_itn
                                )
                            )
                            previous = samples_to_ms(window.start_sample)
                            for row in restored:
                                if (
                                    not previous
                                    <= row.start_ms
                                    < row.end_ms
                                    <= samples_to_ms(duration_samples)
                                ):
                                    raise ValueError("cached time range")
                                previous = row.end_ms
                            if not restored or "".join(row.text for row in restored) != expected:
                                raise ValueError("cached text coverage")
                            restored_limits = [TimeLimit(**row) for row in cached["limits"]]
                            if cached["trace"]["raw_text"] != text:
                                raise ValueError("cached trace")
                        except (KeyError, ValueError, TypeError):
                            cached = None
                        else:
                            for row in restored:
                                segments.append(row.model_copy(update={"id": len(segments)}))
                            limits.extend(restored_limits)
                            languages.append(full.language)
                            traces.append(cached["trace"])
                            continue
                    scoped = _WindowRuntime(self.runtime, full.language)
                    alignment_window = window
                    degraded_reason = None
                    try:
                        alignment_window = _sparse_alignment_window(window, text, self.runtime)
                        aligned = segment_recognition_window(
                            alignment_window.audio,
                            text,
                            recognizer=scoped,
                            aligner=scoped,
                            start_sample=alignment_window.start_sample,
                            segment_seconds=self.settings.asr_segment_seconds,
                        )
                    except AsrSegmentationError as exc:
                        if (
                            self.settings.alignment_failure_policy != "retain_window"
                            or exc.reason not in {"gross_repair", "anchor_token_missing"}
                            or not text.strip()
                                or len(text) > 20_000
                        ):
                            raise
                        degraded_reason = exc.reason
                        aligned = WindowSegmentation(
                            (
                                DisplaySegment(
                                    text,
                                    samples_to_ms(window.start_sample),
                                    samples_to_ms(duration_samples),
                                    0,
                                    len(text),
                                ),
                            ),
                            (),
                            (),
                        )
                    rendered = render_normalized_slices(
                        text,
                        tuple((item.char_start, item.char_end) for item in aligned.segments),
                        itn=self.settings.asr_itn,
                    )
                    for item, display in zip(aligned.segments, rendered, strict=True):
                        segments.append(
                            TranscriptSegment(
                                id=len(segments),
                                start_ms=item.start_ms,
                                end_ms=item.end_ms,
                                text=display,
                                timestamp_precision="window" if degraded_reason else "aligned",
                                timestamp_start_ms=item.start_ms,
                                timestamp_end_ms=item.end_ms,
                                alignment_reason=degraded_reason,
                            )
                        )
                    limits.extend(aligned.time_limits)
                    languages.append(full.language)
                    traces.append(
                        {
                            "start_sample": window.start_sample,
                            "samples": len(window.audio),
                            "silent": False,
                            "raw_text": text,
                            "degraded_reason": degraded_reason,
                            "alignment_start_sample": alignment_window.start_sample,
                            "alignment_samples": len(alignment_window.audio),
                            "language": full.language,
                            "generation_tokens": full.generation_tokens,
                            "internal_segments": full.internal_segments,
                            "boundaries": [asdict(item) for item in aligned.boundaries],
                            "time_limits": [asdict(item) for item in aligned.time_limits],
                            "alignment_retries": [asdict(item) for item in aligned.retries],
                            "display_source_spans": [
                                (s.char_start, s.char_end) for s in aligned.segments
                            ],
                        }
                    )
                    checkpoints.write(
                        "alignment",
                        window.start_sample,
                        len(window.audio),
                        {
                            "segments": [
                                row.model_dump(mode="json") for row in segments[first_segment:]
                            ],
                            "limits": [asdict(item) for item in aligned.time_limits],
                            "trace": traces[-1],
                        },
                        text,
                    )
                duration_ms = samples_to_ms(duration_samples)
                previous = 0
                for item in segments:
                    if (
                        not (previous <= item.start_ms < item.end_ms <= duration_ms)
                        or not item.text
                    ):
                        raise ExternalToolError("Qwen3 最终展示段时间或文字无效")
                    previous = item.end_ms
            except Exception as exc:
                self.runtime.clear_after_failure()
                category = (
                    "unavailable" if isinstance(exc, BackendUnavailableError) else "inference"
                )
                logger.error("Qwen3 failed category=%s fallback=false: %s", category, exc)
                if isinstance(exc, ExternalToolError):
                    raise
                reason = f" [{exc.reason}]" if isinstance(exc, AsrSegmentationError) else ""
                raise ExternalToolError(f"Qwen3 转写失败{reason}：{exc}") from exc
            total_seconds = time.perf_counter() - started
            processing = max(0.0, total_seconds - load_seconds)
            seconds = duration_samples / 16000
            summary = time_limit_summary(limits)
            degraded = [
                {
                    "start_sample": row["start_sample"],
                    "samples": row["samples"],
                    "reason": row["degraded_reason"],
                }
                for row in traces
                if row.get("degraded_reason")
            ]
            provenance = {
                "provider": "qwen3",
                "model": self.settings.asr_model,
                "revision": self.settings.asr_model_revision,
                "vad_model": self.settings.vad_model,
                "vad_revision": self.settings.vad_model_revision,
                "alignment_model": self.settings.alignment_model,
                "alignment_revision": self.settings.alignment_model_revision,
                "segmentation": "qwen3-anchored-alignment-v2",
                "timestamp_kind": "mixed-window" if degraded else "forced-alignment",
                "recognition_window_seconds": 600,
                "alignment_target_seconds": 150,
                "alignment_window_limit_seconds": 180,
                "boundary_limit_count": summary["count"],
                "boundary_limit_max_delta_ms": summary["max_abs_delta_ms"],
                "language": ",".join(dict.fromkeys(languages)) or self.settings.asr_language,
                "itn": "zh-numbers-v1" if self.settings.asr_itn else "disabled",
            }
            if degraded:
                provenance["warnings"] = ["部分文字仅有识别窗口时间，不能作为精确句子时间"]
                provenance["degraded_windows"] = degraded
            metrics = {
                "initialization_seconds": load_seconds,
                "processing_seconds": processing,
                "total_seconds": total_seconds,
                "duration_seconds": seconds,
                "process_rtf": processing / seconds if seconds else 0.0,
                "total_rtf": total_seconds / seconds if seconds else 0.0,
                "segments": len(segments),
                **self.runtime.memory_stats(),
            }
            logger.info(
                "Qwen3 ASR provider=qwen3 model=%s revision=%s fallback=false metrics=%s",
                self.settings.asr_model,
                self.settings.asr_model_revision,
                metrics,
            )
            return segments, provenance, metrics, tuple(traces)
