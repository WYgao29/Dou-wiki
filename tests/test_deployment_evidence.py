"""Failure recovery and evidence precision must survive every output channel."""

import numpy as np
import pytest

from douyin_wiki.adapters.asr_segments import AsrSegmentationError, RecognitionWindow
from douyin_wiki.adapters.qwen3_asr import Qwen3Transcriber
from douyin_wiki.config import MediaSettings
from douyin_wiki.errors import ExternalToolError
from douyin_wiki.gateway_protocol import context_revision
from douyin_wiki.gateway_sources import build_source_catalog, expand_source_references
from douyin_wiki.models import JobStatus, TranscriptSegment
from douyin_wiki.worker import Worker
from tests.support.gateway import complete_gateway_job, make_gateway_job
from tests.test_qwen3_asr import FakeRuntime


@pytest.mark.parametrize("policy", ["fail", "retain_window"])
async def test_alignment_policy_keeps_recognition_checkpoint(tmp_path, monkeypatch, policy):
    monkeypatch.setattr(
        "douyin_wiki.adapters.qwen3_asr.iter_recognition_windows",
        lambda *_: iter([RecognitionWindow(16000, np.ones(16000))]),
    )

    def fail(*args, **kwargs):
        raise AsrSegmentationError("无法对齐", reason="gross_repair")

    monkeypatch.setattr("douyin_wiki.adapters.qwen3_asr.segment_recognition_window", fail)

    class Runtime(FakeRuntime):
        calls = 0

        def recognize(self, *args, **kwargs):
            self.calls += 1
            return super().recognize(*args, **kwargs)

    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"stable-audio-content")
    runtime = Runtime(text="完整原话。")
    settings = MediaSettings(alignment_failure_policy=policy)
    engine = Qwen3Transcriber(settings, runtime=runtime)
    if policy == "fail":
        with pytest.raises(ExternalToolError, match="gross_repair"):
            await engine.transcribe(audio, tmp_path / "checkpoint")
        assert list((tmp_path / "checkpoint").rglob("recognition-*.json"))
        settings.alignment_failure_policy = "retain_window"
    rows = await engine.transcribe(audio, tmp_path / "checkpoint")
    assert len(rows) == 1 and rows[0].text == "完整原话。"
    assert (rows[0].start_ms, rows[0].end_ms) == (1000, 2000)
    assert rows[0].timestamp_precision == "window"
    assert rows[0].alignment_reason == "gross_repair"
    assert runtime.calls == 1
    assert (await engine.transcribe(audio, tmp_path / "checkpoint"))[0].text == "完整原话。"
    assert runtime.calls == 1
    audio.write_bytes(b"changed-audio-content")
    await engine.transcribe(audio, tmp_path / "checkpoint")
    assert runtime.calls == 2


async def test_asr_failure_preserves_ocr_and_retry_does_not_repeat_frames(service, monkeypatch):
    transcriber = service.transcriber
    original_transcribe = transcriber.transcribe
    calls = []
    extract = service.media.extract_frames

    async def frames(*args, **kwargs):
        calls.append(1)
        return await extract(*args, **kwargs)

    async def fail(*args, **kwargs):
        raise ExternalToolError("识别失败")

    monkeypatch.setattr(service.media, "extract_frames", frames)
    monkeypatch.setattr(transcriber, "transcribe", fail)
    job = service.capture_douyin("https://v.douyin.com/uvHsRpXIn8s/")
    failed = await Worker(service).run_once()
    assert failed.status == JobStatus.FAILED
    assert failed.artifacts["ocr"]
    assert len(calls) == 1
    monkeypatch.setattr(transcriber, "transcribe", original_transcribe)
    service.retry_job(job.id)
    resumed = await Worker(service).run_once()
    assert resumed.status == JobStatus.AWAITING_AGENT_ANALYSIS
    assert resumed.artifacts["ocr"] == failed.artifacts["ocr"]
    assert len(calls) == 1


@pytest.mark.parametrize("change", ["video", "frame_settings"])
async def test_changed_material_or_settings_invalidate_ocr_checkpoint(service, monkeypatch, change):
    from pathlib import Path

    original = service.transcriber.transcribe

    async def fail(*args):
        raise ExternalToolError("识别失败")

    monkeypatch.setattr(service.transcriber, "transcribe", fail)
    job = service.capture_douyin("https://v.douyin.com/uvHsRpXIn8s/")
    previous = await Worker(service).run_once()
    assert previous.artifacts["ocr_signature"]
    if change == "video":
        Path(previous.artifacts["video_path"]).write_bytes(b"changed-video-content")
    else:
        service.config.media.frame_interval_seconds += 1
    monkeypatch.setattr(service.transcriber, "transcribe", original)
    service.retry_job(job.id)
    resumed = await Worker(service).run_once()
    assert resumed.status == JobStatus.AWAITING_AGENT_ANALYSIS
    assert resumed.artifacts["ocr_signature"] != previous.artifacts["ocr_signature"]


def test_filtered_middle_ocr_line_remains_available_for_contiguous_quote():
    artifacts = {
        "transcript_corrected": [{"id": 0, "text": "中间字幕。", "start_ms": 0, "end_ms": 1000}],
        "ocr": [{"timestamp_ms": 0, "text": "尺寸42米\n中间字幕\n宽度8米"}],
    }
    catalog = build_source_catalog(artifacts, phase="analysis")
    assert "o:0:1" in catalog.refs
    result = expand_source_references(
        {
            "knowledge_atoms": [
                {
                    "id": "a",
                    "statement": "尺寸",
                    "source_refs": ["o:0:0", "o:0:1", "o:0:2"],
                }
            ]
        },
        catalog,
        source_kind="video",
    )
    assert result["knowledge_atoms"][0]["quote"] == artifacts["ocr"][0]["text"]


@pytest.mark.parametrize("protocol", [1, 2])
def test_server_restores_coarse_precision_instead_of_trusting_agent(service, protocol):
    job = make_gateway_job(
        service,
        phase="analysis",
        artifacts={
            "transcript_corrected": [
                {
                    "id": 0,
                    "text": "完整原话。",
                    "start_ms": 0,
                    "end_ms": 9000,
                    "timestamp_precision": "window",
                    "alignment_reason": "gross_repair",
                }
            ],
        },
    )
    atom = {"id": "a", "statement": "原话", "timestamp_precision": "aligned"}
    atom.update(
        {"source_refs": ["a:0:0"]}
        if protocol == 2
        else {
            "quote": "完整原话。",
            "timestamp_ms": 0,
        }
    )
    result = service.submit_gateway_analysis(
        job.id,
        {"title": "资料", "knowledge_atoms": [atom]},
        producer="test",
        gateway_protocol=protocol,
        context_revision=context_revision(job),
    )
    saved = result.artifacts["analysis"]["knowledge_atoms"][0]
    assert saved["timestamp_precision"] == "window"
    assert saved["timestamp_end_ms"] == 9000


async def test_precision_survives_publication_search_and_vault_rebuild(service, monkeypatch):
    async def transcribe(*args):
        return [
            TranscriptSegment(
                id=0,
                start_ms=0,
                end_ms=9000,
                text="完整原话包含窗户尺寸。",
                timestamp_precision="window",
                alignment_reason="gross_repair",
            )
        ]

    monkeypatch.setattr(service.transcriber, "transcribe", transcribe)
    job = service.capture_douyin("https://v.douyin.com/uvHsRpXIn8s/")
    assert (await Worker(service).run_once()).status == JobStatus.AWAITING_AGENT_ANALYSIS
    completed = await complete_gateway_job(service, job.id, gateway_protocol=2)
    assert completed.status == JobStatus.COMPLETED_WITH_WARNINGS
    entry = service.database.get_entry(completed.result["entry_id"])
    source = service.vault_path(entry.source_path).read_text()
    assert "粗略窗口时间" in source
    raw = service.vault_path(entry.raw_path).read_text()
    assert "时间未经精确对齐" in raw
    found = service.search_knowledge("窗户尺寸", entry_ids=[entry.id])
    assert any(
        item.timestamp_precision == "window" and "未经精确对齐" in item.snippet for item in found
    )
    service.rebuild_database_from_vault(apply=True)
    data = service.database.get_entry_data(entry.id)
    assert data["transcript_corrected"][0]["timestamp_precision"] == "window"
    found = service.search_knowledge("窗户尺寸", entry_ids=[entry.id])
    assert any(
        item.timestamp_precision == "window" and item.timestamp_end_ms == 9000 for item in found
    )


async def test_unlisted_alignment_error_never_degrades(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "douyin_wiki.adapters.qwen3_asr.iter_recognition_windows",
        lambda *_: iter([RecognitionWindow(0, np.ones(16000))]),
    )

    def fail(*args, **kwargs):
        raise AsrSegmentationError("原文没有被覆盖", reason="coverage")

    monkeypatch.setattr("douyin_wiki.adapters.qwen3_asr.segment_recognition_window", fail)
    engine = Qwen3Transcriber(
        MediaSettings(alignment_failure_policy="retain_window"), runtime=FakeRuntime()
    )
    with pytest.raises(ExternalToolError, match="coverage"):
        await engine.transcribe(tmp_path / "a.wav", tmp_path / "cache")
    assert engine.provenance == {}


def test_fresh_init_enables_window_policy_but_legacy_config_stays_strict(tmp_path):
    from typer.testing import CliRunner

    from douyin_wiki.cli import app
    from douyin_wiki.config import load_config

    config_path = tmp_path / "config.toml"
    result = CliRunner().invoke(
        app, ["init", "--vault", str(tmp_path / "new-vault"), "--config-path", str(config_path)]
    )
    assert result.exit_code == 0, result.output
    assert load_config(config_path).media.alignment_failure_policy == "retain_window"
    assert load_config(config_path).media.keep_keyframes is True
    config_path.write_text(
        config_path.read_text()
        .replace('alignment_failure_policy = "retain_window"\n', "")
        .replace("keep_keyframes = true\n", "")
    )
    assert load_config(config_path).media.alignment_failure_policy == "fail"
    assert load_config(config_path).media.keep_keyframes is False
