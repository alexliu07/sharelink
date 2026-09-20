"""过期清理测试：手动 purge + 后台定时任务真跑一遍。"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from app import cleanup, config, db, storage


def _make_record(code: str, content: bytes = b"data", ttl_seconds: int = 3600, filename: str = "a.txt") -> db.FileRecord:
    """直接落库一条记录（绕过 HTTP，便于精确控制过期时间）。"""
    saved = storage.save_stream(_BytesReader(content), code, filename)
    now = db.utcnow()
    record = db.FileRecord(
        code=code,
        original_name=filename,
        stored_name=saved.stored_name,
        size=saved.size,
        sha256=saved.sha256,
        content_type="application/octet-stream",
        created_at=db.to_iso(now),
        expires_at=db.to_iso(now + timedelta(seconds=ttl_seconds)),
    )
    return db.insert_file(record)


class _BytesReader:
    """把 bytes 包装成带 read() 的流，模拟 UploadFile.file。"""

    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            chunk, self._pos = self._data[self._pos:], len(self._data)
            return chunk
        chunk = self._data[self._pos:self._pos + size]
        self._pos += len(chunk)
        return chunk


class TestPurgeExpired:
    def test_deletes_only_expired(self):
        alive = _make_record("ALIVEAAA", ttl_seconds=3600)
        dying = _make_record("DYINGAAA", b"bye", ttl_seconds=-5)

        result = cleanup.purge_expired()

        assert result["count"] == 1
        assert result["codes"] == ["DYINGAAA"]
        assert result["freed_bytes"] == 3
        assert not dying.path.exists()
        assert db.get_file("DYINGAAA") is None
        assert db.get_file("ALIVEAAA") is not None
        assert alive.path.exists()

    def test_idempotent_when_nothing_expired(self):
        _make_record("ALIVEAAA", ttl_seconds=600)
        assert cleanup.purge_expired()["count"] == 0
        assert cleanup.purge_expired()["count"] == 0

    def test_missing_file_still_clears_record(self):
        record = _make_record("GONEGONE", ttl_seconds=-1)
        record.path.unlink()  # 文件已被手工删除

        assert cleanup.purge_expired()["count"] == 1
        assert db.get_file("GONEGONE") is None

    def test_respects_limit(self):
        for i in range(5):
            _make_record(f"EXPIRED{i}", ttl_seconds=-10 - i)
        result = cleanup.purge_expired(limit=3)
        assert result["count"] == 3
        assert db.stats()["files"] == 2

    def test_purge_one(self):
        record = _make_record("ONEONEOO", ttl_seconds=3600)
        assert cleanup.purge_one("ONEONEOO") is True
        assert not record.path.exists()
        assert cleanup.purge_one("ONEONEOO") is False  # 已删，幂等


class TestCleanupLoop:
    def test_background_loop_deletes_expired_files(self):
        """真跑后台协程：1 秒间隔，2.5 秒内应把过期文件清掉。"""
        dying = _make_record("LOOPEXPI", b"to-be-deleted", ttl_seconds=-2)
        alive = _make_record("LOOPALIV", b"keep-me", ttl_seconds=3600)

        async def run():
            stop = asyncio.Event()
            task = asyncio.create_task(cleanup.cleanup_loop(interval=1, stop_event=stop))
            await asyncio.sleep(2.5)
            stop.set()
            await asyncio.wait_for(task, timeout=5)

        asyncio.run(run())

        assert db.get_file("LOOPEXPI") is None
        assert not dying.path.exists()
        assert db.get_file("LOOPALIV") is not None
        assert alive.path.exists()
        assert config.CLEANUP_INTERVAL_SECONDS > 0
