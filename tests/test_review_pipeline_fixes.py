"""Publication and model-evidence regressions from the repository review."""

import sqlite3

import pytest
from pydantic import ValidationError

from douyin_wiki.models import AnalysisResult, JobStatus
from douyin_wiki.worker import Worker
from tests.support.gateway import complete_gateway_job


async def capture(service):
    service.capture_douyin("https://v.douyin.com/uvHsRpXIn8s/")
    completed = await Worker(service).run_once()
    if completed.status == JobStatus.AWAITING_AGENT_ANALYSIS:
        completed = await complete_gateway_job(service, completed.id)
    assert completed.status == JobStatus.COMPLETED, completed.error_message
    return completed.result["entry_id"]




@pytest.mark.asyncio
async def test_submit_analysis_database_failure_preserves_published_documents(service):
    entry_id = await capture(service)
    entry_before = service.database.get_entry(entry_id)
    data_before = service.database.get_entry_data(entry_id)
    chunks_before = service.database.fetch_chunks(entry_ids=[entry_id])
    documents_before = service.get_entry(entry_id, include_documents=True)
    with service.database.connect() as conn:
        conn.execute(
            "CREATE TRIGGER abort_analysis BEFORE UPDATE ON entries "
            "BEGIN SELECT RAISE(ABORT, 'projection rejected'); END"
        )

    with pytest.raises(sqlite3.IntegrityError, match="projection rejected"):
        service.submit_analysis(
            entry_id,
            {
                **data_before["analysis"],
                "one_liner": "发布失败的新分析", "summary": "发布失败的新分析",
            },
            producer="review-test",
        )

    assert service.database.get_entry(entry_id) == entry_before
    assert service.database.get_entry_data(entry_id) == data_before
    assert service.database.fetch_chunks(entry_ids=[entry_id]) == chunks_before
    assert service.get_entry(entry_id, include_documents=True) == documents_before
    restored_entry, restored_data = service.vault.load_entries()[0]
    assert restored_entry.summary == entry_before.summary
    assert restored_data["analysis"] == data_before["analysis"]




def test_analysis_boundary_rejects_duplicate_reminder_ids():
    reminders = [
        {"id": "duplicate", "title": title, "reason": "原话", "source_quote": title,
         "confidence": 0.8}
        for title in ["整理资料", "检查日期"]
    ]
    with pytest.raises(ValidationError, match="提醒.*id.*重复"):
        AnalysisResult(title="重复提醒", reminders=reminders)
