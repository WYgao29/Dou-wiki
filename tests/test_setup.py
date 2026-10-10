from __future__ import annotations

import json
import subprocess
import sysconfig
from pathlib import Path

import pytest
from typer.testing import CliRunner

import douyin_wiki.cli as cli_module
from douyin_wiki.auth_guidance import SubprocessAuthGuidanceLauncher
from douyin_wiki.cli import app
from douyin_wiki.config import AppConfig, MediaSettings, load_config, render_default_config
from douyin_wiki.models import AnalysisMode
from douyin_wiki.setup import (
    VaultSetupMode,
    doctor,
    obsidian_vault_status,
    qwen3_health,
    update_config_values,
    validate_vault_target,
    write_config,
)
from douyin_wiki.vault import VaultWriter

runner = CliRunner()


def test_default_config_contains_no_llm_settings(tmp_path: Path) -> None:
    config = AppConfig(vault_path=tmp_path / "vault")
    rendered = render_default_config(config)
    assert "[llm]" not in rendered
    assert not hasattr(config, "llm")
    path = tmp_path / "config.toml"
    path.write_text(rendered)
    assert load_config(path).analysis_mode == AnalysisMode.GATEWAY


@pytest.mark.parametrize("mode", ["provider", "local", "unknown"])
def test_disabled_and_unknown_analysis_modes_are_rejected(tmp_path: Path, mode: str) -> None:
    with pytest.raises(ValueError, match="只支持 gateway"):
        AppConfig(analysis_mode=mode)
    path = tmp_path / "config.toml"
    path.write_text(f'analysis_mode = "{mode}"\n')
    with pytest.raises(ValueError, match="只支持 gateway"):
        load_config(path)


@pytest.mark.asyncio
async def test_no_llm_runtime_does_not_access_keys_or_model_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx

    from douyin_wiki.webapp.app import create_app
    from douyin_wiki.worker import Worker
    from tests.support.gateway import make_video_gateway_service
    from tests.support.web import local_client

    run = subprocess.run

    def guarded_run(command, *args, **kwargs):
        assert command[0] != "security", "运行检查不应访问 Keychain"
        return run(command, *args, **kwargs)

    async def forbidden_http(*args, **kwargs):
        pytest.fail("初始化和空队列不应调用对话模型 HTTP")

    monkeypatch.delenv("DOUYIN_WIKI_LLM_API_KEY", raising=False)
    monkeypatch.setattr(subprocess, "run", guarded_run)
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden_http)
    service = make_video_gateway_service(tmp_path)
    checks = doctor(service.config)
    assert "llm" not in checks
    assert "api_key" not in checks
    application = create_app(service.config, service=service, start_watcher=False)
    with local_client(application) as client:
        assert client.get("/api/overview").status_code == 200
    assert await Worker(service).run_once() is None
    assert not hasattr(service, "analysis")


@pytest.mark.parametrize("arguments", [[], ["--model", "old-model", "--base-url", "http://localhost:8000/v1"]])
def test_configure_model_is_disabled_without_prompt(tmp_path: Path, monkeypatch, arguments) -> None:
    def forbidden(*args, **kwargs):
        pytest.fail("停用命令不应读取配置、写入或交互")

    for name in ("load_config", "write_config", "update_config_values"):
        monkeypatch.setattr(cli_module, name, forbidden)
    monkeypatch.setattr(cli_module.typer, "prompt", forbidden)
    path = tmp_path / "absent.toml"
    result = runner.invoke(app, ["configure-model", *arguments, "--config-path", str(path)])
    assert result.exit_code == 2, result.output
    assert "已停用" in result.output
    assert not path.exists()


def test_qwen_model_commands_use_the_current_environment_outside_the_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripts = tmp_path / "environment with spaces" / "bin"
    scripts.mkdir(parents=True)
    hf = scripts / "hf"
    hf.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\"\n", encoding="utf-8")
    hf.chmod(0o755)
    get_path = sysconfig.get_path
    monkeypatch.setattr(
        sysconfig,
        "get_path",
        lambda name, *args, **kwargs: str(scripts)
        if name == "scripts"
        else get_path(name, *args, **kwargs),
    )
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "empty-model-cache"))
    monkeypatch.setattr("importlib.util.find_spec", lambda _name: object())
    other_directory = tmp_path / "outside-project"
    other_directory.mkdir()

    health = qwen3_health(MediaSettings())

    for kind, model, revision in (
        ("asr", "mlx-community/Qwen3-ASR-1.7B-bf16", "e1f6c266914abc5a46e8756e02580f834a6cf8a7"),
        ("vad", "mlx-community/silero-vad", "7bc17f22d3c0451bd3a6cd71e759b009271ff49a"),
        (
            "alignment",
            "mlx-community/Qwen3-ForcedAligner-0.6B-8bit",
            "0e1a68e91d815300c7c9754b2a7639378b23db15",
        ),
    ):
        result = subprocess.run(
            health["models"][kind]["prepare_command"],
            shell=True,
            executable="/bin/sh",
            cwd=other_directory,
            env={"PATH": ""},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == [
            "download",
            model,
            "--revision",
            revision,
            "--include",
            "*.safetensors",
            "*.json",
            "*.txt",
        ]


def test_media_engine_config_preserves_legacy_and_renders_new_defaults(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy.toml"
    legacy.write_text('[media]\nwhisper_provider = "mlx"\n', encoding="utf-8")
    old = load_config(legacy)
    assert old.media.asr_provider == "qwen3"
    assert old.media.ocr_provider == "vision"
    assert not hasattr(old.media, "whisper_provider")

    rendered = render_default_config(AppConfig())
    new = tmp_path / "new.toml"
    new.write_text(rendered, encoding="utf-8")
    current = load_config(new)
    assert current.media.asr_provider == "qwen3"
    assert current.media.ocr_provider == "auto"
    assert current.media.asr_model_revision == "e1f6c266914abc5a46e8756e02580f834a6cf8a7"
    assert current.media.vad_model_revision == "7bc17f22d3c0451bd3a6cd71e759b009271ff49a"

    partial = tmp_path / "partial.toml"
    partial.write_text('[media]\nasr_provider = "sensevoice"\n', encoding="utf-8")
    assert load_config(partial).media.ocr_provider == "vision"


def test_gateway_is_default_analysis_mode(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(render_default_config(AppConfig()), encoding="utf-8")
    assert load_config(config_path).analysis_mode == AnalysisMode.GATEWAY
    assert 'analysis_mode = "gateway"' in config_path.read_text(encoding="utf-8")
    assert load_config(tmp_path / "missing.toml").vault_path.name == "抖库"


def test_auth_guidance_defaults_round_trip_through_toml(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(render_default_config(AppConfig()), encoding="utf-8")

    loaded = load_config(config_path)

    assert loaded.auth_guidance.enabled is True
    assert loaded.auth_guidance.timeout_seconds == 600
    assert loaded.auth_guidance.poll_seconds == 5
    assert "[auth_guidance]" in config_path.read_text(encoding="utf-8")


def test_cli_service_injects_subprocess_auth_guidance_launcher(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(render_default_config(AppConfig()), encoding="utf-8")
    captured: dict = {}

    class FakeService:
        def __init__(self, config, **kwargs) -> None:
            captured["config"] = config
            captured.update(kwargs)

        def initialize_runtime(self) -> None:
            captured["initialized"] = True

    monkeypatch.setattr(cli_module, "DouyinWikiService", FakeService)

    cli_module._service(config_path)

    launcher = captured["auth_guidance_launcher"]
    assert isinstance(launcher, SubprocessAuthGuidanceLauncher)
    assert launcher.config_path == config_path
    assert captured["initialized"] is True


def test_config_round_trips_quoted_strings_and_is_written_atomically(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    vault = tmp_path / '包含"引号的 Vault'
    config = AppConfig(
        vault_path=vault,
        media={"browser_profile": 'Profile "Work"'},
    )

    write_config(config, config_path, overwrite=True)
    loaded = load_config(config_path)

    assert loaded.vault_path == vault
    assert loaded.media.browser_profile == 'Profile "Work"'
    assert not list(tmp_path.glob(".config.toml.*.tmp"))










def test_config_patch_preserves_comments_and_unknown_keys(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '# 用户注释\nanalysis_mode = "gateway"\ncustom_root = "keep"\n\n'
        '[media]\nbrowser = "chrome" # 浏览器注释\ncustom_media = 42\n',
        encoding="utf-8",
    )

    update_config_values(
        config_path,
        {None: {"analysis_mode": "gateway"}, "media": {"browser": "edge"}},
    )

    content = config_path.read_text(encoding="utf-8")
    assert "# 用户注释" in content
    assert 'custom_root = "keep"' in content
    assert "custom_media = 42" in content
    assert 'browser = "edge" # 浏览器注释' in content


def test_config_patch_ignores_section_like_text_inside_multiline_values(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        'analysis_mode = "gateway"\n\n[media]\n'
        'banner = """\n[not-a-section]\n保留这段文字\n"""\n'
        'aliases = [\n  "[array-value]",\n]\n'
        'browser = """\nchrome\n[also-not-a-section]\n"""\n'
        'browser_profile = "Profile Chrome"\n',
        encoding="utf-8",
    )

    update_config_values(
        config_path,
        {"media": {"browser": "edge", "browser_profile": "Profile Edge"}},
    )

    content = config_path.read_text(encoding="utf-8")
    assert content.count("browser = ") == 1
    assert "[not-a-section]\n保留这段文字" in content
    assert 'aliases = [\n  "[array-value]",\n]' in content
    assert load_config(config_path).media.browser == "edge"






def test_configure_model_does_not_expose_api_key_option() -> None:
    result = runner.invoke(app, ["configure-model", "--help"])

    assert result.exit_code == 0, result.output
    assert "--api-key" not in result.output


def test_first_init_guides_new_vault_and_is_idempotent(tmp_path: Path) -> None:
    parent = tmp_path / "Obsidian"
    config_path = tmp_path / "config.toml"
    result = runner.invoke(
        app,
        ["init", "--config-path", str(config_path)],
        input=f"1\n{parent}\nMy-Douyin-Wiki\n",
    )
    assert result.exit_code == 0, result.output

    vault = parent / "My-Douyin-Wiki"
    assert (vault / ".obsidian").is_dir()
    assert (vault / ".douyin-wiki" / "state.sqlite3").is_file()
    assert (vault / "raw" / "assets").is_dir()
    assert (vault / "raw" / "covers").is_dir()
    assert (vault / "wiki" / "sources").is_dir()
    assert (vault / "wiki" / "concepts").is_dir()
    assert (vault / "wiki" / "entities").is_dir()
    assert (vault / "wiki" / "syntheses").is_dir()
    assert (vault / "index.md").is_file()
    assert (vault / "log.md").is_file()
    assert (vault / "AGENTS.md").is_file()
    assert (vault / ".git").is_dir()
    gitignore = (vault / ".gitignore").read_text(encoding="utf-8")
    assert "raw/covers/" in gitignore
    assert "raw/assets/**/cover.*" in gitignore
    assert "raw/assets/**/original.info.json" in gitignore
    assert "creators/**/raw/assets/**/original.info.json" in gitignore
    assert "creators/**/raw/avatar.*" in gitignore
    assert ".obsidian/" in gitignore
    assert "# 抖库" in (vault / "index.md").read_text(encoding="utf-8")
    assert "# 抖库维护规则" in (vault / "AGENTS.md").read_text(encoding="utf-8")
    assert "打开 Obsidian" in result.output
    assert f'vault_path = "{vault}"' in config_path.read_text(encoding="utf-8")

    custom = vault / "AGENTS.md"
    custom.write_text("用户自定义内容\n", encoding="utf-8")
    repeated = runner.invoke(app, ["init", "--config-path", str(config_path)])
    assert repeated.exit_code == 0, repeated.output
    assert custom.read_text(encoding="utf-8") == "用户自定义内容\n"


def test_init_can_inject_into_existing_vault_without_overwrite(tmp_path: Path) -> None:
    vault = tmp_path / "Existing"
    vault.mkdir()
    note = vault / "我的笔记.md"
    note.write_text("保留", encoding="utf-8")
    config_path = tmp_path / "config.toml"

    result = runner.invoke(
        app,
        ["init", "--config-path", str(config_path)],
        input=f"2\n{vault}\n",
    )
    assert result.exit_code == 0, result.output
    assert note.read_text(encoding="utf-8") == "保留"
    assert (vault / "wiki" / "sources").is_dir()
    assert (vault / ".obsidian").is_dir()


def test_existing_vault_receives_new_media_ignore_rules(tmp_path: Path) -> None:
    vault = tmp_path / "Existing"
    vault.mkdir()
    gitignore = vault / ".gitignore"
    gitignore.write_text(".douyin-wiki/\n", encoding="utf-8")

    changed = VaultWriter(vault).initialize(initialize_git=False)

    content = gitignore.read_text(encoding="utf-8")
    assert "creators/**/raw/assets/**/original.info.json" in content
    assert "creators/**/raw/covers/" in content
    assert "creators/**/raw/avatar.*" in content
    assert gitignore in changed


def test_existing_vault_migrates_only_legacy_brand_strings(tmp_path: Path) -> None:
    vault = tmp_path / "Existing"
    machine = vault / "wiki" / ".data" / "sources" / "123.md"
    machine.parent.mkdir(parents=True)
    (vault / "index.md").write_text("# 抖音知识库\n\n用户内容\n", encoding="utf-8")
    (vault / "AGENTS.md").write_text(
        "# Douyin Wiki 维护规则\n\n"
        "这个 Vault 是由 AI 维护、供 AI 检索的个人抖音知识库。\n"
        "用户追加规则\n",
        encoding="utf-8",
    )
    machine.write_text("此文件由 Douyin Wiki 管理，供 Agent 读取。\n", encoding="utf-8")

    changed = VaultWriter(vault).migrate_branding()

    assert "# 抖库" in (vault / "index.md").read_text(encoding="utf-8")
    agents = (vault / "AGENTS.md").read_text(encoding="utf-8")
    assert "# 抖库维护规则" in agents
    assert "用户追加规则" in agents
    assert "此文件由抖库管理" in machine.read_text(encoding="utf-8")
    assert set(changed) == {vault / "index.md", vault / "AGENTS.md", machine}


def test_existing_vault_migrates_visible_statuses_to_chinese(tmp_path: Path) -> None:
    vault = tmp_path / "Existing"
    source = vault / "wiki" / "sources" / "source.md"
    source.parent.mkdir(parents=True)
    source.write_text(
        "---\nstatus: active\nmedia_status: present\nmedia_retention: temporary\n---\n"
        "\n正文中的 active 不应被替换。\n",
        encoding="utf-8",
    )

    changed = VaultWriter(vault).migrate_visible_status_labels()

    content = source.read_text(encoding="utf-8")
    assert "status: 正常" in content
    assert "media_status: 已保留" in content
    assert "media_retention: 临时保留" in content
    assert "正文中的 active 不应被替换" in content
    assert changed == [source]


def test_existing_vault_migrates_visible_times_to_beijing_without_touching_inspiration(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "Existing"
    index = vault / "creators" / "示例_abcd" / "index.md"
    index.parent.mkdir(parents=True)
    index.write_text(
        "---\nlast_synced_at: 2026-08-23 08:40:41.757174+00:00\n---\n\n"
        "# 示例\n\n"
        "- 最近同步：2026-08-23T08:40:41.757174+00:00\n"
        "- 灵感原文：保留 2026-08-23T08:40:41.757174+00:00\n",
        encoding="utf-8",
    )

    changed = VaultWriter(vault).migrate_visible_times_to_beijing()

    content = index.read_text(encoding="utf-8")
    assert "last_synced_at: '2026-08-23T16:40:41.757174+08:00'" in content
    assert "最近同步：2026-08-23 16:40:41（北京时间）" in content
    assert "灵感原文：保留 2026-08-23T08:40:41.757174+00:00" in content
    assert changed == [index]


def test_new_vault_rejects_nonempty_unmanaged_directory(tmp_path: Path) -> None:
    target = tmp_path / "notes"
    target.mkdir()
    (target / "existing.md").write_text("do not touch", encoding="utf-8")
    with pytest.raises(ValueError, match="使用已有 Vault"):
        validate_vault_target(target, VaultSetupMode.NEW)


def test_obsidian_registration_status(tmp_path: Path) -> None:
    vault = tmp_path / "Douyin-Wiki"
    vault.mkdir()
    registry = tmp_path / "obsidian.json"
    registry.write_text(
        json.dumps({"vaults": {"vault-id": {"path": str(vault), "open": True}}}),
        encoding="utf-8",
    )
    registered = obsidian_vault_status(vault, registry_path=registry)
    assert registered["ok"] is True
    assert registered["action"] is None

    missing = obsidian_vault_status(tmp_path / "Other", registry_path=registry)
    assert missing["ok"] is False
    assert "打开本地文件夹作为仓库" in missing["action"]


def test_doctor_reports_unregistered_obsidian_vault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "Douyin-Wiki"
    vault.mkdir()
    registry = tmp_path / "obsidian.json"
    registry.write_text('{"vaults": {}}', encoding="utf-8")
    monkeypatch.setattr("douyin_wiki.setup.default_obsidian_registry_path", lambda: registry)

    checks = doctor(AppConfig(vault_path=vault))
    assert checks["obsidian_vault"]["ok"] is False
    assert str(vault) in checks["obsidian_vault"]["action"]




def test_doctor_transcription_and_ocr_follow_media_providers(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    config = AppConfig(vault_path=vault)
    config.media.asr_provider = "auto"
    config.media.ocr_provider = "auto"

    import importlib.util as importlib_util

    real_find = importlib_util.find_spec

    def fake_find(name, package=None):
        if name in {"mlx_audio", "rapidocr"}:
            return None
        return real_find(name, package)

    monkeypatch.setattr("importlib.util.find_spec", fake_find)
    monkeypatch.setattr(
        "shutil.which",
        lambda command: None if command in {"whisper", "swift"} else "/bin/true",
    )
    checks = doctor(config)
    assert checks["transcription"]["ok"] is False
    assert "mlx_audio" in (checks["transcription"]["message"] or "")
    assert checks["ocr"]["ok"] is False
    assert "RapidOCR" in (checks["ocr"]["message"] or "")

    config.media.ocr_provider = "vision"
    checks = doctor(config)
    assert checks["transcription"]["provider"] == "qwen3"
    assert checks["ocr"]["provider"] == "vision"
    assert checks["ocr"]["ok"] is False


def test_qwen_doctor_requires_all_exact_snapshots_without_loading(tmp_path, monkeypatch) -> None:
    from douyin_wiki.adapters.qwen3_runtime import Qwen3Runtime
    from douyin_wiki.config import MediaSettings

    media = MediaSettings(
        asr_provider="qwen3",
        asr_model="mlx-community/Qwen3-ASR-1.7B-bf16",
        asr_model_revision="e1f6c266914abc5a46e8756e02580f834a6cf8a7",
        vad_model="mlx-community/silero-vad",
        vad_model_revision="7bc17f22d3c0451bd3a6cd71e759b009271ff49a",
    )
    config = AppConfig(vault_path=tmp_path / "vault", media=media)
    cache = tmp_path / "cache"
    monkeypatch.setenv("HF_HUB_CACHE", str(cache))
    monkeypatch.setattr("importlib.util.find_spec", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        Qwen3Runtime, "ensure_ready", lambda self: pytest.fail("doctor loaded weights")
    )
    checks = doctor(config)
    assert checks["transcription"]["ok"] is False
    assert checks["qwen3"]["ok"] is False
    assert "hf download" in checks["qwen3"]["message"]
    paths = []
    for role, repo, revision in (
        ("asr", media.asr_model, media.asr_model_revision),
        ("vad", media.vad_model, media.vad_model_revision),
        ("alignment", media.alignment_model, media.alignment_model_revision),
    ):
        path = cache / ("models--" + repo.replace("/", "--")) / "snapshots" / revision
        path.mkdir(parents=True)
        config_data = {"model_type": "qwen3_asr"}
        if role == "vad":
            config_data = {"model_type": "silero_vad", "architecture": "silero_vad"}
        if role == "alignment":
            config_data.update(timestamp_segment_time=80, timestamp_token_id=151705)
        (path / "config.json").write_text(json.dumps(config_data))
        (path / "model.safetensors").write_bytes(b"weights")
        if role != "vad":
            for name in ("tokenizer_config.json", "vocab.json", "preprocessor_config.json"):
                (path / name).write_text("{}")
            (path / "merges.txt").write_text("#version: 0.2")
        paths.append(path)
    assert doctor(config)["transcription"]["ok"] is True
    (paths[-1] / "model.safetensors").unlink()
    assert doctor(config)["transcription"]["ok"] is False
    assert doctor(config)["qwen3"]["models"]["alignment"]["ok"] is False


def test_qwen_doctor_missing_dependency_is_not_rescued_by_legacy(tmp_path, monkeypatch) -> None:
    config = AppConfig(vault_path=tmp_path)
    config.media.asr_provider = "qwen3"
    monkeypatch.setattr(
        "importlib.util.find_spec", lambda name, *args: None if name == "mlx_audio" else object()
    )
    monkeypatch.setattr("shutil.which", lambda command: "/bin/true")
    checks = doctor(config)
    assert checks["transcription"]["ok"] is False
    assert "mlx_audio" in checks["qwen3"]["missing_dependencies"]
