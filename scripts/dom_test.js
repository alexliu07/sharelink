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
};
const calls = [];
const route = (method, url) => routes[`${method} ${url}`] || [404, { detail: { error: "not_found", message: `未打桩: ${method} ${url}` } }];

const dom = new JSDOM(html, { url: "http://localhost/share/", runScripts: "dangerously", pretendToBeVisual: true });
const { window } = dom;
const doc = window.document;

// 把 style.css 以 <style> 注入，getComputedStyle 才会带上级联（否则 display 判断无意义）
const style = doc.createElement("style");
style.textContent = css;
doc.head.appendChild(style);

window.fetch = async (url, options = {}) => {
  const method = (options.method || "GET").toUpperCase();
  const [status, body] = route(method, url);
  calls.push({ method, url, body: options.body, headers: options.headers });
  return { ok: status < 400, status, json: async () => body };
};
class FakeXHR {
  constructor() { this.upload = { addEventListener: (t, f) => { this.upload[t] = f; } }; }
  open(method, url) { this.method = method; this.url = url; }
  setRequestHeader(key, value) { (this.headers ||= {})[key] = value; }
  addEventListener(type, fn) { (this.events ||= {})[type] = fn; }
  send(body) {
    const [status, payload] = route(this.method, this.url);
    calls.push({ method: this.method, url: this.url, body, headers: this.headers });
    this.status = status; this.responseText = JSON.stringify(payload);
    setTimeout(() => {
      this.upload.progress?.({ lengthComputable: true, loaded: 5, total: 10 });
      this.events?.load?.();
    }, 0);
  }
}
window.XMLHttpRequest = FakeXHR;

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

  window.close();
  console.log(`\n结果：${passed} 项通过${process.exitCode ? "，有失败" : "，全部通过"}`);
})();
