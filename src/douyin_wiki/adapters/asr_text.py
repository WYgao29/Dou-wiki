"""Qwen ASR text cleanup and conservative Chinese numeral normalization.

Left to right:
- Strip a leading ``language Name<asr_text>`` envelope, then delete known tags in place.
- Keep clock times, vague phrases (including a hedge after the whole unit), skipped-place
  numerals, ordinary counters, and a bare 分/元 stem inside a longer word.
- Otherwise convert a percentage, a decimal, a quantity with an allowed unit, or an
  identifier numeral at a closed boundary or a grammatical continuation. A trailing
  counter stays, separated from a decimal.
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
_AMBIGUOUS_UNITS = frozenset({"分", "元"})
_VERSION_CONTINUATIONS = ("其实", "的", "在", "是")
_UNIT_BOUNDARIES = frozenset("，。、；：！？…—～·（）【】《》「」『』“”‘’\"'()[]{}")

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


def render_normalized_slices(
    text: str, spans: tuple[tuple[int, int], ...], *, itn: bool
) -> tuple[str, ...]:
    """Render source slices using numeral decisions made in the complete source."""
    cursor = 0
    for start, end in spans:
        if start != cursor or not start < end <= len(text):
            raise ValueError("展示区间必须连续覆盖原文")
        cursor = end
    if cursor != len(text):
        raise ValueError("展示区间必须连续覆盖原文")
    if not itn:
        return tuple(text[start:end] for start, end in spans)
    edits: list[tuple[int, int, str]] = []
    index = 0
    while index < len(text):
        if text[index] not in _NUMERAL_START:
            index += 1
            continue
        clock = _CLOCK.match(text, index)
        if clock is not None:
            index = clock.end()
            continue
        converted = _convert_at(text, index)
        if converted is None:
            index = _phrase_end(text, index)
            continue
        replacement, end = converted
        edits.append((index, end, replacement))
        index = end
    output = []
    for start, end in spans:
        parts = []
        cursor = start
        for edit_start, edit_end, replacement in edits:
            if edit_end <= start or edit_start >= end:
                continue
            if edit_start < start or edit_end > end:
                raise ValueError("数词替换不能跨越展示区间")
            parts.extend((text[cursor:edit_start], replacement))
            cursor = edit_end
        parts.append(text[cursor:end])
        output.append("".join(parts))
    if "".join(output) != normalize_zh_numbers(text):
        raise ValueError("展示文字与整窗规范化结果不一致")
    return tuple(output)


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
    if previous in "到至" and start >= 2:
        before_range = text[start - 2]
        if before_range in _NUMERAL_START or before_range.isdecimal():
            # A shared trailing unit cannot make only the range's right side a
            # standalone quantity (六到七分钟 -> 六到7分钟).
            return True
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
                    char in _IMPLICIT_ONE and section == 0 and last_small is None and not saw_zero
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
    found = _structural_quantity(text, start)
    if found is None:
        return None
    span, unit, replacement = found
    if _ambiguous_unit_stem(text, unit, span):
        return None
    return replacement, span


def _structural_quantity(text: str, start: int) -> tuple[int, str, str] | None:
    """Best integer-plus-unit reading, with no stem lookahead."""
    limit = start
    while limit < len(text) and text[limit] in _PHRASE_CHARS:
        limit += 1
    if limit == start:
        return None
    candidates: list[tuple[int, str, str]] = []
    for unit in _QUANTITY_UNITS:
        search = start + 1
        window = min(len(text), limit + len(unit))
        while search < window:
            found = text.find(unit, search, window)
            if found < 0 or found > limit:
                break
            candidate = _quantity_at(text, start, found, unit)
            if candidate is not None:
                candidates.append(candidate)
            search = found + 1
    if not candidates:
        return None
    # A hedge after the longest reading applies to the whole amount: 一百元左右
    # must not fall back to a shorter unit and become 100元左右.
    longest_span = max(candidate[0] for candidate in candidates)
    if _vague_suffix(text, longest_span):
        return None
    best = candidates[0]
    for candidate in candidates[1:]:
        best = _prefer_quantity(best, candidate)
    return best


def _quantity_at(text: str, start: int, unit_at: int, unit: str) -> tuple[int, str, str] | None:
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


def _ambiguous_unit_stem(text: str, unit: str, span: int) -> bool:
    """Keep 十二元组 / 一分为二. A closed 十二元 or a following quantity still converts."""
    if unit not in _AMBIGUOUS_UNITS or _closed_after_unit(text, span):
        return False
    return not _has_measured_follower(text, span)


def _closed_after_unit(text: str, span: int) -> bool:
    if span >= len(text):
        return True
    char = text[span]
    return char.isspace() or char.isascii() or char in _UNIT_BOUNDARIES


def _has_measured_follower(text: str, span: int) -> bool:
    """Walk later quantities in a loop. Each chunk is at least two characters."""
    if span >= len(text) or text[span] not in _NUMERAL_START or _blocked_prefix(text, span):
        return False
    index = span
    chunks: list[tuple[str, int]] = []
    while index < len(text):
        found = _structural_quantity(text, index)
        if found is None:
            break
        end, unit, _replacement = found
        if end <= index:
            break
        chunks.append((unit, end))
        standalone = unit not in _AMBIGUOUS_UNITS or _closed_after_unit(text, end)
        if _vague_suffix(text, end) or standalone:
            break
        index = end
    resolved = False
    for unit, end in reversed(chunks):
        if _vague_suffix(text, end):
            resolved = False
            continue
        resolved = unit not in _AMBIGUOUS_UNITS or _closed_after_unit(text, end) or resolved
    return resolved


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


def _version_continues(text: str, end: int) -> bool:
    """Closed token or a grammatical continuation. Any other Chinese suffix stays."""
    if _closed_after_unit(text, end):
        return True
    return any(text.startswith(word, end) for word in _VERSION_CONTINUATIONS)


def _match_version(text: str, start: int) -> tuple[str, int] | None:
    if not _identifier_gap(text, start):
        return None
    parsed = _parse_strict_integer(text, start)
    if parsed is None:
        return None
    value, end = parsed
    if _vague_suffix(text, end) or not _version_continues(text, end):
        return None
    return str(value), end


# Existing Arabic forms are not seen by the Chinese scanner. Versions may contain
# spaces or hyphens; bare decimals and integers stay separate when they only touch.
_ARABIC_DECIMAL = re.compile(r"(?<![0-9A-Za-z.])\d+(?:\.\d+)+(?![0-9])")
_ARABIC_INTEGER = re.compile(r"(?<![0-9A-Za-z.])\d+(?![0-9.])")
_ASCII_VERSION = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9]*(?:[ \t.\-]\d+|\d+)(?:\.\d+)*(?![0-9])"
)


def protected_number_spans(text: str) -> tuple[tuple[int, int], ...]:
    """Source spans that normalization would rewrite, plus Arabic numbers.

    The spans share ``_convert_at``. Ambiguous phrases and clock times are not
    protected. Touching spans stay separate so ``3.14五点六`` can split between them.
    """
    if not text:
        return ()
    spans: list[tuple[int, int]] = []
    index = 0
    length = len(text)
    while index < length:
        if text[index] not in _NUMERAL_START:
            index += 1
            continue
        clock = _CLOCK.match(text, index)
        if clock is not None:
            index = clock.end()
            continue
        converted = _convert_at(text, index)
        if converted is None:
            glued = _glued_identifier_numeral(text, index)
            if glued is None:
                index = _phrase_end(text, index)
                continue
            spans.append(glued)
            index = glued[1]
            continue
        _replacement, end = converted
        start = _identifier_prefix(text, index)
        spans.append((start, end))
        index = end
    for pattern in (_ARABIC_DECIMAL, _ARABIC_INTEGER, _ASCII_VERSION):
        spans.extend((match.start(), match.end()) for match in pattern.finditer(text))
    return _merge_overlapping_spans(spans)


def _glued_identifier_numeral(text: str, start: int) -> tuple[int, int] | None:
    """Keep an ASCII identifier on a finished numeral when prose, not a counter, follows.

    ``Fable五吧`` is not rewritten because 吧 is not a closed continuation, but the
    numeral is still one value. ``Fable五个`` stays unprotected.
    """
    if not _identifier_gap(text, start) or _blocked_prefix(text, start):
        return None
    parsed = _parse_strict_integer(text, start)
    if parsed is None:
        return None
    _value, end = parsed
    if _vague_suffix(text, end) or (end < len(text) and text[end] == "点"):
        return None
    if end < len(text) and (
        text[end] in _MEASURE_CHARS
        or text[end] in _PHRASE_CHARS
        or text[end] in _CONTINUING_NUMERALS
    ):
        return None
    return _identifier_prefix(text, start), end


def _identifier_prefix(text: str, start: int) -> int:
    """Include an ASCII letter identifier glued to a converted numeral."""
    if not _identifier_gap(text, start):
        return start
    cursor = start
    while cursor > 0 and text[cursor - 1].isascii() and text[cursor - 1].isalpha():
        cursor -= 1
    return cursor


def _merge_overlapping_spans(spans: list[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    if not spans:
        return ()
    ordered = sorted(spans)
    merged: list[tuple[int, int]] = [ordered[0]]
    for start, end in ordered[1:]:
        previous_start, previous_end = merged[-1]
        if start < previous_end:
            merged[-1] = (previous_start, max(previous_end, end))
            continue
        merged.append((start, end))
    return tuple(merged)
