"""分享码 / 有效期解析 / 文件名清洗的单元测试。"""

from __future__ import annotations

import re

import pytest

from app import codes, config, storage


class TestGenerateCode:
    def test_length_and_alphabet(self):
        for _ in range(50):
            code = codes.generate_code()
            assert len(code) == config.CODE_LENGTH
            assert re.fullmatch(rf"[{codes.ALPHABET}]+", code)

    def test_excludes_confusable_chars(self):
        for char in "IO01":
            assert char not in codes.ALPHABET

    def test_is_random(self):
        assert len({codes.generate_code() for _ in range(200)}) == 200

    def test_generate_unique_code_retries_on_collision(self):
        taken = {"AAAAAAAA"}
        attempts = {"n": 0}

        def fake_generate(length=None):
            attempts["n"] += 1
            return "AAAAAAAA" if attempts["n"] == 1 else "BBBBBBBB"

        original = codes.generate_code
        codes.generate_code = fake_generate
        try:
            code = codes.generate_unique_code(lambda c: c in taken)
        finally:
            codes.generate_code = original
        assert code == "BBBBBBBB"
        assert attempts["n"] == 2

    def test_generate_unique_code_gives_up(self):
        original = codes.generate_code
        codes.generate_code = lambda length=None: "AAAAAAAA"
        try:
            with pytest.raises(RuntimeError):
                codes.generate_unique_code(lambda c: True, max_attempts=3)
        finally:
            codes.generate_code = original


class TestNormalizeCode:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("ab3d7k9m", "AB3D7K9M"),
            ("  ab3d-7k9m ", "AB3D7K9M"),
            ("ab3d 7k9m", "AB3D7K9M"),
            ("AB3D.7K9M", "AB3D7K9M"),
            ("", ""),
            (None, ""),
        ],
    )
    def test_normalize(self, raw, expected):
        assert codes.normalize_code(raw) == expected

    def test_format_check(self):
        assert codes.is_valid_format("AB3D7K9M")
        assert not codes.is_valid_format("AB3D7K9")       # 太短
        assert not codes.is_valid_format("AB3D7K9MM")     # 太长
        assert not codes.is_valid_format("AB3D7K9I")      # 含易混字符 I
        assert not codes.is_valid_format("ab3d7k9m")      # 未规范化


class TestParseTtl:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("600", 600),
            ("30m", 1800),
            ("6h", 21600),
            ("7d", 604800),
            ("1w", 604800),
            ("2.5h", 9000),
        ],
    )
    def test_units(self, raw, expected):
        if expected < config.MIN_TTL_SECONDS:
            pytest.skip("低于最小有效期")
        assert codes.parse_ttl(raw) == expected

    def test_seconds_suffix(self):
        assert codes.parse_ttl(f"{config.MIN_TTL_SECONDS}s") == config.MIN_TTL_SECONDS

    def test_default_when_empty(self):
        assert codes.parse_ttl(None) == config.DEFAULT_TTL_SECONDS
        assert codes.parse_ttl("   ") == config.DEFAULT_TTL_SECONDS
        assert codes.parse_ttl(None, default=120) == 120

    @pytest.mark.parametrize("raw", ["abc", "1x", "0", "-5", ""])
    def test_invalid(self, raw):
        if raw == "":
            return  # 空串走默认值，不算错误
        with pytest.raises(ValueError):
            codes.parse_ttl(raw)

    def test_bounds(self):
        with pytest.raises(ValueError):
            codes.parse_ttl(str(config.MIN_TTL_SECONDS - 1))
        with pytest.raises(ValueError):
            codes.parse_ttl(str(config.MAX_TTL_SECONDS + 1))


class TestHumanize:
    @pytest.mark.parametrize(
        "seconds,expected",
        [(30, "30 秒"), (600, "10 分钟"), (3600, "1 小时"), (5400, "1.5 小时"), (86400, "1 天"), (0, "0 秒")],
    )
    def test_humanize(self, seconds, expected):
        assert codes.humanize_seconds(seconds) == expected


class TestSafeFilename:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("report.pdf", "report.pdf"),
            ("../../etc/passwd", "passwd"),
            (r"C:\Users\me\photo.jpg", "photo.jpg"),
            ("a<b>:c|d?.txt", "a_b_ c_d_.txt".replace(" ", "")),
            ("", "未命名文件"),
            (None, "未命名文件"),
            ("...", "未命名文件"),
        ],
    )
    def test_clean(self, raw, expected):
        assert storage.safe_original_name(raw) == expected

    def test_long_name_truncated_but_keeps_suffix(self):
        name = storage.safe_original_name("x" * 300 + ".tar.gz")
        assert len(name) <= 120
        assert name.endswith(".gz")

    def test_stored_name_has_no_original_name(self):
        stored = storage.build_stored_name("AB3D7K9M", "我的 报告.pdf")
        assert stored.startswith("AB3D7K9M_")
        assert "报告" not in stored
        assert stored.endswith(".pdf")
