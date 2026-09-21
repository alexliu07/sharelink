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
  count: 2, unread: 1,
  items: [{
    code: "AB3D7K9M", filename: "周会材料.pdf", size: 1536, sha256: "x", content_type: "application/pdf",
    created_at: nowIso, expires_at: evtIso, seconds_left: 600, download_count: 0,
    transfer_id: 1, from_device_id: "dev_CCCCDDDD", from_name: "室友的 iPad", note: "看完删掉",
    sent_at: nowIso, seen: false,
    share_url: "http://localhost/share/?code=AB3D7K9M",
    download_url: "http://localhost/share/api/download/AB3D7K9M",
  }, {
    code: "TXT12345", filename: "会议要点.txt", size: 42, sha256: "y",
    content_type: "text/plain; charset=utf-8",
    created_at: nowIso, expires_at: evtIso, seconds_left: 600, download_count: 0,
    transfer_id: 2, from_device_id: "dev_CCCCDDDD", from_name: "室友的 iPad", note: "",
    sent_at: nowIso, seen: false, is_text: true,
    share_url: "http://localhost/share/?code=TXT12345",
    download_url: "http://localhost/share/api/download/TXT12345",
  }],
};
const GROUP_ID = "grp_7KQ2M4XZ9B3D";
const GROUPS = { count: 1, groups: [{
  id: GROUP_ID, name: "家里的设备", owner_device_id: "dev_AAAABBBB", created_at: nowIso,
  owner_name: "我的笔记本", member_count: 2, my_role: "owner", is_owner: true }] };
const GROUP_DETAIL = { group: GROUPS.groups[0], members: [
  { id: "dev_AAAABBBB", name: "我的笔记本", role: "owner", joined_at: nowIso, last_seen_at: nowIso, idle_seconds: 12, is_self: true },
  { id: "dev_CCCCDDDD", name: "室友的 iPad", role: "member", joined_at: nowIso, last_seen_at: nowIso, idle_seconds: 3600, is_self: false },
] };
// 设备列表：服务端只返回"自己 + 同组设备"，并带共同组标签
const SCOPED_DEVICES = { count: 2, self_id: "dev_AAAABBBB", scope: "groups", devices: [
  { ...DEVICES[0], is_self: true, shared_groups: [] },
  { ...DEVICES[1], is_self: false, shared_groups: [{ id: GROUP_ID, name: "家里的设备" }] },
] };

const routes = {
  "GET /api/stats": [200, { shares: 3, shares_bytes: 2048, devices: 2, transfers: 1, disk_files: 3, disk_bytes: 2048,
    max_upload_mb: 200, max_text_chars: 10000, default_ttl_seconds: 3600, min_ttl_seconds: 60, max_ttl_seconds: 2592000,
    cleanup_interval_seconds: 60, device_idle_days: 30, max_targets_per_send: 20 }],
  "GET /api/devices": [200, { count: 0, devices: [], scope: "unregistered" }],       // 不带令牌不再列名单
  "GET /api/devices?device_id=dev_AAAABBBB": [200, SCOPED_DEVICES],
  "GET /api/groups?device_id=dev_AAAABBBB": [200, GROUPS],
  [`GET /api/groups/${GROUP_ID}?device_id=dev_AAAABBBB`]: [200, GROUP_DETAIL],
  "POST /api/groups": [201, { group: GROUPS.groups[0], share_hint: "把组 id 发给别人" }],
  [`POST /api/groups/${GROUP_ID}/join`]: [200, { joined: false, already_member: true, group: GROUPS.groups[0] }],
  [`POST /api/groups/${GROUP_ID}/leave`]: [200, { left: true, group_id: GROUP_ID, name: "家里的设备" }],
  [`DELETE /api/groups/${GROUP_ID}?device_id=dev_AAAABBBB`]: [200, { dissolved: true, group_id: GROUP_ID, name: "家里的设备" }],
  [`DELETE /api/groups/${GROUP_ID}/members/dev_CCCCDDDD?device_id=dev_AAAABBBB`]: [200, { removed: true }],
  [`PATCH /api/groups/${GROUP_ID}`]: [200, { group: { ...GROUPS.groups[0], name: "新组名" } }],
  "POST /api/groups/grp_NOSUCHGROUP99/join": [404, { detail: { error: "group_not_found",
    message: "设备组不存在：组 id 可能抄错了，也可能已经被管理员解散" } }],
  "POST /api/devices": [201, { id: "dev_AAAABBBB", name: "我的笔记本", created_at: nowIso, last_seen_at: nowIso,
    idle_seconds: 0, inbox_count: 0, token: "tok_secret_value" }],
  "GET /api/devices/dev_AAAABBBB/inbox": [200, INBOX],
  "POST /api/texts": [201, { code: "TX99TX99", filename: "第一行标题.txt", size: 42, chars: 12, sha256: "y",
    content_type: "text/plain; charset=utf-8", created_at: nowIso, expires_at: evtIso, seconds_left: 600,
    expired: false, download_count: 0, is_text: true, ttl_seconds: 600, ttl_human: "10 分钟",
    share_url: "http://localhost/share/?code=TX99TX99", download_url: "http://localhost/share/api/download/TX99TX99",
    from_name: "我的笔记本", targets: [{ id: "dev_AAAABBBB", name: "我的笔记本" }], transfer_count: 1 }],
  "GET /api/download/TXT12345": [200, ""],        // 文本下载：正文走 TEXT_ROUTES 的 .text()
  "GET /api/files/TXT12345": [200, { code: "TXT12345", filename: "取件文本.txt", size: 42, sha256: "z",
    content_type: "text/plain; charset=utf-8", created_at: nowIso, expires_at: evtIso, seconds_left: 600,
    expired: false, download_count: 0, is_text: true }],
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
// 文本下载：fetch 的 .text() 走这张表（其它路径仍返回 JSON 串）
const TEXT_ROUTES = { "/api/download/TXT12345": "取件文本第一行\n第二行：中文与 ASCII 混排" };
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
  return { ok: status < 400, status, json: async () => body,
    text: async () => (url in TEXT_ROUTES ? TEXT_ROUTES[url] : JSON.stringify(body)) };
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
// jsdom 不实现 confirm / prompt（真实浏览器都有）：组操作里的确认框一律当"点确定"
window.confirm = () => true;
window.prompt = (_message, defaultValue) => `新组名-${defaultValue ? "改" : "新"}`;
// jsdom 不实现 scrollIntoView（真实浏览器都有）
window.Element.prototype.scrollIntoView = function scrollIntoView() {};
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

  console.log("== 设备组入口（不再有单独的「添加本设备」步骤）==");
  check("设备区没有单独的「添加本设备」步骤：只剩创建/加入两颗按钮", () => {
    assert.ok(!$("#device-add-btn"), "还有旧的「添加本设备」按钮");
    assert.ok(!$("#device-name-input"), "还有旧的设备名输入框");
    const create = $("#group-create-btn");
    const join = $("#group-join-btn");
    assert.ok(create && join, "缺少建组/加入按钮");
    assert.ok(create.closest("#panel-devices") && join.closest("#panel-devices"), "按钮不在设备面板里");
    assert.ok(visible(create) && visible(join), "按钮被隐藏");
    assert.ok(create.textContent.includes("创建设备组"), `建组文案是「${create.textContent.trim()}」`);
    assert.ok(join.textContent.includes("加入设备组"), `加入文案是「${join.textContent.trim()}」`);
    assert.strictEqual(create.disabled, false, "未登记时建组按钮被禁用");
    assert.strictEqual(join.disabled, false, "未登记时加入按钮被禁用");
  });
  check('未登记时显示登记表单、隐藏「本设备」卡片', () => {
    assert.ok(visible($("#device-setup")));
    assert.ok(!visible($("#device-self")));
  });

  check("未登记时：表单照样能用，并提示会自动登记本设备", () => {
    assert.ok(visible($("#group-need-device")), "没有「点按钮会自动登记」的提示");
    assert.ok($("#group-need-device").textContent.includes("自动"), $("#group-need-device").textContent);
    assert.ok(visible($("#group-create-row")), "未登记时建组表单被收起");
    assert.ok(visible($("#group-join-row")), "未登记时加入表单被收起");
    assert.ok(visible($("#device-setup")), "未登记时提示区不见了");
  });
  check("未登记时点「发送至设备」被拦住并切到设备页（投递必须实名）", () => {
    $("#tab-upload").click();
    const input = $("#text-input");                 // 空文件时发送按钮是禁用的，先用文本路径把按钮点亮
    input.value = "想直接发给设备的一段话";
    input.dispatchEvent(new window.Event("input"));
    $("#text-send-btn").click();
    assert.ok(!visible($("#send-modal")), "未登记却打开了发送面板");
    assert.ok($("#notice").textContent.includes("设备组"), `提示没提设备组：${$("#notice").textContent}`);
    assert.ok($("#notice").textContent.includes("分享码"), `提示没给分享码这条退路：${$("#notice").textContent}`);
    assert.ok($("#panel-devices").classList.contains("active"), "没有切到设备页");
    input.value = "";                               // 收拾干净，别影响后面的文本用例
    input.dispatchEvent(new window.Event("input"));
  });

  calls.length = 0;
  $("#group-name-input").value = "家里的设备";
  $("#group-create-btn").click();
  await new Promise((r) => setTimeout(r, 60));
  check("点「创建设备组」：先自动登记本设备，再建组（本设备自动进组）", () => {
    const saved = JSON.parse(window.localStorage.getItem("sharelink.device"));
    assert.strictEqual(saved.token, "tok_secret_value");
    assert.strictEqual(saved.id, "dev_AAAABBBB");
    const paths = calls.map((c) => `${c.method} ${c.url}`);
    assert.ok(paths.includes("POST /api/devices"), `没有先登记：${paths.join(" | ")}`);
    const registerAt = paths.indexOf("POST /api/devices");
    const createAt = paths.findIndex((p) => p.startsWith("POST /api/groups"));
    assert.ok(createAt > registerAt, `顺序不对：${paths.join(" | ")}`);
    assert.ok(!visible($("#device-setup")), "登记后提示区还在");
    assert.ok(visible($("#device-self")), "登记后本设备卡片没出现");
  });
  check("本设备名称/收件箱渲染 + 打开面板自动标已读", () => {
    assert.strictEqual($("#self-name").textContent, "我的笔记本");
    assert.ok($("#inbox-list").textContent.includes("周会材料.pdf"), "收件箱没渲染文件");
    assert.ok($("#inbox-list").textContent.includes("来自 室友的 iPad"));
    assert.ok($("#inbox-list").textContent.includes("剩余"), "没有剩余时间");
    assert.ok($("#inbox-count").textContent.includes("共 2 个"), $("#inbox-count").textContent);
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

  console.log("== 收件箱「刷新」按钮 ==");
  check("刷新按钮在本设备卡片里、文案/图标齐全且是手绘描边", () => {
    const btn = $("#inbox-refresh");
    assert.ok(btn, "按钮不存在");
    assert.ok(btn.closest("#device-self"), "刷新按钮不在收件箱所在的本设备卡片里");
    assert.strictEqual(btn.textContent.trim(), "刷新", `文案是「${btn.textContent.trim()}」`);
    const use = btn.querySelector("svg.icon use");
    assert.ok(use, "按钮里没有 svg 图标");
    assert.strictEqual(use.getAttribute("href"), "#icon-refresh");
    assert.ok(doc.querySelector("symbol#icon-refresh"), "sprite 里没有 #icon-refresh");
    assert.ok(doc.querySelectorAll("symbol#icon-refresh path").length >= 2, "图标里 path 少于 2 条");
    assert.ok(/\p{Extended_Pictographic}/u.test("🔄") && !/\p{Extended_Pictographic}/u.test(btn.textContent), "按钮文案里混进了 emoji");
  });
  check("收件箱小标题与刷新按钮是同一行（.section-head 是 flex 两端对齐）", () => {
    const head = doc.querySelector("#device-self .section-head");
    assert.ok(head, "没有 .section-head");
    assert.strictEqual(window.getComputedStyle(head).display, "flex", "CSS 上不是 flex（可能忘了写样式）");
    assert.ok(head.contains($("#inbox-refresh")), "刷新按钮不在这一行里");
    assert.ok(head.querySelector(".section-title"), "这一行里没有小标题");
    assert.ok(head.contains($("#inbox-count")), "计数没有跟着标题走");
  });

  const inboxHits = () => calls.filter((c) => c.url.endsWith("/inbox")).length;
  const inboxBefore = inboxHits();
  $("#inbox-refresh").click();
  check("点刷新后立刻进入忙碌态（禁用 + aria-busy + 计数行提示）", () => {
    assert.ok($("#inbox-refresh").disabled, "按钮没禁用");
    assert.strictEqual($("#inbox-refresh").getAttribute("aria-busy"), "true");
    assert.strictEqual($("#inbox-count").textContent, "刷新中…", $("#inbox-count").textContent);
  });
  await new Promise((r) => setTimeout(r, 30));
  check("刷新真的重新请求了收件箱，并恢复按钮状态与计数", () => {
    assert.ok(inboxHits() > inboxBefore, `没有重新请求收件箱（${inboxBefore} → ${inboxHits()}）`);
    assert.strictEqual($("#inbox-refresh").getAttribute("aria-busy"), null, "aria-busy 没清掉");
    assert.ok(!$("#inbox-refresh").disabled, "按钮还禁用着");
    assert.ok($("#inbox-count").textContent.includes("共 2 个"), $("#inbox-count").textContent);
  });

  console.log("== 设备组 ==");
  await new Promise((r) => setTimeout(r, 30));            // 等登记后自动拉一次设备组
  check("设备组卡片：名称 / 组 id / 成员 / 管理员标记", () => {
    const card = $("#group-list .group-card");
    assert.ok(card, "没有渲染组卡片");
    assert.ok(card.textContent.includes("家里的设备"), card.textContent);
    assert.strictEqual(card.querySelector(".group-id").textContent.trim(), "grp_7KQ2M4XZ9B3D");
    assert.ok(card.textContent.includes("我是管理员"), "没有管理员标记");
    assert.ok(card.textContent.includes("2 台设备"), card.textContent);
    assert.ok(card.textContent.includes("室友的 iPad（成员）") || card.textContent.includes("室友的 iPad"), "成员名单没显示");
    assert.ok(card.querySelector('[data-act="remove-member"]'), "管理员看不到「移除成员」");
    assert.ok(card.querySelector('[data-act="dissolve-group"]'), "管理员没有「解散」");
    assert.ok(!card.querySelector('[data-act="leave-group"]'), "管理员不该有「退出」");
    assert.ok($("#group-count").textContent.includes("共 1 个"), $("#group-count").textContent);
  });
  check("「复制组 id」是手绘 SVG 按钮", () => {
    const btn = $("#group-list [data-act='copy-group']");
    assert.ok(btn.querySelector("svg use"), "没有图标");
    assert.ok(btn.closest("#panel-devices"), "不在设备面板里");
  });
  check("设备列表：只列同组设备 + 标出共同组", () => {
    const rows = [...$("#device-list").querySelectorAll("li")];
    assert.strictEqual(rows.length, 2, `实际 ${rows.length} 行`);
    assert.ok(rows[0].textContent.includes("本设备"), rows[0].textContent);
    assert.ok(rows[1].textContent.includes("家里的设备"), "同组设备没有共同组标签");
    assert.ok(rows[1].textContent.includes("dev_CCCCDDDD"), rows[1].textContent);
    assert.ok(!visible($("#device-no-group")), "有同组设备时不该提示「还没加入设备组」");
    const last = calls.map((c) => c.url).filter((u) => u.startsWith("/api/devices?")).pop();
    assert.ok(last.includes("device_id=dev_AAAABBBB"), last);
    assert.ok(calls.some((c) => c.url.startsWith("/api/devices?") && c.headers && c.headers["X-Device-Token"] === "tok_secret_value"),
      "拉设备列表没带令牌");
  });

  $("#group-name-input").value = "公司设备";
  $("#group-create-btn").click();
  await new Promise((r) => setTimeout(r, 30));
  check("「创建设备组」：POST /api/groups 带 device_id 与组名，并提示组 id", () => {
    const hit = calls.filter((c) => c.url === "/api/groups" && c.method === "POST").pop();
    assert.ok(hit, "没有 POST /api/groups");
    const body = JSON.parse(hit.body);
    assert.strictEqual(body.device_id, "dev_AAAABBBB");
    assert.strictEqual(body.name, "公司设备");
    assert.strictEqual(hit.headers["X-Device-Token"], "tok_secret_value", "没带设备令牌");
    assert.ok($("#notice").textContent.includes("grp_7KQ2M4XZ9B3D"), $("#notice").textContent);
    assert.strictEqual($("#group-name-input").value, "", "创建后输入框没清空");
  });

  $("#group-join-input").value = "grp_7KQ2M4XZ9B3D";
  $("#group-join-btn").click();
  await new Promise((r) => setTimeout(r, 30));
  check("「加入设备组」：POST /join 带 device_id，提示已加入", () => {
    const hit = calls.filter((c) => c.url.includes("/join")).pop();
    assert.ok(hit, "没有 join 请求");
    assert.strictEqual(JSON.parse(hit.body).device_id, "dev_AAAABBBB");
    assert.ok($("#notice").textContent.includes("已经在") || $("#notice").textContent.includes("已加入"),
      $("#notice").textContent);
    assert.strictEqual($("#group-join-input").value, "", "加入后输入框没清空");
  });

  $("#group-join-input").value = "grp_NOSUCHGROUP99";
  $("#group-join-btn").click();
  await new Promise((r) => setTimeout(r, 30));
  check("加入不存在的组：显示服务端的中文原因", () => {
    assert.ok($("#notice").textContent.includes("设备组不存在"), $("#notice").textContent);
  });

  $("#group-list [data-act='remove-member']").click();
  await new Promise((r) => setTimeout(r, 30));
  check("管理员移除成员：DELETE 带 ?device_id= 且刷新列表", () => {
    const hit = calls.filter((c) => c.method === "DELETE" && c.url.includes("/members/")).pop();
    assert.ok(hit, "没有移除请求");
    assert.ok(hit.url.includes(`/api/groups/${GROUP_ID}/members/dev_CCCCDDDD?device_id=dev_AAAABBBB`), hit.url);
    assert.ok($("#notice").textContent.includes("已移出"), $("#notice").textContent);
  });

  $("#group-list [data-act='dissolve-group']").click();
  await new Promise((r) => setTimeout(r, 30));
  check("管理员解散组：DELETE 用查询参数传 device_id", () => {
    const hit = calls.filter((c) => c.method === "DELETE" && c.url.includes(`/api/groups/${GROUP_ID}?`)).pop();
    assert.ok(hit, "没有解散请求");
    assert.ok(hit.url.endsWith("device_id=dev_AAAABBBB"), hit.url);
    assert.ok($("#notice").textContent.includes("已解散"), $("#notice").textContent);
  });

  console.log("== 发文本 ==");
  check("文本区：空内容时两个按钮禁用，输入后启用且计数跟随", () => {
    const input = $("#text-input");
    assert.ok(input, "没有文本输入框");
    assert.ok($("#text-upload-btn").disabled && $("#text-send-btn").disabled, "空文本时按钮不该可用");
    assert.strictEqual($("#text-limit").textContent, "10000", "上限没按 /api/stats 显示");
    input.value = "第一行标题\n第二行正文";
    input.dispatchEvent(new window.Event("input"));
    assert.strictEqual($("#text-count").textContent, String(input.value.length));
    assert.ok(!$("#text-upload-btn").disabled, "有内容后「生成分享码」应可用");
    assert.ok(!$("#text-send-btn").disabled, "有内容后「发送至设备」应可用");
  });
  check("文本超过上限时拦住并标红", () => {
    const input = $("#text-input");
    input.value = "字".repeat(10001);
    input.dispatchEvent(new window.Event("input"));
    assert.ok($("#text-upload-btn").disabled, "超长还让点");
    assert.ok($("#text-count").classList.contains("over"), "计数没有标红");
  });

  const textInput = $("#text-input");
  textInput.value = "第一行标题\n第二行正文";
  textInput.dispatchEvent(new window.Event("input"));
  $("#text-upload-btn").click();
  await new Promise((r) => setTimeout(r, 40));
  check("点「生成分享码」→ POST /api/texts（带 text 与 ttl_seconds）并显示结果", () => {
    const hit = calls.filter((c) => c.url === "/api/texts" && c.method === "POST").pop();
    assert.ok(hit, "没有请求 /api/texts");
    const body = JSON.parse(hit.body);
    assert.strictEqual(body.text, "第一行标题\n第二行正文");
    assert.strictEqual(body.ttl_seconds, 3600);
    assert.ok(!body.targets, "只要分享码时不该带 targets");
    assert.strictEqual($("#code-text").textContent, "TX99TX99");
    assert.ok(!$("#r-kind-row").classList.contains("hidden"), "结果里没标出「类型：文本」");
    assert.ok($("#r-kind").textContent.includes("字符"), $("#r-kind").textContent);
  });

  $("#text-send-btn").click();
  check("「发送至设备」面板认出这次发的是文本", () => {
    assert.ok(!$("#send-modal").classList.contains("hidden"), "面板没打开");
    assert.ok($("#send-file").textContent.includes("文本"), $("#send-file").textContent);
  });
  await new Promise((r) => setTimeout(r, 40));      // 设备列表异步渲染
  const textBox = $("#send-targets input");
  textBox.checked = true;
  textBox.dispatchEvent(new window.Event("change"));
  check("勾了设备后确认按钮可用", () => assert.ok(!$("#send-confirm").disabled));
  $("#send-confirm").click();
  await new Promise((r) => setTimeout(r, 40));
  check("文本投递：POST /api/texts 带 targets，发完关面板并提示发给谁", () => {
    const hit = calls.filter((c) => c.url === "/api/texts" && c.method === "POST").pop();
    const body = JSON.parse(hit.body);
    assert.deepStrictEqual(body.targets, ["dev_AAAABBBB"], "targets 不对");
    assert.ok($("#send-modal").classList.contains("hidden"), "发送后面板没关");
    assert.ok($("#notice").textContent.includes("已把文本发给"), $("#notice").textContent);
  });

  console.log("== 取件页：文本内联显示 ==");
  $("#code-input").value = "TXT12345";
  $("#lookup-btn").click();
  await new Promise((r) => setTimeout(r, 40));
  check("凭码取到文本：直接显示内容 + 复制按钮（不用先下载）", () => {
    assert.ok(!$("#text-view").classList.contains("hidden"), "文本区没展开");
    assert.ok($("#text-body").textContent.includes("取件文本第一行"), $("#text-body").textContent);
    assert.ok($("#text-view-meta").textContent.includes("字符"), $("#text-view-meta").textContent);
    assert.ok($("#copy-text-btn").querySelector("svg.icon use"), "复制按钮没有手绘图标");
  });

  console.log("== 收件箱：文本条目「查看」 ==");
  const viewBtn = $("#inbox-list [data-view]");
  check("收件箱里文本条目才有「查看」按钮", () => {
    assert.ok(viewBtn, "文本条目没有查看按钮");
    assert.strictEqual(viewBtn.dataset.view, "TXT12345");
    assert.strictEqual(doc.querySelectorAll("#inbox-list [data-view]").length, 1, "非文本条目也出现了「查看」");
  });
  viewBtn.click();
  await new Promise((r) => setTimeout(r, 40));
  check("点「查看」就地展开文本，再点一次收起", () => {
    const pre = $("#inbox-list li .text-body");
    assert.ok(pre, "没有展开文本");
    assert.ok(pre.textContent.includes("取件文本第一行"), pre.textContent);
    $("#inbox-list [data-view]").click();
    assert.ok(!$("#inbox-list li .text-body"), "再点一次没有收起");
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
    const titles = [...doc.querySelectorAll("#send-targets .pick-group-title")].map((el) => el.textContent);
    assert.strictEqual(titles.length, 2, `分区数不对：${titles.join(" / ")}`);
    assert.ok(titles.some((t) => t.includes("家里的设备")), `没有「共同设备组」分区：${titles.join(" / ")}`);
    assert.ok(titles.some((t) => t.includes("本设备")), `没有「发给自己」分区：${titles.join(" / ")}`);
    assert.strictEqual($("#send-confirm").disabled, true, "未选设备时不该能发");
  });
  check("设备相关输入框与分享码输入框同款盒子样式（不是浏览器默认外观）", () => {
    const ref = window.getComputedStyle($("#code-input"));
    const props = ["paddingTop", "paddingLeft", "borderRadius", "borderTopWidth", "backgroundColor", "color"];
    for (const sel of ["#group-name-input", "#send-from-name", "#send-note"]) {
      const st = window.getComputedStyle($(sel));
      for (const prop of props) {
        assert.strictEqual(st[prop], ref[prop], `${sel} 的 ${prop}=${st[prop]}，分享码框=${ref[prop]}`);
      }
    }
  });
  check("字号是 14px（不是浏览器默认的 13.33px）", () => {
    for (const sel of ["#group-name-input", "#send-from-name", "#send-note"]) {
      const size = window.getComputedStyle($(sel)).fontSize;
      assert.strictEqual(size, "14px", `${sel} 字号=${size}`);
    }
  });
  check("设备名/附言输入框不再是分享码那种等宽大写字距", () => {
    const ref = window.getComputedStyle($("#code-input"));
    for (const sel of ["#group-name-input", "#send-note"]) {
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