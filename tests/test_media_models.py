from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from douyin_wiki.adapters.media_models import (
    BackendUnavailableError,
    RapidOCREngine,
    SelectedOCR,
    SelectedTranscriber,
)
from douyin_wiki.config import MediaSettings
from douyin_wiki.models import TranscriptSegment
from douyin_wiki.review import detect_review_issues


def test_rapidocr_keeps_frame_location_and_multiline_text(tmp_path: Path) -> None:
    frame = tmp_path / "frame.jpg"
    engine = RapidOCREngine()
    engine._engine = lambda _path: SimpleNamespace(txts=["第一行", "第二行"], scores=[0.8, 0.9])
    result = engine._recognize_sync([(5000, frame)])
    assert len(result) == 1
    assert result[0].text == "第一行\n第二行"
    assert result[0].source_index == result[0].timestamp_ms == 5000
    assert result[0].image_path == str(frame)
    assert result[0].confidence == pytest.approx(0.85)


@pytest.mark.asyncio
async def test_auto_ocr_fallback_and_explicit_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    selector = SelectedOCR(MediaSettings(), Path("vision.swift"))

    async def unavailable() -> None:
        raise BackendUnavailableError("model unavailable")

    async def vision(_frames):
        return []

    monkeypatch.setattr(selector.rapidocr, "ensure_ready", unavailable)
    monkeypatch.setattr(selector.vision, "recognize", vision)
    assert await selector.recognize([(1, Path("frame.jpg"))]) == []
    assert selector.provenance["provider"] == "vision"
    strict = SelectedOCR(MediaSettings(ocr_provider="rapidocr"), Path("vision.swift"))
    monkeypatch.setattr(strict.rapidocr, "ensure_ready", unavailable)
    with pytest.raises(BackendUnavailableError):
        await strict.recognize([(1, Path("frame.jpg"))])


@pytest.mark.parametrize("text", ["AI工具", "5折。", "1000元", "普通句子"])
def test_unknown_sensevoice_confidence_is_not_a_review_reason(text) -> None:
    segments = [TranscriptSegment(id=2, start_ms=0, end_ms=1000, text=text)]
    assert detect_review_issues(segments) == []


def test_selectors_construct_backends_lazily() -> None:
    asr = SelectedTranscriber(MediaSettings(asr_provider="whisper"))
    assert asr._qwen3 is None
    _ = asr.qwen3
    assert asr._qwen3 is not None
    assert not hasattr(asr, "whisper") and not hasattr(asr, "sensevoice")

    ocr = SelectedOCR(MediaSettings(ocr_provider="vision"), Path("vision.swift"))
    assert ocr._rapidocr is None and ocr._vision is None
    _ = ocr.vision
    assert ocr._vision is not None and ocr._rapidocr is None
