from __future__ import annotations

import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml

from ..database import Database
from ..errors import EntryNotFoundError
from ..models import InspirationInput, LibraryItem, SourceKind
from ..tags import TagManager
from ..time_utils import parse_datetime
from ..vault import VaultWriter

CONTENT_TYPE_LABELS = {
    "tutorial": "教程",
    "explanation": "知识解释",
    "opinion": "观点",
    "recommendation": "推荐",
    "news_event": "新闻事件",
    "story_case": "故事案例",
    "collection": "清单",
    "other": "其他",
}


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    marker = text.find("\n---\n", 4)
    if marker < 0:
        return {}, text
    loaded = yaml.safe_load(text[4:marker]) or {}
    return (loaded if isinstance(loaded, dict) else {}), text[marker + 5 :]


def _section(body: str, heading: str) -> str:
    match = re.search(rf"(?ms)^##\s+{re.escape(heading)}\s*$\n(.*?)(?=^##\s+|\Z)", body)
    return match.group(1).strip() if match else ""


def _plain(text: str) -> str:
    text = re.sub(r"!\[[^]]*]\([^)]*\)", "", text)
    text = re.sub(r"\[([^]]+)]\([^)]*\)", r"\1", text)
    text = re.sub(r"[*_`>#-]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _inspirations(value: Any, body: str) -> list[InspirationInput]:
    result: list[InspirationInput] = []
    has_frontmatter_value = isinstance(value, list)
    if isinstance(value, list):
        for item in value:
            try:
                result.append(
                    InspirationInput(text=item)
                    if isinstance(item, str)
                    else InspirationInput.model_validate(item)
                )
            except (TypeError, ValueError):
                continue
    if not result and not has_frontmatter_value:
        section = _section(body, "灵感")
        for line in section.splitlines():
            text = re.sub(r"^\s*[-*]\s+", "", line).strip()
            if text and text not in {"暂无", "无"}:
                result.append(InspirationInput(text=text))
    return result


class LibraryCatalog:
    """Read-only projection of visible source notes in a Vault."""

    def __init__(self, vault_path: Path, database: Database) -> None:
        self.vault_path = vault_path.expanduser().resolve()
        self.database = database
        self.tags = TagManager(database, VaultWriter(self.vault_path))
        self._items: dict[str, LibraryItem] = {}
        self._load_errors: list[dict[str, str]] = []
        self._fingerprint: tuple[tuple[str, int, int], ...] = ()
        self._version = 0
        self._lock = threading.RLock()

    @property
    def version(self) -> int:
        with self._lock:
            return self._version

    @property
    def fingerprint(self) -> tuple[tuple[str, int, int], ...]:
        with self._lock:
            return self._fingerprint

    @property
    def load_errors(self) -> list[dict[str, str]]:
        with self._lock:
            return [dict(error) for error in self._load_errors]

    @property
    def watch_roots(self) -> list[Path]:
        roots = [self.vault_path / "wiki" / "sources"]
        creators = self.vault_path / "creators"
        if creators.is_dir():
            roots.extend(path for path in creators.glob("*/sources") if path.is_dir())
        return [path for path in roots if path.is_dir()]

    def refresh(self) -> list[LibraryItem]:
        db_entries = {entry.video_id: entry for entry in self.database.list_entries()}
        candidates: list[Path] = []
        legacy = self.vault_path / "wiki" / "sources"
        if legacy.is_dir():
            candidates.extend(legacy.glob("*.md"))
        creators = self.vault_path / "creators"
        if creators.is_dir():
            candidates.extend(creators.glob("*/sources/*.md"))

        resolve_tags = self.tags.resolver()
        items: dict[str, LibraryItem] = {}
        load_errors: list[dict[str, str]] = []
        for path in sorted(candidates):
            try:
                item = self._read_item(path, db_entries)
            except Exception as exc:
                load_errors.append(
                    {
                        "path": str(path.relative_to(self.vault_path)),
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                continue
            if item is None:
                continue
            item.tags = resolve_tags(item.tags)
            previous = items.get(item.entry_id)
            preferred = db_entries.get(item.work_id)
            if previous is None or (
                preferred and Path(preferred.source_path) == path.relative_to(self.vault_path)
            ):
                items[item.entry_id] = item
        with self._lock:
            self._items = items
            self._load_errors = load_errors
            self._fingerprint = self.compute_fingerprint()
            self._version += 1
            return list(items.values())

    def compute_fingerprint(self) -> tuple[tuple[str, int, int], ...]:
        paths: list[Path] = []
        legacy = self.vault_path / "wiki" / "sources"
        if legacy.is_dir():
            paths.extend(legacy.glob("*.md"))
        creators = self.vault_path / "creators"
        if creators.is_dir():
            paths.extend(creators.glob("*/sources/*.md"))
        topics = self.vault_path / "topics"
        if topics.is_dir():
            paths.extend(topics.rglob("*.md"))
        values: list[tuple[str, int, int]] = []
        for path in paths:
            try:
                stat = path.stat()
            except OSError:
                continue
            values.append((str(path), stat.st_mtime_ns, stat.st_size))
        return tuple(sorted(values))

    def list_items(self) -> list[LibraryItem]:
        with self._lock:
            return sorted(
                self._items.values(),
                key=lambda item: (
                    item.captured_at or item.published_at or datetime.min.replace(tzinfo=UTC)
                ),
                reverse=True,
            )

    def get(self, entry_id: str) -> LibraryItem | None:
        with self._lock:
            return self._items.get(entry_id)

    @staticmethod
    def search_match(item: LibraryItem, query: str, *, fuzzy: bool = False) -> dict | None:
        """Explain a keyword hit without exposing the full note or inventing evidence times."""
        needle = query.casefold().strip()
        if not needle:
            return None
        body = next(
            (
                part
                for part in re.split(r"\n\s*\n", item.body_markdown)
                if needle in part.casefold()
            ),
            item.body_markdown,
        )
        unheaded = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", body)
        if needle in unheaded.casefold():
            body = unheaded
        fields = [
            ("title", item.title),
            ("author", item.author),
            ("tags", " ".join(item.tags)),
            ("summary", item.summary),
            ("inspiration", " ".join(value.text for value in item.inspirations)),
            ("body", body),
        ]
        for field, value in fields:
            text = re.sub(r"\s+", " ", value).strip()
            folded = text.casefold()
            start = folded.find(needle)
            if start < 0:
                continue
            end = start + len(needle)
            if len(folded) != len(text):
                # Case folding can expand a character (ß -> ss); retain its actual spelling.
                offset = 0
                original_start = 0
                for index, char in enumerate(text):
                    next_offset = offset + len(char.casefold())
                    if offset <= start < next_offset:
                        original_start = index
                    if offset < end <= next_offset:
                        start, end = original_start, index + 1
                        break
                    offset = next_offset
            left = max(0, start - min(16, max(0, (160 - (end - start)) // 2)))
            right = min(len(text), left + 160)
            return {
                "field": field,
                "before": ("…" if left else "") + text[left:start],
                "match": text[start : min(end, right)],
                "after": text[min(end, right) : right] + ("…" if right < len(text) else ""),
                "approximate": False,
            }
        if fuzzy:
            for field, value in fields[:3]:
                letters = iter(value.casefold())
                if all(char in letters for char in needle):
                    return {
                        "field": field,
                        "before": "",
                        "match": "",
                        "after": value[:160] + ("…" if len(value) > 160 else ""),
                        "approximate": True,
                    }
        return None

    def filter(
        self,
        *,
        query: str = "",
        fuzzy: bool = False,
        author: str = "",
        content_type: str = "",
        tag: str = "",
        inspiration_only: bool = False,
    ) -> list[LibraryItem]:
        needle = query.casefold().strip()
        result: list[LibraryItem] = []
        ranks: dict[str, int] = {}

        def subsequence(value: str) -> bool:
            letters = iter(value)
            return all(char in letters for char in needle)

        for item in self.list_items():
            fields = [value.casefold() for value in [item.title, item.author, *item.tags]]
            haystack = "\n".join(
                [
                    item.title,
                    item.author,
                    item.summary,
                    " ".join(item.tags),
                    " ".join(value.text for value in item.inspirations),
                    item.body_markdown,
                ]
            ).casefold()
            if (
                needle
                and needle not in haystack
                and (not fuzzy or not any(subsequence(value) for value in fields))
            ):
                continue
            if needle in fields[0]:
                rank = 0
            elif any(needle in value for value in fields):
                rank = 1
            elif needle in haystack:
                rank = 2
            else:
                rank = 3
            ranks[item.entry_id] = rank
            if author and item.author != author:
                continue
            if content_type and item.content_type != content_type:
                continue
            if tag and tag not in item.tags:
                continue
            if inspiration_only and not item.inspirations:
                continue
            result.append(item)
        if fuzzy and needle:
            result.sort(key=lambda item: ranks[item.entry_id])
        return result

    def _read_item(self, path: Path, db_entries: dict[str, Any]) -> LibraryItem | None:
        text = path.read_text(encoding="utf-8")
        frontmatter, body = split_frontmatter(text)
        if frontmatter.get("type") not in {None, "source"}:
            return None
        work_id = str(frontmatter.get("video_id") or "").strip()
        if not work_id:
            match = re.search(r"(?<!\d)(\d{12,22})(?!\d)", path.stem)
            work_id = match.group(1) if match else ""
        if not work_id:
            if frontmatter.get("type") == "source":
                raise ValueError("source Markdown 缺少 video_id")
            return None
        entry = db_entries.get(work_id)
        entry_id = entry.id if entry else f"dy-{work_id}"
        title = str(frontmatter.get("title") or "").strip()
        if not title:
            match = re.search(r"(?m)^#\s+(.+)$", body)
            title = match.group(1).strip() if match else path.stem
        author = str(frontmatter.get("author") or "未知作者").strip()
        content_type = str(frontmatter.get("content_type") or "other")
        tags_value = frontmatter.get("tags") or []
        tags = [str(value) for value in tags_value] if isinstance(tags_value, list) else []
        cover = str(frontmatter.get("cover_image") or "").strip() or None
        cover_url = self.media_url(cover) if cover and self.safe_media_path(cover) else None
        source_kind_value = str(frontmatter.get("source_kind") or "video")
        try:
            source_kind = SourceKind(source_kind_value)
        except ValueError:
            source_kind = SourceKind.VIDEO
        captured = frontmatter.get("captured") or frontmatter.get("captured_at")
        published = frontmatter.get("published") or frontmatter.get("published_at")
        original_url = str(
            frontmatter.get("original_url")
            or (entry.original_url if entry else "")
            or frontmatter.get("source_url")
            or frontmatter.get("canonical_url")
        )
        return LibraryItem(
            entry_id=entry_id,
            database_managed=entry is not None,
            work_id=work_id,
            title=title,
            author=author,
            cover_path=cover,
            cover_url=cover_url,
            summary=_plain(_section(body, "一句话").split("\n\n", 1)[0])
            or (entry.summary if entry else ""),
            content_type=content_type,
            tags=tags,
            inspirations=_inspirations(frontmatter.get("inspirations"), body),
            published_at=parse_datetime(published),
            captured_at=parse_datetime(captured),
            status=str(frontmatter.get("status") or "已入库"),
            favorite=entry.favorite if entry else bool(frontmatter.get("favorite", False)),
            media_status=entry.media_status if entry else "present",
            retention=(entry.retention if entry else "temporary"),
            source_kind=source_kind,
            source_path=str(path.relative_to(self.vault_path)),
            original_url=original_url,
            creator_id=str(frontmatter.get("creator_id") or "") or None,
            body_markdown=body,
        )

    def safe_media_path(self, relative: str) -> Path | None:
        if not relative or relative.startswith(("http://", "https://", "/")):
            return None
        try:
            target = (self.vault_path / relative).resolve()
            target.relative_to(self.vault_path)
        except (OSError, ValueError, RuntimeError):
            return None
        if target.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif"}:
            return None
        return target if target.is_file() else None

    def article_materials(self, item: LibraryItem) -> dict[str, Any]:
        result: dict[str, Any] = {
            "transcript": None,
            "local_video_url": None,
            "keyframes": None,
            "keep_keyframes": False,
        }
        if not item.database_managed or item.source_kind != SourceKind.VIDEO:
            return result
        try:
            data = self.database.get_entry_data(item.entry_id)
        except EntryNotFoundError:
            return result
        corrected = data.get("transcript_corrected")
        result["keyframes"] = self.article_keyframes(item, data)
        entry = self.database.get_entry(item.entry_id)
        policy = data.get("media_policy")
        result["keep_keyframes"] = entry.retention == "keep" or (
            entry.retention == "temporary"
            and isinstance(policy, dict)
            and policy.get("keep_keyframes") is True
        )
        rows = corrected or data.get("transcript_raw") or []
        result["transcript"] = {
            "source": "corrected" if corrected else "raw",
            "segments": [
                {key: row[key] for key in ("id", "start_ms", "end_ms", "text")}
                | (
                    {
                        key: row.get(key)
                        for key in (
                            "timestamp_precision",
                            "timestamp_start_ms",
                            "timestamp_end_ms",
                            "alignment_reason",
                        )
                    }
                    if row.get("timestamp_precision") == "window"
                    else {}
                )
                for row in rows
            ],
        }
        if self.local_video_path(item, data=data) is not None:
            result["local_video_url"] = f"/api/articles/{quote(item.entry_id, safe='')}/video"
        return result

    def article_keyframes(self, item: LibraryItem, data: dict[str, Any]) -> list[dict[str, Any]]:
        entry = self.database.get_entry(item.entry_id)
        raw_parent = (self.vault_path / entry.raw_path).parent
        raw_root = raw_parent.parent if raw_parent.name == "records" else raw_parent
        root = raw_root / "assets" / entry.video_id / "frames"
        try:
            root.resolve().relative_to(self.vault_path.resolve())
            if root.resolve() != root.absolute():
                return []
        except (ValueError, OSError, RuntimeError):
            return []
        frames = data.get("frames", [])
        if not isinstance(frames, list):
            frames = []
        if not frames and data.get("frame_manifest_version", 0) != 1 and root.is_dir():
            frames = []
            for path in sorted(root.glob("frame-*")):
                match = re.fullmatch(r"frame-(\d{10,})\.[^.]+", path.name)
                frames.append(
                    {
                        "path": path.relative_to(self.vault_path).as_posix(),
                        "timestamp_ms": int(match[1]) if match else None,
                    }
                )
        result, seen = [], set()
        for frame in frames:
            if not isinstance(frame, dict) or not isinstance(frame.get("path"), str):
                continue
            path = self.safe_media_path(frame["path"])
            if path is None or path in seen:
                continue
            try:
                path.relative_to(root)
            except ValueError:
                continue
            seen.add(path)
            timestamp = frame.get("timestamp_ms")
            if not isinstance(timestamp, int) or isinstance(timestamp, bool) or timestamp < 0:
                timestamp = None
            result.append({"url": self.media_url(frame["path"]), "timestamp_ms": timestamp})
        return sorted(
            result,
            key=lambda frame: (
                frame["timestamp_ms"] is None,
                frame["timestamp_ms"] or 0,
                frame["url"],
            ),
        )

    def local_video_path(
        self, item: LibraryItem, *, data: dict[str, Any] | None = None
    ) -> Path | None:
        if not item.database_managed or item.source_kind != SourceKind.VIDEO:
            return None
        try:
            entry = self.database.get_entry(item.entry_id)
            saved = data if data is not None else self.database.get_entry_data(item.entry_id)
            metadata = saved.get("metadata") or {}
            media_path = metadata.get("media_path")
            if (
                entry.media_status != "present"
                or metadata.get("source_kind") == SourceKind.IMAGE_NOTE.value
                or not media_path
            ):
                return None
            target = Path(media_path)
            if not target.is_absolute():
                target = self.vault_path / target
            target = target.resolve()
            target.relative_to(self.vault_path)
            raw_parent = (self.vault_path / entry.raw_path).parent
            raw_root = raw_parent.parent if raw_parent.name == "records" else raw_parent
            assets = (raw_root / "assets" / entry.video_id).resolve()
            assets.relative_to(self.vault_path)
            target.relative_to(assets)
        except (EntryNotFoundError, OSError, RuntimeError, ValueError):
            return None
        if target.suffix.lower() not in {".mp4", ".m4v", ".mov", ".webm", ".mkv"}:
            return None
        return target if target.is_file() else None

    @staticmethod
    def media_url(relative: str) -> str:
        return "/media/" + quote(relative.replace("\\", "/"), safe="/")
