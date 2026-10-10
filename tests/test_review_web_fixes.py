from __future__ import annotations

import asyncio

import pytest

from douyin_wiki.errors import ExternalToolError
from douyin_wiki.models import AuthCheckResult, CaptureRequest, JobStatus
from douyin_wiki.webapp.app import create_app
from scripts.verify_review_web_races import verify_case
from tests.support.web import _web_fixture


@pytest.mark.parametrize("channel", ["douyin", "video"])
@pytest.mark.parametrize("boundary", ["initial", "login", "verification", "error"])
async def test_foreign_auth_cancel_wins_at_await_boundaries(
    tmp_path, monkeypatch, channel, boundary
):
    monkeypatch.setenv("DOUYIN_WIKI_BROWSER_PROFILE", str(tmp_path / "profile"))
    config, service = _web_fixture(tmp_path)
    scope = "image_note" if channel == "douyin" else "video"
    job = service.database.create_job(
        CaptureRequest(share_text="https://www.douyin.com/video/123"),
        status=JobStatus.NEEDS_AUTH,
    )
    service.database.update_job(job.id, result={"auth_scope": scope}, unlock=True)
    entered, release = asyncio.Event(), asyncio.Event()
    logged_in = False

    async def check(auth_scope, **kwargs):
        if boundary == "initial" and not entered.is_set():
            entered.set()
            await release.wait()
        if boundary == "verification" and logged_in and not entered.is_set():
            entered.set()
            await release.wait()
        ready = logged_in or boundary == "initial"
        return AuthCheckResult(
            scope=auth_scope,
            state="ready" if ready else "needs_login",
            ok=ready,
            server_verified=ready,
            cookie_source="fake",
            message="fake",
        )

    async def authenticate(**kwargs):
        nonlocal logged_in
        if boundary in {"login", "error"}:
            entered.set()
            await release.wait()
        if boundary == "error":
            raise ExternalToolError("模拟授权失败")
        logged_in = True

    monkeypatch.setattr(service, "check_auth_scope", check)
    monkeypatch.setattr(service, "authenticate_douyin", authenticate)
    monkeypatch.setattr(service, "authenticate_video", authenticate)
    apps = [
        create_app(config, service=service,  start_watcher=False)
        for _ in range(2)
    ]
    first, second = (app.state.operations.auth for app in apps)
    session = await first.start(channel, trigger_job_id=job.id)
    try:
        await asyncio.wait_for(entered.wait(), 2)
        reused = await second.start(channel)
        assert reused["id"] == session["id"]
        cancelled = second.cancel(session["id"])
        assert cancelled["stage"] == "cancelled"
        task = first._tasks[session["id"]]
        release.set()
        await asyncio.wait_for(task, 2)
        assert service.database.get_auth_session(session["id"])["stage"] == "cancelled"
        assert service.get_job(job.id).status == JobStatus.NEEDS_AUTH
    finally:
        release.set()
        await first.close()
        await second.close()


async def test_foreign_cancel_after_success_claim_preserves_retry_result(tmp_path, monkeypatch):
    monkeypatch.setenv("DOUYIN_WIKI_BROWSER_PROFILE", str(tmp_path / "profile"))
    config, service = _web_fixture(tmp_path)
    job = service.database.create_job(
        CaptureRequest(share_text="https://www.douyin.com/video/123"),
        status=JobStatus.NEEDS_AUTH,
    )
    service.database.update_job(job.id, result={"auth_scope": "video"}, unlock=True)

    async def check(scope, **kwargs):
        return AuthCheckResult(
            scope=scope,
            state="ready",
            ok=True,
            server_verified=True,
            cookie_source="fake",
            message="fake",
        )

    monkeypatch.setattr(service, "check_auth_scope", check)
    apps = [create_app(config, service=service, start_watcher=False) for _ in range(2)]
    first, second = (app.state.operations.auth for app in apps)
    retry_job = service.retry_job
    cancellations = []

    def retry(job_id):
        cancellations.append(second.cancel(session["id"]))
        return retry_job(job_id)

    monkeypatch.setattr(service, "retry_job", retry)
    session = await first.start("video", trigger_job_id=job.id)
    try:
        await first._tasks[session["id"]]
        assert cancellations[0]["stage"] == "succeeded"
        assert service.get_job(job.id).status == JobStatus.QUEUED
        assert first.get(session["id"])["retried_job_ids"] == [job.id]
    finally:
        await first.close()
        await second.close()


@pytest.mark.parametrize(
    "case",
    [
        "library-new-first",
        "library-old-first",
        "library-query-before-debounce",
        "library-leave-imports",
        "library-stale-error",
        "topic-new-first",
        "topic-old-first",
        "topic-leave-imports",
        "topic-popstate",
        "topic-stale-404",
    ],
)
async def test_browser_discards_stale_responses(case):
    await verify_case(case)


async def test_owner_close_stops_login_task_after_foreign_manager_cancels(tmp_path, monkeypatch):
    from douyin_wiki.web_auth import WebAuthManager

    monkeypatch.setenv("DOUYIN_WIKI_BROWSER_PROFILE", str(tmp_path / "profile"))
    _, service = _web_fixture(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()

    async def check(scope, **_kwargs):
        return AuthCheckResult(
            scope=scope,
            state="needs_login",
            ok=False,
            cookie_source="fake",
            message="login required",
        )

    async def login(**_kwargs):
        entered.set()
        await release.wait()

    monkeypatch.setattr(service, "check_auth_scope", check)
    monkeypatch.setattr(service, "authenticate_douyin", login)
    owner, foreign = WebAuthManager(service), WebAuthManager(service)
    session = await owner.start("douyin")
    try:
        await asyncio.wait_for(entered.wait(), 2)
        task = owner._tasks[session["id"]]
        assert foreign.cancel(session["id"])["stage"] == "cancelled"
        await asyncio.wait_for(owner.close(), 0.3)
        assert task.done()
        assert service.database.get_auth_session(session["id"])["stage"] == "cancelled"
        assert owner._locks == {}
    finally:
        release.set()
        await owner.close()
        await foreign.close()
