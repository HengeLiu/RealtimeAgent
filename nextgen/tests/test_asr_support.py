"""ASR 支持代码测试。"""

from nextgen.apps.server.gateway.asr_support import (
    InterruptKeywordMatcher,
    extract_sentence,
    normalize_cn_text,
    safe_to_dict,
    shorten_text,
)


def test_extract_sentence_reads_nested_sentence() -> None:
    """验证可从嵌套 sentence 字段提取文本和句末标记。"""

    event = {"output": {"sentence": {"text": "帮我找一下手机", "sentence_end": True}}}
    text, is_end = extract_sentence(event)
    assert text == "帮我找一下手机"
    assert is_end is True


def test_extract_sentence_falls_back_to_text_field() -> None:
    """验证无 sentence 结构时可回退到 text 字段。"""

    event = {"result": {"text": "继续前进"}}
    text, is_end = extract_sentence(event)
    assert text == "继续前进"
    assert is_end is None


def test_interrupt_keyword_matcher_detects_hotword() -> None:
    """验证热词匹配器能命中中文打断词。"""

    matcher = InterruptKeywordMatcher(keywords=["停下", "别说了"])
    assert matcher.has_hotword("请你先停下")
    assert matcher.has_hotword("现在别说了")
    assert not matcher.has_hotword("继续寻找杯子")


def test_safe_to_dict_and_helpers_keep_basic_behavior() -> None:
    """验证基础辅助函数行为稳定。"""

    assert safe_to_dict({"text": "abc"}) == {"text": "abc"}
    assert normalize_cn_text(" 停下 ") == "停下"
    assert shorten_text("abcdef", limit=3) == "abc…"
