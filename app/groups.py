"""设备组：组 id 生成/规范化、组名清洗、成员权限判断。

组 id 刻意做得跟分享码明显不同：``grp_`` 前缀 + 12 位（分享码是 8 位、无前缀），
这样两个东西在聊天里、日志里、界面上都不会被看混。字符集沿用分享码那套
（去掉 I O 0 1 等易混字符），方便口头/手抄传递。

权限模型（v1）：
* 任何**已登记设备**都能建组，创建者即管理员（owner）；
* 谁拿到组 id 谁就能加入（组 id 就是邀请凭证，所以没有单独的"邀请"流程）；
* 管理员可移除组内任意成员、改名、解散组；管理员自己不能用"退出"，只能解散；
* 任何成员都能自己退出；
* 只有同组设备之间才能互传。
"""

from __future__ import annotations

import re
import secrets
from typing import Callable, Optional

from . import config, codes

#: 组 id 前缀（与 8 位分享码一眼区分）
PREFIX = config.GROUP_ID_PREFIX

_GROUP_RE = re.compile(rf"^{re.escape(PREFIX)}[{codes.ALPHABET}]{{{config.GROUP_ID_LENGTH}}}$")
#: 复制粘贴时常见的分隔符（空格、短横线）。注意**不能**把下划线算进来：
#: 组 id 的前缀本身就是 ``grp_``，吃掉下划线会让合法组 id 变成 grpXXXX… 而被判非法。
_SEPARATORS = re.compile(r"[\s\-]+")
_UNSAFE = re.compile(r"[\x00-\x1f\x7f<>]+")
_WS = re.compile(r"\s+")

DEFAULT_GROUP_NAME = "未命名设备组"


def generate_group_id(exists: Optional[Callable[[str], bool]] = None) -> str:
    """生成 ``grp_`` + 12 位随机组 id；``exists`` 用来兜碰撞（32**12 空间，实际几乎不会重）。"""
    while True:
        candidate = PREFIX + "".join(secrets.choice(codes.ALPHABET) for _ in range(config.GROUP_ID_LENGTH))
        if exists is None or not exists(candidate):
            return candidate


def normalize_group_id(raw: Optional[str]) -> str:
    """把粘进来的组 id 收拾成标准形式（忽略大小写/空格/短横线，允许省略 grp_ 前缀）。

    刻意**不做**"把 0 当 O、1 当 I"的猜测纠正：组 id 就是加入组的凭证，
    猜错了会把用户带进别的组，宁可明确报"组 id 不对"让人重抄。
    """
    text = _SEPARATORS.sub("", (raw or "").strip())
    if not text:
        return ""
    if text.lower().startswith(PREFIX):
        text = text[len(PREFIX) :]
    candidate = PREFIX + text.upper()
    return candidate if _GROUP_RE.match(candidate) else ""


def looks_like_group_id(raw: Optional[str]) -> bool:
    return bool(normalize_group_id(raw))


def clean_group_name(raw: Optional[str]) -> str:
    """组名清洗：去掉控制字符与尖括号、压空白、限长；空了就给默认名。"""
    text = _UNSAFE.sub("", (raw or "").strip())
    text = _WS.sub(" ", text).strip()
    if not text:
        return DEFAULT_GROUP_NAME
    return text[: config.GROUP_NAME_MAX_CHARS]


def is_owner(group: object, device_id: Optional[str]) -> bool:
    """创建者即管理员。"""
    return bool(device_id) and getattr(group, "owner_device_id", None) == device_id
