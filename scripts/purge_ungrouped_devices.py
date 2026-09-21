#!/usr/bin/env python3
"""一次性维护脚本：删掉"一个设备组都没加入"的设备。

背景（用户决定）：设备组上线后，投递规则变成「必须实名 + 每台目标都与自己同组」。
一台既没建组、也没加入任何组的设备，在新规则下既发不出去、也没人会发给它，
留在设备列表里只会让旧客户端产生"看得到却发不了"的困惑，所以统一清掉，
各设备重新登记 + 建组/凭组 id 加入即可。

删除会级联带走：
  * 该设备的收件箱投递记录；
  * 它作为**管理员**的设备组（组随之解散，其他成员会看到组消失）。
已经生成的分享码/文件**不受影响**（分享码那条路本来就不依赖设备）。

用法：
    .venv/bin/python scripts/purge_ungrouped_devices.py          # 只列出来，不动数据
    .venv/bin/python scripts/purge_ungrouped_devices.py --yes    # 真删
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, db  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="删除未加入任何设备组的设备")
    parser.add_argument("--yes", action="store_true", help="真的执行删除（默认只预览）")
    args = parser.parse_args()

    db.init_db()
    now = db.utcnow()
    devices = db.list_devices(now)
    targets = [device for device in devices if not db.device_group_ids(device.id)]

    print(f"数据库：{config.DB_PATH}")
    print(f"设备总数 {len(devices)}，其中未加入任何设备组的有 {len(targets)} 台：")
    for device in targets:
        print(
            f"  - {device.id}  {device.name}"
            f"  注册于 {device.created_at}  最后活跃 {device.last_seen_at}"
            f"  收件箱 {db.count_device_inbox(device.id)} 条"
            f"  名下设备组 {db.count_groups_owned(device.id)} 个"
        )

    if not targets:
        print("\n没有需要清理的设备。")
        return 0
    if not args.yes:
        print("\n（预览模式，未改动任何数据；确认无误后加 --yes 执行）")
        return 0

    for device in targets:
        owned = db.count_groups_owned(device.id)
        db.delete_device(device.id)
        note = f"（顺带解散它名下的 {owned} 个组）" if owned else ""
        print(f"  已删除 {device.id}  {device.name}{note}")

    remaining = db.list_devices(db.utcnow())
    print(f"\n完成：设备数 {len(devices)} → {len(remaining)}，剩下的每台都至少属于一个设备组。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
