"""Qwen ASR text cleanup and conservative Chinese numeral normalization.

Left to right:
- Strip a leading ``language Name<asr_text>`` envelope, then delete known tags in place.
- Keep clock times, vague phrases, skipped-place numerals, and ordinary counters.
- Otherwise convert a percentage, a decimal, a quantity with an allowed unit, or a bare
  numeral glued to an ASCII identifier. A trailing counter stays, separated from a decimal.
"""

from __future__ import annotations

import re

_DIGITS: dict[str, int] = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_ZERO = frozenset({"零", "〇"})
_SMALL_UNITS: dict[str, int] = {"十": 10, "百": 100, "千": 1000}
_BIG_UNITS: dict[str, int] = {"万": 10_000, "亿": 100_000_000}
_IMPLICIT_ONE = frozenset({"十", "百", "千"})
_NUMERAL_START = frozenset("零〇一二两三四五六七八九十百千万亿")
_PHRASE_CHARS = frozenset("零〇一二两三四五六七八九十百千万亿点几多来余")
_FRACTION_DIGITS = frozenset(_DIGITS)
_CONTINUING_NUMERALS = frozenset("十百千万亿几多来余")
_MEASURE_CHARS = frozenset(
    "个位名只条本件种类台辆张片颗根支把双对套组批场次回趟声句首篇"
    "章节页行段层级别步下维顿份座栋间家所门款样项道股串堆杯瓶碗盘"
    "盒箱包块粒滴朵棵匹头口封则集期轮局圈枚艘架列排班群手脚眼面方"
    "株枝幅顶幢户处扇"
)
_QUANTITY_UNITS = ("分钟", "小时", "万元", "亿元", "秒", "分", "元", "万", "亿")
_VAGUE_PREFIX = frozenset("几数成多来余")
_LARGE_PREFIX = frozenset("十百千万亿")
_MAGNITUDE = frozenset("万亿")

_LANGUAGE_ENVELOPE = re.compile(r"^(?:<\|[^|]*\|>|\s)*language[ \t]+[A-Za-z]+<asr_text>")
_SPECIAL_TAG = re.compile(r"<\|[^|]*\|>|</?asr_text>")
_CLOCK = re.compile(
    r"[零〇一二两三四五六七八九十百千万亿]+点"
    r"[零〇一二两三四五六七八九十百千]+"
    r"(?:分钟|秒|分)"
)


def clean_qwen_text(text: str) -> str:
    """Remove model envelope and known tags. Other characters stay as written."""
    without_envelope = _LANGUAGE_ENVELOPE.sub("", text, count=1)
    return _SPECIAL_TAG.sub("", without_envelope)


def normalize_zh_numbers(text: str) -> str:
    """Rewrite only explicit numerals. Ambiguous prose is copied through."""
    if not text:
        return text
    parts: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        if text[index] not in _NUMERAL_START:
            parts.append(text[index])
            index += 1
            continue
        clock = _CLOCK.match(text, index)
        if clock is not None:
            parts.append(clock.group())
            index = clock.end()
            continue
        converted = _convert_at(text, index)
        if converted is None:
            end = _phrase_end(text, index)
            parts.append(text[index:end])
            index = end
            continue
        replacement, index = converted
        parts.append(replacement)
    return "".join(parts)


def normalize_asr_text(text: str, *, itn: bool) -> str:
    """Clean model formatting, then optionally normalize explicit numerals."""
    cleaned = clean_qwen_text(text)
    if not itn:
        return cleaned
    return normalize_zh_numbers(cleaned)


def _convert_at(text: str, start: int) -> tuple[str, int] | None:
    if _blocked_prefix(text, start):
        return None
    for matcher in (_match_percentage, _match_decimal, _match_quantity, _match_version):
        matched = matcher(text, start)
        if matched is None:
            continue
        replacement, end = matched
        if _needs_separator(text, start):
            replacement = f" {replacement}"
        return replacement, end
    return None


def _identifier_gap(text: str, start: int) -> bool:
    if start == 0:
        return False
    previous = text[start - 1]
    return previous.isascii() and previous.isalpha()


def _needs_separator(text: str, start: int) -> bool:
    """Keep a converted numeral from gluing to an ASCII identifier or digit."""
    if _identifier_gap(text, start):
        return True
    if start == 0:
        return False
    previous = text[start - 1]
    return previous.isascii() and (previous.isdigit() or previous == ".")


def _blocked_prefix(text: str, start: int) -> bool:
    if start == 0:
        return False
    previous = text[start - 1]
    if previous in _VAGUE_PREFIX:
        return True
    return previous == "上" and text[start] in _LARGE_PREFIX


def _vague_suffix(text: str, index: int) -> bool:
    if index >= len(text):
        return False
    if text[index] in "几多来余":
        return True
    return text.startswith("左右", index)


def _phrase_end(text: str, start: int) -> int:
    end = start + 1
    while end < len(text) and text[end] in _PHRASE_CHARS:
        end += 1
    if text.startswith("左右", end):
        end += 2
    return end


def _parse_strict_integer(
    text: str, start: int, end_limit: int | None = None
) -> tuple[int, int] | None:
    """Parse one explicit place-value integer.

    A bare digit after 百/千/万/亿 without 零 is skipped-place speech (一百五, 一万五)
    and is rejected instead of guessed.
    """
    limit = len(text) if end_limit is None else end_limit
    if start >= limit:
        return None
    first = text[start]
    if first not in _DIGITS and first not in _SMALL_UNITS and first not in _BIG_UNITS:
        return None
    if first in _ZERO:
        nxt = start + 1
        follows_number = nxt < limit and (
            text[nxt] in _DIGITS or text[nxt] in _SMALL_UNITS or text[nxt] in _BIG_UNITS
        )
        if follows_number:
            return None
        return 0, nxt

    total = 0
    section = 0
    pending: int | None = None
    last_small: int | None = None
    last_big: int | None = None
    saw_zero = False
    index = start

    def flush_pending() -> bool:
        nonlocal section, pending, saw_zero
        if pending is None:
            return True
        ones_place = last_small == 10 or (last_small is None and last_big is None)
        if ones_place:
            section += pending
            pending = None
            return True
        if saw_zero:
            section += pending
            pending = None
            saw_zero = False
            return True
        return False

    while index < limit:
        char = text[index]
        if char in _ZERO:
            if pending is not None:
                return None
            saw_zero = True
            index += 1
            continue
        if char in _DIGITS:
            if pending is not None:
                return None
            pending = _DIGITS[char]
            index += 1
            continue
        if char in _SMALL_UNITS:
            unit = _SMALL_UNITS[char]
            if last_small is not None and unit >= last_small:
                break
            if pending is None:
                bare = (
                    char in _IMPLICIT_ONE
                    and section == 0
                    and last_small is None
                    and not saw_zero
                )
                if not bare:
                    break
                pending = 1
            section += pending * unit
            pending = None
            last_small = unit
            saw_zero = False
            index += 1
            continue
        if char in _BIG_UNITS:
            magnitude = _BIG_UNITS[char]
            if last_big is not None and magnitude >= last_big:
                break
            if not flush_pending():
                return None
            if section == 0:
                break
            total += section * magnitude
            section = 0
            last_small = None
            saw_zero = False
            last_big = magnitude
            index += 1
            continue
        break

    if not flush_pending() or index == start:
        return None
    return total + section, index


def _match_percentage(text: str, start: int) -> tuple[str, int] | None:
    prefix = "百分之"
    if not text.startswith(prefix, start):
        return None
    body = start + len(prefix)
    if body >= len(text) or text[body] in "几多来余":
        return None
    decimal = _match_decimal(text, body)
    if decimal is not None:
        rendered, end = decimal
        if " " in rendered or _percentage_tail_blocked(text, end):
            return None
        return f"{rendered}%", end
    parsed = _parse_strict_integer(text, body)
    if parsed is None:
        return None
    value, end = parsed
    if end < len(text) and text[end] == "点":
        return None
    if _percentage_tail_blocked(text, end):
        return None
    return f"{value}%", end


def _percentage_tail_blocked(text: str, end: int) -> bool:
    if _vague_suffix(text, end):
        return True
    if end >= len(text):
        return False
    if text[end] in _MEASURE_CHARS or text[end] in _PHRASE_CHARS:
        return True
    return text[end] in "分秒小万亿元"


def _match_decimal(text: str, start: int) -> tuple[str, int] | None:
    parsed = _parse_strict_integer(text, start)
    if parsed is None:
        return None
    value, index = parsed
    if index >= len(text) or text[index] != "点":
        return None
    cursor = index + 1
    digits: list[str] = []
    peeled_counter = False
    while cursor < len(text) and text[cursor] in _FRACTION_DIGITS:
        following = cursor + 1
        if following < len(text) and text[following] in _MEASURE_CHARS:
            peeled_counter = True
            break
        digits.append(str(_DIGITS[text[cursor]]))
        cursor += 1
    if not digits or _vague_suffix(text, cursor):
        return None
    if cursor < len(text) and text[cursor] in _CONTINUING_NUMERALS:
        return None
    if cursor < len(text) and text[cursor] in "分秒":
        return None
    rendered = f"{value}.{''.join(digits)}"
    if peeled_counter:
        rendered = f"{rendered} "
    return rendered, cursor


def _match_quantity(text: str, start: int) -> tuple[str, int] | None:
    limit = start
    while limit < len(text) and text[limit] in _PHRASE_CHARS:
        limit += 1
    if limit == start:
        return None
    best: tuple[int, str, str] | None = None
    for unit in _QUANTITY_UNITS:
        search = start + 1
        window = min(len(text), limit + len(unit))
        while search < window:
            found = text.find(unit, search, window)
            if found < 0 or found > limit:
                break
            candidate = _quantity_at(text, start, found, unit)
            if candidate is not None:
                best = candidate if best is None else _prefer_quantity(best, candidate)
            search = found + 1
    if best is None:
        return None
    span, _unit, replacement = best
    return replacement, span


def _quantity_at(
    text: str, start: int, unit_at: int, unit: str
) -> tuple[int, str, str] | None:
    parsed = _parse_strict_integer(text, start, unit_at)
    if parsed is None or parsed[1] != unit_at or _vague_suffix(text, unit_at):
        return None
    span = unit_at + len(unit)
    # 万/亿 may still be inside a larger numeral (三万二千). 分/秒/元 already close it.
    if _has_magnitude(unit) and span < len(text) and text[span] in _PHRASE_CHARS:
        return None
    if not _unit_allowed(text, start, unit_at, unit):
        return None
    coefficient = text[start:unit_at]
    if _has_magnitude(coefficient) and _has_magnitude(unit):
        return None
    return span, unit, f"{parsed[0]}{unit}"


def _unit_allowed(text: str, start: int, unit_at: int, unit: str) -> bool:
    if unit != "分":
        return True
    # Bare 十分 is the adverb; bare 百分/千分 are ratio stems, not scores.
    if text[start:unit_at] in {"十", "百", "千"}:
        return False
    return not text.startswith("之", unit_at + len(unit))


def _has_magnitude(value: str) -> bool:
    return any(char in _MAGNITUDE for char in value)


def _prefer_quantity(
    current: tuple[int, str, str], candidate: tuple[int, str, str]
) -> tuple[int, str, str]:
    current_span, current_unit, _current = current
    cand_span, cand_unit, _cand = candidate
    if cand_span > current_span:
        return candidate
    if cand_span == current_span and _has_magnitude(cand_unit) and not _has_magnitude(current_unit):
        return candidate
    return current


def _match_version(text: str, start: int) -> tuple[str, int] | None:
    if not _identifier_gap(text, start):
        return None
    parsed = _parse_strict_integer(text, start)
    if parsed is None:
        return None
    value, end = parsed
    if _vague_suffix(text, end):
        return None
    if end < len(text) and (
        text[end] in _MEASURE_CHARS or text[end] in _PHRASE_CHARS or text[end] in "点分秒小万亿元"
    ):
        return None
    return str(value), end
