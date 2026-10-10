"""Issue known read-only calls inside the authenticated site's request environment."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from typing import Any
from urllib.parse import parse_qs, urlsplit

from ..collection_models import stable_id
from ..errors import ExternalToolError
from .collection_api import RawCollectionPage, Resource, parse_collection_page

_PATHS = {
    "detail": "/aweme/v1/web/mix/detail/",
    "list": "/aweme/v1/web/mix/aweme/",
}
_READ_BUDGET = 20
_ATTEMPT_TIMEOUT = 6
_BACKOFF = (1, 2)
_BRIDGE_WAIT_BUDGET = 3
_CALL_NATIVE_JS = r"""
p => { // douyin-wiki:collection-api
  if (location.origin !== 'https://www.douyin.com') return {ok:false};
  const paths = ['/aweme/v1/web/mix/detail/', '/aweme/v1/web/mix/aweme/'];
  if (!paths.includes(p.path)) return {ok:false};
  const chunks = window.webpackChunkdouyin_web;
  if (!Array.isArray(chunks)) return {ok:false, reason:'not_ready'};
  const req = chunks.push([[Symbol('douyin-wiki-collection-read')], {}, r => r]);
  chunks.pop();
  if (!req?.m) return {ok:false, reason:'not_ready'};
  const functions = new Set();
  for (const [id, module] of Object.entries(req.m)) {
    if (!Function.prototype.toString.call(module).includes(p.path)) continue;
    for (const fn of Object.values(req(id) || {})) {
      if (typeof fn !== 'function') continue;
      const text = Function.prototype.toString.call(fn);
      if (!text.includes(p.path) || !text.includes('mixId')) continue;
      if (paths.some(path => path !== p.path && text.includes(path))) continue;
      if (p.path === paths[1] && !(text.includes('mixScene') &&
          text.includes('cursor') && text.includes('count'))) continue;
      functions.add(fn);
    }
  }
  if (!functions.size) return {ok:false, reason:'not_ready'};
  if (functions.size !== 1) return {ok:false};
  const args = {mixId:p.mix_id};
  if (p.path === paths[1]) Object.assign(args, {cursor:p.cursor, count:p.count});
  // Only network bytes are decoded in Python; the converted result is discarded.
  try { Promise.resolve([...functions][0](args)).catch(() => {}); }
  catch (_) { return {ok:false}; }
  return {ok:true};
}
"""

Key = tuple[Resource, str, str, int]


class _RetryableError(Exception):
    pass


class CollectionBrowserClient:
    def __init__(self, page: Any) -> None:
        self.page = page
        self._open = False
        self._epoch = 0
        self._serial = 0
        self._requests: dict[Any, tuple[int, int, Key]] = {}
        self._cache: OrderedDict[Key, RawCollectionPage] = OrderedDict()
        self._pending: dict[Key, tuple[asyncio.Future, int, int]] = {}
        self._tasks: set[asyncio.Task] = set()
        self._captures: dict[asyncio.Task, tuple[int, int, Key]] = {}

    async def __aenter__(self) -> CollectionBrowserClient:
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
        self._captures.clear()

    def _on_navigation(self, frame: Any) -> None:
        if frame != self.page.main_frame:
            return
        self._epoch += 1
        self._cache.clear()
        for future, _, _ in self._pending.values():
            if not future.done():
                future.set_exception(ExternalToolError("合集页面已切换，本次读取结束"))

    def _on_request(self, request: Any) -> None:
        if not self._open:
            return
        try:
            if not self._is_douyin_page():
                return
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
            ids = query.get("mix_id", [])
            mix_id = stable_id(ids[0]) if len(ids) == 1 else None
            if mix_id is None:
                return
            cursor, count = "0", 0
            if resource == "list":
                cursors, counts = query.get("cursor", []), query.get("count", [])
                if len(cursors) != 1 or len(counts) != 1:
                    return
                if stable_id(cursors[0]) is None or stable_id(counts[0]) is None:
                    return
                cursor, count = cursors[0], int(counts[0])
            key = (resource, mix_id, cursor, count)
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
        task = asyncio.create_task(
            self._capture(response, stamp), name="douyin-collections-response"
        )
        self._tasks.add(task)
        self._captures[task] = stamp
        task.add_done_callback(self._capture_done)

    def _capture_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        self._captures.pop(task, None)

    def _has_inflight(self, key: Key) -> bool:
        return any(
            epoch == self._epoch and resource_key == key
            for epoch, _, resource_key in self._requests.values()
        ) or any(
            not task.done() and epoch == self._epoch and resource_key == key
            for task, (epoch, _, resource_key) in self._captures.items()
        )

    async def _capture(self, response: Any, stamp: tuple[int, int, Key]) -> None:
        epoch, _, key = stamp
        try:
            body = await response.body()
        except Exception:
            self._deliver(stamp, error=_RetryableError())
            return
        if not self._open or epoch != self._epoch:
            return
        resource, mix_id, cursor, count = key
        raw = RawCollectionPage(resource, mix_id, cursor, count, response.status, body)
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

    def _is_douyin_page(self) -> bool:
        origin = urlsplit(self.page.url)
        return origin.scheme == "https" and origin.netloc == "www.douyin.com"

    async def _invoke(self, parameters: dict[str, Any]) -> None:
        deadline = asyncio.get_running_loop().time() + _BRIDGE_WAIT_BUDGET
        while True:
            if not self._is_douyin_page():
                raise ExternalToolError("合集页面来源已变化，本次读取结束")
            try:
                invoked = await self.page.evaluate(_CALL_NATIVE_JS, parameters)
            except Exception:
                raise ExternalToolError("无法调用合集网页的原生读取函数") from None
            if isinstance(invoked, dict) and invoked.get("ok") is True:
                return
            if (
                not isinstance(invoked, dict)
                or invoked.get("reason") != "not_ready"
                or asyncio.get_running_loop().time() >= deadline
            ):
                raise ExternalToolError("当前合集网页没有可确认的原生读取函数")
            await asyncio.sleep(0.1)

    async def read_page(
        self,
        *,
        resource: Resource,
        cursor: str = "0",
        mix_id: str,
        count: int = 20,
        use_cached: bool = True,
    ) -> RawCollectionPage:
        if not self._open:
            raise ExternalToolError("合集读取器已关闭")
        if resource not in _PATHS or type(count) is not int or count <= 0:
            raise ValueError("Invalid collections resource or page size")
        if stable_id(mix_id) is None or stable_id(cursor) is None:
            raise ValueError("Collection requires stable mix ID and cursor")
        if resource == "detail":
            cursor, count = "0", 0
        key = (resource, mix_id, str(cursor), count)
        if use_cached and (raw := self._cache.pop(key, None)) is not None:
            parse_collection_page(raw)
            return raw
        if key in self._pending:
            raise ExternalToolError("同一合集页正在读取")
        try:
            async with asyncio.timeout(_READ_BUDGET):
                for attempt in range(3):
                    future = asyncio.get_running_loop().create_future()
                    reuse_inflight = attempt == 0 and use_cached and self._has_inflight(key)
                    self._pending[key] = (
                        future,
                        -1 if reuse_inflight else self._serial,
                        self._epoch,
                    )
                    try:
                        async with asyncio.timeout(_ATTEMPT_TIMEOUT):
                            if not reuse_inflight:
                                await self._invoke(
                                    {
                                        "path": _PATHS[resource],
                                        "mix_id": mix_id,
                                        "cursor": str(cursor),
                                        "count": count,
                                    }
                                )
                            raw = await future
                            if raw.http_status == 429 or 500 <= raw.http_status <= 599:
                                raise _RetryableError()
                            parse_collection_page(raw)
                            return raw
                    except (TimeoutError, _RetryableError):
                        if attempt == 2:
                            raise ExternalToolError("合集接口读取失败，已达到重试次数") from None
                    finally:
                        self._pending.pop(key, None)
                        future.cancel()
                    await asyncio.sleep(_BACKOFF[attempt])
        except TimeoutError:
            raise ExternalToolError("合集接口读取超过总时间预算") from None
        raise ExternalToolError("合集接口读取失败")
