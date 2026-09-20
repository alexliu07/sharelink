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

-- 设备：一个浏览器登记成一台设备，id 公开（用于收件），token 只在登记时返回一次
CREATE TABLE IF NOT EXISTS devices (
    id           TEXT PRIMARY KEY,
    name         TEXT    NOT NULL,
    token_hash   TEXT    NOT NULL,
    created_at   TEXT    NOT NULL,
    last_seen_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_devices_last_seen ON devices (last_seen_at);

-- 投递记录：一次"发送至设备"给每个目标设备写一行，指向同一份文件（同一个分享码）
CREATE TABLE IF NOT EXISTS transfers (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    code           TEXT    NOT NULL,
    device_id      TEXT    NOT NULL,
    from_device_id TEXT,
    from_name      TEXT    NOT NULL DEFAULT '',
    note           TEXT    NOT NULL DEFAULT '',
    created_at     TEXT    NOT NULL,
    expires_at     TEXT    NOT NULL,
    seen_at        TEXT,
    FOREIGN KEY (code)      REFERENCES files (code)   ON DELETE CASCADE,
    FOREIGN KEY (device_id) REFERENCES devices (id)   ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_transfers_device ON transfers (device_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_transfers_code   ON transfers (code);
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
    # 打开外键：files/devices 被删时，对应的 transfers 自动级联删除
    conn.execute("PRAGMA foreign_keys=ON")
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
        conn.execute("DELETE FROM transfers")
        conn.execute("DELETE FROM devices")
        conn.execute("DELETE FROM files")


def stats() -> dict:
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS total, COALESCE(SUM(size), 0) AS bytes FROM files"
        ).fetchone()
        devices = conn.execute("SELECT COUNT(*) AS n FROM devices").fetchone()["n"]
        transfers = conn.execute("SELECT COUNT(*) AS n FROM transfers").fetchone()["n"]
    return {"files": row["total"], "bytes": row["bytes"], "devices": devices, "transfers": transfers}


# ============================================================ 设备与投递
@dataclass
class DeviceRecord:
    """一台已登记的设备（``token_hash`` 永不对外返回）。"""

    id: str
    name: str
    token_hash: str
    created_at: str
    last_seen_at: str

    @property
    def created_dt(self) -> datetime:
        return from_iso(self.created_at)

    @property
    def last_seen_dt(self) -> datetime:
        return from_iso(self.last_seen_at)

    def idle_seconds(self, now: Optional[datetime] = None) -> int:
        return max(int(((now or utcnow()) - self.last_seen_dt).total_seconds()), 0)

    def to_public_dict(self, now: Optional[datetime] = None) -> dict:
        """设备列表里对所有人可见的字段（不含令牌相关）。"""
        now = now or utcnow()
        return {
            "id": self.id,
            "name": self.name,
            "created_at": self.created_at,
            "last_seen_at": self.last_seen_at,
            "idle_seconds": self.idle_seconds(now),
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "DeviceRecord":
        return cls(
            id=row["id"],
            name=row["name"],
            token_hash=row["token_hash"],
            created_at=row["created_at"],
            last_seen_at=row["last_seen_at"],
        )


@dataclass
class TransferRecord:
    """一条"发送至某设备"的投递记录，指向一份已落库的文件。"""

    id: int
    code: str
    device_id: str
    from_device_id: Optional[str]
    from_name: str
    note: str
    created_at: str
    expires_at: str
    seen_at: Optional[str] = None

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

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "TransferRecord":
        return cls(
            id=row["id"],
            code=row["code"],
            device_id=row["device_id"],
            from_device_id=row["from_device_id"],
            from_name=row["from_name"],
            note=row["note"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            seen_at=row["seen_at"],
        )


def insert_device(device: DeviceRecord) -> DeviceRecord:
    with connect() as conn:
        conn.execute(
            """INSERT INTO devices (id, name, token_hash, created_at, last_seen_at)
               VALUES (?, ?, ?, ?, ?)""",
            (device.id, device.name, device.token_hash, device.created_at, device.last_seen_at),
        )
    return device


def device_id_exists(device_id: str) -> bool:
    with connect() as conn:
        return conn.execute("SELECT 1 FROM devices WHERE id = ? LIMIT 1", (device_id,)).fetchone() is not None


def get_device(device_id: str) -> Optional[DeviceRecord]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
    return DeviceRecord.from_row(row) if row else None


def list_devices(now: Optional[datetime] = None) -> List[DeviceRecord]:
    """设备列表：最近活跃的在前。"""
    with connect() as conn:
        rows = conn.execute("SELECT * FROM devices ORDER BY last_seen_at DESC, name").fetchall()
    return [DeviceRecord.from_row(row) for row in rows]


def touch_device(device_id: str, name: Optional[str] = None, now: Optional[datetime] = None) -> bool:
    """更新设备心跳时间（可选改名），返回是否命中设备。"""
    stamp = to_iso(now or utcnow())
    with connect() as conn:
        if name is None:
            cur = conn.execute("UPDATE devices SET last_seen_at = ? WHERE id = ?", (stamp, device_id))
        else:
            cur = conn.execute(
                "UPDATE devices SET last_seen_at = ?, name = ? WHERE id = ?", (stamp, name, device_id)
            )
    return cur.rowcount > 0


def delete_device(device_id: str) -> bool:
    """注销设备；其收件箱记录随外键级联删除。"""
    with connect() as conn:
        cur = conn.execute("DELETE FROM devices WHERE id = ?", (device_id,))
    return cur.rowcount > 0


def prune_idle_devices(cutoff: datetime, now: Optional[datetime] = None) -> List[str]:
    """回收长期未活跃、且收件箱里没有待取文件的设备，返回被删的 id。"""
    now = now or utcnow()
    with connect() as conn:
        rows = conn.execute(
            """SELECT d.id FROM devices d
               WHERE d.last_seen_at < ?
                 AND NOT EXISTS (
                     SELECT 1 FROM transfers t
                     WHERE t.device_id = d.id AND t.expires_at > ?
                 )
               ORDER BY d.last_seen_at""",
            (to_iso(cutoff), to_iso(now)),
        ).fetchall()
        ids = [row["id"] for row in rows]
        for device_id in ids:
            conn.execute("DELETE FROM devices WHERE id = ?", (device_id,))
    return ids


def insert_transfer(transfer: TransferRecord) -> TransferRecord:
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO transfers
               (code, device_id, from_device_id, from_name, note, created_at, expires_at, seen_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                transfer.code,
                transfer.device_id,
                transfer.from_device_id,
                transfer.from_name,
                transfer.note,
                transfer.created_at,
                transfer.expires_at,
                transfer.seen_at,
            ),
        )
        transfer.id = int(cur.lastrowid or 0)
    return transfer


def list_inbox(
    device_id: str, now: Optional[datetime] = None, limit: int = 200
) -> List[tuple[TransferRecord, FileRecord]]:
    """某设备的收件箱：与 files 内联，只返回文件仍在的文件（已过期的自然消失）。"""
    now = now or utcnow()
    with connect() as conn:
        rows = conn.execute(
            """SELECT t.*, f.original_name, f.size, f.sha256, f.content_type,
                      f.stored_name, f.created_at AS file_created_at,
                      f.expires_at AS file_expires_at, f.download_count, f.last_download_at
               FROM transfers t
               JOIN files f ON f.code = t.code
               WHERE t.device_id = ? AND f.expires_at > ?
               ORDER BY t.created_at DESC, t.id DESC
               LIMIT ?""",
            (device_id, to_iso(now), limit),
        ).fetchall()

    items: List[tuple[TransferRecord, FileRecord]] = []
    for row in rows:
        file_record = FileRecord(
            code=row["code"],
            original_name=row["original_name"],
            stored_name=row["stored_name"],
            size=row["size"],
            sha256=row["sha256"],
            content_type=row["content_type"],
            created_at=row["file_created_at"],
            expires_at=row["file_expires_at"],
            download_count=row["download_count"],
            last_download_at=row["last_download_at"],
        )
        items.append((TransferRecord.from_row(row), file_record))
    return items


def count_unread(device_id: str, now: Optional[datetime] = None) -> int:
    """收件箱里还没看过的条数（用于标签页角标）。"""
    now = now or utcnow()
    with connect() as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS n FROM transfers t
               JOIN files f ON f.code = t.code
               WHERE t.device_id = ? AND f.expires_at > ? AND t.seen_at IS NULL""",
            (device_id, to_iso(now)),
        ).fetchone()
    return row["n"]


def mark_inbox_seen(device_id: str, now: Optional[datetime] = None) -> int:
    """把该设备收件箱标为已读，返回本次标记的条数。"""
    stamp = to_iso(now or utcnow())
    with connect() as conn:
        cur = conn.execute(
            "UPDATE transfers SET seen_at = ? WHERE device_id = ? AND seen_at IS NULL",
            (stamp, device_id),
        )
    return cur.rowcount


def delete_transfer(device_id: str, code: str) -> bool:
    """从某设备的收件箱里移掉一条（不影响文件本身）。"""
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM transfers WHERE device_id = ? AND code = ?", (device_id, code)
        )
    return cur.rowcount > 0


def delete_transfers_for_code(code: str) -> int:
    with connect() as conn:
        cur = conn.execute("DELETE FROM transfers WHERE code = ?", (code,))
    return cur.rowcount


def count_device_inbox(device_id: str) -> int:
    with connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM transfers WHERE device_id = ?", (device_id,)).fetchone()
    return row["n"]
