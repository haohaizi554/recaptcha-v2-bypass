"""
audio_solver.py 单元测试
========================
测试音频识别中的纯逻辑函数:
  - _extract_digits(): 从识别文本提取数字
    支持: 英文单词 / 阿拉伯数字 / 序数词 / 混合形式

不依赖 faster-whisper / speech_recognition / 浏览器,
可在无 ML 模型的环境中运行.
"""

import pytest

from audio_solver import AudioRecaptchaSolver

# ============================================================
# _extract_digits 测试
# ============================================================
# _extract_digits 是实例方法但不使用 self, 可通过传 None 调用
# 这样避免创建 AudioRecaptchaSolver 实例 (其 __init__ 需要 page 参数)
_extract = AudioRecaptchaSolver._extract_digits


class TestExtractDigits:
    """测试数字提取逻辑"""

    # ---- 阿拉伯数字 ----
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("1 2 3 4 5", "12345"),
            ("0", "0"),
            ("9 8 7", "987"),
            ("1, 2, 3", "123"),
            ("1; 2; 3", "123"),
        ],
    )
    def test_numeric_digits(self, text, expected):
        """纯阿拉伯数字"""
        assert _extract(None, text) == expected

    # ---- 英文单词 ----
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("one two three", "123"),
            ("zero one two", "012"),
            ("seven eight nine", "789"),
            ("four five six", "456"),
        ],
    )
    def test_english_words(self, text, expected):
        """英文数字单词"""
        assert _extract(None, text) == expected

    # ---- 序数词 ----
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("first second third", "123"),
            ("fourth fifth sixth", "456"),
            ("seventh eighth ninth", "789"),
        ],
    )
    def test_ordinal_words(self, text, expected):
        """英文序数词"""
        assert _extract(None, text) == expected

    # ---- 混合形式 ----
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("one 2 three", "123"),
            ("first 2 3rd", "123"),
            ("1st 2nd 3rd", "123"),
        ],
    )
    def test_mixed_format(self, text, expected):
        """混合数字/单词/序数词"""
        assert _extract(None, text) == expected

    # ---- 大小写无关 ----
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("ONE TWO THREE", "123"),
            ("One Two Three", "123"),
            ("First Second Third", "123"),
        ],
    )
    def test_case_insensitive(self, text, expected):
        """大小写不影响识别"""
        assert _extract(None, text) == expected

    # ---- 边界情况 ----
    def test_empty_string(self):
        """空字符串返回空"""
        assert _extract(None, "") == ""

    def test_none_input(self):
        """None 输入返回空"""
        assert _extract(None, None) == ""

    def test_no_digits(self):
        """无数字内容返回空"""
        assert _extract(None, "hello world") == ""

    def test_with_noise(self):
        """含噪声文本仍能提取数字"""
        # Whisper 可能输出 "The numbers are one two three please enter"
        assert _extract(None, "The numbers are one two three please enter") == "123"

    def test_punctuation_separated(self):
        """标点分隔的数字"""
        assert _extract(None, "one, two, three.") == "123"
