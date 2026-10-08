"""Optional local ASR and OCR engines and their selection rules."""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from ..config import MediaSettings
from ..errors import ExternalToolError
from ..models import OCRObservation, TranscriptSegment

if TYPE_CHECKING:
    from .media import VisionOCR

logger = logging.getLogger(__name__)

# First element of OCR frame tuples: video extract_frames uses milliseconds;
# image-note callers may pass an ordinal later remapped onto image_index.
FrameStampMs = int


class Transcriber(Protocol):
    async def transcribe(self, audio_path: Path, output_dir: Path) -> list[TranscriptSegment]: ...


class OCREngine(Protocol):
    async def recognize(self, frames: list[tuple[FrameStampMs, Path]]) -> list[OCRObservation]: ...


class BackendUnavailableError(ExternalToolError):
    """The selected engine cannot be initialized before processing input."""


class RapidOCREngine:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._engine = None

    def _ensure_ready(self) -> None:
        with self._lock:
            if self._engine is not None:
                return
            try:
                from rapidocr import EngineType, ModelType, OCRVersion, RapidOCR
            except ImportError as exc:
                raise BackendUnavailableError("未安装 RapidOCR；运行 uv sync --extra ocr") from exc
            params = {
                "Det.engine_type": EngineType.ONNXRUNTIME,
                "Det.model_type": ModelType.SMALL,
                "Det.ocr_version": OCRVersion.PPOCRV6,
                "Rec.engine_type": EngineType.ONNXRUNTIME,
                "Rec.model_type": ModelType.SMALL,
                "Rec.ocr_version": OCRVersion.PPOCRV6,
                # CoreML emits model-shape errors on the verified macOS runtime.
                # ONNX Runtime CPU processes the same sample without those errors.
                "EngineConfig.onnxruntime.use_coreml": False,
            }
            try:
                self._engine = RapidOCR(params=params)
            except Exception as exc:
                raise BackendUnavailableError(f"RapidOCR small 模型无法加载：{exc}") from exc

    async def ensure_ready(self) -> None:
        await asyncio.to_thread(self._ensure_ready)

    async def recognize(self, frames: list[tuple[FrameStampMs, Path]]) -> list[OCRObservation]:
        if not frames:
            return []
        await self.ensure_ready()
        return await asyncio.to_thread(self._recognize_sync, frames)

    def _recognize_sync(self, frames: list[tuple[FrameStampMs, Path]]) -> list[OCRObservation]:
        observations: list[OCRObservation] = []
        with self._lock:
            try:
                for timestamp_ms, path in frames:
                    result = self._engine(str(path))
                    texts = getattr(result, "txts", None)
                    scores = getattr(result, "scores", None)
                    lines = [
                        str(value).strip()
                        for value in (texts if texts is not None else [])
                        if str(value).strip()
                    ]
                    if not lines:
                        continue
                    confidence = None
                    if scores is not None:
                        values = [float(score) for score in scores]
                        if (
                            values
                            and len(values) == len(texts)
                            and all(0 <= value <= 1 for value in values)
                        ):
                            confidence = sum(values) / len(values)
                    observations.append(
                        OCRObservation(
                            timestamp_ms=timestamp_ms,
                            source_index=timestamp_ms,
                            text="\n".join(lines),
                            confidence=confidence,
                            image_path=str(path),
                        )
                    )
            except Exception as exc:
                raise ExternalToolError(f"RapidOCR 识别失败：{exc}") from exc
        return observations


class SelectedTranscriber:
    """Keep the transcription entry point; construct Qwen3 only when used."""

    def __init__(self, settings: MediaSettings) -> None:
        self.settings = settings
        self._qwen3 = None
        self.provenance: dict = {}

    @property
    def qwen3(self):
        if self._qwen3 is None:
            from .qwen3_asr import Qwen3Transcriber

            self._qwen3 = Qwen3Transcriber(self.settings)
        return self._qwen3

    async def transcribe(self, audio_path: Path, output_dir: Path) -> list[TranscriptSegment]:
        self.provenance = {}
        result = await self.qwen3.transcribe(audio_path, output_dir)
        self.provenance = dict(self.qwen3.provenance)
        return result


class SelectedOCR:
    """Pick RapidOCR or Vision; backends are constructed only when selected."""

    def __init__(self, settings: MediaSettings, vision_script: Path) -> None:
        self.settings = settings
        self._vision_script = vision_script
        self._rapidocr: RapidOCREngine | None = None
        self._vision: VisionOCR | None = None
        self.provenance: dict[str, str] = {}

    @property
    def rapidocr(self) -> RapidOCREngine:
        if self._rapidocr is None:
            self._rapidocr = RapidOCREngine()
        return self._rapidocr

    @property
    def vision(self) -> VisionOCR:
        if self._vision is None:
            from .media import VisionOCR

            self._vision = VisionOCR(self._vision_script)
        return self._vision

    async def recognize(self, frames: list[tuple[FrameStampMs, Path]]) -> list[OCRObservation]:
        self.provenance = {}
        if not frames:
            return []
        provider = self.settings.ocr_provider
        if provider != "vision":
            try:
                await self.rapidocr.ensure_ready()
            except BackendUnavailableError as exc:
                if provider != "auto":
                    raise
                logger.warning("RapidOCR unavailable; using Vision: %s", exc)
                self.provenance = {
                    "provider": "vision",
                    "model": "macOS Vision",
                    "fallback_reason": str(exc),
                }
            else:
                self.provenance = {"provider": "rapidocr", "model": "PP-OCRv6 small"}
                return await self.rapidocr.recognize(frames)
        if not self.provenance:
            self.provenance = {"provider": "vision", "model": "macOS Vision"}
        return await self.vision.recognize(frames)
