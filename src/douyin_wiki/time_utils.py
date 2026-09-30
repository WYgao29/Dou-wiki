from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

BEIJING_TZ = ZoneInfo("Asia/Shanghai")

USER_TIME_FIELDS = {
    "absolute_time",
    "created",
    "published",
    "updated",
}


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_now() -> str:
    return utc_now().isoformat()


def parse_clock_to_ms(value: int | str | None) -> int | None:
    """Parse a reader clock. Empty is unset; digits are milliseconds; m:s or h:m:s."""
    if value is None or isinstance(value, bool):
        if isinstance(value, bool):
            raise ValueError("时间请用分:秒，例如 1:05；只填整数则按毫秒")
        return None
    if isinstance(value, int):
        if value < 0:
            raise ValueError("时间不能为负数")
        return value
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    parts = text.split(":")
    if len(parts) < 2 or len(parts) > 3 or any(not part.isdigit() for part in parts):
        raise ValueError("时间请用分:秒，例如 1:05；只填整数则按毫秒")
    nums = [int(part) for part in parts]
    if len(nums) == 2:
        minutes, secs = nums
        if secs >= 60:
            raise ValueError("秒数要小于 60，例如 1:05")
        seconds = minutes * 60 + secs
    else:
        hours, minutes, secs = nums
        if minutes >= 60 or secs >= 60:
            raise ValueError("分和秒都要小于 60，例如 1:02:03")
        seconds = hours * 3600 + minutes * 60 + secs
    return seconds * 1000


def parse_datetime(value: str | date | datetime | None) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def to_beijing(value: str | date | datetime) -> datetime:
    parsed = parse_datetime(value)
    if parsed is None:  # pragma: no cover - protected by the non-optional input type
        raise ValueError("时间不能为空")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=BEIJING_TZ)
    return parsed.astimezone(BEIJING_TZ)


def beijing_iso(value: str | date | datetime | None) -> str | None:
    if value is None:
        return None
    return to_beijing(value).isoformat()


def beijing_date(value: str | date | datetime | None = None) -> str:
    target = utc_now() if value is None else value
    return to_beijing(target).date().isoformat()


def format_beijing(value: str | date | datetime | None) -> str:
    if value is None:
        return ""
    return to_beijing(value).strftime("%Y-%m-%d %H:%M:%S（北京时间）")


def _is_time_field(field: str | None) -> bool:
    if not field:
        return False
    return field in USER_TIME_FIELDS or field.endswith(("_at", "_until", "_after"))


def _convert_time_string(value: str, field: str | None) -> str:
    if not _is_time_field(field) or len(value) < 16:
        return value
    try:
        return beijing_iso(value) or value
    except ValueError:
        return value


def user_times_to_beijing(value: Any, *, field: str | None = None) -> Any:
    """Convert user-facing timestamps recursively while preserving internal UTC storage."""
    if isinstance(value, datetime):
        return beijing_iso(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: user_times_to_beijing(item, field=str(key)) for key, item in value.items()}
    if isinstance(value, list):
        return [user_times_to_beijing(item, field=field) for item in value]
    if isinstance(value, tuple):
        return [user_times_to_beijing(item, field=field) for item in value]
    if isinstance(value, str):
        return _convert_time_string(value, field)
    return value
