// admin-reviews.js: quick review and duplicate-review workflows
// Classic script module; shared state and API contracts remain in admin.js.

const getAvailableReviewArticles = () => getCatalog().filter((article) => (
  reviewFilters.volume === "all" || article.volume === reviewFilters.volume
));

const getReviewFilteredQuestions = () => {
  const keyword = reviewFilters.query.trim().toLowerCase();
  return bank.questions.filter((question) => {
    const review = getQuestionReview(question.id);
    const duplicateReview = getDuplicateReview(question);
    if (reviewFilters.volume !== "all" && question.volume !== reviewFilters.volume) return false;
    if (reviewFilters.articleId !== "all" && question.articleId !== reviewFilters.articleId) return false;
    if (reviewFilters.status !== "all" && review.status !== reviewFilters.status) return false;
    if (reviewFilters.duplicate === "pending" && !isDuplicateReviewPending(question)) return false;
    if (reviewFilters.duplicate === "resolved" && !["kept", "skipped"].includes(duplicateReview?.status)) return false;
    if (!keyword) return true;
    return [
      question.number,
      question.word,
      question.article,
      question.sentence,
      question.explanation,
      ...(question.options || []).map((option) => option.text),
    ].join(" ").toLowerCase().includes(keyword);
  }).sort((left, right) => {
    const leftDuplicatePending = isDuplicateReviewPending(left) ? 0 : 1;
    const rightDuplicatePending = isDuplicateReviewPending(right) ? 0 : 1;
    const leftPending = getQuestionReview(left.id).status === "pending" ? 0 : 1;
    const rightPending = getQuestionReview(right.id).status === "pending" ? 0 : 1;
    return leftDuplicatePending - rightDuplicatePending
      || leftPending - rightPending
      || (Number(left.number) || 0) - (Number(right.number) || 0)
      || String(left.id).localeCompare(String(right.id));
  });
};

const getReviewCounts = () => {
  const counts = { pending: 0, passed: 0, needs_revision: 0, skipped: 0 };
  bank.questions.forEach((question) => {
    const status = getQuestionReview(question.id).status;
    if (Object.hasOwn(counts, status)) counts[status] += 1;
  });
  return counts;
};

const getDuplicateComparisonQuestions = (question) => {
  const duplicateReview = getDuplicateReview(question);
  if (!duplicateReview) return [];
  const relatedIds = new Set(duplicateReview.relatedQuestionIds);
  return bank.questions.filter((candidate) => relatedIds.has(candidate.id));
};

const renderDuplicateComparison = (question) => {
  const duplicateReview = getDuplicateReview(question);
  if (!duplicateReview) return "";
  const relatedQuestions = getDuplicateComparisonQuestions(question);
  const statusMeta = DUPLICATE_REVIEW_STATUS_META[duplicateReview.status];
  return `
    <details class="duplicate-comparison" ${duplicateReview.status === "pending" ? "open" : ""}>
      <summary><span>${statusMeta.label}</span><strong>查看同组 ${relatedQuestions.length} 道题对比</strong></summary>
      <div class="duplicate-comparison-list">
        ${relatedQuestions.map((candidate) => `
          <div class="duplicate-comparison-item ${candidate.id === question.id ? "current" : ""}">
            <div class="duplicate-comparison-heading"><strong>#${escapeHtml(candidate.number)} · ${candidate.id === question.id ? "当前题目" : "关联题"}</strong><span>${escapeHtml(getDuplicateReview(candidate)?.status ? DUPLICATE_REVIEW_STATUS_META[getDuplicateReview(candidate).status].label : "普通题")}</span></div>
            <p><span>原句：</span>${escapeHtml(candidate.sentence)}</p>
            <p><span>考点：</span>${escapeHtml(candidate.word)} · 第 ${escapeHtml(candidate.targetOccurrence || 1)} 处</p>
            <p><span>选项：</span>${escapeHtml((candidate.options || []).map((option) => option.text).join("｜"))}</p>
            <p><span>答案：</span>${escapeHtml(optionText(candidate, candidate.answer))}</p>
            <p><span>解析：</span>${escapeHtml(candidate.explanation)}</p>
          </div>
        `).join("")}
      </div>
    </details>
  `;
};

const renderReviewOption = (question, option, review) => {
  const isAnswer = option.key === question.answer;
  const hasIssue = review.optionIssues.includes(option.key);
  return `
    <div class="review-option ${isAnswer ? "system-answer" : ""} ${hasIssue ? "has-issue" : ""}">
      <span class="review-option-key">${escapeHtml(option.key)}</span>
      <span class="review-option-text">${escapeHtml(option.text)}</span>
      ${isAnswer ? `<span class="review-answer-badge">系统答案</span>` : ""}
      ${hasIssue ? `<span class="review-issue-badge">有问题</span>` : ""}
    </div>
  `;
};

const renderReviewIssuePanel = (question, review) => `
  <div class="review-issue-panel">
    <div>
      <h3>记录这道题的问题</h3>
      <p>先保存审查结论；题目内容需要修改时，可再进入“题库管理”。</p>
    </div>
    <form class="review-issue-form" data-review-form data-question-id="${escapeHtml(question.id)}">
      <label class="editor-field">建议正确答案（可选）
        <select class="admin-select" name="suggestedAnswer">
          <option value="">尚未确定</option>
          ${["A", "B", "C", "D"].map((key) => `<option value="${key}" ${review.suggestedAnswer === key ? "selected" : ""}>${key}：${escapeHtml(optionText(question, key))}</option>`).join("")}
        </select>
      </label>
      <fieldset class="review-optional-issues">
        <legend>干扰项问题（可选，不影响快速审查）</legend>
        <div class="review-issue-checkboxes">
          ${question.options.map((option) => `
            <label><input type="checkbox" name="optionIssue" value="${escapeHtml(option.key)}" ${review.optionIssues.includes(option.key) ? "checked" : ""} /> ${escapeHtml(option.key)} 有问题</label>
          `).join("")}
        </div>
      </fieldset>
      <label class="editor-field full">审查备注（可选）
        <textarea class="admin-textarea" name="note" maxlength="1000" placeholder="例如：正确答案应为 C；原句或释义需要核对。">${escapeHtml(review.note)}</textarea>
      </label>
      <div class="review-issue-actions">
        <button class="admin-primary" type="submit">保存审查备注</button>
        <button class="admin-secondary" type="button" data-action="open-question-editor" data-question-id="${escapeHtml(question.id)}">去题库管理修改</button>
      </div>
    </form>
  </div>
`;

const renderReviewSentence = (question) => {
  return renderTargetSentence(question.sentence, question.word, question.targetOccurrence, "review-target-word");
};

const renderReviewCard = (question) => {
  const review = getQuestionReview(question.id);
  const duplicateReview = getDuplicateReview(question);
  const meta = getReviewStatusMeta(review.status);
  const isSaving = savingReviewIds.has(question.id);
  const expanded = expandedReviewId === question.id;
  const sourceTitle = question.source?.title || "暂无来源说明";
  const occurrenceCount = getWordOccurrences(question.sentence, question.word).length;
  const occurrenceLabel = occurrenceCount > 1 ? ` · 第 ${Number(question.targetOccurrence) || 1} 处/共 ${occurrenceCount} 处` : "";
  const availability = getQuestionAvailability(question);
  let actions = "";

  if (review.status === "pending") {
    actions = `
      <button class="review-action review-action-primary" type="button" data-review-action="pass" data-question-id="${escapeHtml(question.id)}">确认正确</button>
      <button class="review-action review-action-warning" type="button" data-review-action="needs_revision" data-question-id="${escapeHtml(question.id)}">答案有误</button>
      <button class="review-action review-action-quiet" type="button" data-review-action="skip" data-question-id="${escapeHtml(question.id)}">跳过</button>
    `;
  } else if (review.status === "passed") {
    actions = `
      <button class="review-action review-action-warning" type="button" data-review-action="needs_revision" data-question-id="${escapeHtml(question.id)}">发现问题</button>
      <button class="review-action review-action-quiet" type="button" data-review-action="reset" data-question-id="${escapeHtml(question.id)}">恢复待审</button>
    `;
  } else if (review.status === "needs_revision") {
    actions = `
      <button class="review-action review-action-primary" type="button" data-review-action="pass" data-question-id="${escapeHtml(question.id)}">复核通过</button>
      <button class="review-action review-action-quiet" type="button" data-review-action="reset" data-question-id="${escapeHtml(question.id)}">恢复待审</button>
    `;
  } else {
    actions = `
      <button class="review-action review-action-primary" type="button" data-review-action="pass" data-question-id="${escapeHtml(question.id)}">确认正确</button>
      <button class="review-action review-action-warning" type="button" data-review-action="needs_revision" data-question-id="${escapeHtml(question.id)}">答案有误</button>
    `;
  }

  const duplicateActions = duplicateReview?.status === "pending"
    ? `
      <button class="review-action review-action-primary" type="button" data-duplicate-action="keep" data-question-id="${escapeHtml(question.id)}">确认保留</button>
      <button class="review-action review-action-warning" type="button" data-duplicate-action="skip" data-question-id="${escapeHtml(question.id)}">标记跳过</button>
    `
    : duplicateReview
      ? `<button class="review-action review-action-quiet" type="button" data-duplicate-action="reset" data-question-id="${escapeHtml(question.id)}">恢复重复待审</button>`
      : "";

  return `
    <article class="admin-card review-card review-card-${meta.className}" id="review-card-${escapeHtml(question.id)}" data-review-id="${escapeHtml(question.id)}">
      <header class="review-card-header">
        <div class="review-card-identity">
          <span class="review-status-pill review-status-${meta.className}">${meta.label}</span>
          ${isQuestionAbnormal(question) ? `<span class="question-abnormal-badge">划线异常</span>` : ""}
          ${duplicateReview ? `<span class="duplicate-review-badge duplicate-review-${duplicateReview.status}">${DUPLICATE_REVIEW_STATUS_META[duplicateReview.status].label}</span>` : ""}
          <span class="review-availability review-availability-${availability.className}">${availability.label}</span>
          <strong class="review-number">#${escapeHtml(question.number)}</strong>
          <span class="review-word">考查实词：${escapeHtml(question.word)}${escapeHtml(occurrenceLabel)}</span>
          <span class="review-source-label">${escapeHtml(question.volume)} · ${escapeHtml(formatArticleLabel(question.article))}</span>
        </div>
        <div class="review-card-actions ${isSaving ? "is-saving" : ""}">
          ${actions.replaceAll("<button ", `<button ${isSaving ? "disabled " : ""}`)}
          ${duplicateActions}
        </div>
      </header>
      <div class="review-card-body">
        <p class="review-sentence">${renderReviewSentence(question)}</p>
        <div class="review-options" aria-label="四个释义选项">
          ${question.options.map((option) => renderReviewOption(question, option, review)).join("")}
        </div>
        <details class="review-reference">
          <summary>查看当前解析与来源</summary>
          <div class="review-reference-content">
            <p><span>当前解析：</span>${escapeHtml(question.explanation)}</p>
            <p><span>题库来源：</span>${escapeHtml(sourceTitle)}</p>
          </div>
        </details>
        ${renderDuplicateComparison(question)}
        ${review.note ? `<p class="review-note"><span>审查备注：</span>${escapeHtml(review.note)}</p>` : ""}
      </div>
      ${expanded ? renderReviewIssuePanel(question, review) : ""}
    </article>
  `;
};

const renderReviewTab = () => {
  const counts = getReviewCounts();
  const duplicateCount = getDuplicateReviewCount();
  const questions = getReviewFilteredQuestions();
  return `
    <section class="review-workspace" aria-label="快速审查题目">
      <section class="admin-card review-summary">
        <div class="review-summary-heading">
          <div>
            <h2>连续审查题目</h2>
            <p>题目按顺序全部展开，直接滚动查看；正确答案优先审查，干扰项问题可以不记录。</p>
          </div>
          <span class="review-summary-total">共 ${bank.questions.length} 道题</span>
        </div>
        <div class="review-stat-grid">
          ${["pending", "passed", "needs_revision", "skipped"].map((status) => `
            <div class="review-stat review-stat-${REVIEW_STATUS_META[status].className}">
              <span>${REVIEW_STATUS_META[status].label}</span><strong>${counts[status]}</strong>
            </div>
          `).join("")}
          <div class="review-stat review-stat-duplicate">
            <span>重复候选</span><strong>${duplicateCount}</strong>
          </div>
        </div>
      </section>
      <section class="admin-card review-toolbar">
        <div class="review-toolbar-row">
          <div class="review-toolbar-title"><strong>题目列表</strong><span>${questions.length} 道符合条件</span></div>
          ${statusMessage ? `<span class="review-save-message" role="status">${escapeHtml(statusMessage)}</span>` : ""}
        </div>
        <div class="review-filters">
          <select class="admin-select" id="review-volume" aria-label="按教材册筛选">
            <option value="all" ${reviewFilters.volume === "all" ? "selected" : ""}>全部教材册</option>
            ${getVolumes().map((volume) => `<option value="${escapeHtml(volume)}" ${reviewFilters.volume === volume ? "selected" : ""}>${escapeHtml(volume)}</option>`).join("")}
          </select>
          <select class="admin-select" id="review-article" aria-label="按文章筛选">
            <option value="all">全部文章</option>
            ${getAvailableReviewArticles().map((article) => `<option value="${escapeHtml(article.id)}" ${reviewFilters.articleId === article.id ? "selected" : ""}>${escapeHtml(article.title)}</option>`).join("")}
          </select>
          <select class="admin-select" id="review-status" aria-label="按审查状态筛选">
            <option value="all" ${reviewFilters.status === "all" ? "selected" : ""}>全部状态</option>
            ${["pending", "passed", "needs_revision", "skipped"].map((status) => `<option value="${status}" ${reviewFilters.status === status ? "selected" : ""}>${REVIEW_STATUS_META[status].label}</option>`).join("")}
          </select>
          <select class="admin-select" id="review-duplicate" aria-label="按重复候选筛选">
            <option value="all" ${reviewFilters.duplicate === "all" ? "selected" : ""}>全部重复状态</option>
            <option value="pending" ${reviewFilters.duplicate === "pending" ? "selected" : ""}>重复候选</option>
            <option value="resolved" ${reviewFilters.duplicate === "resolved" ? "selected" : ""}>已处理重复题</option>
          </select>
          <input class="admin-input" id="review-search" type="search" value="${escapeHtml(reviewFilters.query)}" placeholder="搜索题号、实词、原句或释义" />
        </div>
      </section>
      <div class="review-feed">
        ${questions.length ? questions.map(renderReviewCard).join("") : `<section class="admin-card review-empty">没有符合条件的题目。</section>`}
      </div>
    </section>
  `;
};

const renderKeepingScroll = () => {
  const scrollTop = window.scrollY;
  render();
  window.scrollTo(0, scrollTop);
};

const buildReview = (questionId, status, overrides = {}) => {
  const current = getQuestionReview(questionId);
  const review = {
    ...current,
    status,
    answerCorrect: status === "passed" ? true : status === "needs_revision" ? false : null,
    reviewedAt: new Date().toISOString(),
    ...overrides,
  };
  if (status === "passed") {
    review.suggestedAnswer = null;
    review.optionIssues = [];
    review.note = "";
  }
  return review;
};

const persistReview = async (questionId, review, message, expand = false) => {
  if (savingReviewIds.has(questionId)) return;
  const card = document.getElementById(`review-card-${questionId}`);
  card?.querySelectorAll("button").forEach((button) => { button.disabled = true; });
  savingReviewIds.add(questionId);
  try {
    const payload = await patchJson(API.questionReviews, { questionId, review });
    reviews = normalizeReviews(payload);
    const previousQuestion = getQuestion(questionId);
    bank = await fetchJson(API.questions);
    const savedQuestion = getQuestion(questionId);
    const wasPendingPublication = review.status === "passed"
      && ["candidate", "admin_created", "admin_edited"].includes(previousQuestion?.reviewStatus)
      && savedQuestion?.reviewStatus === "verified";
    if (wasPendingPublication) {
      message = `${message} 已发布到学生端。`;
    }
    expandedReviewId = expand ? questionId : expandedReviewId === questionId ? null : expandedReviewId;
    statusMessage = message;
    savingReviewIds.delete(questionId);
    renderKeepingScroll();
  } catch (error) {
    savingReviewIds.delete(questionId);
    card?.querySelectorAll("button").forEach((button) => { button.disabled = false; });
    throw error;
  }
};

const saveDuplicateReviewStatus = async (questionId, status) => {
  if (!Object.hasOwn(DUPLICATE_REVIEW_STATUS_META, status)) throw new Error("重复题审查状态无效。");
  const current = bank.questions.find((question) => question.id === questionId);
  const duplicateReview = getDuplicateReview(current);
  if (!current || !duplicateReview) throw new Error("找不到这道重复候选题。");
  bank = await putJson(API.questions, {
    ...bank,
    questions: bank.questions.map((question) => question.id === questionId
      ? { ...question, duplicateReview: { ...duplicateReview, status } }
      : question),
  });
  const savedQuestion = bank.questions.find((question) => question.id === questionId) || current;
  statusMessage = status === "kept"
    ? isQuestionAbnormal(savedQuestion)
      ? `已确认保留第 ${current.number} 题，但它仍有划线异常，修复前学生端不会抽到。`
      : `已确认保留第 ${current.number} 题，学生端可以抽到（前提是训练范围包含它）。`
    : status === "skipped"
      ? `已标记跳过第 ${current.number} 题；题目仍保留在题库中。`
      : `第 ${current.number} 题已恢复为重复候选。`;
  renderKeepingScroll();
};

const saveReviewForm = async (form) => {
  const questionId = form.dataset.questionId;
  const formData = new FormData(form);
  const suggestedAnswer = formData.get("suggestedAnswer").toString();
  const review = buildReview(questionId, "needs_revision", {
    suggestedAnswer: suggestedAnswer || null,
    optionIssues: formData.getAll("optionIssue").map((key) => key.toString()),
    note: formData.get("note").toString().trim(),
  });
  await persistReview(questionId, review, "审查备注已保存。", true);
};

const wireReviewEvents = () => {
  const volume = adminApp.querySelector("#review-volume");
  const article = adminApp.querySelector("#review-article");
  const status = adminApp.querySelector("#review-status");
  const duplicate = adminApp.querySelector("#review-duplicate");
  const search = adminApp.querySelector("#review-search");

  volume?.addEventListener("change", () => {
    reviewFilters = { ...reviewFilters, volume: volume.value, articleId: "all" };
    statusMessage = "";
    render();
  });
  article?.addEventListener("change", () => {
    reviewFilters = { ...reviewFilters, articleId: article.value };
    statusMessage = "";
    render();
  });
  status?.addEventListener("change", () => {
    reviewFilters = { ...reviewFilters, status: status.value };
    statusMessage = "";
    render();
  });
  duplicate?.addEventListener("change", () => {
    reviewFilters = { ...reviewFilters, duplicate: duplicate.value };
    statusMessage = "";
    render();
  });
  search?.addEventListener("change", () => {
    reviewFilters = { ...reviewFilters, query: search.value };
    statusMessage = "";
    render();
  });
  search?.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    reviewFilters = { ...reviewFilters, query: search.value };
    statusMessage = "";
    render();
  });

  adminApp.querySelectorAll("[data-review-action]").forEach((button) => {
    button.addEventListener("click", async () => {
      const questionId = button.dataset.questionId;
      const action = button.dataset.reviewAction;
      if (!questionId || !action) return;
      try {
        if (action === "pass") {
          await persistReview(questionId, buildReview(questionId, "passed"), "已确认答案正确。");
        } else if (action === "needs_revision") {
          await persistReview(questionId, buildReview(questionId, "needs_revision"), "已标记为待修改。", true);
        } else if (action === "skip") {
          await persistReview(questionId, buildReview(questionId, "skipped"), "已跳过这道题。");
        } else if (action === "reset") {
          await persistReview(questionId, buildReview(questionId, "pending"), "已恢复为待审。");
        }
      } catch (error) {
        window.alert(error instanceof Error ? error.message : "保存审查结果失败。");
      }
    });
  });

  adminApp.querySelectorAll("[data-duplicate-action]").forEach((button) => {
    button.addEventListener("click", async () => {
      const questionId = button.dataset.questionId;
      const action = button.dataset.duplicateAction;
      const statusByAction = { keep: "kept", skip: "skipped", reset: "pending" };
      if (!questionId || !statusByAction[action]) return;
      button.disabled = true;
      try {
        await saveDuplicateReviewStatus(questionId, statusByAction[action]);
      } catch (error) {
        button.disabled = false;
        window.alert(error instanceof Error ? error.message : "保存重复题审查结果失败。");
      }
    });
  });

  adminApp.querySelectorAll("[data-review-form]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const submitButton = form.querySelector('button[type="submit"]');
      if (submitButton) {
        submitButton.disabled = true;
        submitButton.textContent = "正在保存…";
      }
      try {
        await saveReviewForm(form);
      } catch (error) {
        if (submitButton) {
          submitButton.disabled = false;
          submitButton.textContent = "保存审查备注";
        }
        window.alert(error instanceof Error ? error.message : "保存审查备注失败。");
      }
    });
  });

  adminApp.querySelectorAll('[data-action="open-question-editor"]').forEach((button) => {
    button.addEventListener("click", () => {
      selectedQuestionId = button.dataset.questionId;
      activeTab = "questions";
      expandedReviewId = null;
      statusMessage = "";
      render();
      window.scrollTo(0, 0);
    });
  });
};

