"""设备互传的领域逻辑：设备 id / 令牌、名称清洗、UA 猜测、收件箱视图。

设计前提：本服务没有账号体系。
- **设备 id** 是公开的（出现在设备列表里，供别人选为投递目标）；
- **设备令牌** 只在该设备登记时返回一次，浏览器存 localStorage，
  之后读写自己的收件箱都要带上它（请求头 ``X-Device-Token``）。
  库里只存令牌的 SHA256，泄库也拿不到可用的令牌。
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from typing import Iterable, Optional

from . import codes, config, db

#: 设备令牌长度（urlsafe base64 的字节数）
TOKEN_BYTES = 24

#: 设备名长度上限（清洗后）
MAX_NAME_LEN = 40
#: 一次最多投递给多少台设备
MAX_TARGETS = 20
#: 备注长度上限
MAX_NOTE_LEN = 200

_WS = re.compile(r"\s+")
_UNSAFE = re.compile(r"[\x00-\x1f\x7f<>]+")

DEFAULT_DEVICE_NAME = "未命名设备"


def generate_device_id() -> str:
    """公开设备 id：``dev_`` + 8 位分享码字符集（去掉易混字符）。"""
    return "dev_" + codes.generate_code(config.CODE_LENGTH)


def generate_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def verify_token(device: db.DeviceRecord, token: Optional[str]) -> bool:
    """常数时间比对比对令牌哈希，避免时序侧信道。"""
    if not token or not device:
        return False
    return hmac.compare_digest(device.token_hash, hash_token(token))


def clean_device_name(raw: Optional[str]) -> str:
    """清洗设备名：去控制字符/尖括号、折叠空白、限长，空则给默认名。"""
    name = _UNSAFE.sub(" ", (raw or "").strip())
    name = _WS.sub(" ", name).strip()
    if not name:
        return DEFAULT_DEVICE_NAME
    return name[:MAX_NAME_LEN]


def clean_note(raw: Optional[str]) -> str:
    note = _UNSAFE.sub(" ", (raw or "").strip())
    note = _WS.sub(" ", note).strip()
    return note[:MAX_NOTE_LEN]


def guess_device_name(user_agent: Optional[str]) -> str:
    """按 User-Agent 猜一个顺手的默认设备名，例如 ``Chrome · Windows``。"""
    ua = user_agent or ""
    browser = next(
        (name for token, name in (
            ("Edg", "Edge"), ("OPR", "Opera"), ("Firefox", "Firefox"),
            ("Chrome", "Chrome"), ("Safari", "Safari"),
        ) if token in ua),
        "浏览器",
    )
    system = next(
        (name for token, name in (
            ("Windows", "Windows"), ("iPhone", "iPhone"), ("iPad", "iPad"),
            ("Android", "Android"), ("Mac OS X", "macOS"), ("Linux", "Linux"),
        ) if token in ua),
        "",
    )
    return clean_device_name(f"{browser} · {system}" if system else browser)


def normalize_targets(target_ids: Iterable[str]) -> list[str]:
    """去重、去空、保序地规范化目标设备 id 列表。"""
    seen: set[str] = set()
    out: list[str] = []
    for raw in target_ids:
        device_id = (raw or "").strip()
        if not device_id or device_id in seen:
            continue
        seen.add(device_id)
        out.append(device_id)
    return out
