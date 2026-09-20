"""文件本体落盘：流式写入 + 大小限制 + SHA256 + 安全删除。

磁盘上只保留随机名（``<分享码>_<随机后缀>``），原始文件名存库里，
避免用户的文件名影响磁盘路径（目录穿越、非法字符、重名覆盖）。
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import secrets
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Optional

from . import config

logger = logging.getLogger(__name__)

_UNSAFE_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')
_MAX_NAME_LEN = 120


class FileTooLarge(Exception):
    """上传超过大小限制；携带已写入字节数，便于前端提示。"""

    def __init__(self, limit_bytes: int, written: int = 0):
        self.limit_bytes = limit_bytes
        self.written = written
        super().__init__(f"文件超过上限 {limit_bytes} 字节（已写入 {written} 字节）")


@dataclass
class SavedFile:
    stored_name: str
    path: Path
    size: int
    sha256: str

    @property
    def absolute_path(self) -> str:
        return str(self.path)


def safe_original_name(raw: Optional[str]) -> str:
    """清洗浏览器传来的文件名：去掉目录部分与非法字符。"""
    name = unicodedata.normalize("NFC", (raw or "").strip())
    # 兼容 Windows 客户端上传的 "C:\\dir\\file.txt"
    name = name.replace("\\", "/").split("/")[-1]
    name = _UNSAFE_CHARS.sub("_", name).strip(" .")
    if not name:
        name = "未命名文件"
    if len(name) > _MAX_NAME_LEN:
        stem, dot, suffix = name.rpartition(".")
        if dot and len(suffix) <= 10:
            name = stem[: _MAX_NAME_LEN - len(suffix) - 1] + "." + suffix
        else:
            name = name[:_MAX_NAME_LEN]
    return name


def build_stored_name(code: str, original_name: str) -> str:
    """磁盘文件名：分享码 + 随机后缀（保留原扩展名，方便运维排查）。"""
    suffix = Path(original_name).suffix
    if len(suffix) > 16 or not suffix[1:].isalnum():
        suffix = ""
    return f"{code}_{secrets.token_hex(4)}{suffix}"


def save_stream(
    source: BinaryIO,
    code: str,
    original_name: str,
    max_bytes: Optional[int] = None,
) -> SavedFile:
    """把上传流写入磁盘，返回落盘信息。

    先写 ``.part`` 临时文件，校验完成后 ``os.replace`` 原子改名，
    因此中途超限/断连不会留下半个正式文件。
    """
    max_bytes = config.MAX_UPLOAD_BYTES if max_bytes is None else max_bytes
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)

    stored_name = build_stored_name(code, original_name)
    target = config.DATA_DIR / stored_name
    temp = target.with_suffix(target.suffix + ".part")

    digest = hashlib.sha256()
    written = 0
    try:
        with open(temp, "wb") as out:
            while True:
                chunk = source.read(config.CHUNK_SIZE)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise FileTooLarge(max_bytes, written)
                digest.update(chunk)
                out.write(chunk)
        os.replace(temp, target)
    except FileTooLarge:
        temp.unlink(missing_ok=True)
        raise
    except BaseException:
        temp.unlink(missing_ok=True)
        raise

    return SavedFile(stored_name=stored_name, path=target, size=written, sha256=digest.hexdigest())


def delete_stored_file(path: Path | str | None) -> bool:
    """删除磁盘文件；不存在也算成功（幂等），便于清理任务重跑。"""
    if not path:
        return False
    target = Path(path)
    try:
        target.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:  # 权限、占用等：记录后继续，元数据仍会被清掉
        logger.warning("删除文件失败：%s（%s）", target, exc)
        return False


def disk_usage() -> dict:
    """统计存储目录占用，供 /api/stats 使用。"""
    total = 0
    count = 0
    if config.DATA_DIR.exists():
        for item in config.DATA_DIR.iterdir():
            if item.is_file():
                count += 1
                total += item.stat().st_size
    return {"files": count, "bytes": total}
