"""Offline regressions for the reviewed deployment feedback."""

import json
import subprocess
import sys
from types import SimpleNamespace

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from douyin_wiki import mcp_server
from douyin_wiki.adapters.embeddings import EmbeddingService
from douyin_wiki.config import EmbeddingSettings
from douyin_wiki.gateway_protocol import context_revision
from douyin_wiki.models import CreatorInventoryWork, CreatorProfile, RetentionPolicy
from tests.support.gateway import make_gateway_job


@pytest.mark.parametrize(
    "analysis",
    [
        {"title": "资料", "takeaways": [str(i) for i in range(6)]},
        {"title": "资料", "chapters": [None] * 13},
        {"title": "资料", "takeaways": 42},
    ],
)
async def test_invalid_analysis_is_actionable_and_does_not_change_job(
    service, monkeypatch, analysis
):
    job = make_gateway_job(service, phase="analysis")
    monkeypatch.setattr(mcp_server, "_SERVICE", service)
    with pytest.raises(ToolError) as caught:
        await mcp_server.mcp.call_tool(
            "submit_gateway_analysis",
            {
                "job_id": job.id,
                "analysis": analysis,
                "producer": "test",
                "gateway_protocol": 2,
                "context_revision": context_revision(job),
            },
        )
    error = json.loads(str(caught.value))["error"]
    assert error["code"] == "invalid_analysis"
    assert error["details"]["errors"][0]["path"] in {"takeaways", "chapters"}
    assert service.database.get_job(job.id) == job


def test_creator_sync_keeps_explicit_retention(service):
    creator = service.database.upsert_creator(
        CreatorProfile(
            sec_uid="creator-secuid",
            canonical_url="https://www.douyin.com/user/creator",
            original_url="https://www.douyin.com/user/creator",
            nickname="博主",
        ),
        creator_id="creator",
        folder_path="creators/博主",
        inspirations=[],
    )
    job = service.sync_creator(creator.id, retention=RetentionPolicy.KEEP)
    assert job.request.options.retention == RetentionPolicy.KEEP
    assert service.sync_creator(creator.id).request.options.retention == RetentionPolicy.TEMPORARY
    service.database.register_creator_work(
        creator.id,
        CreatorInventoryWork(
            work_id="1234567890123456789",
            source_kind="video",
            title="视频",
            canonical_url="https://www.douyin.com/video/1234567890123456789",
            original_url="https://www.douyin.com/video/1234567890123456789",
        ),
    )
    imported = service.import_creator_works(
        creator.id,
        ["1234567890123456789"],
        retention=RetentionPolicy.KEEP,
    )
    children = service.database.batch_child_ids(imported.id)
    assert len(children) == 1
    assert service.database.get_job(children[0]).request.options.retention == RetentionPolicy.KEEP


def test_embedding_load_is_local_only(monkeypatch):
    calls = []

    def load(model, **kwargs):
        calls.append(kwargs)
        assert kwargs.get("local_files_only") is True
        raise FileNotFoundError("not cached")

    monkeypatch.setitem(
        sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=load)
    )
    embeddings = EmbeddingService(EmbeddingSettings())
    assert embeddings.embed(["资料"])
    assert calls == [{"local_files_only": True}]
    assert embeddings.provider_name == "char-ngram-fallback"
    embeddings.embed(["第二次查询"])
    assert len(calls) == 1


def test_date_migration_does_not_reformat_existing_date_only_page(service):
    path = service.config.vault_path / "wiki/concepts/日期.md"
    original = "---\ntype: concept\ncreated: 2026-10-16\nupdated: 2026-10-16\n---\n\n# 日期\n"
    path.write_text(original)
    assert path not in service.vault.migrate_visible_times_to_beijing()
    assert path.read_text() == original


def test_new_concept_page_survives_initialization_without_reformatting(service):
    path = service.config.vault_path / "wiki/concepts/概念.md"
    service.vault._ensure_link_page(
        path,
        "concept",
        "概念",
        SimpleNamespace(source_path="wiki/sources/example.md", title="资料"),
    )
    original = path.read_bytes()
    service.initialize_runtime()
    assert path.read_bytes() == original


def test_browser_health_detects_missing_binary_without_using_login_profile(tmp_path, monkeypatch):
    from douyin_wiki.adapters.browser_health import playwright_health

    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "empty-cache"))
    result = playwright_health()
    assert result["ok"] is False
    assert "install chromium" in result["message"]
    assert not (tmp_path / "empty-cache").exists()


def test_git_commit_failure_exposes_reason_without_removing_documents(service, monkeypatch):
    path = service.config.vault_path / "test.md"
    path.write_text("资料已保存")
    (service.config.vault_path / ".git").mkdir()

    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ["git", "rev-parse"])

    monkeypatch.setattr("douyin_wiki.vault.subprocess.run", fail)
    with pytest.warns(RuntimeWarning, match="Git 提交失败"):
        assert service.commit_vault([path], "save") is False
    assert service.vault.last_commit_status == "failed"
    assert "CalledProcessError" in service.vault.last_commit_error
    assert path.read_text() == "资料已保存"


def test_browser_timeout_kills_process_group_and_removes_temporary_profile(monkeypatch):
    from pathlib import Path

    from douyin_wiki.adapters import browser_health

    profiles, killed = [], []

    class HungProbe:
        pid = 999999

        def __init__(self, command, **kwargs):
            profiles.append(Path(command[-1]))
            assert profiles[-1].is_dir()
            self.calls = 0

        def communicate(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired("browser", timeout)
            return "", ""

    monkeypatch.setattr(browser_health.subprocess, "Popen", HungProbe)
    monkeypatch.setattr(browser_health.os, "killpg", lambda *args: killed.append(args))
    assert browser_health.playwright_health()["ok"] is False
    assert len(killed) == 1
    assert not profiles[0].exists()


def test_git_no_changes_is_not_reported_as_a_failure(service):
    (service.config.vault_path / ".git").mkdir()
    assert service.commit_vault([], "unchanged") is True
    assert service.vault.last_commit_status == "unchanged"


@pytest.mark.parametrize("analysis, path", [
    ({"title": "资料", "takeaways": 42}, "takeaways"),
    ({"title": "资料", "claims": [42]}, "claims.0"),
])
def test_legacy_analysis_type_errors_report_the_field(service, analysis, path):
    from douyin_wiki.errors import InvalidAnalysisError

    job = make_gateway_job(service, phase="analysis")
    with pytest.raises(InvalidAnalysisError) as caught:
        service.submit_gateway_analysis(job.id, analysis, producer="test")
    assert caught.value.details["errors"][0]["path"] == path
    assert service.database.get_job(job.id) == job
