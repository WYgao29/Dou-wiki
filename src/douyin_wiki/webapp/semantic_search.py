"""Read-only, scoped text search using the existing knowledge index."""

import asyncio
import sqlite3
from datetime import timedelta
from typing import Annotated, Literal

from fastapi import HTTPException, Query

from ..errors import DouyinWikiError, ExternalToolError
from ..time_utils import utc_now


def register_semantic_search(app, core, catalog, item_payload, facets, read_tags):
    @app.get("/api/library/search")
    async def search(
        q: str = Query("", max_length=2000),
        author: Annotated[list[str] | None, Query()] = None,
        type: Annotated[list[str] | None, Query()] = None,
        tag: Annotated[list[str] | None, Query()] = None,
        source: Annotated[list[str] | None, Query()] = None,
        inspiration_only: bool = False,
        section: Literal["all", "recent", "favorite", "inspiration"] = "all",
        favorites_job: str | None = None,
        topic_id: str | None = None,
        limit: int = Query(100, ge=1, le=500),
    ):
        overview = await asyncio.to_thread(read_tags, refresh=True)
        all_items = catalog.list_items()
        resolved_tags = set(core.tags.resolve(tag or []))
        items = [
            item
            for item in all_items
            if (not author or item.author in author)
            and (not type or item.content_type in type)
            and (not resolved_tags or resolved_tags.intersection(item.tags))
            and (not source or item.source_kind in source)
            and (not inspiration_only or item.inspirations)
            and (section != "favorite" or item.favorite)
            and (section != "inspiration" or item.inspirations)
            and (section != "recent" or item.captured_at >= utc_now() - timedelta(days=7))
        ]
        try:
            if favorites_job:
                selected = set(
                    (await asyncio.to_thread(core.favorites.get, favorites_job, limit=1))[
                        "imported_entry_ids"
                    ]
                )
                items = [item for item in items if item.entry_id in selected]
            if topic_id:
                topic = await asyncio.to_thread(core.database.get_topic, topic_id)
                selected = {item.entry_id for item in topic.sources if item.enabled}
                items = [item for item in items if item.entry_id in selected]
        except DouyinWikiError as exc:
            raise HTTPException(404, str(exc)) from exc

        def retrieve():
            if not q.strip() or not items:
                return [], "semantic", "当前范围没有匹配证据", {}
            try:
                core.embeddings.signature()
                if core.embeddings.provider_name.startswith("sentence-transformers:"):
                    found = core.search_knowledge(
                        q,
                        entry_ids=[item.entry_id for item in items if item.database_managed],
                        limit=limit * 3,
                    )
                    if not core.embeddings.last_error:
                        by_id = {item.entry_id: item for item in items}
                        ordered, evidence = [], {}
                        for match in found:
                            if match.entry_id not in by_id:
                                continue
                            if match.entry_id not in evidence:
                                evidence[match.entry_id] = []
                                ordered.append(by_id[match.entry_id])
                            evidence[match.entry_id].append(match.model_dump(mode="json"))
                        return (
                            ordered[:limit],
                            "semantic",
                            ("语义检索覆盖已入库资料；只读笔记可使用关键词搜索"),
                            evidence,
                        )
            except (ExternalToolError, OSError, sqlite3.Error, ValueError):
                pass
            keyword_ids = {item.entry_id for item in catalog.filter(query=q)}
            return (
                [item for item in items if item.entry_id in keyword_ids][:limit],
                "keyword",
                (
                    "语义检索暂不可用，已显示关键词结果。请检查本地模型与索引；首次准备模型后需重启服务。"
                ),
                {},
            )

        found, mode, notice, evidence = await asyncio.to_thread(retrieve)
        return {
            "items": [
                {**item_payload(item), "search_evidence": evidence.get(item.entry_id, [])}
                for item in found
            ],
            "facets": facets(all_items, overview),
            "total": len(found),
            "limit": limit,
            "mode": mode,
            "notice": notice,
            "version": catalog.version,
        }
