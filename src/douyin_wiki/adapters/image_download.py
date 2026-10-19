"""Guarded downloads for cover, preview, avatar and image-note bytes.

Video, audio and ordinary browser navigation do not use this module.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

TRUSTED_IMAGE_ROOTS = ("douyinpic.com", "byteimg.com", "pstatp.com")
MAX_IMAGE_REDIRECTS = 5
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_DEFAULT_PORTS = {"http": 80, "https": 443}
_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_IMAGE_SUBTYPE_RE = re.compile(r"[a-z0-9][a-z0-9.+-]*")


class ImageDownloadRejected(Exception):
    """A picture URL or response cannot be saved. The message is a reason code."""

    def __init__(self, reason: str, *, status: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status


@dataclass(frozen=True)
class _ImagePayload:
    content: bytes
    content_type: str


def validate_image_url(url: str) -> str:
    """Return ``url`` when it may be requested. The query string is unchanged."""
    if not isinstance(url, str) or _ambiguous(url):
        raise ImageDownloadRejected("rejected_url")
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        raise ImageDownloadRejected("rejected_url") from None
    if (
        parsed.scheme not in _DEFAULT_PORTS
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ImageDownloadRejected("rejected_url")
    if host is None or not _trusted_image_host(host):
        raise ImageDownloadRejected("rejected_url")
    if port not in (None, _DEFAULT_PORTS[parsed.scheme]):
        raise ImageDownloadRejected("rejected_url")
    return url


def image_media_type(value: str | None) -> str | None:
    """Return a normalized ``image/<subtype>`` or None when the declaration is unusable."""
    if value is None:
        return None
    mime = value.split(";", 1)[0].strip().lower()
    main, separator, subtype = mime.partition("/")
    if separator != "/" or main != "image" or _IMAGE_SUBTYPE_RE.fullmatch(subtype) is None:
        return None
    return mime


def download_image_httpx(
    url: str,
    *,
    timeout: float,
    headers: Mapping[str, str],
) -> tuple[bytes, str]:
    """Download one image with httpx. Redirects are followed only after each hop is checked."""
    current = validate_image_url(url)
    seen: set[str] = set()
    with httpx.Client(follow_redirects=False, timeout=timeout, headers=dict(headers)) as client:
        for hop in range(MAX_IMAGE_REDIRECTS + 1):
            if current in seen:
                raise ImageDownloadRejected("redirect_loop")
            seen.add(current)
            try:
                response = client.get(current)
            except httpx.HTTPError:
                raise ImageDownloadRejected("transport") from None
            outcome = _classify_httpx(response, current=current, hop=hop)
            if isinstance(outcome, str):
                current = outcome
                continue
            return outcome.content, outcome.content_type
    raise ImageDownloadRejected("redirect_limit")


async def download_image_playwright(
    request: Any,
    url: str,
    *,
    timeout_ms: float,
    headers: Mapping[str, str],
) -> tuple[bytes, str]:
    """Download one image through a Playwright API request context."""
    current = validate_image_url(url)
    seen: set[str] = set()
    for hop in range(MAX_IMAGE_REDIRECTS + 1):
        if current in seen:
            raise ImageDownloadRejected("redirect_loop")
        seen.add(current)
        try:
            response = await request.get(
                current,
                headers=dict(headers),
                timeout=timeout_ms,
                max_redirects=0,
            )
        except ImageDownloadRejected:
            raise
        except Exception:
            raise ImageDownloadRejected("transport") from None
        try:
            outcome = await _classify_playwright(response, current=current, hop=hop)
        except ImageDownloadRejected:
            raise
        except Exception:
            raise ImageDownloadRejected("transport") from None
        if isinstance(outcome, str):
            current = outcome
            continue
        return outcome.content, outcome.content_type
    raise ImageDownloadRejected("redirect_limit")


def _ambiguous(url: str) -> bool:
    if not url or "\\" in url:
        return True
    return any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in url)


def _trusted_image_host(host: str) -> bool:
    lowered = host.lower()
    if lowered == "localhost" or lowered.endswith(".") or ".." in lowered:
        return False
    try:
        ipaddress.ip_address(lowered)
    except ValueError:
        pass
    else:
        return False
    labels = lowered.split(".")
    if not labels or any(_LABEL_RE.fullmatch(label) is None for label in labels):
        return False
    return any(lowered == root or lowered.endswith("." + root) for root in TRUSTED_IMAGE_ROOTS)


def _header(headers: Any, name: str) -> str | None:
    getter = getattr(headers, "get", None)
    if getter is None:
        return None
    value = getter(name)
    if value is None:
        value = getter(name.lower())
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if value is None:
        return None
    return str(value)


def _classify(
    status: int,
    location: str | None,
    content_type: str | None,
    body: bytes,
    *,
    current: str,
    hop: int,
) -> _ImagePayload | str:
    if status in REDIRECT_STATUSES:
        if hop >= MAX_IMAGE_REDIRECTS:
            raise ImageDownloadRejected("redirect_limit")
        if location is None or location == "":
            raise ImageDownloadRejected("missing_location")
        if _ambiguous(location):
            raise ImageDownloadRejected("rejected_url")
        return validate_image_url(urljoin(current, location))
    if status < 200 or status >= 300:
        raise ImageDownloadRejected("http_status", status=status)
    if image_media_type(content_type) is None:
        raise ImageDownloadRejected("content_type")
    if not body:
        raise ImageDownloadRejected("empty")
    return _ImagePayload(body, content_type or "")


def _classify_httpx(response: httpx.Response, *, current: str, hop: int) -> _ImagePayload | str:
    status = response.status_code
    body = b"" if status in REDIRECT_STATUSES else response.content
    return _classify(
        status,
        _header(response.headers, "location"),
        _header(response.headers, "content-type"),
        body,
        current=current,
        hop=hop,
    )


async def _classify_playwright(response: Any, *, current: str, hop: int) -> _ImagePayload | str:
    try:
        status = int(response.status)
        location = _header(response.headers, "location")
        content_type = _header(response.headers, "content-type")
        body = b"" if status in REDIRECT_STATUSES else await response.body()
        return _classify(
            status,
            location,
            content_type,
            body,
            current=current,
            hop=hop,
        )
    finally:
        await _dispose_response(response)


async def _dispose_response(response: Any) -> None:
    dispose = getattr(response, "dispose", None)
    if dispose is None:
        return
    try:
        result = dispose()
        if hasattr(result, "__await__"):
            await result
    except Exception:
        return
