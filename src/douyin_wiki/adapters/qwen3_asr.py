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
from .asr_segments import (
    AsrSegmentationError,
    iter_recognition_windows,
    samples_to_ms,
    segment_recognition_window,
    time_limit_summary,
)
from .asr_text import clean_qwen_text, render_normalized_slices
from .media_models import BackendUnavailableError
from .qwen3_runtime import Qwen3Runtime

logger = logging.getLogger(__name__)


class _WindowRuntime:
    def __init__(self, runtime, language: str) -> None:
        self.runtime, self.language = runtime, language

    def recognize(self, audio, *, max_tokens):
        return self.runtime.recognize(audio, max_tokens=max_tokens, language=self.language)

    def align(self, audio, text):
        return self.runtime.align(audio, text, language=self.language)


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
            )
        except BaseException:
            self.provenance, self.metrics, self.last_trace = {}, {}, ()
            raise
        # No await here: metadata and result belong to this caller together.
        self.provenance, self.metrics, self.last_trace = provenance, metrics, trace
        return result

    def _transcribe_sync(self, audio_path: Path):
        with self._lock:
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
                    full = self.runtime.recognize(window.audio, max_tokens=8192)
                    text = clean_qwen_text(full.text)
                    scoped = _WindowRuntime(self.runtime, full.language)
                    aligned = segment_recognition_window(
                        window.audio,
                        text,
                        recognizer=scoped,
                        aligner=scoped,
                        start_sample=window.start_sample,
                        segment_seconds=self.settings.asr_segment_seconds,
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
            provenance = {
                "provider": "qwen3",
                "model": self.settings.asr_model,
                "revision": self.settings.asr_model_revision,
                "vad_model": self.settings.vad_model,
                "vad_revision": self.settings.vad_model_revision,
                "alignment_model": self.settings.alignment_model,
                "alignment_revision": self.settings.alignment_model_revision,
                "segmentation": "qwen3-anchored-alignment-v1",
                "timestamp_kind": "forced-alignment",
                "recognition_window_seconds": 600,
                "alignment_target_seconds": 150,
                "alignment_window_limit_seconds": 180,
                "boundary_limit_count": summary["count"],
                "boundary_limit_max_delta_ms": summary["max_abs_delta_ms"],
                "language": ",".join(dict.fromkeys(languages)) or self.settings.asr_language,
                "itn": "zh-numbers-v1" if self.settings.asr_itn else "disabled",
            }
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
