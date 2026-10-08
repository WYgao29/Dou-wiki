"""Pinned local data and fixed library classes; no repository hooks or downloads."""

from __future__ import annotations

import importlib
import json
import logging
import platform
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from ..config import MediaSettings
from ..errors import ExternalToolError
from .asr_segments import SAMPLE_RATE, AlignedToken, QwenEnergySplitter
from .asr_text import clean_qwen_text
from .media_models import BackendUnavailableError

logger = logging.getLogger(__name__)
_FORBIDDEN = {".py", ".pyc", ".pyo", ".pkl", ".pickle", ".pth", ".bin", ".pt"}
ALIGNMENT_LANGUAGES = (
    "Chinese",
    "Cantonese",
    "English",
    "German",
    "Spanish",
    "French",
    "Italian",
    "Portuguese",
    "Russian",
    "Korean",
    "Japanese",
)


@dataclass(frozen=True)
class RuntimeRecognition:
    text: str
    language: str
    generation_tokens: int
    internal_segments: int


def validate_language(value: str, *, allow_auto: bool = False) -> str:
    allowed = {item.lower(): item for item in ALIGNMENT_LANGUAGES}
    if allow_auto:
        allowed["auto"] = "auto"
    if not isinstance(value, str) or value.lower() not in allowed:
        raise ValueError(f"Qwen3 识别与对齐不共同支持语言：{value}")
    return allowed[value.lower()]


def resolve_snapshot(repo_id: str, revision: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise BackendUnavailableError("Qwen3 模型 revision 必须是完整固定 SHA")
    try:
        from huggingface_hub import snapshot_download

        path = Path(
            snapshot_download(
                repo_id=repo_id,
                revision=revision,
                local_files_only=True,
                allow_patterns=["*.safetensors", "*.json", "*.txt"],
            )
        )
        if path.name != revision or not path.is_dir():
            raise BackendUnavailableError("本地快照 revision 不匹配")
        return path
    except BackendUnavailableError:
        raise
    except Exception as exc:
        raise BackendUnavailableError(
            f"缺少本地模型 {repo_id}@{revision}；请按 README 显式准备模型"
        ) from exc


def _has_hook(value: object) -> bool:
    if isinstance(value, dict):
        return "auto_map" in value or any(_has_hook(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_hook(item) for item in value)
    return False


def inspect_snapshot(path: Path, kind: str) -> dict:
    try:
        for item in path.rglob("*"):
            if not item.is_file():
                continue
            if item.suffix.lower() in _FORBIDDEN:
                raise BackendUnavailableError(f"模型快照含不安全文件：{item.name}")
            if item.suffix.lower() == ".json" and _has_hook(json.loads(item.read_text())):
                raise BackendUnavailableError(f"模型快照禁止 auto_map：{item.name}")
        config = json.loads((path / "config.json").read_text())
        if not (path / "model.safetensors").is_file():
            raise BackendUnavailableError("模型快照缺少 model.safetensors")
        if kind == "vad":
            valid = config.get("model_type") == config.get("architecture") == "silero_vad"
        elif kind == "alignment":
            valid = (
                config.get("model_type") == "qwen3_asr"
                and config.get("timestamp_segment_time") == 80
                and config.get("timestamp_token_id") == 151705
            )
        else:
            valid = (
                kind == "asr"
                and config.get("model_type") == "qwen3_asr"
                and "timestamp_token_id" not in config
            )
        if not valid:
            raise BackendUnavailableError(f"模型架构不符合固定 {kind} 架构")
        return config
    except BackendUnavailableError:
        raise
    except Exception as exc:
        raise BackendUnavailableError(f"模型快照损坏或不完整：{path.name}") from exc


def _model_bindings(kind: str) -> SimpleNamespace:
    import mlx.core as mx
    from mlx.utils import tree_flatten
    from mlx_audio.utils import apply_quantization, load_weights

    if kind == "vad":
        from mlx_audio.vad.models.silero_vad import Model, ModelConfig

        tokenizer = extractor = None
    else:
        from transformers import AutoTokenizer, WhisperFeatureExtractor

        tokenizer, extractor = AutoTokenizer, WhisperFeatureExtractor
        if kind == "asr":
            from mlx_audio.stt.models.qwen3_asr import Model, ModelConfig
        else:
            from mlx_audio.stt.models.qwen3_asr.qwen3_forced_aligner import (
                ForcedAlignerConfig as ModelConfig,
            )
            from mlx_audio.stt.models.qwen3_asr.qwen3_forced_aligner import (
                ForcedAlignerModel as Model,
            )
    return SimpleNamespace(
        Model=Model,
        Config=ModelConfig,
        mx=mx,
        flatten=tree_flatten,
        quantize=apply_quantization,
        load_weights=load_weights,
        Tokenizer=tokenizer,
        FeatureExtractor=extractor,
    )


def load_fixed_model(kind: str, path: Path, config: dict) -> object:
    binding = _model_bindings(kind)
    model = binding.Model(binding.Config.from_dict(config))
    weights = model.sanitize(binding.load_weights(path))
    binding.quantize(model, config, weights, getattr(model, "model_quant_predicate", None))
    parameters = {key for key, _value in binding.flatten(model.parameters())}
    if parameters != set(weights):
        raise BackendUnavailableError(
            f"{kind} 权重键不匹配；缺少 {len(parameters - set(weights))}，"
            f"多出 {len(set(weights) - parameters)}"
        )
    model.load_weights(list(weights.items()), strict=True)
    binding.mx.eval(model.parameters())
    model.eval()
    binding.mx.synchronize()
    if kind != "vad":
        target = model._model if kind == "asr" else model
        target._tokenizer = binding.Tokenizer.from_pretrained(
            str(path),
            local_files_only=True,
            trust_remote_code=False,
        )
        target._feature_extractor = binding.FeatureExtractor.from_pretrained(
            str(path),
            local_files_only=True,
        )
    return model


def synchronize() -> None:
    import mlx.core as mx

    mx.synchronize()


class Qwen3Runtime:
    """Owned by one transcriber's task lock, including readiness and recovery."""

    def __init__(self, settings: MediaSettings) -> None:
        self.settings = settings
        self._asr = self._vad = self._aligner = None
        self.alignment_trace: list[dict] = []
        self.pending_import_seconds = 0.0

    def ensure_ready(self) -> None:
        if self._asr is not None and self._vad is not None and self._aligner is not None:
            return
        if platform.system() != "Darwin" or platform.machine().lower() not in {"arm64", "aarch64"}:
            raise BackendUnavailableError("Qwen3 MLX 要求 Apple Silicon Mac")
        try:
            models = []
            for kind, repo, revision in (
                ("asr", self.settings.asr_model, self.settings.asr_model_revision),
                ("vad", self.settings.vad_model, self.settings.vad_model_revision),
                (
                    "alignment",
                    self.settings.alignment_model,
                    self.settings.alignment_model_revision,
                ),
            ):
                path = resolve_snapshot(repo, revision)
                models.append(load_fixed_model(kind, path, inspect_snapshot(path, kind)))
            self._asr, self._vad, self._aligner = models
        except Exception as exc:
            self.clear_after_failure()
            if isinstance(exc, BackendUnavailableError):
                raise
            raise BackendUnavailableError(
                f"Qwen3 依赖或模型无法初始化：{exc}；安装 uv sync --extra qwen3 并准备模型"
            ) from exc

    def clear_after_failure(self) -> None:
        self._asr = self._vad = self._aligner = None
        self.alignment_trace = []
        mx = sys.modules.get("mlx.core")
        if mx is not None:
            try:
                mx.synchronize()
                mx.clear_cache()
            except Exception:
                logger.debug("Qwen3 cache cleanup failed", exc_info=True)

    def memory_stats(self, *, reset_peak: bool = False) -> dict[str, int]:
        mx = sys.modules.get("mlx.core")
        if mx is None:
            return {}
        if reset_peak and hasattr(mx, "reset_peak_memory"):
            mx.reset_peak_memory()
        stats = {}
        for label, name in (
            ("mlx_active_bytes", "get_active_memory"),
            ("mlx_cache_bytes", "get_cache_memory"),
            ("mlx_peak_bytes", "get_peak_memory"),
        ):
            function = getattr(mx, name, None)
            if function is not None:
                stats[label] = int(function())
        return stats

    def split_audio(self, audio: np.ndarray, *, window_seconds: float):
        # Long input needs this splitter before yielding the first window.
        # Charge its first model-library import to initialization, not inference.
        module = "mlx_audio.stt.models.qwen3_asr.qwen3_asr"
        if module not in sys.modules:
            started = time.perf_counter()
            importlib.import_module(module)
            self.pending_import_seconds += time.perf_counter() - started
        return QwenEnergySplitter().split_audio(audio, window_seconds=window_seconds)

    def speech_ranges(self, audio: np.ndarray) -> list[tuple[int, int]]:
        """Return first detected speech; scan silence fully with overlapping 16s probes."""
        step, size = 15 * SAMPLE_RATE, 16 * SAMPLE_RATE
        for start in range(0, len(audio), step):
            probe = audio[start : start + size]
            ranges = self._vad.get_speech_timestamps(
                probe,
                sample_rate=SAMPLE_RATE,
                threshold=0.5,
                min_speech_duration_ms=250,
                min_silence_duration_ms=300,
                speech_pad_ms=200,
                return_seconds=False,
            )
            if not isinstance(ranges, list):
                raise ExternalToolError("Silero 返回无效语音区间")
            for item in ranges:
                if not isinstance(item, dict):
                    raise ExternalToolError("Silero 返回无效语音区间")
                begin, end = item.get("start"), item.get("end")
                if (
                    isinstance(begin, (bool, np.bool_))
                    or isinstance(end, (bool, np.bool_))
                    or not isinstance(begin, (int, np.integer))
                    or not isinstance(end, (int, np.integer))
                    or not 0 <= begin < end <= len(probe)
                ):
                    raise ExternalToolError("Silero 返回越界语音区间")
            if ranges:
                return [(start + int(item["start"]), start + int(item["end"])) for item in ranges]
            if start + size >= len(audio):
                break
        return []

    def recognize(
        self,
        audio: np.ndarray,
        *,
        max_tokens: int,
        language: str | None = None,
    ) -> RuntimeRecognition:
        requested = self.settings.asr_language if language is None else language
        selected = None if requested == "auto" else validate_language(requested)
        result = self._asr.generate(
            audio,
            max_tokens=max_tokens,
            batch_size=1,
            temperature=0.0,
            language=selected,
            system_prompt=None,
            hotwords=None,
            chunk_duration=600,
            min_chunk_duration=0,
            verbose=False,
        )
        synchronize()
        parts = result.segments
        count = result.generation_tokens
        if not isinstance(count, (int, np.integer)) or isinstance(count, (bool, np.bool_)):
            raise ExternalToolError("Qwen3 返回无效 token 数")
        if count >= max_tokens or count < 0:
            raise ExternalToolError(f"Qwen3 token 预算耗尽或无效：{max_tokens}")
        if not isinstance(parts, list) or len(parts) != 1 or not isinstance(parts[0], dict):
            raise ExternalToolError("Qwen3 必须完整返回一个内部识别块")
        raw = parts[0].get("text")
        if not isinstance(raw, str) or raw != result.text:
            raise ExternalToolError("Qwen3 返回不完整识别文字")
        cleaned = clean_qwen_text(raw)
        if not cleaned:
            raise ExternalToolError("Qwen3 有语音窗口未返回文字")
        try:
            detected = validate_language(parts[0].get("language"))
        except ValueError as exc:
            raise ExternalToolError(str(exc)) from exc
        return RuntimeRecognition(cleaned, detected, int(count), 1)

    def align(self, audio: np.ndarray, text: str, *, language: str) -> list[AlignedToken]:
        import mlx.core as mx

        model = self._aligner
        selected = validate_language(language)
        processor = model.aligner_processor
        try:
            words, prompt = processor.encode_timestamp(text, selected)
        except ImportError as exc:
            raise BackendUnavailableError(f"{selected} 对齐缺少分词依赖：{exc}") from exc
        if not words:
            raise ExternalToolError("Qwen3 对齐器没有返回词")
        features, attention, audio_tokens = model._preprocess_audio(audio)
        prompt = prompt.replace("<|audio_pad|>", "<|audio_pad|>" * audio_tokens)
        ids = mx.array(
            model._tokenizer.encode(prompt, return_tensors="np", add_special_tokens=False)
        )
        logits = model(ids, input_features=features, feature_attention_mask=attention)
        mx.eval(logits)
        output = np.asarray(mx.argmax(logits, axis=-1)).reshape(-1)
        input_ids = np.asarray(ids).reshape(-1)
        classes = output[input_ids == model.config.timestamp_token_id]
        if len(classes) != 2 * len(words) or model.config.timestamp_segment_time != 80:
            raise ExternalToolError("Qwen3 对齐时间数量或 80ms 栅格不正确")
        raw = np.asarray(classes, dtype=np.float64) * 80
        native = processor.fix_timestamp(raw)
        tokens = [
            AlignedToken(
                word, int(raw[2 * i]), int(raw[2 * i + 1]), native[2 * i], native[2 * i + 1]
            )
            for i, word in enumerate(words)
        ]
        del logits, output, ids, features, attention
        mx.clear_cache()
        self.alignment_trace.append(
            {"language": selected, "samples": len(audio), "text": text, "tokens": tokens}
        )
        return tokens
