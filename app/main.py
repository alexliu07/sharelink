"""FastAPI 应用：分享码取件 HTTP 接口 + 静态页面。

接口一览
--------
POST   /api/upload          上传文件（multipart：file + ttl），返回分享码
GET    /api/files/{code}    查询分享信息（文件名/大小/剩余时间）
GET    /api/download/{code} 凭分享码下载（过期返回 410 并删除）
DELETE /api/files/{code}    提前取消分享（分享码即凭证）
GET    /api/stats           站点统计
GET    /api/healthz         存活探针
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import mimetypes
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import cleanup, codes, config, db, storage

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

STATIC_DIR = config.BASE_DIR / "static"


def _error(status_code: int, error: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error": error, "message": message})


def _resolve_record(code: str) -> db.FileRecord:
    """规范化分享码 → 取记录；处理格式错误、不存在、已过期（顺手删除）。"""
    normalized = codes.normalize_code(code)
    if not codes.is_valid_format(normalized):
        raise _error(400, "bad_code", f"分享码格式不对，应为 {config.CODE_LENGTH} 位字母数字（如 AB3D7K9M）")

    record = db.get_file(normalized)
    if record is None:
        raise _error(404, "not_found", "分享码不存在，或文件已过期被清理")

    if record.is_expired():
        cleanup.purge_one(record.code)  # 惰性删除：过期即删
        raise _error(410, "expired", "分享已过期，文件已被删除")
    return record


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    stop_event = asyncio.Event()
    task = asyncio.create_task(cleanup.cleanup_loop(stop_event=stop_event))
    app.state.cleanup_task = task
    logger.info("ShareLink 启动：数据目录 %s，数据库 %s", config.DATA_DIR, config.DB_PATH)
    try:
        yield
    finally:
        stop_event.set()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await asyncio.wait_for(task, timeout=5)


app = FastAPI(
    title="ShareLink",
    description="上传文件 → 设置有效期 → 拿分享码取件；到期自动删除。",
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------- 上传
@app.post("/api/upload", status_code=201)
def upload_file(
    request: Request,
    file: UploadFile = File(..., description="要分享的文件"),
    ttl: Optional[str] = Form(None, description="有效期，如 30m / 6h / 7d，或纯秒数"),
    ttl_seconds: Optional[int] = Form(None, description="有效期（秒），优先级高于 ttl"),
):
    raw_ttl = ttl_seconds if ttl_seconds is not None else ttl
    try:
        seconds = codes.parse_ttl(raw_ttl)
    except ValueError as exc:
        raise _error(400, "bad_ttl", str(exc)) from exc

    original_name = storage.safe_original_name(file.filename)
    code = codes.generate_unique_code(db.code_exists)

    try:
        saved = storage.save_stream(file.file, code, original_name)
    except storage.FileTooLarge as exc:
        raise _error(
            413,
            "too_large",
            f"文件太大，单文件上限 {exc.limit_bytes // (1024 * 1024)} MB",
        ) from exc
    finally:
        file.file.close()

    now = db.utcnow()
    record = db.FileRecord(
        code=code,
        original_name=original_name,
        stored_name=saved.stored_name,
        size=saved.size,
        sha256=saved.sha256,
        content_type=file.content_type or mimetypes.guess_type(original_name)[0] or "application/octet-stream",
        created_at=db.to_iso(now),
        expires_at=db.to_iso(now + timedelta(seconds=seconds)),
    )
    db.insert_file(record)

    base = str(request.base_url).rstrip("/")
    payload = record.to_public_dict(now)
    payload.update(
        {
            "ttl_seconds": seconds,
            "ttl_human": codes.humanize_seconds(seconds),
            "share_url": f"{base}/?code={code}",
            "download_url": f"{base}/api/download/{code}",
        }
    )
    logger.info("新分享 %s（%s，%d 字节，%s）", code, original_name, saved.size, record.expires_at)
    return JSONResponse(payload, status_code=201)


# ---------------------------------------------------------------- 查询 / 下载
@app.get("/api/files/{code}")
def file_info(code: str):
    record = _resolve_record(code)
    return JSONResponse(record.to_public_dict())


@app.get("/api/download/{code}")
def download_file(code: str):
    record = _resolve_record(code)
    path = record.path
    if not path.exists():
        # 记录在、文件没了（手工删过/磁盘异常）：清掉脏记录并提示
        db.delete_file_row(record.code)
        raise _error(410, "gone", "文件已被清理，无法下载")

    db.register_download(record.code)

    # 强制 attachment + nosniff：即使用户上传 HTML/SVG 也不会在本站执行
    headers = {
        "X-Share-Code": record.code,
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": "attachment",
    }
    return FileResponse(
        path,
        media_type=record.content_type or "application/octet-stream",
        filename=record.original_name,
        headers=headers,
    )


@app.delete("/api/files/{code}")
def delete_file(code: str):
    record = _resolve_record(code)
    cleanup.purge_one(record.code)
    logger.info("分享 %s 已被提前删除", record.code)
    return {"deleted": True, "code": record.code, "filename": record.original_name}


# ---------------------------------------------------------------- 运维
@app.get("/api/stats")
def site_stats():
    db_stats = db.stats()
    disk_stats = storage.disk_usage()
    return {
        "shares": db_stats["files"],
        "shares_bytes": db_stats["bytes"],
        "disk_files": disk_stats["files"],
        "disk_bytes": disk_stats["bytes"],
        "cleanup_interval_seconds": config.CLEANUP_INTERVAL_SECONDS,
        "max_upload_mb": config.MAX_UPLOAD_MB,
        "default_ttl_seconds": config.DEFAULT_TTL_SECONDS,
        "min_ttl_seconds": config.MIN_TTL_SECONDS,
        "max_ttl_seconds": config.MAX_TTL_SECONDS,
    }


@app.get("/api/healthz")
def healthz():
    return {"status": "ok", "version": app.version}


# 静态页面挂在最后，保证 /api/* 优先匹配
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
