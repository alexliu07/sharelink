/**
 * 真 DOM 测试：用 jsdom 加载 static/index.html、执行 static/app.js，
 * 模拟点击「设备」标签页与整个设备互传流程（fetch/XHR 打桩，不发真实请求）。
 * 这个测试就是为了抓 "tab 切过去但面板不显示" 这类只有真 DOM 才暴露的问题。
 */
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const STATIC = "/home/azureuser/sharelink/static";
const html = fs.readFileSync(path.join(STATIC, "index.html"), "utf8");
const css = fs.readFileSync(path.join(STATIC, "style.css"), "utf8");
const appJs = fs.readFileSync(path.join(STATIC, "app.js"), "utf8");

let passed = 0;
const check = (label, fn) => {
  try { fn(); passed++; console.log(`  ✅ ${label}`); }
  catch (err) { console.log(`  ❌ ${label}\n     ${err.message}`); process.exitCode = 1; }
};

const nowIso = new Date(Date.now() + 3600_000).toISOString();
const evtIso = new Date(Date.now() + 600_000).toISOString();
const DEVICES = [
  { id: "dev_AAAABBBB", name: "我的笔记本", created_at: nowIso, last_seen_at: nowIso, idle_seconds: 12, inbox_count: 1 },
  { id: "dev_CCCCDDDD", name: '室友<img src=x onerror="alert(1)">的 iPad', created_at: nowIso, last_seen_at: nowIso, idle_seconds: 3600, inbox_count: 0 },
];
const INBOX = {
  device: { id: "dev_AAAABBBB", name: "我的笔记本", created_at: nowIso, last_seen_at: nowIso, idle_seconds: 3 },
  count: 1, unread: 1,
  items: [{
    code: "AB3D7K9M", filename: "周会材料.pdf", size: 1536, sha256: "x", content_type: "application/pdf",
    created_at: nowIso, expires_at: evtIso, seconds_left: 600, download_count: 0,
    transfer_id: 1, from_device_id: "dev_CCCCDDDD", from_name: "室友的 iPad", note: "看完删掉",
    sent_at: nowIso, seen: false,
    share_url: "http://localhost/share/?code=AB3D7K9M",
    download_url: "http://localhost/share/api/download/AB3D7K9M",
  }],
};
const routes = {
  "GET /api/stats": [200, { shares: 3, shares_bytes: 2048, devices: 2, transfers: 1, disk_files: 3, disk_bytes: 2048,
    max_upload_mb: 200, default_ttl_seconds: 3600, min_ttl_seconds: 60, max_ttl_seconds: 2592000,
    cleanup_interval_seconds: 60, device_idle_days: 30, max_targets_per_send: 20 }],
  "GET /api/devices": [200, { count: DEVICES.length, devices: DEVICES }],
  "POST /api/devices": [201, { id: "dev_AAAABBBB", name: "我的笔记本", created_at: nowIso, last_seen_at: nowIso,
    idle_seconds: 0, inbox_count: 0, token: "tok_secret_value" }],
  "GET /api/devices/dev_AAAABBBB/inbox": [200, INBOX],
  "POST /api/devices/dev_AAAABBBB/inbox/seen": [200, { marked: 1 }],
  "PATCH /api/devices/dev_AAAABBBB": [200, { id: "dev_AAAABBBB", name: "改名后", created_at: nowIso, last_seen_at: nowIso, idle_seconds: 0, inbox_count: 1 }],
  "DELETE /api/devices/dev_AAAABBBB/inbox/AB3D7K9M": [200, { removed: true }],
  "POST /api/transfers": [201, { code: "ZZ99YY88", filename: "报告.pdf", size: 10, ttl_seconds: 3600, ttl_human: "1 小时",
    from_name: "我的笔记本", targets: [{ id: "dev_AAAABBBB", name: "我的笔记本" }, { id: "dev_CCCCDDDD", name: "室友的 iPad" }],
    transfer_count: 2 }],
  // 令牌相关的路由按请求头给不同结果，才能验证"导入时会先校验令牌"
  "GET /api/devices/dev_ZZZZ9999/inbox": ({ headers }) =>
    headers?.["X-Device-Token"] === "tok_restored"
      ? [200, { device: { id: "dev_ZZZZ9999", name: "旧手机", created_at: nowIso, last_seen_at: nowIso, idle_seconds: 5 },
          count: 2, unread: 1, items: [] }]
      : [403, { detail: { error: "bad_device_token", message: "设备令牌无效" } }],
};
const calls = [];
const route = (method, url, ctx = {}) => {
  const hit = routes[`${method} ${url}`];
  if (typeof hit === "function") return hit(ctx);
  return hit || [404, { detail: { error: "not_found", message: `未打桩: ${method} ${url}` } }];
};

const dom = new JSDOM(html, { url: "http://localhost/share/", runScripts: "dangerously", pretendToBeVisual: true });
const { window } = dom;
const doc = window.document;

// 把 style.css 以 <style> 注入，getComputedStyle 才会带上级联（否则 display 判断无意义）
const style = doc.createElement("style");
style.textContent = css;
doc.head.appendChild(style);

window.fetch = async (url, options = {}) => {
  const method = (options.method || "GET").toUpperCase();
  const [status, body] = route(method, url, { headers: options.headers, body: options.body });
  calls.push({ method, url, body: options.body, headers: options.headers });
  return { ok: status < 400, status, json: async () => body };
};
class FakeXHR {
  constructor() { this.upload = { addEventListener: (t, f) => { this.upload[t] = f; } }; }
  open(method, url) { this.method = method; this.url = url; }
  setRequestHeader(key, value) { (this.headers ||= {})[key] = value; }
  addEventListener(type, fn) { (this.events ||= {})[type] = fn; }
  send(body) {
    const [status, payload] = route(this.method, this.url, { headers: this.headers, body });
    calls.push({ method: this.method, url: this.url, body, headers: this.headers });
    this.status = status; this.responseText = JSON.stringify(payload);
    setTimeout(() => {
      this.upload.progress?.({ lengthComputable: true, loaded: 5, total: 10 });
      this.events?.load?.();
    }, 0);
  }
}
window.XMLHttpRequest = FakeXHR;

// service worker：jsdom 不实现，桩掉并记录注册参数
const swCalls = [];
Object.defineProperty(window.navigator, "serviceWorker", {
  configurable: true,
  value: { register: async (url, options) => { swCalls.push({ url, options }); return { update() {} }; } },
});
// jsdom 这个版本没有 matchMedia，补一个（真实浏览器都有）
if (!window.matchMedia) {
  window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
}

window.eval(appJs);
const $ = (sel) => doc.querySelector(sel);
const visible = (el) => !el.classList.contains("hidden") && window.getComputedStyle(el).display !== "none";

(async () => {
  console.log("== 标签页切换（这次 bug 的现场）==");
  check("三个标签页，设备页 data-panel 齐全", () => {
    const tabs = [...doc.querySelectorAll(".tab")];
    assert.strictEqual(tabs.length, 3, `实际 ${tabs.length} 个`);
    assert.deepStrictEqual(tabs.map((t) => t.dataset.panel), ["panel-upload", "panel-fetch", "panel-devices"]);
  });
  check("默认显示上传面板，设备面板隐藏", () => {
    assert.ok(visible($("#panel-upload")));
    assert.ok(!visible($("#panel-devices")));
  });
  $("#tab-devices").click();
  check("点「设备」后设备面板 active 且真的可见", () => {
    assert.ok($("#panel-devices").classList.contains("active"), "没有 active 类 → switchTab 未登记该面板");
    assert.strictEqual(window.getComputedStyle($("#panel-devices")).display, "block", "CSS 上仍是 display:none");
    assert.ok(!$("#panel-upload").classList.contains("active"));
  });
  check("标签页 aria-selected 正确", () => {
    assert.strictEqual($("#tab-devices").getAttribute("aria-selected"), "true");
    assert.strictEqual($("#tab-upload").getAttribute("aria-selected"), "false");
  });

  console.log("== 添加本设备 ==");
  check("「添加本设备」按钮存在、可见、文案正确", () => {
    const btn = $("#device-add-btn");
    assert.ok(btn, "按钮不存在");
    assert.ok(btn.closest("#panel-devices"), "按钮不在设备面板里");
    assert.ok(visible(btn), "按钮被隐藏");
    assert.ok(btn.textContent.includes("添加本设备"), `文案是「${btn.textContent.trim()}」`);
  });
  check('未登记时显示登记表单、隐藏「本设备」卡片', () => {
    assert.ok(visible($("#device-setup")));
    assert.ok(!visible($("#device-self")));
  });

  $("#device-name-input").value = "我的笔记本";
  $("#device-add-btn").click();
  await new Promise((r) => setTimeout(r, 20));
  check("登记后令牌存进 localStorage、面板切换", () => {
    const saved = JSON.parse(window.localStorage.getItem("sharelink.device"));
    assert.strictEqual(saved.token, "tok_secret_value");
    assert.strictEqual(saved.id, "dev_AAAABBBB");
    assert.ok(!visible($("#device-setup")), "登记后表单还在");
    assert.ok(visible($("#device-self")), "登记后本设备卡片没出现");
  });
  check("本设备名称/收件箱渲染 + 打开面板自动标已读", () => {
    assert.strictEqual($("#self-name").textContent, "我的笔记本");
    assert.ok($("#inbox-list").textContent.includes("周会材料.pdf"), "收件箱没渲染文件");
    assert.ok($("#inbox-list").textContent.includes("来自 室友的 iPad"));
    assert.ok($("#inbox-list").textContent.includes("剩余"), "没有剩余时间");
    assert.ok($("#inbox-count").textContent.includes("共 1 个"), $("#inbox-count").textContent);
    assert.ok(calls.some((c) => c.url.endsWith("/inbox/seen")), "没有标已读");
    assert.strictEqual($("#inbox-badge").textContent, "0", "已读后角标应清零");
  });
  check("下载链接指向接口返回的 download_url", () => {
    const link = $("#inbox-list a");
    assert.ok(link.getAttribute("href").endsWith("/api/download/AB3D7K9M"), link.getAttribute("href"));
  });
  check("设备名里的 HTML 被当文本（无 XSS）", () => {
    const list = $("#device-list");
    assert.ok(!list.querySelector("img"), "设备名里的 <img> 被解析成了元素 → 没转义");
    assert.ok(list.textContent.includes("<img src=x"), "原始文本应原样显示");
  });

  console.log("== 发送至设备 ==");
  const file = new window.File([new Uint8Array([1, 2, 3])], "报告.pdf", { type: "application/pdf" });
  Object.defineProperty($("#file-input"), "files", { value: [file], configurable: true });
  $("#file-input").dispatchEvent(new window.Event("change"));
  await new Promise((r) => setTimeout(r, 10));
  check("选文件后「发送至设备」可用", () => {
    assert.ok(!$("#send-btn").disabled, "按钮仍是 disabled");
    assert.strictEqual($("#picked-name").textContent, "报告.pdf");
  });
  $("#send-btn").click();
  await new Promise((r) => setTimeout(r, 20));
  check("弹出面板、列出两台设备", () => {
    assert.ok(visible($("#send-modal")));
    assert.strictEqual(doc.querySelectorAll("#send-targets input").length, 2);
    assert.strictEqual($("#send-confirm").disabled, true, "未选设备时不该能发");
  });
  check("设备相关输入框与分享码输入框同款盒子样式（不是浏览器默认外观）", () => {
    const ref = window.getComputedStyle($("#code-input"));
    const props = ["paddingTop", "paddingLeft", "borderRadius", "borderTopWidth", "backgroundColor", "color"];
    for (const sel of ["#device-name-input", "#send-from-name", "#send-note"]) {
      const st = window.getComputedStyle($(sel));
      for (const prop of props) {
        assert.strictEqual(st[prop], ref[prop], `${sel} 的 ${prop}=${st[prop]}，分享码框=${ref[prop]}`);
      }
    }
  });
  check("字号是 14px（不是浏览器默认的 13.33px）", () => {
    for (const sel of ["#device-name-input", "#send-from-name", "#send-note"]) {
      const size = window.getComputedStyle($(sel)).fontSize;
      assert.strictEqual(size, "14px", `${sel} 字号=${size}`);
    }
  });
  check("设备名/附言输入框不再是分享码那种等宽大写字距", () => {
    const ref = window.getComputedStyle($("#code-input"));
    for (const sel of ["#device-name-input", "#send-note"]) {
      const st = window.getComputedStyle($(sel));
      assert.notStrictEqual(st.fontFamily, ref.fontFamily, `${sel} 仍是等宽字体`);
      assert.notStrictEqual(st.letterSpacing, ref.letterSpacing, `${sel} 仍带分享码字距`);
      assert.notStrictEqual(st.textTransform, ref.textTransform, `${sel} 仍是大写转换`);
    }
  });
  doc.querySelectorAll("#send-targets input").forEach((box) => { box.checked = true; box.dispatchEvent(new window.Event("change")); });
  check("勾两台后按钮文案更新", () => {
    assert.strictEqual($("#send-confirm").disabled, false);
    assert.ok($("#send-confirm").textContent.includes("2 台设备"), $("#send-confirm").textContent);
  });
  $("#send-confirm").click();
  await new Promise((r) => setTimeout(r, 60));
  check("发出的是 /api/transfers，带两台目标与令牌", () => {
    const call = calls.find((c) => c.url === "/api/transfers");
    assert.ok(call, "没有发出投递请求");
    assert.strictEqual(call.body.get("targets"), "dev_AAAABBBB,dev_CCCCDDDD");
    assert.strictEqual(call.body.get("ttl_seconds"), "3600");
    assert.strictEqual(call.headers["X-Device-Token"], "tok_secret_value");
    assert.strictEqual(call.body.get("from_device_id"), "dev_AAAABBBB");
    assert.strictEqual(call.body.get("file").name, "报告.pdf");
  });
  check("成功后关闭面板并提示发给了谁", () => {
    assert.ok(!visible($("#send-modal")), "面板没关");
    assert.ok($("#notice").textContent.includes("已发送给"), $("#notice").textContent);
    assert.ok($("#notice").textContent.includes("ZZ99YY88"), "提示里没有分享码");
  });

  console.log("== PWA：安装引导 / service worker ==");
  check("未安装时显示安装卡片，且给的是文字步骤（jsdom 不是 iOS）", () => {
    assert.ok(visible($("#install-card")), "安装卡片没出现");
    assert.ok($("#install-btn").classList.contains("hidden"), "没有 beforeinstallprompt 时不该显示按钮");
    assert.ok(visible($("#install-hint-os")), "应显示安装步骤提示");
    assert.ok($("#install-hint-os").textContent.includes("Chrome"), $("#install-hint-os").textContent);
  });
  let prompted = false;
  check("收到 beforeinstallprompt 后出现安装按钮，点了会调 prompt()", () => {
    const event = new window.Event("beforeinstallprompt");
    event.preventDefault = () => {};
    event.prompt = () => { prompted = true; };
    event.userChoice = Promise.resolve({ outcome: "accepted" });
    window.dispatchEvent(event);
    assert.ok(visible($("#install-btn")), "安装按钮没出现");
    assert.ok($("#install-hint-os").classList.contains("hidden"), "有按钮时不该再显示步骤");
    $("#install-btn").click();
    assert.ok(prompted, "没有调用 prompt()");
  });
  check("「下载APK」按钮：指向 Release 里的 APK，图标是 sprite 里的手绘 SVG（不是 emoji/位图）", () => {
    const apk = $("#apk-download-btn");
    assert.ok(apk, "按钮不存在");
    assert.strictEqual(apk.tagName, "A", "应该是个能直接下载的链接");
    assert.strictEqual(
      apk.getAttribute("href"),
      "https://github.com/alexliu07/sharelink/releases/download/android-latest/ShareLink.apk",
      `href=${apk.getAttribute("href")}`);
    assert.ok(visible(apk), "按钮被隐藏");
    const use = apk.querySelector("use");
    assert.ok(use, "没有 <use> 图标");
    assert.strictEqual(use.getAttribute("href"), "#icon-apk", `图标引用 ${use.getAttribute("href")}`);
    assert.ok(apk.textContent.includes("下载APK"), `文案是「${apk.textContent.trim()}」`);
  });
  check("装成 PWA 后（standalone）：收起安装引导，但「下载APK」依然可见", () => {
    // standalone 是靠 matchMedia("(display-mode: standalone)") 判的；jsdom 里把它桩成 true 再走一遍 appinstalled
    window.matchMedia = (query) => ({ matches: query.includes("standalone"), media: query,
      addEventListener() {}, removeEventListener() {} });
    window.dispatchEvent(new window.Event("appinstalled"));
    assert.ok(!visible($("#install-main")), "安装引导没收起");
    assert.ok(!visible($("#install-btn")), "安装按钮还在");
    assert.ok(!visible($("#install-hint-os")), "安装步骤提示还在");
    assert.ok(visible($("#install-card")), "整个安装卡片被藏了");
    assert.ok(visible($("#apk-download-btn")), "「下载APK」跟着一起消失了");
    assert.ok($("#notice").textContent.includes("已装到设备上"), $("#notice").textContent);
  });
  check("页面加载后注册了 service worker（相对路径 + 作用域）", () => {
    window.dispatchEvent(new window.Event("load"));
    const call = swCalls.at(-1);
    assert.ok(call, "没有调用 register()");
    assert.strictEqual(call.url, "./sw.js");
    assert.strictEqual(call.options.scope, "./");
  });

  console.log("== 设备令牌备份 / 恢复 ==");
  const blobUrls = [];
  const downloaded = [];
  let exportedText = "";
  window.URL.createObjectURL = (blob) => { blobUrls.push(blob); blob.text().then((text) => { exportedText = text; }); return "blob:stub"; };
  window.URL.revokeObjectURL = () => {};
  window.HTMLAnchorElement.prototype.click = function () { downloaded.push({ href: this.href, download: this.download }); };

  $("#device-export-btn").click();
  await new Promise((r) => setTimeout(r, 20));
  check("导出令牌：文件名带设备名与 id，内容是可恢复的 JSON", () => {
    const file = downloaded.at(-1);
    assert.ok(file, "没有触发下载");
    assert.ok(file.download.startsWith("sharelink-") && file.download.endsWith(".json"), file.download);
    assert.ok(file.download.includes("dev_AAAABBBB"), file.download);
    const payload = JSON.parse(exportedText);
    assert.strictEqual(payload.id, "dev_AAAABBBB");
    assert.strictEqual(payload.token, "tok_secret_value");
  });

  const importWith = async (payload) => {
    const upload = new window.File([JSON.stringify(payload)], "device.json", { type: "application/json" });
    Object.defineProperty($("#device-import-file"), "files", { value: [upload], configurable: true });
    $("#device-import-file").dispatchEvent(new window.Event("change"));
    await new Promise((r) => setTimeout(r, 30));
  };

  await importWith({ id: "dev_ZZZZ9999", token: "token-wrong" });
  check("导入错误令牌：拒绝且不覆盖当前设备", () => {
    assert.ok($("#notice").textContent.includes("导入失败"), $("#notice").textContent);
    assert.strictEqual(JSON.parse(window.localStorage.getItem("sharelink.device")).id, "dev_AAAABBBB");
  });

  await importWith({ id: "dev_ZZZZ9999", token: "tok_restored" });
  check("导入正确令牌：先校验收件箱再落盘，并更新界面", () => {
    const saved = JSON.parse(window.localStorage.getItem("sharelink.device"));
    assert.strictEqual(saved.id, "dev_ZZZZ9999");
    assert.strictEqual(saved.token, "tok_restored");
    assert.strictEqual($("#self-name").textContent, "旧手机");
    assert.ok($("#notice").textContent.includes("已恢复设备"), $("#notice").textContent);
    assert.strictEqual($("#inbox-badge").textContent, "1");
  });
  const junk = new window.File(["这不是 JSON"], "x.json", { type: "application/json" });
  Object.defineProperty($("#device-import-file"), "files", { value: [junk], configurable: true });
  $("#device-import-file").dispatchEvent(new window.Event("change"));
  await new Promise((r) => setTimeout(r, 30));
  check("导入垃圾文件：报错而不是静默失败", () => {
    assert.ok($("#notice").textContent.includes("导入失败"), $("#notice").textContent);
  });

  console.log("== 令牌：复制 / 粘贴 / 读剪贴板（不依赖文件那条路） ==");
  const clipboard = { written: "", read: "" };
  Object.defineProperty(window.navigator, "clipboard", {
    value: {
      writeText: async (text) => { clipboard.written = text; },
      readText: async () => clipboard.read,
    },
    configurable: true,
  });

  // 先把设备换成"原设备"，验证复制出来的就是它的令牌
  $("#device-import-text").value = JSON.stringify({ id: "dev_AAAABBBB", token: "tok_secret_value" });
  $("#device-import-apply-btn").click();
  await new Promise((r) => setTimeout(r, 40));
  check("粘贴导入：校验收件箱后落盘并刷新界面", () => {
    const saved = JSON.parse(window.localStorage.getItem("sharelink.device"));
    assert.strictEqual(saved.id, "dev_AAAABBBB");
    assert.strictEqual(saved.token, "tok_secret_value");
    assert.ok($("#notice").textContent.includes("已恢复设备"), $("#notice").textContent);
    assert.strictEqual($("#device-import-text").value, "", "导入后应清空粘贴框");
  });

  $("#device-import-text").value = JSON.stringify({ id: "dev_ZZZZ9999", token: "token-wrong" });
  $("#device-import-apply-btn").click();
  await new Promise((r) => setTimeout(r, 40));
  check("粘贴导入错误令牌：拒绝且不覆盖当前设备", () => {
    assert.ok($("#notice").textContent.includes("导入失败"), $("#notice").textContent);
    assert.strictEqual(JSON.parse(window.localStorage.getItem("sharelink.device")).id, "dev_AAAABBBB");
  });

  $("#device-import-text").value = "随便写点非 JSON";
  $("#device-import-apply-btn").click();
  await new Promise((r) => setTimeout(r, 20));
  check("粘贴非 JSON：明确报错", () => {
    assert.ok($("#notice").textContent.includes("不是合法的 JSON"), $("#notice").textContent);
  });

  check("令牌框里直接就摆着可复制的 JSON（不用先导出文件）", () => {
    const payload = JSON.parse($("#device-token-text").value);
    assert.strictEqual(payload.sharelink_device, 1);
    assert.strictEqual(payload.id, "dev_AAAABBBB");
    assert.strictEqual(payload.token, "tok_secret_value");
  });

  $("#device-copy-btn").click();
  await new Promise((r) => setTimeout(r, 20));
  check("复制令牌：写进剪贴板的就是可恢复的 JSON", () => {
    const payload = JSON.parse(clipboard.written);
    assert.strictEqual(payload.id, "dev_AAAABBBB");
    assert.strictEqual(payload.token, "tok_secret_value");
    assert.ok($("#notice").textContent.includes("已复制到剪贴板"), $("#notice").textContent);
  });

  clipboard.read = JSON.stringify({ id: "dev_ZZZZ9999", token: "tok_restored" });
  $("#device-import-clip-btn").click();
  await new Promise((r) => setTimeout(r, 40));
  check("读取剪贴板导入：认领同一台设备", () => {
    const saved = JSON.parse(window.localStorage.getItem("sharelink.device"));
    assert.strictEqual(saved.id, "dev_ZZZZ9999");
    assert.strictEqual(saved.name, "旧手机");
  });

  clipboard.read = "   ";
  $("#device-import-clip-btn").click();
  await new Promise((r) => setTimeout(r, 20));
  check("剪贴板为空：给出可操作的提示", () => {
    assert.ok($("#notice").textContent.includes("剪贴板"), $("#notice").textContent);
  });

  window.close();
  console.log(`\n结果：${passed} 项通过${process.exitCode ? "，有失败" : "，全部通过"}`);
})();