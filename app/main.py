"""FastAPI 应用：分享码取件 + 设备互传的 HTTP 接口，以及静态页面。

接口一览
--------
POST   /api/upload                        上传文件（multipart：file + ttl），返回分享码
GET    /api/files/{code}                  查询分享信息（文件名/大小/剩余时间）
GET    /api/download/{code}               凭分享码下载（过期返回 410 并删除）
DELETE /api/files/{code}                  提前取消分享（分享码即凭证）

POST   /api/devices                       把本设备加入设备列表，返回设备 id 与令牌（仅此一次）
GET    /api/devices                       设备列表（公开，供选择投递目标）
PATCH  /api/devices/{id}                  改设备名（需令牌）
DELETE /api/devices/{id}                  注销设备（需令牌）
GET    /api/devices/{id}/inbox            我的收件箱（需令牌）：别人发来的文件与过期时间
POST   /api/devices/{id}/inbox/seen       收件箱标为已读（需令牌）
DELETE /api/devices/{id}/inbox/{code}     从自己的收件箱移掉一条（需令牌）

POST   /api/transfers                     发送至设备（multipart：file + targets 列表）
POST   /api/share-target                  安卓「分享 → ShareLink」的落地页（收文件/文本后 303 回首页并带分享码）

GET    /api/stats                         站点统计
GET    /api/healthz                       存活探针
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import logging
import mimetypes
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import List, Optional

from fastapi import Body, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.datastructures import UploadFile as StarletteUploadFile  # fastapi.UploadFile 是它的子类，multipart 解析出的是父类实例

from . import cleanup, codes, config, db, devices, storage

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

STATIC_DIR = config.BASE_DIR / "static"

DEVICE_TOKEN_HEADER = "X-Device-Token"


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


def _share_urls(request: Request, code: str) -> dict:
    """拼对外链接，带上反代子路径前缀（如 /share）。"""
    base = str(request.base_url).rstrip("/")
    prefix = config.PUBLIC_BASE_PATH
    return {
        "share_url": f"{base}{prefix}/?code={code}",
        "download_url": f"{base}{prefix}/api/download/{code}",
    }


def _store(fileobj, original_name: str, content_type: Optional[str], raw_ttl) -> tuple[db.FileRecord, int]:
    """校验有效期 → 落盘 → 写 files 表；上传、定向投递、分享面板落地都走这里。"""
    try:
        seconds = codes.parse_ttl(raw_ttl)
    except ValueError as exc:
        raise _error(400, "bad_ttl", str(exc)) from exc

    safe_name = storage.safe_original_name(original_name)
    code = codes.generate_unique_code(db.code_exists)
    try:
        saved = storage.save_stream(fileobj, code, safe_name)
    except storage.FileTooLarge as exc:
        raise _error(
            413,
            "too_large",
            f"文件太大，单文件上限 {exc.limit_bytes // (1024 * 1024)} MB",
        ) from exc

    now = db.utcnow()
    record = db.FileRecord(
        code=code,
        original_name=safe_name,
        stored_name=saved.stored_name,
        size=saved.size,
        sha256=saved.sha256,
        content_type=content_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream",
        created_at=db.to_iso(now),
        expires_at=db.to_iso(now + timedelta(seconds=seconds)),
    )
    db.insert_file(record)
    return record, seconds


def _save_and_record(file: StarletteUploadFile, raw_ttl) -> tuple[db.FileRecord, int]:
    """上传接口用的薄封装：关掉上传流再交给 _store。"""
    try:
        return _store(file.file, file.filename or "", file.content_type, raw_ttl)
    finally:
        file.file.close()


def _device_or_403(device_id: str, token: Optional[str]) -> db.DeviceRecord:
    """取设备并校验令牌：设备 id 是公开的，令牌才是"我是这台设备"的凭证。"""
    device = db.get_device(device_id)
    if device is None:
        raise _error(404, "device_not_found", "设备不存在或已注销，请在设备标签页重新添加本设备")
    if not devices.verify_token(device, token):
        raise _error(403, "bad_device_token", "设备令牌无效：这个浏览器不是该设备，或令牌已丢失")
    return device


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
    description="上传文件 → 设置有效期 → 拿分享码取件 / 直接发给其他设备；到期自动删除。",
    version="1.1.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------- 上传 / 分享码
@app.post("/api/upload", status_code=201)
def upload_file(
    request: Request,
    file: UploadFile = File(..., description="要分享的文件"),
    ttl: Optional[str] = Form(None, description="有效期，如 30m / 6h / 7d，或纯秒数"),
    ttl_seconds: Optional[int] = Form(None, description="有效期（秒），优先级高于 ttl"),
):
    record, seconds = _save_and_record(file, ttl_seconds if ttl_seconds is not None else ttl)

    payload = record.to_public_dict()
    payload.update(
        {
            "ttl_seconds": seconds,
            "ttl_human": codes.humanize_seconds(seconds),
            **_share_urls(request, record.code),
        }
    )
    logger.info("新分享 %s（%s，%d 字节，%s）", record.code, record.original_name, record.size, record.expires_at)
    return JSONResponse(payload, status_code=201)


@app.get("/api/files/{code}")
def file_info(code: str):
    return JSONResponse(_resolve_record(code).to_public_dict())


@app.get("/api/download/{code}")
def download_file(code: str):
    record = _resolve_record(code)
    path = record.path
    if not path.exists():
        # 记录在、文件没了（手工删过/磁盘异常）：清掉脏记录并提示
        db.delete_file_row(record.code)
        raise _error(410, "gone", "文件已被清理，无法下载")

    db.register_download(record.code)

    # 交给 FileResponse 生成 Content-Disposition（含 RFC 5987 的中文名编码），
    # 但显式要求 attachment：即使用户上传 HTML/SVG 也只是下载、不在本站执行。
    headers = {
        "X-Share-Code": record.code,
        "X-Content-Type-Options": "nosniff",
    }
    return FileResponse(
        path,
        media_type=record.content_type or "application/octet-stream",
        filename=record.original_name,
        content_disposition_type="attachment",
        headers=headers,
    )


@app.delete("/api/files/{code}")
def delete_file(code: str):
    record = _resolve_record(code)
    cleanup.purge_one(record.code)
    logger.info("分享 %s 已被提前删除", record.code)
    return {"deleted": True, "code": record.code, "filename": record.original_name}


# ---------------------------------------------------------------- 设备
class DeviceCreate(BaseModel):
    # 只挡异常超长的负载；真正的长度上限由 devices.clean_device_name 截断到 40 字符
    name: Optional[str] = Field(default=None, max_length=200, description="设备名，留空按 User-Agent 猜一个")


class DeviceRename(BaseModel):
    name: str = Field(..., max_length=200, description="新的设备名")


@app.post("/api/devices", status_code=201)
def register_device(
    payload: Optional[DeviceCreate] = None,
    user_agent: Optional[str] = Header(default=None, description="用于猜默认设备名"),
):
    """把当前浏览器登记成一台设备，返回设备 id 与令牌（令牌只返回这一次）。"""
    name = devices.clean_device_name((payload.name if payload else None) or devices.guess_device_name(user_agent))

    device_id = devices.generate_device_id()
    for _ in range(12):  # 公开 id 也要避免撞车（8 位字符集下几乎不会重试）
        if not db.device_id_exists(device_id):
            break
        device_id = devices.generate_device_id()
    else:
        raise _error(500, "id_exhausted", "设备 id 生成失败，请重试")

    token = devices.generate_token()
    now = db.utcnow()
    device = db.insert_device(
        db.DeviceRecord(
            id=device_id,
            name=name,
            token_hash=devices.hash_token(token),
            created_at=db.to_iso(now),
            last_seen_at=db.to_iso(now),
        )
    )
    logger.info("新设备 %s（%s）", device.id, device.name)
    return JSONResponse(
        {
            **device.to_public_dict(now),
            "token": token,
            "token_hint": "令牌只返回这一次：浏览器已存在本机 localStorage，换浏览器或清缓存后需重新登记",
        },
        status_code=201,
    )


@app.get("/api/devices")
def list_devices():
    now = db.utcnow()
    items = []
    for device in db.list_devices(now):
        item = device.to_public_dict(now)
        item["inbox_count"] = db.count_device_inbox(device.id)
        items.append(item)
    return {"count": len(items), "devices": items}


@app.patch("/api/devices/{device_id}")
def rename_device(
    device_id: str,
    payload: DeviceRename,
    x_device_token: Optional[str] = Header(default=None, alias=DEVICE_TOKEN_HEADER),
):
    device = _device_or_403(device_id, x_device_token)
    name = devices.clean_device_name(payload.name)
    db.touch_device(device.id, name=name)
    updated = db.get_device(device.id)
    assert updated is not None
    return {**updated.to_public_dict(), "renamed": name != device.name}


@app.delete("/api/devices/{device_id}")
def unregister_device(
    device_id: str,
    x_device_token: Optional[str] = Header(default=None, alias=DEVICE_TOKEN_HEADER),
):
    device = _device_or_403(device_id, x_device_token)
    db.delete_device(device.id)
    logger.info("设备 %s（%s）已注销", device.id, device.name)
    return {"deleted": True, "id": device.id, "name": device.name}


# ---------------------------------------------------------------- 收件箱
@app.get("/api/devices/{device_id}/inbox")
def device_inbox(
    request: Request,
    device_id: str,
    x_device_token: Optional[str] = Header(default=None, alias=DEVICE_TOKEN_HEADER),
):
    """发给本设备的文件列表（含剩余有效时间）；访问即算心跳。"""
    device = _device_or_403(device_id, x_device_token)
    now = db.utcnow()
    db.touch_device(device.id, now=now)  # 打开网页看收件箱 = 这台设备在线

    items = []
    for transfer, file_record in db.list_inbox(device.id, now=now):
        item = file_record.to_public_dict(now)
        item.update(
            {
                "transfer_id": transfer.id,
                "from_device_id": transfer.from_device_id,
                "from_name": transfer.from_name,
                "note": transfer.note,
                "sent_at": transfer.created_at,
                "seen": transfer.seen_at is not None,
                **_share_urls(request, file_record.code),
            }
        )
        items.append(item)

    return {
        "device": device.to_public_dict(now),
        "count": len(items),
        "unread": db.count_unread(device.id, now=now),
        "items": items,
    }


@app.post("/api/devices/{device_id}/inbox/seen")
def mark_inbox_seen(
    device_id: str,
    x_device_token: Optional[str] = Header(default=None, alias=DEVICE_TOKEN_HEADER),
):
    device = _device_or_403(device_id, x_device_token)
    return {"marked": db.mark_inbox_seen(device.id)}


@app.delete("/api/devices/{device_id}/inbox/{code}")
def remove_inbox_item(
    device_id: str,
    code: str,
    x_device_token: Optional[str] = Header(default=None, alias=DEVICE_TOKEN_HEADER),
):
    """把一条投递从自己的收件箱里移掉（文件本身不受影响，分享码仍可下载）。"""
    device = _device_or_403(device_id, x_device_token)
    normalized = codes.normalize_code(code)
    if not db.delete_transfer(device.id, normalized):
        raise _error(404, "not_in_inbox", "收件箱里没有这条记录")
    return {"removed": True, "code": normalized}


# ---------------------------------------------------------------- 发送至设备
@app.post("/api/transfers", status_code=201)
def send_to_devices(
    request: Request,
    file: UploadFile = File(..., description="要发送的文件"),
    targets: str = Form("", description="目标设备 id，逗号分隔（可多选；为空则报 400）"),
    ttl: Optional[str] = Form(None, description="有效期，如 30m / 6h / 7d"),
    ttl_seconds: Optional[int] = Form(None, description="有效期（秒），优先级高于 ttl"),
    note: str = Form("", description="附言"),
    from_device_id: Optional[str] = Form(None, description="发送设备的 id（可选）"),
    from_name: Optional[str] = Form(None, description="发送者名称（未登记设备时使用）"),
    x_device_token: Optional[str] = Header(default=None, alias=DEVICE_TOKEN_HEADER),
):
    """把一份文件投递给一台或多台设备：文件只存一份，每台目标设备各有一条投递记录。"""
    target_ids = devices.normalize_targets((targets or "").split(","))
    if not target_ids:
        raise _error(400, "no_target", "请至少选择一台目标设备")
    if len(target_ids) > config.MAX_TARGETS_PER_SEND:
        raise _error(
            400, "too_many_targets", f"一次最多发给 {config.MAX_TARGETS_PER_SEND} 台设备（当前选了 {len(target_ids)} 台）"
        )

    target_devices: List[db.DeviceRecord] = []
    unknown: List[str] = []
    for device_id in target_ids:
        device = db.get_device(device_id)
        if device is None:
            unknown.append(device_id)
        else:
            target_devices.append(device)
    if unknown:
        raise _error(400, "bad_target", "以下设备不存在或已注销：" + "、".join(unknown))

    # 发送方身份：带上自己设备的 id + 令牌才算"实名"，否则用填的名称（或匿名）
    sender: Optional[db.DeviceRecord] = None
    if from_device_id:
        candidate = db.get_device(from_device_id)
        if candidate and devices.verify_token(candidate, x_device_token):
            sender = candidate
    sender_name = devices.clean_device_name(sender.name if sender else (from_name or "匿名设备"))
    clean_note = devices.clean_note(note)

    record, seconds = _save_and_record(file, ttl_seconds if ttl_seconds is not None else ttl)

    for target in target_devices:
        db.insert_transfer(
            db.TransferRecord(
                id=0,
                code=record.code,
                device_id=target.id,
                from_device_id=sender.id if sender else None,
                from_name=sender_name,
                note=clean_note,
                created_at=record.created_at,
                expires_at=record.expires_at,
            )
        )

    logger.info(
        "投递 %s（%s，%d 字节）→ %s，来自 %s",
        record.code,
        record.original_name,
        record.size,
        "、".join(f"{t.name}({t.id})" for t in target_devices),
        sender_name,
    )

    payload = record.to_public_dict()
    payload.update(
        {
            "ttl_seconds": seconds,
            "ttl_human": codes.humanize_seconds(seconds),
            "from_name": sender_name,
            "from_device_id": sender.id if sender else None,
            "note": clean_note,
            "targets": [{"id": t.id, "name": t.name} for t in target_devices],
            "transfer_count": len(target_devices),
            **_share_urls(request, record.code),
        }
    )
    return JSONResponse(payload, status_code=201)


# ---------------------------------------------------------------- 安卓分享面板
def _page_url(request: Request) -> str:
    base = str(request.base_url).rstrip("/")
    return f"{base}{config.PUBLIC_BASE_PATH}/"


def _wants_json(request: Request) -> bool:
    """第三方分享 App 要 JSON、浏览器要跳转：显式 `?response=json`，或 Accept 里既没 html 也没通配。"""
    if request.query_params.get("response") == "json":
        return True
    accept = request.headers.get("accept", "*/*")
    return "text/html" not in accept and "*/*" not in accept


@app.post("/api/share-target")
async def android_share_target(request: Request):
    """分享落地点：浏览器（PWA 分享面板）和第三方分享 App（Hupl / MyShare / 自动化）都打这里。

    - 表单字段名不固定：文件名/附言认 `title`/`text`/`url`，文件部分**任意字段名**都收（取第一个），
      因为各家客户端 multipart 的 part 名不一样（`file`/`myfile`/`files[]`…）。
    - 默认 303 回首页带 `?code=`（浏览器走这条，看完就有码）。
    - 传 `?response=json` 或 Accept 里不要 HTML 时，改成 200 返回 JSON，方便第三方 App 用正则把分享链接抠出来。
    """
    form = await request.form()
    uploads = [value for _, value in form.multi_items() if isinstance(value, StarletteUploadFile)]
    title = str(form.get("title") or "")
    text = str(form.get("text") or form.get("url") or "")
    raw_ttl = form.get("ttl_seconds") or form.get("ttl")

    if uploads:
        if len(uploads) > 1:
            logger.info("分享面板一次送了 %d 个文件，只取第一个：%s", len(uploads), uploads[0].filename)
        for extra in uploads[1:]:
            await extra.close()
        record, seconds = _save_and_record(uploads[0], raw_ttl)
    else:
        body = text.strip() or title.strip()
        if not body:
            if _wants_json(request):
                return JSONResponse({"error": "empty_share", "message": "分享的内容是空的"}, status_code=400)
            return RedirectResponse(f"{_page_url(request)}?share=empty", status_code=303)
        stem = storage.safe_original_name(title).strip() if title.strip() else ""  # 空标题会得到"未命名文件"
        name = f"{stem}.txt" if stem else "分享文本.txt"
        record, seconds = _store(io.BytesIO(text.strip().encode("utf-8") or body.encode("utf-8")),
                                 name, "text/plain; charset=utf-8", raw_ttl)

    logger.info("分享面板收到 %s（%d 字节，%s）→ 分享码 %s",
                record.original_name, record.size, record.content_type, record.code)
    if _wants_json(request):
        return JSONResponse({**_share_urls(request, record.code), "code": record.code,
                             "filename": record.original_name, "size": record.size,
                             "sha256": record.sha256, "expires_at": record.expires_at,
                             "ttl_seconds": seconds,
                             "ttl_human": codes.humanize_seconds(seconds)})
    return RedirectResponse(f"{_share_urls(request, record.code)['share_url']}&share=ok", status_code=303)


# ---------------------------------------------------------------- 运维
@app.get("/api/stats")
def site_stats():
    db_stats = db.stats()
    disk_stats = storage.disk_usage()
    return {
        "shares": db_stats["files"],
        "shares_bytes": db_stats["bytes"],
        "devices": db_stats["devices"],
        "transfers": db_stats["transfers"],
        "disk_files": disk_stats["files"],
        "disk_bytes": disk_stats["bytes"],
        "cleanup_interval_seconds": config.CLEANUP_INTERVAL_SECONDS,
        "device_idle_days": config.DEVICE_IDLE_DAYS,
        "max_targets_per_send": config.MAX_TARGETS_PER_SEND,
        "max_upload_mb": config.MAX_UPLOAD_MB,
        "default_ttl_seconds": config.DEFAULT_TTL_SECONDS,
        "min_ttl_seconds": config.MIN_TTL_SECONDS,
        "max_ttl_seconds": config.MAX_TTL_SECONDS,
    }


@app.get("/api/healthz")
def healthz():
    return {"status": "ok", "version": app.version}


# ---------------------------------------------------------------- 静态资源缓存策略
# 反代（nginx 会剥掉 /share 前缀）与 Cloudflare 都按这些头决定缓存行为：
#   - sw.js 必须 no-cache，否则 CF 会按 .js 默认规则缓存 4 小时，service worker 更新被拖住
#   - 外壳文件（页面/脚本/样式/manifest）也走 no-cache：浏览器与 CF 每次带 ETag 回源校验，
#     命中就 304（开销极小），部署后刷新一次即生效，不用再教用户 Ctrl+Shift+R
#   - 图标这类几乎不变的内容给长缓存
#   - 接口响应一律 no-store：文件有"过期即删"的语义，绝不能进任何中间层缓存
_SHELL_PATHS = {"/", "/index.html", "/app.js", "/style.css", "/sw.js", "/favicon.svg", "/manifest.webmanifest"}
_LONG_CACHE_PREFIXES = ("/icons/",)


@app.middleware("http")
async def cache_policy(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    elif path in _SHELL_PATHS:
        response.headers["Cache-Control"] = "no-cache"
    elif path.startswith(_LONG_CACHE_PREFIXES):
        response.headers["Cache-Control"] = "public, max-age=604800"
    return response


# ---------------------------------------------------------------- 顶层清单（manifest）
SHARE_TARGET_ACTION_PLACEHOLDER = "__SHARE_TARGET_ACTION__"


@app.get("/manifest.webmanifest")
def web_manifest(request: Request):
    """动态吐 manifest：把 share_target.action 换成**绝对 URL**。

    踩过的坑：action 写成相对路径时，安卓 Chrome 生成 WebAPK 有几率解析不出来，
    应用就永远不出现在系统分享面板里（Chrome 文档的示例还是相对路径，实际会翻车）。
    绝对地址按请求推导，所以本地 uvicorn、反代子路径、Cloudflare 后面都对。
    """
    raw = (STATIC_DIR / "manifest.webmanifest").read_text(encoding="utf-8")
    action = f"{str(request.base_url).rstrip('/')}{config.PUBLIC_BASE_PATH}/api/share-target"
    manifest = json.loads(raw.replace(SHARE_TARGET_ACTION_PLACEHOLDER, action))
    logger.info("manifest: share_target.action=%s", manifest["share_target"]["action"])
    return JSONResponse(content=manifest, media_type="application/manifest+json")


# 静态页面挂在最后，保证 /api/* 优先匹配
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
