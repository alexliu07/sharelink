"""过期清理：惰性校验（取件时）+ 后台定时任务双保险。

- 惰性：任何人用过期分享码访问，立刻删文件 + 删记录，返回 410 Gone。
- 定时：后台协程按 ``CLEANUP_INTERVAL_SECONDS`` 扫库，保证"没人访问也照样删"。
"""

from __future__ import annotations

import asyncio
import logging
import contextlib
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from . import config, db, storage

logger = logging.getLogger(__name__)


def purge_expired(now: Optional[datetime] = None, limit: int = 500) -> Dict[str, object]:
    """删除所有已过期记录及其文件，返回本次清理明细。"""
    now = now or db.utcnow()
    expired = db.list_expired(now=now, limit=limit)

    codes: List[str] = []
    freed = 0
    for record in expired:
        storage.delete_stored_file(record.path)
        if db.delete_file_row(record.code):
            # 投递记录跟着文件一起走，收件箱里不会留下取不到的死条目
            db.delete_transfers_for_code(record.code)
            freed += record.size
            codes.append(record.code)

    if codes:
        logger.info("已清理 %d 个过期文件，释放 %d 字节：%s", len(codes), freed, ", ".join(codes))
    return {"count": len(codes), "codes": codes, "freed_bytes": freed, "at": db.to_iso(now)}


def purge_one(code: str) -> bool:
    """删除单个分享码对应的文件与记录（过期删除 + 主动取消分享共用）。"""
    record = db.get_file(code)
    if record is None:
        return False
    storage.delete_stored_file(record.path)
    removed = db.delete_file_row(code)
    db.delete_transfers_for_code(code)
    return removed


def prune_idle_devices(now: Optional[datetime] = None, days: Optional[int] = None) -> List[str]:
    """回收长期未活跃且收件箱为空的设备，避免设备列表无限膨胀。"""
    now = now or db.utcnow()
    days = config.DEVICE_IDLE_DAYS if days is None else days
    cutoff = now - timedelta(days=days)
    removed = db.prune_idle_devices(cutoff, now=now)
    if removed:
        logger.info("已回收 %d 台长期未活跃设备：%s", len(removed), ", ".join(removed))
    return removed


async def cleanup_loop(
    interval: Optional[int] = None,
    stop_event: Optional[asyncio.Event] = None,
) -> None:
    """后台循环：周期性调用 ``purge_expired``。

    清理是同步 IO，丢到线程里执行，避免阻塞事件循环。
    """
    interval = interval or config.CLEANUP_INTERVAL_SECONDS
    stop_event = stop_event or asyncio.Event()
    logger.info("过期清理任务已启动，间隔 %s 秒", interval)

    while not stop_event.is_set():
        try:
            result = await asyncio.to_thread(purge_expired)
            if result["count"]:
                logger.info("定时清理完成：%s 个文件", result["count"])
            await asyncio.to_thread(prune_idle_devices)
        except Exception:  # 清理失败不能让整个服务挂掉
            logger.exception("过期清理任务出错，将在下一轮重试")
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=interval)

    logger.info("过期清理任务已停止")
