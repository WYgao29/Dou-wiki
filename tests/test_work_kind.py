from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from douyin_wiki.adapters.image_note import PlaywrightImageNoteDownloader
from douyin_wiki.adapters.share import DouyinShareResolver
from douyin_wiki.config import MediaSettings
from douyin_wiki.errors import BrowserAuthRequiredError, InvalidShareTextError
from douyin_wiki.models import JobStatus, SourceKind
from douyin_wiki.worker import Worker
from tests.support.gateway import complete_gateway_job
from tests.test_image_note import WORK_ID, make_service

MODAL_URL = f"https://www.douyin.com/user/self?modal_id={WORK_ID}&showTab=favorite_collection"


@pytest.mark.parametrize("parameter", ["modal_id", "aweme_id", "item_id"])
async def test_query_work_id_does_not_imply_video(parameter):
    def no_network(request):
        raise AssertionError("work id extraction should not fetch the page")

    resolver = DouyinShareResolver(transport=httpx.MockTransport(no_network))
    result = await resolver.resolve(f"https://www.douyin.com/user/self?{parameter}={WORK_ID}")
    assert result.video_id == WORK_ID
    assert result.source_kind is None
    assert "/video/" not in result.canonical_url


async def test_untyped_final_redirect_keeps_confirmed_note_route():
    locations = iter((f"https://www.douyin.com/note/{WORK_ID}", MODAL_URL))

    def handler(request):
        location = next(locations, None)
        return (
            httpx.Response(302, headers={"location": location}) if location else httpx.Response(200)
        )

    result = await DouyinShareResolver(transport=httpx.MockTransport(handler)).resolve(
        "https://v.douyin.com/example/"
    )
    assert result.source_kind == SourceKind.IMAGE_NOTE
    assert result.canonical_url == f"https://www.douyin.com/note/{WORK_ID}"


async def test_profile_modal_is_a_work_rather_than_a_creator():
    resolver = DouyinShareResolver()
    url = f"https://www.douyin.com/user/long-sec-uid-123456?modal_id={WORK_ID}"
    assert await resolver.classify_target(url) == ("work", url)


@pytest.mark.parametrize("legacy_checkpoint", [False, True])
async def test_modal_image_ingestion_never_invokes_video_tools(tmp_path, legacy_checkpoint):
    service = make_service(tmp_path)
    service.resolver = DouyinShareResolver()
    probed = []

    async def identify(url, work_id):
        probed.append((url, work_id))
        return SourceKind.IMAGE_NOTE

    service.image_note_downloader.resolve_source_kind = identify
    job = service.capture_douyin(MODAL_URL)
    if legacy_checkpoint:
        service.database.update_job(job.id, status=JobStatus.FAILED, artifacts={"resolved": {
            "original_url": MODAL_URL, "canonical_url": f"https://www.douyin.com/video/{WORK_ID}",
            "video_id": WORK_ID, "redirect_chain": [MODAL_URL], "source_kind": "video",
        }})
        assert service.retry_job(job.id).id == job.id
    paused = await Worker(service).run_once()
    assert paused.status == JobStatus.AWAITING_AGENT_ANALYSIS
    assert paused.result["phase"] == "analysis"
    assert probed == [(MODAL_URL, WORK_ID)]
    resolved = service.get_job(job.id).artifacts["resolved"]
    assert resolved["source_kind"] == "image_note"
    assert resolved["source_kind_confirmed"] is True
    assert resolved["canonical_url"] == f"https://www.douyin.com/note/{WORK_ID}"
    completed = await complete_gateway_job(service, job.id)
    assert completed.status == JobStatus.COMPLETED
    assert completed.result["image_count"] == 3
    assert len(service.database.list_jobs()) == 1
    data = service.database.get_entry_data(completed.result["entry_id"])
    assert "transcript_raw" not in data and len(data["ocr"]) == 3
    assert service.database.get_entry(completed.result["entry_id"]).retention.value == "keep"


async def test_modal_video_still_uses_the_video_pipeline(service):
    service.resolver = DouyinShareResolver()

    async def identify(url, work_id):
        return SourceKind.VIDEO

    service.image_note_downloader.resolve_source_kind = identify
    job = service.capture_douyin(MODAL_URL)
    paused = await Worker(service).run_once()
    assert paused.status == JobStatus.AWAITING_AGENT_ANALYSIS
    assert paused.result["phase"] == "transcript_correction"
    assert service.downloader.calls == 1
    assert service.get_job(job.id).artifacts["resolved"]["source_kind_confirmed"] is True


async def test_existing_image_entry_repairs_legacy_checkpoint_without_network(tmp_path):
    service = make_service(tmp_path)
    original = service.capture_douyin(f"https://www.douyin.com/note/{WORK_ID}")
    await Worker(service).run_once()
    await complete_gateway_job(service, original.id)
    service.resolver = DouyinShareResolver()
    job = service.capture_douyin(MODAL_URL)
    service.database.update_job(job.id, artifacts={"resolved": {
        "original_url": MODAL_URL, "canonical_url": f"https://www.douyin.com/video/{WORK_ID}",
        "video_id": WORK_ID, "redirect_chain": [MODAL_URL], "source_kind": "video",
    }})

    async def no_probe(*args):
        raise AssertionError("existing typed metadata should avoid a browser probe")

    service.image_note_downloader.resolve_source_kind = no_probe
    completed = await Worker(service).run_once()
    assert completed.status == JobStatus.COMPLETED and completed.result["duplicate"] is True
    assert service.get_job(job.id).artifacts["resolved"]["source_kind"] == "image_note"


def fake_probe_browser(monkeypatch, payload, *, status=200, text="作品正文"):
    closed = []

    class Page:
        url = MODAL_URL
        callback = None

        def on(self, event, callback):
            self.callback = callback

        async def goto(self, url, **kwargs):
            async def json_payload():
                return payload
            await self.callback(SimpleNamespace(
                url="https://www.douyin.com/aweme/v1/web/aweme/detail/",
                headers={"content-type": "application/json"}, json=json_payload,
            ))
            return SimpleNamespace(status=status)

        async def wait_for_timeout(self, ms):
            await asyncio.sleep(0)

        def locator(self, selector):
            async def inner_text(**kwargs):
                return text
            return SimpleNamespace(inner_text=inner_text)

    class Context:
        pages = [Page()]

        async def close(self):
            closed.append(True)

    class Playwright:
        async def __aenter__(self):
            async def launch(**kwargs):
                return Context()
            return SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch))

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr("playwright.async_api.async_playwright", Playwright)
    return closed


@pytest.mark.parametrize("kind", [SourceKind.IMAGE_NOTE, SourceKind.VIDEO])
async def test_browser_type_probe_matches_the_requested_work(tmp_path, monkeypatch, kind):
    detail = {"aweme_id": WORK_ID, **(
        {"images": [{"url_list": ["https://p3.douyinpic.com/image.webp"]}],
         "video": {"cover": {"url_list": ["https://p3.douyinpic.com/cover.webp"]}}}
        if kind == SourceKind.IMAGE_NOTE else {"video": {"play_addr": {"uri": "target"}}}
    )}
    payload = {"recommendations": [{"aweme_id": "9999999999999999999", "images": [{}]}],
               "aweme_detail": detail}
    closed = fake_probe_browser(monkeypatch, payload)
    downloader = PlaywrightImageNoteDownloader(MediaSettings(), tmp_path / "profile")
    assert await downloader.resolve_source_kind(MODAL_URL, WORK_ID) == kind
    assert closed == [True]


@pytest.mark.parametrize("auth_wall", [False, True])
@pytest.mark.parametrize("partial_target", [False, True])
async def test_probe_does_not_guess_from_recommendations_or_login(
    tmp_path, monkeypatch, auth_wall, partial_target
):
    payload = {"aweme_detail": {"aweme_id": "9999999999999999999", "images": [{}]}}
    if partial_target:
        payload["target"] = {"aweme_id": WORK_ID, "video": {"cover": {"uri": "cover-only"}}}
    closed = fake_probe_browser(
        monkeypatch, payload,
        status=403 if auth_wall else 200, text="请登录" if auth_wall else "作品正文",
    )
    downloader = PlaywrightImageNoteDownloader(MediaSettings(), tmp_path / "profile")
    expected = BrowserAuthRequiredError if auth_wall else InvalidShareTextError
    with pytest.raises(expected):
        await downloader.resolve_source_kind(MODAL_URL, WORK_ID)
    assert closed == [True]
