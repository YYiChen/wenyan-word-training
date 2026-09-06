// admin-sync.js: classroom realtime-sync UI (settings card, conflicts, backups)
// Classic script module; shared state and API contracts remain in admin.js.
// Sync-conflict lifecycle is independent from import-conflict lifecycle:
// this module keeps its own queue state and never touches the import one.

const SYNC_PHASE_LABELS = {
  disabled: "已关闭",
  connected: "已连接",
  syncing: "同步中",
  offline: "离线",
  conflict: "有冲突",
  paused: "已暂停",
};

// Pure sync UI helpers (no DOM access; covered by Node tests).
const syncStatusBadgeText = (status) => {
  if (!status || typeof status !== "object") return "同步：已关闭";
  const phase = SYNC_PHASE_LABELS[status.phase] || "未知";
  if (status.phase === "offline") {
    const pending = Number(status.pendingLocal) || 0;
    return pending > 0 ? `同步：离线 · ${pending} 项待同步` : "同步：离线";
  }
  if (status.phase === "conflict") {
    const count = Number(status.openConflicts) || 0;
    return count > 0 ? `同步：${count} 个冲突` : "同步：有冲突";
  }
  return `同步：${phase}`;
};

const adaptSyncConflict = (conflict, localBank) => {
  if (!conflict || typeof conflict !== "object") return null;
  const questions = Array.isArray(localBank?.questions) ? localBank.questions : [];
  const question = questions.find((item) => item && item.id === conflict.entity_id) || null;
  const article = (Array.isArray(localBank?.catalog) ? localBank.catalog : [])
    .find((item) => item && question && item.id === question.articleId) || {};
  const incoming = conflict.incoming_value || {};
  const server = conflict.server_value || {};
  const source = conflict.source_device || conflict.source_username || "其他设备";
  return {
    id: conflict.conflict_id || "",
    kind: conflict.kind || conflict.entity_kind || "review",
    entityKind: conflict.entity_kind || "",
    entityId: conflict.entity_id || "",
    source,
    createdAt: conflict.created_at || "",
    display: {
      word: question ? question.word || "" : "",
      sentence: question ? question.sentence || "" : "",
      article: article.title || "",
    },
    serverValue: server.value !== undefined ? server.value : null,
    incomingValue: incoming.value !== undefined ? incoming.value : null,
  };
};

const countUnresolvedSync = (conflicts, choices) => {
  const list = Array.isArray(conflicts) ? conflicts : [];
  const current = choices && typeof choices === "object" ? choices : {};
  return list.filter((item) => !item || (current[item.id] !== "server" && current[item.id] !== "incoming")).length;
};

const buildSyncChoices = (conflicts, choice) => {
  const resolutions = {};
  if (choice !== "server" && choice !== "incoming") return resolutions;
  (Array.isArray(conflicts) ? conflicts : []).forEach((item) => {
    if (item && item.id) resolutions[item.id] = choice;
  });
  return resolutions;
};

// --- DOM state (separate from import-conflict state) ---
let syncStatus = { phase: "disabled" };
let syncConfigOpen = false;
let syncBusy = false;
let syncFirstSync = null;
let syncFirstSyncPreview = null;
let syncFirstSyncChoices = {};
let syncConflictsOpen = false;
let syncConflictCards = [];
let syncConflictChoices = {};
let syncBackups = [];
let syncStatusTimer = null;

const loadSyncStatus = async () => {
  try {
    syncStatus = await fetchJson(API.syncStatus);
  } catch {
    syncStatus = { phase: "disabled" };
  }
  return syncStatus;
};

const renderSyncBadge = () => `<span class="sync-badge" id="sync-badge">${escapeHtml(syncStatusBadgeText(syncStatus))}</span>`;

const refreshSyncBadge = async () => {
  await loadSyncStatus();
  const badge = adminApp.querySelector("#sync-badge");
  if (badge) badge.textContent = syncStatusBadgeText(syncStatus);
};

const startSyncBadgePolling = () => {
  if (syncStatusTimer !== null) return;
  syncStatusTimer = window.setInterval(() => {
    if (document.hidden) return;
    void refreshSyncBadge();
  }, 5000);
};

const stopSyncBadgePolling = () => {
  if (syncStatusTimer !== null) {
    window.clearInterval(syncStatusTimer);
    syncStatusTimer = null;
  }
};

const formatSyncReviewLine = (review) => {
  if (typeof formatConflictReviewLine === "function") {
    return formatConflictReviewLine(review);
  }
  if (!review || typeof review !== "object") return "—";
  return `结论：${review.status || "—"}`;
};

const renderSyncValueLine = (entityKind, value) => {
  if (entityKind === "review") return formatSyncReviewLine(value);
  if (entityKind === "question" && value && typeof value === "object") {
    const question = value.question || {};
    const review = value.review || {};
    return `题目：${question.word || "—"}（${question.sentence || "—"}）；审查：${formatSyncReviewLine(review)}`;
  }
  return escapeHtml(JSON.stringify(value ?? null));
};

const renderSyncCard = () => {
  const status = syncStatus && typeof syncStatus === "object" ? syncStatus : { phase: "disabled" };
  const enabled = status.enabled === true;
  const lastSync = status.lastSyncAt ? `最后同步：${escapeHtml(status.lastSyncAt)}` : "尚未同步";
  const revision = status.serverRevision ? `服务器 revision：${escapeHtml(status.serverRevision)}` : "";
  const pending = Number(status.pendingLocal) || 0;
  const conflictCount = Number(status.openConflicts) || 0;
  return `
  <section class="admin-card settings-card settings-card-wide" aria-label="同步与备份">
    <div class="settings-card-heading">
      <div>
        <h2 class="admin-card-title">同步与备份</h2>
        <p>办公室、教室与讲台电脑可共同审同一份题库；本机题库永远可用，断网也不影响答题审题。</p>
      </div>
      <span class="settings-badge">${escapeHtml(syncStatusBadgeText(status))}</span>
    </div>
    <div class="sync-status-lines">
      <span>服务器：${enabled ? `${escapeHtml(status.host || "")}:${escapeHtml(status.port || "")}` : "未连接"}</span>
      <span>账号：${enabled ? escapeHtml(status.username || "") : "—"}</span>
      <span>${escapeHtml(lastSync)}</span>
      ${revision ? `<span>${escapeHtml(revision)}</span>` : ""}
      ${pending ? `<span>本机待同步：${pending}</span>` : ""}
      ${conflictCount ? `<span>待处理冲突：${conflictCount}</span>` : ""}
      ${status.lastError && !enabled ? `<span class="sync-error">${escapeHtml(status.lastError)}</span>` : ""}
      ${status.message ? `<span>${escapeHtml(status.message)}</span>` : ""}
    </div>
    <div class="sync-actions">
      <button class="admin-secondary" type="button" data-action="sync-now" ${!enabled || syncBusy ? "disabled" : ""}>立即同步</button>
      <button class="admin-secondary" type="button" data-action="sync-config">${enabled ? "同步设置" : "连接同步服务器"}</button>
      ${enabled ? `<button class="admin-secondary" type="button" data-action="sync-disconnect">断开同步</button>` : ""}
      ${enabled ? `<button class="admin-secondary" type="button" data-action="sync-conflicts">处理冲突${conflictCount ? `（${conflictCount}）` : ""}</button>` : ""}
      ${enabled ? `<button class="admin-secondary" type="button" data-action="sync-backups">远程备份</button>` : ""}
    </div>
    ${syncConfigOpen ? renderSyncConfigForm() : ""}
    ${syncFirstSync ? renderFirstSyncPanel() : ""}
    ${syncConflictsOpen ? renderSyncConflictPanel() : ""}
    ${syncBackups.length || syncBackupsOpen ? renderSyncBackupPanel() : ""}
  </section>`;
};

let syncBackupsOpen = false;

const renderSyncConfigForm = () => `
  <form id="sync-config-form" class="settings-form">
    <label class="editor-field">服务器地址
      <input class="admin-input" name="host" maxlength="255" required value="${escapeHtml(syncStatus.host || "39.171.79.237")}" />
    </label>
    <label class="editor-field">端口
      <input class="admin-input" name="port" type="number" min="7501" max="65535" required value="${escapeHtml(syncStatus.port || "10001")}" />
    </label>
    <label class="editor-field">账号
      <input class="admin-input" name="username" maxlength="64" required value="${escapeHtml(syncStatus.username || "")}" />
    </label>
    <label class="editor-field">密码
      <input class="admin-input" name="password" type="password" autocomplete="off" required />
    </label>
    <label class="editor-field">本机显示名称（可选）
      <input class="admin-input" name="deviceName" maxlength="40" placeholder="例如：办公室电脑" value="${escapeHtml(syncStatus.deviceName || "")}" />
    </label>
    <p class="sync-note">密码只用于本次登录验证，不会明文传输也不会保存在本机。题库同步内容没有 TLS 级网络机密性。</p>
    <div class="editor-actions">
      <button class="admin-secondary" type="button" data-action="sync-test">测试连接</button>
      <button class="admin-primary" type="submit">连接并启用同步</button>
    </div>
  </form>`;

const renderFirstSyncPanel = () => {
  const info = syncFirstSync;
  if (!info || typeof info !== "object") return "";
  if (info.case === "server_empty") {
    return `<div class="sync-panel">
      <h3>服务器尚无共享题库</h3>
      <p>是否使用本机当前题库（${escapeHtml(info.local?.question_count ?? 0)} 道）初始化同步空间？这是一次性初始化。</p>
      <div class="editor-actions">
        <button class="admin-secondary" type="button" data-action="sync-firstsync-cancel">取消</button>
        <button class="admin-primary" type="button" data-action="sync-firstsync-server-empty">使用本机题库初始化</button>
      </div>
    </div>`;
  }
  if (info.case === "local_empty") {
    return `<div class="sync-panel">
      <h3>使用服务器共享题库初始化本机</h3>
      <p>服务器已有 ${escapeHtml(info.server?.question_count ?? 0)} 道题。确认后会先备份本机当前题库，再下载服务器题库。</p>
      <div class="editor-actions">
        <button class="admin-secondary" type="button" data-action="sync-firstsync-cancel">取消</button>
        <button class="admin-primary" type="button" data-action="sync-firstsync-local-empty">使用服务器题库初始化</button>
      </div>
    </div>`;
  }
  if (info.case === "both" && !syncFirstSyncPreview) {
    return `<div class="sync-panel">
      <h3>两边都有题库</h3>
      <p>本机 ${escapeHtml(info.local?.question_count ?? 0)} 道，服务器 ${escapeHtml(info.server?.question_count ?? 0)} 道。不会自动覆盖任何一边，先预览合并。</p>
      <div class="editor-actions">
        <button class="admin-secondary" type="button" data-action="sync-firstsync-cancel">取消</button>
        <button class="admin-primary" type="button" data-action="sync-firstsync-preview">预览首次同步合并</button>
      </div>
    </div>`;
  }
  if (info.case === "both" && syncFirstSyncPreview) {
    const preview = syncFirstSyncPreview;
    const summary = preview.summary || {};
    const conflicts = (preview.conflicts || []).filter((item) => item && item.kind === "review");
    const unresolved = countUnresolvedReviewConflicts(
      conflicts.map((item) => ({ conflictId: item.conflictId })), syncFirstSyncChoices);
    return `<div class="sync-panel">
      <h3>首次同步合并预览</h3>
      <p>以服务器为共享基准合并；内容冲突默认保留服务器版本。审查结论不同的题必须逐项选择，暂不处理将暂停本次连接。</p>
      <p>自动补充审查 ${escapeHtml(summary.reviewsSupplemented || 0)} · 双方一致 ${escapeHtml(summary.sameReviewed || 0)} · 审查冲突 ${escapeHtml(summary.reviewConflicts || 0)}</p>
      <ol class="review-conflict-list">
        ${conflicts.map((item, index) => {
          const display = item.questionDisplay || {};
          const choice = syncFirstSyncChoices[item.conflictId];
          return `<li class="review-conflict-card${choice ? " is-resolved" : ""}">
            <div class="review-conflict-heading"><strong>审查冲突 ${index + 1} / ${conflicts.length}</strong><span>${escapeHtml([display.book, display.article].filter(Boolean).join(" · "))}</span></div>
            <div class="review-conflict-question">考点：${escapeHtml(display.word || "—")}　原句：${escapeHtml(display.sentence || "—")}</div>
            <div class="review-conflict-sides">
              <div class="review-conflict-side"><h4>保留服务器结果</h4><p>${escapeHtml(formatConflictReviewLine(item.localReview))}</p></div>
              <div class="review-conflict-side"><h4>采用本机结果</h4><p>${escapeHtml(formatConflictReviewLine(item.incomingReview))}</p></div>
            </div>
            <div class="review-conflict-choices" role="group" aria-label="首次同步冲突 ${index + 1}">
              ${["server", "local"].map((value) => `<button type="button" class="admin-secondary${choice === value ? " is-selected" : ""}" data-action="sync-firstsync-choice" data-conflict="${escapeHtml(item.conflictId)}" data-choice="${value}">${value === "server" ? "保留服务器" : "采用本机"}</button>`).join("")}
            </div>
          </li>`;
        }).join("")}
      </ol>
      <div class="editor-actions">
        <button class="admin-secondary" type="button" data-action="sync-firstsync-cancel">稍后处理</button>
        <button class="admin-primary" type="button" data-action="sync-firstsync-confirm" ${unresolved > 0 || syncBusy ? "disabled" : ""}>${unresolved > 0 ? `还剩 ${unresolved} 道未处理` : "确认并启用同步"}</button>
      </div>
    </div>`;
  }
  return "";
};

const renderSyncConflictPanel = () => {
  const unresolved = countUnresolvedSync(syncConflictCards, syncConflictChoices);
  return `<div class="sync-panel">
    <div class="question-import-preview-heading">
      <div>
        <p class="eyebrow">同步冲突处理</p>
        <h3>请选择每道冲突题的审查结论</h3>
        <p class="admin-subtitle">冲突保存在服务器上，所有设备可见；有未解决冲突的题目暂不进入学生题池。</p>
      </div>
      <span class="question-import-preview-badge">${unresolved > 0 ? `剩余 ${unresolved} 道未处理` : "已全部处理"}</span>
    </div>
    <div class="review-conflict-batch">
      <button class="admin-secondary" type="button" data-action="sync-conflict-all" data-choice="server">全部保留服务器</button>
      <button class="admin-secondary" type="button" data-action="sync-conflict-all" data-choice="incoming">全部采用提交结果</button>
    </div>
    <ol class="review-conflict-list">
      ${syncConflictCards.map((card, index) => {
        if (!card) return "";
        const choice = syncConflictChoices[card.id];
        return `<li class="review-conflict-card${choice ? " is-resolved" : ""}">
        <div class="review-conflict-heading"><strong>同步冲突 ${index + 1} / ${syncConflictCards.length}</strong><span>${escapeHtml(card.display.article)}</span></div>
        <div class="review-conflict-question">考点：${escapeHtml(card.display.word || "—")}　原句：${escapeHtml(card.display.sentence || "—")}</div>
        <div class="review-conflict-question">来自：${escapeHtml(card.source)}${card.createdAt ? `　时间：${escapeHtml(card.createdAt)}` : ""}</div>
        <div class="review-conflict-sides">
          <div class="review-conflict-side"><h4>保留服务器结果</h4><p>${renderSyncValueLine(card.entityKind, card.serverValue)}</p></div>
          <div class="review-conflict-side"><h4>采用这台设备提交的结果</h4><p>${renderSyncValueLine(card.entityKind, card.incomingValue)}</p></div>
        </div>
        <div class="review-conflict-choices" role="group" aria-label="同步冲突 ${index + 1}">
          ${[["server", "保留服务器结果"], ["incoming", "采用提交结果"], ["skip", "稍后处理"]].map(([value, label]) => `<button type="button" class="admin-secondary${choice === value ? " is-selected" : ""}" data-action="sync-conflict-choice" data-conflict="${escapeHtml(card.id)}" data-choice="${value}">${label}</button>`).join("")}
        </div>
      </li>`;
      }).join("")}
    </ol>
    <div class="editor-actions">
      <button class="admin-secondary" type="button" data-action="sync-conflicts-close">关闭</button>
      <button class="admin-primary" type="button" data-action="sync-conflicts-apply" ${syncBusy ? "disabled" : ""}>应用已选择的冲突处理</button>
    </div>
  </div>`;
};

const renderSyncBackupPanel = () => `
  <div class="sync-panel">
    <div class="question-import-preview-heading">
      <div>
        <p class="eyebrow">异地备份</p>
        <h3>远程完整题库备份</h3>
        <p class="admin-subtitle">备份与实时同步完全分开：上传不改变共享题库，下载不自动导入本机。</p>
      </div>
    </div>
    <div class="editor-actions">
      <button class="admin-secondary" type="button" data-action="sync-backup-upload" ${syncBusy ? "disabled" : ""}>上传当前完整题库备份</button>
      <button class="admin-secondary" type="button" data-action="sync-backups-close">关闭</button>
    </div>
    <div class="settings-list">
      ${syncBackups.length ? syncBackups.map((item) => {
        const summary = item.review_summary || {};
        return `<div class="settings-list-item">
          <div><strong>${escapeHtml(item.created_at || "")}</strong>
          <p>${escapeHtml(item.question_count ?? 0)} 道 · passed ${escapeHtml(summary.passed ?? 0)} · pending ${escapeHtml(summary.pending ?? 0)} · 待修改 ${escapeHtml(summary.needs_revision ?? 0)} · skipped ${escapeHtml(summary.skipped ?? 0)} · ${escapeHtml(formatBackupSize(item.size))} · ${escapeHtml(item.device_name || "")}</p></div>
          <button class="admin-secondary admin-compact-button" type="button" data-action="sync-backup-download" data-backup-id="${escapeHtml(item.backup_id)}">下载</button>
        </div>`;
      }).join("") : "<p>暂无远程备份。</p>"}
    </div>
  </div>`;

const formatBackupSize = (size) => {
  const bytes = Number(size) || 0;
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${bytes} B`;
};

// --- actions ---
const withSyncBusy = async (work) => {
  if (syncBusy) return;
  syncBusy = true;
  render();
  try {
    await work();
  } catch (error) {
    window.alert(error instanceof Error ? error.message : "同步操作失败。");
  } finally {
    syncBusy = false;
    await loadSyncStatus();
    render();
  }
};

const submitSyncConfig = async (form, { testOnly }) => {
  const formData = new FormData(form);
  const payload = {
    host: formData.get("host").toString(),
    port: Number(formData.get("port")),
    username: formData.get("username").toString(),
    password: formData.get("password").toString(),
    deviceName: formData.get("deviceName").toString(),
  };
  const result = await postJson(testOnly ? API.syncTest : API.syncConfigure, payload);
  form.querySelector('[name="password"]').value = "";
  return result;
};

const connectSyncFromConfig = async () => {
  const result = await postJson(API.syncConnect, {});
  const outcome = result?.case;
  if (outcome === "resumed") {
    syncFirstSync = null;
    window.alert("已恢复增量同步。");
    return;
  }
  if (outcome === "server_empty" || outcome === "local_empty" || outcome === "both") {
    syncFirstSync = result;
    syncFirstSyncPreview = null;
    syncFirstSyncChoices = {};
    return;
  }
  syncFirstSync = result;
};

const wireSyncEvents = () => {
  adminApp.querySelector('[data-action="sync-now"]')?.addEventListener("click", () => {
    void withSyncBusy(async () => {
      await postJson(API.syncNow, {});
    });
  });
  adminApp.querySelector('[data-action="sync-config"]')?.addEventListener("click", () => {
    syncConfigOpen = !syncConfigOpen;
    render();
  });
  adminApp.querySelector('[data-action="sync-test"]')?.addEventListener("click", async () => {
    const form = adminApp.querySelector("#sync-config-form");
    if (!form) return;
    try {
      const result = await submitSyncConfig(form, { testOnly: true });
      window.alert(`连接成功：服务器 revision ${result.serverRevision ?? 0}。`);
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "连接失败。");
    }
  });
  adminApp.querySelector("#sync-config-form")?.addEventListener("submit", (event) => {
    event.preventDefault();
    const form = event.target;
    void withSyncBusy(async () => {
      await submitSyncConfig(form, { testOnly: false });
      syncConfigOpen = false;
      await connectSyncFromConfig();
    });
  });
  adminApp.querySelector('[data-action="sync-disconnect"]')?.addEventListener("click", () => {
    const clear = window.confirm("断开同步吗？\n\n确定 = 同时清除本机保存的同步凭据；\n取消 = 仅断开，保留服务器地址与账号，下次重连需重新输入密码。\n\n本机题库不受影响。");
    void withSyncBusy(async () => {
      await postJson(API.syncDisconnect, { clearCredential: clear });
      syncFirstSync = null;
      syncConflictsOpen = false;
      syncBackupsOpen = false;
    });
  });
  adminApp.querySelector('[data-action="sync-firstsync-cancel"]')?.addEventListener("click", () => {
    syncFirstSync = null;
    syncFirstSyncPreview = null;
    syncFirstSyncChoices = {};
    render();
  });
  adminApp.querySelector('[data-action="sync-firstsync-server-empty"]')?.addEventListener("click", () => {
    void withSyncBusy(async () => {
      await postJson(API.syncBootstrapConfirm, { action: "server_empty" });
      syncFirstSync = null;
    });
  });
  adminApp.querySelector('[data-action="sync-firstsync-local-empty"]')?.addEventListener("click", () => {
    void withSyncBusy(async () => {
      await postJson(API.syncBootstrapConfirm, { action: "local_empty" });
      syncFirstSync = null;
      await load();
    });
  });
  adminApp.querySelector('[data-action="sync-firstsync-preview"]')?.addEventListener("click", () => {
    void withSyncBusy(async () => {
      syncFirstSyncPreview = await postJson(API.syncBootstrapPreview, {});
      syncFirstSyncChoices = {};
    });
  });
  adminApp.querySelectorAll('[data-action="sync-firstsync-choice"]').forEach((button) => {
    button.addEventListener("click", () => {
      const conflictId = button.dataset.conflict;
      const choice = button.dataset.choice;
      if (!conflictId || (choice !== "server" && choice !== "local")) return;
      syncFirstSyncChoices = { ...syncFirstSyncChoices, [conflictId]: choice };
      render();
    });
  });
  adminApp.querySelector('[data-action="sync-firstsync-confirm"]')?.addEventListener("click", () => {
    void withSyncBusy(async () => {
      // First-sync review resolutions reuse the import contract shape;
      // "server" maps to local side (server is the merge base there).
      const mapped = {};
      Object.entries(syncFirstSyncChoices).forEach(([conflictId, choice]) => {
        mapped[conflictId] = choice === "server" ? "local" : "incoming";
      });
      await postJson(API.syncBootstrapConfirm, { action: "both", reviewResolutions: mapped });
      syncFirstSync = null;
      syncFirstSyncPreview = null;
      syncFirstSyncChoices = {};
      await load();
    });
  });
  adminApp.querySelector('[data-action="sync-conflicts"]')?.addEventListener("click", async () => {
    try {
      const result = await fetchJson(API.syncConflicts);
      const raw = Array.isArray(result?.conflicts) ? result.conflicts : [];
      syncConflictCards = raw.map((item) => adaptSyncConflict(item, bank)).filter(Boolean);
      syncConflictChoices = {};
      syncConflictsOpen = true;
      render();
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "读取冲突失败。");
    }
  });
  adminApp.querySelector('[data-action="sync-conflicts-close"]')?.addEventListener("click", () => {
    syncConflictsOpen = false;
    render();
  });
  adminApp.querySelectorAll('[data-action="sync-conflict-choice"]').forEach((button) => {
    button.addEventListener("click", () => {
      const conflictId = button.dataset.conflict;
      const choice = button.dataset.choice;
      if (!conflictId || !["server", "incoming", "skip"].includes(choice)) return;
      syncConflictChoices = { ...syncConflictChoices, [conflictId]: choice };
      render();
    });
  });
  adminApp.querySelectorAll('[data-action="sync-conflict-all"]').forEach((button) => {
    button.addEventListener("click", () => {
      const choice = button.dataset.choice;
      syncConflictChoices = buildSyncChoices(syncConflictCards, choice);
      render();
    });
  });
  // Applying posts each decided choice; "skip" stays local for later.
  adminApp.querySelector('[data-action="sync-conflicts-apply"]')?.addEventListener("click", () => {
    const decided = Object.entries(syncConflictChoices).filter(
      ([, choice]) => choice === "server" || choice === "incoming");
    if (!decided.length) {
      window.alert("还没有选择任何冲突处理方式。");
      return;
    }
    void withSyncBusy(async () => {
      for (const [conflictId, choice] of decided) {
        try {
          await postJson(API.syncConflictsResolve, { conflict_id: conflictId, choice });
        } catch (error) {
          window.alert(`冲突 ${conflictId} 处理失败：${error instanceof Error ? error.message : "未知错误"}`);
          break;
        }
      }
      const result = await fetchJson(API.syncConflicts);
      const raw = Array.isArray(result?.conflicts) ? result.conflicts : [];
      syncConflictCards = raw.map((item) => adaptSyncConflict(item, bank)).filter(Boolean);
      syncConflictChoices = {};
    });
  });
  adminApp.querySelector('[data-action="sync-backups"]')?.addEventListener("click", async () => {
    try {
      const result = await fetchJson(API.syncBackups);
      syncBackups = Array.isArray(result?.backups) ? result.backups : [];
      syncBackupsOpen = true;
      render();
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "读取备份失败。");
    }
  });
  adminApp.querySelector('[data-action="sync-backups-close"]')?.addEventListener("click", () => {
    syncBackupsOpen = false;
    render();
  });
  adminApp.querySelector('[data-action="sync-backup-upload"]')?.addEventListener("click", () => {
    if (!window.confirm("上传当前完整题库到远程备份吗？这不会改变共享题库，也不会增加同步版本号。")) return;
    void withSyncBusy(async () => {
      await postJson(API.syncBackupsUpload, {});
      const result = await fetchJson(API.syncBackups);
      syncBackups = Array.isArray(result?.backups) ? result.backups : [];
    });
  });
  adminApp.querySelectorAll('[data-action="sync-backup-download"]').forEach((button) => {
    button.addEventListener("click", async () => {
      const backupId = button.dataset.backupId;
      if (!backupId) return;
      try {
        const response = await fetch(`${API.syncBackupsDownload}?id=${encodeURIComponent(backupId)}`, {
          headers: { "X-Wenyan-Admin-Token": adminToken || "" },
        });
        if (!response.ok) {
          const payload = await response.json().catch(() => ({}));
          throw new Error(payload.error || `下载失败（${response.status}）。`);
        }
        const blob = await response.blob();
        const filename = response.headers.get("X-Backup-Filename") || `wenyan-question-bank-backup-${Date.now()}.json`;
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = filename;
        link.click();
        URL.revokeObjectURL(url);
        window.alert("备份已下载到本地文件。请用题库管理的合并/替换功能自行恢复，本次下载没有修改本机题库。");
      } catch (error) {
        window.alert(error instanceof Error ? error.message : "下载失败。");
      }
    });
  });
};
