"""Gateway-only material projection; original artifacts remain the evidence authority."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Literal

SOURCE_PROJECTION_VERSION = 1
SUBTITLE_MIN_LENGTH = 4
SUBTITLE_WINDOW_MS = 5000
OCR_DEDUP_WINDOW_MS = 60_000
SOURCE_UNIT_MAX_CHARS = 256

SourceKind = Literal["audio", "ocr", "image_ocr", "post_text"]


@dataclass(frozen=True)
class SourceSpan:
    kind: SourceKind
    container: str
    start: int
    end: int
    timestamp_ms: int | None = None
    image_index: int | None = None
    segment_id: int | None = None
    start_ms: int | None = None
    end_ms: int | None = None


@dataclass(frozen=True)
class SourceCatalog:
    sources: list[dict[str, Any]]
    ocr: list[dict[str, Any]]
    refs: dict[str, SourceSpan]
    containers: dict[str, str]


def _comparison_key(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).split()).casefold()


def _unit_ranges(text: str, *, audio: bool) -> Iterator[tuple[int, int]]:
    start = 0
    for match in re.finditer(r"[。！？!?；;\n]" if audio else r"\n", text):
        end = match.end()
        for offset in range(start, end, SOURCE_UNIT_MAX_CHARS):
            yield offset, min(offset + SOURCE_UNIT_MAX_CHARS, end)
        start = end
    for offset in range(start, len(text), SOURCE_UNIT_MAX_CHARS):
        yield offset, min(offset + SOURCE_UNIT_MAX_CHARS, len(text))


def build_source_catalog(
    artifacts: Mapping[str, Any], *, phase: Literal["transcript_correction", "analysis"]
) -> SourceCatalog:
    metadata = artifacts.get("metadata", {}) or {}
    is_image_note = metadata.get("source_kind") == "image_note"
    transcript = (
        artifacts.get("transcript_corrected") or artifacts.get("transcript_raw", [])
        if phase == "analysis"
        else artifacts.get("transcript_raw", [])
    )
    if is_image_note:
        transcript = []
    sources: list[dict[str, Any]] = []
    projected_ocr: list[dict[str, Any]] = []
    refs: dict[str, SourceSpan] = {}
    containers: dict[str, str] = {}
    containers["audio"] = "\n".join(str(segment.get("text", "")) for segment in transcript)
    offset = 0
    for segment in transcript:
        text = str(segment.get("text", ""))
        for unit_index, (start, end) in enumerate(_unit_ranges(text, audio=True)):
            ref = f"a:{segment['id']}:{unit_index}"
            span = SourceSpan(
                "audio", "audio", offset + start, offset + end,
                timestamp_ms=segment.get("start_ms"), segment_id=segment["id"],
                start_ms=segment.get("start_ms"), end_ms=segment.get("end_ms"),
            )
            refs[ref] = span
            sources.append({"kind": "audio", "text": text[start:end], "locations": [{
                "source_ref": ref, "segment_id": span.segment_id,
                "start_ms": span.start_ms, "end_ms": span.end_ms,
                "timestamp_ms": span.timestamp_ms,
            }]})
        offset += len(text) + 1

    observations = list(enumerate(artifacts.get("ocr", [])))
    if not is_image_note:
        observations.sort(key=lambda pair: (
            pair[1].get("timestamp_ms") is None, pair[1].get("timestamp_ms") or 0, pair[0]
        ))
    groups: dict[tuple[Any, str], tuple[int | None, dict[str, Any]]] = {}
    for observation_index, observation in observations:
        timestamp = observation.get("timestamp_ms")
        image_index = observation.get("image_index")
        text = str(observation.get("text", ""))
        container = f"ocr:{observation_index}"
        containers[container] = text
        nearby = ""
        if not is_image_note and timestamp is not None:
            nearby = _comparison_key("".join(
                str(segment.get("text", "")) for segment in transcript
                if segment.get("start_ms", 0) - SUBTITLE_WINDOW_MS <= timestamp
                <= segment.get("end_ms", 0) + SUBTITLE_WINDOW_MS
            ))
        offset = 0
        for line_index, raw_line in enumerate(text.splitlines(keepends=True)):
            line = raw_line.rstrip("\r\n")
            start, end = offset, offset + len(line)
            offset += len(raw_line)
            if not line.strip():
                continue
            key = _comparison_key(line)
            if nearby and len(key) >= SUBTITLE_MIN_LENGTH and key in nearby:
                continue
            ref = f"o:{observation_index}:{line_index}"
            kind: SourceKind = "image_ocr" if is_image_note else "ocr"
            refs[ref] = SourceSpan(kind, container, start, end, timestamp, image_index)
            location: dict[str, Any] = {"source_ref": ref}
            if is_image_note and image_index is not None:
                location["image_index"] = image_index
            if not is_image_note and timestamp is not None:
                location["timestamp_ms"] = timestamp
            # Missing locators only permit sharing within this observation.
            boundary = (
                ("image", image_index) if is_image_note and image_index is not None
                else ("observation", observation_index) if timestamp is None or is_image_note
                else ("video",)
            )
            group_key = (boundary, key)
            previous = groups.get(group_key)
            can_merge = previous is not None and (
                boundary[0] != "video"
                or timestamp - previous[0] <= OCR_DEDUP_WINDOW_MS
            )
            if can_merge:
                previous[1]["locations"].append(location)
            else:
                row = {"text": line, "locations": [location]}
                projected_ocr.append(row)
                sources.append({"kind": kind, **row})
                groups[group_key] = (timestamp, row)

    post_text = str(metadata.get("post_text") or "")
    containers["post_text"] = post_text
    for unit_index, (start, end) in enumerate(_unit_ranges(post_text, audio=False)):
        ref = f"p:{unit_index}"
        refs[ref] = SourceSpan("post_text", "post_text", start, end)
        sources.append({"kind": "post_text", "text": post_text[start:end],
                        "locations": [{"source_ref": ref}]})
    return SourceCatalog(sources, projected_ocr, refs, containers)
