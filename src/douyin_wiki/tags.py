"""Tag vocabulary, aliases and metadata mutations across Vault and SQLite."""

from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict
from datetime import UTC
from pathlib import Path
from typing import Any

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
"""


def tag_key(name: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", name).casefold())


def valid_name(name: str) -> str:
    name = name.strip()
    if not name or len(name) > 100 or any(ord(char) < 32 for char in name):
        raise ValueError("标签名须为 1–100 个字符，不能包含换行或控制字符")
    return name


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
    parents = {name: name for name in names}

    def root(name):
        while parents[name] != name:
            name = parents[name]
        return name

    for i, left in enumerate(names):
        for right in names[i + 1 :]:
            a, b = keys[left], keys[right]
            if (
                a == b
                or translations.get(a, a) == translations.get(b, b)
                or (min(len(a), len(b)) >= 2 and (a in b or b in a))
            ):
                parents[root(right)] = root(left)
    groups: dict[str, list[str]] = defaultdict(list)
    for name in names:
        groups[root(name)].append(name)
    return [group for group in groups.values() if len(group) > 1]


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

    def overview(self, items) -> dict[str, Any]:
        now = iso_now()
        counts: dict[str, list] = defaultdict(list)
        resolve = self.resolver()
        with self.database.connect() as conn:
            for item in items:
                for name in resolve(item.tags):
                    counts[name].append(item)
            for name in counts:
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
            records = conn.execute("SELECT * FROM tags").fetchall()
            aliases = dict(conn.execute("SELECT alias, canonical FROM tag_aliases").fetchall())
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
                }
            )
        rows.sort(key=lambda row: (-row["count"], row["name"].casefold(), row["name"]))
        pinned = [row for row in rows if row["pinned_at"]]
        pinned.sort(key=lambda row: (row["position"], row["pinned_at"], row["name"]))
        sidebar = [row for row in (pinned if pinned else rows) if row["count"] > 0][:8]
        return {
            "tags": rows,
            "sidebar": sidebar,
            "duplicates": duplicate_groups(rows),
            "aliases": aliases,
        }

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

    def rename(self, name: str, target: str) -> None:
        target = valid_name(target)
        if name == target:
            return
        self._mutate([name], target=target, rename=True)

    def merge(self, names: list[str], target: str) -> None:
        target = valid_name(target)
        names = list(dict.fromkeys(names))
        if len(names) < 2:
            raise ValueError("合并须选择至少两个标签")
        self._mutate([name for name in names if name != target], target=target)

    def delete(self, names: list[str]) -> None:
        if not names:
            raise ValueError("请先选择标签")
        self._mutate(list(dict.fromkeys(names)), target=None)

    def _mutate(self, sources: list[str], *, target: str | None, rename=False) -> None:
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
                            metadata["tags"] = tags
                            source_tags[path] = tags
                            changed[path] = (
                                "---\n"
                                + yaml.safe_dump(
                                    metadata, allow_unicode=True, sort_keys=False
                                ).rstrip()
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
            except Exception:
                for path, content in previous.items():
                    if content is None:
                        path.unlink(missing_ok=True)
                    else:
                        self.vault._atomic_write(path, content)
                raise
            if (self.vault.vault_path / ".git").exists():
                self.vault.commit(list(changed), "tags: update vocabulary")

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
