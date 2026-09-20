"""前端一致性校验：JS 引用的 DOM id / class 必须存在；emoji 必须为零。"""
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

dup_ids = sorted({i for i in html_ids if html.count(f'id="{i}"') > 1})
EMOJI = re.compile('[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F]')
emoji_hits = {name: EMOJI.findall(text) for name, text in
              (("index.html", html), ("app.js", js), ("style.css", css))}

print(f"HTML id {len(html_ids)} 个 / JS 引用 {len(js_ids)} 个")
print("JS 引用但 HTML 缺失:", missing_ids or "无")
print("JS 用到但 CSS 未定义:", missing_classes or "无")
print("symbol 定义:", len(sprite_ids), "| use 引用:", len(used), "| 引用未定义:", missing_symbols or "无")
print("HTML 重复 id:", dup_ids or "无")
for name, hits in emoji_hits.items():
    print(f"emoji 残留 {name}:", hits or "无")

problems = missing_ids or missing_classes or missing_symbols or dup_ids or any(emoji_hits.values())
print("结论:", "❌ 有问题" if problems else "✅ 全部通过")
sys.exit(1 if problems else 0)
