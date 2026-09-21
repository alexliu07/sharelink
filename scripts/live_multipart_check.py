#!/usr/bin/env python3
"""线上自检：multipart 的分段边界必须合法 —— 复现并回归 App 的字节布局。

背景（真机上暴露的 bug）：安卓 App 的 Api.field() 返回的片段以 `--boundary` 开头、
没有前导 CRLF。纯文字分享时各字段尾部的 CRLF 恰好凑成下一段的前导 CRLF，所以没问题；
但**带文件**时字段块被写在文件原始字节之后，文件字节不以 CRLF 结尾 →
`--boundary` 前面缺 CRLF，服务端按 RFC 2046 不认这个边界，把它当成文件内容，
于是 ttl_seconds 被吞掉（有效期回落到默认 1 小时）、文件尾部还多出几百字节垃圾。

本脚本对**真实服务端**发两种布局各一次，断言：
  A) 缺 CRLF 的旧布局：ttl_seconds 不被采纳（==3689 默认 3600）且落盘字节 > 原始内容
  B) 修好后的布局：ttl_seconds == 600 且落盘字节与原始内容逐字节相同
跑完删掉两个测试文件。

用法：.venv/bin/python scripts/live_multipart_check.py [base_url]
默认打本机 127.0.0.1:18000（避开 Cloudflare）。
"""
import hashlib
import json
import sys
import urllib.error
import urllib.request
import uuid

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18000"
CONTENT = "multipart 边界自检内容\n".encode() * 7          # 有换行的二进制内容
TTL = 600

passed, failed = 0, []


def check(label, ok, detail=""):
    global passed
    if ok:
        passed += 1
        print(f"  ✅ {label}{(' — ' + detail) if detail else ''}")
    else:
        failed.append(label)
        print(f"  ❌ {label}{(' — ' + detail) if detail else ''}")


def call(method, path, body=None, ctype=None):
    request = urllib.request.Request(BASE + path, data=body, method=method)
    if ctype:
        request.add_header("Content-Type", ctype)
    request.add_header("User-Agent", "ShareLink-MultipartCheck/1")
    try:
        with urllib.request.urlopen(request, timeout=60) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()


def build(buggy):
    """按 App 的两种布局拼 multipart：buggy=True 复刻现在的 Java，False 是修好后的。"""
    boundary = "----ShareLink" + uuid.uuid4().hex
    def field(name, value):
        return (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n"
                f"{value}\r\n").encode()

    head = (f"--{boundary}\r\n"
            f"Content-Disposition: form-data; name=\"file\"; filename=\"自检-边界.txt\"\r\n"
            "Content-Type: text/plain\r\n\r\n").encode()
    tail = field("ttl_seconds", str(TTL)) + field("title", "边界自检")
    if buggy:
        closing = f"--{boundary}--\r\n".encode()          # 旧：字段块直接顶在文件字节后，且收尾也缺 CRLF
    else:
        tail = b"\r\n" + tail                              # 修好后：文件字节后先补 CRLF
        closing = f"--{boundary}--\r\n".encode()           # 字段末尾自带 CRLF，收尾直接接上
    body = head + CONTENT + tail + closing
    return body, f"multipart/form-data; boundary={boundary}", boundary


print(f"服务端 {BASE}（有效期请求值 {TTL} 秒）")
created = []
for buggy, label in ((True, "A) 旧布局（--boundary 前缺 CRLF）"), (False, "B) 修好后的布局")):
    print(f"\n{label}")
    body, ctype, _ = build(buggy)
    status, raw = call("POST", "/api/upload", body, ctype)
    try:
        data = json.loads(raw.decode())
    except Exception:
        data = {}
    check("HTTP 200/201 且返回分享码", status in (200, 201) and bool(data.get("code")),
          f"HTTP {status} code={data.get('code')}")
    if data.get("code"):
        created.append(data["code"])
    ttl = data.get("ttl_seconds")
    if buggy:
        check("复现：ttl_seconds 被忽略（回落到默认 3600）", ttl != TTL, f"实际 {ttl}")
    else:
        check("修复：ttl_seconds == 600", ttl == TTL, f"实际 {ttl}")
    # 落盘字节：下载回来对比
    if data.get("code"):
        status, got = call("GET", f"/api/download/{data['code']}")
        same = got == CONTENT
        if buggy:
            check("复现：落盘内容被追加了垃圾字节", not same,
                  f"原始 {len(CONTENT)} 字节，落盘 {len(got)} 字节（多 {len(got) - len(CONTENT)}）")
            print(f"     尾部垃圾预览: {got[len(CONTENT):len(CONTENT)+60]!r}")
        else:
            check("修复：落盘内容与原始逐字节相同", same,
                  f"原始 {len(CONTENT)} 字节，落盘 {len(got)} 字节 "
                  f"sha256 {hashlib.sha256(got).hexdigest()[:12]}…")

print("\n清理")
for code in created:
    status, _ = call("DELETE", f"/api/files/{code}")
    print(f"   删除 {code} → HTTP {status}")

print(f"\n结果：{passed} 项通过" + (f"，{len(failed)} 项失败：{failed}" if failed else "，全部通过"))
raise SystemExit(1 if failed else 0)
