/* ShareLink 前端逻辑：纯原生 JS，无外部依赖。 */
(() => {
  "use strict";

  // 与 index.html 里 app.js?v= / style.css?v= 保持一致。改动前端（app.js / style.css / index.html）
  // 必须把这个版本号 +1 并同步 index.html，否则浏览器/CDN 可能继续用旧文件
  // （CF 早期曾把 .js 按 4 小时缓存，光靠 no-cache 头救不回已经缓存过的那份）。
  // scripts/check_frontend.py 会强制三者一致。
  const ASSET_VERSION = 20;

  const $ = (id) => document.getElementById(id);

  const els = {
    stats: $("stats"),
    tabs: document.querySelectorAll(".tab"),
    panels: { upload: $("panel-upload"), fetch: $("panel-fetch"), devices: $("panel-devices") },
    dropzone: $("dropzone"),
    fileInput: $("file-input"),
    dzHint: $("dz-hint"),
    picked: $("picked"),
    pickedName: $("picked-name"),
    pickedSize: $("picked-size"),
    clearFile: $("clear-file"),
    chips: $("ttl-chips"),
    chipCustom: $("chip-custom"),
    customTtl: $("custom-ttl"),
    customValue: $("custom-value"),
    customUnit: $("custom-unit"),
    uploadBtn: $("upload-btn"),
    progress: $("progress"),
    barFill: $("bar-fill"),
    progressText: $("progress-text"),
    result: $("result"),
    codeText: $("code-text"),
    copyCode: $("copy-code"),
    shareLink: $("share-link"),
    copyLink: $("copy-link"),
    rName: $("r-name"),
    rSize: $("r-size"),
    rExpire: $("r-expire"),
    rLeft: $("r-left"),
    rSha: $("r-sha"),
    newUpload: $("new-upload"),
    revoke: $("revoke"),
    codeInput: $("code-input"),
    lookupBtn: $("lookup-btn"),
    fileCard: $("file-card"),
    fName: $("f-name"),
    fSize: $("f-size"),
    fLeft: $("f-left"),
    downloadBtn: $("download-btn"),
    notice: $("notice"),
    // 设备互传
    panelDevices: $("panel-devices"),
    tabDevices: $("tab-devices"),
    inboxBadge: $("inbox-badge"),
    deviceSelf: $("device-self"),
    installCard: $("install-card"),
    installMain: $("install-main"),
    installBtn: $("install-btn"),
    deviceCopyBtn: $("device-copy-btn"),
    deviceImportApplyBtn: $("device-import-apply-btn"),
    selfName: $("self-name"),
    selfMeta: $("self-meta"),
    deviceRenameBtn: $("device-rename-btn"),
    deviceLeaveBtn: $("device-leave-btn"),
    inboxList: $("inbox-list"),
    inboxCount: $("inbox-count"),
    inboxEmpty: $("inbox-empty"),
    inboxRefresh: $("inbox-refresh"),
    textInput: $("text-input"),
    textCount: $("text-count"),
    textLimit: $("text-limit"),
    textUploadBtn: $("text-upload-btn"),
    textSendBtn: $("text-send-btn"),
    textView: $("text-view"),
    textViewMeta: $("text-view-meta"),
    textBody: $("text-body"),
    copyTextBtn: $("copy-text-btn"),
    rKindRow: $("r-kind-row"),
    rKind: $("r-kind"),
    deviceCount: $("device-count"),
    groupList: $("group-list"),
    groupEmpty: $("group-empty"),
    groupCreateRow: $("group-create-row"),
    groupNameInput: $("group-name-input"),
    groupCreateBtn: $("group-create-btn"),
    groupJoinRow: $("group-join-row"),
    groupJoinInput: $("group-join-input"),
    groupJoinBtn: $("group-join-btn"),
    groupRefresh: $("group-refresh"),
    sendNeedDevice: $("send-need-device"),
    sendBtn: $("send-btn"),
    sendModal: $("send-modal"),
    sendClose: $("send-close"),
    sendCancel: $("send-cancel"),
    sendConfirm: $("send-confirm"),
    sendFile: $("send-file"),
    sendTargets: $("send-targets"),
    sendNoDevices: $("send-no-devices"),
    sendNote: $("send-note"),
    sendProgress: $("send-progress"),
    sendBar: $("send-bar"),
    sendPercent: $("send-percent"),
  };

  const state = { file: null, text: "", ttl: 3600, uploading: false, share: null, target: null, timer: null,
    sendMode: "file" };            // sendMode：发送面板当前发的是文件还是文本

  /* ---------------------------------------------------------- 小工具 */
  const humanSize = (bytes) => {
    if (bytes < 1024) return `${bytes} B`;
    const units = ["KB", "MB", "GB", "TB"];
    let value = bytes / 1024;
    let i = 0;
    while (value >= 1024 && i < units.length - 1) { value /= 1024; i += 1; }
    return `${value.toFixed(value >= 10 ? 0 : 1)} ${units[i]}`;
  };

  const humanLeft = (seconds) => {
    if (seconds <= 0) return "已过期";
    const d = Math.floor(seconds / 86400);
    const h = Math.floor((seconds % 86400) / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    if (d) return `${d} 天 ${h} 小时`;
    if (h) return `${h} 小时 ${m} 分`;
    if (m) return `${m} 分 ${s} 秒`;
    return `${s} 秒`;
  };

  const localTime = (iso) => {
    const dt = new Date(iso);
    const pad = (n) => String(n).padStart(2, "0");
    return `${dt.getFullYear()}-${pad(dt.getMonth() + 1)}-${pad(dt.getDate())} ` +
           `${pad(dt.getHours())}:${pad(dt.getMinutes())}:${pad(dt.getSeconds())}`;
  };

  const normalizeCode = (raw) => String(raw || "").replace(/[\s\-_.]+/g, "").toUpperCase();

  /* 设备名 / 文件名 / 附言都来自其他人，插入 innerHTML 前必须转义（防 XSS） */
  const escapeHtml = (text) => String(text ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));

  const lastSeenText = (seconds) => {
    if (seconds < 60) return "刚刚活跃";
    if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前活跃`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前活跃`;
    return `${Math.floor(seconds / 86400)} 天前活跃`;
  };

  const showNotice = (message, kind = "error") => {
    els.notice.textContent = message;
    els.notice.className = `notice ${kind}`;
  };
  const hideNotice = () => { els.notice.className = "notice hidden"; };

  const errorMessage = (payload, fallback) => {
    if (!payload) return fallback;
    const detail = payload.detail ?? payload;
    if (typeof detail === "string") return detail;
    return detail.message || detail.error || fallback;
  };

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (_) {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand("copy");
      document.body.removeChild(ta);
      return ok;
    }
  }

  // 复制成功的反馈图标（与页面 sprite 里同一套 SVG，不再用 emoji）
  const ICON_CHECK = '<svg class="icon" aria-hidden="true"><use href="#icon-check"></use></svg>';

  const flash = (button, html) => {
    const original = button.innerHTML;
    button.innerHTML = html;
    setTimeout(() => { button.innerHTML = original; }, 1200);
  };

  /* ---------------------------------------------------------- 站点统计 */
  async function loadStats() {
    try {
      const res = await fetch("/api/stats");
      if (!res.ok) throw new Error("stats failed");
      const data = await res.json();
      els.stats.innerHTML =
        `${data.shares} 个分享 · ${humanSize(data.disk_bytes)}<br>` +
        `单文件上限 ${data.max_upload_mb} MB`;
      els.dzHint.textContent = `单文件上限 ${data.max_upload_mb} MB`;
      if (data.max_text_chars) {                      // 文本上限也以服务端为准
        MAX_TEXT_CHARS = data.max_text_chars;
        els.textLimit.textContent = String(MAX_TEXT_CHARS);
        els.textInput.maxLength = MAX_TEXT_CHARS;
        updateTextState();
      }
      const maxTtl = data.max_ttl_seconds;
      document.querySelectorAll(".chip[data-ttl]").forEach((chip) => {
        if (Number(chip.dataset.ttl) > maxTtl) chip.remove();
      });
      if (state.ttl > maxTtl) state.ttl = Number(els.chips.querySelector(".chip[data-ttl]")?.dataset.ttl || 3600);
    } catch (_) {
      els.stats.textContent = "离线";
      els.dzHint.textContent = "无法读取站点信息，上传仍可尝试";
    }
  }

  /* ---------------------------------------------------------- 标签页 */
  function switchTab(name) {
    Object.entries(els.panels).forEach(([key, panel]) => {
      if (panel) panel.classList.toggle("active", key === name);
    });
    els.tabs.forEach((tab) => {
      const active = tab.dataset.panel === `panel-${name}`;
      tab.classList.toggle("active", active);
      tab.setAttribute("aria-selected", String(active));
    });
  }
  els.tabs.forEach((tab) => tab.addEventListener("click", () => switchTab(tab.dataset.panel.replace("panel-", ""))));

  /* ---------------------------------------------------------- 选文件 */
  function pickFile(file) {
    if (!file) return;
    state.file = file;
    els.pickedName.textContent = file.name;
    els.pickedSize.textContent = humanSize(file.size);
    els.picked.classList.remove("hidden");
    els.dropzone.classList.add("hidden");
    els.uploadBtn.disabled = false;
    els.sendBtn.disabled = false;
    hideNotice();
  }

  function clearFile() {
    state.file = null;
    els.fileInput.value = "";
    els.picked.classList.add("hidden");
    els.dropzone.classList.remove("hidden");
    els.uploadBtn.disabled = true;
    els.sendBtn.disabled = true;
  }

  els.dropzone.addEventListener("click", () => els.fileInput.click());
  els.dropzone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); els.fileInput.click(); }
  });
  els.fileInput.addEventListener("change", () => pickFile(els.fileInput.files[0]));
  els.clearFile.addEventListener("click", clearFile);

  ["dragenter", "dragover"].forEach((evt) =>
    els.dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      els.dropzone.classList.add("dragover");
    }));
  ["dragleave", "drop"].forEach((evt) =>
    els.dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      els.dropzone.classList.remove("dragover");
    }));
  els.dropzone.addEventListener("drop", (e) => {
    const file = e.dataTransfer?.files?.[0];
    if (file) pickFile(file);
  });
  document.addEventListener("dragover", (e) => e.preventDefault());
  document.addEventListener("drop", (e) => e.preventDefault());

  /* ---------------------------------------------------------- 有效期 */
  els.chips.addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (!chip) return;
    els.chips.querySelectorAll(".chip").forEach((c) => c.classList.toggle("active", c === chip));
    const custom = chip === els.chipCustom;
    els.customTtl.classList.toggle("hidden", !custom);
    if (custom) {
      els.customValue.focus();
      updateCustomTtl();
    } else {
      state.ttl = Number(chip.dataset.ttl);
    }
  });

  function updateCustomTtl() {
    const amount = Number(els.customValue.value || 0);
    const unit = Number(els.customUnit.value);
    if (amount > 0) state.ttl = Math.round(amount * unit);
  }
  els.customValue.addEventListener("input", updateCustomTtl);
  els.customUnit.addEventListener("change", updateCustomTtl);

  /* ---------------------------------------------------------- 上传 */
  function resetUploadView() {
    els.result.classList.add("hidden");
    els.progress.classList.add("hidden");
    els.barFill.style.width = "0%";
    els.progressText.textContent = "0%";
    els.uploadBtn.disabled = !state.file;
    els.sendBtn.disabled = !state.file;
    els.uploadBtn.textContent = "生成分享码";
    state.uploading = false;
    updateTextState();
  }

  /* ---------------------------------------------------------- 发文本 */
  let MAX_TEXT_CHARS = 10000;                       // 启动后用 /api/stats 覆盖

  function updateTextState() {
    state.text = els.textInput.value;
    els.textCount.textContent = String(state.text.length);
    els.textCount.classList.toggle("over", state.text.length >= MAX_TEXT_CHARS);
    const ready = state.text.trim().length > 0 && state.text.length <= MAX_TEXT_CHARS;
    els.textUploadBtn.disabled = !ready || state.uploading;
    els.textSendBtn.disabled = !ready;
  }

  async function postText({ targets = null } = {}) {
    const body = { text: els.textInput.value, ttl_seconds: state.ttl };
    if (targets && targets.length) {
      body.targets = targets;
      const note = els.sendNote.value.trim();
      if (note) body.note = note;
      if (myDevice) body.from_device_id = myDevice.id;      // 显示名由服务端取设备名，不再传 from_name
    }
    const headers = { "Content-Type": "application/json" };
    if (targets && myDevice) headers["X-Device-Token"] = myDevice.token;
    const res = await fetch("/api/texts", { method: "POST", headers, body: JSON.stringify(body) });
    const payload = await res.json().catch(() => null);
    if (!res.ok) throw new Error(errorMessage(payload, `发送失败（HTTP ${res.status}）`));
    return payload;
  }

  els.textInput.addEventListener("input", updateTextState);

  els.textUploadBtn.addEventListener("click", async () => {
    if (!state.text.trim()) return;
    hideNotice();
    els.textUploadBtn.disabled = true;
    els.textUploadBtn.textContent = "发送中…";
    let payload = null;
    try {
      payload = await postText();
    } catch (err) {
      showNotice(err.message);
    }
    els.textUploadBtn.textContent = "生成分享码";
    updateTextState();
    if (payload) {                     // 发送成功后再动界面：界面出错不该被当成发送失败
      showResult(payload);
      loadStats();
    }
  });
  els.textSendBtn.addEventListener("click", () => openSendDialog("text"));

  function startCountdown(span, expiresAt) {
    if (state.timer) clearInterval(state.timer);
    const end = new Date(expiresAt).getTime();
    const tick = () => {
      const left = Math.round((end - Date.now()) / 1000);
      span.textContent = humanLeft(left);
      if (left <= 0) {
        clearInterval(state.timer);
        state.timer = null;
        showNotice("该分享已过期，文件已从服务器删除。");
      }
    };
    tick();
    state.timer = setInterval(tick, 1000);
  }

  function showResult(data) {
    state.share = data;
    els.codeText.textContent = data.code;
    els.shareLink.value = data.share_url;
    els.rName.textContent = data.filename;
    els.rKindRow.classList.toggle("hidden", !data.is_text);
    if (data.is_text) els.rKind.textContent = `文本 · ${data.chars} 字符`;
    els.rSize.textContent = humanSize(data.size);
    els.rExpire.textContent = localTime(data.expires_at);
    els.rSha.textContent = data.sha256;
    els.result.classList.remove("hidden");
    startCountdown(els.rLeft, data.expires_at);
    els.result.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  els.uploadBtn.addEventListener("click", () => {
    if (!state.file || state.uploading) return;
    hideNotice();
    state.uploading = true;
    els.uploadBtn.disabled = true;
    els.uploadBtn.textContent = "上传中…";
    els.progress.classList.remove("hidden");
    els.result.classList.add("hidden");

    const form = new FormData();
    form.append("file", state.file);
    form.append("ttl_seconds", String(state.ttl));

    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/upload");
    xhr.upload.addEventListener("progress", (e) => {
      if (!e.lengthComputable) return;
      const pct = Math.round((e.loaded / e.total) * 100);
      els.barFill.style.width = `${pct}%`;
      els.progressText.textContent = `${pct}%`;
    });
    xhr.addEventListener("load", () => {
      state.uploading = false;
      els.uploadBtn.textContent = "生成分享码";
      let payload = null;
      try { payload = JSON.parse(xhr.responseText); } catch (_) { /* 非 JSON 响应 */ }
      if (xhr.status === 201 && payload) {
        els.progress.classList.add("hidden");
        clearFile();
        showResult(payload);
        loadStats();
      } else {
        els.progress.classList.add("hidden");
        els.uploadBtn.disabled = !state.file;
        showNotice(errorMessage(payload, `上传失败（HTTP ${xhr.status}）`));
      }
    });
    xhr.addEventListener("error", () => {
      state.uploading = false;
      els.progress.classList.add("hidden");
      els.uploadBtn.disabled = !state.file;
      els.uploadBtn.textContent = "生成分享码";
      showNotice("网络错误，上传中断。");
    });
    xhr.send(form);
  });

  els.copyCode.addEventListener("click", async () => {
    if (!state.share) return;
    if (await copyText(state.share.code)) flash(els.copyCode, ICON_CHECK);
  });
  els.copyLink.addEventListener("click", async () => {
    if (!state.share) return;
    if (await copyText(state.share.share_url)) flash(els.copyLink, `${ICON_CHECK} 已复制`);
  });
  els.newUpload.addEventListener("click", () => { hideNotice(); resetUploadView(); clearFile(); });
  els.revoke.addEventListener("click", async () => {
    if (!state.share) return;
    if (!confirm("确定立即删除这个分享？下载链接会马上失效。")) return;
    const res = await fetch(`/api/files/${state.share.code}`, { method: "DELETE" });
    if (res.ok) {
      showNotice("分享已删除，文件已从服务器移除。", "ok");
      hideResult();
      loadStats();
    } else {
      const payload = await res.json().catch(() => null);
      showNotice(errorMessage(payload, "删除失败"));
    }
  });

  function hideResult() {
    els.result.classList.add("hidden");
    if (state.timer) { clearInterval(state.timer); state.timer = null; }
    state.share = null;
    resetUploadView();
  }

  /* ---------------------------------------------------------- 取件 */
  async function lookup(rawCode) {
    const code = normalizeCode(rawCode ?? els.codeInput.value);
    if (!code) {
      showNotice("请输入分享码。");
      return;
    }
    els.codeInput.value = code;
    hideNotice();
    els.fileCard.classList.add("hidden");

    const res = await fetch(`/api/files/${encodeURIComponent(code)}`);
    const payload = await res.json().catch(() => null);
    if (!res.ok) {
      showNotice(errorMessage(payload, "查询失败，请稍后重试。"));
      return;
    }

    state.target = payload;
    els.fName.textContent = payload.filename;
    els.fSize.textContent = humanSize(payload.size);
    els.downloadBtn.href = `/api/download/${encodeURIComponent(payload.code)}`;
    els.downloadBtn.setAttribute("download", payload.filename);
    els.fileCard.classList.remove("hidden");
    startCountdown(els.fLeft, payload.expires_at);

    // 文本分享：把内容拉下来直接显示（不用先下载再找文件）
    els.textView.classList.add("hidden");
    els.textBody.textContent = "";
    if (payload.is_text) {
      try {
        const textRes = await fetch(`/api/download/${encodeURIComponent(payload.code)}`);
        const body = textRes.ok ? await textRes.text() : "";
        els.textBody.textContent = body;
        els.textViewMeta.textContent = `${payload.filename} · ${body.length} 字符`;
        els.textView.classList.remove("hidden");
      } catch (_) {
        showNotice("文本内容读取失败，可以点「下载」拿原文。");
      }
    }
  }

  els.copyTextBtn.addEventListener("click", async () => {
    const body = els.textBody.textContent;
    if (body && await copyText(body)) flash(els.copyTextBtn, `${ICON_CHECK} 已复制`);
  });

  els.lookupBtn.addEventListener("click", () => lookup());
  els.codeInput.addEventListener("keydown", (e) => { if (e.key === "Enter") lookup(); });
  els.codeInput.addEventListener("input", () => {
    const pos = els.codeInput.selectionStart;
    els.codeInput.value = normalizeCode(els.codeInput.value).slice(0, 12);
    els.codeInput.setSelectionRange(pos, pos);
  });

  /* ---------------------------------------------------------- 设备互传 */
  const DEVICE_KEY = "sharelink.device";   // localStorage：{id, token, name}
  let myDevice = null;                     // 本浏览器登记的设备
  let deviceCache = [];                    // 设备列表缓存（发送面板与设备页共用）
  let deviceScope = "";                    // "groups" = 只列同组设备；"unregistered" = 未登记
  let groupCache = [];                     // 我加入的设备组（含成员名单）
  let inboxTicker = null;

  const icon = (name) => `<svg class="icon" aria-hidden="true"><use href="#${name}"></use></svg>`;

  function loadMyDevice() {
    try {
      const raw = localStorage.getItem(DEVICE_KEY);
      const parsed = raw ? JSON.parse(raw) : null;
      return parsed && parsed.id && parsed.token ? parsed : null;
    } catch (_) {
      return null;   // 隐私模式 / 数据损坏：当作未登记
    }
  }

  function saveMyDevice(device) {
    myDevice = device;
    try {
      if (device) localStorage.setItem(DEVICE_KEY, JSON.stringify(device));
      else localStorage.removeItem(DEVICE_KEY);
    } catch (_) { /* 存不进去也不影响本次会话 */ }
    renderSelf();
  }

  const deviceHeaders = () => (myDevice ? { "X-Device-Token": myDevice.token } : {});

  /**
   * 服务端已经不认这台设备（被清掉了 / 令牌对不上）时，本机缓存必须跟着清：
   * 否则设备页一直显示一台服务端没有的设备，注销还注销不掉。
   */
  function forgetLocalDevice(message) {
    const had = !!myDevice;
    saveMyDevice(null);
    renderSelf();
    if (had) {
      showNotice(message || "服务端已经没有这台设备了（被清理或换过浏览器），已自动清除本机登记："
        + "点「创建设备组」或「加入设备组」重新开始，会自动把这台设备登记回去。");
    }
  }

  /** 服务端的错误码：404 device_not_found / 403 bad_device_token 都属于「本机缓存过期」。 */
  const staleDeviceCode = (payload) => {
    const detail = (payload && (payload.detail ?? payload)) || {};
    return detail.error === "device_not_found" || detail.error === "bad_device_token";
  };

  /**
   * 只有「拿本机当前设备的令牌」发出的请求，被服务端拒绝才能断定本机缓存过期。
   * 粘贴别人的令牌去试探（导入令牌流程）失败，不能把当前设备清掉。
   */
  const usesStoredDevice = (options) => {
    const header = (options.headers || {})["X-Device-Token"];
    return !!myDevice && header === myDevice.token;
  };

  async function apiJson(url, options = {}) {
    const res = await fetch(url, options);
    const payload = await res.json().catch(() => null);
    if (!res.ok) {
      const err = new Error(errorMessage(payload, `请求失败（HTTP ${res.status}）`));
      if (staleDeviceCode(payload) && usesStoredDevice(options)) {
        err.deviceStale = true;
        forgetLocalDevice();
      }
      throw err;
    }
    return payload;
  }

  function setBadge(count) {
    els.inboxBadge.textContent = String(count);
    els.inboxBadge.classList.toggle("hidden", !count);
  }

  function renderSelf() {
    const has = !!myDevice;
    els.deviceSelf.classList.toggle("hidden", !has);
    if (has) {
      els.selfName.textContent = myDevice.name;
      els.selfMeta.textContent = "";                                            // 不再显示设备 id
      // 令牌不再显示在页面上：要用就点「导出令牌」
      loadGroups();                                        // 登记/恢复后立刻显示设备组（含成员）
    } else {
      els.inboxList.innerHTML = "";
      els.inboxCount.textContent = "";
      groupCache = [];
      renderGroups();
      setBadge(0);
    }
  }

  /** 令牌的备份文本：字段名与手机 App、导入逻辑约定一致。 */
  function deviceTokenJson() {
    if (!myDevice) return "";
    return JSON.stringify(
      { sharelink_device: 1, id: myDevice.id, name: myDevice.name, token: myDevice.token },
      null, 2,
    );
  }

  /* ---------- 设备列表（只显示"自己 + 同组设备"） ---------- */
  async function loadDevices() {
    try {
      // 服务端按令牌判断"我和谁同组"：不带令牌只会得到空列表（不再对外列出设备名单）
      const url = myDevice
        ? `/api/devices?device_id=${encodeURIComponent(myDevice.id)}`
        : "/api/devices";
      const data = await apiJson(url, myDevice ? { headers: deviceHeaders() } : {});
      deviceCache = data.devices || [];
      deviceScope = data.scope || "";
      renderGroups();          // 合并后：设备信息用来补「同组设备」里的收件箱数，列表按组渲染
      return deviceCache;
    } catch (err) {
      if (err.deviceStale) return [];              // 本机登记刚被清掉，界面由 renderSelf 重画，别再报错
      showNotice(`设备组读取失败：${err.message}`);
      return [];
    }
  }

  /* ---------- 设备组 ---------- */
  function deviceUrl(path) {
    const joiner = path.includes("?") ? "&" : "?";
    return `${path}${joiner}device_id=${encodeURIComponent(myDevice.id)}`;
  }

  async function loadGroups() {
    if (!myDevice) {
      groupCache = [];
      renderGroups();
      return [];
    }
    try {
      const data = await apiJson(deviceUrl("/api/groups"), { headers: deviceHeaders() });
      groupCache = data.groups || [];
      // 成员名单只有组内可见：逐组拉一次（组数量级很小）
      await Promise.all(groupCache.map(async (group) => {
        try {
          const detail = await apiJson(deviceUrl(`/api/groups/${encodeURIComponent(group.id)}`), { headers: deviceHeaders() });
          group.members = detail.members || [];
        } catch (err) {
          group.members = null;                       // 拉不到就只显示成员数
        }
      }));
      renderGroups();
      return groupCache;
    } catch (err) {
      if (err.deviceStale) return [];              // 本机登记刚被清掉，界面由 renderSelf 重画，别再报错
      els.groupList.innerHTML = `<li class="muted">设备组读取失败：${escapeHtml(err.message)}</li>`;
      return [];
    }
  }

  function renderGroups() {
    const byId = new Map(deviceCache.map((device) => [device.id, device]));
    const others = deviceCache.filter((device) => !(myDevice && device.id === myDevice.id));
    els.deviceCount.textContent = deviceCache.length
      ? `共 ${others.length} 台其他设备 · ${groupCache.length} 个组` : "";
    els.groupEmpty.classList.toggle("hidden", groupCache.length > 0);

    els.groupList.innerHTML = groupCache.map((group) => {
      const owner = group.is_owner;
      const members = Array.isArray(group.members) ? group.members : [];
      const memberRows = members.length
        ? members.map((member) => {
            const info = byId.get(member.id);                                 // 设备信息（收件箱数）来自 /api/devices
            return `
            <li>
              <span class="picked-icon">${icon("icon-devices")}</span>
              <span class="meta">
                <span class="device-line">
                  <strong>${escapeHtml(member.name)}</strong>
                  ${member.is_self ? '<span class="tag self">本设备</span>' : ""}
                  <span class="tag">${member.role === "owner" ? "管理员" : "成员"}</span>
                </span>
                <span class="muted">${lastSeenText(member.idle_seconds)}${info ? ` · 收件箱 ${info.inbox_count} 个文件` : ""}</span>
              </span>
              ${owner && !member.is_self
                ? `<button class="icon-btn" data-act="remove-member" data-group="${escapeHtml(group.id)}"
                           data-member="${escapeHtml(member.id)}" title="移出设备组"
                           aria-label="把 ${escapeHtml(member.name)} 移出设备组">${icon("icon-close")}</button>`
                : ""}
            </li>`;
          }).join("")
        : `<li class="group-empty-members">成员 ${group.member_count} 台${group.members === null ? "（名单读取失败，点右上角刷新重试）" : ""}</li>`;
      return `<li class="group-card">
        <div class="group-card-head">
          <strong>${escapeHtml(group.name)}</strong>
          <span class="tag ${owner ? "self" : ""}">${owner ? "我是管理员" : "成员"}</span>
          <span class="muted">${group.member_count} 台设备</span>
        </div>
        <div class="group-card-head">
          <span class="group-id">${escapeHtml(group.id)}</span>
          <button class="ghost" data-act="copy-group" data-group="${escapeHtml(group.id)}"><svg class="icon" aria-hidden="true"><use href="#icon-copy"></use></svg>复制组 id</button>
        </div>
        <ul class="group-members">${memberRows}</ul>
        <div class="group-actions">
          ${owner
            ? `<button class="ghost" data-act="rename-group" data-group="${escapeHtml(group.id)}" data-name="${escapeHtml(group.name)}">改组名</button>
               <button class="danger" data-act="dissolve-group" data-group="${escapeHtml(group.id)}" data-name="${escapeHtml(group.name)}">解散设备组</button>`
            : `<button class="ghost" data-act="leave-group" data-group="${escapeHtml(group.id)}" data-name="${escapeHtml(group.name)}">退出设备组</button>`}
        </div>
      </li>`;
    }).join("");
  }

  els.groupList.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-act]");
    if (!button || !myDevice) return;
    const groupId = button.dataset.group;
    const label = button.dataset.name || groupId;
    const act = button.dataset.act;
    hideNotice();
    try {
      if (act === "copy-group") {
        showNotice((await copyText(groupId)) ? `已复制组 id：${groupId}` : `组 id：${groupId}（长按复制）`, "ok");
        return;
      }
      if (act === "rename-group") {
        const name = window.prompt("新的设备组名字：", label);
        if (name === null) return;
        await apiJson(`/api/groups/${encodeURIComponent(groupId)}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json", ...deviceHeaders() },
          body: JSON.stringify({ device_id: myDevice.id, name }),
        });
        showNotice("组名已更新。", "ok");
      } else if (act === "leave-group") {
        if (!window.confirm(`退出「${label}」？退出后就不能和组里其他设备互传了。`)) return;
        await apiJson(`/api/groups/${encodeURIComponent(groupId)}/leave`, {
          method: "POST",
          headers: { "Content-Type": "application/json", ...deviceHeaders() },
          body: JSON.stringify({ device_id: myDevice.id }),
        });
        showNotice(`已退出「${label}」。`, "ok");
      } else if (act === "dissolve-group") {
        if (!window.confirm(`解散「${label}」？组内成员关系会清空（已经收到的文件不受影响）。`)) return;
        await apiJson(deviceUrl(`/api/groups/${encodeURIComponent(groupId)}`), {
          method: "DELETE",
          headers: deviceHeaders(),
        });
        showNotice(`已解散「${label}」。`, "ok");
      } else if (act === "remove-member") {
        const memberId = button.dataset.member;
        if (!window.confirm("把这台设备移出设备组？移出后就不能互传了。")) return;
        await apiJson(deviceUrl(`/api/groups/${encodeURIComponent(groupId)}/members/${encodeURIComponent(memberId)}`), {
          method: "DELETE",
          headers: deviceHeaders(),
        });
        showNotice("已移出该设备。", "ok");
      }
      await loadGroups();
      await loadDevices();
    } catch (err) {
      showNotice(err.message);
    }
  });

  els.groupRefresh.addEventListener("click", async () => {
    els.groupRefresh.disabled = true;
    try {
      await loadGroups();
      await loadDevices();
    } finally {
      els.groupRefresh.disabled = false;
    }
  });

  els.groupCreateBtn.addEventListener("click", async () => {
    els.groupCreateBtn.disabled = true;
    hideNotice();
    try {
      const name = els.groupNameInput.value.trim();
      await ensureThisDevice();                          // 没登记就顺手登记：创建设备组的设备自动进组
      const data = await apiJson("/api/groups", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...deviceHeaders() },
        body: JSON.stringify({ device_id: myDevice.id, name }),
      });
      els.groupNameInput.value = "";
      showNotice(`已创建设备组「${data.group.name}」，本设备已在组里（你是管理员）：把组 id ${data.group.id} 发给别的设备，对方粘贴就能加入。`, "ok");
      await loadGroups();
      await loadDevices();
    } catch (err) {
      showNotice(err.message);
    } finally {
      els.groupCreateBtn.disabled = false;
    }
  });

  els.groupJoinBtn.addEventListener("click", async () => {
    const raw = els.groupJoinInput.value.trim();
    if (!raw) {
      showNotice("先粘贴对方给的组 id（形如 grp_7KQ2M4XZ9B3D）。");
      return;
    }
    els.groupJoinBtn.disabled = true;
    hideNotice();
    try {
      await ensureThisDevice();                          // 没登记就顺手登记，加入后这台设备就是成员
      const data = await apiJson(`/api/groups/${encodeURIComponent(raw)}/join`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...deviceHeaders() },
        body: JSON.stringify({ device_id: myDevice.id }),
      });
      els.groupJoinInput.value = "";
      showNotice(data.already_member
        ? `你已经在「${data.group.name}」里了。`
        : `已加入「${data.group.name}」，现在可以和组里的设备互传了。`, "ok");
      await loadGroups();
      await loadDevices();
    } catch (err) {
      showNotice(err.message);
    } finally {
      els.groupJoinBtn.disabled = false;
    }
  });

  /* ---------- 收件箱 ---------- */
  function renderInbox(data) {
    const items = data.items || [];
    els.inboxCount.textContent = items.length ? `共 ${items.length} 个 · ${data.unread} 个未读` : "";
    els.inboxEmpty.classList.toggle("hidden", items.length > 0);
    els.inboxList.innerHTML = items.map((item) => `
      <li class="inbox-item" data-code="${escapeHtml(item.code)}">
        <span class="picked-icon">${icon("icon-file")}</span>
        <span class="meta">
          <strong>${escapeHtml(item.filename)}</strong>
          <span class="muted">${humanSize(item.size)} · 来自 ${escapeHtml(item.from_name)} · 剩余
            <span data-expires="${escapeHtml(item.expires_at)}">${humanLeft(item.seconds_left)}</span></span>
          ${item.note ? `<span class="note">附言：${escapeHtml(item.note)}</span>` : ""}
        </span>
        <a class="primary inline" href="${escapeHtml(item.download_url)}" download>下载</a>
        ${item.is_text ? `<button class="ghost" data-view="${escapeHtml(item.code)}">查看</button>` : ""}
        <button class="icon-btn" data-remove="${escapeHtml(item.code)}" title="从收件箱移除"
                aria-label="从收件箱移除">${icon("icon-close")}</button>
      </li>`).join("");
    startInboxTicker();
  }

  function startInboxTicker() {
    if (inboxTicker) clearInterval(inboxTicker);
    inboxTicker = setInterval(() => {
      document.querySelectorAll("#inbox-list [data-expires]").forEach((el) => {
        const left = Math.round((new Date(el.dataset.expires).getTime() - Date.now()) / 1000);
        el.textContent = humanLeft(left);
      });
    }, 1000);
  }

  async function refreshInbox({ markSeen = false } = {}) {
    if (!myDevice) return false;
    try {
      const data = await apiJson(`/api/devices/${myDevice.id}/inbox`, { headers: deviceHeaders() });
      renderInbox(data);
      els.selfMeta.textContent = `收件箱 ${data.count} 个文件`;
      setBadge(data.unread);
      if (markSeen && data.unread) {
        await apiJson(`/api/devices/${myDevice.id}/inbox/seen`, { method: "POST", headers: deviceHeaders() });
        setBadge(0);
        els.inboxCount.textContent = `共 ${data.count} 个 · 0 个未读`;
      }
      return true;
    } catch (err) {
      if (err.deviceStale) return false;           // 本机登记刚被清掉，提示已经在 forgetLocalDevice 里给过
      showNotice(err.message);
      return false;
    }
  }

  /** 收件箱右上角「刷新」：手动重拉一次，过程中图标转圈、失败也明说（不静默） */
  async function refreshInboxByHand() {
    if (!els.inboxRefresh) return;
    els.inboxRefresh.disabled = true;
    els.inboxRefresh.setAttribute("aria-busy", "true");
    els.inboxCount.textContent = "刷新中…";
    try {
      if (!(await refreshInbox())) els.inboxCount.textContent = "刷新失败";
    } finally {
      els.inboxRefresh.disabled = false;
      els.inboxRefresh.removeAttribute("aria-busy");
    }
  }
  els.inboxRefresh?.addEventListener("click", refreshInboxByHand);

  els.inboxList.addEventListener("click", async (event) => {
    // 「查看」：就地拉下文本内容并展开（再点一次收起）
    const view = event.target.closest("[data-view]");
    if (view) {
      const item = view.closest("li");
      const shown = item.querySelector(".text-body");
      if (shown) {
        shown.remove();
        view.textContent = "查看";
        return;
      }
      view.disabled = true;
      view.textContent = "读取中…";
      try {
        const res = await fetch(`/api/download/${encodeURIComponent(view.dataset.view)}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const pre = document.createElement("pre");
        pre.className = "text-body";
        pre.textContent = await res.text();
        item.appendChild(pre);
        view.textContent = "收起";
      } catch (err) {
        showNotice(`文本读取失败：${err.message}`);
        view.textContent = "查看";
      } finally {
        view.disabled = false;
      }
      return;
    }

    const button = event.target.closest("[data-remove]");
    if (!button || !myDevice) return;
    try {
      await apiJson(`/api/devices/${myDevice.id}/inbox/${button.dataset.remove}`, {
        method: "DELETE", headers: deviceHeaders(),
      });
      showNotice("已从收件箱移除（分享码仍然有效，文件不会删除）。", "ok");
      await refreshInbox();
    } catch (err) {
      showNotice(err.message);
    }
  });

  /* ---------- 添加 / 改名 / 退出 ---------- */
  /**
   * 本设备还没登记就先登记（不传名字，交给服务端按浏览器猜），登记动作对用户不可见：
   * 点「创建设备组」/「加入设备组」时自动跑一遍，名字之后可在设备卡里改。
   */
  async function ensureThisDevice() {
    if (myDevice) return myDevice;
    const device = await apiJson("/api/devices", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    saveMyDevice({ id: device.id, token: device.token, name: device.name });
    renderSelf();                                        // 设备卡（含令牌）立刻出现
    try {
      await loadDevices();                               // 顺手把设备页/收件箱填好
      await refreshInbox({ markSeen: true });
    } catch (err) {
      // 收件箱晚点看也行，不能因此让建组/加入失败
    }
    return myDevice;
  }

  els.deviceRenameBtn.addEventListener("click", async () => {
    if (!myDevice) return;
    const name = (prompt("新的设备名称：", myDevice.name) || "").trim();
    if (!name) return;
    try {
      const updated = await apiJson(`/api/devices/${myDevice.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json", ...deviceHeaders() },
        body: JSON.stringify({ name }),
      });
      saveMyDevice({ ...myDevice, name: updated.name });
      await loadDevices();
      showNotice(`设备名已改为"${updated.name}"。`, "ok");
    } catch (err) {
      showNotice(err.message);
    }
  });

  els.deviceLeaveBtn.addEventListener("click", async () => {
    if (!myDevice) return;
    if (!confirm(`把"${myDevice.name}"从设备列表里注销？注销后别人无法再发给它，本浏览器里已收到的记录也会消失。`)) return;
    try {
      await apiJson(`/api/devices/${myDevice.id}`, { method: "DELETE", headers: deviceHeaders() });
      saveMyDevice(null);
      await loadDevices();
      showNotice("本设备已从设备列表注销。", "ok");
    } catch (err) {
      if (err.deviceStale) {
        // 上面已经自动清掉本机登记了，这里只补一句说明
        showNotice(`${err.message}已清除本机登记。`);
      } else if (confirm(`${err.message}\n\n服务端联系不上，只清除本机登记吗？（清除后可以重新「创建设备组」或「加入设备组」）`)) {
        forgetLocalDevice("已清除本机登记。");
      } else {
        showNotice(err.message);
      }
    }
  });

  /* ---------- 发送至设备 ---------- */
  function openSendDialog(mode = "file") {
    if (!myDevice) {
      // 投递（发文件或文本给设备）必须实名：先把本设备加进设备列表
      showNotice('投递给设备要先建一个设备组、或用对方给的组 id 加入（在「设备」页点「创建设备组」/「加入设备组」，会自动登记本设备）。只想把文件给对方就用"生成分享码"，让对方凭码取件。');
      switchTab("devices");
      return;
    }
    if (mode === "text" && !state.text.trim()) {
      showNotice('先在「上传文件」里写一段文本。');
      switchTab("upload");
      return;
    }
    if (mode === "file" && !state.file) {
      showNotice('请先在「上传文件」里选择文件。');
      switchTab("upload");
      return;
    }
    state.sendMode = mode;
    els.sendFile.textContent = mode === "text"
      ? `将发送：文本（${state.text.length} 字符）· 有效期 ${humanLeft(state.ttl)}`
      : `将发送：${state.file.name}（${humanSize(state.file.size)}）· 有效期 ${humanLeft(state.ttl)}`;
    els.sendModal.classList.remove("hidden");
    loadDevices().then(renderSendTargets);
    loadGroups();
  }

  function closeSendDialog() {
    els.sendModal.classList.add("hidden");
    els.sendProgress.classList.add("hidden");
    els.sendBar.style.width = "0%";
    els.sendPercent.textContent = "0%";
  }

  function renderSendTargets() {
    const others = deviceCache.filter((device) => !(myDevice && device.id === myDevice.id));
    els.sendNoDevices.classList.toggle("hidden", others.length > 0);
    els.sendNeedDevice.classList.toggle("hidden", !!myDevice);

    // 按"我和它共同的设备组"分区：同一组里的设备排在一起，一眼看出哪些能发
    const buckets = new Map();
    deviceCache.forEach((device) => {
      const mine = myDevice && device.id === myDevice.id;
      const names = (device.shared_groups || []).map((group) => group.name);
      const key = mine ? "本设备（发给自己）" : (names.length ? names.join(" · ") : "不在同一个设备组");
      if (!buckets.has(key)) buckets.set(key, []);
      buckets.get(key).push({ device, mine });
    });

    els.sendTargets.innerHTML = [...buckets.entries()].map(([title, items]) => `
      <li class="pick-group-title">${escapeHtml(title)}</li>
      ${items.map(({ device, mine }) => `
        <li><label class="pick">
          <input type="checkbox" value="${escapeHtml(device.id)}">
          <span class="pick-body">
            <strong>${escapeHtml(device.name)}${mine ? "（本设备）" : ""}</strong>
            <span class="muted">${lastSeenText(device.idle_seconds)} · 收件箱 ${device.inbox_count} 个文件</span>
          </span>
        </label></li>`).join("")}`).join("");
    els.sendTargets.querySelectorAll("input").forEach((box) => box.addEventListener("change", updateSendCount));
    updateSendCount();
  }

  const selectedTargets = () => [...els.sendTargets.querySelectorAll("input:checked")].map((box) => box.value);

  function updateSendCount() {
    const count = selectedTargets().length;
    els.sendConfirm.disabled = count === 0;
    els.sendConfirm.textContent = count ? `发送给 ${count} 台设备` : "发送";
  }

  els.sendBtn.addEventListener("click", openSendDialog);
  els.sendClose.addEventListener("click", closeSendDialog);
  els.sendCancel.addEventListener("click", closeSendDialog);
  els.sendModal.addEventListener("click", (event) => { if (event.target === els.sendModal) closeSendDialog(); });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !els.sendModal.classList.contains("hidden")) closeSendDialog();
  });

  els.sendConfirm.addEventListener("click", async () => {
    const targets = selectedTargets();
    if (!targets.length) return;

    if (state.sendMode === "text") {          // 文本：走 /api/texts，不发 multipart
      els.sendConfirm.disabled = true;
      els.sendConfirm.textContent = "发送中…";
      els.sendProgress.classList.remove("hidden");
      let payload = null;
      try {
        payload = await postText({ targets });
      } catch (err) {
        closeSendDialog();
        showNotice(err.message);
        return;
      }
      closeSendDialog();
      els.sendNote.value = "";
      const names = payload.targets.map((t) => t.name).join("、");
      showNotice(`已把文本发给 ${names}（分享码 ${payload.code}，${payload.ttl_human}后到期）`, "ok");
      showResult(payload);
      loadStats();
      await loadDevices();
      if (myDevice) await refreshInbox({ markSeen: true });
      return;
    }
    if (!state.file) return;

    const form = new FormData();
    form.append("file", state.file);
    form.append("targets", targets.join(","));
    form.append("ttl_seconds", String(state.ttl));
    const note = els.sendNote.value.trim();
    if (note) form.append("note", note);
    if (myDevice) form.append("from_device_id", myDevice.id);
    // 不再传 from_name：接收端显示的发送者就是本设备登记的设备名

    els.sendConfirm.disabled = true;
    els.sendConfirm.textContent = "发送中…";
    els.sendProgress.classList.remove("hidden");

    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/transfers");
    if (myDevice) xhr.setRequestHeader("X-Device-Token", myDevice.token);
    xhr.upload.addEventListener("progress", (event) => {
      if (!event.lengthComputable) return;
      const pct = Math.round((event.loaded / event.total) * 100);
      els.sendBar.style.width = `${pct}%`;
      els.sendPercent.textContent = `${pct}%`;
    });
    xhr.addEventListener("load", async () => {
      let payload = null;
      try { payload = JSON.parse(xhr.responseText); } catch (_) { /* 非 JSON */ }
      if (xhr.status === 201 && payload) {
        closeSendDialog();
        els.sendNote.value = "";
        clearFile();
        const names = payload.targets.map((t) => t.name).join("、");
        showNotice(`已发送给 ${names}：${payload.filename}（分享码 ${payload.code}，${payload.ttl_human}后到期）`, "ok");
        await loadDevices();
        await refreshInbox({ markSeen: true });
      } else {
        els.sendProgress.classList.add("hidden");
        updateSendCount();
        showNotice(errorMessage(payload, `发送失败（HTTP ${xhr.status}）`));
      }
    });
    xhr.addEventListener("error", () => {
      els.sendProgress.classList.add("hidden");
      updateSendCount();
      showNotice("网络错误，发送中断。");
    });
    xhr.send(form);
  });

  els.tabDevices.addEventListener("click", () => {
    loadDevices();
    loadGroups();
    if (myDevice) refreshInbox({ markSeen: true });
  });

  /* 开着页面时每隔 20 秒刷新一次：设备标签页开着就顺手标已读 */
  setInterval(() => {
    if (!myDevice || document.visibilityState !== "visible") return;
    refreshInbox({ markSeen: els.panelDevices.classList.contains("active") });
  }, 20000);

  /* ---------- 装成应用（PWA）引导 ---------- */
  let installPrompt = null;

  const isStandalone = () => {
    const mode = window.matchMedia?.("(display-mode: standalone)");   // 极老的浏览器可能没有 matchMedia
    return Boolean(mode?.matches) || window.navigator.standalone === true;
  };
  function renderInstallCard() {
    // 卡片本身一直显示：里面还有「下载APK」，装成 PWA 之后也是有用的（换设备 / 给别人的手机装）
    els.installCard.classList.remove("hidden");
    const standalone = isStandalone();
    const canPrompt = !standalone && Boolean(installPrompt);
    // 只有真的能一键安装时才显示这段引导：没有按钮就没有可操作的步骤，不留纯文字说明
    els.installMain.classList.toggle("hidden", standalone);   // 装过了才收起这段引导
    els.installBtn.classList.toggle("hidden", !canPrompt);     // 浏览器不给一键安装时只藏按钮
  }

  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();          // 拦下浏览器的默认小条，等用户点我们的按钮
    installPrompt = event;
    renderInstallCard();
  });
  window.addEventListener("appinstalled", () => {
    installPrompt = null;
    renderInstallCard();             // 收起安装引导，但卡片里的「下载APK」留着
    showNotice("已装到设备上，以后从桌面图标直接进。", "ok");
  });
  els.installBtn.addEventListener("click", async () => {
    if (!installPrompt) return;
    installPrompt.prompt();
    try { await installPrompt.userChoice; } catch (_) { /* 用户直接划掉也算选择 */ }
    installPrompt = null;
    renderInstallCard();
  });
  renderInstallCard();

  /* ---------- 设备令牌备份 / 恢复 ----------
     令牌是本设备的凭证，页面上不再显示它的明文（只留「导出令牌」「导入令牌」两颗按钮）：
     导出 = 复制到剪贴板，导入 = 读剪贴板。手机上"下载 .json 再挑回来"这条路本来就不通
     （安卓 Chrome 的 blob 下载会静默失败，国产 ROM 的文件选择器还按类型过滤），所以不做文件路径。 */
  async function copyTokenToClipboard() {
    const text = deviceTokenJson();
    if (!text) return;
    try {
      if (!navigator.clipboard?.writeText) throw new Error("这个浏览器不给写剪贴板");
      await navigator.clipboard.writeText(text);
      showNotice("令牌已复制到剪贴板 —— 它就是凭证，别发给别人。", "ok");
    } catch (_) {
      // 老环境（非安全上下文 / 旧浏览器）：弹出可长按复制的文本，别让这条路彻底断掉
      let shown = false;
      try {
        window.prompt("复制不了，请长按下面这段文字手动复制（它就是凭证，别发给别人）：", text);
        shown = true;
      } catch (_) {
        shown = false;
      }
      if (!shown) showNotice("复制失败：这个浏览器不给写剪贴板。");
      else hideNotice();
    }
  }

  els.deviceCopyBtn.addEventListener("click", () => { if (myDevice) copyTokenToClipboard(); });

  /** 导入令牌：解析 → 先用它读一次收件箱验证有效 → 才落盘（避免把错的令牌存下来）。 */
  async function applyTokenText(raw) {
    const text = String(raw || "").trim();
    if (!text) throw new Error("没有拿到令牌内容");
    let data;
    try {
      data = JSON.parse(text);
    } catch (_) {
      throw new Error("这不是合法的 JSON");
    }
    const id = String(data.id || data.device_id || "").trim();
    const token = String(data.token || data.device_token || "").trim();
    if (!id || !token) throw new Error("JSON 里没有 id / token");
    const inbox = await apiJson(`/api/devices/${encodeURIComponent(id)}/inbox`, {
      headers: { "X-Device-Token": token },
    });
    saveMyDevice({ id, token, name: inbox.device?.name || data.name || "已恢复设备" });
    renderInbox(inbox);
    setBadge(inbox.unread || 0);
    await loadDevices();
    showNotice(`已恢复设备「${myDevice.name}」，收件箱 ${inbox.count} 个文件。`, "ok");
  }

  async function importFromText(raw) {
    try {
      await applyTokenText(raw);
    } catch (error) {
      showNotice(`导入失败：${error.message}`);
    }
  }

  /** 「导入令牌」：先读剪贴板；读不到（没权限 / 是空的）时弹一个输入框让用户粘贴。 */
  els.deviceImportApplyBtn.addEventListener("click", async () => {
    let text = "";
    try {
      if (navigator.clipboard?.readText) {
        text = (await navigator.clipboard.readText()) || "";
      }
    } catch (_) {
      text = "";
    }
    if (!String(text).trim()) {
      let manual = "";
      try {
        manual = window.prompt("把令牌 JSON 粘贴到这里（在另一台设备上点「导出令牌」复制过来的那段）：", "") || "";
      } catch (_) {
        manual = "";
      }
      if (!manual.trim()) {
        showNotice("剪贴板里没有令牌：先在另一台已登记的设备上点「导出令牌」，再回来点「导入令牌」。");
        return;
      }
      text = manual;
    }
    await importFromText(text);
  });

  /* ---------------------------------------------------------- PWA（装成应用） */
  function registerServiceWorker() {
    if (!("serviceWorker" in navigator)) return;
    // 非安全上下文（http / 自签域名）注册会直接抛错，先自己判断，省得控制台一片红
    if (location.protocol !== "https:" && !["localhost", "127.0.0.1"].includes(location.hostname)) return;
    window.addEventListener("load", () => {
      navigator.serviceWorker
        .register("./sw.js", { scope: "./" })
        .then((registration) => registration.update?.())
        .catch((error) => console.warn("[pwa] service worker 注册失败", error));
    });
  }
  registerServiceWorker();

  window.addEventListener("offline", () => {
    showNotice("当前离线：上传、下载、发送都会失败，联网后自动恢复。", "ok");
  });
  window.addEventListener("online", () => {
    hideNotice();
    loadStats();
    loadDevices();
    if (myDevice) refreshInbox();
  });

  /* ---------------------------------------------------------- 初始化 */
  const params = new URLSearchParams(location.search);
  const preset = params.get("code");
  loadStats();
  loadDevices();
  myDevice = loadMyDevice();
  renderSelf();
  if (myDevice) refreshInbox();
  if (params.get("share") === "ok") {
    showNotice("已收到分享面板发来的内容，分享码在上面的「用分享码下载」里就绪。", "ok");
  } else if (params.get("share") === "empty") {
    showNotice("分享的内容是空的，什么都没收到。");
  }
  if (preset) {
    switchTab("fetch");
    els.codeInput.value = normalizeCode(preset);
    lookup(preset);
  }
})();
