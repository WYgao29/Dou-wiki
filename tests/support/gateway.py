"""Offline Gateway jobs shared by protocol and adapter tests."""

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal

from douyin_wiki import mcp_server
from douyin_wiki.adapters.embeddings import EmbeddingService
from douyin_wiki.config import AppConfig, EmbeddingSettings, LLMSettings
from douyin_wiki.models import (
    AnalysisMode,
    CaptureRequest,
    GatewayContext,
    InspirationInput,
    JobRecord,
    JobStatus,
    TranscriptSegment,
)
from douyin_wiki.service import DouyinWikiService
from douyin_wiki.worker import Worker
from tests.conftest import (
    FakeAnalysisProvider,
    FakeDownloader,
    FakeMediaProcessor,
    FakeOCR,
    FakeReminderAdapter,
    FakeResolver,
    FakeTranscriber,
)


def make_gateway_job(
    service: DouyinWikiService,
    *,
    phase: str = "transcript_correction",
    source_kind: str = "video",
    artifacts: dict[str, Any] | None = None,
) -> JobRecord:
    service.config.analysis_mode = AnalysisMode.GATEWAY
    raw = [
        TranscriptSegment(id=0, start_ms=0, end_ms=5000, text="第一段原话。"),
        TranscriptSegment(id=1, start_ms=5000, end_ms=9000, text="原始第二段。"),
    ]
    materials = {
        "metadata": {
            "video_id": "test-video",
            "source_kind": source_kind,
            "title": "资料",
            "duration_seconds": 90,
            "original_url": "https://www.douyin.com/video/test-video",
        },
        "resolved": {"video_id": "test-video"},
        "transcript_raw": [segment.model_dump(mode="json") for segment in raw]
        if source_kind == "video"
        else [],
        "ocr": [],
    }
    if phase == "analysis" and source_kind == "video":
        materials["transcript_corrected"] = deepcopy(materials["transcript_raw"])
    materials.update(deepcopy(artifacts or {}))
    job = service.database.create_job(
        CaptureRequest(
            share_text="https://www.douyin.com/video/test-video",
            inspirations=[InspirationInput(text="逐字保留的灵感")],
            gateway_context=GatewayContext(gateway="test-agent", conversation_id="thread-1"),
        ),
        status=JobStatus.AWAITING_AGENT_ANALYSIS,
        artifacts=materials,
        progress=0.6,
    )
    return service.database.update_job(job.id, result={"phase": phase})


def make_video_gateway_service(tmp_path: Path) -> DouyinWikiService:
    config = AppConfig(
        vault_path=tmp_path / "vault",
        analysis_mode=AnalysisMode.GATEWAY,
        llm=LLMSettings(enabled=False),
        embeddings=EmbeddingSettings(provider="hash", fallback_dimensions=128),
    )
    service = DouyinWikiService(
        config,
        resolver=FakeResolver(),
        downloader=FakeDownloader(),
        media=FakeMediaProcessor(),
        transcriber=FakeTranscriber(),
        ocr=FakeOCR(),
        analysis=FakeAnalysisProvider(),
        embeddings=EmbeddingService(config.embeddings),
        reminders=FakeReminderAdapter(),
    )
    service.initialize(initialize_git=False)
    return service


async def run_gateway_roundtrip(
    service: DouyinWikiService, *, gateway_protocol: Literal[1, 2]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Same synthetic 90-second video, real MCP/Worker, no external model or account."""
    raw = [
        TranscriptSegment(
            id=i,
            start_ms=i * 3000,
            end_ms=(i + 1) * 3000,
            text=f"第{i}段。" + "这段内容保留完整原文与符号20%。" * 20,
        ).model_dump(mode="json")
        for i in range(30)
    ]
    observations = [
        {"timestamp_ms": i * 500, "text": "图表说明原始值42GB。\n" + "重复图表文字" * 20}
        for i in range(120)
    ]
    observations.append({"timestamp_ms": 70000, "text": "后半片独有图表17GB"})
    job = service.capture_douyin(
        "https://v.douyin.com/uvHsRpXIn8s/",
        inspirations=[InspirationInput(text="逐字保留的灵感")],
        gateway_context=GatewayContext(gateway="test-agent", conversation_id="thread-1"),
    )
    paused = await Worker(service).run_once()
    assert paused.id == job.id and paused.status == JobStatus.AWAITING_AGENT_ANALYSIS
    service.database.update_job(job.id, artifacts={"transcript_raw": raw, "ocr": observations})
    exchanges: list[dict[str, Any]] = []
    previous = mcp_server._SERVICE
    mcp_server._SERVICE = service

    async def call(tool: str, request: dict[str, Any]) -> dict[str, Any]:
        request = {**request, "gateway_protocol": gateway_protocol}
        response = await mcp_server.mcp.call_tool(tool, request)
        payload = response.structured_content
        text = response.content[0].text
        assert json.loads(text) == payload
        exchanges.append(
            {
                "tool": tool,
                "request": request,
                "response_text": text,
                "response_structured": payload,
            }
        )
        return payload

    try:
        await call("get_job", {"job_id": job.id})
        correction = await call("get_analysis_context", {"job_id": job.id})
        corrected_text = raw[0]["text"].replace("第0段", "更正的第0段", 1)
        patch = [{"id": 0, "text": corrected_text}]
        corrections = (
            patch
            if gateway_protocol == 2
            else [
                {"id": row["id"], "text": corrected_text if row["id"] == 0 else row["text"]}
                for row in raw
            ]
        )
        submit_params = {"job_id": job.id, "producer": "test-agent"}
        if gateway_protocol == 2:
            submit_params["context_revision"] = correction["context_revision"]
        await call("submit_transcript_correction", {**submit_params, "corrections": corrections})
        context = await call("get_analysis_context", {"job_id": job.id})
        atom = {"id": "late-chart", "statement": "后半片图表记录17GB"}
        if gateway_protocol == 2:
            atom["source_refs"] = ["o:120:0"]
            submit_params["context_revision"] = context["context_revision"]
        else:
            atom.update({"quote": "后半片独有图表17GB", "provenance": "ocr", "timestamp_ms": 70000})
        await call(
            "submit_gateway_analysis",
            {
                **submit_params,
                "analysis": {
                    "title": "完整材料基准",
                    "one_liner": "后半片图表记录17GB",
                    "knowledge_atoms": [atom],
                    "tags": ["图表参数", "资料核验"],
                },
            },
        )
        completed = await Worker(service).run_once()
        assert completed.id == job.id and completed.status == JobStatus.COMPLETED
        await call("get_job", {"job_id": job.id})
        return service.database.get_entry_data(completed.result["entry_id"])["analysis"], exchanges
    finally:
        mcp_server._SERVICE = previous
