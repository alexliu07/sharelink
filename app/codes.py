"""分享码：生成、规范化、格式校验。

字符集刻意去掉 ``I O 0 1`` 等肉眼易混字符（保证分享码可以被口头/手抄传递），
因此 32 个字符、8 位的空间为 32**8 ≈ 1.1e12，随机碰撞概率可忽略；
落库时仍会做存在性检查并重试。
"""

from __future__ import annotations

import re
import secrets
from typing import Callable, Optional  # noqa: F401  (Optional 供类型注解使用)

from . import config

#: 分享码字符集（32 个，Base32 风格，去掉易混字符）
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

_CODE_RE = re.compile(rf"^[{ALPHABET}]{{{config.CODE_LENGTH}}}$")

#: 用户输入里允许被忽略的分隔符（复制粘贴常见）
_SEPARATORS = re.compile(r"[\s\-_.]+")


def generate_code(length: Optional[int] = None) -> str:
    """生成一个随机分享码。"""
    length = length or config.CODE_LENGTH
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


def normalize_code(raw: str) -> str:
    """把用户输入规范成大写、去掉分隔符的分享码。

    ``ab3d-7k9m`` / ``ab3d 7k9m`` → ``AB3D7K9M``
    """
    if not raw:
        return ""
    return _SEPARATORS.sub("", str(raw).strip()).upper()


def is_valid_format(code: str) -> bool:
    """判断是否形如合法的分享码（不含存在性校验）。"""
    return bool(_CODE_RE.match(code or ""))


def generate_unique_code(exists: Callable[[str], bool], max_attempts: int = 12) -> str:
    """生成一个 ``exists(code)`` 为假的分享码。

    32**8 的空间下几乎不会重试；重试耗尽时抛错而不是覆盖已有文件。
    """
    for _ in range(max_attempts):
        code = generate_code()
        if not exists(code):
            return code
    raise RuntimeError(f"连续 {max_attempts} 次未能生成未占用的分享码")


def parse_ttl(raw: Optional[str], default: Optional[int] = None) -> int:
    """把有效期字符串解析成秒数。

    支持 ``600``（秒）、``30m``、``6h``、``7d``、``1w``，大小写不敏感。
    超出 ``MIN_TTL_SECONDS``/``MAX_TTL_SECONDS`` 会抛 ``ValueError``。
    """
    if raw is None or str(raw).strip() == "":
        seconds = config.DEFAULT_TTL_SECONDS if default is None else default
    else:
        text = str(raw).strip().lower()
        multiplier = 1
        if text and text[-1] in "smhdw":
            unit = text[-1]
            multiplier = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[unit]
            text = text[:-1]
        try:
            value = float(text)
        except ValueError:
            raise ValueError(f"无法识别的有效期限：{raw!r}（示例：30m / 6h / 7d）") from None
        if value <= 0:
            raise ValueError("有效期限必须大于 0")
        seconds = int(value * multiplier)

    if seconds < config.MIN_TTL_SECONDS:
        raise ValueError(f"有效期限太短，最少 {config.MIN_TTL_SECONDS} 秒（当前 {seconds} 秒）")
    if seconds > config.MAX_TTL_SECONDS:
        raise ValueError(
            f"有效期限太长，最多 {config.MAX_TTL_SECONDS} 秒（约 "
            f"{config.MAX_TTL_SECONDS // 86400} 天）"
        )
    return seconds


def humanize_seconds(seconds: int) -> str:
    """把秒数转成中文可读文案，用于前端展示。"""
    seconds = max(int(seconds), 0)
    if seconds < 60:
        return f"{seconds} 秒"
    if seconds < 3600:
        return f"{seconds // 60} 分钟"
    if seconds < 86400:
        hours = seconds / 3600
        return f"{hours:.0f} 小时" if hours % 1 == 0 else f"{hours:.1f} 小时"
    days = seconds / 86400
    return f"{days:.0f} 天" if days % 1 == 0 else f"{days:.1f} 天"
