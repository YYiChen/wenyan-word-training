// admin-records.js: leaderboard and answer-record administration
// Classic script module; shared state and API contracts remain in admin.js.

const formatLeaderboardContextAdmin = (context) => {
  if (!context || typeof context !== "object") return "历史成绩 · 规则未知";
  const volumes = Array.isArray(context.volumes) ? context.volumes.map((item) => item?.label).filter(Boolean) : [];
  const articles = Array.isArray(context.articles) ? context.articles.map((item) => item?.label).filter(Boolean) : [];
  const duration = Number(context.durationSeconds) > 0 ? `${Math.floor(Number(context.durationSeconds) / 60).toString().padStart(2, "0")}:${(Number(context.durationSeconds) % 60).toString().padStart(2, "0")}` : "未知";
  return `${volumes.join("、") || "历史范围未知"}${articles.length ? ` · ${articles.length}篇` : ""} · ${duration} · ${context.scoring?.mode === "streak" ? "连续表现" : "固定计分"}`;
};

const renderLeaderboardTab = () => `
  <section class="leaderboard-layout">
    <section class="admin-card leaderboard-card-admin">
      <div class="admin-card-header"><h2 class="admin-card-title">排行榜</h2><span class="admin-count">${leaderboard.length} 条记录</span></div>
      <form id="leaderboard-editor">
        <table class="leaderboard-table">
          <thead><tr><th>排名</th><th>姓名</th><th>分数</th><th>操作</th></tr></thead>
          <tbody>
            ${leaderboard.length ? leaderboard.map((entry, index) => `
              <tr data-entry-index="${index}" data-entry-id="${escapeHtml(entry.id || "")}" data-record-id="${escapeHtml(entry.recordId || "")}" data-created-at="${entry.createdAt}" data-context="${escapeHtml(JSON.stringify(entry.context || {}))}">
                <td>${index + 1}</td>
                <td><input class="admin-input" name="entry-name-${index}" value="${escapeHtml(entry.name)}" maxlength="20" required /><small class="leaderboard-entry-context">${escapeHtml(formatLeaderboardContextAdmin(entry.context))}</small></td>
                <td><input class="admin-input" name="entry-score-${index}" type="number" value="${entry.score}" required /></td>
                <td><button class="admin-danger entry-delete" type="button" data-action="delete-entry" data-entry-index="${index}">删除</button></td>
              </tr>
            `).join("") : `<tr><td colspan="4" class="editor-empty">暂时没有成绩记录。</td></tr>`}
          </tbody>
        </table>
        <div class="leaderboard-actions">
          <button class="admin-primary" type="submit">保存排行榜修改</button>
          <button class="admin-danger" type="button" data-action="clear-leaderboard">清空排行榜</button>
        </div>
      </form>
    </section>
    <aside class="admin-card leaderboard-add-card">
      <h2>新增成绩</h2>
      <form id="leaderboard-add">
        <label class="editor-field">姓名<input class="admin-input" name="name" maxlength="20" required /></label>
        <label class="editor-field">分数<input class="admin-input" name="score" type="number" value="0" required /></label>
        <div class="leaderboard-actions"><button class="admin-primary" type="submit">新增并保存</button></div>
      </form>
    </aside>
  </section>
`;

const renderAnswerRecordsTab = () => {
  const archivedCount = answerRecords.filter((record) => record.archived).length;
  const visibleRecords = answerRecords.filter((record) => record.archived === showArchivedRecords);
  const visibleIds = new Set(visibleRecords.map((record) => record.id));
  [...selectedAnswerRecordIds].forEach((id) => {
    if (!visibleIds.has(id)) selectedAnswerRecordIds.delete(id);
  });
  const selectedCount = [...selectedAnswerRecordIds].filter((id) => visibleIds.has(id)).length;
  const allVisibleSelected = visibleRecords.length > 0 && selectedCount === visibleRecords.length;
  const emptyMessage = showArchivedRecords ? "暂时没有已折叠的答题记录。" : "暂时没有未折叠的答题记录。";

  return `
    <section class="admin-card answer-records-admin-card">
      <div class="admin-card-header answer-record-admin-header">
        <div>
          <h2 class="admin-card-title">答题记录</h2>
          <p class="admin-subtitle">答题记录可手动折叠或恢复；普通训练与双人 PK 合计只保留最近 1 个月内的最新 100 条，超出总量的旧记录会清除并在清除前备份。导入导出均使用本机 JSON 文件。</p>
        </div>
        <span class="admin-count">未折叠 ${answerRecords.length - archivedCount} · 已折叠 ${archivedCount} · 合计 ${answerRecords.length} / 100</span>
      </div>
      <div class="answer-record-admin-actions">
        <div class="answer-record-admin-file-actions">
          <button class="admin-secondary" type="button" data-action="export-answer-records">导出全部记录</button>
          <button class="admin-secondary" type="button" data-action="import-answer-records">导入答题记录</button>
          <input id="answer-record-file" type="file" accept=".json,application/json" hidden />
        </div>
        <button class="admin-secondary" type="button" data-action="toggle-archived-records">${showArchivedRecords ? "返回未折叠记录" : `查看已折叠记录（${archivedCount}）`}</button>
      </div>
      <div class="answer-record-admin-toolbar">
        <label class="answer-record-select-all">
          <input type="checkbox" data-action="select-all-answer-records" ${allVisibleSelected ? "checked" : ""} ${visibleRecords.length ? "" : "disabled"} />
          <span>全选当前显示记录</span>
        </label>
        <span class="answer-record-selection-count" data-selection-count>已选 ${selectedCount} 条</span>
        <button class="${showArchivedRecords ? "admin-secondary" : "admin-danger"}" type="button" data-action="archive-selected" ${selectedCount ? "" : "disabled"}>${showArchivedRecords ? "恢复所选记录" : "折叠所选记录"}</button>
      </div>
      ${visibleRecords.length === 0 ? `<div class="editor-empty">${emptyMessage}</div>` : `
        <div class="answer-record-admin-list">
          ${visibleRecords.map((record) => `
            <article class="answer-record-admin-row ${selectedAnswerRecordIds.has(record.id) ? "selected" : ""}">
              <label class="answer-record-checkbox" aria-label="选择${escapeHtml(record.name)}的答题记录">
                <input type="checkbox" data-action="select-answer-record" data-record-id="${escapeHtml(record.id)}" ${selectedAnswerRecordIds.has(record.id) ? "checked" : ""} />
              </label>
              <div class="answer-record-admin-main">
                <strong>${record.recordType === "pk" ? "双人 PK · " + (record.pkMode === "questions" ? "比题数" : "比时间") : escapeHtml(record.name)}</strong>
                <span>${escapeHtml(formatRecordDate(record.finishedAt))} · ${record.recordType === "pk" ? "双方合计已答 " + record.answeredCount + " 题" : "用时 " + formatSeconds(record.usedSeconds) + " · " + (record.completedAll ? "全部答完" : "提前结束")}</span>
              </div>
              <div class="answer-record-admin-stats">
                <strong>${record.recordType === "pk" && record.players?.length === 2 ? String(record.players[0].score) + " : " + String(record.players[1].score) : record.score + " 分"}</strong>
                <span>答对 ${record.correctCount} · 答错 ${record.wrongCount} · 已答 ${record.answeredCount} 题</span>
              </div>
            </article>
          `).join("")}
        </div>
      `}
    </section>
  `;
};

const importAnswerRecordsJson = async (records) => {
  const response = await fetch(API.answerRecordsImport, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(adminToken ? { "X-Wenyan-Admin-Token": adminToken } : {}),
    },
    body: JSON.stringify({ records }),
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok || !payload?.ok) throw new Error(payload?.error || "答题记录导入失败。");
  return payload;
};

const exportAnswerRecords = () => {
  const exportPayload = {
    schemaVersion: 1,
    type: "wenyan-answer-records",
    exportedAt: new Date().toISOString(),
    records: answerRecords,
  };
  const dateLabel = new Date().toISOString().slice(0, 10);
  downloadTextFile(
    `文言实词答题记录-${dateLabel}.json`,
    JSON.stringify(exportPayload, null, 2),
    "application/json;charset=utf-8",
  );
};

const importAnswerRecordsFromFile = async (file) => {
  let imported;
  try {
    imported = JSON.parse(await file.text());
  } catch (error) {
    throw new Error(`答题记录 JSON 无法读取：${error instanceof Error ? error.message : "格式错误"}`);
  }
  const records = Array.isArray(imported) ? imported : imported?.records;
  if (!Array.isArray(records) || !records.length) {
    throw new Error("导入失败：文件必须包含非空 records 答题记录数组。");
  }
  if (!window.confirm(`确定导入 ${records.length} 条答题记录吗？导入会在原记录基础上新增，重复 id 将跳过。`)) return false;
  const result = await importAnswerRecordsJson(records);
  answerRecords = normalizeAnswerRecords(result.data);
  selectedAnswerRecordIds.clear();
  const prunedText = result.prunedCount ? `，自动清理 ${result.prunedCount} 条超出保留范围的记录` : "";
  statusMessage = `答题记录导入完成：新增 ${result.addedCount} 条，跳过 ${result.skippedCount} 条重复记录${prunedText}`;
  render();
  return true;
};

const setSelectedAnswerRecordsArchived = async (archived) => {
  const ids = [...selectedAnswerRecordIds];
  if (!ids.length) return;
  const actionLabel = archived ? "折叠" : "恢复";
  if (!window.confirm(`确定${actionLabel}所选的 ${ids.length} 条答题记录吗？记录仍会保存在本机，只改变默认显示状态。`)) return;
  const result = await patchJson(API.answerRecords, { ids, archived });
  answerRecords = normalizeAnswerRecords(result);
  selectedAnswerRecordIds.clear();
  if (!archived) showArchivedRecords = false;
  statusMessage = `已${actionLabel} ${ids.length} 条答题记录，数据仍保存在本机用户目录`;
  render();
};

const readLeaderboardForm = (form) => [...form.querySelectorAll("tbody tr[data-entry-index]")].map((row) => {
  const index = Number(row.dataset.entryIndex);
  return {
    id: row.dataset.entryId || undefined,
    recordId: row.dataset.recordId || undefined,
    name: form.elements[`entry-name-${index}`].value.trim(),
    score: Number(form.elements[`entry-score-${index}`].value),
    createdAt: Number(row.dataset.createdAt) || Date.now(),
    context: row.dataset.context ? JSON.parse(row.dataset.context) : undefined,
  };
});

const saveLeaderboardEntries = async (entries) => {
  leaderboard = normalizeLeaderboard(await putJson(API.leaderboard, entries));
  statusMessage = "排行榜已写入电脑用户数据目录";
  render();
};


const wireRecordEvents = () => {
    const selectionCount = adminApp.querySelector("[data-selection-count]");
    const batchButton = adminApp.querySelector('[data-action="archive-selected"]');
    const visibleCheckboxes = () => [...adminApp.querySelectorAll('[data-action="select-answer-record"]')];
    const updateSelectionUi = () => {
      const selectedCount = selectedAnswerRecordIds.size;
      if (selectionCount) selectionCount.textContent = `已选 ${selectedCount} 条`;
      if (batchButton) batchButton.disabled = selectedCount === 0;
      const selectAll = adminApp.querySelector('[data-action="select-all-answer-records"]');
      const checkboxes = visibleCheckboxes();
      if (selectAll) {
        selectAll.checked = checkboxes.length > 0 && checkboxes.every((checkbox) => checkbox.checked);
        selectAll.indeterminate = checkboxes.some((checkbox) => checkbox.checked) && !selectAll.checked;
      }
    };
    adminApp.querySelectorAll('[data-action="select-answer-record"]').forEach((checkbox) => {
      checkbox.addEventListener("change", () => {
        const recordId = checkbox.dataset.recordId;
        if (!recordId) return;
        if (checkbox.checked) selectedAnswerRecordIds.add(recordId);
        else selectedAnswerRecordIds.delete(recordId);
        checkbox.closest(".answer-record-admin-row")?.classList.toggle("selected", checkbox.checked);
        updateSelectionUi();
      });
    });
    adminApp.querySelector('[data-action="select-all-answer-records"]')?.addEventListener("change", (event) => {
      const checked = event.currentTarget.checked;
      visibleCheckboxes().forEach((checkbox) => {
        checkbox.checked = checked;
        const recordId = checkbox.dataset.recordId;
        if (!recordId) return;
        if (checked) selectedAnswerRecordIds.add(recordId);
        else selectedAnswerRecordIds.delete(recordId);
        checkbox.closest(".answer-record-admin-row")?.classList.toggle("selected", checked);
      });
      updateSelectionUi();
    });
    batchButton?.addEventListener("click", async () => {
      try {
        await setSelectedAnswerRecordsArchived(!showArchivedRecords);
      } catch (error) {
        window.alert(error instanceof Error ? error.message : "答题记录处理失败。");
      }
    });
    adminApp.querySelector('[data-action="toggle-archived-records"]')?.addEventListener("click", () => {
      showArchivedRecords = !showArchivedRecords;
      selectedAnswerRecordIds.clear();
      statusMessage = "";
      render();
    });
    adminApp.querySelector('[data-action="export-answer-records"]')?.addEventListener("click", exportAnswerRecords);
    const recordFileInput = adminApp.querySelector("#answer-record-file");
    adminApp.querySelector('[data-action="import-answer-records"]')?.addEventListener("click", () => recordFileInput?.click());
    recordFileInput?.addEventListener("change", async () => {
      const file = recordFileInput.files?.[0];
      if (!file) return;
      try {
        await importAnswerRecordsFromFile(file);
      } catch (error) {
        window.alert(error instanceof Error ? error.message : "答题记录导入失败。");
      } finally {
        recordFileInput.value = "";
      }
    });
    
};

const wireLeaderboardEvents = () => {
  const leaderboardEditor = adminApp.querySelector("#leaderboard-editor");
  leaderboardEditor?.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await saveLeaderboardEntries(readLeaderboardForm(leaderboardEditor));
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "排行榜保存失败。");
    }
  });
  adminApp.querySelectorAll('[data-action="delete-entry"]').forEach((button) => {
    button.addEventListener("click", async () => {
      const index = Number(button.dataset.entryIndex);
      if (!window.confirm("确定删除这条成绩吗？")) return;
      try {
        await saveLeaderboardEntries(leaderboard.filter((_, entryIndex) => entryIndex !== index));
      } catch (error) {
        window.alert(error instanceof Error ? error.message : "删除失败。");
      }
    });
  });
  adminApp.querySelector('[data-action="clear-leaderboard"]')?.addEventListener("click", async () => {
    if (!window.confirm("确定清空全部排行榜记录吗？旧文件会先自动备份。")) return;
    try {
      await saveLeaderboardEntries([]);
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "清空失败。");
    }
  });
  const addForm = adminApp.querySelector("#leaderboard-add");
  addForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const formData = new FormData(addForm);
    const name = formData.get("name").toString().trim();
    const score = Number(formData.get("score"));
    if (!name || !Number.isFinite(score)) return;
    try {
      await saveLeaderboardEntries([...leaderboard, { name, score, createdAt: Date.now() }]);
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "新增失败。");
    }
  });
};
