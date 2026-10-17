from __future__ import annotations

import importlib.util
import json
import os
import platform
import plistlib
import re
import shlex
import shutil
import socket
import subprocess
import sys
import sysconfig
import tempfile
import tomllib
from enum import StrEnum
from pathlib import Path
from typing import Any

from .adapters.browser_health import playwright_health
from .config import (
    AppConfig,
    default_config_path,
    render_default_config,
)
from .errors import ExternalToolError


def qwen3_health(settings) -> dict[str, Any]:
    """Inspect pinned local data and packages without instantiating models."""
    from .adapters.qwen3_runtime import inspect_snapshot

    missing = [
        name
        for name in ("mlx", "mlx_audio", "transformers", "soundfile", "huggingface_hub")
        if importlib.util.find_spec(name) is None
    ]
    platform_ok = platform.system() == "Darwin" and platform.machine().lower() in {
        "arm64",
        "aarch64",
    }
    cache = os.environ.get("HF_HUB_CACHE") or os.environ.get("HUGGINGFACE_HUB_CACHE")
    if cache is None:
        try:
            from huggingface_hub.constants import HF_HUB_CACHE

            cache = HF_HUB_CACHE
        except ImportError:
            home = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))
            cache = str(home / "hub")
    models = {}
    messages = []
    for kind, repo, revision in (
        ("asr", settings.asr_model, settings.asr_model_revision),
        ("vad", settings.vad_model, settings.vad_model_revision),
        ("alignment", settings.alignment_model, settings.alignment_model_revision),
    ):
        path = Path(cache) / ("models--" + repo.replace("/", "--")) / "snapshots" / revision
        command = shlex.join(
            [
                str(Path(sysconfig.get_path("scripts")) / "hf"),
                "download",
                repo,
                "--revision",
                revision,
                "--include",
                "*.safetensors",
                "--include",
                "*.json",
                "--include",
                "*.txt",
            ]
        )
        reason = None
        try:
            if not re.fullmatch(r"[0-9a-f]{40}", revision):
                raise ValueError("revision 不是完整 SHA")
            inspect_snapshot(path, kind)
            if kind != "vad":
                required = ("tokenizer_config.json", "preprocessor_config.json")
                if any(not (path / name).is_file() for name in required):
                    raise ValueError("缺少 tokenizer / feature extractor 配置")
                if not (path / "tokenizer.json").is_file() and not all(
                    (path / name).is_file() for name in ("vocab.json", "merges.txt")
                ):
                    raise ValueError("缺少 tokenizer 数据")
        except (ExternalToolError, OSError, ValueError) as exc:
            reason = str(exc)
            messages.append(f"{kind}: {reason}；{command}")
        models[kind] = {
            "ok": reason is None,
            "model": repo,
            "revision": revision,
            "message": reason,
            "prepare_command": command,
        }
    if missing:
        messages.insert(0, f"缺少依赖 {', '.join(missing)}；运行 uv sync --extra qwen3")
    if not platform_ok:
        messages.insert(0, "Qwen3 MLX 需要 Apple Silicon Mac")
    return {
        "ok": platform_ok and not missing and all(item["ok"] for item in models.values()),
        "platform_ok": platform_ok,
        "missing_dependencies": missing,
        "models": models,
        "message": "\n".join(messages) or None,
    }


class VaultSetupMode(StrEnum):
    NEW = "new"
    EXISTING = "existing"


def validate_vault_target(path: Path, mode: VaultSetupMode) -> Path:
    """Resolve and validate a user-selected Vault without modifying it."""
    target = path.expanduser().resolve()
    if target in {Path("/"), Path.home().resolve()}:
        raise ValueError("Vault 不能使用磁盘根目录或用户主目录")
    if target.exists() and not target.is_dir():
        raise ValueError(f"Vault 路径不是目录：{target}")
    if mode == VaultSetupMode.EXISTING and not target.is_dir():
        raise ValueError(f"已有 Vault 不存在：{target}")
    if mode == VaultSetupMode.NEW and target.is_dir():
        initialized = (target / ".douyin-wiki").is_dir()
        allowed = {".DS_Store", ".git", ".gitignore", ".obsidian"}
        unexpected = {item.name for item in target.iterdir()} - allowed
        if unexpected and not initialized:
            raise ValueError(
                f"新 Vault 目录不是空目录；请选择“使用已有 Vault”，或换一个新目录：{target}"
            )
    return target


def default_obsidian_registry_path() -> Path:
    return Path.home() / "Library" / "Application Support" / "obsidian" / "obsidian.json"


def obsidian_vault_status(vault_path: Path, *, registry_path: Path | None = None) -> dict[str, Any]:
    """Report whether Obsidian has registered the selected directory as a Vault."""
    target = vault_path.expanduser().resolve()
    registry = registry_path or default_obsidian_registry_path()
    registered = False
    registry_error: str | None = None
    try:
        payload = json.loads(registry.read_text(encoding="utf-8"))
        vaults = payload.get("vaults", {})
        records = vaults.values() if isinstance(vaults, dict) else []
        registered = any(
            Path(record.get("path", "")).expanduser().resolve() == target
            for record in records
            if isinstance(record, dict) and record.get("path")
        )
    except FileNotFoundError:
        registry_error = "未找到 Obsidian Vault 注册表"
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        registry_error = f"无法读取 Obsidian Vault 注册表：{exc}"

    action = None
    if not registered:
        action = (
            f"打开 Obsidian → 管理仓库（Manage vaults）→ 打开本地文件夹作为仓库，选择：{target}"
        )
    return {
        "ok": registered,
        "registered": registered,
        "path": str(target),
        "registry_path": str(registry),
        "message": (
            "已在 Obsidian 中登记" if registered else registry_error or "尚未在 Obsidian 中登记"
        ),
        "action": action,
    }


def write_config(config: AppConfig, path: Path | None = None, *, overwrite: bool = False) -> Path:
    target = path or default_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not overwrite:
        return target
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(render_default_config(config))
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = Path(handle.name)
        temporary_path.replace(target)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return target


def update_config_values(
    path: Path,
    updates: dict[str | None, dict[str, object]],
) -> Path:
    """Patch known TOML keys while preserving comments and unknown settings."""
    if not path.exists():
        raise FileNotFoundError(path)
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    for section, values in updates.items():
        for key, value in values.items():
            lines = _patch_toml_value(lines, section, key, _toml_scalar(value))
    content = "".join(lines)
    # Never replace a valid configuration with a malformed line-level patch.
    tomllib.loads(content)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = Path(handle.name)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return path


def _patch_toml_value(lines: list[str], section: str | None, key: str, rendered: str) -> list[str]:
    records = _toml_records(lines)
    existing = next(
        (
            record
            for record in records
            if record["kind"] == "key" and record["section"] == section and record["name"] == key
        ),
        None,
    )
    if existing is not None:
        start = int(existing["start"])
        end = int(existing["end"])
        original = lines[start].rstrip("\r\n")
        indent = re.match(r"^\s*", original).group(0)
        comment = _toml_inline_comment(original)
        return [
            *lines[:start],
            f"{indent}{key} = {rendered}{comment}\n",
            *lines[end:],
        ]

    section_records = [record for record in records if record["kind"] == "section"]
    if section is None:
        insertion = int(section_records[0]["start"]) if section_records else len(lines)
    else:
        header = next((record for record in section_records if record["name"] == section), None)
        if header is None:
            result = list(lines)
            if result and not result[-1].endswith(("\n", "\r")):
                result[-1] += "\n"
            if result and result[-1].strip():
                result.append("\n")
            result.extend([f"[{section}]\n", f"{key} = {rendered}\n"])
            return result
        following = next(
            (record for record in section_records if int(record["start"]) > int(header["start"])),
            None,
        )
        insertion = int(following["start"]) if following else len(lines)
    prefix = list(lines[:insertion])
    if prefix and not prefix[-1].endswith(("\n", "\r")):
        prefix[-1] += "\n"
    return [*prefix, f"{key} = {rendered}\n", *lines[insertion:]]


def _toml_records(lines: list[str]) -> list[dict[str, object]]:
    """Locate real TOML sections and key spans without parsing text inside values."""
    records: list[dict[str, object]] = []
    section: str | None = None
    string_state: str | None = None
    square_depth = 0
    brace_depth = 0
    pending_key: dict[str, object] | None = None

    for line_index, line in enumerate(lines):
        at_top_level = string_state is None and square_depth == 0 and brace_depth == 0
        if at_top_level:
            header_match = re.match(r"^\s*\[([A-Za-z0-9_.-]+)\]\s*(?:#.*)?$", line.rstrip("\r\n"))
            if header_match:
                section = header_match.group(1)
                records.append(
                    {"kind": "section", "section": section, "name": section, "start": line_index}
                )
            elif key_match := re.match(r"^\s*([A-Za-z0-9_-]+)\s*=", line.rstrip("\r\n")):
                pending_key = {
                    "kind": "key",
                    "section": section,
                    "name": key_match.group(1),
                    "start": line_index,
                    "end": line_index + 1,
                }
                records.append(pending_key)

        string_state, square_depth, brace_depth = _scan_toml_line(
            line, string_state, square_depth, brace_depth
        )
        if (
            pending_key is not None
            and string_state is None
            and square_depth == 0
            and brace_depth == 0
        ):
            pending_key["end"] = line_index + 1
            pending_key = None

    if pending_key is not None:
        pending_key["end"] = len(lines)
    return records


def _scan_toml_line(
    line: str,
    string_state: str | None,
    square_depth: int,
    brace_depth: int,
) -> tuple[str | None, int, int]:
    index = 0
    while index < len(line):
        if string_state == "triple_basic":
            if line.startswith('"""', index):
                string_state = None
                index += 3
            elif line[index] == "\\":
                index += 2
            else:
                index += 1
            continue
        if string_state == "triple_literal":
            if line.startswith("'''", index):
                string_state = None
                index += 3
            else:
                index += 1
            continue
        if string_state == "basic":
            if line[index] == "\\":
                index += 2
            elif line[index] == '"':
                string_state = None
                index += 1
            else:
                index += 1
            continue
        if string_state == "literal":
            if line[index] == "'":
                string_state = None
            index += 1
            continue

        if line[index] == "#":
            break
        if line.startswith('"""', index):
            string_state = "triple_basic"
            index += 3
        elif line.startswith("'''", index):
            string_state = "triple_literal"
            index += 3
        elif line[index] == '"':
            string_state = "basic"
            index += 1
        elif line[index] == "'":
            string_state = "literal"
            index += 1
        else:
            if line[index] == "[":
                square_depth += 1
            elif line[index] == "]":
                square_depth = max(0, square_depth - 1)
            elif line[index] == "{":
                brace_depth += 1
            elif line[index] == "}":
                brace_depth = max(0, brace_depth - 1)
            index += 1
    return string_state, square_depth, brace_depth


def _toml_scalar(value: object) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


def _toml_inline_comment(line: str) -> str:
    quote: str | None = None
    escaped = False
    for index, character in enumerate(line):
        if escaped:
            escaped = False
            continue
        if character == "\\" and quote == '"':
            escaped = True
        elif character in {'"', "'"}:
            quote = None if quote == character else character if quote is None else quote
        elif character == "#" and quote is None:
            return " " + line[index:].lstrip()
    return ""


def doctor(config: AppConfig) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    checks["analysis_mode"] = {
        "ok": True,
        "mode": config.analysis_mode.value,
        "message": "校正与分析由 Gateway Agent 完成",
    }
    for command in ("yt-dlp", "ffmpeg", "ffprobe", "swift", "osascript", "git"):
        checks[command] = {"ok": bool(shutil.which(command)), "path": shutil.which(command)}
    playwright_available = importlib.util.find_spec("playwright") is not None
    checks["playwright"] = playwright_health() if playwright_available else {
        "ok": False, "message": "未安装 Playwright；请在当前 runtime 安装运行依赖",
    }
    web_dependencies = ("fastapi", "uvicorn", "jinja2", "markdown_it", "nh3", "watchfiles")
    missing_web = [name for name in web_dependencies if importlib.util.find_spec(name) is None]
    checks["web_dependencies"] = {
        "ok": not missing_web,
        "required": config.web.enabled,
        "missing": missing_web,
        "message": None if not missing_web else "缺少 Web 依赖；请运行 uv sync",
    }
    port_available = False
    already_running = False
    try:
        with socket.create_connection((config.web.host, config.web.port), timeout=0.15):
            already_running = True
    except OSError:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind((config.web.host, config.web.port))
                port_available = True
        except OSError:
            port_available = False
    web_plist = Path.home() / "Library" / "LaunchAgents" / "com.local.douyin-wiki.web.plist"
    checks["web"] = {
        "ok": bool(config.web.enabled and (port_available or already_running)),
        "enabled": config.web.enabled,
        "address": f"http://{config.web.host}:{config.web.port}",
        "port_available": port_available,
        "running": already_running,
        "launch_agent_installed": web_plist.exists(),
        "message": (
            "Web 服务正在运行"
            if already_running
            else "端口可用，可运行 douyin-wiki web run"
            if port_available
            else "Web 端口已被其他程序占用"
        ),
    }
    browser_name = config.media.browser.lower()
    app_names = {
        "chrome": "Google Chrome.app",
        "safari": "Safari.app",
        "firefox": "Firefox.app",
        "edge": "Microsoft Edge.app",
        "arc": "Arc.app",
    }
    browser_app = Path("/Applications") / app_names.get(browser_name, f"{browser_name}.app")
    checks["browser"] = {"ok": browser_app.exists(), "path": str(browser_app)}
    profile = config.browser_profile_dir
    checks["douyin_browser_profile"] = {
        "ok": profile.exists(),
        "required": False,
        "path": str(profile),
        "message": (
            "图文与博主清点专用浏览器目录存在；这不代表登录仍有效，"
            "请到「设置 › 授权状态」检查，或运行 douyin-wiki auth status"
            if profile.exists()
            else "首次保存图文或清点博主前，请到「设置 › 授权状态」授权，"
            "或运行 douyin-wiki auth douyin"
        ),
    }
    checks["vault"] = {"ok": config.vault_path.exists(), "path": str(config.vault_path)}
    checks["obsidian_vault"] = obsidian_vault_status(config.vault_path)
    checks["database"] = {
        "ok": config.database_path.exists(),
        "path": str(config.database_path),
    }
    sentence_transformers = importlib.util.find_spec("sentence_transformers") is not None
    checks["embeddings"] = {
        "ok": sentence_transformers,
        "model": config.embeddings.model,
        "local_files_only": True,
        "message": "运行只读取本地模型缓存；缺缓存时使用字符检索替代"
        if sentence_transformers
        else "未安装 embeddings extra；将使用字符 n-gram fallback",
    }
    for retired in ("whisper", "mlx_whisper", "funasr"):
        checks[retired] = {
            "ok": False,
            "retired": True,
            "path": None,
            "message": "旧识别引擎已移除；当前使用 Qwen3",
        }
    rapidocr_ok = importlib.util.find_spec("rapidocr") is not None
    checks["rapidocr"] = {
        "ok": rapidocr_ok,
        "message": None if rapidocr_ok else "未安装 ocr extra（rapidocr）；RapidOCR 不可用",
    }
    checks["qwen3"] = qwen3_health(config.media)
    checks["transcription"] = {
        "ok": checks["qwen3"]["ok"],
        "provider": "qwen3",
        "message": checks["qwen3"]["message"],
    }
    ocr_provider = config.media.ocr_provider
    vision_ocr_ok = checks["swift"]["ok"]
    if ocr_provider == "rapidocr":
        ocr_ok = rapidocr_ok
        ocr_message = (
            None if ocr_ok else "ocr_provider=rapidocr 但未安装 rapidocr；运行 uv sync --extra ocr"
        )
    elif ocr_provider == "vision":
        ocr_ok = vision_ocr_ok
        ocr_message = None if ocr_ok else "ocr_provider=vision 但缺少 Swift；OCR 不可用"
    else:
        ocr_ok = rapidocr_ok or vision_ocr_ok
        ocr_message = (
            None
            if ocr_ok
            else "未安装 RapidOCR（uv sync --extra ocr）且缺少 Swift；视频和图文 OCR 不可用"
        )
    checks["ocr"] = {
        "ok": ocr_ok,
        "provider": ocr_provider,
        "message": ocr_message,
    }
    checks["reminders"] = {
        "ok": checks["osascript"]["ok"],
        "required": False,
        "message": (
            "命令可用；首次创建提醒时仍可能需要授予自动化权限"
            if checks["osascript"]["ok"]
            else "无法创建 macOS 提醒事项"
        ),
    }
    checks["overall"] = all(
        value.get("ok", False)
        for key, value in checks.items()
        if key
        in {
            "yt-dlp",
            "ffmpeg",
            "ffprobe",
            "git",
            "browser",
            "playwright",
            "transcription",
            "ocr",
            "vault",
            "obsidian_vault",
            "database",
            "llm",
            "web_dependencies",
            "web",
        }
    )
    return checks


class LaunchAgentInstaller:
    WORKER_LABEL = "com.local.douyin-wiki.worker"
    MAINTENANCE_LABEL = "com.local.douyin-wiki.maintenance"

    def __init__(self, config_path: Path | None = None) -> None:
        self.config_path = config_path or default_config_path()
        self.launch_agents = Path.home() / "Library" / "LaunchAgents"
        self.logs = Path.home() / "Library" / "Logs" / "douyin-wiki"

    def install(self) -> list[Path]:
        self.launch_agents.mkdir(parents=True, exist_ok=True)
        self.logs.mkdir(parents=True, exist_ok=True)
        worker_path = self.launch_agents / f"{self.WORKER_LABEL}.plist"
        maintenance_path = self.launch_agents / f"{self.MAINTENANCE_LABEL}.plist"
        executable_dirs = [
            str(Path(sys.executable).parent),
            "/opt/homebrew/bin",
            "/usr/local/bin",
            "/usr/bin",
            "/bin",
            "/usr/sbin",
            "/sbin",
        ]
        inherited_path = os.environ.get("PATH", "").split(os.pathsep)
        launch_path = os.pathsep.join(dict.fromkeys([*executable_dirs, *inherited_path]))
        common_env = {
            "DOUYIN_WIKI_CONFIG": str(self.config_path),
            "PATH": launch_path,
            "PYTHONUNBUFFERED": "1",
        }
        worker = {
            "Label": self.WORKER_LABEL,
            "ProgramArguments": [
                sys.executable,
                "-m",
                "douyin_wiki",
                "worker",
                "run",
                "--forever",
            ],
            "RunAtLoad": True,
            "KeepAlive": True,
            "ThrottleInterval": 10,
            "EnvironmentVariables": common_env,
            "StandardOutPath": str(self.logs / "worker.log"),
            "StandardErrorPath": str(self.logs / "worker-error.log"),
        }
        maintenance = {
            "Label": self.MAINTENANCE_LABEL,
            "ProgramArguments": [
                sys.executable,
                "-m",
                "douyin_wiki",
                "maintenance",
                "run",
                "--apply",
            ],
            "StartCalendarInterval": {"Weekday": 0, "Hour": 3, "Minute": 0},
            "EnvironmentVariables": common_env,
            "StandardOutPath": str(self.logs / "maintenance.log"),
            "StandardErrorPath": str(self.logs / "maintenance-error.log"),
        }
        for path, payload in ((worker_path, worker), (maintenance_path, maintenance)):
            with path.open("wb") as handle:
                plistlib.dump(payload, handle)
            subprocess.run(["launchctl", "unload", str(path)], capture_output=True, check=False)
            subprocess.run(["launchctl", "load", str(path)], capture_output=True, check=False)
        return [worker_path, maintenance_path]

    def uninstall(self) -> list[Path]:
        removed: list[Path] = []
        for label in (self.WORKER_LABEL, self.MAINTENANCE_LABEL):
            path = self.launch_agents / f"{label}.plist"
            if path.exists():
                subprocess.run(["launchctl", "unload", str(path)], capture_output=True, check=False)
                path.unlink()
                removed.append(path)
        return removed


class WebLaunchAgentInstaller:
    LABEL = "com.local.douyin-wiki.web"

    def __init__(self, config_path: Path | None = None) -> None:
        self.config_path = config_path or default_config_path()
        self.launch_agents = Path.home() / "Library" / "LaunchAgents"
        self.logs = Path.home() / "Library" / "Logs" / "douyin-wiki"

    @property
    def path(self) -> Path:
        return self.launch_agents / f"{self.LABEL}.plist"

    def install(self) -> Path:
        self.launch_agents.mkdir(parents=True, exist_ok=True)
        self.logs.mkdir(parents=True, exist_ok=True)
        executable_dirs = [
            str(Path(sys.executable).parent),
            "/opt/homebrew/bin",
            "/usr/local/bin",
            "/usr/bin",
            "/bin",
            "/usr/sbin",
            "/sbin",
        ]
        inherited_path = os.environ.get("PATH", "").split(os.pathsep)
        launch_path = os.pathsep.join(dict.fromkeys([*executable_dirs, *inherited_path]))
        payload = {
            "Label": self.LABEL,
            "ProgramArguments": [
                sys.executable,
                "-m",
                "douyin_wiki",
                "web",
                "run",
                "--config-path",
                str(self.config_path),
            ],
            "RunAtLoad": True,
            "KeepAlive": True,
            "ThrottleInterval": 10,
            "EnvironmentVariables": {
                "DOUYIN_WIKI_CONFIG": str(self.config_path),
                "PATH": launch_path,
                "PYTHONUNBUFFERED": "1",
            },
            "StandardOutPath": str(self.logs / "web.log"),
            "StandardErrorPath": str(self.logs / "web-error.log"),
        }
        with self.path.open("wb") as handle:
            plistlib.dump(payload, handle)
        subprocess.run(["launchctl", "unload", str(self.path)], capture_output=True, check=False)
        subprocess.run(["launchctl", "load", str(self.path)], capture_output=True, check=False)
        return self.path

    def uninstall(self) -> Path | None:
        if not self.path.exists():
            return None
        subprocess.run(["launchctl", "unload", str(self.path)], capture_output=True, check=False)
        self.path.unlink()
        return self.path

    def status(self, config: AppConfig) -> dict[str, Any]:
        running = False
        try:
            with socket.create_connection((config.web.host, config.web.port), timeout=0.2):
                running = True
        except OSError:
            pass
        return {
            "installed": self.path.exists(),
            "running": running,
            "address": f"http://{config.web.host}:{config.web.port}",
            "launch_agent": str(self.path),
            "log": str(self.logs / "web.log"),
            "error_log": str(self.logs / "web-error.log"),
        }
