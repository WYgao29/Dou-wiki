from __future__ import annotations

import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest

from douyin_wiki.config import MediaSettings
from douyin_wiki.errors import ExternalToolError
from douyin_wiki.favorites_models import FavoriteFolder, FavoriteInventory, FavoriteWork

SCRIPT = Path(__file__).parents[1] / "scripts/verify_favorites_api.py"


def verifier():
    assert SCRIPT.is_file(), "read-only favorites verification script is missing"
    return runpy.run_path(str(SCRIPT))["verify_readonly"]


async def test_verification_requires_opt_in_before_accessing_config(monkeypatch, tmp_path):
    verify = verifier()
    monkeypatch.delenv("DOUYIN_WIKI_RUN_LIVE", raising=False)

    def forbidden():
        pytest.fail("config must not be accessed without live opt-in")

    monkeypatch.setitem(verify.__globals__, "load_config", forbidden)
    with pytest.raises(ValueError, match="DOUYIN_WIKI_RUN_LIVE"):
        await verify(
            directory_only=True, folder_ids=None, page_size=20, output_dir=tmp_path / "out"
        )
    assert not (tmp_path / "out").exists()


async def test_verification_refuses_reports_inside_repository(monkeypatch):
    verify = verifier()
    monkeypatch.setenv("DOUYIN_WIKI_RUN_LIVE", "1")
    with pytest.raises(ValueError, match="仓库外"):
        await verify(
            directory_only=True,
            folder_ids=None,
            page_size=20,
            output_dir=SCRIPT.parent / "private-results",
        )


async def test_verification_failure_removes_only_its_profile_copy(monkeypatch, tmp_path):
    verify = verifier()
    monkeypatch.setenv("DOUYIN_WIKI_RUN_LIVE", "1")
    profile = tmp_path / "original"
    cookies = profile / "Default/Cookies"
    cookies.parent.mkdir(parents=True)
    cookies.write_bytes(b"synthetic session fixture")
    config = SimpleNamespace(media=MediaSettings(), browser_profile_dir=profile)
    monkeypatch.setitem(verify.__globals__, "load_config", lambda: config)
    seen = []

    async def fail(self, **_):
        seen.append(self.profile_dir)
        assert self.profile_dir != profile
        assert (self.profile_dir / "Default/Cookies").read_bytes() == b"synthetic session fixture"
        raise ExternalToolError("synthetic failure")

    monkeypatch.setattr("douyin_wiki.adapters.favorites.DouyinFavoritesAdapter.inventory", fail)
    result = await verify(
        directory_only=True, folder_ids=None, page_size=20, output_dir=tmp_path / "reports"
    )
    assert not result["passed"]
    assert result["error"] == "ExternalToolError"
    assert result["temporary_profile_removed"]
    assert seen and not seen[0].exists()
    assert cookies.read_bytes() == b"synthetic session fixture"
    assert Path(result["result_path"]).is_file()


@pytest.mark.parametrize(
    "warning,expected",
    [
        ("未取得可信的收藏分页结束信号", False),
        ("收藏夹「测试」显示 7 条，实际读取 4 条，数量不一致", True),
    ],
)
async def test_verification_distinguishes_incomplete_read_from_quantity_difference(
    monkeypatch, tmp_path, warning, expected
):
    verify = verifier()
    monkeypatch.setenv("DOUYIN_WIKI_RUN_LIVE", "1")
    profile = tmp_path / "original"
    profile.mkdir()
    config = SimpleNamespace(media=MediaSettings(), browser_profile_dir=profile)
    monkeypatch.setitem(verify.__globals__, "load_config", lambda: config)

    async def partial(self, **_):
        return FavoriteInventory(
            account_id="fixture",
            folders_complete=True,
            complete=False,
            folders=[FavoriteFolder(id="12", name="测试", reported_count=7)],
            works=[
                FavoriteWork(
                    work_id=str(w),
                    canonical_url=f"https://www.douyin.com/video/{w}",
                    folder_ids=["12"],
                )
                for w in range(1, 5)
            ],
            warnings=[warning],
        )

    monkeypatch.setattr("douyin_wiki.adapters.favorites.DouyinFavoritesAdapter.inventory", partial)
    result = await verify(
        directory_only=False, folder_ids=["12"], page_size=20, output_dir=tmp_path / "reports"
    )
    assert result["passed"] is expected
    assert result["temporary_profile_removed"]
