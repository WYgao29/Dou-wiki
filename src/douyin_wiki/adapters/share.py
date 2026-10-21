from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx

from ..errors import InvalidShareTextError
from ..models import SourceKind

URL_PATTERN = re.compile(r"https?://[^\s<>\]\[\)\(]+", re.IGNORECASE)
WORK_ID_PATTERNS = (
    re.compile(r"/(?:share/)?(?P<kind>video|note|gallery)/(?P<id>\d{10,})"),
    re.compile(r"[?&](?:aweme_id|item_id)=(?P<id>\d{10,})"),
    re.compile(r"[?&]modal_id=(?P<id>\d{10,})"),
)
CREATOR_PATH_PATTERN = re.compile(r"/user/(?P<sec_uid>[A-Za-z0-9_-]{8,256})")
COLLECTION_PATH_PATTERN = re.compile(r"/collection/(?P<mix_id>[0-9]{1,32})/?\Z")
ALLOWED_HOST_SUFFIXES = ("douyin.com", "iesdouyin.com")


@dataclass(frozen=True)
class ResolvedShare:
    original_url: str
    canonical_url: str
    video_id: str
    redirect_chain: tuple[str, ...]
    source_kind: SourceKind | None = SourceKind.VIDEO


@dataclass(frozen=True)
class ResolvedCollection:
    original_url: str
    canonical_url: str
    mix_id: str
    redirect_chain: tuple[str, ...]


def extract_douyin_url(share_text: str) -> str:
    for match in URL_PATTERN.finditer(share_text):
        candidate = match.group(0).rstrip(".,;:!?'\"，。；：！？）")
        host = (urlparse(candidate).hostname or "").lower()
        if any(host == suffix or host.endswith(f".{suffix}") for suffix in ALLOWED_HOST_SUFFIXES):
            return candidate
    raise InvalidShareTextError("分享文本中没有找到有效的抖音链接")


def extract_work_identity(value: str) -> tuple[str, SourceKind | None] | None:
    for pattern in WORK_ID_PATTERNS:
        if match := pattern.search(value):
            kind = match.groupdict().get("kind")
            source_kind = (
                SourceKind.IMAGE_NOTE if kind in {"note", "gallery"}
                else SourceKind.VIDEO if kind == "video" else None
            )
            return match.group("id"), source_kind
    return None


def extract_video_id(value: str) -> str | None:
    """Compatibility helper returning the Douyin work ID for any supported work."""
    identity = extract_work_identity(value)
    return identity[0] if identity else None


def extract_creator_sec_uid(value: str) -> str | None:
    """Return the stable creator sec_uid from a canonical Douyin user URL."""
    if match := CREATOR_PATH_PATTERN.search(value):
        return match.group("sec_uid")
    return None


def extract_collection_id(value: str) -> str | None:
    try:
        parsed = urlparse(value)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme not in {"http", "https"}
        or (parsed.hostname or "").lower() not in {"www.douyin.com", "douyin.com"}
        or parsed.username
        or parsed.password
        or port is not None
    ):
        return None
    match = COLLECTION_PATH_PATTERN.fullmatch(parsed.path)
    return match.group("mix_id") if match else None


def assert_collection_share(share_text: str) -> str:
    url = extract_douyin_url(share_text)
    if extract_collection_id(url) or is_douyin_short_link(url):
        return url
    raise InvalidShareTextError("请粘贴抖音合集链接，或 v.douyin.com 合集短链")


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def is_douyin_short_link(url: str) -> bool:
    host = _host(url)
    return host == "v.douyin.com" or host.endswith(".v.douyin.com")


def assert_creator_share(share_text: str) -> str:
    """Accept a creator home page or a v.douyin.com short link. Reject other links."""
    url = extract_douyin_url(share_text)
    if is_douyin_short_link(url) or extract_creator_sec_uid(url):
        return url
    raise InvalidShareTextError("请粘贴抖音博主主页链接，或 v.douyin.com 短链")


def _resolved(
    original_url: str, identity: tuple[str, SourceKind | None], chain: list[str]
) -> ResolvedShare:
    work_id, source_kind = identity
    route = "note" if source_kind == SourceKind.IMAGE_NOTE else "video"
    return ResolvedShare(
        original_url=original_url,
        canonical_url=(
            f"https://www.douyin.com/{route}/{work_id}" if source_kind else chain[-1]
        ),
        video_id=work_id,
        redirect_chain=tuple(chain),
        source_kind=source_kind,
    )


def _merge_identity(
    current: tuple[str, SourceKind | None] | None,
    candidate: tuple[str, SourceKind | None],
    chain: list[str],
) -> tuple[str, SourceKind | None]:
    if current is not None and current[0] != candidate[0]:
        raise InvalidShareTextError(
            "短链重定向中的作品 ID 不一致",
            details={"redirect_chain": chain},
        )
    return current if current is not None and candidate[1] is None else candidate


class DouyinShareResolver:
    def __init__(
        self, *, timeout_seconds: float = 15, transport: httpx.AsyncBaseTransport | None = None
    ):
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def resolve(self, share_text: str) -> ResolvedShare:
        original_url = extract_douyin_url(share_text)
        if identity := extract_work_identity(original_url):
            return _resolved(original_url, identity, [original_url])

        chain = [original_url]
        current = original_url
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 Chrome/124 Safari/537.36"
            )
        }
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds, headers=headers, transport=self.transport
        ) as client:
            identity: tuple[str, SourceKind | None] | None = None
            for _ in range(8):
                current = extract_douyin_url(current)
                try:
                    response = await client.get(current, follow_redirects=False)
                except httpx.HTTPError as exc:
                    raise InvalidShareTextError(
                        "无法解析抖音短链", details={"url": current, "cause": str(exc)}
                    ) from exc
                if response_identity := extract_work_identity(str(response.url)):
                    identity = _merge_identity(identity, response_identity, chain)
                location = response.headers.get("location")
                if location:
                    current = urljoin(current, location)
                    chain.append(current)
                    if location_identity := extract_work_identity(current):
                        identity = _merge_identity(identity, location_identity, chain)
                    continue
                if identity:
                    return _resolved(original_url, identity, chain)
                break
            else:
                raise InvalidShareTextError(
                    "抖音短链重定向次数过多",
                    details={"redirect_chain": chain},
                )
        raise InvalidShareTextError(
            "短链已解析，但没有得到抖音作品 ID", details={"redirect_chain": chain}
        )

    async def resolve_creator(self, share_text: str) -> str:
        kind, url = await self.classify_target(share_text)
        if kind != "creator":
            raise InvalidShareTextError("请粘贴抖音博主主页链接；这个链接不是博主主页")
        return url

    async def resolve_collection(self, share_text: str) -> ResolvedCollection:
        original = assert_collection_share(share_text)
        chain = [original]
        mix_id = extract_collection_id(original)
        if mix_id is None:
            current = original
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                transport=self.transport,
                headers={"User-Agent": "Mozilla/5.0 Chrome/124 Safari/537.36"},
            ) as client:
                for _ in range(8):
                    extract_douyin_url(current)
                    try:
                        response = await client.get(current, follow_redirects=False)
                    except httpx.HTTPError as exc:
                        raise InvalidShareTextError(
                            "无法解析抖音合集短链", details={"url": current, "cause": str(exc)}
                        ) from exc
                    candidates = [str(response.url)]
                    location = response.headers.get("location")
                    if location:
                        current = urljoin(current, location)
                        extract_douyin_url(current)
                        candidates.append(current)
                        chain.append(current)
                    for candidate in candidates:
                        if found := extract_collection_id(candidate):
                            if mix_id is not None and found != mix_id:
                                raise InvalidShareTextError("短链重定向中的合集 ID 不一致")
                            mix_id = found
                    if not location:
                        break
                else:
                    raise InvalidShareTextError("抖音短链重定向次数过多")
        if mix_id is None:
            raise InvalidShareTextError("这个链接不是抖音合集")
        return ResolvedCollection(
            original, f"https://www.douyin.com/collection/{mix_id}", mix_id, tuple(chain)
        )

    async def classify(self, share_text: str) -> str:
        kind, _ = await self.classify_target(share_text)
        return kind

    async def classify_target(self, share_text: str) -> tuple[str, str]:
        """Return work, creator, collection, or unknown without fetching canonical links."""
        original_url = extract_douyin_url(share_text)
        if mix_id := extract_collection_id(original_url):
            return "collection", f"https://www.douyin.com/collection/{mix_id}"
        if extract_work_identity(original_url):
            return "work", original_url
        if extract_creator_sec_uid(original_url):
            return "creator", original_url
        if not is_douyin_short_link(original_url):
            return "unknown", original_url
        chain = [original_url]
        current = original_url
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 Chrome/124 Safari/537.36"
            )
        }
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds, headers=headers, transport=self.transport
        ) as client:
            for _ in range(8):
                current = extract_douyin_url(current)
                try:
                    response = await client.get(current, follow_redirects=False)
                except httpx.HTTPError as exc:
                    raise InvalidShareTextError(
                        "无法解析抖音短链", details={"url": current, "cause": str(exc)}
                    ) from exc
                location = response.headers.get("location")
                candidates = [str(response.url)]
                if location:
                    current = urljoin(current, location)
                    extract_douyin_url(current)
                    chain.append(current)
                    candidates.append(current)
                for candidate in candidates:
                    if mix_id := extract_collection_id(candidate):
                        return "collection", f"https://www.douyin.com/collection/{mix_id}"
                    if extract_work_identity(candidate):
                        return "work", candidate
                    if extract_creator_sec_uid(candidate):
                        return "creator", candidate
                if not location:
                    break
            else:
                raise InvalidShareTextError(
                    "抖音短链重定向次数过多", details={"redirect_chain": chain}
                )
        return "unknown", current
