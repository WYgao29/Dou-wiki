"""Isolated local browser smoke check; never opens the authenticated profile."""

import json
import os
import signal
import subprocess
import sys
import tempfile
from contextlib import suppress

_PROBE = """
import json
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright
try:
    with sync_playwright() as playwright:
        executable = Path(playwright.chromium.executable_path)
        if not executable.is_file():
            raise FileNotFoundError('缺少 Chromium 登录内核：' + str(executable))
        context = playwright.chromium.launch_persistent_context(
            sys.argv[1], headless=True, timeout=5000)
        try:
            print(json.dumps({'ok': True, 'executable': str(executable)}))
        finally:
            context.close()
except Exception as error:
    print(json.dumps({'ok': False, 'message': str(error)[:1200]}))
"""


def playwright_health() -> dict:
    with tempfile.TemporaryDirectory(prefix="douyin-browser-check-") as profile:
        return _probe(profile)


def _probe(profile: str) -> dict:
    process = None
    try:
        process = subprocess.Popen(
            [sys.executable, "-c", _PROBE, profile],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        stdout, stderr = process.communicate(timeout=12)
        if process.returncode:
            raise RuntimeError(stderr[-1200:] or f"浏览器探测退出码 {process.returncode}")
        result = json.loads(stdout)
        if not result.get("ok"):
            result["message"] = str(result.get("message", "浏览器不可用")) + (
                "；请在当前 runtime 执行 python -m playwright install chromium"
            )
        return result
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        if process is not None:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
        return {"ok": False, "message": f"浏览器启动检查失败：{exc}"}
