from pathlib import Path

import pytest

from douyin_wiki.config import AppConfig, MediaSettings, load_config, render_default_config
from douyin_wiki.setup import doctor

ASR = ("mlx-community/Qwen3-ASR-1.7B-bf16", "e1f6c266914abc5a46e8756e02580f834a6cf8a7")
VAD = ("mlx-community/silero-vad", "7bc17f22d3c0451bd3a6cd71e759b009271ff49a")
OLD_ASR = ("iic/SenseVoiceSmall", "70514a3da51f1160f51d18449dab6128bbd4928b")
OLD_VAD = (
    "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
    "662fc7a38813d81305085696d59eb5b1141a204a",
)


@pytest.mark.parametrize("provider", [None, "auto", "sensevoice", "whisper", "qwen3"])
@pytest.mark.parametrize("explicit_pairs", [False, True])
def test_legacy_asr_config_migrates_model_and_revision_together(provider, explicit_pairs):
    data = {} if provider is None else {"asr_provider": provider}
    if explicit_pairs:
        data.update(
            asr_model=OLD_ASR[0],
            asr_model_revision=OLD_ASR[1],
            vad_model=OLD_VAD[0],
            vad_model_revision=OLD_VAD[1],
        )
    settings = MediaSettings.model_validate(data)
    assert settings.asr_provider == "qwen3"
    assert (settings.asr_model, settings.asr_model_revision) == ASR
    assert (settings.vad_model, settings.vad_model_revision) == VAD


@pytest.mark.parametrize(
    "field,model",
    [
        ("asr", "mlx-community/Qwen3-ASR-1.7B-8bit"),
        ("asr", ASR[0]),
        ("vad", "mlx-community/silero-vad"),
        ("alignment", "mlx-community/Qwen3-ForcedAligner-0.6B-8bit"),
    ],
)
def test_qwen_model_requires_matching_revision(field, model):
    with pytest.raises(ValueError, match="revision"):
        MediaSettings.model_validate({"asr_provider": "qwen3", f"{field}_model": model})
    settings = MediaSettings.model_validate(
        {
            "asr_provider": "qwen3",
            f"{field}_model": model,
            f"{field}_model_revision": "a" * 40,
        }
    )
    assert getattr(settings, f"{field}_model_revision") == "a" * 40


@pytest.mark.parametrize("provider", ["qwen3", "auto", "sensevoice", "whisper"])
def test_explicit_qwen_pair_is_preserved_under_legacy_provider(provider):
    settings = MediaSettings(
        asr_provider=provider,
        asr_model=ASR[0],
        asr_model_revision=ASR[1],
        vad_model=VAD[0],
        vad_model_revision=VAD[1],
    )
    assert settings.asr_provider == "qwen3"
    assert (settings.asr_model, settings.asr_model_revision) == ASR
    assert (settings.vad_model, settings.vad_model_revision) == VAD


@pytest.mark.parametrize(
    "data",
    [
        {"asr_provider": "unknown"},
        {"asr_provider": "auto", "asr_model": "custom/legacy", "asr_model_revision": "a" * 40},
        {"asr_provider": "whisper", "vad_model": "custom/legacy", "vad_model_revision": "a" * 40},
        {"asr_model": OLD_ASR[0], "asr_model_revision": "a" * 40},
        {"asr_provider": "qwen3", "asr_model": ASR[0], "asr_model_revision": OLD_ASR[1]},
        {"asr_provider": "qwen3", "asr_model_revision": "main"},
        {"asr_provider": ["auto"]},
        {"asr_model": OLD_ASR[0], "asr_model_revision": [OLD_ASR[1]]},
    ],
)
def test_unknown_or_mismatched_asr_configuration_is_not_silently_discarded(data):
    with pytest.raises(ValueError):
        MediaSettings.model_validate(data)


def test_asr_config_migration_does_not_write_or_change_ocr(tmp_path: Path, caplog):
    path = tmp_path / "legacy.toml"
    original = (
        b'[media]\nasr_provider = "auto"\nasr_device = "cpu"\n'
        b'whisper_model = "old-model"\nmax_frames = 23\n'
    )
    path.write_bytes(original)
    cfg = load_config(path)
    assert path.read_bytes() == original
    assert cfg.media.asr_provider == "qwen3"
    assert cfg.media.ocr_provider == "vision" and cfg.media.max_frames == 23
    assert "迁移" in caplog.text
    rendered = render_default_config(cfg)
    assert "whisper_" not in rendered and "asr_device" not in rendered
    fresh = AppConfig()
    assert fresh.media.ocr_provider == "auto"


def test_doctor_never_probes_retired_asr_engines(tmp_path, monkeypatch):
    def find(name):
        assert name not in {"funasr", "mlx_whisper"}
        return object()

    def which(command):
        assert command != "whisper"
        return "/bin/true"

    monkeypatch.setattr("importlib.util.find_spec", find)
    monkeypatch.setattr("shutil.which", which)
    monkeypatch.setattr(
        "douyin_wiki.setup.qwen3_health", lambda _: {"ok": False, "message": "missing cache"}
    )
    result = doctor(AppConfig(vault_path=tmp_path))
    assert result["transcription"] == {"ok": False, "provider": "qwen3", "message": "missing cache"}
    for name in ("whisper", "mlx_whisper", "funasr"):
        assert result[name]["retired"] is True and result[name]["ok"] is False
