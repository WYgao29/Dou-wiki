"""Issue known read-only calls inside the authenticated site's request environment."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from typing import Any
from urllib.parse import parse_qs, urlsplit

from ..errors import ExternalToolError
from .favorites_api import RawFavoritePage, Resource, parse_favorite_page, stable_id

_PATHS = {
    "directory": "/aweme/v1/web/collects/list/",
    "folder": "/aweme/v1/web/collects/video/list/",
}
_READ_BUDGET = 20
_ATTEMPT_TIMEOUT = 6
_BACKOFF = (1, 2)
_CALL_NATIVE_JS = r"""
p => { // douyin-wiki:favorites-api
  const paths = ['/aweme/v1/web/collects/list/', '/aweme/v1/web/collects/video/list/'];
  if (!paths.includes(p.path)) return {ok:false};
  const chunks = window.webpackChunkdouyin_web;
  if (!Array.isArray(chunks)) return {ok:false};
  const req = chunks.push([[Symbol('douyin-wiki-read')], {}, r => r]);
  chunks.pop();
  if (!req?.m) return {ok:false};
  const functions = new Set();
  for (const [id, module] of Object.entries(req.m)) {
    const source = Function.prototype.toString.call(module);
    if (!source.includes(p.path)) continue;
    for (const fn of Object.values(req(id) || {})) {
      if (typeof fn !== 'function') continue;
      const text = Function.prototype.toString.call(fn);
      if (!text.includes(p.path) || !text.includes('cursor') || !text.includes('offset')) continue;
      if (paths.some(path => path !== p.path && text.includes(path))) continue;
      if (p.folder_id && !text.includes('collectsId')) continue;
      functions.add(fn);
    }
  }
  if (functions.size !== 1) return {ok:false};
  const args = {cursor:p.cursor, offset:p.count};
  if (p.folder_id) args.collectsId = p.folder_id;
  // The converted return object may already have lost integer precision. Ignore it.
  try { Promise.resolve([...functions][0](args)).catch(() => {}); }
  catch (_) { return {ok:false}; }
  return {ok:true};
}
"""

Key = tuple[Resource, str | None, str, int]


class _RetryableError(Exception):
    pass


class FavoritesBrowserClient:
    def __init__(self, page: Any) -> None:
        self.page = page
        self._open = False
        self._epoch = 0
        self._serial = 0
        self._requests: dict[Any, tuple[int, int, Key]] = {}
        self._cache: OrderedDict[Key, RawFavoritePage] = OrderedDict()
        self._pending: dict[Key, tuple[asyncio.Future, int, int]] = {}
        self._tasks: set[asyncio.Task] = set()

    async def __aenter__(self) -> FavoritesBrowserClient:
        self._open = True
        self.page.on("request", self._on_request)
        self.page.on("response", self._on_response)
        self.page.on("requestfailed", self._on_failed)
        self.page.on("framenavigated", self._on_navigation)
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        self._open = False
        for event, callback in (
            ("request", self._on_request),
            ("response", self._on_response),
            ("requestfailed", self._on_failed),
            ("framenavigated", self._on_navigation),
        ):
            self.page.remove_listener(event, callback)
        for future, _, _ in self._pending.values():
            future.cancel()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._pending.clear()
        self._requests.clear()
        self._cache.clear()

    def _on_navigation(self, frame: Any) -> None:
        if frame != self.page.main_frame:
            return
        self._epoch += 1
        self._cache.clear()
        for future, _, _ in self._pending.values():
            if not future.done():
                future.set_exception(ExternalToolError("收藏页面已切换，本次读取结束"))

    def _on_request(self, request: Any) -> None:
        if not self._open:
            return
        try:
            parsed = urlsplit(request.url)
            if (
                parsed.scheme != "https"
                or parsed.netloc != "www.douyin.com"
                or request.method != "GET"
                or request.frame != self.page.main_frame
            ):
                return
            resource = next((r for r, path in _PATHS.items() if path == parsed.path), None)
            if resource is None:
                return
            query = parse_qs(parsed.query, keep_blank_values=True)
            cursor, count = query.get("cursor", []), query.get("count", [])
            if (
                len(cursor) != 1
                or len(count) != 1
                or not count[0].isascii()
                or not count[0].isdigit()
            ):
                return
            folder_id = None
            if resource == "folder":
                ids = query.get("collects_id", [])
                folder_id = stable_id(ids[0]) if len(ids) == 1 else None
                if folder_id is None:
                    return
            key = (resource, folder_id, cursor[0], int(count[0]))
            self._serial += 1
            self._requests[request] = (self._epoch, self._serial, key)
        except Exception:
            # Request URLs and SDK errors may contain credentials; never expose them.
            return

    def _on_failed(self, request: Any) -> None:
        stamp = self._requests.pop(request, None)
        if stamp is not None:
            self._deliver(stamp, error=_RetryableError())

    def _on_response(self, response: Any) -> None:
        stamp = self._requests.pop(response.request, None)
        if not self._open or stamp is None:
            return
        task = asyncio.create_task(self._capture(response, stamp), name="douyin-favorites-response")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _capture(self, response: Any, stamp: tuple[int, int, Key]) -> None:
        epoch, _, key = stamp
        try:
            body = await response.body()
        except Exception:
            self._deliver(stamp, error=_RetryableError())
            return
        if not self._open or epoch != self._epoch:
            return
        resource, folder_id, cursor, count = key
        raw = RawFavoritePage(resource, folder_id, cursor, count, response.status, body)
        if cursor == "0" and raw.http_status == 200:
            self._cache[key] = raw
            self._cache.move_to_end(key)
            while len(self._cache) > 32:
                self._cache.popitem(last=False)
        self._deliver(stamp, result=raw)

    def _deliver(self, stamp, *, result=None, error=None) -> None:
        epoch, serial, key = stamp
        pending = self._pending.get(key)
        if not self._open or pending is None or epoch != self._epoch:
            return
        future, minimum_serial, pending_epoch = pending
        if future.done() or serial <= minimum_serial or epoch != pending_epoch:
            return
        if error is not None:
            future.set_exception(error)
        else:
            future.set_result(result)

    async def read_page(
        self,
        *,
        resource: Resource,
        cursor: str = "0",
        folder_id: str | None = None,
        count: int = 20,
        use_cached: bool = True,
    ) -> RawFavoritePage:
        if not self._open:
            raise ExternalToolError("收藏读取器已关闭")
        if resource not in _PATHS or type(count) is not int or count <= 0:
            raise ValueError("Invalid favorites resource or page size")
        if resource == "folder" and stable_id(folder_id) is None:
            raise ValueError("Favorites folder requires a stable ID")
        if resource == "directory" and folder_id is not None:
            raise ValueError("Directory request cannot specify a folder")
        key = (resource, folder_id, str(cursor), count)
        if use_cached and (raw := self._cache.pop(key, None)) is not None:
            parse_favorite_page(raw)
            return raw
        if key in self._pending:
            raise ExternalToolError("同一收藏页正在读取")
        try:
            async with asyncio.timeout(_READ_BUDGET):
                for attempt in range(3):
                    future = asyncio.get_running_loop().create_future()
                    self._pending[key] = (future, self._serial, self._epoch)
                    try:
                        async with asyncio.timeout(_ATTEMPT_TIMEOUT):
                            try:
                                invoked = await self.page.evaluate(
                                    _CALL_NATIVE_JS,
                                    {
                                        "path": _PATHS[resource],
                                        "folder_id": folder_id,
                                        "cursor": str(cursor),
                                        "count": count,
                                    },
                                )
                            except Exception as exc:
                                raise ExternalToolError("无法调用收藏网页的原生读取函数") from exc
                            if not isinstance(invoked, dict) or invoked.get("ok") is not True:
                                raise ExternalToolError("当前收藏网页没有可确认的原生读取函数")
                            raw = await future
                            if raw.http_status == 429 or 500 <= raw.http_status <= 599:
                                raise _RetryableError()
                            parse_favorite_page(raw)
                            return raw
                    except (TimeoutError, _RetryableError):
                        if attempt == 2:
                            raise ExternalToolError("收藏接口读取失败，已达到重试次数") from None
                    finally:
                        self._pending.pop(key, None)
                        future.cancel()
                    await asyncio.sleep(_BACKOFF[attempt])
        except TimeoutError:
            raise ExternalToolError("收藏接口读取超过总时间预算") from None
        raise ExternalToolError("收藏接口读取失败")
