"""Server-owned precision annotations for audio evidence."""

from .models import TimestampQuality


def audio_timing(transcript, timestamp_ms, quote=None) -> dict:
    rows = [row if isinstance(row, dict) else row.model_dump() for row in transcript]
    default = TimestampQuality().model_dump()
    if timestamp_ms is None and not quote:
        return default
    if not any(row.get("timestamp_precision") == "window" for row in rows):
        if rows and all(row.get("timestamp_precision") == "aligned" for row in rows):
            return {**default, "timestamp_precision": "aligned"}
        return default
    selected = []
    if quote:
        text, owners = "", []
        for index, row in enumerate(rows):
            normalized = "".join(c for c in row.get("text", "").casefold() if c.isalnum())
            text += normalized
            owners.extend([index] * len(normalized))
        needle = "".join(c for c in quote.casefold() if c.isalnum())
        position = text.find(needle) if needle else -1
        while position >= 0:
            first, last = owners[position], owners[position + len(needle) - 1]
            if timestamp_ms is None or (
                rows[first]["start_ms"] - 1000 <= timestamp_ms <= rows[last]["end_ms"] + 1000
            ):
                selected.extend(rows[first : last + 1])
            position = text.find(needle, position + 1)
    if not selected and timestamp_ms is not None:
        selected = [row for row in rows if row["start_ms"] <= timestamp_ms <= row["end_ms"]]
    coarse = [row for row in selected if row.get("timestamp_precision") == "window"]
    if coarse:
        return {
            "timestamp_precision": "window",
            "timestamp_start_ms": min(row["start_ms"] for row in selected),
            "timestamp_end_ms": max(row["end_ms"] for row in selected),
            "alignment_reason": ",".join(
                sorted({row.get("alignment_reason") or "alignment_failed" for row in coarse})
            ),
        }
    if selected and all(row.get("timestamp_precision") == "aligned" for row in selected):
        return {**default, "timestamp_precision": "aligned"}
    return default


def apply_timing(item, info):
    for name, value in info.items():
        setattr(item, name, value)


def coarse_label(item) -> str:
    getter = (
        item.get
        if isinstance(item, dict)
        else lambda name, default=None: getattr(item, name, default)
    )
    if getter("timestamp_precision") != "window":
        return ""
    start = getter("timestamp_start_ms")
    end = getter("timestamp_end_ms")
    if start is None:
        start = getter("start_ms", getter("timestamp_ms"))
    if end is None:
        end = getter("end_ms")

    def clock(value):
        seconds = int(value or 0) // 1000
        return f"{seconds // 60:02d}:{seconds % 60:02d}"

    return f"约 {clock(start)}–{clock(end)}（时间未经精确对齐）"
