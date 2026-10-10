"""Offline Gateway jobs shared by protocol and adapter tests."""

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal

from douyin_wiki import mcp_server
from douyin_wiki.adapters.embeddings import EmbeddingService
from douyin_wiki.config import AppConfig, EmbeddingSettings
from douyin_wiki.gateway_protocol import CompactAnalysis
from douyin_wiki.models import (
    AnalysisMode,
    AnalysisResult,
    CaptureRequest,
    GatewayContext,
    InspirationInput,
    JobRecord,
    JobStatus,
    TranscriptCorrection,
    TranscriptSegment,
)
from douyin_wiki.service import DouyinWikiService
from douyin_wiki.worker import Worker
from tests.conftest import (
    FakeDownloader,
    FakeMediaProcessor,
    FakeOCR,
    FakeReminderAdapter,
    FakeResolver,
    FakeTranscriber,
)


def gateway_analysis(job: JobRecord) -> dict[str, Any]:
    """Create offline analysis input from the job's actual captured evidence."""
    artifacts = job.artifacts
    if artifacts.get("metadata", {}).get("source_kind") == "image_note":
        observations = artifacts.get("ocr", [])
        second = next((row for row in observations if row.get("image_index") == 2), None)
        second = second or next((row for row in observations if row.get("text")), None)
        return {
            "title": "本地模型量化怎么选",
            "one_liner": "按显存、速度和质量需求在 FP16、Q8、Q4 之间选择。",
            "relevance_to_inspiration": "与本地模型部署灵感直接相关。",
            "takeaways": ["FP16 质量高", "Q8 更均衡", "Q4 更省显存"],
            "content_type": "explanation",
            "content_card": {
                "kind": "explanation", "question": "量化格式怎么选",
                "concepts": ["FP16", "Q8", "Q4"],
                "mechanism": ["位宽越低通常越省显存"], "examples": [],
            },
            "knowledge_atoms": [{
                "id": "format-choice", "statement": "Q4 更适合显存紧张的本地部署",
                "atom_type": "recommendation", "provenance": "image_ocr",
                "image_index": second["image_index"], "quote": second["text"],
            }] if second else [],
            "tags": ["本地模型", "量化"],
        }
    transcript = artifacts.get("transcript_corrected") or artifacts.get("transcript_raw", [])
    evidence = next((row for row in reversed(transcript) if row.get("text")), None)
    observations = artifacts.get("ocr", [])
    ocr = next((row for row in observations if row.get("text")), None)
    source_quote = evidence["text"].rstrip("。") if evidence else ocr["text"] if ocr else ""
    atom = {
        "id": "claim-1", "statement": "高质量信息源应提供一手证据",
        "atom_type": "fact", "quote": source_quote,
        "provenance": "audio" if evidence else "ocr",
        "timestamp_ms": (
            evidence["start_ms"] if evidence else ocr.get("timestamp_ms", 0) if ocr else 0
        ),
    }
    payload = {
        "title": "离职后如何筛选财经媒体",
        "summary": "减少低价值财经媒体，优先保留能够提供一手证据的信息源。",
        "core_points": ["信息源质量比数量重要"],
        "evidence": ["作者说明自己主动取关大量财经媒体"],
        "steps": ["识别信息源", "检查是否提供一手证据"],
        "applicable_scenarios": [item.text for item in job.request.inspirations],
        "risks": ["这是作者个人经验，不代表所有场景"],
        "actions": ["整理当前关注的信息源"], "tags": ["财经", "信息筛选"],
        "concepts": ["信息源筛选"], "entities": [{"name": "Nee霓公子", "kind": "creator"}],
        "knowledge_atoms": [atom] if source_quote else [],
        "reminders": [{
            "id": "reminder-1", "title": "检查财经信息源", "due_at": None,
            "reason": "视频提出了可执行的整理动作", "source_quote": source_quote,
            "confidence": 0.7, "needs_clarification": True,
        }] if source_quote else [],
        "ai_judgment": "值得保存，适合用于建立个人信息源筛选标准。",
    }
    normalized = AnalysisResult.model_validate(payload).model_dump(mode="json")
    return {key: value for key, value in normalized.items() if key in CompactAnalysis.model_fields}



async def complete_gateway_job(
    service: DouyinWikiService, job_id: str, *,
    analysis: dict[str, Any] | None = None, gateway_protocol: Literal[1, 2] = 1,
) -> JobRecord:
    """Submit only this waiting job and let the real Worker publish its result."""
    job = service.database.get_job(job_id)
    assert job.status == JobStatus.AWAITING_AGENT_ANALYSIS, (job.id, job.status, job.result)
    if job.result.get("phase") == "transcript_correction":
        context = service.get_analysis_context(job_id, gateway_protocol=gateway_protocol)
        raw = job.artifacts.get("transcript_raw", [])
        corrections = [] if gateway_protocol == 2 else [
            TranscriptCorrection(id=row["id"], text=row["text"]) for row in raw
        ]
        service.submit_transcript_correction(
            job_id, corrections, producer="test-agent", gateway_protocol=gateway_protocol,
            context_revision=context.get("context_revision"),
        )
    job = service.database.get_job(job_id)
    assert job.result.get("phase") == "analysis", job.result
    context = service.get_analysis_context(job_id, gateway_protocol=gateway_protocol)
    service.submit_gateway_analysis(
        job_id, analysis if analysis is not None else gateway_analysis(job),
        producer="test-agent", gateway_protocol=gateway_protocol,
        context_revision=context.get("context_revision"),
    )
    worker = Worker(service)
    for _ in range(max(16, len(service.database.list_jobs(limit=None)) * 3)):
        target = service.database.get_job(job_id)
        if target.status in {JobStatus.COMPLETED, JobStatus.COMPLETED_WITH_WARNINGS}:
            return target
        assert target.status not in {
            JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.NEEDS_AUTH, JobStatus.NEEDS_REVIEW,
        }, (target.id, target.status, target.error_message, target.result)
        processed = await worker.run_once()
        assert processed is not None, (target.id, target.status, target.result)
    raise AssertionError(f"Gateway 任务未在有限步数内完成：{service.database.get_job(job_id)}")


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
        embeddings=EmbeddingSettings(provider="hash", fallback_dimensions=128),
    )
    service = DouyinWikiService(
        config,
        resolver=FakeResolver(),
        downloader=FakeDownloader(),
        media=FakeMediaProcessor(),
        transcriber=FakeTranscriber(),
        ocr=FakeOCR(),
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
