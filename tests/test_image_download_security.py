"""Offline checks for picture URL checks, redirect hops and adapter downloads."""

from __future__ import annotations

import asyncio
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
import pytest

from douyin_wiki.adapters.creator import DouyinCreatorAdapter
from douyin_wiki.adapters.image_download import (
    ImageDownloadRejected,
    download_image_httpx,
    download_image_playwright,
    validate_image_url,
)
from douyin_wiki.adapters.image_note import PlaywrightImageNoteDownloader
from douyin_wiki.adapters.media import (
    CapturedCdnMedia,
    YtDlpDownloader,
    _download_cover_candidates,
    _info_from_aweme_detail,
)
from douyin_wiki.config import MediaSettings
from douyin_wiki.errors import ExternalToolError

JPEG = b"\xff\xd8\xfffake-jpeg"
PNG = b"\x89PNGfake-png"
HEADERS = {"Referer": "https://www.douyin.com/"}
SIGNED = "https://p3.douyinpic.com/obj/a.jpg?x-expires=1&x-signature=ab%2Bcd%3D%2F"
ALLOWED = [
    "https://douyinpic.com/a.jpg",
    "http://byteimg.com/a.jpg",
    "https://pstatp.com/a.jpg",
    SIGNED,
    "https://P3.DouYinPic.COM/A.JPG",
    "https://img.pstatp.com/a.webp",
    "https://a.b.byteimg.com/a.png",
    "https://p3.douyinpic.com:443/a.jpg",
    "http://byteimg.com:80/a.jpg",
    "https://p3.douyinpic.com/a.jpg#fragment",
]
REJECTED = [
    "http://127.0.0.1/a.jpg",
    "http://127.0.0.1:80/a.jpg",
    "https://[::1]/a.jpg",
    "https://localhost/a.jpg",
    "https://LOCALHOST/a.jpg",
    "http://douyinpic.com:81/a.jpg",
    "https://douyinpic.com:8443/a.jpg",
    "https://user:pass@p3.douyinpic.com/a.jpg?x-signature=super-secret-value",
    "https://user@p3.douyinpic.com/a.jpg",
    "https://@p3.douyinpic.com/a.jpg",
    "https://douyinpic.com.evil.test/a.jpg",
    "https://evildouyinpic.com/a.jpg",
    "https://notpstatp.com/a.jpg",
    "https://cdn.com/a.jpg",
    "https://example.test/a.jpg",
    "https://evil.com/douyinpic.com/a.jpg",
    "javascript:alert(1)",
    "file:///etc/passwd",
    "https:///a.jpg",
    "",
    "https://p3.douyinpic.com/a b.jpg",
    "https://p3.douyinpic.com/a\\b.jpg",
    "https://p3.douyinpic.com/a\nb.jpg",
    " https://p3.douyinpic.com/a.jpg",
    "https://p3.douyinpic.com./a.jpg",
    "https://p3..douyinpic.com/a.jpg",
    "https://例子.douyinpic.com/a.jpg",
    "https://p3.douyinpic.com%2eevil.test/a.jpg",
    "http://2130706433/a.jpg",
    "https://0x7f000001/a.jpg",
]


class Step:
    def __init__(
        self,
        status=200,
        headers=None,
        body=b"",
        location=None,
        get_error=None,
        body_error=None,
    ):
        self.status = status
        self.headers = dict(headers or {})
        if location is not None:
            self.headers["location"] = location
        self.body = body
        self.get_error = get_error
        self.body_error = body_error


class PlaywrightResponse:
    def __init__(self, step: Step):
        self.status = step.status
        self.headers = step.headers
        self._body = step.body
        self._body_error = step.body_error
        self.disposed = False

    async def body(self) -> bytes:
        if self._body_error is not None:
            raise self._body_error
        return self._body

    async def dispose(self) -> None:
        self.disposed = True


class PlaywrightRequest:
    def __init__(self, routes: dict[str, Step]):
        self.routes = routes
        self.urls: list[str] = []
        self.calls: list[dict] = []
        self.responses: list[PlaywrightResponse] = []

    async def get(self, url, **kwargs):
        self.urls.append(url)
        self.calls.append(kwargs)
        step = _step_for(self.routes, url)
        if step.get_error is not None:
            raise step.get_error
        response = PlaywrightResponse(step)
        self.responses.append(response)
        return response


@pytest.fixture(autouse=True)
def block_real_http(monkeypatch):
    def explode(_transport, request):
        raise AssertionError(f"real network transport used for {request.url.host}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", explode)


def _find_step(routes: dict[str, Step], url: str) -> Step | None:
    if url in routes:
        return routes[url]
    stripped = url.split("#", 1)[0]
    if stripped in routes:
        return routes[stripped]
    for route_url, step in routes.items():
        if route_url.split("#", 1)[0] == stripped:
            return step
    return None


def _step_for(routes: dict[str, Step], url: str) -> Step:
    return _find_step(routes, url) or Step(status=500, body=b"unexpected")


def _install_httpx(monkeypatch, routes: dict[str, Step]):
    recorded: list[str] = []
    options: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        # httpx lowercases the host and drops :80/:443 before MockTransport
        # sees request.url. Match the string passed to Client.get.
        requested = recorded[-1] if recorded else str(request.url)
        step = _find_step(routes, requested) or _find_step(routes, str(request.url))
        if step is None:
            step = Step(status=500, body=b"unexpected")
        return httpx.Response(step.status, headers=step.headers, content=step.body)

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def factory(*args, **kwargs):
        options.append(dict(kwargs))
        kwargs["transport"] = transport
        client = real_client(*args, **kwargs)
        original = client.get

        def get(url, *args, **kwargs):
            recorded.append(str(url))
            return original(url, *args, **kwargs)

        client.get = get
        return client

    monkeypatch.setattr("douyin_wiki.adapters.image_download.httpx.Client", factory)
    return recorded, options


def _hosts(urls: list[str]) -> list[str]:
    return [(urlsplit(url).hostname or "").lower() for url in urls]


def _snapshot(path: Path) -> list[tuple[str, bytes]]:
    if not path.exists():
        return []
    return sorted(
        (item.relative_to(path).as_posix(), item.read_bytes())
        for item in path.rglob("*")
        if item.is_file()
    )


async def _both(monkeypatch, url: str, routes: dict[str, Step], directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    sentinel = directory / "kept.jpg"
    sentinel.write_bytes(b"old")
    before = _snapshot(directory)
    recorded, options = _install_httpx(monkeypatch, routes)
    httpx_result = None
    httpx_error = None
    try:
        httpx_result = download_image_httpx(url, timeout=5, headers=HEADERS)
    except ImageDownloadRejected as exc:
        httpx_error = exc
    request = PlaywrightRequest(routes)
    playwright_result = None
    playwright_error = None
    try:
        playwright_result = await download_image_playwright(
            request, url, timeout_ms=5_000, headers=HEADERS
        )
    except ImageDownloadRejected as exc:
        playwright_error = exc
    assert _snapshot(directory) == before
    assert sentinel.read_bytes() == b"old"
    return {
        "httpx_urls": recorded,
        "httpx_options": options,
        "httpx_result": httpx_result,
        "httpx_error": httpx_error,
        "playwright": request,
        "playwright_result": playwright_result,
        "playwright_error": playwright_error,
    }


def _assert_same_failure(outcome, reason: str, urls: list[str], absent: list[str]):
    assert [item.reason for item in (outcome["httpx_error"], outcome["playwright_error"])] == [
        reason,
        reason,
    ]
    assert outcome["httpx_result"] is outcome["playwright_result"] is None
    assert outcome["httpx_urls"] == outcome["playwright"].urls == urls
    for secret in absent:
        assert secret not in str(outcome["httpx_error"])
        assert secret not in str(outcome["playwright_error"])
        assert all(secret not in url for url in urls)
    assert all(response.disposed for response in outcome["playwright"].responses)
    for call in outcome["playwright"].calls:
        assert call["max_redirects"] == 0
        assert call["headers"]["Referer"] == "https://www.douyin.com/"
        assert "cookie" not in {key.lower() for key in call["headers"]}
    for item in outcome["httpx_options"]:
        assert item["follow_redirects"] is False
        assert item["headers"]["Referer"] == "https://www.douyin.com/"
        assert "cookie" not in {key.lower() for key in item["headers"]}


def _assert_same_success(outcome, urls: list[str], body: bytes, content_type: str):
    assert outcome["httpx_error"] is outcome["playwright_error"] is None
    assert outcome["httpx_result"] == outcome["playwright_result"] == (body, content_type)
    assert outcome["httpx_urls"] == outcome["playwright"].urls == urls
    assert all(response.disposed for response in outcome["playwright"].responses)
    assert all(call["max_redirects"] == 0 for call in outcome["playwright"].calls)
    assert all(item["follow_redirects"] is False for item in outcome["httpx_options"])


@pytest.mark.parametrize("url", ALLOWED)
def test_allowed_image_urls_keep_the_original_string(url: str) -> None:
    assert validate_image_url(url) == url


@pytest.mark.parametrize("url", REJECTED)
def test_rejected_image_urls_have_no_signed_target_in_the_error(url: str) -> None:
    with pytest.raises(ImageDownloadRejected) as caught:
        validate_image_url(url)
    assert caught.value.reason == "rejected_url"
    assert "x-signature" not in str(caught.value)
    assert "user:pass" not in str(caught.value)


@pytest.mark.parametrize("url", REJECTED)
async def test_rejected_initial_urls_are_not_requested(
    monkeypatch, tmp_path: Path, url: str
) -> None:
    outcome = await _both(monkeypatch, url, {}, tmp_path)
    _assert_same_failure(
        outcome, "rejected_url", [], ["x-signature", "user:pass", "super-secret-value"]
    )
    assert _hosts(outcome["httpx_urls"]) == []


@pytest.mark.parametrize("url", ALLOWED)
async def test_allowed_urls_are_requested_unchanged(monkeypatch, tmp_path: Path, url: str) -> None:
    routes = {url: Step(headers={"content-type": "image/jpeg"}, body=JPEG)}
    outcome = await _both(monkeypatch, url, routes, tmp_path)
    _assert_same_success(outcome, [url], JPEG, "image/jpeg")


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
async def test_trusted_redirect_is_rechecked_for_each_status(
    monkeypatch, tmp_path: Path, status: int
) -> None:
    start = "https://p3.douyinpic.com/dir/a.jpg?old=1"
    nxt = "https://img.byteimg.com/z.jpg?x-signature=ab%2Bcd%3D%2F"
    routes = {
        start: Step(status=status, location=nxt),
        nxt: Step(headers={"content-type": "image/png; charset=utf-8"}, body=PNG),
    }
    outcome = await _both(monkeypatch, start, routes, tmp_path)
    _assert_same_success(outcome, [start, nxt], PNG, "image/png; charset=utf-8")


async def test_relative_redirect_preserves_the_signed_query(monkeypatch, tmp_path: Path) -> None:
    start = "https://p3.douyinpic.com/dir/a.jpg?old=1"
    nxt = "https://p3.douyinpic.com/dir/b.jpg?x-signature=ab%2Bcd%3D%2F"
    routes = {
        start: Step(status=302, location="b.jpg?x-signature=ab%2Bcd%3D%2F"),
        nxt: Step(headers={"content-type": "Image/JPEG; charset=binary"}, body=JPEG),
    }
    outcome = await _both(monkeypatch, start, routes, tmp_path)
    _assert_same_success(outcome, [start, nxt], JPEG, "Image/JPEG; charset=binary")


async def test_protocol_relative_redirect_can_cross_to_another_trusted_host(
    monkeypatch, tmp_path: Path
) -> None:
    start = "https://p3.douyinpic.com/a.jpg"
    nxt = "https://img.pstatp.com/z.jpg"
    routes = {
        start: Step(status=302, location="//img.pstatp.com/z.jpg"),
        nxt: Step(headers={"content-type": "image/webp"}, body=b"RIFF\x00\x00\x00\x00WEBPok"),
    }
    outcome = await _both(monkeypatch, start, routes, tmp_path)
    _assert_same_success(
        outcome, [start, nxt], b"RIFF\x00\x00\x00\x00WEBPok", "image/webp"
    )


@pytest.mark.parametrize(
    ("location", "secret"),
    [
        ("http://127.0.0.1/secret.jpg", "127.0.0.1"),
        ("https://[::1]/secret.jpg", "::1"),
        ("https://localhost/secret.jpg", "localhost"),
        ("https://douyinpic.com.evil.test/secret.jpg", "douyinpic.com.evil.test"),
        ("https://evildouyinpic.com/secret.jpg", "evildouyinpic.com"),
        (
            "https://user:pass@p3.douyinpic.com/a.jpg?x-signature=super-secret-value",
            "super-secret-value",
        ),
        ("https://p3.douyinpic.com:8443/a.jpg", ":8443"),
        ("//evil.test/a.jpg", "evil.test"),
        (" https://p3.douyinpic.com/b.jpg", "b.jpg"),
    ],
)
async def test_redirect_to_an_untrusted_target_is_not_requested(
    monkeypatch, tmp_path: Path, location: str, secret: str
) -> None:
    start = SIGNED
    outcome = await _both(
        monkeypatch, start, {start: Step(status=302, location=location)}, tmp_path
    )
    _assert_same_failure(outcome, "rejected_url", [start], [secret, "user:pass"])
    assert secret not in " ".join(_hosts(outcome["httpx_urls"]))


async def test_missing_redirect_location_stops(monkeypatch, tmp_path: Path) -> None:
    start = "https://p3.douyinpic.com/a.jpg"
    outcome = await _both(monkeypatch, start, {start: Step(status=302)}, tmp_path)
    _assert_same_failure(outcome, "missing_location", [start], [])


async def test_redirect_loop_stops_before_repeating_a_request(monkeypatch, tmp_path: Path) -> None:
    first = "https://p3.douyinpic.com/a.jpg"
    second = "https://p9.byteimg.com/b.jpg"
    routes = {
        first: Step(status=302, location=second),
        second: Step(status=302, location=first),
    }
    outcome = await _both(monkeypatch, first, routes, tmp_path)
    _assert_same_failure(outcome, "redirect_loop", [first, second], [])


async def test_self_redirect_is_not_requested_twice(monkeypatch, tmp_path: Path) -> None:
    start = "https://pstatp.com/a.jpg"
    outcome = await _both(
        monkeypatch, start, {start: Step(status=302, location=start)}, tmp_path
    )
    _assert_same_failure(outcome, "redirect_loop", [start], [])


async def test_five_redirects_can_reach_an_image(monkeypatch, tmp_path: Path) -> None:
    urls = [f"https://p3.douyinpic.com/r{index}.jpg" for index in range(6)]
    routes = {urls[index]: Step(status=302, location=urls[index + 1]) for index in range(5)}
    routes[urls[5]] = Step(headers={"content-type": "image/jpeg"}, body=JPEG)
    outcome = await _both(monkeypatch, urls[0], routes, tmp_path)
    _assert_same_success(outcome, urls, JPEG, "image/jpeg")


async def test_sixth_redirect_is_not_requested(monkeypatch, tmp_path: Path) -> None:
    urls = [f"https://p3.douyinpic.com/r{index}.jpg" for index in range(7)]
    routes = {urls[index]: Step(status=302, location=urls[index + 1]) for index in range(6)}
    outcome = await _both(monkeypatch, urls[0], routes, tmp_path)
    _assert_same_failure(outcome, "redirect_limit", urls[:6], [])
    assert urls[6] not in outcome["httpx_urls"]


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"content-type": "text/html"},
        {"content-type": "TEXT/HTML; charset=utf-8"},
        {"content-type": "application/octet-stream"},
        {"content-type": "image/"},
        {"content-type": "image/*"},
    ],
)
async def test_non_image_responses_are_not_saved(
    monkeypatch, tmp_path: Path, headers: dict[str, str]
) -> None:
    start = "https://p3.douyinpic.com/looks-like.jpg"
    outcome = await _both(
        monkeypatch,
        start,
        {start: Step(headers=headers, body=JPEG)},
        tmp_path,
    )
    _assert_same_failure(outcome, "content_type", [start], [])


async def test_empty_image_response_is_rejected(monkeypatch, tmp_path: Path) -> None:
    start = "https://p3.douyinpic.com/empty.jpg"
    outcome = await _both(
        monkeypatch,
        start,
        {start: Step(headers={"content-type": "image/jpeg"}, body=b"")},
        tmp_path,
    )
    _assert_same_failure(outcome, "empty", [start], [])


async def test_non_redirect_status_does_not_follow_location(monkeypatch, tmp_path: Path) -> None:
    start = "https://p3.douyinpic.com/a.jpg"
    other = "https://img.byteimg.com/b.jpg"
    routes = {
        start: Step(status=304, location=other),
        other: Step(body=JPEG, headers={"content-type": "image/jpeg"}),
    }
    outcome = await _both(monkeypatch, start, routes, tmp_path)
    _assert_same_failure(outcome, "http_status", [start], [])
    assert outcome["httpx_error"].status == 304
    assert other not in outcome["httpx_urls"]


def _cover_detail(urls: dict[str, str]) -> dict:
    return {
        "aweme_id": "1234567890123",
        "desc": "测试作品",
        "author": {"sec_uid": "creator123", "nickname": "作者"},
        "video": {key: {"url_list": [url]} for key, url in urls.items()},
    }


def _patch_cover_download(monkeypatch, tmp_path: Path, handler):
    transport = httpx.MockTransport(handler)
    real_client = httpx.Client
    options: list[dict] = []

    def factory(*args, **kwargs):
        options.append(dict(kwargs))
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr("douyin_wiki.adapters.image_download.httpx.Client", factory)

    async def captured(_url):
        return CapturedCdnMedia(
            video_url="https://example.test/video.mp4",
            audio_url=None,
            page_title="测试作品",
            info=_info_from_aweme_detail(_cover_detail({
                "cover": "https://p3-pc-sign.douyinpic.com/custom.jpg?x-signature=ab%2B",
                "origin_cover": "https://p9.douyinpic.com/origin.jpg",
                "dynamic_cover": "https://p3.byteimg.com/dynamic.webp",
            })),
        )

    def run(command, **kwargs):
        output = command[command.index("--output") + 1].replace("%(ext)s", "mp4")
        Path(output).write_bytes(b"video")
        return subprocess.CompletedProcess(command, 0, "", "")

    downloader = YtDlpDownloader(MediaSettings(), tmp_path / "browser")
    monkeypatch.setattr(downloader, "_capture_cdn_url", captured)
    monkeypatch.setattr("douyin_wiki.adapters.media._run", run)
    return downloader, options


async def test_cover_redirect_keeps_worker_heartbeat_and_tries_the_next_kind(
    monkeypatch, tmp_path: Path
) -> None:
    requested: list[str] = []
    started = threading.Event()
    heartbeat_ran = threading.Event()
    continued = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if request.url.host in {"127.0.0.1", "localhost"}:
            return httpx.Response(500, content=b"forbidden")
        if "custom.jpg" in str(request.url):
            started.set()
            continued.append(heartbeat_ran.wait(timeout=2))
            return httpx.Response(302, headers={"location": "http://127.0.0.1/secret.jpg"})
        if "origin.jpg" in str(request.url):
            return httpx.Response(
                200, headers={"content-type": "image/jpeg"}, content=b"\xff\xd8\xfforigin"
            )
        return httpx.Response(500, content=b"unexpected")

    downloader, options = _patch_cover_download(monkeypatch, tmp_path, handler)

    async def heartbeat():
        while not started.is_set():
            await asyncio.sleep(0.001)
        heartbeat_ran.set()

    metadata = (
        await asyncio.gather(
            heartbeat(),
            downloader.download(
                "https://www.douyin.com/video/1234567890123", "1234567890123", tmp_path
            ),
        )
    )[1]
    assert continued == [True]
    assert metadata.thumbnail_kind == "origin_cover"
    assert Path(metadata.thumbnail_path).read_bytes() == b"\xff\xd8\xfforigin"
    assert [urlsplit(url).hostname for url in requested] == [
        "p3-pc-sign.douyinpic.com",
        "p9.douyinpic.com",
    ]
    assert "x-signature=ab%2B" in requested[0]
    assert all(item["follow_redirects"] is False for item in options)
    assert not list(tmp_path.glob("*.part"))


async def test_cover_cache_precedes_lower_priority_after_a_rejected_redirect(
    monkeypatch, tmp_path: Path
) -> None:
    cached = tmp_path / "cover.jpg"
    cached.write_bytes(b"cached-cover")
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if "custom.jpg" in str(request.url):
            return httpx.Response(302, headers={"location": "https://douyinpic.com.evil.test/x.jpg"})
        return httpx.Response(
            200, headers={"content-type": "image/jpeg"}, content=b"\xff\xd8\xffshould-not-save"
        )

    downloader, _options = _patch_cover_download(monkeypatch, tmp_path, handler)
    metadata = await downloader.download(
        "https://www.douyin.com/video/1234567890123", "1234567890123", tmp_path
    )
    assert metadata.thumbnail_path == str(cached)
    assert metadata.thumbnail_kind == "douyin_cover"
    assert cached.read_bytes() == b"cached-cover"
    assert [urlsplit(url).hostname for url in requested] == ["p3-pc-sign.douyinpic.com"]
    assert not (tmp_path / "origin_cover.jpg").exists()


def test_direct_cover_helper_skips_invalid_candidate_without_touching_files(tmp_path: Path) -> None:
    existing = tmp_path / "cover.jpg"
    existing.write_bytes(b"already")
    result = _download_cover_candidates(
        {"thumbnails": [
            {"id": "cover", "url": "http://127.0.0.1/secret.jpg"},
            {"id": "origin_cover", "url": "https://example.test/origin.jpg"},
        ]},
        tmp_path,
    )
    assert result is None
    assert existing.read_bytes() == b"already"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["cover.jpg"]


class _InventoryPage:
    def __init__(self, responses, url: str):
        self.url = url
        self.responses = responses
        self.handlers = []
        self.evaluations = 0

    def on(self, event, handler):
        assert event == "response"
        self.handlers.append(handler)

    async def goto(self, _url, **_kwargs):
        for response in self.responses:
            for handler in self.handlers:
                await handler(response)
        return SimpleNamespace(status=200)

    async def wait_for_timeout(self, _ms):
        return None

    async def evaluate(self, _script):
        self.evaluations += 1

    def locator(self, _selector):
        return self

    async def inner_text(self, timeout=None):
        return "作者主页"

    async def title(self):
        return "作者的抖音 - 抖音"


class _JsonResponse:
    def __init__(self, url: str, payload):
        self.url = url
        self.headers = {"content-type": "application/json"}
        self.status = 200
        self.payload = payload

    async def json(self):
        return self.payload


async def test_creator_preview_and_avatar_share_the_guarded_download(tmp_path: Path) -> None:
    sec_uid = "MS4wLjABAAAAtestcreator"
    profile_url = f"https://www.douyin.com/user/{sec_uid}"
    bad = "https://p3.douyinpic.com/preview-bad.jpg?x-signature=keep"
    good = "https://p9.byteimg.com/preview-good.jpg"
    avatar = "https://img.pstatp.com/avatar.jpg"
    routes = {
        bad: Step(status=302, location="http://127.0.0.1/secret.jpg"),
        good: Step(headers={"content-type": "image/jpeg"}, body=b"good-preview"),
        avatar: Step(headers={"content-type": "image/png"}, body=PNG),
    }
    request = PlaywrightRequest(routes)
    first_id = "1111111111111"
    existing = tmp_path / f"001-{first_id}.jpg"
    existing.write_bytes(b"old-preview")
    awemes = []
    for work_id, cover in ((first_id, bad), ("2222222222222", good)):
        awemes.append({
            "aweme_id": work_id,
            "desc": "作品",
            "create_time": 1_700_000_000,
            "author": {
                "sec_uid": sec_uid,
                "nickname": "作者",
                "avatar_larger": {"url_list": [avatar]},
            },
            "video": {"duration": 1000, "cover": {"url_list": [cover]}},
        })
    page = _InventoryPage(
        [
            _JsonResponse(
                "https://www.douyin.com/aweme/v1/web/aweme/post/",
                {"aweme_list": awemes, "has_more": False},
            )
        ],
        profile_url,
    )
    adapter = DouyinCreatorAdapter(MediaSettings(), tmp_path / "profile")
    result = await adapter._inventory_profile(
        SimpleNamespace(request=request),
        page,
        profile_url,
        original_url=profile_url,
        target_dir=tmp_path,
    )
    assert page.evaluations == 0
    assert [work.thumbnail_path for work in result.works] == [
        None,
        str(tmp_path / "002-2222222222222.jpg"),
    ]
    assert existing.read_bytes() == b"old-preview"
    assert Path(result.works[1].thumbnail_path).read_bytes() == b"good-preview"
    assert result.profile.avatar_path == str(tmp_path / "avatar.png")
    assert Path(result.profile.avatar_path).read_bytes() == PNG
    assert request.urls == [bad, good, avatar]
    assert "127.0.0.1" not in _hosts(request.urls)
    assert {call["max_redirects"] for call in request.calls} == {0}
    assert all(response.disposed for response in request.responses)


async def test_creator_preview_rejects_oversized_and_non_image_without_overwrite(
    tmp_path: Path,
) -> None:
    adapter = DouyinCreatorAdapter(MediaSettings(), tmp_path / "profile")
    huge = "https://p3.douyinpic.com/huge.jpg"
    html = "https://p3.douyinpic.com/page.jpg"
    routes = {
        huge: Step(
            headers={"content-type": "image/jpeg"},
            body=b"a" * (12 * 1024 * 1024 + 1),
        ),
        html: Step(headers={"content-type": "text/html"}, body=b"<html>no</html>"),
    }
    request = PlaywrightRequest(routes)
    existing = tmp_path / "avatar.jpg"
    existing.write_bytes(b"old-avatar")
    context = SimpleNamespace(request=request)
    assert await adapter._download_preview(context, huge, tmp_path / "avatar") is None
    assert await adapter._download_preview(context, html, tmp_path / "avatar") is None
    assert existing.read_bytes() == b"old-avatar"
    assert request.urls == [huge, html]


@pytest.mark.parametrize(
    "failure",
    [
        Step(status=302, location="http://127.0.0.1/secret.jpg"),
        Step(headers={"content-type": "text/html"}, body=b"<html>no</html>"),
        Step(body=JPEG),
        Step(headers={"content-type": "application/octet-stream"}, body=JPEG),
        Step(headers={"content-type": "image/jpeg"}, body=b""),
        Step(status=404, headers={"content-type": "image/jpeg"}, body=JPEG),
    ],
)
async def test_image_note_failure_publishes_nothing_and_clears_staging(
    tmp_path: Path, failure: Step
) -> None:
    first = "https://p3.douyinpic.com/one.jpg"
    second = "https://p9.byteimg.com/two.jpg"
    third = "https://img.pstatp.com/three.jpg"
    request = PlaywrightRequest({
        first: Step(headers={"content-type": "image/jpeg"}, body=b"first-image"),
        second: failure,
        third: Step(headers={"content-type": "image/jpeg"}, body=b"third-image"),
    })
    target = tmp_path / "images"
    target.mkdir()
    existing = target / "001.jpg"
    existing.write_bytes(b"original")
    downloader = PlaywrightImageNoteDownloader(MediaSettings(), tmp_path / "profile")
    with pytest.raises(ExternalToolError, match="第 2 张"):
        await downloader._download_images(
            SimpleNamespace(request=request), [first, second, third], target
        )
    assert existing.read_bytes() == b"original"
    assert sorted(path.name for path in target.iterdir()) == ["001.jpg"]
    assert not list(tmp_path.glob(".images.staging-*"))
    assert request.urls == [first, second]
    assert "127.0.0.1" not in _hosts(request.urls)
    assert all(response.disposed for response in request.responses)


@pytest.mark.parametrize("mode", ["get", "body"])
async def test_image_note_transport_failure_raises_external_tool_error(
    tmp_path: Path, mode: str
) -> None:
    first = "https://p3.douyinpic.com/one.jpg"
    second = "https://p9.byteimg.com/two.jpg?x-signature=super-secret-value"
    third = "https://img.pstatp.com/three.jpg"
    failure = Step(headers={"content-type": "image/jpeg"}, body=JPEG)
    if mode == "get":
        failure.get_error = RuntimeError("socket down")
    else:
        failure.body_error = RuntimeError("body failed")
    request = PlaywrightRequest({
        first: Step(headers={"content-type": "image/jpeg"}, body=b"first-image"),
        second: failure,
        third: Step(headers={"content-type": "image/jpeg"}, body=b"third-image"),
    })
    target = tmp_path / "images"
    target.mkdir()
    existing = target / "001.jpg"
    existing.write_bytes(b"original")
    downloader = PlaywrightImageNoteDownloader(MediaSettings(), tmp_path / "profile")
    with pytest.raises(ExternalToolError, match="第 2 张图片下载失败") as caught:
        await downloader._download_images(
            SimpleNamespace(request=request), [first, second, third], target
        )
    assert type(caught.value) is ExternalToolError
    assert caught.value.details == {}
    rendered = f"{caught.value} {caught.value.details}"
    assert "super-secret-value" not in rendered
    assert "cookie" not in rendered.lower()
    assert existing.read_bytes() == b"original"
    assert sorted(path.name for path in target.iterdir()) == ["001.jpg"]
    assert not list(tmp_path.glob(".images.staging-*"))
    assert request.urls == [first, second]
    assert [call["timeout"] for call in request.calls] == [60_000, 60_000]
    assert {call["max_redirects"] for call in request.calls} == {0}
    assert all(response.disposed for response in request.responses)


async def test_image_note_publishes_only_after_every_image_succeeds(tmp_path: Path) -> None:
    urls = [
        "https://p3.douyinpic.com/one.jpg?x-signature=ab%2B",
        "https://img.pstatp.com/two.png",
    ]
    request = PlaywrightRequest({
        urls[0]: Step(headers={"content-type": "image/jpeg"}, body=b"one"),
        urls[1]: Step(headers={"content-type": "image/png"}, body=PNG),
    })
    target = tmp_path / "images"
    downloader = PlaywrightImageNoteDownloader(MediaSettings(), tmp_path / "profile")
    paths = await downloader._download_images(SimpleNamespace(request=request), urls, target)
    assert [path.name for path in paths] == ["001.jpg", "002.png"]
    assert paths[0].read_bytes() == b"one"
    assert paths[1].read_bytes() == PNG
    assert request.urls == urls
    assert not list(tmp_path.glob(".images.staging-*"))
    assert all(response.disposed for response in request.responses)
