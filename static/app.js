/* ShareLink 前端逻辑：纯原生 JS，无外部依赖。 */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);

  const els = {
    stats: $("stats"),
    tabs: document.querySelectorAll(".tab"),
    panels: { upload: $("panel-upload"), fetch: $("panel-fetch") },
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

  const flash = (button, text) => {
    const original = button.textContent;
    button.textContent = text;
    setTimeout(() => { button.textContent = original; }, 1200);
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
      panel.classList.toggle("active", key === name);
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
    hideNotice();
  }

  function clearFile() {
    state.file = null;
    els.fileInput.value = "";
    els.picked.classList.add("hidden");
    els.dropzone.classList.remove("hidden");
    els.uploadBtn.disabled = true;
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
    if (await copyText(state.share.code)) flash(els.copyCode, "✅");
  });
  els.copyLink.addEventListener("click", async () => {
    if (!state.share) return;
    if (await copyText(state.share.share_url)) flash(els.copyLink, "已复制");
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

  /* ---------------------------------------------------------- 初始化 */
  const params = new URLSearchParams(location.search);
  const preset = params.get("code");
  loadStats();
  if (preset) {
    switchTab("fetch");
    els.codeInput.value = normalizeCode(preset);
    lookup(preset);
  }
})();
