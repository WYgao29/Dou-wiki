from copy import deepcopy

import pytest

from douyin_wiki.gateway_sources import build_source_catalog


def test_ocr_projection_filters_nearby_subtitles_and_keeps_late_material():
    artifacts = {
        "metadata": {"source_kind": "video"},
        "transcript_raw": [{"id": 7, "start_ms": 0, "end_ms": 1000,
                            "text": "营收增长20%"}],
        "ocr": [
            {"timestamp_ms": 1000, "text": "营收增长２０％\nAI\n营收增长20.0%"},
            {"timestamp_ms": 10000, "text": "营收增长20%"},
            {"timestamp_ms": 130000, "text": "末段表格42GB"},
        ],
    }
    before = deepcopy(artifacts)
    catalog = build_source_catalog(artifacts, phase="transcript_correction")
    assert [row["text"] for row in catalog.ocr] == [
        "AI", "营收增长20.0%", "营收增长20%", "末段表格42GB"
    ]
    assert "o:0:0" not in catalog.refs
    assert catalog.refs["o:2:0"].timestamp_ms == 130000
    assert artifacts == before


@pytest.mark.parametrize("times", [[0, 60000, 60001], [60000, 60001, 0]])
def test_ocr_dedup_uses_last_retained_time(times):
    catalog = build_source_catalog({"ocr": [
        {"timestamp_ms": time, "text": "原始字符Ａ  B"} for time in times
    ]}, phase="analysis")
    assert len(catalog.ocr) == 2
    assert [len(row["locations"]) for row in catalog.ocr] == [2, 1]
    assert [loc["timestamp_ms"] for loc in catalog.ocr[0]["locations"]] == [0, 60000]
    assert catalog.ocr[0]["locations"][0]["source_ref"] == f"o:{times.index(0)}:0"


def test_ocr_without_timestamp_is_not_deduped_across_frames():
    catalog = build_source_catalog({"ocr": [{"text": "同一行"}, {"text": "同一行"}]},
                                   phase="analysis")
    assert len(catalog.ocr) == 2
    assert all("timestamp_ms" not in row["locations"][0] for row in catalog.ocr)


def test_image_dedup_preserves_every_image():
    catalog = build_source_catalog({
        "metadata": {"source_kind": "image_note", "post_text": "同一行"},
        "ocr": [{"image_index": 1, "text": "同一行\n同一行"},
                {"image_index": 2, "text": "同一行"}],
    }, phase="analysis")
    assert len(catalog.ocr) == 2
    assert [loc["image_index"] for row in catalog.ocr for loc in row["locations"]] == [1, 1, 2]
    assert [row["kind"] for row in catalog.sources] == ["image_ocr", "image_ocr", "post_text"]


def test_projection_keeps_short_labels_and_symbol_differences():
    expected_texts = {"AI", "20.0%", "下降3", "张四"}
    catalog = build_source_catalog({
        "transcript_raw": [{"id": 0, "start_ms": 0, "end_ms": 1000,
                            "text": "增长20%/下降-3/张三"}],
        "ocr": [{"timestamp_ms": 0, "text": "AI\n20.0%\n下降3\n张四"}],
    }, phase="transcript_correction")
    assert expected_texts <= {row["text"] for row in catalog.ocr}


def test_source_units_preserve_original_characters_and_ranges():
    text = "ΟΣ。" + "a" * 260
    catalog = build_source_catalog({"transcript_corrected": [
        {"id": 7, "start_ms": 0, "end_ms": 5000, "text": text}
    ]}, phase="analysis")
    assert "".join(row["text"] for row in catalog.sources) == text
    assert [len(row["text"]) for row in catalog.sources] == [3, 256, 4]
    for row in catalog.sources:
        location = row["locations"][0]
        assert (location["start_ms"], location["end_ms"]) == (0, 5000)
        span = catalog.refs[location["source_ref"]]
        assert catalog.containers[span.container][span.start:span.end] == row["text"]


@pytest.mark.parametrize("source_kind", ["video", "image_note"])
def test_projection_has_no_global_count_or_size_limit(source_kind):
    observations = [{"text": f"第{i}张表格" + "原话" * 120,
                     "timestamp_ms": i * 5000, "image_index": i + 1} for i in range(30)]
    observations[-1]["timestamp_ms"] = 130000
    catalog = build_source_catalog({"metadata": {"source_kind": source_kind},
                                    "ocr": observations}, phase="analysis")
    assert len(catalog.ocr) == 30
    assert sum(len(row["text"]) for row in catalog.ocr) > 6000
    assert catalog.refs["o:29:0"].timestamp_ms == 130000
    assert catalog.refs["o:29:0"].image_index == 30


def test_subtitle_filter_uses_phase_and_does_not_change_quoted_characters():
    artifacts = {
        "transcript_raw": [{"id": 0, "start_ms": 0, "end_ms": 1000, "text": "错误识别"}],
        "transcript_corrected": [{"id": 0, "start_ms": 0, "end_ms": 1000, "text": "真实字幕"}],
        "ocr": [{"timestamp_ms": 6000, "text": "真实字幕"},
                {"timestamp_ms": 6001, "text": "真实字幕"}],
    }
    correction = build_source_catalog(artifacts, phase="transcript_correction")
    analysis = build_source_catalog(artifacts, phase="analysis")
    assert len(correction.ocr[0]["locations"]) == 2
    assert analysis.ocr[0]["locations"] == [{"source_ref": "o:1:0", "timestamp_ms": 6001}]
    assert "o:0:0" not in analysis.refs
