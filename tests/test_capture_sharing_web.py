"""Shared callers can see and reach their executor through the public Web API."""

import pytest

from douyin_wiki.errors import CookieRequiredError
from douyin_wiki.models import AnalysisMode, GatewayContext, InspirationInput, JobStatus
from douyin_wiki.webapp.app import create_app
from douyin_wiki.worker import Worker
from tests.conftest import FakeResolver
from tests.support.web import local_client
from tests.test_capture_sharing import SHORT, URL


async def shared_pair(service, pause):
    service.config.analysis_mode = AnalysisMode.GATEWAY
    service.resolver = FakeResolver()
    if pause == JobStatus.NEEDS_AUTH:
        class UnauthorizedDownloader:
            async def download(self, *_args):
                raise CookieRequiredError("视频下载授权已失效")

        service.downloader = UnauthorizedDownloader()
    owner = service.capture_douyin(URL)
    assert (await Worker(service).run_once()).status == pause
    context = GatewayContext(gateway="caller", conversation_id="shared-web")
    follower = service.capture_douyin(
        SHORT, inspirations=[InspirationInput(text="保留等待者的灵感")], gateway_context=context
    )
    assert (await Worker(service).run_once()).status == JobStatus.MONITORING
    return owner.id, follower.id


@pytest.mark.parametrize(
    "pause,stage,message,requires_action",
    [
        (JobStatus.AWAITING_AGENT_ANALYSIS, "analyze", "需要外部 Agent 接续", False),
        (JobStatus.NEEDS_AUTH, "download", "视频下载授权已失效", True),
    ],
)
async def test_shared_waiter_http_shows_pause_and_links_executor(
    service, pause, stage, message, requires_action
):
    owner, follower = await shared_pair(service, pause)
    app = create_app(service.config, service=service, start_watcher=False)
    with local_client(app) as client:
        detail = client.get(f"/api/jobs/{follower}").json()
        listing = client.get("/api/jobs").json()
        active = client.get("/api/jobs", params={"status": "in_progress"}).json()
        todo = client.get("/api/jobs", params={"requires_user_action": True}).json()
    listed = next(item for item in listing["items"] if item["id"] == follower)
    for payload in (detail, listed):
        assert payload["id"] == follower and payload["status"] == JobStatus.MONITORING.value
        assert payload["stage"] == stage
        assert message in payload["message_for_user"]
        assert payload["next_action"] == {
            "code": "view_shared_capture", "label": "查看执行任务", "href": f"/jobs/{owner}"
        }
        assert payload["requires_user_action"] is requires_action
        assert payload["auth_scope"] == ("video" if requires_action else None)
        assert payload["share_text"] == SHORT
        assert payload["inspirations"][0]["text"] == "保留等待者的灵感"
        assert payload["can_cancel"] and not payload["retryable"]
    assert follower not in {item["id"] for item in active["items"]}
    assert (follower in {item["id"] for item in todo["items"]}) is requires_action
    caller = service.database.get_job(follower).request.gateway_context
    assert caller.conversation_id == "shared-web"


@pytest.mark.parametrize("status", [JobStatus.QUEUED, JobStatus.ANALYZING])
async def test_shared_waiter_http_follows_current_executor_stage_and_progress(service, status):
    owner, follower = await shared_pair(service, JobStatus.AWAITING_AGENT_ANALYSIS)
    service.database.update_job(
        owner, status=status, progress=0.63,
        artifacts={"analysis_progress": {"phase": "correction"}},
    )
    app = create_app(service.config, service=service, start_watcher=False)
    with local_client(app) as client:
        detail = client.get(f"/api/jobs/{follower}").json()
        root = client.get(f"/api/jobs/{owner}").json()
        active = client.get("/api/jobs", params={"status": "in_progress"}).json()
    assert detail["stage"] == root["stage"]
    assert detail["stage_label"] == root["stage_label"]
    assert detail["progress"] == root["progress"] == 0.63
    assert root["message_for_user"] in detail["message_for_user"]
    assert detail["next_action"]["href"] == f"/jobs/{owner}"
    assert (follower in {item["id"] for item in active["items"]}) is (
        status == JobStatus.ANALYZING
    )


@pytest.mark.parametrize("delete", [False, True])
async def test_cancelled_shared_waiter_http_stops_showing_executor_actions(service, delete):
    owner, follower = await shared_pair(service, JobStatus.NEEDS_AUTH)
    service.cancel_job(follower)
    if delete:
        service.delete_job(follower)
    app = create_app(service.config, service=service, start_watcher=False)
    with local_client(app) as client:
        response = client.get(f"/api/jobs/{follower}")
        listing = client.get("/api/jobs").json()
    assert service.get_job(owner).status == JobStatus.NEEDS_AUTH
    if delete:
        assert response.status_code == 404
        assert follower not in {item["id"] for item in listing["items"]}
    else:
        detail = response.json()
        assert detail["status"] == JobStatus.CANCELLED.value
        assert detail["stage"] == "done" and not detail["requires_user_action"]
        assert detail["next_action"] is None and detail["auth_scope"] is None
        assert "视频下载授权已失效" not in detail["message_for_user"]
