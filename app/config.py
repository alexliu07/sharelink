"""环境变量与全局配置。

所有配置均可在启动时通过环境变量覆盖（前缀 ``SHARELINK_``），
默认值面向单机小规模部署。
"""

from __future__ import annotations

import os
from pathlib import Path

# 项目根目录（app/ 的上一级）
BASE_DIR = Path(__file__).resolve().parent.parent


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:  # 配置写错时直接启动失败，避免带病运行
        raise RuntimeError(f"环境变量 {name} 必须是整数，当前为 {raw!r}") from exc


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw).expanduser() if raw else default


def _env_base_path(name: str, default: str = "") -> str:
    """对外访问的路径前缀，规范成 ``""`` 或 ``"/prefix"``（结尾不带斜杠）。"""
    raw = (os.environ.get(name) or "").strip()
    if not raw or raw == "/":
        return default
    if any(ch in raw for ch in "?#"):
        raise RuntimeError(f"环境变量 {name} 只能是路径前缀（如 /share），当前为 {raw!r}")
    return "/" + raw.strip("/")


#: 上传文件的落盘目录
DATA_DIR = _env_path("SHARELINK_DATA_DIR", BASE_DIR / "storage")
#: SQLite 元数据库
DB_PATH = _env_path("SHARELINK_DB_PATH", BASE_DIR / "data" / "sharelink.db")

#: 对外访问的路径前缀：服务挂在 https://host/share/ 下就设为 /share，
#: 返回的 share_url / download_url 会自动带上它（默认空，即挂在根路径）。
PUBLIC_BASE_PATH = _env_base_path("SHARELINK_PUBLIC_BASE_PATH")

#: 单个文件大小上限
MAX_UPLOAD_MB = _env_int("SHARELINK_MAX_UPLOAD_MB", 200)
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

# 「发文本」：字符数上限（UTF-8 最坏 4 字节/字符，另留点余量）
MAX_TEXT_CHARS = _env_int("SHARELINK_MAX_TEXT_CHARS", 10000)
MAX_TEXT_BYTES = MAX_TEXT_CHARS * 4 + 1024
# 多大的文本文件可以在网页/App 里内联显示（而不是只给下载）
MAX_INLINE_TEXT_BYTES = _env_int("SHARELINK_MAX_INLINE_TEXT_BYTES", 64 * 1024)

#: 有效期限（秒）
DEFAULT_TTL_SECONDS = _env_int("SHARELINK_DEFAULT_TTL_SECONDS", 3600)
MIN_TTL_SECONDS = _env_int("SHARELINK_MIN_TTL_SECONDS", 60)
MAX_TTL_SECONDS = _env_int("SHARELINK_MAX_TTL_SECONDS", 30 * 24 * 3600)

#: 后台清理任务扫描间隔（秒）
CLEANUP_INTERVAL_SECONDS = _env_int("SHARELINK_CLEANUP_INTERVAL_SECONDS", 60)

#: 设备多久没活跃（且收件箱为空）就从设备列表里回收（天）
DEVICE_IDLE_DAYS = _env_int("SHARELINK_DEVICE_IDLE_DAYS", 30)

#: 一次"发送至设备"最多选多少台目标设备
MAX_TARGETS_PER_SEND = _env_int("SHARELINK_MAX_TARGETS_PER_SEND", 20)

#: 分享码长度（字符集见 codes.ALPHABET）
CODE_LENGTH = 8

#: 读写文件的分块大小（1 MiB）
CHUNK_SIZE = 1024 * 1024
