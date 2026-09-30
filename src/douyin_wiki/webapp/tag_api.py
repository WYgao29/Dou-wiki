from __future__ import annotations

import asyncio

from fastapi import HTTPException
from pydantic import BaseModel, Field


class TagChange(BaseModel):
    name: str = Field(default="", max_length=100)
    names: list[str] = Field(default_factory=list, max_length=5000)
    target: str = Field(default="", max_length=100)
    pinned: bool = True


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
            overview()
            if action == "rename":
                core.tags.rename(payload.name, payload.target)
            elif action == "merge":
                core.tags.merge(payload.names, payload.target)
            elif action == "delete":
                core.tags.delete(payload.names)
            elif action == "pin":
                core.tags.pin(payload.name, payload.pinned)
            elif action == "reorder":
                core.tags.reorder(payload.names)
            return overview()

        try:
            result = await asyncio.to_thread(apply)
        except KeyError as exc:
            raise HTTPException(404, detail=str(exc).strip("'")) from exc
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
        def apply():
            overview()
            core.tags.split(payload.name)
            return overview()

        try:
            result = await asyncio.to_thread(apply)
        except KeyError as exc:
            raise HTTPException(404, detail=str(exc).strip("'")) from exc
        except ValueError as exc:
            raise HTTPException(422, detail=str(exc)) from exc
        return await publish_mutation(result, refresh_catalog=False)

    @app.post("/api/tags/cleanup")
    async def cleanup(payload: TagCleanup):
        def apply():
            names = core.tags.unused_names(catalog.list_items())
            if not payload.confirmed:
                return {"names": names, "count": len(names), "confirmed": False}
            if names:
                core.tags.delete(names)
            result = overview()
            result["deleted"] = names
            result["confirmed"] = True
            return result

        try:
            result = await asyncio.to_thread(apply)
        except KeyError as exc:
            raise HTTPException(404, detail=str(exc).strip("'")) from exc
        except ValueError as exc:
            raise HTTPException(422, detail=str(exc)) from exc
        if payload.confirmed:
            return await publish_mutation(result, refresh_catalog=False)
        return result

    return overview
