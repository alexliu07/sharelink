"""SQLite 元数据层：一次上传 = 一行记录。

文件本体放磁盘（``config.DATA_DIR``），这里只存元数据，
使"到期删除"只需删一行 + 删一个文件，无需遍历目录。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    code             TEXT PRIMARY KEY,
    original_name    TEXT    NOT NULL,
    stored_name      TEXT    NOT NULL,
    size             INTEGER NOT NULL,
    sha256           TEXT    NOT NULL DEFAULT '',
    content_type     TEXT    NOT NULL DEFAULT 'application/octet-stream',
    created_at       TEXT    NOT NULL,
    expires_at       TEXT    NOT NULL,
    download_count   INTEGER NOT NULL DEFAULT 0,
    last_download_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_files_expires_at ON files (expires_at);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(moment: datetime) -> str:
    """统一存成 UTC ISO8601（秒精度，带 Z 后缀）。"""
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def from_iso(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


@dataclass
class FileRecord:
    code: str
    original_name: str
    stored_name: str
    size: int
    sha256: str
    content_type: str
    created_at: str
    expires_at: str
    download_count: int = 0
    last_download_at: Optional[str] = None

    # 便于测试/序列化的额外字段
    _extra: dict = field(default_factory=dict, repr=False)

    @property
    def expires_dt(self) -> datetime:
        return from_iso(self.expires_at)

    @property
    def created_dt(self) -> datetime:
        return from_iso(self.created_at)

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        return self.expires_dt <= (now or utcnow())

    def seconds_left(self, now: Optional[datetime] = None) -> int:
        return max(int((self.expires_dt - (now or utcnow())).total_seconds()), 0)

    @property
    def path(self) -> Path:
        return config.DATA_DIR / self.stored_name

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "FileRecord":
        return cls(
            code=row["code"],
            original_name=row["original_name"],
            stored_name=row["stored_name"],
            size=row["size"],
            sha256=row["sha256"],
            content_type=row["content_type"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            download_count=row["download_count"],
            last_download_at=row["last_download_at"],
        )

    def to_public_dict(self, now: Optional[datetime] = None) -> dict:
        """对外暴露的字段（分享码本身就是凭证，故一并返回）。"""
        now = now or utcnow()
        return {
            "code": self.code,
            "filename": self.original_name,
            "size": self.size,
            "sha256": self.sha256,
            "content_type": self.content_type,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "seconds_left": self.seconds_left(now),
            "expired": self.is_expired(now),
            "download_count": self.download_count,
        }


def connect() -> sqlite3.Connection:
    """新建一个连接（FastAPI 同步接口跑在线程池里，按需建连最稳妥）。"""
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db() -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)


def insert_file(record: FileRecord) -> FileRecord:
    with connect() as conn:
        conn.execute(
            """INSERT INTO files
               (code, original_name, stored_name, size, sha256, content_type,
                created_at, expires_at, download_count, last_download_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.code,
                record.original_name,
                record.stored_name,
                record.size,
                record.sha256,
                record.content_type,
                record.created_at,
                record.expires_at,
                record.download_count,
                record.last_download_at,
            ),
        )
    return record


def get_file(code: str) -> Optional[FileRecord]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM files WHERE code = ?", (code,)).fetchone()
    return FileRecord.from_row(row) if row else None


def code_exists(code: str) -> bool:
    with connect() as conn:
        return conn.execute("SELECT 1 FROM files WHERE code = ? LIMIT 1", (code,)).fetchone() is not None


def register_download(code: str, now: Optional[datetime] = None) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE files SET download_count = download_count + 1, last_download_at = ? WHERE code = ?",
            (to_iso(now or utcnow()), code),
        )


def delete_file_row(code: str) -> bool:
    with connect() as conn:
        cur = conn.execute("DELETE FROM files WHERE code = ?", (code,))
    return cur.rowcount > 0


def list_expired(now: Optional[datetime] = None, limit: int = 500) -> List[FileRecord]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM files WHERE expires_at <= ? ORDER BY expires_at LIMIT ?",
            (to_iso(now or utcnow()), limit),
        ).fetchall()
    return [FileRecord.from_row(row) for row in rows]


def clear_all() -> None:
    """仅测试用：清空元数据表。"""
    with connect() as conn:
        conn.execute("DELETE FROM files")


def stats() -> dict:
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS total, COALESCE(SUM(size), 0) AS bytes FROM files"
        ).fetchone()
    return {"files": row["total"], "bytes": row["bytes"]}
