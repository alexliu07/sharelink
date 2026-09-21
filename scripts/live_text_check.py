#!/usr/bin/env python3
"""线上自检：发文本（POST /api/texts）—— 分享码取文本 + 投递到设备收件箱。

对**真实服务端**跑一遍，覆盖：
  1) 纯文本 → 分享码：文件名取首行、chars/size 对得上、is_text 标记、有效期按请求值
  2) 凭码取文本：/api/files/{code} 与 /api/download/{code} 返回的字节与原文逐字节一致
  3) 空文本 → 400 empty_text；超长（>MAX_TEXT_CHARS）→ 413 too_long（带中文原因）
  4) 文本投递：勾一台临时设备 → 收件箱里出现该文本、is_text、附言、发送者实名
  5) 跑完清理干净（删文件、注销临时设备、设备数回到跑前）

用法：.venv/bin/python scripts/live_text_check.py [base_url]
不传参数则本机与公网各跑一遍。
"""
import json
import sys
import urllib.error
import urllib.request
import uuid

BASES = sys.argv[1:] or ["http://127.0.0.1:18000", "https://skylare.me/share"]
TEXT = "文本自检：第一行当标题\n第二行：中文 与 ASCII 混排 ✔\n第三行：制表\t符"
NOTE = "自检附言"

passed, failed = 0, []


def check(label, ok, detail=""):
    global passed
    if ok:
        passed += 1
        print(f"  ✅ {label}{(' — ' + detail) if detail else ''}")
    else:
        failed.append(label)
        print(f"  ❌ {label}{(' — ' + detail) if detail else ''}")


def call(base, method, path, payload=None, token=None):
    body = None
    headers = {"User-Agent": "ShareLink-TextCheck/1"}
    if payload is not None:
        body = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["X-Device-Token"] = token
    request = urllib.request.Request(base + path, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=90) as resp:
            return resp.status, resp.read(), resp.headers        # HTTPMessage：取头大小写不敏感
    except urllib.error.HTTPError as err:
        return err.code, err.read(), err.headers


def run(base):
    print(f"\n=== {base} ===")
    created_codes, temp_device = [], None
    status, raw, _ = call(base, "GET", "/api/devices")
    devices_before = json.loads(raw)["count"] if status == 200 else None

    # 1) 纯文本 → 分享码
    status, raw, _ = call(base, "POST", "/api/texts", {"text": TEXT, "ttl_seconds": 600})
    payload = json.loads(raw) if raw else {}
    check("发文本 → 201 且拿到分享码", status == 201 and bool(payload.get("code")), f"HTTP {status} code={payload.get('code')}")
    code = payload.get("code")
    if code:
        created_codes.append(code)
    check("文件名取首行", payload.get("filename") == "文本自检：第一行当标题.txt", str(payload.get("filename")))
    check("chars/size 与原文一致",
          payload.get("chars") == len(TEXT) and payload.get("size") == len(TEXT.encode()),
          f"chars={payload.get('chars')} size={payload.get('size')} 期望 {len(TEXT)}/{len(TEXT.encode())}")
    check("标记 is_text", payload.get("is_text") is True)
    check("有效期按请求值（600 秒）", payload.get("ttl_seconds") == 600, f"实际 {payload.get('ttl_seconds')}")
    check("分享链接带分享码", code and code in (payload.get("share_url") or ""))

    # 2) 凭码取文本
    if code:
        status, raw, _ = call(base, "GET", f"/api/files/{code}")
        info = json.loads(raw)
        check("查信息也标 is_text", status == 200 and info.get("is_text") is True, f"HTTP {status}")
        status, raw, headers = call(base, "GET", f"/api/download/{code}")
        check("取回的文本与原文逐字节一致", status == 200 and raw == TEXT.encode(),
              f"HTTP {status} {len(raw)} 字节" if status != 200 else f"{len(raw)} 字节")
        ctype = (headers.get("Content-Type") or "").lower()   # 走 CF 时头的大小写会变
        check("Content-Type 是 text/plain", ctype.startswith("text/plain"), ctype)

    # 3) 非法输入
    status, raw, _ = call(base, "POST", "/api/texts", {"text": "   \n  "})
    detail = (json.loads(raw) or {}).get("detail", {}) if raw else {}
    check("空文本 → 400 empty_text", status == 400 and detail.get("error") == "empty_text",
          f"HTTP {status} {detail.get('error')}")
    status, raw, _ = call(base, "POST", "/api/texts", {"text": "字" * 10001})
    detail = (json.loads(raw) or {}).get("detail", {}) if raw else {}
    check("超长（10001 字符）→ 413 too_long 且给出上限",
          status == 413 and detail.get("error") == "too_long" and "10000" in (detail.get("message") or ""),
          f"HTTP {status} {detail.get('message')}")

    # 4) 投递到设备
    name = f"自检-文本接收-{uuid.uuid4().hex[:6]}"
    status, raw, _ = call(base, "POST", "/api/devices", {"name": name})
    temp_device = json.loads(raw) if status == 201 else None
    check("临时设备登记成功", status == 201 and temp_device, f"HTTP {status}")
    if temp_device:
        status, raw, _ = call(base, "POST", "/api/texts",
                              {"text": "投递给设备的文本\n第二行", "ttl_seconds": 600,
                               "targets": [temp_device["id"]], "from_device_id": temp_device["id"],
                               "note": NOTE},
                              token=temp_device["token"])
        payload = json.loads(raw) if raw else {}
        if payload.get("code"):
            created_codes.append(payload["code"])
        check("投递文本 → 201 且 transfer_count=1",
              status == 201 and payload.get("transfer_count") == 1,
              f"HTTP {status} transfer_count={payload.get('transfer_count')}")
        check("发送者实名（用令牌认出来的设备名）", payload.get("from_name") == name, str(payload.get("from_name")))

        status, raw, _ = call(base, "GET", f"/api/devices/{temp_device['id']}/inbox", token=temp_device["token"])
        inbox = json.loads(raw) if status == 200 else {}
        items = inbox.get("items", [])
        check("收件箱里出现了这条文本", status == 200 and len(items) == 1, f"HTTP {status} count={inbox.get('count')}")
        if items:
            item = items[0]
            check("收件箱条目标 is_text", item.get("is_text") is True)
            check("附言与发送者一起送达", item.get("note") == NOTE and item.get("from_name") == name,
                  f"note={item.get('note')} from={item.get('from_name')}")
            status, raw, _ = call(base, "GET", f"/api/download/{item['code']}")
            check("收件方按码取回的文本一致", raw == "投递给设备的文本\n第二行".encode(), f"{len(raw)} 字节")

    # 5) 清理
    for code_to_drop in created_codes:
        call(base, "DELETE", f"/api/files/{code_to_drop}")
    if temp_device:
        call(base, "DELETE", f"/api/devices/{temp_device['id']}", token=temp_device["token"])
    status, raw, _ = call(base, "GET", "/api/devices")
    devices_after = json.loads(raw)["count"] if status == 200 else None
    check("跑完设备数回到跑前（没留垃圾设备）",
          devices_before == devices_after, f"before={devices_before} after={devices_after}")


for base in BASES:
    run(base)

print(f"\n结果：{passed} 项通过" + (f"，{len(failed)} 项失败：{failed}" if failed else "，全部通过"))
raise SystemExit(1 if failed else 0)
