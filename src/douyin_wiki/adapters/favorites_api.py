"""Decode original favorite responses before JavaScript can round platform IDs."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

from ..errors import BrowserAuthRequiredError, ExternalToolError
from ..favorites_models import FavoriteFolder, FavoriteWork

Resource = Literal["directory", "folder"]
_ID = re.compile(r"[0-9]{1,32}\Z")


@dataclass(frozen=True)
class RawFavoritePage:
    resource: Resource
    folder_id: str | None
    request_cursor: str
    request_count: int
    http_status: int
    body: bytes


@dataclass(frozen=True)
class FavoriteApiPage:
    folders: list[FavoriteFolder]
    works: list[FavoriteWork]
    next_cursor: str | None
    has_more: bool | None
    reported_count: int | None
    valid: bool
    warnings: list[str]


def stable_id(value: Any) -> str | None:
    if type(value) not in {str, int}:
        return None
    text = str(value)
    return text if _ID.fullmatch(text) else None


def _record_id(record: dict[str, Any], key: str) -> str | None:
    values = [record[k] for k in (key + "_str", key) if record.get(k) is not None]
    ids = [stable_id(value) for value in values]
    if not ids or any(value is None for value in ids) or len(set(ids)) != 1:
        return None
    return ids[0]


def _count(value: Any) -> int | None:
    if type(value) is int and value >= 0:
        return value
    if type(value) is str and value.isascii() and value.isdigit():
        return int(value)
    return None


def _text(value: Any, default: str = "") -> str:
    return value.strip() if isinstance(value, str) and value.strip() else default


def _work(record: dict[str, Any], folder_id: str | None) -> tuple[FavoriteWork | None, str | None]:
    work_id = _record_id(record, "aweme_id")
    if work_id is None:
        return None, "有作品缺少可信的稳定 ID，未加入清单"
    route, kind, warning = "video", "video", None
    try:
        share = urlsplit(_text(record.get("share_url")))
        article = (
            share.hostname in {"www.douyin.com", "douyin.com"}
            and share.scheme in {"https", "http"}
            and not share.port
            and not share.username
            and not share.password
            and share.path.rstrip("/") == f"/article/{work_id}"
        )
    except ValueError:
        article = False
    image_info = record.get("image_post_info")
    images = record.get("images")
    if article:
        route, kind = "article", "article"
    elif (isinstance(images, list) and bool(images)) or (
        isinstance(image_info, dict)
        and isinstance(image_info.get("images"), list)
        and bool(image_info["images"])
    ):
        route, kind = "note", "image_note"
    elif not isinstance(record.get("video"), dict):
        warning = f"作品 {work_id} 类型无法确认，未纳入导入选择"
    author = record.get("author")
    status = record.get("status")
    deleted = isinstance(status, dict) and status.get("is_delete") in (True, 1)
    return FavoriteWork(
        work_id=work_id,
        canonical_url=f"https://www.douyin.com/{route}/{work_id}",
        title=_text(record.get("desc"), "抖音作品"),
        author=_text(author.get("nickname")) if isinstance(author, dict) else "",
        source_kind=kind,
        folder_ids=[folder_id] if folder_id else [],
        available=warning is None and not deleted,
    ), warning


def parse_favorite_page(response: RawFavoritePage) -> FavoriteApiPage:
    if response.http_status != 200:
        raise ExternalToolError(f"收藏接口 HTTP 状态异常：{response.http_status}")
    try:
        data = json.loads(response.body)
    except (ValueError, UnicodeError) as exc:
        raise ExternalToolError("收藏接口未返回有效 JSON") from exc
    if not isinstance(data, dict):
        raise ExternalToolError("收藏接口响应结构异常")
    status = data.get("status_code")
    message = _text(data.get("status_msg"))
    # Code 8 is generic; only a known login code or an explicit message denies auth.
    if (type(status) is int and status == 2483) or (
        status != 0
        and re.search(
            r"请.*登录|未登录|登录.*(?:失效|过期)|login\s+(?:required|expired)|not\s+logged\s+in",
            message,
            re.I,
        )
    ):
        raise BrowserAuthRequiredError("收藏接口要求重新登录抖音")
    warnings: list[str] = []
    if type(status) is not int or status != 0:
        warnings.append("收藏接口没有明确返回成功状态")
    more = data.get("has_more")
    has_more = None
    if type(more) is bool:
        has_more = more
    elif type(more) is int and more in (0, 1):
        has_more = bool(more)
    elif type(more) is str and more in ("0", "1"):
        has_more = more == "1"
    if has_more is None:
        warnings.append("收藏接口缺少可信的分页结束信号")
    cursor = data.get("cursor")
    if cursor is None:
        cursor = data.get("max_cursor")
    next_cursor = str(cursor) if type(cursor) in {str, int} and str(cursor) else None
    if type(status) is not int or status != 0:
        has_more, next_cursor = None, None
    if has_more and next_cursor is None:
        warnings.append("收藏接口缺少下一页 cursor")
    count = _count(data.get("total_number"))
    if data.get("total_number") is not None and count is None:
        warnings.append("收藏接口总数不是可信的非负整数")
    rows = data.get("collects_list" if response.resource == "directory" else "aweme_list")
    if not isinstance(rows, list):
        warnings.append("收藏接口列表缺失或结构异常")
        rows = []
    folders, works = [], []
    # A rejected request must not publish records as if they were a successful page.
    for row in rows if type(status) is int and status == 0 else []:
        if not isinstance(row, dict):
            warnings.append("收藏接口存在结构异常的记录")
            continue
        if response.resource == "directory":
            info = row.get("collects_info", row)
            folder_id = _record_id(info, "collects_id") if isinstance(info, dict) else None
            if folder_id is None:
                warnings.append("有收藏夹缺少可信的稳定 ID，未加入目录")
                continue
            reported = _count(info.get("total_number"))
            if info.get("total_number") is not None and reported is None:
                warnings.append(f"收藏夹 {folder_id} 的数量不是可信整数")
            folders.append(
                FavoriteFolder(
                    id=folder_id,
                    name=_text(info.get("collects_name"), "未命名收藏夹"),
                    reported_count=reported,
                )
            )
        else:
            work, warning = _work(row, response.folder_id)
            if work is not None:
                works.append(work)
            if warning:
                warnings.append(warning)
    return FavoriteApiPage(
        folders, works, next_cursor, has_more, count, not warnings, list(dict.fromkeys(warnings))
    )
