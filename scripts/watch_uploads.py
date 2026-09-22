#!/usr/bin/env python3
"""ShareLink 上传监测：列出「上次检查之后」新上传的文件/文本，做机械风险标记，供 agent 写报告。

用法：python3 scripts/watch_uploads.py [--window-minutes 35] [--state /path/state.json]
输出：纯文本报告（stdout），同时把已检查到的水位写进 state 文件。

判定规则刻意简单、可复现：
  - 扩展名 / magic bytes 判类型（可执行、脚本、压缩包、文档、图片、音视频、未知）
  - 危险标记：可执行/脚本类、双重扩展名（x.pdf.exe）、无扩展名、超大文件、文件名含控制字符、
    投递附言里有链接、文本内容里出现常见脚本/命令特征
"""
import argparse
import json
import os
import pathlib
import sqlite3
import sys
import time

BASE = pathlib.Path(__file__).resolve().parents[1]
DB = pathlib.Path(os.environ.get("SHARELINK_DB_PATH", BASE / "data" / "sharelink.db"))
STORAGE = pathlib.Path(os.environ.get("SHARELINK_STORAGE_DIR", BASE / "storage"))
DEFAULT_STATE = pathlib.Path.home() / ".hermes" / "cron" / "state" / "sharelink_uploads.json"

EXEC_EXT = {"exe", "msi", "bat", "cmd", "com", "scr", "dll", "sys", "apk", "ipa", "deb", "rpm",
            "sh", "bash", "zsh", "ps1", "vbs", "vb", "js", "jse", "jar", "py", "pyc", "pl", "php",
            "app", "bin", "run", "elf", "so", "dylib", "lnk", "reg", "hta", "wsf", "pif"}
ARCHIVE_EXT = {"zip", "rar", "7z", "tar", "gz", "bz2", "xz", "tgz", "iso", "dmg", "cab"}
DOC_EXT = {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "md", "csv", "rtf", "epub"}
MEDIA_EXT = {"jpg", "jpeg", "png", "gif", "webp", "bmp", "svg", "heic", "mp3", "wav", "flac",
             "m4a", "aac", "ogg", "mp4", "mov", "mkv", "webm", "avi"}
SUSPECT_TEXT = ["#!/", "<script", "powershell", "cmd.exe", "rm -rf", "eval(", "base64 -d",
                "curl http", "wget http", "chmod +x", "nc -e", "/etc/passwd", "CREATE TABLE",
                "DROP TABLE", "INSERT INTO", "比特币", "收款码", "加微信", "扫码", "裸聊", "赌博",
                "博彩", "刷单", "代考", "身份证", "银行卡", "password=", "私钥", "BEGIN RSA"]


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def sniff(path):
    """看前 16 字节猜类型（只读文件头，不整读）。"""
    try:
        head = path.open("rb").read(16)
    except OSError as exc:
        return f"读不到（{exc}）"
    if head.startswith(b"%PDF-"):
        return "PDF 文档"
    if head.startswith(b"PK\x03\x04"):
        return "ZIP 容器（zip/docx/xlsx/apk 都是这个头）"
    if head.startswith(b"\x7fELF"):
        return "Linux 可执行文件（ELF）"
    if head.startswith(b"MZ"):
        return "Windows 可执行文件（PE/EXE）"
    if head.startswith(b"\x89PNG"):
        return "PNG 图片"
    if head.startswith(b"\xff\xd8\xff"):
        return "JPEG 图片"
    if head.startswith(b"GIF8"):
        return "GIF 图片"
    if head.startswith(b"\x1f\x8b"):
        return "gzip 压缩"
    if head.startswith(b"Rar!"):
        return "RAR 压缩"
    if head.startswith(b"\xca\xfe\xba\xbe"):
        return "Java class / Mach-O fat"
    if all(c in b"\r\n\t" or 32 <= c < 127 or c >= 128 for c in head):
        return "纯文本（需人工看内容）"
    return "未知二进制"


def load_state(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:                                  # noqa: BLE001 - 首次运行没有 state
        return {"since": None, "seen_codes": []}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window-minutes", type=float, default=35.0)
    ap.add_argument("--state", default=str(DEFAULT_STATE))
    ap.add_argument("--max-items", type=int, default=40)
    args = ap.parse_args()

    state_path = pathlib.Path(args.state)
    state = load_state(state_path)
    since = state.get("since")
    if since is None:
        since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - args.window_minutes * 60))
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    seen = set(state.get("seen_codes") or [])

    if not DB.exists():
        print(f"⚠️ 找不到数据库 {DB}（服务未部署？）")
        return 0

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = list(con.execute(
        "select * from files where created_at > ? order by created_at", (since,)))
    transfers = list(con.execute(
        "select * from transfers where created_at > ? order by created_at", (since,)))
    devices = {r["id"]: r["name"] for r in con.execute("select id, name from devices")}
    live = list(con.execute("select count(*) c, coalesce(sum(size),0) s from files where expires_at > ?",
                            (now,)))[0]
    con.close()

    rows = [r for r in rows if r["code"] not in seen]
    print(f"ShareLink 上传监测：窗口 {since} → {now}（UTC）")
    print(f"新上传：{len(rows)} 件；新投递记录：{len(transfers)} 条")
    print(f"当前库里未过期的文件/文本总数：{live['c']} 件，合计 {human(live['s'])}")
    if not rows:
        print("（窗口内没有新的上传）")
    by_tx = {t["code"]: t for t in transfers}

    flags_global = []
    for index, r in enumerate(rows[: args.max_items], 1):
        code, name = r["code"], r["original_name"] or ""
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        path = STORAGE / (r["stored_name"] or "")
        ctype = r["content_type"] or ""
        is_text = ctype.startswith("text/") or ext == "txt" or ctype == "application/json"
        print(f"\n[{index}] {code} | {name} | {human(r['size'])} | mime={ctype or '未知'} | "
              f"创建 {r['created_at']} | 过期 {r['expires_at']} | 下载 {r['download_count']} 次")
        tx = by_tx.get(code)
        if tx:
            print(f"    投递给设备：{devices.get(tx['device_id'], tx['device_id'])}"
                  f"（发件人 {tx['from_name'] or tx['from_device_id'] or '未知'}）"
                  f"附言：{tx['note'] or '（无）'}")
        flags = []
        if ext in EXEC_EXT:
            flags.append(f"可执行/脚本类扩展名 .{ext}")
        elif ext in ARCHIVE_EXT:
            flags.append(f"压缩包 .{ext}（内容看不到）")
        if len(name.split(".")) > 2 and name.split(".")[-1].lower() in EXEC_EXT:
            flags.append("双重扩展名（伪装成文档）")
        if not ext:
            flags.append("没有扩展名")
        if any(ord(c) < 32 for c in name.replace("\t", "")):
            flags.append("文件名里有控制字符")
        if r["size"] > 20 * 1024 * 1024:
            flags.append(f"体积偏大（{human(r['size'])}）")
        if ext in DOC_EXT or ext in MEDIA_EXT or ext in ARCHIVE_EXT:
            pass
        if path.exists():
            print(f"    文件头判定：{sniff(path)}")
        else:
            flags.append("落盘文件不存在（可能已过期清理）")
        if is_text:
            try:
                body = path.read_text(encoding="utf-8", errors="replace")
                print(f"    文本内容（前 300 字）：{body[:300]!r}")
                hit = [w for w in SUSPECT_TEXT if w in body]
                if hit:
                    flags.append("文本里出现可疑特征：" + "、".join(hit[:5]))
            except OSError as exc:
                flags.append(f"文本读不出来（{exc}）")
        if name and any(w in name for w in ("密码", "passwd", "身份证", "银行卡", "私钥", "key")):
            flags.append("文件名像是在传敏感信息")
        print("    机械风险标记：" + ("、".join(flags) if flags else "无"))
        flags_global += [f"{code} {name}：{f}" for f in flags]

    if len(rows) > args.max_items:
        print(f"\n…还有 {len(rows) - args.max_items} 件未展开（超出 --max-items）")

    print("\n=== 供 agent 判断的要点 ===")
    print("机械标记汇总：" + ("；".join(flags_global) if flags_global else "无"))
    print("请结合文件名、附言、文本内容判断是否涉黄赌毒、诈骗钓鱼、恶意代码、隐私泄漏等；"
          "有则**重点强调**并给出处理建议（如删除分享码 DELETE /api/files/{code}）。")

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(
        {"since": now, "seen_codes": sorted(seen | {r["code"] for r in rows})[-500:]},
        ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
