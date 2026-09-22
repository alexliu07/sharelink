#!/usr/bin/env python3
"""线上自检（只读验证，不改服务端）：注销一台设备后，它在设备组里的身份会怎样？

两个场景，都对着**真实服务端**跑，并且每步既看接口返回、也直接读 SQLite
（WAL 模式，只 SELECT）确认行真的没了：

  A) 注销**管理员**：设备 A 建组、B 加入，然后注销 A
     → 组还在吗？B 还在组里吗？已注销的设备还会出现在设备/组名单里吗？
  B) 注销**组员**：设备 A2 建组、B2 加入，然后注销 B2
     → A2 那边的成员数 / 名单是否同步减少？

跑完自动清理：注销所有测试设备（注销管理员会连带他的组），并断言
「跑前的设备 id 集合 / 设备组数」原封不动。

用法：.venv/bin/python scripts/live_unregister_group_check.py [base_url] [db_path]
默认 http://127.0.0.1:18000 与仓库的 data/sharelink.db。
"""
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18000"
DB_PATH = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(
    os.environ.get("SHARELINK_DB_PATH") or (BASE_DIR / "data" / "sharelink.db")
)

passed, failed = 0, []


def check(label, ok, detail=""):
    global passed
    if ok:
        passed += 1
        print(f"  ✅ {label}{(' — ' + detail) if detail else ''}")
    else:
        failed.append(label)
        print(f"  ❌ {label}{(' — ' + detail) if detail else ''}")


def call(method, path, payload=None, token=None):
    body = None
    headers = {"User-Agent": "ShareLink-UnregisterCheck/1"}
    if payload is not None:
        body = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["X-Device-Token"] = token
    request = urllib.request.Request(BASE + path, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as err:
        raw = err.read()
        try:
            return err.code, json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return err.code, {"raw": raw[:200].decode("utf-8", "ignore")}


def error_of(payload) -> str:
    detail = (payload or {}).get("detail") if isinstance(payload, dict) else None
    return detail.get("error", "") if isinstance(detail, dict) else ""


def rows(sql, args=()):
    """直接读库（只 SELECT）；WAL 模式下普通连接读得到未 checkpoints 的最新提交。"""
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def db_snapshot():
    return {
        "devices": {row["id"]: row["name"] for row in rows("SELECT id, name FROM devices")},
        "groups": rows("SELECT id, name, owner_device_id FROM groups"),
        "members": rows("SELECT group_id, device_id, role FROM group_members ORDER BY group_id, role"),
    }


def register(name):
    status, payload = call("POST", "/api/devices", {"name": name})
    assert status == 201, (status, payload)
    return payload


def unregister(device):
    return call("DELETE", f"/api/devices/{device['id']}", token=device["token"])


def dump(label, payload):
    print(f"     ↳ {label}: {json.dumps(payload, ensure_ascii=False)[:400]}")


def scenario_member(base_tag):
    """B) 注销普通成员：组的成员数 / 名单应同步减少。"""
    print("\n--- 场景 B：注销**组员**（B2） ---")
    admin = register(f"验证组长-{base_tag}")
    mate = register(f"验证组员-{base_tag}")
    created_devices.extend([admin, mate])

    _, made = call("POST", "/api/groups", {"device_id": admin["id"], "name": f"验证组-成员注销-{base_tag}"},
                   token=admin["token"])
    gid = (made.get("group") or {}).get("id", "")
    call("POST", f"/api/groups/{gid}/join", {"device_id": mate["id"]}, token=mate["token"])

    status, before = call("GET", f"/api/groups?device_id={admin['id']}", token=admin["token"])
    member_count_before = ((before.get("groups") or [{}])[0]).get("member_count")
    check("注销前：组存在且成员数 2", status == 200 and before.get("count") == 1 and member_count_before == 2,
          f"count={before.get('count')} member_count={member_count_before}")

    status, deleted = unregister(mate)
    dump("DELETE /api/devices/B2", deleted)
    check("注销组员 200 deleted=true", status == 200 and deleted.get("deleted") is True, f"HTTP {status}")

    status, after = call("GET", f"/api/groups?device_id={admin['id']}", token=admin["token"])
    member_count_after = ((after.get("groups") or [{}])[0]).get("member_count")
    check("注销组员后：组还在（管理员那侧 count=1）", status == 200 and after.get("count") == 1,
          f"count={after.get('count')}")
    check("成员数同步减少 2 → 1", member_count_after == 1, f"member_count={member_count_after}")

    status, detail = call("GET", f"/api/groups/{gid}?device_id={admin['id']}", token=admin["token"])
    members = detail.get("members") or []
    dump("GET /api/groups/{gid} 成员名单", {"members": [m.get("name") for m in members],
                                            "ids": [m.get("id") for m in members]})
    check("成员名单里没有已注销的设备", status == 200 and len(members) == 1
          and mate["id"] not in [m.get("id") for m in members], f"len={len(members)}")

    check("库里 groups 行还在（1 行）", len(rows("SELECT id FROM groups WHERE id = ?", (gid,))) == 1)
    left = rows("SELECT device_id FROM group_members WHERE group_id = ?", (gid,))
    check("库里 group_members 只剩管理员一行", len(left) == 1 and left[0]["device_id"] == admin["id"],
          f"rows={[r['device_id'] for r in left]}")

    return gid


def scenario_owner(base_tag):
    """A) 注销管理员：组是否随之解散。"""
    print("\n--- 场景 A：注销**管理员**（A） ---")
    owner = register(f"验证组长-{base_tag}")
    mate = register(f"验证组员-{base_tag}")
    created_devices.extend([owner, mate])

    _, made = call("POST", "/api/groups", {"device_id": owner["id"], "name": f"验证组-管理员注销-{base_tag}"},
                   token=owner["token"])
    gid = (made.get("group") or {}).get("id", "")
    call("POST", f"/api/groups/{gid}/join", {"device_id": mate["id"]}, token=mate["token"])

    status, before = call("GET", f"/api/groups?device_id={mate['id']}", token=mate["token"])
    check("注销前：组员看到 1 个组、成员数 2",
          before.get("count") == 1 and ((before.get("groups") or [{}])[0]).get("member_count") == 2,
          f"count={before.get('count')} member_count={((before.get('groups') or [{}])[0]).get('member_count')}")

    status, deleted = unregister(owner)
    dump("DELETE /api/devices/A（管理员）", deleted)
    check("注销管理员 200 deleted=true", status == 200 and deleted.get("deleted") is True, f"HTTP {status}")

    status, mine = call("GET", f"/api/groups?device_id={mate['id']}", token=mate["token"])
    dump("注销管理员后 组员 B 的 GET /api/groups", mine)
    check("组员那侧组消失了（count=0）", status == 200 and mine.get("count") == 0, f"count={mine.get('count')}")

    status, detail = call("GET", f"/api/groups/{gid}?device_id={mate['id']}", token=mate["token"])
    check("按组 id 查详情 404 group_not_found",
          status == 404 and error_of(detail) == "group_not_found", f"HTTP {status} {error_of(detail)}")

    check("库里 groups 行已被级联删除", rows("SELECT id FROM groups WHERE id = ?", (gid,)) == [])
    check("库里 group_members 行也一并删掉",
          rows("SELECT device_id FROM group_members WHERE group_id = ?", (gid,)) == [])

    status, devices = call("GET", f"/api/devices?device_id={mate['id']}", token=mate["token"])
    names = [d.get("name") for d in (devices.get("devices") or [])]
    dump("组员 B 看到的设备列表", {"count": devices.get("count"), "names": names})
    check("组员 B 看到的设备列表里没有已注销的管理员",
          owner["id"] not in [d.get("id") for d in (devices.get("devices") or [])], f"names={names}")

    status, again = unregister(owner)
    check("再次注销同一设备 → 404 device_not_found",
          status == 404 and error_of(again) == "device_not_found", f"HTTP {status} {error_of(again)}")

    return gid


created_devices = []
make_tag = uuid.uuid4().hex[:5]

print(f"服务端 {BASE}\n数据库 {DB_PATH}（WAL，只读打开）")
_, stats_before = call("GET", "/api/stats")
snap_before = db_snapshot()
print(f"跑前：stats devices={stats_before.get('devices')} groups={stats_before.get('groups')}｜"
      f"库内设备 {len(snap_before['devices'])} 台、组 {len(snap_before['groups'])} 个")

try:
    scenario_member(make_tag)
    scenario_owner(make_tag)
finally:
    print("\n--- 清理 ---")
    for device in created_devices:
        status, payload = unregister(device)
        print(f"  清理 {device['name']}（{device['id']}）：HTTP {status} {json.dumps(payload, ensure_ascii=False)[:120]}")

_, stats_after = call("GET", "/api/stats")
snap_after = db_snapshot()
check("清理后设备数回到跑前", stats_after.get("devices") == stats_before.get("devices"),
      f"before={stats_before.get('devices')} after={stats_after.get('devices')}")
check("清理后设备组数回到跑前", stats_after.get("groups") == stats_before.get("groups"),
      f"before={stats_before.get('groups')} after={stats_after.get('groups')}")
check("原有的设备 id / 名字一台没动", snap_after["devices"] == snap_before["devices"],
      f"before={sorted(snap_before['devices'].values())} after={sorted(snap_after['devices'].values())}")
check("原有的设备组一行没动", snap_after["groups"] == snap_before["groups"],
      f"before={[g['id'] for g in snap_before['groups']]} after={[g['id'] for g in snap_after['groups']]}")
leftovers = [d["name"] for d in snap_after["devices"].values() if "验证" in d]
check("没有残留的测试设备（名字带「验证」）", not leftovers, f"leftovers={leftovers}")

print(f"\n结果：{passed} 项通过" + (f"，{len(failed)} 项失败：{failed}" if failed else "，全部通过"))
raise SystemExit(1 if failed else 0)
