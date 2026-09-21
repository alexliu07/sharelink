#!/usr/bin/env python3
"""线上自检：安卓 App 上传时带的 ttl_seconds 真的生效（两条路径都测）。

App 的两条上传路径：
  1) 分享面板 / 「选择文件发给设备」且不勾设备 → POST /api/share-target?response=json
  2) 勾了目标设备                        → POST /api/transfers
这里逐字节复刻 App 的 multipart 请求，打到线上服务，断言返回的 ttl_seconds / ttl_human
与请求一致，然后删掉测试文件与临时设备（跑完不留垃圾）。

用法：.venv/bin/python scripts/live_ttl_check.py
"""
import json
import mimetypes
import os
import urllib.error
import urllib.request
import uuid

BASE = os.environ.get("SHARELINK_BASE", "https://skylare.me/share")
UA = "ShareLink-Android/1.4"

passed = 0
failed = []


def check(label, ok, detail=""):
    global passed
    if ok:
        passed += 1
        print(f"  ✅ {label}{(' — ' + detail) if detail else ''}")
    else:
        failed.append(label)
        print(f"  ❌ {label}{(' — ' + detail) if detail else ''}")


def multipart(fields, filename="ttl-check.txt", content=b"ShareLink TTL self-check\n"):
    """构造和 App 一样的 multipart/form-data 请求体。"""
    boundary = "----ShareLinkBoundary" + uuid.uuid4().hex
    out = bytearray()
    for name, value in fields:
        out += f"--{boundary}\r\n".encode()
        out += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        out += str(value).encode() + b"\r\n"
    if filename is not None:
        ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        out += f"--{boundary}\r\n".encode()
        out += f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode()
        out += f"Content-Type: {ctype}\r\n\r\n".encode()
        out += content + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def call(method, path, body=None, ctype=None, token=None):
    request = urllib.request.Request(BASE + path, data=body, method=method)
    if ctype:
        request.add_header("Content-Type", ctype)
    request.add_header("User-Agent", UA)
    if token:
        request.add_header("X-Device-Token", token)
    try:
        with urllib.request.urlopen(request, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as err:
        raw = err.read().decode()
        try:
            return err.code, json.loads(raw)
        except json.JSONDecodeError:
            return err.code, {"raw": raw}


def cleanup_codes(codes):
    for code in codes:
        try:
            call("DELETE", f"/api/files/{code}")
        except Exception as err:            # noqa: BLE001 - 清理失败也要继续
            print(f"     (清理 {code} 失败：{err})")


print(f"服务端 {BASE}")
created = []

# ---- 1) 分享面板路径：ttl_seconds=600 应变成「10 分钟」 ----
print("\n[1/3] /api/share-target?response=json 带 ttl_seconds=600")
body, ctype = multipart([("title", "ttl 自检"), ("ttl_seconds", "600")])
status, data = call("POST", "/api/share-target?response=json", body, ctype)
check("HTTP 200 且返回分享码", status in (200, 201) and bool(data.get("code")), f"HTTP {status} code={data.get('code')}")
check("ttl_seconds == 600", data.get("ttl_seconds") == 600, f"实际 {data.get('ttl_seconds')}")
check("ttl_human 说人话", "10" in str(data.get("ttl_human", "")), f"实际 {data.get('ttl_human')!r}")
if data.get("code"):
    created.append(data["code"])
    # 再查一次元数据：剩余时间应该≈600 秒（这才是"有效期真的生效了"的硬证据）
    status, info = call("GET", f"/api/files/{data['code']}")
    left = info.get("seconds_left")
    check("文件元数据剩余时间 ≈ 600 秒", isinstance(left, int) and 560 <= left <= 600, f"实际 {left}")

# ---- 2) 投递给设备路径：先登记一台临时设备，再 ttl_seconds=2592000（30 天） ----
print("\n[2/3] /api/transfers 投递给临时设备，带 ttl_seconds=2592000")
status, device = call("POST", "/api/devices", json.dumps({"name": "ttl 自检设备"}).encode(), "application/json")
check("临时设备登记成功", status in (200, 201) and bool(device.get("id")), f"HTTP {status} id={device.get('id')}")
device_id, device_token = device.get("id"), device.get("token")
if device_id and device_token:
    body, ctype = multipart([("targets", device_id), ("from_device_id", device_id),
                             ("note", "ttl 自检"), ("ttl_seconds", "2592000")])
    status, data = call("POST", "/api/transfers", body, ctype, token=device_token)
    check("投递成功且拿到分享码", status in (200, 201) and bool(data.get("code")), f"HTTP {status} code={data.get('code')}")
    check("ttl_seconds == 2592000", data.get("ttl_seconds") == 2592000, f"实际 {data.get('ttl_seconds')}")
    check("ttl_human 说人话", "30" in str(data.get("ttl_human", "")), f"实际 {data.get('ttl_human')!r}")
    if data.get("code"):
        created.append(data["code"])
    # 收件箱里应该能看到，且带剩余时间
    status, inbox = call("GET", f"/api/devices/{device_id}/inbox", token=device_token)
    items = inbox.get("files") or inbox.get("items") or []
    check("临时设备收件箱收到 1 件", status == 200 and len(items) == 1, f"HTTP {status} 数量 {len(items)}")

# ---- 3) 网页版同款路径 /api/upload：ttl_seconds=604800（7 天） ----
print("\n[3/3] /api/upload 带 ttl_seconds=604800")
body, ctype = multipart([("ttl_seconds", "604800")])
status, data = call("POST", "/api/upload", body, ctype)
check("HTTP 200/201 且返回分享码", status in (200, 201) and bool(data.get("code")), f"HTTP {status} code={data.get('code')}")
check("ttl_seconds == 604800", data.get("ttl_seconds") == 604800, f"实际 {data.get('ttl_seconds')}")
if data.get("code"):
    created.append(data["code"])

# ---- 清理：删测试文件 + 注销临时设备 ----
print("\n清理")
cleanup_codes(created)
if device_id:
    status, _ = call("DELETE", f"/api/devices/{device_id}", token=device_token)
    check("临时设备已注销", status in (200, 204), f"HTTP {status}")
status, stats = call("GET", "/api/stats")
print(f"  清理后 /api/stats: devices={stats.get('devices')} files={stats.get('files')}")

print(f"\n结果：{passed} 项通过" + (f"，{len(failed)} 项失败：{failed}" if failed else "，全部通过"))
raise SystemExit(1 if failed else 0)
