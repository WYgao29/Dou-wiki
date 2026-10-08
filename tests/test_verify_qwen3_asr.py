from __future__ import annotations

import runpy
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "verify_qwen3_asr.py"


def api():
    return runpy.run_path(str(SCRIPT), run_name="verification_test")


def test_verification_requires_explicit_targets():
    parser = api()["make_parser"]()
    with pytest.raises(SystemExit):
        parser.parse_args([])
    with pytest.raises(SystemExit):
        parser.parse_args(["--output-dir", "/tmp/example", "--audio", "sample=/tmp/sample.wav"])


def test_verification_rejects_unsafe_names_duplicates_and_existing_vault(tmp_path):
    parse = api()["parse_audio"]
    audio = tmp_path / "source.wav"
    audio.write_bytes(b"fixture")
    assert parse([f"sample={audio}"]) == [("sample", audio)]
    for items in (
        [f"../escape={audio}"],
        [f"sample={audio}", f"sample={audio}"],
        ["missing-separator"],
    ):
        with pytest.raises(ValueError):
            parse(items)
    validate = api()["validate_targets"]
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "existing.md").write_text("preserve")
    with pytest.raises(ValueError):
        validate(tmp_path / "output", vault)
    assert not (tmp_path / "output").exists()
    assert (vault / "existing.md").read_text() == "preserve"


def test_trace_validation_rejects_missing_or_duplicated_sample_ownership():
    validate = api()["validate_result"]
    trace = [{"start_sample": 0, "samples": 16000, "silent": True}]
    validate([], trace, 16000)
    with pytest.raises(ValueError):
        validate([], trace, 32000)
    with pytest.raises(ValueError):
        validate([], trace + trace, 32000)


def test_trace_validation_rejects_character_and_public_time_losses():
    from douyin_wiki.models import TranscriptSegment

    validate = api()["validate_result"]
    trace = [
        {
            "start_sample": 0,
            "samples": 16000,
            "silent": False,
            "raw_text": "测试",
            "generation_tokens": 4,
            "internal_segments": 1,
            "boundaries": [],
            "display_source_spans": [[0, 2]],
        }
    ]
    segments = [TranscriptSegment(id=0, start_ms=0, end_ms=1000, text="测试")]
    validate(segments, trace, 16000)
    trace[0]["display_source_spans"] = [[0, 1]]
    with pytest.raises(ValueError):
        validate(segments, trace, 16000)
    trace[0]["display_source_spans"] = [[0, 2]]
    segments[0].end_ms = 1001
    with pytest.raises(ValueError):
        validate(segments, trace, 16000)
