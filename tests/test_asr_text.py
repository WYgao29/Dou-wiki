from __future__ import annotations

import inspect

import pytest

from douyin_wiki.adapters.asr_text import (
    clean_qwen_text,
    normalize_asr_text,
    normalize_zh_numbers,
)

EXPLICIT_NUMBERS = [
    ("五点六", "5.6"),
    ("五点六三", "5.63"),
    ("五十九分", "59分"),
    ("一百五十万", "150万"),
    ("百分之五十", "50%"),
    ("Fable五", "Fable 5"),
    ("五点六三个模型", "5.6 三个模型"),
    ("十二点零五", "12.05"),
    ("零点五", "0.5"),
    ("百分之三点五", "3.5%"),
    ("百分之百", "100%"),
    ("两小时", "2小时"),
    ("二十分钟", "20分钟"),
    ("十分钟", "10分钟"),
    ("三十秒", "30秒"),
    ("一万二千元", "12000元"),
    ("两千零五元", "2005元"),
    ("三亿元", "3亿元"),
    ("一百五十万元", "150万元"),
    ("两千万", "2000万"),
    ("十五点三元", "15.3元"),
    ("五点六小时", "5.6小时"),
    ("GPT四", "GPT 4"),
    ("iOS十六", "iOS 16"),
    ("Fable五点六", "Fable 5.6"),
    ("Fable五点六三个模型", "Fable 5.6 三个模型"),
    ("约五十九分。", "约59分。"),
    ("今天走了三步，用了五十九分，模型有五点六三个。", "今天走了三步，用了59分，模型有5.6 三个。"),
    ("Keep CASE 五点六", "Keep CASE 5.6"),
    ("百分之五十，一百元。", "50%，100元。"),
    ("晚上五十元", "晚上50元"),
    ("两千万元", "2000万元"),
    ("三万二千元", "32000元"),
    ("一千万元", "1000万元"),
    ("一百零五分", "105分"),
    ("两分三十秒", "2分30秒"),
    ("Fable五。", "Fable 5。"),
    ("Fable五十", "Fable 50"),
    ("一百分", "100分"),
    ("3.14五点六", "3.14 5.6"),
    ("3.五点六", "3. 5.6"),
    ("一百元", "100元"),
    ("一百元左右，两分三十秒。", "一百元左右，2分30秒。"),
]

PRESERVED_PHRASES = [
    "一下",
    "三步",
    "三个模型",
    "十几分钟",
    "一心一意",
    "三维",
    "Hello, World!",
    "ENGLISH stays English",
    "已有 3.14 和 GPT-4。",
    "Fable 5",
    "Fable五个",
    "Fable五十个",
    "Model三个方案",
    "MP3五",
    "二十多分钟",
    "二十多万",
    "一百五十几万",
    "十几万",
    "几十秒",
    "好几百元",
    "上千元",
    "五十左右",
    "十点多",
    "五点六左右",
    "一百五",
    "一万五",
    "一百五万元",
    "三万二千",
    "两点十分",
    "五点六分",
    "三点零五秒",
    "十分好",
    "一个半小时",
    "半小时",
    "价格是一百。",
    "晚上十元",
    "百分之五十个",
    "百分之十几",
    "百分五十",
    "百分比",
    "千分之五十",
    "一分为二",
    "他一分为二地看问题。",
    "二分法",
    "二分查找",
    "三元组",
    "一元二次方程",
    "一分耕耘",
    "一元复始",
    "一百五十万左右",
    "一百元左右",
    "五十九分左右",
    "三十秒左右",
]


@pytest.mark.parametrize(
    "phrase",
    [
        "一分为二",
        "他一分为二地看问题。",
        "二分法",
        "二分查找",
        "三元组",
        "一元二次方程",
        "一分耕耘",
        "一元复始",
    ],
)
def test_ordinary_unit_stems_stay_unchanged(phrase: str) -> None:
    assert normalize_zh_numbers(phrase) == phrase


@pytest.mark.parametrize(
    "phrase",
    ["一百五十万左右", "一百元左右", "五十九分左右", "三十秒左右"],
)
def test_hedge_after_full_unit_stays_unchanged(phrase: str) -> None:
    assert normalize_zh_numbers(phrase) == phrase


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("两分三十秒", "2分30秒"),
        ("一百元", "100元"),
        ("一百五十万", "150万"),
        ("五十九分", "59分"),
    ],
)
def test_measured_quantities_beside_ambiguous_stems(raw: str, expected: str) -> None:
    assert normalize_zh_numbers(raw) == expected


@pytest.mark.parametrize(("raw", "expected"), EXPLICIT_NUMBERS)
def test_normalize_explicit_numbers(raw: str, expected: str) -> None:
    assert normalize_zh_numbers(raw) == expected


@pytest.mark.parametrize("phrase", PRESERVED_PHRASES)
def test_preserve_ambiguous_phrases(phrase: str) -> None:
    assert normalize_zh_numbers(phrase) == phrase


def test_number_normalization_is_idempotent() -> None:
    samples = [raw for raw, _expected in EXPLICIT_NUMBERS]
    samples.extend(PRESERVED_PHRASES)
    samples.extend(["5.6 三个模型", "50%", "Fable 5", "150万。", "已有3.5不变"])
    for sample in samples:
        once = normalize_zh_numbers(sample)
        assert normalize_zh_numbers(once) == once


def test_clean_qwen_tags_without_changing_prose() -> None:
    assert clean_qwen_text("language Chinese<asr_text>你好，世界。") == "你好，世界。"
    assert clean_qwen_text("language English<asr_text>Hello, World!") == "Hello, World!"
    assert clean_qwen_text("language None<asr_text>自动识别。") == "自动识别。"
    assert clean_qwen_text("<|im_start|>language Chinese<asr_text>正文<|im_end|>") == "正文"
    assert clean_qwen_text("你好，<|endoftext|>世界！") == "你好，世界！"
    assert clean_qwen_text("（<asr_text>保留）") == "（保留）"
    assert clean_qwen_text("结束。<|im_end|>") == "结束。"
    assert clean_qwen_text("A<|zh|>B") == "AB"
    assert clean_qwen_text("前</asr_text>后") == "前后"
    assert clean_qwen_text("五点六") == "五点六"
    assert clean_qwen_text("中文是一种语言。") == "中文是一种语言。"
    assert clean_qwen_text("Chinese people use English.") == "Chinese people use English."
    assert clean_qwen_text("language models matter.") == "language models matter."
    assert clean_qwen_text("language Chinese，然后继续说。") == "language Chinese，然后继续说。"
    spoken_language = "他说 language Chinese 但没有标记。"
    assert clean_qwen_text(spoken_language) == spoken_language
    assert clean_qwen_text("比较 1 < 2 且 a < b。") == "比较 1 < 2 且 a < b。"
    assert clean_qwen_text("保留<|未闭合") == "保留<|未闭合"
    assert clean_qwen_text("  你好") == "  你好"
    assert clean_qwen_text("<|im_end|>\nlanguage English<asr_text>Hi.") == "Hi."


def test_itn_can_be_disabled() -> None:
    signature = inspect.signature(normalize_asr_text)
    assert list(signature.parameters) == ["text", "itn"]
    assert signature.parameters["itn"].kind is inspect.Parameter.KEYWORD_ONLY

    raw = "language Chinese<asr_text>百分之五十，五点六三个模型。"
    assert normalize_asr_text(raw, itn=False) == "百分之五十，五点六三个模型。"
    assert normalize_asr_text(raw, itn=True) == "50%，5.6 三个模型。"

    prose = "language is not a prefix。三个模型。"
    assert normalize_asr_text(prose, itn=False) == prose
    assert normalize_asr_text(prose, itn=True) == prose

    tagged = "价钱<|money|>五十九分<asr_text>。"
    assert normalize_asr_text(tagged, itn=False) == "价钱五十九分。"
    assert normalize_asr_text(tagged, itn=True) == "价钱59分。"
    assert normalize_asr_text("五点<asr_text>六", itn=False) == "五点六"
    assert normalize_asr_text("五点<asr_text>六", itn=True) == "5.6"
    assert normalize_asr_text(normalize_asr_text(raw, itn=True), itn=True) == "50%，5.6 三个模型。"
