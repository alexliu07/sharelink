"""测试夹具：把数据目录/数据库指向临时路径，保证不污染仓库。

注意：必须在导入 app.* 之前设置环境变量（config 在导入时读取），
所以这里用模块级赋值而不是 fixture。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_TMP_ROOT = Path(tempfile.mkdtemp(prefix="sharelink-tests-"))
os.environ["SHARELINK_DATA_DIR"] = str(_TMP_ROOT / "storage")
os.environ["SHARELINK_DB_PATH"] = str(_TMP_ROOT / "db" / "sharelink.db")
os.environ["SHARELINK_MAX_UPLOAD_MB"] = "1"
os.environ["SHARELINK_CLEANUP_INTERVAL_SECONDS"] = "3600"  # 测试里手动触发清理

import pytest  # noqa: E402

from app import config, db, storage  # noqa: E402


def pytest_sessionfinish(session, exitstatus):  # pragma: no cover - 收尾清理
    shutil.rmtree(_TMP_ROOT, ignore_errors=True)


@pytest.fixture(autouse=True)
def clean_state():
    """每个用例前清空元数据与磁盘文件。"""
    db.init_db()
    db.clear_all()
    if config.DATA_DIR.exists():
        for item in config.DATA_DIR.iterdir():
            if item.is_file():
                item.unlink()
    yield
    db.clear_all()
    for item in config.DATA_DIR.glob("*"):
        item.unlink(missing_ok=True)


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def upload(client):
    """便捷上传夹具：``upload(b"data", "a.txt", "1h")`` → 响应 JSON。"""

    def _upload(content: bytes, filename: str = "hello.txt", ttl: str = "1h", **form):
        files = {"file": (filename, content, "application/octet-stream")}
        data = {"ttl": ttl}
        data.update({k: str(v) for k, v in form.items()})
        resp = client.post("/api/upload", files=files, data=data)
        assert resp.status_code == 201, resp.text
        return resp.json()

    return _upload


@pytest.fixture()
def expire_now():
    """把某个分享码的过期时间改到过去，用于模拟"已到期"。"""

    def _expire(code: str, seconds_ago: int = 5):
        record = db.get_file(code)
        assert record is not None, code
        past = db.utcnow().replace(microsecond=0)
        from datetime import timedelta

        record.expires_at = db.to_iso(past - timedelta(seconds=seconds_ago))
        import sqlite3

        with sqlite3.connect(config.DB_PATH) as conn:
            conn.execute("UPDATE files SET expires_at = ? WHERE code = ?", (record.expires_at, code))
        return record

    return _expire


__all__ = ["storage", "config", "db"]
