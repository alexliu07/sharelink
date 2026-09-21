#!/usr/bin/env python3
"""线上自检：设备组（建组/加入/退出/移除/解散）+ 投递授权收紧。

对**真实服务端**跑一遍，覆盖：
  1) 建组：id 是 ``grp_`` + 12 位（与 8 位分享码区分）、创建者即管理员、成员数 1
  2) 加入：凭组 id 加入（幂等）、成员名单只有组内可见
  3) 设备页只列「自己 + 同组设备」，并带共同组标签；不带令牌完全看不到名单
  4) 投递规则：同组 201；不同组 403 not_in_same_group；未登记/令牌不对 403 needs_device 且不落盘
  5) 退出 / 管理员移除 / 非管理员不能移除 / 管理员不能退出（要解散）/ 解散后再投递被拒
  6) 跑完清理干净（设备数、设备组数都回到跑前）

用法：.venv/bin/python scripts/live_groups_check.py [base_url]
不传参数则本机与公网各跑一遍。
"""
import json
import sys
import urllib.error
import urllib.request
import uuid

BASES = sys.argv[1:] or ["http://127.0.0.1:18000", "https://skylare.me/share"]

passed, failed = 0, []


def check(label, ok, detail=""):
    global passed
    if ok:
        passed += 1
        print(f"  ✅ {label}{(' — ' + detail) if detail else ''}")
    else:
        failed.append(label)
        print(f"  ❌ {label}{(' — ' + detail) if detail else ''}")


def call(base, method, path, payload=None, token=None, form=None, content_type=None):
    body = None
    headers = {"User-Agent": "ShareLink-GroupCheck/1"}
    if payload is not None:
        body = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    if form is not None:
        body = form
        if content_type:                 # multipart 必须带 boundary，否则服务端只能给 422
            headers["Content-Type"] = content_type
    if token:
        headers["X-Device-Token"] = token
    request = urllib.request.Request(base + path, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=90) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as err:
        raw = err.read()
        try:
            return err.code, json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return err.code, {"raw": raw[:200].decode("utf-8", "ignore")}


def _detail(payload) -> dict:
    """FastAPI 的错误体是 {"detail": {...}}，但 405/404 这类是 {"detail": "字符串"}。"""
    detail = (payload or {}).get("detail") if isinstance(payload, dict) else None
    return detail if isinstance(detail, dict) else {}


def error_of(payload) -> str:
    return _detail(payload).get("error", "")


def message_of(payload) -> str:
    return _detail(payload).get("message", "")


def register(base, name):
    status, payload = call(base, "POST", "/api/devices", {"name": name})
    assert status == 201, payload
    return payload


def multipart(fields, filename, content, content_type="text/plain"):
    boundary = "----ShareLinkGroupCheck" + uuid.uuid4().hex[:12]
    chunks = []
    for key, value in fields:
        chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
    chunks.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n".encode() + content + b"\r\n"
    )
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), {"Content-Type": f"multipart/form-data; boundary={boundary}"}


def deliver(base, sender, target_id, filename="组内文件.txt", content=b"group-payload"):
    body, headers = multipart([("targets", target_id), ("from_device_id", sender["id"]), ("ttl", "10m")],
                              filename, content)
    status, payload = call(base, "POST", "/api/transfers", token=sender["token"], form=body,
                           content_type=headers["Content-Type"])
    return status, payload


def run(base):
    print(f"\n=== {base} ===")
    made_devices, made_files = [], []
    _, before = call(base, "GET", "/api/stats")
    devices_before, groups_before = before.get("devices"), before.get("groups")

    tag = uuid.uuid4().hex[:5]
    admin = register(base, f"自检组长-{tag}")
    mate = register(base, f"自检组员-{tag}")
    stranger = register(base, f"自检组外-{tag}")
    made_devices += [admin, mate, stranger]

    # 1) 建组
    status, payload = call(base, "POST", "/api/groups", {"device_id": admin["id"], "name": f"自检组-{tag}"},
                           token=admin["token"])
    group = payload.get("group") or {}
    group_id = group.get("id", "")
    check("建组 201 且返回组 id", status in (200, 201) and bool(group_id), f"HTTP {status} id={group_id}")
    check("组 id 是 grp_ + 12 位（与 8 位分享码区分）",
          group_id.startswith("grp_") and len(group_id) == len("grp_") + 12, group_id)
    check("创建者即管理员、成员数 1",
          group.get("is_owner") is True and group.get("my_role") == "owner" and group.get("member_count") == 1,
          f"role={group.get('my_role')} members={group.get('member_count')}")
    check("组名保留", group.get("name") == f"自检组-{tag}", str(group.get("name")))

    # 2) 凭组 id 加入
    status, joined = call(base, "POST", f"/api/groups/{group_id}/join", {"device_id": mate["id"]},
                          token=mate["token"])
    check("组员凭组 id 加入 → 201", status in (200, 201) and (joined.get("joined") is True or joined.get("already_member")),
          f"HTTP {status} joined={joined.get('joined')}")
    status, again = call(base, "POST", f"/api/groups/{group_id}/join", {"device_id": mate["id"]},
                         token=mate["token"])
    check("重复加入幂等（already_member，不重复计数）",
          status == 200 and again.get("already_member") is True
          and (again.get("group") or {}).get("member_count") == 2, f"HTTP {status} {(again.get('group') or {}).get('member_count')}")
    status, pasted = call(base, "POST", f"/api/groups/{group_id.lower()}/join", {"device_id": stranger["id"]},
                          token=stranger["token"])
    check("组 id 大小写不敏感也能加入（粘贴友好）", status in (200, 201), f"HTTP {status}")
    status, bad = call(base, "POST", "/api/groups/grp_WRONGWRONG99/join", {"device_id": stranger["id"]},
                       token=stranger["token"])
    check("乱填组 id → 404 group_not_found（不猜不误入）",
          status == 404 and error_of(bad) == "group_not_found", f"HTTP {status} {error_of(bad)}")
    status, left = call(base, "POST", f"/api/groups/{group_id}/leave", {"device_id": stranger["id"]},
                        token=stranger["token"])
    check("组外设备退出自己刚加入的组 200", status == 200, f"HTTP {status}")

    # 3) 成员名单 + 设备页可见范围
    status, detail = call(base, "GET", f"/api/groups/{group_id}?device_id={admin['id']}", token=admin["token"])
    members = detail.get("members") or []
    check("成员名单：管理员在最前，其余按加入顺序",
          [m["name"] for m in members] == [admin["name"], mate["name"]],
          str([f"{m['name']}/{m['role']}" for m in members]))
    check("成员带 role / is_self / 最后活跃",
          all(m.get("role") and "is_self" in m and isinstance(m.get("idle_seconds"), int) for m in members))
    status, forbidden = call(base, "GET", f"/api/groups/{group_id}?device_id={stranger['id']}", token=stranger["token"])
    check("非成员看不到成员名单 403 not_a_member",
          status == 403 and error_of(forbidden) == "not_a_member", f"HTTP {status} {error_of(forbidden)}")

    status, listing = call(base, "GET", f"/api/devices?device_id={admin['id']}", token=admin["token"])
    names = [d["name"] for d in listing.get("devices", [])]
    check("设备页只列「自己 + 同组设备」", names == [admin["name"], mate["name"]], str(names))
    peers = [d for d in listing.get("devices", []) if not d.get("is_self")]
    check("同组设备带共同组标签",
          bool(peers) and [g["id"] for g in peers[0]["shared_groups"]] == [group_id],
          str(peers[0]["shared_groups"] if peers else None))
    status, anon = call(base, "GET", "/api/devices")
    check("不带令牌看不到任何设备名单", anon.get("count") == 0 and anon.get("devices") == [],
          f"count={anon.get('count')} scope={anon.get('scope')}")
    status, solo = call(base, "GET", f"/api/devices?device_id={stranger['id']}", token=stranger["token"])
    check("组外设备只看到自己一台", [d["name"] for d in solo.get("devices", [])] == [stranger["name"]],
          str([d["name"] for d in solo.get("devices", [])]))

    # 4) 投递授权
    status, sent = deliver(base, admin, mate["id"])
    check("同组投递 201", status == 201, f"HTTP {status} code={sent.get('code')}")
    if sent.get("code"):
        made_files.append(sent["code"])
    status, inbox = call(base, "GET", f"/api/devices/{mate['id']}/inbox", token=mate["token"])
    check("组员收件箱收到这条",
          any(i["code"] == sent.get("code") for i in inbox.get("items", [])), f"count={inbox.get('count')}")

    status, refused = deliver(base, admin, stranger["id"])
    check("不同组投递 403 not_in_same_group（说清是谁）",
          status == 403 and error_of(refused) == "not_in_same_group" and stranger["name"] in message_of(refused),
          f"HTTP {status} {message_of(refused)}")
    body, headers = multipart([("targets", mate["id"])], "匿名.txt", b"nope")
    status, anonymous = call(base, "POST", "/api/transfers", form=body, content_type=headers["Content-Type"])
    check("未登记设备投递 403 needs_device", status == 403 and error_of(anonymous) == "needs_device",
          f"HTTP {status} {error_of(anonymous)}")
    body, headers = multipart([("targets", mate["id"]), ("from_device_id", admin["id"])], "错令牌.txt", b"nope")
    status, forged = call(base, "POST", "/api/transfers", token="forged-token", form=body,
                          content_type=headers["Content-Type"])
    check("令牌不对 = 不是这台设备（403，不冒充）",
          status == 403 and error_of(forged) == "needs_device", f"HTTP {status} {error_of(forged)}")
    status, text_bad = call(base, "POST", "/api/texts",
                            {"text": "跨组文本", "targets": [stranger["id"]], "from_device_id": admin["id"]},
                            token=admin["token"])
    check("发文本投递同样要求同组（403）",
          status == 403 and error_of(text_bad) == "not_in_same_group", f"HTTP {status} {error_of(text_bad)}")
    status, text_ok = call(base, "POST", "/api/texts",
                           {"text": "组内文本", "targets": [mate["id"]], "from_device_id": admin["id"]},
                           token=admin["token"])
    check("组内发文本 201", status == 201, f"HTTP {status} code={text_ok.get('code')}")
    if text_ok.get("code"):
        made_files.append(text_ok["code"])

    # 5) 退出 / 移除 / 权限边界 / 解散
    status, _ = call(base, "POST", f"/api/groups/{group_id}/leave", {"device_id": mate["id"]}, token=mate["token"])
    check("组员自己退出 200", status == 200, f"HTTP {status}")
    status, after_leave = deliver(base, admin, mate["id"])
    check("退出后不能再互传（403）", status == 403 and error_of(after_leave) == "not_in_same_group", f"HTTP {status}")
    status, too_late = call(base, "POST", f"/api/groups/{group_id}/leave", {"device_id": mate["id"]},
                            token=mate["token"])
    check("不在组里再退出 → 404 not_a_member", status == 404 and error_of(too_late) == "not_a_member",
          f"HTTP {status} {error_of(too_late)}")

    call(base, "POST", f"/api/groups/{group_id}/join", {"device_id": mate["id"]}, token=mate["token"])
    status, removed = call(base, "DELETE", f"/api/groups/{group_id}/members/{mate['id']}?device_id={admin['id']}",
                           token=admin["token"])
    check("管理员移除成员 200", status == 200 and removed.get("removed") is True,
          f"HTTP {status} name={removed.get('name')}")
    status, mine = call(base, "GET", f"/api/groups?device_id={mate['id']}", token=mate["token"])
    check("被移除后他那边没有这个组了", mine.get("count") == 0, f"count={mine.get('count')}")

    call(base, "POST", f"/api/groups/{group_id}/join", {"device_id": mate["id"]}, token=mate["token"])
    status, not_owner = call(base, "DELETE", f"/api/groups/{group_id}/members/{stranger['id']}?device_id={mate['id']}",
                             token=mate["token"])
    check("非管理员不能移除成员 403 not_owner",
          status == 403 and error_of(not_owner) == "not_owner", f"HTTP {status} {error_of(not_owner)}")
    status, self_remove = call(base, "DELETE", f"/api/groups/{group_id}/members/{admin['id']}?device_id={admin['id']}",
                               token=admin["token"])
    check("管理员不能被移除（要结束就解散）",
          status == 409 and error_of(self_remove) == "owner_cannot_be_removed", f"HTTP {status} {error_of(self_remove)}")
    status, owner_leave = call(base, "POST", f"/api/groups/{group_id}/leave", {"device_id": admin["id"]},
                               token=admin["token"])
    check("管理员不能用「退出」（409 owner_must_dissolve）",
          status == 409 and error_of(owner_leave) == "owner_must_dissolve", f"HTTP {status} {error_of(owner_leave)}")
    status, renamed = call(base, "PATCH", f"/api/groups/{group_id}",
                           {"device_id": admin["id"], "name": f"改名-{tag}"}, token=admin["token"])
    check("管理员改组名 200", status == 200 and (renamed.get("group") or {}).get("name") == f"改名-{tag}",
          f"HTTP {status} {(renamed.get('group') or {}).get('name')}")

    status, dissolved = call(base, "DELETE", f"/api/groups/{group_id}?device_id={admin['id']}", token=admin["token"])
    check("管理员解散组 200", status == 200 and dissolved.get("dissolved") is True, f"HTTP {status}")
    status, gone = call(base, "GET", f"/api/groups/{group_id}?device_id={admin['id']}", token=admin["token"])
    check("解散后组不存在 404", status == 404 and error_of(gone) == "group_not_found", f"HTTP {status}")
    status, after_dissolve = deliver(base, admin, mate["id"])
    check("解散后不能再互传（403）", status == 403 and error_of(after_dissolve) == "not_in_same_group", f"HTTP {status}")
    status, inbox_still = call(base, "GET", f"/api/devices/{mate['id']}/inbox", token=mate["token"])
    check("解散不影响已经收到的文件", inbox_still.get("count", 0) >= 1, f"count={inbox_still.get('count')}")

    # 6) 清理
    for code in dict.fromkeys(made_files):
        call(base, "DELETE", f"/api/files/{code}")
    for device in made_devices:
        call(base, "DELETE", f"/api/devices/{device['id']}", token=device["token"])
    _, after = call(base, "GET", "/api/stats")
    check("跑完设备数回到跑前", after.get("devices") == devices_before,
          f"before={devices_before} after={after.get('devices')}")
    check("跑完设备组数回到跑前", after.get("groups") == groups_before,
          f"before={groups_before} after={after.get('groups')}")


for base in BASES:
    run(base)

print(f"\n结果：{passed} 项通过" + (f"，{len(failed)} 项失败：{failed}" if failed else "，全部通过"))
raise SystemExit(1 if failed else 0)
