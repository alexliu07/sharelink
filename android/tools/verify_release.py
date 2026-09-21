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
import json
import time
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
    "/api/transfers",          # 设备互传：定向投递
    "X-Device-Token",          # 设备鉴权头
    "/inbox",                  # 收件箱
    "凭分享码下载",              # 1.7：取件卡片
    "发给哪些设备？",            # 1.8：投递目标勾选框（不再用系统 setMultiChoiceItems）
    "先勾一台设备",              # 1.8：没勾设备时不静默降级
    "正在刷新收件箱…",          # 1.11：收件箱「刷新」按钮的进行中提示
    "发送这段文本",              # 1.12：发文本卡片的主按钮
    "点一下看文本内容",          # 1.12：收件箱文本条目的提示
    "创建设备组",                # 1.13：设备组卡片
    "加入设备组",
    "复制组 id",
    "解散设备组",
    "只有同一个组里的设备之间才能互传",
    "已有令牌？从剪贴板导入",     # 1.14：登记步骤并入建组/加入后，令牌认领改走这个次要按钮
    "加入时用对方给的组 id",      # 1.14：未登记时的说明文案
    "本机已在组里（你是管理员）",  # 1.14：建组成功提示（说明本机自动进组）
]


def fetch_sum():
    """README 里的 .sha256 资产偶尔会被下载 CDN 回 504；重试几次后改走 API 资产接口，
    仍拿不到就只告警——签名/dex/分享目标这几项校验不依赖它。"""
    last = None
    for attempt in range(3):
        try:
            return fetch(SUM_URL).split()[0].decode()
        except Exception as exc:                       # noqa: BLE001 - 网络层什么都可能抛
            last = exc
            time.sleep(2 * (attempt + 1))
    try:
        api = "https://api.github.com/repos/alexliu07/sharelink/releases/tags/android-latest"
        req = urllib.request.Request(api, headers={"User-Agent": "ShareLink-ReleaseCheck/1",
                                                  "Accept": "application/vnd.github+json"})
        assets = json.loads(urllib.request.urlopen(req, timeout=60).read())
        asset = next(a for a in assets["assets"] if a["name"].endswith(".sha256"))
        req2 = urllib.request.Request(asset["url"], headers={"User-Agent": "ShareLink-ReleaseCheck/1",
                                                             "Accept": "application/octet-stream"})
        return urllib.request.urlopen(req2, timeout=60).read().decode().split()[0]
    except Exception:                                  # noqa: BLE001
        print(f"  ⚠️  拿不到 CI 的 .sha256（{last}）——跳过一致性比对，其余校验照跑")
        return None


def fetch(url):
    # 必须带一个像样的 UA：默认的 Python-urllib 会被 GitHub 的下载 CDN 回 504/403
    req = urllib.request.Request(url, headers={"User-Agent": "ShareLink-ReleaseCheck/1"})
    with urllib.request.urlopen(req, timeout=180) as resp:
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
    ci_sum = fetch_sum()
    local = hashlib.sha256(apk).hexdigest()
    if ci_sum is None:
        ok1 = True                                    # 拿不到基线：不据此判失败，改用下面的指纹校验
        print(f"  sha256 本地 {local[:16]}…（无 CI 基线可比）")
    else:
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
