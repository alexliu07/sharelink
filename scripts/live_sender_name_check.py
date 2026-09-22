"""真接口验证：投递时 from_name 一律被设备名覆盖（服务端行为），验证完清理掉测试设备/组。"""
import json
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:18000"
UA = "ShareLink-SenderNameCheck/1"


def call(method, path, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("User-Agent", UA)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


ok = fail = 0
made_devices, made_groups, made_tokens = [], [], []


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ✅ {name}")
    else:
        fail += 1
        print(f"  ❌ {name} {extra}")


try:
    st, a = call("POST", "/api/devices", {"name": "验证甲设备"})
    check("登记甲设备", st in (200, 201) and a.get("id", "").startswith("dev_"), f"{st} {a}")
    st, b = call("POST", "/api/devices", {"name": "验证乙设备"})
    check("登记乙设备", st in (200, 201) and b.get("id", "").startswith("dev_"), f"{st} {b}")
    made_devices = [a["id"], b["id"]]
    made_tokens = [(a["id"], a["token"]), (b["id"], b["token"])]

    st, gres = call("POST", "/api/groups", {"device_id": a["id"], "name": "验证组"}, {"X-Device-Token": a["token"]})
    g = gres.get("group", gres)
    check("甲建组", st in (200, 201) and g.get("id", "").startswith("grp_"), f"{st} {gres}")
    gid = g["id"]
    made_groups = [gid]
    st, _ = call("POST", f"/api/groups/{gid}/join", {"device_id": b["id"]}, {"X-Device-Token": b["token"]})
    check("乙加入该组", st in (200, 201), st)

    # 发文本：故意带一个假的 from_name
    st, r = call("POST", "/api/texts", {
        "text": "发送者名字验证", "ttl_seconds": 600, "targets": [b["id"]],
        "from_device_id": a["id"], "from_name": "伪造的名字",
    }, {"X-Device-Token": a["token"]})
    check("投递文本 201", st == 201, f"{st} {r}")
    check("响应里的发送者名 = 设备名（不是伪造的）", r.get("from_name") == "验证甲设备", r.get("from_name"))

    st, inbox = call("GET", f"/api/devices/{b['id']}/inbox?device_id={b['id']}", None, {"X-Device-Token": b["token"]})
    items = (inbox or {}).get("items") or (inbox or {}).get("transfers") or []
    check("收件箱读到 1 条", st == 200 and len(items) >= 1, f"{st} {inbox}")
    if items:
        name = items[0].get("from_name")
        check("收件箱里显示的发送者 = 设备名", name == "验证甲设备", name)
        check("收件箱里没有伪造名字", "伪造" not in json.dumps(items, ensure_ascii=False))

    # 未登记（不带令牌）走老路径：服务端应拒绝
    st, r2 = call("POST", "/api/texts", {"text": "x", "ttl_seconds": 600, "targets": [b["id"]], "from_name": "匿名"})
    check("不带令牌的投递被拒 403", st == 403, f"{st} {r2}")
finally:
    for gid in made_groups:
        try:
            c_st, _ = call("DELETE", f"/api/groups/{gid}?device_id={made_tokens[0][0]}", None, {"X-Device-Token": made_tokens[0][1]})
            print(f"  🧹 解散测试组 {gid}: {c_st}")
        except Exception as exc:      # noqa: BLE001
            print(f"  ⚠️ 解散 {gid} 失败：{exc}")
    for did, tok in made_tokens:
        try:
            st, d = call("DELETE", f"/api/devices/{did}", None, {"X-Device-Token": tok})
            print(f"  🧹 注销测试设备 {did}: {st} {d.get('deleted', '')}")
        except Exception as exc:      # noqa: BLE001
            print(f"  ⚠️ 注销 {did} 失败：{exc}")
    st, g = call("GET", f"/api/groups/{gid}?device_id={made_tokens[0][0]}", None, {"X-Device-Token": made_tokens[0][1]}) if made_groups else (0, {})
    check("测试组已解散（404/410）", st in (404, 410), f"{st} {g}")

print(f"\n结果：{ok} 项通过，{fail} 项失败")
