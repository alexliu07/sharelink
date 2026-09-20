/* ShareLink 前端逻辑：纯原生 JS，无外部依赖。 */
(() => {
  "use strict";

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
    deviceSetup: $("device-setup"),
    deviceSelf: $("device-self"),
    selfName: $("self-name"),
    selfMeta: $("self-meta"),
    deviceNameInput: $("device-name-input"),
    deviceAddBtn: $("device-add-btn"),
    deviceRenameBtn: $("device-rename-btn"),
    deviceLeaveBtn: $("device-leave-btn"),
    inboxList: $("inbox-list"),
    inboxCount: $("inbox-count"),
    inboxEmpty: $("inbox-empty"),
    deviceList: $("device-list"),
    deviceCount: $("device-count"),
    deviceEmpty: $("device-empty"),
    sendBtn: $("send-btn"),
    sendModal: $("send-modal"),
    sendClose: $("send-close"),
    sendCancel: $("send-cancel"),
    sendConfirm: $("send-confirm"),
    sendFile: $("send-file"),
    sendTargets: $("send-targets"),
    sendNoDevices: $("send-no-devices"),
    sendFromName: $("send-from-name"),
    sendNote: $("send-note"),
    sendProgress: $("send-progress"),
    sendBar: $("send-bar"),
    sendPercent: $("send-percent"),
  };

  const state = { file: null, ttl: 3600, uploading: false, share: null, target: null, timer: null };

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
      els.dzHint.textContent =
        `单文件上限 ${data.max_upload_mb} MB · 到期后自动删除`;
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
  }

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
  }

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

  async function apiJson(url, options = {}) {
    const res = await fetch(url, options);
    const payload = await res.json().catch(() => null);
    if (!res.ok) throw new Error(errorMessage(payload, `请求失败（HTTP ${res.status}）`));
    return payload;
  }

  function setBadge(count) {
    els.inboxBadge.textContent = String(count);
    els.inboxBadge.classList.toggle("hidden", !count);
  }

  function renderSelf() {
    const has = !!myDevice;
    els.deviceSetup.classList.toggle("hidden", has);
    els.deviceSelf.classList.toggle("hidden", !has);
    if (has) {
      els.selfName.textContent = myDevice.name;
      els.selfMeta.textContent = `设备 id ${myDevice.id} · 令牌只存在本浏览器`;
    } else {
      els.inboxList.innerHTML = "";
      els.inboxCount.textContent = "";
      setBadge(0);
    }
  }

  /* ---------- 设备列表 ---------- */
  async function loadDevices() {
    try {
      const data = await apiJson("/api/devices");
      deviceCache = data.devices || [];
      renderDeviceList();
      return deviceCache;
    } catch (err) {
      els.deviceList.innerHTML = `<li class="muted">设备列表读取失败：${escapeHtml(err.message)}</li>`;
      return [];
    }
  }

  function renderDeviceList() {
    els.deviceCount.textContent = deviceCache.length ? `共 ${deviceCache.length} 台` : "";
    els.deviceEmpty.classList.toggle("hidden", deviceCache.length > 0);
    els.deviceList.innerHTML = deviceCache.map((device) => {
      const mine = myDevice && device.id === myDevice.id;
      return `<li>
        <span class="picked-icon">${icon("icon-devices")}</span>
        <span class="meta">
          <strong>${escapeHtml(device.name)}</strong>
          <span class="muted">${lastSeenText(device.idle_seconds)} · 收件箱 ${device.inbox_count} 个文件</span>
        </span>
        ${mine ? '<span class="tag self">本设备</span>' : `<span class="tag">${escapeHtml(device.id)}</span>`}
      </li>`;
    }).join("");
  }

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
    if (!myDevice) return;
    try {
      const data = await apiJson(`/api/devices/${myDevice.id}/inbox`, { headers: deviceHeaders() });
      renderInbox(data);
      els.selfMeta.textContent = `设备 id ${myDevice.id} · 令牌只存在本浏览器 · 收件箱 ${data.count} 个文件`;
      setBadge(data.unread);
      if (markSeen && data.unread) {
        await apiJson(`/api/devices/${myDevice.id}/inbox/seen`, { method: "POST", headers: deviceHeaders() });
        setBadge(0);
        els.inboxCount.textContent = `共 ${data.count} 个 · 0 个未读`;
      }
    } catch (err) {
      showNotice(err.message);
    }
  }

  els.inboxList.addEventListener("click", async (event) => {
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
  els.deviceAddBtn.addEventListener("click", async () => {
    const name = els.deviceNameInput.value.trim();
    els.deviceAddBtn.disabled = true;
    try {
      const device = await apiJson("/api/devices", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name || undefined }),
      });
      saveMyDevice({ id: device.id, token: device.token, name: device.name });
      els.deviceNameInput.value = "";
      showNotice(`已把"${device.name}"加入设备列表；令牌已存在本浏览器，换浏览器需重新添加。`, "ok");
      await loadDevices();
      await refreshInbox({ markSeen: true });
    } catch (err) {
      showNotice(err.message);
    } finally {
      els.deviceAddBtn.disabled = false;
    }
  });

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
      showNotice(err.message);
    }
  });

  /* ---------- 发送至设备 ---------- */
  function openSendDialog() {
    if (!state.file) {
      showNotice('请先在「上传文件」里选择文件。');
      switchTab("upload");
      return;
    }
    els.sendFile.textContent = `将发送：${state.file.name}（${humanSize(state.file.size)}）· 有效期 ${humanLeft(state.ttl)}`;
    els.sendFromName.value = myDevice ? myDevice.name : els.sendFromName.value;
    els.sendFromName.disabled = !!myDevice;
    els.sendFromName.placeholder = myDevice ? "" : "匿名设备";
    els.sendModal.classList.remove("hidden");
    loadDevices().then(renderSendTargets);
  }

  function closeSendDialog() {
    els.sendModal.classList.add("hidden");
    els.sendProgress.classList.add("hidden");
    els.sendBar.style.width = "0%";
    els.sendPercent.textContent = "0%";
  }

  function renderSendTargets() {
    els.sendNoDevices.classList.toggle("hidden", deviceCache.length > 0);
    els.sendTargets.innerHTML = deviceCache.map((device) => `
      <li><label class="pick">
        <input type="checkbox" value="${escapeHtml(device.id)}">
        <span class="pick-body">
          <strong>${escapeHtml(device.name)}${myDevice && device.id === myDevice.id ? "（本设备）" : ""}</strong>
          <span class="muted">${lastSeenText(device.idle_seconds)} · 收件箱 ${device.inbox_count} 个文件</span>
        </span>
      </label></li>`).join("");
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

  els.sendConfirm.addEventListener("click", () => {
    const targets = selectedTargets();
    if (!targets.length || !state.file) return;

    const form = new FormData();
    form.append("file", state.file);
    form.append("targets", targets.join(","));
    form.append("ttl_seconds", String(state.ttl));
    const note = els.sendNote.value.trim();
    if (note) form.append("note", note);
    if (myDevice) form.append("from_device_id", myDevice.id);
    else form.append("from_name", els.sendFromName.value.trim());

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
    if (myDevice) refreshInbox({ markSeen: true });
  });

  /* 开着页面时每隔 20 秒刷新一次：设备标签页开着就顺手标已读 */
  setInterval(() => {
    if (!myDevice || document.visibilityState !== "visible") return;
    refreshInbox({ markSeen: els.panelDevices.classList.contains("active") });
  }, 20000);

  /* ---------------------------------------------------------- 初始化 */
  const params = new URLSearchParams(location.search);
  const preset = params.get("code");
  loadStats();
  loadDevices();
  myDevice = loadMyDevice();
  renderSelf();
  if (myDevice) refreshInbox();
  if (preset) {
    switchTab("fetch");
    els.codeInput.value = normalizeCode(preset);
    lookup(preset);
  }
})();
