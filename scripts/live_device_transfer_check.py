#!/usr/bin/env python3
"""把安卓 App 会发的请求逐字节复刻，打真实服务端跑完整链路（设备互传）。

用法：python3 scripts/live_device_transfer_check.py
会自己登记两台临时设备，走投递 → 收件箱 → 下载 → 标记已读 → 移出 → 改名，最后全部清理干净。

App 侧字段/头完全按 Api.java + MainActivity.java 的写法：multipart 里文件 part 固定叫 file，
其余字段跟在文件后面（title/text 或 targets/from_device_id/note），鉴权用 X-Device-Token。
"""
import json
import urllib.request
import urllib.error
import uuid

BASE = "https://skylare.me/share"
ok = 0
fail = 0


def check(label, condition, extra=""):
    global ok, fail
    if condition:
        ok += 1
        print(f"  ✅ {label} {extra}")
    else:
        fail += 1
        print(f"  ❌ {label} {extra}")


def call(method, path, token=None, body=None, headers=None, raw=False):
    url = path if path.startswith("http") else BASE + path
    req = urllib.request.Request(url, method=method, data=body)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "ShareLink-Android/1.2")
    if token:
        req.add_header("X-Device-Token", token)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
            return resp.status, (data if raw else json.loads(data.decode() or "{}"))
    except urllib.error.HTTPError as exc:
        data = exc.read()
        try:
            return exc.code, json.loads(data.decode())
        except Exception:
            return exc.code, {"raw": data[:200].decode("utf-8", "replace")}


def multipart(fields, file_part=None, filename=None, content_type="application/octet-stream", payload=b""):
    """完全复刻 Api.uploadMultipart 的字节布局。"""
    boundary = "----ShareLink" + uuid.uuid4().hex[:12]
    out = b""
    if file_part:
        out += f"--{boundary}\r\n".encode()
        out += f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode("utf-8")
        out += f"Content-Type: {content_type}\r\n\r\n".encode()
        out += payload
        out += b"\r\n"
        for name, value in fields:
            if value:
                out += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode("utf-8")
        out += f"\r\n--{boundary}--\r\n".encode()
    else:
        for name, value in fields:
            if value:
                out += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode("utf-8")
        out += f"--{boundary}--\r\n".encode()
    return out, {"Content-Type": f"multipart/form-data; boundary={boundary}"}


print("== 1. 登记两台测试设备（App 的 POST /api/devices） ==")
_, before_devices = call("GET", "/api/devices")
before_count = before_devices.get("count")
print(f"  跑之前的设备数：{before_count}（跑完必须回到这个数）")
sender_name = f"测试发送机{uuid.uuid4().hex[:4]}"
target_name = f"测试接收机{uuid.uuid4().hex[:4]}"
status, sender = call("POST", "/api/devices", body=json.dumps({"name": sender_name}).encode(),
                      headers={"Content-Type": "application/json"})
check("发送机登记 201", status == 201, f"id={sender.get('id')} token长度={len(sender.get('token', ''))}")
status, target = call("POST", "/api/devices", body=json.dumps({"name": target_name}).encode(),
                      headers={"Content-Type": "application/json"})
check("接收机登记 201", status == 201, f"id={target.get('id')}")
check("返回体含 id/name/token", all(sender.get(k) for k in ("id", "name", "token")))
check("公开字段（不含令牌哈希）", "token_hash" not in sender and "idle_seconds" in sender)

print("== 2. 投递文件（App 的 POST /api/transfers：file + targets + from_device_id + note） ==")
content = "设备互传联调测试内容\n".encode("utf-8") * 40
body, headers = multipart(
    [("targets", target["id"]), ("from_device_id", sender["id"]), ("note", "来自安卓App的附言")],
    file_part=True, filename="投递测试 报告.txt", content_type="text/plain", payload=content,
)
status, transfer = call("POST", "/api/transfers", token=sender["token"], body=body, headers=headers)
check("投递 201", status == 201, f"code={transfer.get('code')} targets={[t['name'] for t in transfer.get('targets', [])]}")
check("投递回执带分享码与链接", bool(transfer.get("code")) and bool(transfer.get("share_url")))
check("发送者被识别为实名设备", transfer.get("from_name") == sender_name, f"from_name={transfer.get('from_name')}")
check("附言被记录", transfer.get("note") == "来自安卓App的附言")
check("文件名保持中文与空格", transfer.get("filename") == "投递测试 报告.txt", f"filename={transfer.get('filename')}")
code = transfer.get("code")

print("== 3. 接收机读收件箱（App 的 GET /api/devices/{id}/inbox + X-Device-Token） ==")
status, inbox = call("GET", f"/api/devices/{target['id']}/inbox", token=target["token"])
check("收件箱 200", status == 200)
item = next((i for i in inbox.get("items", []) if i["code"] == code), None)
check("收件箱里有这条投递", item is not None, f"count={inbox.get('count')} unread={inbox.get('unread')}")
if item:
    check("带 from_name（谁发的）", item.get("from_name") == sender_name)
    check("带 sent_at / seen / note", bool(item.get("sent_at")) and item.get("seen") is False and item.get("note") == "来自安卓App的附言")
    check("带 download_url", bool(item.get("download_url")), item.get("download_url"))
    check("带剩余有效期字段", isinstance(item.get("seconds_left"), int) and item.get("seconds_left") > 0,
          f"seconds_left={item.get('seconds_left')}")
    download_url = item["download_url"]

print("== 4. 下载投递过来的文件（App 点一下 → 下载并打开） ==")
status, data = call("GET", download_url, raw=True)
check("下载 200", status == 200, f"{len(data)} 字节")
check("字节与上传一致", data == content)

print("== 5. 标记已读 / 移出收件箱 ==")
status, res = call("POST", f"/api/devices/{target['id']}/inbox/seen", token=target["token"], body=b"{}",
                   headers={"Content-Type": "application/json"})
check("标记已读 200", status == 200, f"marked={res.get('marked')}")
status, inbox2 = call("GET", f"/api/devices/{target['id']}/inbox", token=target["token"])
check("未读数归零", inbox2.get("unread") == 0, f"unread={inbox2.get('unread')}")
status, res = call("DELETE", f"/api/devices/{target['id']}/inbox/{code}", token=target["token"])
check("移出收件箱 200", status == 200, f"removed={res.get('removed')}")
status, inbox3 = call("GET", f"/api/devices/{target['id']}/inbox", token=target["token"])
check("收件箱里已无这条", all(i["code"] != code for i in inbox3.get("items", [])))

print("== 6. 令牌校验（错的令牌必须被拒） ==")
status, _ = call("GET", f"/api/devices/{target['id']}/inbox", token="wrong-token-123")
check("错令牌 401/403", status in (401, 403), f"HTTP {status}")

print("== 7. 改名（App 的 PATCH，靠反射发 PATCH） ==")
new_name = f"改名为{uuid.uuid4().hex[:4]}"
status, res = call("PATCH", f"/api/devices/{sender['id']}", token=sender["token"],
                   body=json.dumps({"name": new_name}).encode(), headers={"Content-Type": "application/json"})
check("改名 200", status == 200, f"name={res.get('name')}")
status, devices = call("GET", "/api/devices")
check("设备列表里名字已更新", any(d["id"] == sender["id"] and d["name"] == new_name for d in devices.get("devices", [])))

print("== 8. 清理（注销测试设备 + 删测试文件） ==")
status, res = call("DELETE", f"/api/devices/{target['id']}", token=target["token"])
check("接收机注销 200", status == 200)
status, res = call("DELETE", f"/api/devices/{sender['id']}", token=sender["token"])
check("发送机注销 200", status == 200)
status, res = call("DELETE", f"/api/files/{code}")
check("测试文件已删除", status in (200, 204), f"HTTP {status}")
status, stats = call("GET", "/api/stats")
check("设备数回到跑之前", stats.get("devices") == before_count,
      f"before={before_count} after={stats.get('devices')} transfers={stats.get('transfers')}")
check("没有留下测试设备", not any(d["name"].startswith("测试") for d in call("GET", "/api/devices")[1].get("devices", [])))

print(f"\n结果：{ok} 项通过，{fail} 项失败")
raise SystemExit(1 if fail else 0)
