from __future__ import annotations

import asyncio
import subprocess
import threading
from pathlib import Path

import httpx
import pytest

from douyin_wiki.adapters.media import (
    CapturedCdnMedia,
    YtDlpDownloader,
    _info_from_aweme_detail,
    download_preferred_cover,
)
from douyin_wiki.config import MediaSettings


@pytest.fixture
def cover_download(monkeypatch, tmp_path):
    urls = {
        "cover": "https://example.test/custom.jpg",
        "origin_cover": "https://example.test/origin.jpg",
        "dynamic_cover": "https://example.test/dynamic.webp",
    }
    detail = {
        "aweme_id": "1234567890123",
        "desc": "测试作品",
        "author": {"sec_uid": "creator123", "nickname": "作者"},
        "video": {key: {"url_list": [url]} for key, url in urls.items()},
    }
    downloader = YtDlpDownloader(MediaSettings(), tmp_path / "browser")

    async def captured(_url):
        return CapturedCdnMedia(
            video_url="https://example.test/video.mp4",
            audio_url=None,
            page_title="测试作品",
            info=_info_from_aweme_detail(detail),
        )

    def run(command, **kwargs):
        output = command[command.index("--output") + 1].replace("%(ext)s", "mp4")
        Path(output).write_bytes(b"video")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(downloader, "_capture_cdn_url", captured)
    monkeypatch.setattr("douyin_wiki.adapters.media._run", run)
    return downloader, detail, urls


def install_cover_client(monkeypatch, get):
    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url):
            return get(url)

    monkeypatch.setattr("douyin_wiki.adapters.media.httpx.Client", Client)


def cover_response(url):
    content = b"RIFF\x00\x00\x00\x00WEBPimage" if url.endswith("webp") else b"\xff\xd8\xffimage"
    return httpx.Response(
        200,
        request=httpx.Request("GET", url),
        headers={"content-type": "image/webp" if url.endswith("webp") else "image/jpeg"},
        content=content,
    )


@pytest.mark.asyncio
async def test_cover_network_wait_allows_worker_heartbeat_to_continue(
    monkeypatch, tmp_path, cover_download
):
    downloader, _detail, _urls = cover_download
    request_started = threading.Event()
    heartbeat_ran = threading.Event()
    continued_during_request = []

    def get(url):
        request_started.set()
        continued_during_request.append(heartbeat_ran.wait(timeout=2))
        return cover_response(url)

    install_cover_client(monkeypatch, get)

    async def heartbeat():
        while not request_started.is_set():
            await asyncio.sleep(0.001)
        heartbeat_ran.set()

    await asyncio.gather(
        heartbeat(),
        downloader.download(
            "https://www.douyin.com/video/1234567890123", "1234567890123", tmp_path
        ),
    )
    assert continued_during_request == [True]


@pytest.mark.parametrize(
    ("missing", "failed", "expected_requests", "expected_kind"),
    [
        ([], [], ["cover"], "douyin_cover"),
        ([], ["cover"], ["cover", "origin_cover"], "origin_cover"),
        (
            [], ["cover", "origin_cover"],
            ["cover", "origin_cover", "dynamic_cover"], "dynamic_cover",
        ),
        (["cover"], [], ["origin_cover"], "origin_cover"),
        (["cover", "origin_cover"], [], ["dynamic_cover"], "dynamic_cover"),
    ],
)
@pytest.mark.asyncio
async def test_aweme_cover_priority_and_fallback_keep_actual_kind(
    monkeypatch, tmp_path, cover_download, missing, failed, expected_requests, expected_kind
):
    downloader, detail, urls = cover_download
    for key in missing:
        detail["video"].pop(key)
    requested = []

    def get(url):
        requested.append(url)
        if url in [urls[key] for key in failed]:
            raise httpx.ConnectError("cover unavailable")
        return cover_response(url)

    install_cover_client(monkeypatch, get)
    metadata = await downloader.download(
        "https://www.douyin.com/video/1234567890123", "1234567890123", tmp_path
    )

    assert requested == [urls[key] for key in expected_requests]
    assert metadata.thumbnail_kind == expected_kind
    assert Path(metadata.thumbnail_path).read_bytes() == cover_response(requested[-1]).content
    if expected_kind == "dynamic_cover":
        assert Path(metadata.thumbnail_path).suffix == ".webp"


def test_static_cover_helper_does_not_label_fallback_as_independent_cover(monkeypatch, tmp_path):
    requested = []

    def get(url):
        requested.append(url)
        raise httpx.ConnectError("cover unavailable")

    install_cover_client(monkeypatch, get)
    result = download_preferred_cover(
        {"thumbnails": [
            {"id": "cover", "url": "https://example.test/custom.jpg"},
            {"id": "origin_cover", "url": "https://example.test/origin.jpg"},
        ]},
        tmp_path,
    )
    assert result is None
    assert requested == ["https://example.test/custom.jpg"]


@pytest.mark.parametrize(
    ("cached_files", "expected_file", "expected_kind"),
    [
        ({"cover.jpg": b"custom", "original.jpg": b"larger original"},
         "cover.jpg", "douyin_cover"),
        ({"origin_cover.jpg": b"origin"}, "origin_cover.jpg", "origin_cover"),
        ({"dynamic_cover.webp": b"dynamic"}, "dynamic_cover.webp", "dynamic_cover"),
        ({"original.jpg": b"original"}, "original.jpg", "origin_cover"),
    ],
)
@pytest.mark.asyncio
async def test_download_retry_reuses_cached_cover_with_its_actual_kind(
    monkeypatch, tmp_path, cover_download, cached_files, expected_file, expected_kind
):
    downloader, _detail, _urls = cover_download
    for name, content in cached_files.items():
        (tmp_path / name).write_bytes(content)

    def get(url):
        raise httpx.ConnectError("cover unavailable")

    install_cover_client(monkeypatch, get)
    metadata = await downloader.download(
        "https://www.douyin.com/video/1234567890123", "1234567890123", tmp_path
    )
    assert metadata.thumbnail_path == str(tmp_path / expected_file)
    assert metadata.thumbnail_kind == expected_kind


@pytest.mark.parametrize(
    ("cached_file", "failed_kinds", "expected_kind"),
    [
        ("cover.jpg", ["cover"], "douyin_cover"),
        ("origin_cover.jpg", ["cover", "origin_cover"], "origin_cover"),
        ("original.jpg", ["cover", "origin_cover"], "origin_cover"),
    ],
)
async def test_cached_cover_precedes_successful_lower_priority_remote_cover(
    monkeypatch, tmp_path, cover_download, cached_file, failed_kinds, expected_kind
):
    downloader, _detail, urls = cover_download
    cached = tmp_path / cached_file
    cached.write_bytes(b"cached-higher-priority-cover")
    requested = []

    def get(url):
        requested.append(url)
        if url in [urls[key] for key in failed_kinds]:
            raise httpx.ConnectError("higher priority cover unavailable")
        return cover_response(url)

    install_cover_client(monkeypatch, get)
    metadata = await downloader.download(
        "https://www.douyin.com/video/1234567890123", "1234567890123", tmp_path
    )
    assert metadata.thumbnail_path == str(cached)
    assert metadata.thumbnail_kind == expected_kind
    assert requested == [urls[key] for key in failed_kinds]
