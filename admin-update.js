// admin-update.js: GitHub update status polling and application
// Classic script module; shared state and API contracts remain in admin.js.

const UPDATE_BUSY_PHASES = new Set(["checking", "downloading", "applying", "verifying"]);

const formatDisplayVersion = (value) => {
  const version = String(value || "").trim();
  return version ? `v${version.replace(/^v/i, "")}` : "未知";
};

const formatUpdateDate = (value) => {
  if (!value) return "发布时间未知";
  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) return "发布时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(timestamp);
};

const updatePhaseLabel = (phase) => ({
  idle: "尚未检查",
  checking: "正在检查更新…",
  available: "发现新版本",
  up_to_date: "已是最新版",
  unavailable: "暂时无法检查",
  blocked: "本地源码有修改",
  downloading: "正在下载更新…",
  applying: "正在重启程序…",
  verifying: "正在验证新版…",
  failed: "更新失败，已保留当前版本",
}[phase] || "更新状态未知");

const getReleasePageUrl = () => {
  const candidate = String(updateStatus?.htmlUrl || "").trim();
  if (!candidate) return "";
  try {
    const url = new URL(candidate, window.location.href);
    if (url.protocol !== "https:" || url.hostname !== "github.com") return "";
    if (!/^\/[^/]+\/[^/]+\/releases\//.test(url.pathname)) return "";
    return url.href;
  } catch {
    return "";
  }
};

const renderUpdateModal = () => {
  if (!updateModalOpen || !updateStatus?.available) return "";
  const busy = UPDATE_BUSY_PHASES.has(updateStatus.phase) || updateRestarting;
  const canApply = updateStatus.canApply !== false;
  const releasePageUrl = getReleasePageUrl();
  const note = updateStatus.notes || "此次 Release 未提供详细更新说明。";
  const blockedMessage = updateStatus.sourceClean === false
    ? "检测到源码目录存在未提交修改或本地文件变更。为避免覆盖你的代码，本次不会自动替换源码。"
    : "";
  const applyLabel = updateStatus.phase === "downloading"
      ? `正在下载 ${Math.max(0, Number(updateStatus.progress) || 0)}%`
    : updateStatus.phase === "verifying"
      ? "正在验证新版…"
      : updateStatus.phase === "applying" || updateRestarting
        ? "正在重启…"
      : "立即更新";
  return `
    <div class="update-modal-backdrop" role="presentation">
      <section class="update-modal" role="dialog" aria-modal="true" aria-labelledby="update-modal-title">
        <div class="update-modal-heading">
          <div>
            <p class="eyebrow">程序更新</p>
            <h2 id="update-modal-title">发现 ${escapeHtml(updateStatus.latestVersion || "新版本")}</h2>
          </div>
          <span class="update-badge">${escapeHtml(updatePhaseLabel(updateStatus.phase))}</span>
        </div>
        <p class="update-modal-meta">当前版本 ${escapeHtml(formatDisplayVersion(updateStatus.currentVersion))} · ${escapeHtml(formatUpdateDate(updateStatus.publishedAt))}</p>
        <p class="update-modal-meta">最新版本 ${escapeHtml(formatDisplayVersion(updateStatus.latestVersion))}</p>
        <h3>${escapeHtml(updateStatus.title || "GitHub Release")}</h3>
        <pre class="update-notes">${escapeHtml(note)}</pre>
        <p class="update-preserve-note">更新会自动重启本地服务，不会删除题库、排行榜或答题记录；正在答题的页面可能需要刷新。</p>
        ${blockedMessage ? `<p class="update-blocked-note">${escapeHtml(blockedMessage)}</p>` : ""}
        ${updateStatus.phase === "failed" ? `<p class="update-blocked-note">更新包下载、校验或启动更新助手失败，当前版本未被替换；如已进入更新流程，请查看 Windows 启动窗口中的最终结果。</p>` : ""}
        <div class="update-modal-actions">
          <button class="admin-secondary" type="button" data-action="dismiss-update" ${busy ? "disabled" : ""}>稍后</button>
          ${releasePageUrl ? `<a class="admin-secondary update-github-link" href="${escapeHtml(releasePageUrl)}" target="_blank" rel="noopener noreferrer">在 GitHub 查看 / 下载</a>` : ""}
          <button class="admin-primary" type="button" data-action="apply-update" ${busy || !canApply ? "disabled" : ""}>${applyLabel}</button>
        </div>
      </section>
    </div>
  `;
};

const stopUpdatePolling = () => {
  if (updatePollTimer !== null) {
    window.clearTimeout(updatePollTimer);
    updatePollTimer = null;
  }
};

const applyUpdateStatus = (next, autoPrompt = false) => {
  if (!next || typeof next !== "object") return;
  updateStatus = next;
  if (next.phase === "applying") updateRestarting = true;
  if (autoPrompt && next.available && !updatePromptDismissed && !updateRestarting) updateModalOpen = true;
  if (!UPDATE_BUSY_PHASES.has(next.phase)) stopUpdatePolling();
  render();
};

const notifyManualUpdateResult = (next) => {
  if (!manualUpdateCheck || !next || UPDATE_BUSY_PHASES.has(next.phase)) return;
  manualUpdateCheck = false;
  if (next.available) return;
  const detail = next.error ? `\n${next.error}` : "";
  const message = next.phase === "up_to_date"
    ? "检查更新完成：当前已经是最新版。"
    : next.phase === "blocked"
      ? "检查更新完成：检测到本地源码有修改，为避免覆盖本地内容，暂不自动更新。"
      : next.phase === "failed"
        ? `检查更新失败：更新服务没有完成本次检查。${detail}`
        : "暂时无法检查更新：请确认本机服务正常，并检查网络或 GitHub 是否可访问。";
  window.alert(message);
};

const pollUpdateStatus = async (autoPrompt = false) => {
  try {
    const next = await fetchJson(API.updateStatus);
    applyUpdateStatus(next, autoPrompt);
    notifyManualUpdateResult(next);
    if (UPDATE_BUSY_PHASES.has(next.phase)) {
      updatePollTimer = window.setTimeout(() => pollUpdateStatus(autoPrompt), 800);
    }
  } catch {
    stopUpdatePolling();
    if (!updateRestarting) {
      updateStatus = { ...updateStatus, phase: "unavailable", available: false };
      render();
      if (manualUpdateCheck) {
        manualUpdateCheck = false;
        window.alert("暂时无法检查更新：更新服务或 GitHub 暂时不可访问，请稍后重试。\n请确认本地服务仍在运行。");
      }
    }
  }
};

const startUpdateMonitoring = () => {
  stopUpdatePolling();
  void pollUpdateStatus(true);
};

const checkForUpdates = async () => {
  manualUpdateCheck = true;
  updateRestarting = false;
  updateStatus = { ...updateStatus, phase: "checking", available: false, progress: 0 };
  updateModalOpen = false;
  render();
  try {
    const next = await postJson(API.updateCheck, {});
    applyUpdateStatus(next, false);
    notifyManualUpdateResult(next);
    if (next.available) {
      updatePromptDismissed = false;
      updateModalOpen = true;
      render();
    }
    if (UPDATE_BUSY_PHASES.has(next.phase)) {
      updatePollTimer = window.setTimeout(() => pollUpdateStatus(false), 500);
    }
  } catch {
    manualUpdateCheck = false;
    updateStatus = { ...updateStatus, phase: "unavailable", available: false };
    render();
    window.alert("暂时无法检查更新：更新服务或 GitHub 暂时不可访问，请稍后重试。\n请确认本地服务仍在运行。");
  }
};

const applyAvailableUpdate = async () => {
  if (!updateStatus.available || updateStatus.canApply === false || UPDATE_BUSY_PHASES.has(updateStatus.phase)) return;
  updateStatus = { ...updateStatus, phase: "downloading", progress: 0 };
  updateModalOpen = true;
  render();
  try {
    const next = await postJson(API.updateApply, { version: updateStatus.latestVersion });
    applyUpdateStatus(next, false);
    if (UPDATE_BUSY_PHASES.has(next.phase)) {
      updatePollTimer = window.setTimeout(() => pollUpdateStatus(false), 500);
    }
  } catch {
    updateStatus = { ...updateStatus, phase: "failed", available: true, progress: 0 };
    render();
  }
};


const wireUpdateEvents = () => {
  adminApp.querySelector('[data-action="check-update"]')?.addEventListener("click", checkForUpdates);
  adminApp.querySelector('[data-action="dismiss-update"]')?.addEventListener("click", () => {
    updatePromptDismissed = true;
    updateModalOpen = false;
    render();
  });
  adminApp.querySelector('[data-action="apply-update"]')?.addEventListener("click", applyAvailableUpdate);
};
