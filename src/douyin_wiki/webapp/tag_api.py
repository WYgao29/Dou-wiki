from __future__ import annotations

import asyncio

from fastapi import HTTPException
from pydantic import BaseModel, Field

from ..tags import TagUndoConflict


class TagChange(BaseModel):
    name: str = Field(default="", max_length=100)
    names: list[str] = Field(default_factory=list, max_length=5000)
    target: str = Field(default="", max_length=100)
    pinned: bool = True
    operation_id: str = Field(default="", max_length=32)


class TagCleanup(BaseModel):
    confirmed: bool = False


def install_tag_api(app, core, catalog, publish_mutation):
    def overview():
        # Serialize against publication, and avoid resurrecting a deleted name from stale items.
        with core.vault.entry_operations_locked():
            if catalog.compute_fingerprint() != catalog.fingerprint:
                catalog.refresh()
            return core.tags.overview(catalog.list_items())

    @app.get("/api/tags")
    async def list_tags():
        return await asyncio.to_thread(overview)

    async def mutate(action, payload):
        def apply():
            with core.vault.entry_operations_locked():
                overview()
                operation_id = None
                if action == "rename":
                    operation_id = core.tags.rename(payload.name, payload.target)
                elif action == "merge":
                    operation_id = core.tags.merge(payload.names, payload.target)
                elif action == "delete":
                    operation_id = core.tags.delete(payload.names)
                elif action == "pin":
                    core.tags.pin(payload.name, payload.pinned)
                elif action == "reorder":
                    core.tags.reorder(payload.names)
                elif action == "split":
                    core.tags.split(payload.name)
                elif action == "undo":
                    core.tags.undo(payload.operation_id)
                catalog.refresh()
                return {**overview(), "operation_id": operation_id}

        try:
            result = await asyncio.to_thread(apply)
        except KeyError as exc:
            raise HTTPException(404, detail=str(exc).strip("'")) from exc
        except TagUndoConflict as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, detail=str(exc)) from exc
        return await publish_mutation(result, refresh_catalog=False)

    @app.post("/api/tags/rename")
    async def rename(payload: TagChange):
        return await mutate("rename", payload)

    @app.post("/api/tags/merge")
    async def merge(payload: TagChange):
        return await mutate("merge", payload)

    @app.post("/api/tags/delete")
    async def delete(payload: TagChange):
        return await mutate("delete", payload)

    @app.post("/api/tags/pin")
    async def pin(payload: TagChange):
        return await mutate("pin", payload)

    @app.post("/api/tags/reorder")
    async def reorder(payload: TagChange):
        return await mutate("reorder", payload)

    @app.post("/api/tags/split")
    async def split(payload: TagChange):
        return await mutate("split", payload)

    @app.post("/api/tags/undo")
    async def undo(payload: TagChange):
        return await mutate("undo", payload)

    @app.post("/api/tags/cleanup")
    async def cleanup(payload: TagCleanup):
        def apply():
            with core.vault.entry_operations_locked():
                catalog.refresh()
                if catalog.load_errors:
                    raise TagUndoConflict(
                        "部分资料无法读取，暂时无法安全清理标签；请先修复资料格式"
                    )
                items = catalog.list_items()
                visible = {item.entry_id: item for item in items}
                entries = core.database.list_entries()
                if any(
                    entry.id not in visible
                    or visible[entry.id].source_path != entry.source_path
                    for entry in entries
                ):
                    raise TagUndoConflict(
                        "部分受管资料未能完整读取，暂时无法安全清理标签；请先修复资料格式"
                    )
                names = core.tags.unused_names(items)
                resolve = core.tags.resolver()
                referenced = {name for entry in entries for name in resolve(entry.tags)}
                if referenced.intersection(names):
                    raise TagUndoConflict(
                        "资料文件与数据库的标签不一致，暂时无法安全清理；请先同步资料索引"
                    )
                if not payload.confirmed:
                    return {"names": names, "count": len(names), "confirmed": False}
                operation_id = core.tags.delete(names) if names else None
                catalog.refresh()
                return {
                    **overview(),
                    "deleted": names,
                    "confirmed": True,
                    "operation_id": operation_id,
                }

        try:
            result = await asyncio.to_thread(apply)
        except KeyError as exc:
            raise HTTPException(404, detail=str(exc).strip("'")) from exc
        except TagUndoConflict as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, detail=str(exc)) from exc
        if payload.confirmed:
            return await publish_mutation(result, refresh_catalog=False)
        return result

    return overview
