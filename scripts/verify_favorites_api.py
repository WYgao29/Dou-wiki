"""Opt-in favorites verification using a disposable copy of the login session.

Never constructs a service, task queue, download adapter, or confirmation request.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from playwright.async_api import async_playwright

from douyin_wiki.adapters.favorites import DouyinFavoritesAdapter
from douyin_wiki.adapters.favorites_api import parse_favorite_page
from douyin_wiki.adapters.favorites_browser import FavoritesBrowserClient
from douyin_wiki.config import load_config
from douyin_wiki.errors import ExternalToolError

_SESSION_FILES = ["Local State", "First Run", "Last Version"] + [
    f"Default/{name}"
    for name in (
        "Cookies",
        "Cookies-journal",
        "Network",
        "Preferences",
        "Secure Preferences",
        "Local Storage",
        "Session Storage",
        "IndexedDB",
        "Web Data",
        "Web Data-journal",
    )
]


async def _force_pages(client, adapter, account_id, *, resource, folder_id, count, events):
    cursor, seen, ids = "0", set(), set()
    for _ in range(500):
        await adapter._assert_account(client.page, account_id)
        seen.add(cursor)
        raw = await client.read_page(
            resource=resource, folder_id=folder_id, cursor=cursor, count=count, use_cached=False
        )
        parsed = parse_favorite_page(raw)
        await adapter._assert_account(client.page, account_id)
        rows = parsed.folders if resource == "directory" else parsed.works
        ids.update(row.id if resource == "directory" else row.work_id for row in rows)
        events.append(
            {
                "resource": resource,
                "folder_id": folder_id,
                "request_cursor": cursor,
                "request_count": count,
                "http_status": raw.http_status,
                "has_more": parsed.has_more,
                "next_cursor": parsed.next_cursor,
                "observed": len(rows),
            }
        )
        advancing_gap = (
            resource == "folder"
            and not rows
            and parsed.has_more is True
            and parsed.next_cursor
            and parsed.next_cursor not in seen
            and set(parsed.warnings) <= {"收藏接口列表缺失或结构异常"}
        )
        if not parsed.valid and not advancing_gap:
            raise ExternalToolError("主动分页返回不可信结果")
        if parsed.has_more is False:
            return ids
        if (
            (not rows and resource == "directory")
            or not parsed.next_cursor
            or parsed.next_cursor in seen
        ):
            raise ExternalToolError("主动分页没有可信的下一页")
        cursor = parsed.next_cursor
    raise ExternalToolError("主动分页达到安全上限")


async def verify_readonly(*, directory_only, folder_ids, page_size, output_dir):
    if os.environ.get("DOUYIN_WIKI_RUN_LIVE") != "1":
        raise ValueError("必须设置 DOUYIN_WIKI_RUN_LIVE=1 才能访问真实账号")
    output_dir = Path(output_dir).expanduser().resolve()
    if output_dir.is_relative_to(Path(__file__).resolve().parents[1]):
        raise ValueError("验证记录必须保存到仓库外目录")
    if not 1 <= page_size <= 100 or (directory_only and folder_ids):
        raise ValueError("页大小必须为 1–100，目录模式不能同时指定收藏夹")
    if not directory_only and not folder_ids:
        raise ValueError("必须选择 --directory-only 或 --folder-id")
    config = load_config()
    source = DouyinFavoritesAdapter(config.media, config.browser_profile_dir)
    if not source.profile_dir.is_dir():
        raise ExternalToolError("专用浏览器会话不存在，请先完成授权")
    report = {
        "mode": "read_only",
        "tested_at": datetime.now().astimezone().isoformat(),
        "page_size": page_size,
        "passed": False,
        "forced_pages": [],
    }
    with tempfile.TemporaryDirectory(prefix="douyin-favorites-verify-") as temporary:
        profile = Path(temporary) / "profile"
        try:
            async with source._locked_profile():
                profile.mkdir(mode=0o700)
                for relative in _SESSION_FILES:
                    original, copied = source.profile_dir / relative, profile / relative
                    if not original.exists():
                        continue
                    copied.parent.mkdir(parents=True, exist_ok=True)
                    if original.is_dir():
                        shutil.copytree(original, copied)
                    else:
                        shutil.copy2(original, copied)
            adapter = DouyinFavoritesAdapter(config.media, profile)
            # The public adapter always uses its standard page size first.
            inventory = await adapter.inventory(
                directory_only=directory_only, folder_ids=folder_ids
            )
            report.update(
                {
                    "folders": [f.model_dump() for f in inventory.folders],
                    "works": [
                        {
                            "id": w.work_id,
                            "folder_ids": w.folder_ids,
                            "source_kind": w.source_kind,
                            "available": w.available,
                        }
                        for w in inventory.works
                    ],
                    "complete": inventory.complete,
                    "folders_complete": inventory.folders_complete,
                    "warnings": inventory.warnings,
                }
            )
            if not inventory.folders_complete:
                raise ExternalToolError("正式适配器的目录读取未完成")
            if not directory_only and not inventory.complete:
                gap_warnings = {
                    "收藏接口列表缺失或结构异常",
                    "收藏接口返回空页或缺失列表，清点不完整",
                }
                expected_partial = bool(inventory.warnings) and all(
                    warning in gap_warnings
                    or (warning.startswith("收藏夹「") and warning.endswith("数量不一致"))
                    for warning in inventory.warnings
                )
                if not expected_partial:
                    raise ExternalToolError("正式适配器未完成读取，不能将分页失败记为验收通过")
            if page_size != 20:
                async with async_playwright() as p:
                    context = await p.chromium.launch_persistent_context(
                        **adapter._launch_options(headless=True)
                    )
                    try:
                        page = context.pages[0] if context.pages else await context.new_page()
                        async with FavoritesBrowserClient(page) as client:
                            await page.goto(
                                "https://www.douyin.com/user/self?showTab=favorite_collection"
                                "&showSubTab=favorite_folder",
                                wait_until="domcontentloaded",
                                timeout=60000,
                            )
                            await page.wait_for_timeout(750)
                            await adapter._read_initial_account(page)
                            directory = await _force_pages(
                                client,
                                adapter,
                                inventory.account_id,
                                resource="directory",
                                folder_id=None,
                                count=page_size,
                                events=report["forced_pages"],
                            )
                            if directory != {f.id for f in inventory.folders}:
                                raise ExternalToolError("主动分页的目录 ID 与正式入口不一致")
                            for folder_id in dict.fromkeys(folder_ids or []):
                                ids = await _force_pages(
                                    client,
                                    adapter,
                                    inventory.account_id,
                                    resource="folder",
                                    folder_id=folder_id,
                                    count=page_size,
                                    events=report["forced_pages"],
                                )
                                expected = {
                                    w.work_id for w in inventory.works if folder_id in w.folder_ids
                                }
                                if ids != expected:
                                    raise ExternalToolError("主动分页的作品 ID 与正式入口不一致")
                    finally:
                        await context.close()
            # Partial quantity remains visible; passing checks never certify inventory.complete.
            report["passed"] = True
        except Exception as exc:
            # Exception details from a browser can contain authenticated URLs.
            report["error"] = type(exc).__name__
    report["temporary_profile_removed"] = not profile.exists()
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    result_path = output_dir / f"favorites-api-{uuid4().hex[:12]}.json"
    report["result_path"] = str(result_path)
    result_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    result_path.chmod(0o600)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory-only", action="store_true")
    parser.add_argument("--folder-id", action="append", default=None)
    parser.add_argument("--page-size", type=int, default=20)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = asyncio.run(
            verify_readonly(
                directory_only=args.directory_only,
                folder_ids=args.folder_id,
                page_size=args.page_size,
                output_dir=args.output_dir,
            )
        )
    except (ValueError, ExternalToolError) as exc:
        parser.exit(1, str(exc) + "\n")
    print(
        json.dumps(
            {
                "passed": result["passed"],
                "result_path": result["result_path"],
                "complete": result.get("complete"),
                "error": result.get("error"),
            },
            ensure_ascii=False,
        )
    )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
