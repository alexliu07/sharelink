#!/usr/bin/env python3
"""从 GitHub Release 下载最新 APK 并做离线校验（不需要安卓设备）。

校验四件事：
  1. 下载的字节与 CI 当场算出的 sha256 一致（证明拿到的是 CI 构建的那份）
  2. APK 里确实声明了系统分享目标（二进制 AndroidManifest 的字符串池是 UTF-16LE）
  3. dex 里编进了正确的服务端地址
  4. 签名证书指纹（用于确认「固定签名」没变——签名变了装新版本就得先卸载）

用法：python3 android/tools/verify_release.py [期望的证书指纹]
"""
import hashlib
import io
import re
import struct
import sys
import urllib.request
import zipfile

BASE = "https://github.com/alexliu07/sharelink/releases/download/android-latest"
APK_URL = f"{BASE}/ShareLink.apk"
SUM_URL = f"{BASE}/ShareLink.apk.sha256"

MANIFEST_NEEDLES = [
    "android.intent.action.SEND",
    "android.intent.action.SEND_MULTIPLE",
    "android.intent.category.DEFAULT",
    "android.intent.category.LAUNCHER",
    "android.permission.INTERNET",
    "*/*",
]
DEX_NEEDLES = [
    "https://skylare.me/share/api/share-target?response=json",
    "multipart/form-data; boundary=",
]


def fetch(url):
    with urllib.request.urlopen(url, timeout=120) as resp:
        return resp.read()


def v2_signer_cert(apk):
    """从 APK Signing Block 里取出 v2 方案的签名者证书（DER）。"""
    u32 = lambda b, o: struct.unpack_from("<I", b, o)[0]
    u64 = lambda b, o: struct.unpack_from("<Q", b, o)[0]
    eocd = apk.rfind(b"PK\x05\x06")
    cd = u32(apk, eocd + 16)
    size = u64(apk, cd - 24)
    start = cd - size - 8
    if apk[cd - 16:cd] != b"APK Sig Block 42":
        raise SystemExit("❌ 没有 v2 签名块，APK 未签名？")
    o, v2 = start + 8, None
    while o < cd - 24:
        ln = u64(apk, o)
        o += 8
        sid = u32(apk, o)
        o += 4
        if sid == 0x7109871a:
            v2 = apk[o:o + ln - 4]
        o += ln - 4
    if not v2:
        raise SystemExit("❌ 找不到 v2 签名方案")
    signers = v2[4:4 + u32(v2, 0)]
    signer = signers[4:4 + u32(signers, 0)]
    signed = signer[4:4 + u32(signer, 0)]
    coff = 4 + u32(signed, 0)
    certs = signed[coff + 4:coff + 4 + u32(signed, coff)]
    return certs[4:4 + u32(certs, 0)]


def cert_fingerprint(der):
    """证书 DER 的 sha256（和 `openssl x509 -fingerprint -sha256` 一致）。"""
    return hashlib.sha256(der).hexdigest().upper()


def main():
    expect_fp = sys.argv[1].replace(":", "").upper() if len(sys.argv) > 1 else None
    print(f"下载 {APK_URL}")
    apk = fetch(APK_URL)
    print(f"  大小 {len(apk)} 字节")
    ci_sum = fetch(SUM_URL).split()[0].decode()
    local = hashlib.sha256(apk).hexdigest()
    ok1 = local == ci_sum
    print(f"  sha256 本地 {local[:16]}… / CI {ci_sum[:16]}… → {'✅ 一致' if ok1 else '❌ 不一致'}")

    z = zipfile.ZipFile(io.BytesIO(apk))
    manifest, dex = z.read("AndroidManifest.xml"), z.read("classes.dex")
    ok2 = all(n.encode("utf-16-le") in manifest for n in MANIFEST_NEEDLES)
    print(f"  分享目标声明（SEND/SEND_MULTIPLE/LAUNCHER/*/*） → {'✅ 齐全' if ok2 else '❌ 有缺失'}")
    missing = [n for n in DEX_NEEDLES if n.encode() not in dex]
    ok3 = not missing
    print(f"  服务端地址已编入 dex → {'✅' if ok3 else '❌ 缺 ' + str(missing)}")

    der = v2_signer_cert(apk)
    fp = cert_fingerprint(der)
    subj = re.search(rb"Android Debug", der) is not None
    print(f"  签名证书 sha256: {'%s:%s:%s…%s' % (fp[0:2], fp[2:4], fp[4:6], fp[-4:])}（CN=Android Debug: {'是' if subj else '否'}）")
    ok4 = True
    if expect_fp:
        ok4 = fp == expect_fp
        print(f"  与期望指纹比对 → {'✅ 一致（签名固定，可以直接升级安装）' if ok4 else '❌ 变了！装新版本前要先卸载'}")

    print("结果:", "✅ 全部通过" if (ok1 and ok2 and ok3 and ok4) else "❌ 有检查未通过")
    return 0 if (ok1 and ok2 and ok3 and ok4) else 1


if __name__ == "__main__":
    sys.exit(main())
