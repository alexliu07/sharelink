#!/usr/bin/env python3
"""线上自检：安卓 App「凭分享码下载」这条链路的服务端行为。

App 点的两下（都在工作线程里，不需要设备令牌）：
  1) GET /api/files/{code}     → 先看信息（文件名 / 大小 / 剩余时间 / expired）
  2) GET /api/download/{code}  → 下载，文件名与 MIME 从响应头里取

这里逐字节复刻这两条请求打到线上服务，并用**与 Api.filenameOf 同构的解析逻辑**去解真实的
Content-Disposition（Starlette 对中文名只发 RFC 5987 的 `filename*=utf-8''…`，ASCII 名发
`filename="…"`）—— 能真验出「中文名会不会被解成乱码」这类问题，不用真机。

另外覆盖 App 会遇到的失败分支：格式不对（本地就该拦）、不存在（404）、过期（410，线上造不出
过期文件，只验状态码/文案约定由 pytest 覆盖）。

用法：.venv/bin/python scripts/live_code_download_check.py
      SHARELINK_BASE=http://127.0.0.1/share .venv/bin/python scripts/live_code_download_check.py
"""
import json
import mimetypes
import os
import urllib.error
import urllib.request
import uuid
from urllib.parse import unquote

BASE = os.environ.get("SHARELINK_BASE", "https://skylare.me/share")
UA = "ShareLink-Android/1.7"

# 与服务端一致的分享码字符集（app/codes.py 与 MainActivity.CODE_ALPHABET）
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

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


# ---------------------------------------------------------------- 与 Java 同构的解析

def java_filename_of(disposition: str):
    """Api.filenameOf 的等价实现（步骤一一对应，含 filename* 优先与百分号解码）。"""

    def header_value(header, key):
        at = header.lower().find(key)
        if at < 0:
            return None
        rest = header[at + len(key):].strip()
        if rest.startswith('"'):
            end = rest.find('"', 1)
            return rest[1:] if end < 0 else rest[1:end]
        semi = rest.find(";")
        return (rest if semi < 0 else rest[:semi]).strip()

    if not disposition or not disposition.strip():
        return None
    starred = header_value(disposition, "filename*=")
    if starred is not None:
        encoded = starred
        mark = encoded.find("''")          # utf-8''%E4%B8%AD…
        if mark >= 0:
            encoded = encoded[mark + 2:]
        try:
            return unquote(encoded, encoding="utf-8", errors="strict")
        except Exception:
            return encoded
    plain = header_value(disposition, "filename=")
    return None if plain is None else plain.strip().strip('"')


def java_mime_of(content_type):
    if not content_type:
        return ""
    return content_type.split(";")[0].strip()


def java_normalize_code(raw):
    """MainActivity.normalizeCode 的等价实现：去掉空白/-/_/. 并转大写。"""
    keep = [c for c in (raw or "") if c not in " \t\r\n-_. " and not c.isspace()]
    return "".join(keep).upper()


# ---------------------------------------------------------------- HTTP

def multipart(fields, filename=None, content=b""):
    boundary = "----ShareLinkBoundary" + uuid.uuid4().hex
    out = bytearray()
    for name, value in fields:
        out += f"--{boundary}\r\n".encode()
        out += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        out += str(value).encode() + b"\r\n"
    if filename is not None:
        ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        out += f"--{boundary}\r\n".encode()
        # 文件名照 App 的写法原样放进 header（UTF-8 原始字节，中文名不做编码）
        out += f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode()
        out += f"Content-Type: {ctype}\r\n\r\n".encode()
        out += content + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def header(headers, name):
    """大小写不敏感地取响应头（HttpURLConnection.getHeaderField 也是这样）。"""
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return ""


def call(method, path, body=None, ctype=None):
    """返回 (status, headers, payload)。payload 是 JSON dict 或 bytes。"""
    request = urllib.request.Request(BASE + path, data=body, method=method)
    if ctype:
        request.add_header("Content-Type", ctype)
    request.add_header("User-Agent", UA)
    try:
        with urllib.request.urlopen(request, timeout=60) as resp:
            raw = resp.read()
            try:
                return resp.status, dict(resp.headers), json.loads(raw.decode() or "{}")
            except (UnicodeDecodeError, json.JSONDecodeError):
                return resp.status, dict(resp.headers), raw
    except urllib.error.HTTPError as err:
        raw = err.read()
        try:
            return err.code, dict(err.headers), json.loads(raw.decode() or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            return err.code, dict(err.headers), {"raw": repr(raw[:200])}


def upload(filename, content=b"ShareLink code-download self-check\n"):
    body, ctype = multipart([("title", "凭码下载自检")], filename=filename, content=content)
    return call("POST", "/api/share-target?response=json", body, ctype)


created = []
print(f"服务端 {BASE}")

# ---- 0) 本地格式校验（App 里点「下载」之前就该拦下来的输入） ----
print("\n[0/4] 输入清洗与格式校验（MainActivity.normalizeCode / isCodeShaped 的等价行为）")
cases = {
    "ab3d-7k9m": "AB3D7K9M",
    " AB3D 7K9M ": "AB3D7K9M",
    "ab3d_7k9m.": "AB3D7K9M",
}
for raw, want in cases.items():
    got = java_normalize_code(raw)
    check(f"{raw!r} → {want}", got == want, f"实际 {got!r}")
check("含易混字符 I 的输入不会被当成合法分享码",
      not all(c in ALPHABET for c in java_normalize_code("ABID7K9M")),
      f"实际 {java_normalize_code('ABID7K9M')}")

# ---- 1) 中文文件名：App 拿到的是 filename*=utf-8''…，必须能解回原名 ----
print("\n[1/4] 上传中文名文件 → GET /api/files/{code} → GET /api/download/{code}")
cn_name = "凭码下载自检-中文名.txt"
status, _, data = upload(cn_name)
code = data.get("code") if isinstance(data, dict) else None
check("上传拿到分享码", status in (200, 201) and bool(code), f"HTTP {status} code={code}")

if code:
    created.append(code)
    status, _, info = call("GET", f"/api/files/{code}")
    check("GET /api/files/{code} 返回 200", status == 200, f"HTTP {status}")
    check("文件名为上传时的中文名", info.get("filename") == cn_name, f"实际 {info.get('filename')!r}")
    check("expired 字段为 false", info.get("expired") is False, f"实际 {info.get('expired')!r}")
    check("seconds_left 是正数", isinstance(info.get("seconds_left"), int) and info["seconds_left"] > 0,
          f"实际 {info.get('seconds_left')!r}")

    status, headers, body = call("GET", f"/api/download/{code}")
    disposition = header(headers, "Content-Disposition")
    content_type = header(headers, "Content-Type")
    check("下载返回 200", status == 200, f"HTTP {status}")
    check("下载头要求 attachment（不在浏览器里直接执行）", disposition.startswith("attachment"),
          disposition)
    parsed = java_filename_of(disposition)
    check("App 的解析逻辑能从 Content-Disposition 解出中文文件名", parsed == cn_name,
          f"header={disposition!r} → 解析出 {parsed!r}")
    check("下载内容与上传一致", body == b"ShareLink code-download self-check\n",
          f"{len(body) if isinstance(body, bytes) else body} 字节")
    check("MIME 解析后不带 charset", java_mime_of(content_type) == "text/plain",
          f"实际 {content_type!r} → {java_mime_of(content_type)!r}")

# ---- 2) ASCII 文件名：走 filename="…" 分支 ----
print("\n[2/4] 上传 ASCII 名文件（Content-Disposition 走 filename=\"…\" 分支）")
status, _, data = upload("plain-name.txt")
code2 = data.get("code") if isinstance(data, dict) else None
if code2:
    created.append(code2)
    status, headers, _ = call("GET", f"/api/download/{code2}")
    disposition = header(headers, "Content-Disposition")
    check("ASCII 名解出 plain-name.txt", java_filename_of(disposition) == "plain-name.txt",
          f"header={disposition!r} → {java_filename_of(disposition)!r}")

# ---- 3) 失败分支：不存在的码 / 格式错的码 ----
print("\n[3/4] 失败分支（App 会照着状态码给中文提示）")
missing = "QQQQQQQQ"
for _ in range(5):                                  # 万一撞上已存在的码
    status, _, info = call("GET", f"/api/files/{missing}")
    if status == 404:
        break
    missing = "".join(uuid.uuid4().hex[:8]).upper().translate(str.maketrans("IO01", "WXYZ"))
check("不存在的码 → 404", status == 404, f"HTTP {status}")
check("404 附带中文原因", isinstance(info, dict) and "不存在" in str(info.get("detail", info)),
      str(info)[:120])

status, _, info = call("GET", "/api/files/AB3D7K9")     # 7 位，格式就不对
check("格式不对的码 → 400", status == 400, f"HTTP {status} {str(info)[:80]}")
status, _, info = call("GET", "/api/files/ABID7K9M")    # 含易混字符 I
check("含 I 的码 → 400（App 本地也会拦）", status == 400, f"HTTP {status} {str(info)[:80]}")

# ---- 4) 清理 ----
print("\n[4/4] 清理测试分享码")
for c in created:
    status, _, _ = call("DELETE", f"/api/files/{c}")
    check(f"删除 {c}", status == 200, f"HTTP {status}")
    status, _, _ = call("GET", f"/api/files/{c}")
    check(f"{c} 已不可查（清理干净，跑完不留垃圾）", status == 404, f"HTTP {status}")

print(f"\n通过 {passed} 项" + (f"，失败 {len(failed)} 项：{failed}" if failed else "，全部通过 ✅"))
raise SystemExit(1 if failed else 0)
