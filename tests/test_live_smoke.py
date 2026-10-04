from __future__ import annotations

import os

import pytest

from douyin_wiki.config import load_config
from douyin_wiki.service import DouyinWikiService


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_douyin_browser_sessions_are_ready() -> None:
    """Opt-in, read-only smoke test for the real local browser sessions."""
    if os.environ.get("DOUYIN_WIKI_RUN_LIVE") != "1":
        pytest.skip("set DOUYIN_WIKI_RUN_LIVE=1 to run real browser checks")

    service = DouyinWikiService(load_config())
    service.initialize_runtime()
    status = await service.get_auth_status()

    assert status["image_note"]["ok"] is True
    assert status["image_note"]["server_verified"] is True
    assert status["creator"]["ok"] is True
    assert status["creator"]["server_verified"] is True
    assert status["cookie_values_exposed"] is False


@pytest.mark.live
async def test_live_favorites_directory_readonly(tmp_path):
    if os.environ.get("DOUYIN_WIKI_RUN_LIVE") != "1":
        pytest.skip("set DOUYIN_WIKI_RUN_LIVE=1 to run real browser checks")
    import runpy
    from pathlib import Path

    verify = runpy.run_path(str(Path(__file__).parents[1] / "scripts/verify_favorites_api.py"))[
        "verify_readonly"
    ]
    result = await verify(
        directory_only=True, folder_ids=None, page_size=2, output_dir=tmp_path / "favorites-results"
    )
    assert result["passed"]
    assert result["folders_complete"]
    assert not result["works"]
    assert result["temporary_profile_removed"]
