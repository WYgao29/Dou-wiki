"""Decode collection responses without JavaScript integer conversion."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlsplit

from ..collection_models import CollectionProfile, CollectionWork, stable_id
from ..errors import BrowserAuthRequiredError, ExternalToolError

Resource = Literal["detail", "list"]


@dataclass(frozen=True)
class RawCollectionPage:
    resource: Resource
    mix_id: str
    request_cursor: str
    request_count: int
    http_status: int
    body: bytes


@dataclass(frozen=True)
class CollectionPage:
    profile: CollectionProfile | None
    works: list[CollectionWork]
    next_cursor: str | None
    has_more: bool | None
    valid: bool
    warnings: list[str]


def _id(record, key):
    ids = [stable_id(record[k]) for k in (key, key + "_str") if record.get(k) is not None]
    return ids[0] if ids and None not in ids and len(set(ids)) == 1 else None


def _text(value, default=""):
    return value.strip() if isinstance(value, str) and value.strip() else default


def _count(value):
    if type(value) is int and value >= 0:
        return value
    if type(value) is str and value.isascii() and value.isdigit():
        return int(value)
    return None


def _url(value):
    values = value.get("url_list", []) if isinstance(value, dict) else []
    return next((v for v in values if isinstance(v, str) and v.startswith("https://")), None)


def _work(record, mix_id, position, episodes):
    work_id = _id(record, "aweme_id")
    if not work_id:
        return None, "有作品缺少可信的稳定 ID"
    info = record.get("mix_info")
    if (
        isinstance(info, dict)
        and ("mix_id" in info or "mix_id_str" in info)
        and _id(info, "mix_id") != mix_id
    ):
        return None, f"作品 {work_id} 的合集归属不一致"
    video = record.get("video")
    images = record.get("images")
    image_info = record.get("image_post_info")
    image_list = image_info.get("images") if isinstance(image_info, dict) else None
    share = urlsplit(_text(record.get("share_url")))
    kind = "unknown"
    if share.hostname in {"www.douyin.com", "douyin.com"} and share.path == f"/article/{work_id}":
        kind = "article"
    elif (isinstance(images, list) and images) or (isinstance(image_list, list) and image_list):
        kind = "image_note"
    elif isinstance(video, dict) and video:
        kind = "video"
    warning = f"作品 {work_id} 类型无法确认" if kind == "unknown" else None
    author = record.get("author")
    status = record.get("status")
    deleted = isinstance(status, dict) and status.get("is_delete") in (True, 1)
    duration = _count(video.get("duration")) if isinstance(video, dict) else None
    published = _count(record.get("create_time"))
    try:
        published_at = datetime.fromtimestamp(published, UTC) if published is not None else None
    except (ValueError, OverflowError, OSError):
        published_at = None
    episode = episodes.get(work_id)
    if episode is None and isinstance(info, dict) and isinstance(info.get("statis"), dict):
        episode = info["statis"].get("current_episode")
    episode = _count(episode)
    return CollectionWork(
        work_id=work_id,
        title=_text(record.get("desc"), "抖音作品"),
        author=_text(author.get("nickname")) if isinstance(author, dict) else "",
        source_kind=kind,
        position=position,
        episode_label=str(episode) if episode else None,
        duration_seconds=duration / 1000 if duration is not None else None,
        published_at=published_at,
        thumbnail_url=_url(video.get("cover")) if isinstance(video, dict) else None,
        available=not deleted and kind != "unknown",
    ), warning


def parse_collection_page(raw: RawCollectionPage) -> CollectionPage:
    if raw.http_status != 200:
        raise ExternalToolError(f"合集接口 HTTP 状态异常：{raw.http_status}")
    try:
        data = json.loads(raw.body)
    except (ValueError, UnicodeError) as exc:
        raise ExternalToolError("合集接口未返回有效 JSON") from exc
    if not isinstance(data, dict):
        raise ExternalToolError("合集接口响应结构异常")
    code = data.get("status_code")
    message = _text(data.get("status_msg"))
    if (type(code) is int and code == 2483) or re.search(
        r"请.*登录|未登录|登录.*(?:失效|过期)|login\s+(?:required|expired)", message, re.I
    ):
        raise BrowserAuthRequiredError("合集接口要求重新登录抖音")
    if type(code) is not int or code != 0:
        raise ExternalToolError("合集接口没有明确返回成功状态")
    if raw.resource == "detail":
        info = data.get("mix_info")
        if not isinstance(info, dict) or _id(info, "mix_id") != raw.mix_id:
            raise ExternalToolError("合集详情与目标编号不一致")
        author = info.get("author")
        statis = info.get("statis")
        profile = CollectionProfile(
            mix_id=raw.mix_id,
            name=_text(info.get("mix_name"), "抖音合集"),
            author=_text(author.get("nickname")) if isinstance(author, dict) else "",
            author_sec_uid=_text(author.get("sec_uid")) or None
            if isinstance(author, dict)
            else None,
            thumbnail_url=_url(info.get("cover_url")),
            updated_to_episode=_count(statis.get("updated_to_episode"))
            if isinstance(statis, dict)
            else None,
        )
        return CollectionPage(profile, [], None, None, True, [])
    if raw.resource != "list":
        raise ValueError("Unknown collection resource")
    warnings = []
    more = data.get("has_more")
    has_more = (
        more
        if type(more) is bool
        else (bool(int(more)) if type(more) in {str, int} and more in (0, 1, "0", "1") else None)
    )
    if has_more is None:
        warnings.append("合集接口缺少可信的分页结束信号")
    cursor = data.get("cursor", data.get("max_cursor"))
    next_cursor = stable_id(cursor)
    if has_more and next_cursor is None:
        warnings.append("合集接口缺少下一页 cursor")
    rows = data.get("aweme_list")
    if not isinstance(rows, list):
        warnings.append("合集作品数组缺失或结构异常")
        rows = []
    if not rows and has_more:
        warnings.append("合集接口返回异常空页")
    episodes = data.get("item_id_to_episode", {})
    if isinstance(episodes, str):
        try:
            episodes = json.loads(episodes)
        except ValueError:
            episodes = {}
            warnings.append("合集集数映射不是有效 JSON")
    if not isinstance(episodes, dict):
        episodes = {}
        warnings.append("合集集数映射结构异常")
    works = []
    for row in rows:
        if not isinstance(row, dict):
            warnings.append("合集存在结构异常的作品记录")
            continue
        work, warning = _work(row, raw.mix_id, len(works) + 1, episodes)
        if work is not None:
            works.append(work)
        if warning:
            warnings.append(warning)
    return CollectionPage(
        None, works, next_cursor, has_more, not warnings, list(dict.fromkeys(warnings))
    )
