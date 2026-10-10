from __future__ import annotations

import logging
import os
import re
import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import AnalysisMode

logger = logging.getLogger(__name__)
_QWEN_DEFAULTS = {
    "asr": ("mlx-community/Qwen3-ASR-1.7B-bf16", "e1f6c266914abc5a46e8756e02580f834a6cf8a7"),
    "vad": ("mlx-community/silero-vad", "7bc17f22d3c0451bd3a6cd71e759b009271ff49a"),
    "alignment": (
        "mlx-community/Qwen3-ForcedAligner-0.6B-8bit",
        "0e1a68e91d815300c7c9754b2a7639378b23db15",
    ),
}
_LEGACY_ASR_MODELS = {
    "asr": ("iic/SenseVoiceSmall", "70514a3da51f1160f51d18449dab6128bbd4928b"),
    "vad": (
        "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
        "662fc7a38813d81305085696d59eb5b1141a204a",
    ),
}


def default_config_path() -> Path:
    override = os.environ.get("DOUYIN_WIKI_CONFIG")
    if override:
        return Path(override).expanduser()
    return Path.home() / "Library" / "Application Support" / "douyin-wiki" / "config.toml"






class EmbeddingSettings(BaseModel):
    provider: str = "sentence-transformers"
    model: str = "BAAI/bge-small-zh-v1.5"
    fallback_dimensions: int = 384


class MediaSettings(BaseModel):
    browser: str = "chrome"
    browser_profile: str | None = None
    retention_days: int = 30
    # 保留旧配置字段以兼容现有 TOML；采集流程不再按时长暂停。
    cloud_confirmation_minutes: int = 30
    max_duration_minutes: int = 120
    frame_interval_seconds: int = 10
    scene_threshold: float = 0.35
    max_frames: int = 60
    asr_provider: Literal["qwen3"] = "qwen3"
    ocr_provider: Literal["auto", "rapidocr", "vision"] = "auto"
    asr_model: str = _QWEN_DEFAULTS["asr"][0]
    vad_model: str = _QWEN_DEFAULTS["vad"][0]
    asr_model_revision: str = _QWEN_DEFAULTS["asr"][1]
    vad_model_revision: str = _QWEN_DEFAULTS["vad"][1]
    alignment_model: str = _QWEN_DEFAULTS["alignment"][0]
    alignment_model_revision: str = _QWEN_DEFAULTS["alignment"][1]
    asr_language: str = "Chinese"
    asr_itn: bool = True
    asr_segment_seconds: int = Field(default=30, ge=5, le=60)

    @model_validator(mode="before")
    @classmethod
    def migrate_asr(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        provider = data.get("asr_provider", "auto")
        if not isinstance(provider, str) or provider not in {
            "auto",
            "sensevoice",
            "whisper",
            "qwen3",
        }:
            raise ValueError("未知 asr_provider；当前仅支持 qwen3")
        legacy = provider != "qwen3"
        changed = "asr_provider" in data and legacy
        data["asr_provider"] = "qwen3"
        for role, (default_model, default_revision) in _QWEN_DEFAULTS.items():
            model_key, revision_key = f"{role}_model", f"{role}_model_revision"
            explicit_model = model_key in data
            model = data.get(model_key, default_model)
            revision = data.get(revision_key)
            old = _LEGACY_ASR_MODELS.get(role)
            if old and (model == old[0] or (not explicit_model and revision == old[1])):
                if revision is not None and revision != old[1]:
                    raise ValueError(f"旧 {role}_model 自定义 revision 不能自动迁移")
                data[model_key], data[revision_key] = default_model, default_revision
                changed = True
                continue
            if legacy and explicit_model and role in {"asr", "vad"}:
                expected = "qwen3-asr" if role == "asr" else "silero-vad"
                if not isinstance(model, str) or expected not in model.lower():
                    raise ValueError(f"未知旧 {role}_model；请显式配置 Qwen3 模型与 revision")
            if explicit_model and revision is None:
                raise ValueError(f"显式 {model_key} 必须提供 {revision_key}")
            if revision is not None:
                if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
                    raise ValueError(f"{revision_key} 必须为完整固定 SHA")
                if revision in {pair[1] for pair in _LEGACY_ASR_MODELS.values()}:
                    raise ValueError(f"{model_key} 不能沿用旧模型 revision")
        retired = [
            key
            for key in data
            if isinstance(key, str) and (key == "asr_device" or key.startswith("whisper_"))
        ]
        for key in retired:
            data.pop(key)
        if changed or retired:
            logger.warning("旧 ASR 配置已在内存中迁移为 Qwen3；原配置文件未改写")
        return data

    @field_validator("asr_language")
    @classmethod
    def validate_asr_language(cls, value: str) -> str:
        languages = {
            language.lower(): language
            for language in (
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
                "auto",
            )
        }
        if value.lower() not in languages:
            raise ValueError("ASR 语言不在 Qwen3 识别与对齐共同支持的列表中")
        return languages[value.lower()]


class WorkerSettings(BaseModel):
    poll_seconds: float = 2
    download_concurrency: int = 2
    media_concurrency: int = 1
    analysis_concurrency: int = 1
    lease_seconds: int = 180
    heartbeat_seconds: int = 30


class AuthGuidanceSettings(BaseModel):
    enabled: bool = True
    timeout_seconds: int = Field(default=600, ge=30, le=1800)
    poll_seconds: float = Field(default=5, ge=2, le=30)


class WebSettings(BaseModel):
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1024, le=65535)

    @model_validator(mode="after")
    def local_only(self) -> WebSettings:
        if self.host != "127.0.0.1":
            raise ValueError("Web 服务只允许绑定 127.0.0.1")
        return self


class AppConfig(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    vault_path: Path = Path.home() / "Documents" / "Obsidian" / "抖库"
    timezone: str = "Asia/Shanghai"
    analysis_mode: AnalysisMode = AnalysisMode.GATEWAY
    embeddings: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    media: MediaSettings = Field(default_factory=MediaSettings)
    worker: WorkerSettings = Field(default_factory=WorkerSettings)
    auth_guidance: AuthGuidanceSettings = Field(default_factory=AuthGuidanceSettings)
    web: WebSettings = Field(default_factory=WebSettings)

    @field_validator("analysis_mode", mode="before")
    @classmethod
    def gateway_only(cls, value: Any) -> AnalysisMode:
        if value != AnalysisMode.GATEWAY:
            raise ValueError("精简版只支持 gateway 分析模式")
        return AnalysisMode.GATEWAY

    @property
    def state_dir(self) -> Path:
        return self.vault_path / ".douyin-wiki"

    @property
    def database_path(self) -> Path:
        return self.state_dir / "state.sqlite3"

    @property
    def work_dir(self) -> Path:
        return self.state_dir / "work"

    @property
    def browser_profile_dir(self) -> Path:
        """Persistent profile used only by 抖库 browser automation."""
        override = os.environ.get("DOUYIN_WIKI_BROWSER_PROFILE")
        if override:
            return Path(override).expanduser()
        return Path.home() / "Library" / "Application Support" / "douyin-wiki" / "browser-profile"






def _merge_dict(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(path: Path | None = None) -> AppConfig:
    config_path = path or default_config_path()
    if not config_path.exists():
        return AppConfig()
    with config_path.open("rb") as handle:
        data = tomllib.load(handle)
    media = data.setdefault("media", {})
    if isinstance(media, dict):
        # ASR migrates in MediaSettings; legacy missing OCR keeps Vision.
        media.setdefault("ocr_provider", "vision")
    return AppConfig.model_validate(data)


def _toml_string(value: object) -> str:
    """Encode a value as a TOML basic string using JSON-compatible escaping."""
    import json

    return json.dumps(str(value), ensure_ascii=False)


def render_default_config(config: AppConfig | None = None) -> str:
    cfg = config or AppConfig()
    profile = (
        f"\nbrowser_profile = {_toml_string(cfg.media.browser_profile)}"
        if cfg.media.browser_profile
        else ""
    )
    return f"""vault_path = {_toml_string(cfg.vault_path)}
timezone = {_toml_string(cfg.timezone)}
analysis_mode = {_toml_string(cfg.analysis_mode.value)}

[embeddings]
provider = {_toml_string(cfg.embeddings.provider)}
model = {_toml_string(cfg.embeddings.model)}
fallback_dimensions = {cfg.embeddings.fallback_dimensions}

[media]
browser = {_toml_string(cfg.media.browser)}{profile}
retention_days = {cfg.media.retention_days}
# 兼容旧配置字段，不再用于限制视频时长或要求再次确认。
cloud_confirmation_minutes = {cfg.media.cloud_confirmation_minutes}
max_duration_minutes = {cfg.media.max_duration_minutes}
frame_interval_seconds = {cfg.media.frame_interval_seconds}
scene_threshold = {cfg.media.scene_threshold}
max_frames = {cfg.media.max_frames}
asr_provider = {_toml_string(cfg.media.asr_provider)}
ocr_provider = {_toml_string(cfg.media.ocr_provider)}
asr_model = {_toml_string(cfg.media.asr_model)}
vad_model = {_toml_string(cfg.media.vad_model)}
asr_model_revision = {_toml_string(cfg.media.asr_model_revision)}
vad_model_revision = {_toml_string(cfg.media.vad_model_revision)}
alignment_model = {_toml_string(cfg.media.alignment_model)}
alignment_model_revision = {_toml_string(cfg.media.alignment_model_revision)}
asr_language = {_toml_string(cfg.media.asr_language)}
asr_itn = {str(cfg.media.asr_itn).lower()}
asr_segment_seconds = {cfg.media.asr_segment_seconds}

[worker]
poll_seconds = {cfg.worker.poll_seconds}
download_concurrency = {cfg.worker.download_concurrency}
media_concurrency = {cfg.worker.media_concurrency}
analysis_concurrency = {cfg.worker.analysis_concurrency}
lease_seconds = {cfg.worker.lease_seconds}
heartbeat_seconds = {cfg.worker.heartbeat_seconds}

[auth_guidance]
enabled = {str(cfg.auth_guidance.enabled).lower()}
timeout_seconds = {cfg.auth_guidance.timeout_seconds}
poll_seconds = {cfg.auth_guidance.poll_seconds}

[web]
enabled = {str(cfg.web.enabled).lower()}
host = {_toml_string(cfg.web.host)}
port = {cfg.web.port}
"""
