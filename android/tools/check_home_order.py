#!/usr/bin/env python3
"""首页卡片顺序的源码级检查（本机没有安卓设备，布局顺序只能这样守住）。

用户要求：「凭分享码下载」（接收）要排在「发文件给别的设备」（发送）**下面**。
这里直接解析 MainActivity.showHome() 里 addView(...) 的先后顺序并断言，避免以后改回去没人发现。

用法：python3 android/tools/check_home_order.py
"""
import pathlib
import re
import sys

SOURCE = pathlib.Path(__file__).resolve().parents[1] / "app/src/main/java/me/skylare/sharelink/MainActivity.java"

# 注册设备后的期望顺序（不含标题）
EXPECTED = ["deviceCard", "inboxCard", "groupCard", "ttlCard", "actionsCard", "textCard", "codeCard"]
CARD_LABEL = {
    "deviceCard": "我的设备",
    "inboxCard": "收件箱（收到的文件）",
    "groupCard": "设备组",
    "ttlCard": "上传有效期",
    "actionsCard": "发文件给别的设备（发送）",
    "textCard": "发文本",
    "codeCard": "凭分享码下载（接收）",
}

text = SOURCE.read_text(encoding="utf-8")
body = re.search(r"private void showHome\(\)\s*\{(.*?)\n    \}", text, re.S)
if not body:
    sys.exit("❌ 找不到 showHome() —— 源码结构变了，检查脚本要跟着改")
section = body.group(1)

# 「有设备」那个分支：从 else { 开始取
else_branch = re.search(r"\} else \{\s*(.*?)\n        \}", section, re.S)
if not else_branch:
    sys.exit("❌ showHome() 里找不到「已登记设备」分支")
branch = else_branch.group(1)

found = re.findall(r"addView\((?:withTop\()?(\w+)\(\)", branch)
order = [name for name in found if name in CARD_LABEL]

print("showHome()（已登记设备）中的卡片顺序：")
for index, name in enumerate(order, 1):
    print(f"  {index}. {name:12} {CARD_LABEL[name]}")

ok = order == EXPECTED
errors = []
if not ok:
    errors.append(f"顺序应为 {EXPECTED}，实际 {order}")
if order and order.index("codeCard") < order.index("actionsCard"):
    errors.append("「凭分享码下载」还在「发文件给别的设备」上面")

# 未登记设备分支：接收卡片也应当在最后
null_branch = re.search(r"if \(device == null\) \{(.*?)\n        \} else", section, re.S)
if null_branch:
    names = re.findall(r"addView\(withTop\((\w+)\(\)", null_branch.group(1))
    print(f"\nshowHome()（未登记设备）顺序：{names}")
    if names and names[-1] != "codeCard":
        errors.append(f"未登记分支里「凭分享码下载」应为最后一个，实际 {names[-1]}")

print()
if errors:
    for err in errors:
        print("❌", err)
    sys.exit(1)
print("结果: ✅ 接收（凭分享码下载）排在发送（发文件给别的设备）下面")
