"""前端一致性校验：JS 引用的 DOM id / class 必须存在；emoji 必须为零。"""
import json
import re
import sys
from pathlib import Path

static = Path("/home/azureuser/sharelink/static")
js = (static / "app.js").read_text(encoding="utf-8")
html = (static / "index.html").read_text(encoding="utf-8")
css = (static / "style.css").read_text(encoding="utf-8")

html_ids = set(re.findall(r'id="([^"]+)"', html))
js_ids = set(re.findall(r'\$\("([^"]+)"\)', js))
missing_ids = sorted(js_ids - html_ids)

js_classes = set()
for sel in re.findall(r'querySelector(?:All)?\("([^"]+)"\)', js):
    js_classes |= set(re.findall(r'\.([a-zA-Z][\w-]*)', sel))
css_classes = set(re.findall(r'\.([a-zA-Z][\w-]*)', css))
missing_classes = sorted(js_classes - css_classes)

sprite_ids = re.findall(r'<symbol id="([^"]+)"', html)
used = set(re.findall(r'<use href="#([^"]+)"', html)) | set(re.findall(r'icon\("([^"]+)"\)', js)) \
    | set(re.findall(r'#(icon-[a-z-]+)', js))
missing_symbols = sorted(used - set(sprite_ids))

# tab → 面板映射：每个标签页的 data-panel 都必须在 app.js 的 els.panels 里登记，
# 否则 switchTab() 切过去时面板拿不到 .active，整块（含按钮）都是 display:none。
panels_block = re.search(r"panels:\s*\{([^}]*)\}", js)
registered = dict(re.findall(r'(\w+):\s*\$\("([^"]+)"\)', panels_block.group(1) if panels_block else ""))
tabs = re.findall(r'<button[^>]*\bid="([^"]+)"[^>]*\bdata-panel="([^"]+)"', html)
tab_problems = []
for tab_id, panel_id in tabs:
    key = panel_id.replace("panel-", "")
    if key not in registered:
        tab_problems.append(f"{tab_id} → {key} 未登记在 els.panels 里（面板不会显示）")
    elif registered[key] != panel_id:
        tab_problems.append(f"{tab_id} 指向 {panel_id}，但 els.panels.{key} 是 {registered[key]}")

orphan_panels = sorted(set(registered.values()) - {p for _, p in tabs} - set(html_ids))

# ---- PWA 接线检查：manifest / 图标 / service worker 预缓存清单都必须真实存在 ----
pwa_problems: list[str] = []
manifest_path = static / "manifest.webmanifest"
if not manifest_path.exists():
    pwa_problems.append("缺少 manifest.webmanifest")
else:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for field in ("name", "short_name", "start_url", "scope", "display", "theme_color", "icons"):
        if not manifest.get(field):
            pwa_problems.append(f"manifest 缺字段 {field}")
    sizes = {icon.get("sizes") for icon in manifest.get("icons", [])}
    for need in ("192x192", "512x512"):
        if need not in sizes:
            pwa_problems.append(f"manifest 缺少 {need} 图标（安卓安装条件）")
    if not any(i.get("purpose") == "maskable" for i in manifest.get("icons", [])):
        pwa_problems.append("manifest 没有 maskable 图标（安卓自适应图标会裁得很难看）")
    # 图标 URL 带 ?v=N 是刻意的：图标按 7 天缓存，换图标必须换 URL，否则手机/CF 都用旧的
    def file_of(url: str) -> str:
        return url.split("?")[0].split("#")[0].lstrip("./")

    icon_urls = [icon["src"] for icon in manifest.get("icons", [])]
    for url in icon_urls:
        if not (static / file_of(url)).exists():
            pwa_problems.append(f"manifest 引用的图标不存在：{url}")
    if any("?" not in url for url in icon_urls):
        pwa_problems.append("manifest 里图标 URL 没带版本串 ?v=N（改了图标会被 7 天缓存挡住）")
    if not manifest.get("share_target"):
        pwa_problems.append("manifest 没有 share_target（安卓分享面板入口）")
    else:
        action = manifest["share_target"].get("action", "")
        if manifest["share_target"].get("method") != "POST" or not action:
            pwa_problems.append("share_target 必须是 POST + action")
        elif action == "__SHARE_TARGET_ACTION__":
            # 静态文件里是占位符，由 app/main.py 的 /manifest.webmanifest 路由换成绝对地址
            main_py = (static.parent / "app" / "main.py").read_text(encoding="utf-8")
            if "SHARE_TARGET_ACTION_PLACEHOLDER" not in main_py:
                pwa_problems.append("manifest 里是 action 占位符，但 app/main.py 没有替换它的路由（分享面板会失效）")
        elif not action.startswith(("http://", "https://")):
            pwa_problems.append(
                f"share_target.action 必须是绝对 URL（安卓 Chrome 才会把应用放进分享面板），现在是 {action!r}"
            )

# sw.js 的预缓存清单：每个 URL 都要能对应到磁盘上的文件，否则 SW 安装时静默跳过
sw_path = static / "sw.js"
if not sw_path.exists():
    pwa_problems.append("缺少 sw.js")
else:
    sw_js = sw_path.read_text(encoding="utf-8")
    shell_block = re.search(r"const SHELL = \[(.*?)\];", sw_js, re.S)
    if not shell_block:
        pwa_problems.append("sw.js 里找不到 SHELL 预缓存清单")
    else:
        urls = re.findall(r'"([^"]+)"', shell_block.group(1))
        for url in urls:
            rel = url.lstrip("./") or "index.html"
            if not (static / rel).exists():
                pwa_problems.append(f"sw.js 预缓存的文件不存在：{url}")
        if not urls:
            pwa_problems.append("sw.js 的 SHELL 清单是空的")

if 'rel="manifest"' not in html:
    pwa_problems.append("index.html 没有引用 manifest")
if "apple-touch-icon" not in html:
    pwa_problems.append("index.html 没有 apple-touch-icon（iOS 主屏图标）")
else:
    match = re.search(r'rel="apple-touch-icon"[^>]*href="([^"]+)"', html)
    if match and not (static / match.group(1).split("?")[0].lstrip("./")).exists():
        pwa_problems.append(f"apple-touch-icon 指向的文件不存在：{match.group(1)}")
    elif match and match.group(1).split("?")[0].endswith(".svg"):
        pwa_problems.append("apple-touch-icon 指向 SVG —— iOS 只认 PNG")
    elif match and "?" not in match.group(1):
        pwa_problems.append("apple-touch-icon 的 URL 没带版本串 ?v=N（换了图标 iOS 不认新图）")

dup_ids = sorted({i for i in html_ids if html.count(f'id="{i}"') > 1})
EMOJI = re.compile('[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F]')
emoji_hits = {name: EMOJI.findall(text) for name, text in
              (("index.html", html), ("app.js", js), ("style.css", css))}

print(f"HTML id {len(html_ids)} 个 / JS 引用 {len(js_ids)} 个")
print("JS 引用但 HTML 缺失:", missing_ids or "无")
print("JS 用到但 CSS 未定义:", missing_classes or "无")
print("symbol 定义:", len(sprite_ids), "| use 引用:", len(used), "| 引用未定义:", missing_symbols or "无")
print("HTML 重复 id:", dup_ids or "无")
print("标签页:", [(t, p) for t, p in tabs])
print("els.panels 登记:", registered)
print("标签页→面板映射问题:", tab_problems or "无")
print("登记了但页面没有的面板:", orphan_panels or "无")
print("PWA 接线问题:", pwa_problems or "无")
for name, hits in emoji_hits.items():
    print(f"emoji 残留 {name}:", hits or "无")

problems = (missing_ids or missing_classes or missing_symbols or dup_ids
            or tab_problems or orphan_panels or pwa_problems or any(emoji_hits.values()))
print("结论:", "❌ 有问题" if problems else "✅ 全部通过")
sys.exit(1 if problems else 0)
