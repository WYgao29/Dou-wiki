"""Tag vocabulary, aliases and metadata mutations across Vault and SQLite."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import unicodedata
import uuid
from collections import defaultdict
from contextlib import closing
from datetime import UTC
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml

from .database import Database, lexical_document
from .time_utils import iso_now
from .vault import VaultWriter

PIN_LIMIT = 8

TAG_SCHEMA = """
CREATE TABLE IF NOT EXISTS tags (
    name TEXT PRIMARY KEY,
    pinned_at TEXT,
    position INTEGER NOT NULL DEFAULT 0,
    last_used_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tag_aliases (
    alias TEXT PRIMARY KEY,
    canonical TEXT NOT NULL REFERENCES tags(name) ON UPDATE CASCADE ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS tag_undo (
    id TEXT PRIMARY KEY,
    snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    undone_at TEXT
);
CREATE TABLE IF NOT EXISTS tag_undo_aliases (
    operation_id TEXT NOT NULL REFERENCES tag_undo(id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    canonical TEXT NOT NULL,
    PRIMARY KEY(operation_id, alias)
);
"""


class TagUndoConflict(ValueError):
    pass


def tag_key(name: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", name).casefold())


def valid_name(name: str) -> str:
    name = name.strip()
    if not name or len(name) > 100 or any(ord(char) < 32 for char in name):
        raise ValueError("标签名须为 1–100 个字符，不能包含换行或控制字符")
    return name


def replace_frontmatter_tags(frontmatter: str, tags: list[str]) -> str:
    """Replace only the top-level tags value, preserving other YAML verbatim."""
    replacement = json.dumps(tags, ensure_ascii=False)
    root = yaml.compose(frontmatter, Loader=yaml.SafeLoader)
    original = yaml.safe_load(frontmatter)

    def validated(result: str) -> str:
        try:
            parsed = yaml.safe_load(result)
        except yaml.YAMLError as exc:
            raise ValueError("标签更新无法保留合法 YAML，已停止修改") from exc
        if parsed.get("tags") != tags or {
            key: value for key, value in parsed.items() if key != "tags"
        } != {key: value for key, value in original.items() if key != "tags"}:
            raise ValueError("标签更新会改变其他 YAML 属性，已停止修改")
        return result

    keys = [key for key, _ in root.value if key.value == "tags"]
    if not keys:
        # Tags can be inherited through a YAML merge; make the override explicit.
        return validated(frontmatter.rstrip("\n") + "\ntags: " + replacement)
    key = keys[-1]
    events = list(yaml.parse(frontmatter, Loader=yaml.SafeLoader))
    index = (
        next(
            i
            for i, event in enumerate(events)
            if isinstance(event, yaml.ScalarEvent)
            and event.start_mark.index == key.start_mark.index
        )
        + 1
    )
    value = events[index]
    start, end = value.start_mark.index, value.end_mark.index
    if isinstance(value, yaml.SequenceStartEvent):
        if value.anchor:
            replacement = f"&{value.anchor} " + replacement
        if (
            not value.flow_style
            and value.start_mark.line > key.end_mark.line
            and value.start_mark.column <= key.start_mark.column
        ):
            # YAML permits an unindented block list; a flow list needs indentation.
            replacement = " " * (key.start_mark.column + 2 - value.start_mark.column) + replacement
        depth = 0
        for event in events[index:]:
            if isinstance(event, (yaml.SequenceStartEvent, yaml.MappingStartEvent)):
                depth += 1
            elif isinstance(event, (yaml.SequenceEndEvent, yaml.MappingEndEvent)):
                depth -= 1
                if depth == 0:
                    if value.flow_style:
                        end = event.end_mark.index
                    break
            end = event.end_mark.index
    elif not isinstance(value, yaml.AliasEvent):
        raise ValueError("tags 必须是 YAML 列表")
    if frontmatter[start:end].endswith("\n") and frontmatter[end:] and frontmatter[end] != "\n":
        replacement += "\n"

    # Replacing a list can remove anchors that other properties still reference.
    # Inline those aliases with their original values before removing the definitions.
    nodes = {}
    visited = set()

    def collect(node):
        if id(node) in visited:
            return
        visited.add(id(node))
        nodes[node.start_mark.index] = node
        if isinstance(node, yaml.SequenceNode):
            for child in node.value:
                collect(child)
        elif isinstance(node, yaml.MappingNode):
            for key_node, value_node in node.value:
                collect(key_node)
                collect(value_node)

    collect(root)
    anchors = {
        event.anchor: nodes[event.start_mark.index]
        for event in events
        if getattr(event, "anchor", None)
        and not isinstance(event, yaml.AliasEvent)
        and start <= event.start_mark.index < end
    }
    changes = [(start, end, replacement)]
    loader = yaml.SafeLoader("")
    try:
        for event in events:
            if (
                isinstance(event, yaml.AliasEvent)
                and event.anchor in anchors
                and not start <= event.start_mark.index < end
            ):
                value = loader.construct_object(anchors[event.anchor], deep=True)
                text = yaml.safe_dump(
                    value, allow_unicode=True, default_flow_style=True, width=1_000_000
                )
                if text.endswith("\n...\n"):
                    text = text[:-5]
                text = text.rstrip("\n").replace("\n", "\n" + " " * event.start_mark.column)
                changes.append((event.start_mark.index, event.end_mark.index, text))
    finally:
        loader.dispose()
    result = frontmatter
    for begin, finish, text in sorted(changes, reverse=True):
        result = result[:begin] + text + result[finish:]
    return validated(result)


DUPLICATE_GROUP_LIMIT = 5
_UNDOABLE_CACHE: dict[tuple[str, str], bool] = {}
_UNDOABLE_CACHE_LIMIT = 256
_UNDOABLE_CACHE_LOCK = threading.Lock()
_UNDO_CONFLICT = "操作后资料或标签已变化，无法撤销；后续修改已保留"
_UNDO_NAME_CONFLICT = "后续标签与待恢复的标准标签或别名重名，无法撤销；后续关联已保留"
_UNDO_YAML_CONFLICT = "资料格式已变化，无法安全撤销；请先修复 Markdown"
_UNDO_PIN_CONFLICT = "固定标签已满，无法恢复原固定状态；请先取消一个固定标签"


def _pair_strength(left_key: str, right_key: str, translations: dict[str, str]) -> int:
    """3 exact key, 2 shared translation, 1 long-enough substring. 0 is not a match."""
    if left_key == right_key:
        return 3
    if translations.get(left_key, left_key) == translations.get(right_key, right_key):
        return 2
    shorter, longer = (
        (left_key, right_key) if len(left_key) <= len(right_key) else (right_key, left_key)
    )
    if len(shorter) >= 4 and shorter in longer and len(shorter) / len(longer) >= 0.75:
        return 1
    return 0


def duplicate_groups(rows: list[dict[str, Any]]) -> list[list[str]]:
    translations = {
        "aiagent": "ai代理",
        "aiagents": "ai代理",
        "agent": "ai代理",
        "人工智能": "ai",
        "大语言模型": "llm",
        "机器学习": "machinelearning",
        "深度学习": "deeplearning",
        "检索增强生成": "rag",
        "retrievalaugmentedgeneration": "rag",
        "artificialintelligence": "ai",
        "largelanguagemodel": "llm",
        "largelanguagemodels": "llm",
        "智能体": "ai代理",
        "ai智能体": "ai代理",
        "知识管理": "knowledgemanagement",
        "提示词": "prompt",
        "提示工程": "promptengineering",
        "向量数据库": "vectordatabase",
    }
    names = [row["name"] for row in rows]
    keys = {name: tag_key(name) for name in names}
    similar: dict[str, set[str]] = {name: set() for name in names}
    for index, left in enumerate(names):
        for right in names[index + 1 :]:
            if _pair_strength(keys[left], keys[right], translations):
                similar[left].add(right)
                similar[right].add(left)
    # Every member must be similar to the seed and to every name already chosen.
    # Indirect chains (A~B~C while A and C differ) stay in separate groups.
    remaining = set(names)
    groups: list[list[str]] = []
    for seed in names:
        if seed not in remaining or not (similar[seed] & remaining):
            continue
        group = [seed]
        for name in names:
            if name == seed or name not in remaining or name not in similar[seed]:
                continue
            if len(group) >= DUPLICATE_GROUP_LIMIT:
                break
            if all(name in similar[member] for member in group):
                group.append(name)
        if len(group) > 1:
            groups.append(group)
            remaining.difference_update(group)
    return groups


def _read_only_connection(path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(path.resolve().as_posix())}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _undo_stamp(vault: VaultWriter, database_path: Path) -> str:
    """Fingerprint the files and database pages that can make an undo stale.

    A new connection's ``PRAGMA data_version`` does not move when another
    connection commits, so it cannot invalidate this cache. Database, WAL and
    source-file stamps do. Rewriting ``last_used_at`` to the same value does
    not grow the WAL, so a quiet tags page can reuse the previous answer.
    """
    parts: list[str] = []
    for candidate in (
        database_path,
        Path(f"{database_path}-wal"),
        Path(f"{database_path}-shm"),
    ):
        try:
            stat = candidate.stat()
        except OSError:
            parts.append(f"{candidate.name}:missing")
        else:
            parts.append(f"{candidate.name}:{stat.st_mtime_ns}:{stat.st_size}")
    root = vault.vault_path
    sources = sorted((root / "wiki" / "sources").glob("*.md"))
    sources.extend(sorted((root / "creators").glob("*/sources/*.md")))
    for path in sources:
        relative = path.relative_to(root).as_posix()
        try:
            stat = path.stat()
        except OSError:
            parts.append(f"{relative}:missing")
        else:
            parts.append(f"{relative}:{stat.st_mtime_ns}:{stat.st_size}")
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def _undo_conflict(conn, snapshot: dict[str, Any], vault: VaultWriter) -> str | None:
    """Return why this snapshot cannot be undone.

    ``vault`` is required because the checks read Markdown and reject symlinks.
    Pin capacity is simulated from the rows undo would write, before any restore.
    A truthy ``before`` restores that row, ``None`` deletes it, and an empty
    mapping leaves the row unchanged.
    """
    for relative, values in snapshot["files"].items():
        path = vault.vault_path / relative
        vault._reject_vault_symlinks(path, field="tag undo")
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current != values["after"]:
            return _UNDO_CONFLICT
    for entry_id, values in snapshot["entries"].items():
        current = conn.execute(
            "SELECT tags_json, data_json FROM entries WHERE id=?", (entry_id,)
        ).fetchone()
        chunks = {
            row["id"] for row in conn.execute("SELECT id FROM chunks WHERE entry_id=?", (entry_id,))
        }
        if (
            current is None
            or dict(current) != values["after"]
            or chunks != {row["id"] for row in values["chunks"]}
        ):
            return _UNDO_CONFLICT
    fields = ("name", "pinned_at", "position", "created_at")
    for name, values in snapshot["tags"].items():
        row = conn.execute("SELECT * FROM tags WHERE name=?", (name,)).fetchone()
        current = dict(row) if row else None
        expected = values["after"]
        if (current is None) != (expected is None) or (
            current and any(current[key] != expected[key] for key in fields)
        ):
            return _UNDO_CONFLICT
    for name, values in snapshot["aliases"].items():
        row = conn.execute("SELECT canonical FROM tag_aliases WHERE alias=?", (name,)).fetchone()
        if (row[0] if row else None) != values["after"]:
            return _UNDO_CONFLICT
    standards = {row[0] for row in conn.execute("SELECT name FROM tags")}
    alias_map = dict(conn.execute("SELECT alias, canonical FROM tag_aliases"))
    restored_names: set[str] = set()
    for name, values in snapshot["tags"].items():
        if values["before"]:
            standards.add(name)
            restored_names.add(name)
        else:
            standards.discard(name)
    for name, values in snapshot["aliases"].items():
        if values["before"]:
            alias_map[name] = values["before"]
            restored_names.add(name)
        else:
            alias_map.pop(name, None)
    if restored_names.intersection(standards, alias_map):
        return _UNDO_NAME_CONFLICT
    created = {name for name, values in snapshot["tags"].items() if values["before"] is None}
    if created:
        changed_paths = set(snapshot["files"])
        sources = list((vault.vault_path / "wiki/sources").glob("*.md"))
        sources += list((vault.vault_path / "creators").glob("*/sources/*.md"))
        for path in sources:
            vault._reject_vault_symlinks(path, field="tag undo source")
            if str(path.relative_to(vault.vault_path)) in changed_paths:
                continue
            text = path.read_text(encoding="utf-8")
            match = re.match(r"\A---\n(.*?)\n---(?:\n|$)", text, re.S)
            try:
                metadata = yaml.safe_load(match[1]) if match else None
            except yaml.YAMLError:
                return _UNDO_YAML_CONFLICT
            if (
                isinstance(metadata, dict)
                and metadata.get("type") in {None, "source"}
                and isinstance(metadata.get("tags"), list)
                and created.intersection(metadata["tags"])
            ):
                return _UNDO_CONFLICT
        for row in conn.execute("SELECT alias, canonical FROM tag_aliases"):
            if row["canonical"] in created and row["alias"] not in snapshot["aliases"]:
                return _UNDO_CONFLICT
        for row in conn.execute("SELECT id, tags_json FROM entries"):
            if row["id"] not in snapshot["entries"] and created.intersection(
                json.loads(row["tags_json"])
            ):
                return _UNDO_CONFLICT
    pins = {
        row["name"]: row["pinned_at"] for row in conn.execute("SELECT name, pinned_at FROM tags")
    }
    for name, values in snapshot["tags"].items():
        before = values["before"]
        if before:
            pins[name] = before["pinned_at"]
        elif before is None:
            pins.pop(name, None)
    if sum(1 for value in pins.values() if value) > PIN_LIMIT:
        return _UNDO_PIN_CONFLICT
    return None


def snapshot_still_undoable(vault: VaultWriter, conn, snapshot: dict[str, Any]) -> bool:
    """Read-only mirror of undo()'s conflict checks. False means a click would 409."""
    try:
        return _undo_conflict(conn, snapshot, vault) is None
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return False


class TagManager:
    def __init__(self, database: Database, vault: VaultWriter) -> None:
        self.database, self.vault = database, vault

    @staticmethod
    def _resolve(conn, values: list[str], vocabulary=None) -> list[str]:
        if vocabulary is None:
            aliases = dict(conn.execute("SELECT alias, canonical FROM tag_aliases").fetchall())
            standards = {row[0] for row in conn.execute("SELECT name FROM tags")}
        else:
            aliases, standards = vocabulary
        result = []
        for value in values:
            value = str(value).strip()
            if not value:
                continue
            canonical = value if value in standards else aliases.get(value)
            if canonical is None:
                matches = {
                    target for alias, target in aliases.items() if tag_key(alias) == tag_key(value)
                }
                canonical = matches.pop() if len(matches) == 1 else value
            if canonical not in result:
                result.append(canonical)
        return result

    def resolver(self):
        with self.database.connect() as conn:
            aliases = dict(conn.execute("SELECT alias, canonical FROM tag_aliases").fetchall())
            standards = {row[0] for row in conn.execute("SELECT name FROM tags")}
        return lambda values: self._resolve(None, values, (aliases, standards))

    def resolve(self, values: list[str]) -> list[str]:
        with self.database.connect() as conn:
            return self._resolve(conn, values)

    def standard_names(self) -> list[str]:
        with self.database.connect() as conn:
            names = [row[0] for row in conn.execute("SELECT name FROM tags")]
            for row in conn.execute("SELECT tags_json FROM entries WHERE status='active'"):
                names.extend(json.loads(row[0]))
            return sorted(self._resolve(conn, names), key=str.casefold)

    def overview(
        self, items, *, persist: bool = True, include_undoable: bool = False
    ) -> dict[str, Any]:
        now = iso_now()
        counts: dict[str, list] = defaultdict(list)
        resolve = self.resolver()
        with self.database.connect() as conn:
            for item in items:
                for name in resolve(item.tags):
                    counts[name].append(item)
            for name in counts:
                if not persist:
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO tags(name, created_at) VALUES (?, ?)", (name, now)
                )
                last = max(
                    (
                        (item.captured_at or item.published_at).astimezone(UTC).isoformat()
                        for item in counts[name]
                        if item.captured_at or item.published_at
                    ),
                    default=None,
                )
                if last:
                    conn.execute(
                        "UPDATE tags SET last_used_at=MAX(COALESCE(last_used_at, ''), ?) "
                        "WHERE name=?",
                        (last, name),
                    )
            records = [dict(row) for row in conn.execute("SELECT * FROM tags")]
            known = {row["name"] for row in records}
            records.extend(
                {"name": name, "pinned_at": None, "position": 0, "last_used_at": None}
                for name in counts
                if name not in known
            )
            aliases = dict(conn.execute("SELECT alias, canonical FROM tag_aliases").fetchall())
            undo_by_alias = {}
            for row in conn.execute(
                "SELECT a.alias, a.operation_id FROM tag_undo_aliases a "
                "JOIN tag_undo u ON u.id=a.operation_id "
                "JOIN tag_aliases t ON t.alias=a.alias AND t.canonical=a.canonical "
                "WHERE u.undone_at IS NULL ORDER BY u.created_at DESC"
            ):
                undo_by_alias.setdefault(row["alias"], row["operation_id"])

        undoable_by_alias: dict[str, str] = {}
        if include_undoable and undo_by_alias:
            undoable_by_alias = self._undoable_aliases(undo_by_alias)

        rows = []
        for record in records:
            name = record["name"]
            recent = sorted(
                counts[name],
                key=lambda item: (
                    (item.captured_at or item.published_at).astimezone(UTC).isoformat()
                    if (item.captured_at or item.published_at)
                    else ""
                ),
                reverse=True,
            )
            last = max(
                filter(
                    None,
                    [
                        record["last_used_at"],
                        *[
                            (item.captured_at or item.published_at).astimezone(UTC).isoformat()
                            for item in recent
                            if item.captured_at or item.published_at
                        ],
                    ],
                ),
                default=None,
            )
            rows.append(
                {
                    "name": name,
                    "count": len(recent),
                    "last_used_at": last,
                    "pinned_at": record["pinned_at"],
                    "position": record["position"],
                    "recent": [
                        {"entry_id": item.entry_id, "title": item.title} for item in recent[:5]
                    ],
                    "entry_ids": [item.entry_id for item in recent],
                }
            )
        rows.sort(key=lambda row: (-row["count"], row["name"].casefold(), row["name"]))
        pinned = [row for row in rows if row["pinned_at"]]
        pinned.sort(key=lambda row: (row["position"], row["pinned_at"], row["name"]))
        sidebar = [row for row in (pinned if pinned else rows) if row["count"] > 0][:8]
        entry_ids = {name: {item.entry_id for item in counts[name]} for name in counts}
        duplicates = []
        for names in duplicate_groups(rows):
            affected: set[str] = set()
            for name in names:
                affected.update(entry_ids.get(name, ()))
            duplicates.append({"names": names, "article_count": len(affected)})
        return {
            "tags": rows,
            "sidebar": sidebar,
            "duplicates": duplicates,
            "aliases": aliases,
            "undo_by_alias": undo_by_alias,
            "undoable_by_alias": undoable_by_alias,
        }

    def _undoable_aliases(self, undo_by_alias: dict[str, str]) -> dict[str, str]:
        """Check undo snapshots on a read-only connection, cached by operation and stamp."""
        undoable: dict[str, str] = {}
        checked: dict[str, bool] = {}
        with closing(_read_only_connection(self.database.path)) as conn:
            stamp = _undo_stamp(self.vault, self.database.path)
            for alias, operation_id in undo_by_alias.items():
                if operation_id not in checked:
                    key = (operation_id, stamp)
                    with _UNDOABLE_CACHE_LOCK:
                        hit = key in _UNDOABLE_CACHE
                        if hit:
                            checked[operation_id] = _UNDOABLE_CACHE[key]
                    if not hit:
                        stored = conn.execute(
                            "SELECT snapshot_json FROM tag_undo WHERE id=?", (operation_id,)
                        ).fetchone()
                        snapshot = None
                        if stored is not None:
                            try:
                                snapshot = json.loads(stored["snapshot_json"])
                            except json.JSONDecodeError:
                                snapshot = None
                        still = isinstance(snapshot, dict) and snapshot_still_undoable(
                            self.vault, conn, snapshot
                        )
                        with _UNDOABLE_CACHE_LOCK:
                            if len(_UNDOABLE_CACHE) >= _UNDOABLE_CACHE_LIMIT:
                                _UNDOABLE_CACHE.clear()
                            _UNDOABLE_CACHE[key] = still
                        checked[operation_id] = still
                if checked[operation_id]:
                    undoable[alias] = operation_id
        return undoable

    def pin(self, name: str, pinned: bool) -> None:
        with self.vault.entry_operations_locked(), self.database.connect() as conn:
            row = conn.execute("SELECT * FROM tags WHERE name=?", (name,)).fetchone()
            if row is None:
                raise KeyError("标签不存在")
            if pinned and not row["pinned_at"]:
                pinned_count = conn.execute(
                    "SELECT COUNT(*) FROM tags WHERE pinned_at IS NOT NULL"
                ).fetchone()[0]
                if pinned_count >= PIN_LIMIT:
                    raise ValueError("最多固定 8 个")
                position = conn.execute("SELECT COALESCE(MAX(position), 0)+1 FROM tags").fetchone()[
                    0
                ]
                conn.execute(
                    "UPDATE tags SET pinned_at=?, position=? WHERE name=?",
                    (iso_now(), position, name),
                )
            elif not pinned:
                conn.execute("UPDATE tags SET pinned_at=NULL, position=0 WHERE name=?", (name,))

    def reorder(self, names: list[str]) -> None:
        with self.vault.entry_operations_locked(), self.database.connect() as conn:
            pinned = {
                row[0] for row in conn.execute("SELECT name FROM tags WHERE pinned_at IS NOT NULL")
            }
            if len(names) != len(set(names)) or set(names) != pinned:
                raise ValueError("排序须包含全部固定标签各一次")
            conn.executemany("UPDATE tags SET position=? WHERE name=?", enumerate(names, 1))

    def rename(self, name: str, target: str) -> str | None:
        target = valid_name(target)
        if name == target:
            return
        return self._mutate([name], target=target, rename=True)

    def merge(self, names: list[str], target: str) -> str:
        target = valid_name(target)
        names = list(dict.fromkeys(names))
        if len(names) < 2:
            raise ValueError("合并须选择至少两个标签")
        return self._mutate([name for name in names if name != target], target=target)

    def delete(self, names: list[str]) -> str:
        if not names:
            raise ValueError("请先选择标签")
        return self._mutate(list(dict.fromkeys(names)), target=None)

    def _mutate(self, sources: list[str], *, target: str | None, rename=False) -> str:
        operation_id = uuid.uuid4().hex
        entries = {}
        previous: dict[Path, str | None] = {}
        changed: dict[Path, str] = {}
        with self.vault.entry_operations_locked(), self.vault.locked():
            try:
                with self.database.connect() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    records = {row["name"]: dict(row) for row in conn.execute("SELECT * FROM tags")}
                    if any(name not in records for name in sources):
                        raise KeyError("标签不存在，请刷新后重试")
                    if rename:
                        if (
                            target in records
                            or conn.execute(
                                "SELECT 1 FROM tag_aliases WHERE alias=?", (target,)
                            ).fetchone()
                        ):
                            raise ValueError("目标标签已存在，请使用合并")
                        source = records[sources[0]]
                        conn.execute(
                            "INSERT INTO tags VALUES (?, ?, ?, ?, ?)",
                            (
                                target,
                                source["pinned_at"],
                                source["position"],
                                iso_now(),
                                source["created_at"],
                            ),
                        )
                    elif target is not None and target not in records:
                        raise KeyError("目标标签不存在")
                    aliases = dict(
                        conn.execute("SELECT alias, canonical FROM tag_aliases").fetchall()
                    )

                    def replace(values):
                        # Resolve old alias spellings before replacing their canonical name.
                        resolved = self._resolve(conn, values, (aliases, set(records)))
                        if not any(value in sources for value in resolved):
                            return values
                        return list(
                            dict.fromkeys(
                                target if value in sources else value
                                for value in resolved
                                if value not in sources or target is not None
                            )
                        )

                    paths = list((self.vault.vault_path / "wiki/sources").glob("*.md"))
                    paths += list((self.vault.vault_path / "creators").glob("*/sources/*.md"))
                    source_tags = {}
                    for path in paths:
                        self.vault._reject_vault_symlinks(path, field="tag source")
                        text = path.read_text(encoding="utf-8")
                        match = re.match(r"\A---\n(.*?)\n---\n", text, re.S)
                        if not match:
                            continue
                        metadata = yaml.safe_load(match[1])
                        if not isinstance(metadata, dict) or not isinstance(
                            metadata.get("tags"), list
                        ):
                            continue
                        if metadata.get("type") not in {None, "source"}:
                            continue
                        tags = replace(metadata["tags"])
                        if tags != metadata["tags"]:
                            source_tags[path] = tags
                            changed[path] = (
                                "---\n"
                                + replace_frontmatter_tags(match[1], tags)
                                + "\n---\n"
                                + text[match.end() :]
                            )
                    for row in conn.execute("SELECT * FROM entries").fetchall():
                        old = json.loads(row["tags_json"])
                        tags = source_tags.get(
                            self.vault.vault_path / row["source_path"], replace(old)
                        )
                        if old == tags:
                            continue
                        entries[row["id"]] = {
                            "before": {
                                "tags_json": row["tags_json"],
                                "data_json": row["data_json"],
                            },
                            "chunks": [
                                dict(chunk)
                                for chunk in conn.execute(
                                    "SELECT id, tags_text FROM chunks WHERE entry_id=?",
                                    (row["id"],),
                                )
                            ],
                            "fts": [
                                dict(chunk)
                                for chunk in conn.execute(
                                    "SELECT chunk_id, tags FROM chunks_fts WHERE entry_id=?",
                                    (row["id"],),
                                )
                            ],
                        }
                        data = json.loads(row["data_json"])
                        data.setdefault("analysis", {})["tags"] = tags
                        conn.execute(
                            "UPDATE entries SET tags_json=?, data_json=? WHERE id=?",
                            (
                                json.dumps(tags, ensure_ascii=False),
                                json.dumps(data, ensure_ascii=False),
                                row["id"],
                            ),
                        )
                        conn.execute(
                            "UPDATE chunks SET tags_text=? WHERE entry_id=?",
                            (" ".join(tags), row["id"]),
                        )
                        conn.execute(
                            "UPDATE chunks_fts SET tags=? WHERE entry_id=?",
                            (lexical_document(" ".join(tags)), row["id"]),
                        )
                        entry = self.database._entry_from_row(row).model_copy(update={"tags": tags})
                        folder = data.get("creator", {}).get("folder_path") or "wiki"
                        machine = (
                            self.vault.vault_path
                            / folder
                            / ".data/sources"
                            / f"{entry.video_id}.md"
                        )
                        self.vault._reject_vault_symlinks(machine, field="tag machine data")
                        changed[machine] = self.vault._render_machine(entry, data)
                    if target is not None:
                        source_pins = [
                            records[name] for name in sources if records[name]["pinned_at"]
                        ]
                        if source_pins and not rename and not records[target]["pinned_at"]:
                            first = min(
                                source_pins, key=lambda row: (row["position"], row["pinned_at"])
                            )
                            conn.execute(
                                "UPDATE tags SET pinned_at=?, position=? WHERE name=?",
                                (first["pinned_at"], first["position"], target),
                            )
                        # Flatten existing aliases before removing the source canonical rows.
                        for name in sources:
                            conn.execute(
                                "UPDATE tag_aliases SET canonical=? WHERE canonical=?",
                                (target, name),
                            )
                            conn.execute(
                                "INSERT OR REPLACE INTO tag_aliases VALUES (?, ?)", (name, target)
                            )
                        conn.execute(
                            "UPDATE tags SET last_used_at=? WHERE name=?", (iso_now(), target)
                        )
                    conn.executemany("DELETE FROM tags WHERE name=?", [(name,) for name in sources])
                    for path, content in changed.items():
                        previous[path] = path.read_text(encoding="utf-8") if path.exists() else None
                        self.vault._atomic_write(path, content)
                    affected = set(sources) | ({target} if target else set())
                    after_records = {
                        row["name"]: dict(row) for row in conn.execute("SELECT * FROM tags")
                    }
                    after_aliases = dict(conn.execute("SELECT alias, canonical FROM tag_aliases"))
                    changed_aliases = {
                        key
                        for key in aliases.keys() | after_aliases.keys()
                        if aliases.get(key) != after_aliases.get(key)
                    }
                    for entry_id, snapshot in entries.items():
                        row = conn.execute(
                            "SELECT tags_json, data_json FROM entries WHERE id=?", (entry_id,)
                        ).fetchone()
                        snapshot["after"] = dict(row)
                    snapshot = {
                        "files": {
                            str(path.relative_to(self.vault.vault_path)): {
                                "before": previous[path],
                                "after": content,
                            }
                            for path, content in changed.items()
                        },
                        "entries": entries,
                        "tags": {
                            name: {"before": records.get(name), "after": after_records.get(name)}
                            for name in affected
                        },
                        "aliases": {
                            name: {"before": aliases.get(name), "after": after_aliases.get(name)}
                            for name in changed_aliases
                        },
                    }
                    conn.execute(
                        "INSERT INTO tag_undo VALUES (?, ?, ?, NULL)",
                        (
                            operation_id,
                            json.dumps(snapshot, ensure_ascii=False),
                            iso_now(),
                        ),
                    )
                    conn.executemany(
                        "INSERT INTO tag_undo_aliases VALUES (?, ?, ?)",
                        [
                            (operation_id, name, after_aliases[name])
                            for name in changed_aliases
                            if name in after_aliases
                        ],
                    )
            except Exception:
                for path, content in previous.items():
                    if content is None:
                        path.unlink(missing_ok=True)
                    else:
                        self.vault._atomic_write(path, content)
                raise
            if (self.vault.vault_path / ".git").exists():
                self.vault.commit(list(changed), "tags: update vocabulary")
        return operation_id

    def undo(self, operation_id: str) -> None:
        rollback = {}
        with self.vault.entry_operations_locked(), self.vault.locked():
            try:
                with self.database.connect() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    row = conn.execute(
                        "SELECT * FROM tag_undo WHERE id=?", (operation_id,)
                    ).fetchone()
                    if row is None:
                        raise KeyError("撤销记录不存在")
                    if row["undone_at"]:
                        return
                    snapshot = json.loads(row["snapshot_json"])
                    conflict = _undo_conflict(conn, snapshot, self.vault)
                    if conflict:
                        raise TagUndoConflict(conflict)
                    paths = {
                        self.vault.vault_path / relative: values
                        for relative, values in snapshot["files"].items()
                    }
                    # Restore standards before aliases to preserve foreign-key validity.
                    for values in snapshot["tags"].values():
                        before = values["before"]
                        if before:
                            conn.execute(
                                "INSERT INTO tags VALUES (?, ?, ?, ?, ?) "
                                "ON CONFLICT(name) DO UPDATE SET "
                                "pinned_at=excluded.pinned_at, position=excluded.position, "
                                "last_used_at=MAX(COALESCE(tags.last_used_at, ''), "
                                "COALESCE(excluded.last_used_at, '')), "
                                "created_at=excluded.created_at",
                                tuple(
                                    before[key]
                                    for key in (
                                        "name",
                                        "pinned_at",
                                        "position",
                                        "last_used_at",
                                        "created_at",
                                    )
                                ),
                            )
                    for name, values in snapshot["aliases"].items():
                        conn.execute("DELETE FROM tag_aliases WHERE alias=?", (name,))
                        if values["before"]:
                            conn.execute(
                                "INSERT INTO tag_aliases VALUES (?, ?)", (name, values["before"])
                            )
                    for name, values in snapshot["tags"].items():
                        if values["before"] is None:
                            conn.execute("DELETE FROM tags WHERE name=?", (name,))
                    for entry_id, values in snapshot["entries"].items():
                        before = values["before"]
                        conn.execute(
                            "UPDATE entries SET tags_json=?, data_json=? WHERE id=?",
                            (before["tags_json"], before["data_json"], entry_id),
                        )
                        for chunk in values["chunks"]:
                            conn.execute(
                                "UPDATE chunks SET tags_text=? WHERE id=?",
                                (chunk["tags_text"], chunk["id"]),
                            )
                        for chunk in values["fts"]:
                            conn.execute(
                                "UPDATE chunks_fts SET tags=? WHERE chunk_id=?",
                                (chunk["tags"], chunk["chunk_id"]),
                            )
                    for path, values in paths.items():
                        rollback[path] = values["after"]
                        if values["before"] is None:
                            path.unlink(missing_ok=True)
                        else:
                            self.vault._atomic_write(path, values["before"])
                    conn.execute(
                        "UPDATE tag_undo SET undone_at=? WHERE id=?", (iso_now(), operation_id)
                    )
            except Exception:
                for path, content in rollback.items():
                    self.vault._atomic_write(path, content)
                raise
            if (self.vault.vault_path / ".git").exists():
                self.vault.commit(list(rollback), "tags: undo vocabulary change")

    def split(self, alias: str) -> None:
        """Turn an alias back into an independent tag. Articles stay on the canonical name."""
        alias = alias.strip()
        if not alias:
            raise ValueError("请指定要拆开的别名")
        with self.vault.entry_operations_locked(), self.database.connect() as conn:
            row = conn.execute(
                "SELECT canonical FROM tag_aliases WHERE alias=?", (alias,)
            ).fetchone()
            if row is None:
                raise KeyError("别名不存在")
            if conn.execute("SELECT 1 FROM tags WHERE name=?", (alias,)).fetchone():
                raise ValueError("同名标签已存在")
            conn.execute("DELETE FROM tag_aliases WHERE alias=?", (alias,))
            conn.execute(
                "INSERT INTO tags(name, created_at) VALUES (?, ?)",
                (alias, iso_now()),
            )

    def unused_names(self, items) -> list[str]:
        overview = self.overview(items)
        return [row["name"] for row in overview["tags"] if row["count"] == 0]
