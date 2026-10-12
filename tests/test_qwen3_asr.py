from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from douyin_wiki.adapters.asr_segments import AlignedToken, RecognitionWindow
from douyin_wiki.adapters.asr_text import render_normalized_slices
from douyin_wiki.adapters.media_models import BackendUnavailableError, SelectedTranscriber
from douyin_wiki.adapters.qwen3_asr import Qwen3Transcriber
from douyin_wiki.adapters.qwen3_runtime import (
    Qwen3Runtime,
    RuntimeRecognition,
    inspect_snapshot,
    load_fixed_model,
    resolve_snapshot,
    validate_language,
)
from douyin_wiki.config import MediaSettings
from douyin_wiki.errors import ExternalToolError


def settings(**kwargs) -> MediaSettings:
    return MediaSettings(
        asr_provider="qwen3",
        asr_model="mlx-community/Qwen3-ASR-1.7B-bf16",
        asr_model_revision="e1f6c266914abc5a46e8756e02580f834a6cf8a7",
        vad_model="mlx-community/silero-vad",
        vad_model_revision="7bc17f22d3c0451bd3a6cd71e759b009271ff49a",
        **kwargs,
    )


def test_constructor_is_lazy_in_a_fresh_process() -> None:
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; "
            "from douyin_wiki.adapters.qwen3_asr import Qwen3Transcriber; "
            "from douyin_wiki.config import MediaSettings; "
            "Qwen3Transcriber(MediaSettings()); "
            "assert not any(x in sys.modules "
            "for x in ('mlx', 'mlx.core', 'mlx_audio', 'transformers'))",
        ],
        check=True,
    )


@pytest.mark.parametrize("language", ["Chinese", "auto", "english", "Cantonese"])
def test_language_validation(language: str) -> None:
    normalized = validate_language(language, allow_auto=True)
    assert normalized == ("auto" if language == "auto" else language.title())


@pytest.mark.parametrize("language", ["Swedish", "zh", "", "English,Chinese", "auto"])
def test_unsupported_alignment_language_fails(language: str) -> None:
    with pytest.raises(ValueError):
        validate_language(language)


def snapshot(tmp_path: Path, config: dict | None = None) -> Path:
    path = tmp_path / ("a" * 40)
    path.mkdir()
    (path / "config.json").write_text(json.dumps(config or {"model_type": "qwen3_asr"}))
    (path / "model.safetensors").write_bytes(b"test-weights")
    return path


@pytest.mark.parametrize("unsafe", ["model.py", "weights.bin", "x.pickle", "x.pth"])
def test_unsafe_snapshot_files_are_rejected(tmp_path: Path, unsafe: str) -> None:
    path = snapshot(tmp_path)
    (path / unsafe).write_bytes(b"unsafe")
    with pytest.raises(BackendUnavailableError, match="不安全"):
        inspect_snapshot(path, "asr")


@pytest.mark.parametrize("filename", ["config.json", "tokenizer_config.json"])
def test_repository_hooks_are_rejected_in_all_json(tmp_path: Path, filename: str) -> None:
    path = snapshot(tmp_path)
    (path / filename).write_text(json.dumps({"nested": {"auto_map": {"x": "evil.X"}}}))
    with pytest.raises(BackendUnavailableError, match="auto_map"):
        inspect_snapshot(path, "asr")


def test_cached_aligner_has_asr_model_type(tmp_path: Path) -> None:
    path = snapshot(
        tmp_path,
        {
            "model_type": "qwen3_asr",
            "timestamp_segment_time": 80,
            "timestamp_token_id": 151705,
        },
    )
    assert inspect_snapshot(path, "alignment")["timestamp_segment_time"] == 80
    with pytest.raises(BackendUnavailableError):
        inspect_snapshot(path, "asr")


def test_snapshot_resolution_pins_revision_and_never_downloads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import huggingface_hub

    path = snapshot(tmp_path)
    calls = []
    monkeypatch.setattr(huggingface_hub, "snapshot_download", lambda **kw: calls.append(kw) or path)
    assert resolve_snapshot("org/model", "a" * 40) == path
    assert calls[0]["local_files_only"] is True
    assert calls[0]["revision"] == "a" * 40
    assert calls[0]["allow_patterns"] == ["*.safetensors", "*.json", "*.txt"]
    with pytest.raises(BackendUnavailableError):
        resolve_snapshot("org/model", "main")
    monkeypatch.setattr(huggingface_hub, "snapshot_download", lambda **kw: tmp_path)
    with pytest.raises(BackendUnavailableError, match="revision"):
        resolve_snapshot("org/model", "a" * 40)


def test_quantization_precedes_strict_keys_and_tokenizers_are_local(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import douyin_wiki.adapters.qwen3_runtime as module

    calls = []

    class Model:
        def __init__(self, config):
            self.config = config
            self._model = self
            self.quantized = False

        def sanitize(self, weights):
            return weights

        def parameters(self):
            calls.append("keys" if self.quantized else "keys-before-quantize")
            return (
                {"weight": object(), "scales": object()} if self.quantized else {"weight": object()}
            )

        def load_weights(self, weights, strict):
            calls.append(("load", strict))

        def eval(self):
            pass

    def quantize(model, *_args):
        calls.append("quantize")
        model.quantized = True

    class Tokenizer:
        @staticmethod
        def from_pretrained(path, **kwargs):
            calls.append(("tokenizer", kwargs))
            return SimpleNamespace()

    bindings = SimpleNamespace(
        Model=Model,
        Config=SimpleNamespace(from_dict=lambda value: SimpleNamespace(**value)),
        load_weights=lambda path: {"weight": object(), "scales": object()},
        quantize=quantize,
        flatten=lambda value: list(value.items()),
        mx=SimpleNamespace(eval=lambda value: None, synchronize=lambda: None),
        Tokenizer=Tokenizer,
        FeatureExtractor=Tokenizer,
    )
    monkeypatch.setattr(module, "_model_bindings", lambda kind: bindings)
    load_fixed_model("asr", snapshot(tmp_path), {"model_type": "qwen3_asr"})
    assert "keys-before-quantize" not in calls
    assert calls.index("quantize") < calls.index("keys") < calls.index(("load", True))
    options = [item[1] for item in calls if isinstance(item, tuple) and item[0] == "tokenizer"]
    assert all(item["local_files_only"] for item in options)
    assert options[0]["trust_remote_code"] is False
    bindings.load_weights = lambda path: {"unexpected": object()}
    with pytest.raises(BackendUnavailableError, match="权重"):
        load_fixed_model("asr", tmp_path, {"model_type": "qwen3_asr"})


def test_recognition_has_per_call_budget_and_no_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    import douyin_wiki.adapters.qwen3_runtime as module

    monkeypatch.setattr(module, "synchronize", lambda: None)
    runtime = Qwen3Runtime(settings())
    calls = []

    def generate(audio, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            text="测试", segments=[{"text": "测试", "language": "Chinese"}], generation_tokens=2
        )

    runtime._asr = SimpleNamespace(generate=generate)
    for budget in (8192, 2048, 8192):
        assert runtime.recognize(np.zeros(16000), max_tokens=budget).text == "测试"
    assert [item["max_tokens"] for item in calls] == [8192, 2048, 8192]
    assert all(item["temperature"] == 0 and item["batch_size"] == 1 for item in calls)
    assert all(item["system_prompt"] is None and item["hotwords"] is None for item in calls)
    assert all(item["chunk_duration"] >= 600 and item["min_chunk_duration"] == 0 for item in calls)


@pytest.mark.parametrize("bad", ["budget", "chunks", "partial", "empty", "language"])
def test_incomplete_recognition_is_an_error(bad: str, monkeypatch: pytest.MonkeyPatch) -> None:
    import douyin_wiki.adapters.qwen3_runtime as module

    monkeypatch.setattr(module, "synchronize", lambda: None)
    runtime = Qwen3Runtime(settings())
    result = SimpleNamespace(
        text="测试", segments=[{"text": "测试", "language": "Chinese"}], generation_tokens=2
    )
    if bad == "budget":
        result.generation_tokens = 2048
    elif bad == "chunks":
        result.segments *= 2
    elif bad == "partial":
        result.text += "漏字"
    elif bad == "empty":
        result.text = result.segments[0]["text"] = ""
    else:
        result.segments[0]["language"] = "Swedish"
    runtime._asr = SimpleNamespace(generate=lambda *_args, **kwargs: result)
    with pytest.raises(ExternalToolError):
        runtime.recognize(np.zeros(16000), max_tokens=2048)


def test_silence_probe_covers_edges_and_stops_after_speech() -> None:
    runtime = Qwen3Runtime(settings())
    audio = np.zeros(40 * 16000, dtype=np.float32)
    audio[16 * 16000 - 100 : 16 * 16000 + 100] = 1
    seen = []

    def vad(probe, **kwargs):
        seen.append((len(probe), kwargs))
        indices = np.flatnonzero(probe)
        # Simulate needing both sides of a boundary to recognize short speech.
        if len(indices) == 200:
            return [{"start": int(indices[0]), "end": int(indices[-1]) + 1}]
        return []

    runtime._vad = SimpleNamespace(get_speech_timestamps=vad)
    assert runtime.speech_ranges(audio)
    assert len(seen) == 2
    assert all(item[0] <= 16 * 16000 for item in seen)
    assert seen[0][1]["threshold"] == 0.5
    assert runtime.speech_ranges(np.zeros(40 * 16000)) == []
    assert len(seen) == 5
    runtime._vad = SimpleNamespace(
        get_speech_timestamps=lambda *_args, **kwargs: [{"start": -1, "end": 4}]
    )
    with pytest.raises(ExternalToolError):
        runtime.speech_ranges(audio)


def test_complete_speech_scan_merges_overlap_and_keeps_later_speech() -> None:
    runtime = Qwen3Runtime(settings())
    seen = []
    detections = [
        [{"start": 14 * 16000, "end": 16 * 16000}],
        [{"start": 0, "end": 2 * 16000}],
        [{"start": 8 * 16000, "end": 9 * 16000}],
    ]

    def vad(probe, **kwargs):
        seen.append(len(probe))
        return detections.pop(0)

    runtime._vad = SimpleNamespace(get_speech_timestamps=vad)
    assert runtime.speech_ranges(np.zeros(40 * 16000), complete=True) == [
        (14 * 16000, 17 * 16000),
        (38 * 16000, 39 * 16000),
    ]
    assert seen == [16 * 16000, 16 * 16000, 10 * 16000]


class FakeRuntime:
    def __init__(self, text="Fable五点六。一分为二。", fail=False):
        self.text = text
        self.fail = fail
        self.initializations = 0
        self.cleared = 0
        self.active = 0
        self.max_active = 0
        self.languages = []

    def ensure_ready(self):
        self.initializations += 1

    def speech_ranges(self, audio, *, complete=False):
        return [(0, len(audio))] if np.any(audio) else []

    def recognize(self, audio, *, max_tokens, language=None):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        time.sleep(0.01)
        self.active -= 1
        if self.fail:
            self.fail = False
            raise RuntimeError("Metal OOM")
        return RuntimeRecognition(self.text, "Chinese", 12, 1)

    def align(self, audio, text, *, language):
        self.languages.append(language)
        return [AlignedToken(text, 0, 1000, 0, 1000)]

    def clear_after_failure(self):
        self.cleared += 1

    def memory_stats(self, **kwargs):
        return {}


@pytest.fixture
def fake_audio(monkeypatch):
    monkeypatch.setattr(
        "douyin_wiki.adapters.qwen3_asr.iter_recognition_windows",
        lambda *_args: iter([RecognitionWindow(0, np.ones(16000))]),
    )


@pytest.mark.asyncio
async def test_transcriber_returns_integer_segments_and_provenance(
    fake_audio, tmp_path: Path
) -> None:
    runtime = FakeRuntime()
    engine = Qwen3Transcriber(settings(), runtime=runtime)
    result = await engine.transcribe(tmp_path / "a.wav", tmp_path)
    assert [item.text for item in result] == ["Fable 5.6。一分为二。"]
    assert [(item.id, item.start_ms, item.end_ms) for item in result] == [(0, 0, 1000)]
    assert result[0].confidence is result[0].no_speech_prob is result[0].avg_logprob is None
    assert engine.provenance["provider"] == "qwen3"
    assert engine.provenance["timestamp_kind"] == "forced-alignment"
    assert engine.provenance["alignment_revision"] == engine.settings.alignment_model_revision
    assert "fallback_reason" not in engine.provenance
    assert engine.metrics["processing_seconds"] >= 0.01
    assert runtime.languages == ["Chinese"]
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_transcriber_normalizes_a_percentage_with_zero_duration_prefix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class ZeroPrefixRuntime(FakeRuntime):
        def align(self, audio, text, *, language):
            times = [
                (0, 16_000),
                (16_000, 28_800),
                (28_800, 28_800),
                (28_800, 28_800),
                (28_800, 28_800),
                (30_000, 30_800),
                (30_800, 32_000),
            ]
            return [
                AlignedToken(char, start, end, start, end)
                for char, (start, end) in zip(text, times, strict=True)
            ]

    monkeypatch.setattr(
        "douyin_wiki.adapters.qwen3_asr.iter_recognition_windows",
        lambda *_args: iter([RecognitionWindow(0, np.ones(40 * 16000, dtype=np.float32))]),
    )
    runtime = ZeroPrefixRuntime(text="涨幅百分之九十")
    engine = Qwen3Transcriber(settings(), runtime=runtime)

    result = await engine.transcribe(tmp_path / "percentage.wav", tmp_path)

    assert [(item.id, item.text, item.start_ms, item.end_ms) for item in result] == [
        (0, "涨幅", 0, 28_800),
        (1, "90%", 28_800, 32_000),
    ]
    assert engine.last_trace[0]["raw_text"] == "涨幅百分之九十"
    assert engine.last_trace[0]["display_source_spans"] == [(0, 2), (2, 7)]
    assert runtime.cleared == 0


@pytest.mark.asyncio
async def test_failure_clears_state_and_next_task_works(fake_audio, tmp_path: Path) -> None:
    runtime = FakeRuntime(fail=True)
    engine = Qwen3Transcriber(settings(), runtime=runtime)
    with pytest.raises(ExternalToolError, match="OOM"):
        await engine.transcribe(tmp_path / "a.wav", tmp_path)
    assert engine.provenance == {}
    assert runtime.cleared == 1
    assert await engine.transcribe(tmp_path / "b.wav", tmp_path)


@pytest.mark.asyncio
async def test_calls_are_serialized_even_after_cancel(fake_audio, tmp_path: Path) -> None:
    runtime = FakeRuntime()
    engine = Qwen3Transcriber(settings(), runtime=runtime)
    calls = [engine.transcribe(tmp_path / f"{i}.wav", tmp_path) for i in range(3)]
    assert all(await asyncio.gather(*calls))
    assert runtime.max_active == 1
    entered = threading.Event()
    release = threading.Event()
    recognize = runtime.recognize

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return recognize(*args, **kwargs)

    runtime.recognize = blocked
    first = asyncio.create_task(engine.transcribe(tmp_path / "cancel.wav", tmp_path))
    assert await asyncio.to_thread(entered.wait, 2)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    second = asyncio.create_task(engine.transcribe(tmp_path / "later.wav", tmp_path))
    release.set()
    assert await second
    assert runtime.max_active == 1


@pytest.mark.asyncio
async def test_explicit_qwen_has_no_legacy_fallback(fake_audio, tmp_path: Path) -> None:
    selected = SelectedTranscriber(settings())
    selected._qwen3 = Qwen3Transcriber(settings(), runtime=FakeRuntime(fail=True))
    with pytest.raises(ExternalToolError):
        await selected.transcribe(tmp_path / "a.wav", tmp_path)
    assert not hasattr(selected, "whisper") and not hasattr(selected, "sensevoice")
    assert selected.provenance == {}


def test_normalization_uses_full_source_context_and_preserves_coverage() -> None:
    source = "一分为二。Fable五点六。"
    spans = ((0, 2), (2, 5), (5, len(source)))
    assert render_normalized_slices(source, spans, itn=True) == ("一分", "为二。", "Fable 5.6。")
    assert "".join(render_normalized_slices(source, spans, itn=False)) == source
    with pytest.raises(ValueError, match="替换"):
        render_normalized_slices("五点六", ((0, 1), (1, 3)), itn=True)
    with pytest.raises(ValueError, match="覆盖"):
        render_normalized_slices(source, ((1, len(source)),), itn=False)


def test_three_models_initialize_once_and_failure_can_retry(monkeypatch) -> None:
    import douyin_wiki.adapters.qwen3_runtime as module

    calls = []
    monkeypatch.setattr(module, "resolve_snapshot", lambda repo, revision: Path(revision))
    monkeypatch.setattr(module, "inspect_snapshot", lambda path, kind: {})
    monkeypatch.setattr(
        module, "load_fixed_model", lambda kind, path, config: calls.append(kind) or object()
    )
    runtime = Qwen3Runtime(settings())
    runtime.ensure_ready()
    first = (runtime._asr, runtime._vad, runtime._aligner)
    runtime.ensure_ready()
    assert calls == ["asr", "vad", "alignment"]
    assert first == (runtime._asr, runtime._vad, runtime._aligner)
    runtime.clear_after_failure()
    monkeypatch.setattr(
        module, "load_fixed_model", lambda *_args: (_ for _ in ()).throw(ImportError("mlx-audio"))
    )
    with pytest.raises(BackendUnavailableError, match="mlx-audio"):
        runtime.ensure_ready()
    assert runtime._asr is runtime._vad is runtime._aligner is None


def test_alignment_retains_predictions_and_reports_missing_language_tokenizer(monkeypatch) -> None:
    mx = SimpleNamespace(
        array=np.asarray, eval=lambda value: None, argmax=np.argmax, clear_cache=lambda: None
    )
    monkeypatch.setitem(sys.modules, "mlx", SimpleNamespace(core=mx))
    monkeypatch.setitem(sys.modules, "mlx.core", mx)
    processor = SimpleNamespace(
        encode_timestamp=lambda text, language: (["测试"], "<|audio_pad|>"),
        fix_timestamp=lambda raw: [0, 800],
    )

    class Model:
        aligner_processor = processor
        config = SimpleNamespace(timestamp_segment_time=80, timestamp_token_id=151705)
        _tokenizer = SimpleNamespace(encode=lambda *_args, **kwargs: np.array([[151705, 151705]]))

        def _preprocess_audio(self, audio):
            return None, None, 1

        def __call__(self, ids, **kwargs):
            logits = np.zeros((1, 2, 12))
            logits[0, 0, 1] = logits[0, 1, 10] = 1
            return logits

    runtime = Qwen3Runtime(settings())
    runtime._aligner = Model()
    tokens = runtime.align(np.zeros(16000), "测试", language="Chinese")
    assert tokens == [AlignedToken("测试", 80, 800, 0, 800)]
    processor.encode_timestamp = lambda *_args: (_ for _ in ()).throw(ImportError("nagisa"))
    with pytest.raises(BackendUnavailableError, match="nagisa"):
        runtime.align(np.zeros(16000), "テスト", language="Japanese")


@pytest.mark.asyncio
async def test_concurrent_call_metadata_belongs_to_each_result(monkeypatch, tmp_path) -> None:
    def windows(path, splitter):
        value = 1 if path.name == "Chinese.wav" else 2
        return iter([RecognitionWindow(0, np.full(16000, value, dtype=np.float32))])

    class Languages(FakeRuntime):
        def recognize(self, audio, *, max_tokens, language=None):
            name = "Chinese" if audio[0] == 1 else "English"
            return RuntimeRecognition(name, name, 2, 1)

    monkeypatch.setattr("douyin_wiki.adapters.qwen3_asr.iter_recognition_windows", windows)
    engine = Qwen3Transcriber(settings(asr_language="auto"), runtime=Languages())

    async def capture(name):
        result = await engine.transcribe(tmp_path / f"{name}.wav", tmp_path)
        return result[0].text, engine.provenance["language"]

    assert await asyncio.gather(capture("Chinese"), capture("English")) == [
        ("Chinese", "Chinese"),
        ("English", "English"),
    ]
