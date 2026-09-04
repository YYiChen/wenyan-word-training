const adminApp = document.querySelector("#admin-app");

const API = {
  questions: "./api/questions",
  leaderboard: "./api/leaderboard",
  questionReviews: "./api/question-reviews",
};

// 这是防止学生误入后台的前端门槛，不是服务器级安全验证。
const ADMIN_PASSWORD = "pc123456";

const QUESTION_TYPES = [
  ["context_meaning", "语境释义题"],
  ["single_choice", "普通单选题"],
  ["select_correct", "选择正确项"],
  ["select_incorrect", "选择错误项"],
];

let bank = null;
let leaderboard = [];
let reviews = {};
let activeTab = "review";
let selectedQuestionId = null;
let creatingQuestion = false;
let filters = { volume: "all", articleId: "all", query: "" };
let reviewFilters = { volume: "all", articleId: "all", status: "all", query: "" };
let expandedReviewId = null;
const savingReviewIds = new Set();
let statusMessage = "";
let loginError = "";
let adminAuthorized = false;

const REVIEW_STATUS_META = {
  pending: { label: "待审", className: "pending" },
  passed: { label: "已确认", className: "passed" },
  needs_revision: { label: "待修改", className: "needs-revision" },
  skipped: { label: "已跳过", className: "skipped" },
};

const escapeHtml = (value) => String(value ?? "")
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#039;");

const normalizeLeaderboard = (entries) => (Array.isArray(entries) ? entries : [])
  .filter((entry) => entry && typeof entry.name === "string" && Number.isFinite(Number(entry.score)))
  .map((entry) => ({
    name: entry.name.trim().slice(0, 20),
    score: Number(entry.score),
    createdAt: Number(entry.createdAt) || 0,
  }))
  .filter((entry) => entry.name)
  .sort((left, right) => right.score - left.score || right.createdAt - left.createdAt);

const normalizeReview = (review) => {
  const status = Object.hasOwn(REVIEW_STATUS_META, review?.status) ? review.status : "pending";
  const optionIssues = Array.isArray(review?.optionIssues)
    ? review.optionIssues.filter((key) => ["A", "B", "C", "D"].includes(key))
    : [];
  return {
    status,
    answerCorrect: typeof review?.answerCorrect === "boolean" ? review.answerCorrect : null,
    suggestedAnswer: ["A", "B", "C", "D"].includes(review?.suggestedAnswer) ? review.suggestedAnswer : null,
    optionIssues: [...new Set(optionIssues)],
    note: String(review?.note || "").trim().slice(0, 1000),
    reviewedAt: String(review?.reviewedAt || "").trim().slice(0, 40),
  };
};

const normalizeReviews = (payload) => {
  const source = payload && typeof payload.reviews === "object" && payload.reviews !== null
    ? payload.reviews
    : {};
  return Object.fromEntries(Object.entries(source).map(([questionId, review]) => [
    questionId,
    normalizeReview(review),
  ]));
};

const getQuestionReview = (questionId) => reviews[questionId] || normalizeReview(null);

const getReviewStatusMeta = (status) => REVIEW_STATUS_META[status] || REVIEW_STATUS_META.pending;

const hasAdminAccess = () => adminAuthorized;

const renderLogin = () => {
  adminApp.innerHTML = `
    <section class="admin-loading" aria-labelledby="admin-login-title">
      <div class="admin-login-card">
        <p class="eyebrow">文言实词 · 管理后台</p>
        <h1 id="admin-login-title">请输入管理密码</h1>
        <p class="admin-subtitle">每次进入管理后台都需要输入密码；离开或退出后台后，授权立即失效。</p>
        <form class="admin-login-form" id="admin-login-form">
          <label class="editor-field" for="admin-password">管理密码
            <input class="admin-input" id="admin-password" name="password" type="password" autocomplete="current-password" required autofocus />
          </label>
          ${loginError ? `<p class="admin-login-error" role="alert">${escapeHtml(loginError)}</p>` : ""}
          <button class="admin-primary" type="submit">进入管理后台</button>
        </form>
        <a class="admin-home-link" href="./index.html">返回学生答题页</a>
      </div>
    </section>
  `;
  adminApp.querySelector("#admin-login-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const password = new FormData(event.currentTarget).get("password").toString();
    if (password !== ADMIN_PASSWORD) {
      loginError = "密码不正确，请重新输入。";
      renderLogin();
      return;
    }
    adminAuthorized = true;
    loginError = "";
    load();
  });
};

const getCatalog = () => Array.isArray(bank?.catalog) ? bank.catalog : [];
const getQuestion = (id) => bank?.questions.find((question) => question.id === id) || null;
const getArticle = (id) => getCatalog().find((article) => article.id === id) || null;

const formatArticleLabel = (title) => {
  const value = String(title || "课内文章");
  return value.startsWith("《") ? value : `《${value}》`;
};

const getNextQuestionNumber = () => Math.max(
  0,
  ...bank.questions.map((question) => Number(question.number) || 0),
) + 1;

const createQuestionId = () => {
  const usedIds = new Set(bank.questions.map((question) => question.id));
  let candidate = "";
  do {
    const token = window.crypto?.randomUUID?.().replaceAll("-", "")
      || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
    candidate = `custom-${token}`;
  } while (usedIds.has(candidate));
  return candidate;
};

const reindexQuestions = (questions) => questions.map((question, index) => ({
  ...question,
  number: index + 1,
}));

const createDraftQuestion = () => {
  const article = getCatalog()[0] || {};
  return {
    id: "",
    type: "context_meaning",
    articleId: article.id || "",
    article: article.title || "",
    volume: article.volume || "",
    unit: article.unit || "",
    word: "",
    sentence: "",
    stem: "",
    explanation: "",
    answer: "A",
    number: getNextQuestionNumber(),
    options: ["A", "B", "C", "D"].map((key) => ({ key, text: "" })),
  };
};

const fetchJson = async (url) => {
  const response = await fetch(url, { cache: "no-store" });
  const payload = await response.json().catch(() => null);
  if (!response.ok) throw new Error(payload?.error || `读取失败（${response.status}）。`);
  return payload;
};

const putJson = async (url, value) => {
  const response = await fetch(url, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(value),
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok || !payload?.ok) throw new Error(payload?.error || "保存失败。");
  return payload.data;
};

const patchJson = async (url, value) => {
  const response = await fetch(url, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(value),
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok || !payload?.ok) throw new Error(payload?.error || "保存失败。");
  return payload.data;
};

const getVolumes = () => [...new Set(getCatalog().map((article) => article.volume))];
const getAvailableArticles = () => getCatalog().filter((article) => (
  filters.volume === "all" || article.volume === filters.volume
));

const getFilteredQuestions = () => {
  const keyword = filters.query.trim().toLowerCase();
  return bank.questions.filter((question) => {
    if (filters.volume !== "all" && question.volume !== filters.volume) return false;
    if (filters.articleId !== "all" && question.articleId !== filters.articleId) return false;
    if (!keyword) return true;
    return [question.word, question.article, question.sentence, question.explanation]
      .join(" ")
      .toLowerCase()
      .includes(keyword);
  });
};

const getAvailableReviewArticles = () => getCatalog().filter((article) => (
  reviewFilters.volume === "all" || article.volume === reviewFilters.volume
));

const getReviewFilteredQuestions = () => {
  const keyword = reviewFilters.query.trim().toLowerCase();
  return bank.questions.filter((question) => {
    const review = getQuestionReview(question.id);
    if (reviewFilters.volume !== "all" && question.volume !== reviewFilters.volume) return false;
    if (reviewFilters.articleId !== "all" && question.articleId !== reviewFilters.articleId) return false;
    if (reviewFilters.status !== "all" && review.status !== reviewFilters.status) return false;
    if (!keyword) return true;
    return [
      question.number,
      question.word,
      question.article,
      question.sentence,
      question.explanation,
      ...(question.options || []).map((option) => option.text),
    ].join(" ").toLowerCase().includes(keyword);
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
  const sentence = escapeHtml(question.sentence || "");
  const target = escapeHtml(question.word || "");
  return target ? sentence.replaceAll(target, `<mark class="review-target-word">${target}</mark>`) : sentence;
};

const renderReviewCard = (question) => {
  const review = getQuestionReview(question.id);
  const meta = getReviewStatusMeta(review.status);
  const isSaving = savingReviewIds.has(question.id);
  const expanded = expandedReviewId === question.id;
  const sourceTitle = question.source?.title || "暂无来源说明";
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

  return `
    <article class="admin-card review-card review-card-${meta.className}" id="review-card-${escapeHtml(question.id)}" data-review-id="${escapeHtml(question.id)}">
      <header class="review-card-header">
        <div class="review-card-identity">
          <span class="review-status-pill review-status-${meta.className}">${meta.label}</span>
          <strong class="review-number">#${escapeHtml(question.number)}</strong>
          <span class="review-word">考查实词：${escapeHtml(question.word)}</span>
          <span class="review-source-label">${escapeHtml(question.volume)} · ${escapeHtml(formatArticleLabel(question.article))}</span>
        </div>
        <div class="review-card-actions ${isSaving ? "is-saving" : ""}">
          ${actions.replaceAll("<button ", `<button ${isSaving ? "disabled " : ""}`)}
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
        ${review.note ? `<p class="review-note"><span>审查备注：</span>${escapeHtml(review.note)}</p>` : ""}
      </div>
      ${expanded ? renderReviewIssuePanel(question, review) : ""}
    </article>
  `;
};

const renderReviewTab = () => {
  const counts = getReviewCounts();
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
          <input class="admin-input" id="review-search" type="search" value="${escapeHtml(reviewFilters.query)}" placeholder="搜索题号、实词、原句或释义" />
        </div>
      </section>
      <div class="review-feed">
        ${questions.length ? questions.map(renderReviewCard).join("") : `<section class="admin-card review-empty">没有符合条件的题目。</section>`}
      </div>
    </section>
  `;
};

const renderQuestionList = () => {
  const questions = getFilteredQuestions();
  if (selectedQuestionId && !questions.some((question) => question.id === selectedQuestionId)) {
    selectedQuestionId = questions[0]?.id || null;
  }
  return `
    <aside class="admin-card question-browser" aria-label="题库列表">
      <div class="admin-card-header">
        <div class="admin-card-heading">
          <h2 class="admin-card-title">题库</h2>
          <span class="admin-count">${questions.length} 道符合条件</span>
        </div>
        <button class="admin-primary admin-compact-button" type="button" data-action="new-question">新增题目</button>
      </div>
      <div class="admin-filters">
        <select class="admin-select" id="admin-volume" aria-label="按教材册筛选">
          <option value="all" ${filters.volume === "all" ? "selected" : ""}>全部教材册</option>
          ${getVolumes().map((volume) => `<option value="${escapeHtml(volume)}" ${filters.volume === volume ? "selected" : ""}>${escapeHtml(volume)}</option>`).join("")}
        </select>
        <select class="admin-select" id="admin-article" aria-label="按文章筛选">
          <option value="all">全部文章</option>
          ${getAvailableArticles().map((article) => `<option value="${escapeHtml(article.id)}" ${filters.articleId === article.id ? "selected" : ""}>${escapeHtml(article.title)}</option>`).join("")}
        </select>
        <input class="admin-input" id="admin-search" type="search" value="${escapeHtml(filters.query)}" placeholder="搜索实词、原句或篇名" />
      </div>
      <div class="question-list">
        ${questions.length ? questions.map((question) => `
          <button class="question-list-item ${question.id === selectedQuestionId ? "selected" : ""}" type="button" data-question-id="${escapeHtml(question.id)}">
            <span class="question-list-topline"><span>#${question.number}</span><span>${escapeHtml(question.word)}</span></span>
            <span class="question-list-meta">${escapeHtml(formatArticleLabel(question.article))} · ${escapeHtml(question.sentence)}</span>
          </button>
        `).join("") : `<p class="editor-empty">没有找到题目。</p>`}
      </div>
    </aside>
  `;
};

const optionText = (question, key) => question.options.find((option) => option.key === key)?.text || "";

const renderQuestionEditor = () => {
  const question = creatingQuestion ? createDraftQuestion() : getQuestion(selectedQuestionId);
  if (!question) {
    return `<section class="admin-card editor-card"><div class="editor-empty">从左侧选择一道题，即可查看和修改。</div></section>`;
  }
  return `
    <section class="admin-card editor-card" aria-label="题目编辑器">
      <div class="editor-title-row">
        <h2>${creatingQuestion ? "新增题目" : `编辑第 ${question.number} 题`}</h2>
        ${statusMessage ? `<span class="editor-status">${escapeHtml(statusMessage)}</span>` : ""}
      </div>
      <form id="question-editor">
        <div class="editor-grid">
          <label class="editor-field">题型
            <select class="admin-select" name="type">
              ${QUESTION_TYPES.map(([value, label]) => `<option value="${value}" ${question.type === value ? "selected" : ""}>${label}</option>`).join("")}
            </select>
          </label>
          <label class="editor-field">考查实词
            <input class="admin-input" name="word" value="${escapeHtml(question.word)}" maxlength="12" required />
          </label>
          <label class="editor-field full">所属文章
            <select class="admin-select" name="articleId">
              ${getCatalog().map((article) => `<option value="${escapeHtml(article.id)}" ${article.id === question.articleId ? "selected" : ""}>${escapeHtml(article.volume)} · ${escapeHtml(formatArticleLabel(article.title))}</option>`).join("")}
            </select>
          </label>
          <label class="editor-field full">原句
            <textarea class="admin-textarea" name="sentence" required>${escapeHtml(question.sentence)}</textarea>
          </label>
          <label class="editor-field full">额外题干（普通单选题可使用）
            <textarea class="admin-textarea" name="stem" placeholder="语境释义题可留空">${escapeHtml(question.stem || "")}</textarea>
            <span class="editor-help">当前“语境释义题”会展示原句；切换其他题型后可用这里的内容替代原句作为题干。</span>
          </label>
          <label class="editor-field">正确答案
            <select class="admin-select" name="answer">
              ${["A", "B", "C", "D"].map((key) => `<option value="${key}" ${question.answer === key ? "selected" : ""}>${key}</option>`).join("")}
            </select>
          </label>
          <label class="editor-field">题目编号
            <input class="admin-input" value="${creatingQuestion ? "保存时自动生成" : question.number}" disabled />
          </label>
          <label class="editor-field full">解析
            <textarea class="admin-textarea" name="explanation" required>${escapeHtml(question.explanation)}</textarea>
          </label>
        </div>
        <div class="options-editor">
          <h3>四个选项</h3>
          ${["A", "B", "C", "D"].map((key) => `
            <label class="option-editor-row"><span class="option-editor-key">${key}</span>
              <input class="admin-input" name="option-${key}" value="${escapeHtml(optionText(question, key))}" maxlength="80" required />
            </label>
          `).join("")}
        </div>
        <div class="editor-actions">
          <button class="admin-primary" type="submit">${creatingQuestion ? "新增这道题" : "保存这道题"}</button>
          <button class="admin-secondary" type="button" data-action="reload-question">放弃未保存修改</button>
          ${creatingQuestion ? "" : `<button class="admin-danger" type="button" data-action="delete-question">删除这道题</button>`}
        </div>
      </form>
    </section>
  `;
};

const renderQuestionTools = () => `
  <section class="admin-card question-tools" aria-label="题库文件工具">
    <div class="question-tools-copy">
      <h2 class="admin-card-title">题库文件</h2>
      <p>导出或导入整份题库 JSON；导入会替换当前题库，保存前会自动备份原文件。</p>
    </div>
    <div class="question-tools-actions">
      <button class="admin-secondary" type="button" data-action="export-bank">导出当前题库 JSON</button>
      <button class="admin-primary" type="button" data-action="import-bank">导入题库 JSON</button>
      <input id="question-bank-file" type="file" accept="application/json,.json" hidden />
    </div>
  </section>
`;

const renderQuestionTab = () => `${renderQuestionTools()}<div class="admin-grid">${renderQuestionList()}${renderQuestionEditor()}</div>`;

const renderLeaderboardTab = () => `
  <section class="leaderboard-layout">
    <section class="admin-card leaderboard-card-admin">
      <div class="admin-card-header"><h2 class="admin-card-title">排行榜</h2><span class="admin-count">${leaderboard.length} 条记录</span></div>
      <form id="leaderboard-editor">
        <table class="leaderboard-table">
          <thead><tr><th>排名</th><th>姓名</th><th>分数</th><th>操作</th></tr></thead>
          <tbody>
            ${leaderboard.length ? leaderboard.map((entry, index) => `
              <tr data-entry-index="${index}" data-created-at="${entry.createdAt}">
                <td>${index + 1}</td>
                <td><input class="admin-input" name="entry-name-${index}" value="${escapeHtml(entry.name)}" maxlength="20" required /></td>
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

const render = () => {
  const isReviewTab = activeTab === "review";
  adminApp.innerHTML = `
    <header class="admin-header">
      <div>
        <p class="eyebrow">文言实词 · 管理后台</p>
        <h1>${isReviewTab ? "快速审查题目" : "管理题库与成绩"}</h1>
        <p class="admin-subtitle">${isReviewTab ? "按顺序滚动查看全部题目，优先核对系统标出的正确答案。" : "题库修改会写入应用数据；排行榜写入电脑用户目录，浏览器关闭、刷新或更换浏览器后仍会保留。"}</p>
      </div>
      <div class="admin-header-actions">
        <button class="admin-secondary" type="button" data-action="logout">退出后台</button>
        <a class="admin-home-link" href="./index.html">返回学生答题页</a>
      </div>
    </header>
    <p class="admin-notice">服务仅监听本机。题库备份在 <code>data/backups</code>；排行榜和排行榜备份在 <code>%LOCALAPPDATA%/WenyanQuiz</code>，升级应用压缩包不会覆盖排行榜。</p>
    <nav class="admin-tabs" aria-label="管理功能">
      <button class="admin-tab ${activeTab === "review" ? "active" : ""}" type="button" data-tab="review">快速审查</button>
      <button class="admin-tab ${activeTab === "questions" ? "active" : ""}" type="button" data-tab="questions">题库管理</button>
      <button class="admin-tab ${activeTab === "leaderboard" ? "active" : ""}" type="button" data-tab="leaderboard">排行榜管理</button>
    </nav>
    ${activeTab === "review" ? renderReviewTab() : activeTab === "questions" ? renderQuestionTab() : renderLeaderboardTab()}
  `;
  wireEvents();
};

const renderError = (message) => {
  adminApp.innerHTML = `
    <section class="admin-loading">
      <div class="loading-mark" aria-hidden="true">!</div>
      <p class="eyebrow">管理后台无法启动</p>
      <h1>请先启动本地服务</h1>
      <p class="admin-subtitle">${escapeHtml(message)}</p>
      <a class="admin-home-link" href="./index.html">返回答题页</a>
    </section>
  `;
};

const downloadBank = () => {
  const blob = new Blob([JSON.stringify(bank, null, 2)], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "文言实词题库-当前完整题库.json";
  link.click();
  URL.revokeObjectURL(url);
};

const importBankFromFile = async (file) => {
  let imported;
  try {
    imported = JSON.parse(await file.text());
  } catch (error) {
    throw new Error(`JSON 文件无法读取：${error instanceof Error ? error.message : "格式错误"}`);
  }
  if (!imported || typeof imported !== "object" || !Array.isArray(imported.questions)) {
    throw new Error("导入失败：文件必须是包含 questions 数组的完整题库 JSON。");
  }
  if (!Array.isArray(imported.catalog)) {
    throw new Error("导入失败：题库 JSON 缺少 catalog 教材目录。");
  }
  if (!window.confirm(`确定导入“${file.name}”吗？当前题库将被替换，原题库会先自动备份。`)) return false;
  bank = await putJson(API.questions, imported);
  selectedQuestionId = bank.questions[0]?.id || null;
  creatingQuestion = false;
  statusMessage = `已导入 ${bank.questions.length} 道题`;
  render();
  return true;
};

const saveQuestion = async (form) => {
  const current = creatingQuestion ? null : getQuestion(selectedQuestionId);
  if (!creatingQuestion && !current) return;
  const formData = new FormData(form);
  const article = getArticle(formData.get("articleId"));
  const options = ["A", "B", "C", "D"].map((key) => ({ key, text: formData.get(`option-${key}`).toString().trim() }));
  const optionTexts = options.map((option) => option.text);
  if (!article) throw new Error("请选择有效的所属文章。");
  if (!optionTexts.every(Boolean) || new Set(optionTexts).size !== 4) throw new Error("四个选项必须填写且不能重复。");

  const updated = {
    ...(current || {}),
    id: current?.id || createQuestionId(),
    number: current?.number || getNextQuestionNumber(),
    type: formData.get("type").toString(),
    word: formData.get("word").toString().trim(),
    articleId: article.id,
    article: article.title,
    volume: article.volume,
    unit: article.unit,
    sentence: formData.get("sentence").toString().trim(),
    explanation: formData.get("explanation").toString().trim(),
    answer: formData.get("answer").toString(),
    options,
    reviewStatus: current ? "admin_edited" : "admin_created",
    source: current?.source || {
      kind: "admin_created",
      title: "管理后台新增题目",
    },
  };
  const stem = formData.get("stem").toString().trim();
  if (stem) updated.stem = stem;
  else delete updated.stem;
  if (!updated.word || !updated.sentence || !updated.explanation) throw new Error("实词、原句和解析不能为空。");

  const nextQuestions = current
    ? bank.questions.map((question) => question.id === updated.id ? updated : question)
    : [...bank.questions, updated];
  const nextBank = {
    ...bank,
    questions: current ? nextQuestions : reindexQuestions(nextQuestions),
  };
  bank = await putJson(API.questions, nextBank);
  selectedQuestionId = updated.id;
  creatingQuestion = false;
  statusMessage = current ? "已保存到 questions.json" : "新题目已保存到 questions.json";
  render();
};

const deleteSelectedQuestion = async () => {
  const current = getQuestion(selectedQuestionId);
  if (!current) return;
  if (bank.questions.length <= 1) throw new Error("题库至少要保留一道题，不能删除最后一道题。");
  if (!window.confirm(`确定删除第 ${current.number} 题“${current.word}”吗？删除前会自动备份题库。`)) return;

  const currentIndex = bank.questions.findIndex((question) => question.id === current.id);
  const nextQuestions = reindexQuestions(bank.questions.filter((question) => question.id !== current.id));
  bank = await putJson(API.questions, { ...bank, questions: nextQuestions });
  creatingQuestion = false;
  selectedQuestionId = nextQuestions[Math.min(currentIndex, nextQuestions.length - 1)]?.id || null;
  statusMessage = "题目已删除，题库编号已重新整理";
  render();
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

const readLeaderboardForm = (form) => [...form.querySelectorAll("tbody tr[data-entry-index]")].map((row) => {
  const index = Number(row.dataset.entryIndex);
  return {
    name: form.elements[`entry-name-${index}`].value.trim(),
    score: Number(form.elements[`entry-score-${index}`].value),
    createdAt: Number(row.dataset.createdAt) || Date.now(),
  };
});

const saveLeaderboardEntries = async (entries) => {
  leaderboard = normalizeLeaderboard(await putJson(API.leaderboard, entries));
  statusMessage = "排行榜已写入电脑用户数据目录";
  render();
};

const wireReviewEvents = () => {
  const volume = adminApp.querySelector("#review-volume");
  const article = adminApp.querySelector("#review-article");
  const status = adminApp.querySelector("#review-status");
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

const wireEvents = () => {
  adminApp.querySelector('[data-action="logout"]')?.addEventListener("click", () => {
    adminAuthorized = false;
    loginError = "";
    renderLogin();
  });
  adminApp.querySelectorAll("[data-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      activeTab = button.dataset.tab;
      statusMessage = "";
      render();
    });
  });

  if (activeTab === "review") {
    wireReviewEvents();
    return;
  }

  if (activeTab === "questions") {
    const volume = adminApp.querySelector("#admin-volume");
    const article = adminApp.querySelector("#admin-article");
    const search = adminApp.querySelector("#admin-search");
    adminApp.querySelector('[data-action="new-question"]')?.addEventListener("click", () => {
      creatingQuestion = true;
      selectedQuestionId = null;
      statusMessage = "";
      render();
    });
    volume?.addEventListener("change", () => {
      filters = { ...filters, volume: volume.value, articleId: "all" };
      statusMessage = "";
      render();
    });
    article?.addEventListener("change", () => {
      filters = { ...filters, articleId: article.value };
      statusMessage = "";
      render();
    });
    search?.addEventListener("input", () => {
      filters = { ...filters, query: search.value };
      selectedQuestionId = null;
      creatingQuestion = false;
      render();
    });
    adminApp.querySelectorAll("[data-question-id]").forEach((button) => {
      button.addEventListener("click", () => {
        selectedQuestionId = button.dataset.questionId;
        creatingQuestion = false;
        statusMessage = "";
        render();
      });
    });
    const editor = adminApp.querySelector("#question-editor");
    editor?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const submitButton = editor.querySelector('button[type="submit"]');
      submitButton.disabled = true;
      submitButton.textContent = "正在保存…";
      try {
        await saveQuestion(editor);
      } catch (error) {
        submitButton.disabled = false;
        submitButton.textContent = creatingQuestion ? "新增这道题" : "保存这道题";
        window.alert(error instanceof Error ? error.message : "保存题目失败。");
      }
    });
    adminApp.querySelector('[data-action="reload-question"]')?.addEventListener("click", () => {
      if (creatingQuestion) {
        creatingQuestion = false;
        selectedQuestionId = bank.questions[0]?.id || null;
      }
      statusMessage = "";
      render();
    });
    adminApp.querySelector('[data-action="export-bank"]')?.addEventListener("click", downloadBank);
    const importButton = adminApp.querySelector('[data-action="import-bank"]');
    const fileInput = adminApp.querySelector("#question-bank-file");
    importButton?.addEventListener("click", () => fileInput?.click());
    fileInput?.addEventListener("change", async () => {
      const file = fileInput.files?.[0];
      if (!file) return;
      importButton.disabled = true;
      importButton.textContent = "正在导入…";
      try {
        const imported = await importBankFromFile(file);
        if (!imported) {
          importButton.disabled = false;
          importButton.textContent = "导入题库 JSON";
        }
      } catch (error) {
        window.alert(error instanceof Error ? error.message : "导入题库失败。");
        importButton.disabled = false;
        importButton.textContent = "导入题库 JSON";
      } finally {
        fileInput.value = "";
      }
    });
    adminApp.querySelector('[data-action="delete-question"]')?.addEventListener("click", async () => {
      try {
        await deleteSelectedQuestion();
      } catch (error) {
        window.alert(error instanceof Error ? error.message : "删除题目失败。");
      }
    });
    return;
  }

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

const load = async () => {
  try {
    const [questionBank, leaderboardData, questionReviews] = await Promise.all([
      fetchJson(API.questions),
      fetchJson(API.leaderboard),
      fetchJson(API.questionReviews),
    ]);
    if (!Array.isArray(questionBank.questions) || !questionBank.questions.length) throw new Error("题库中没有可编辑的题目。");
    bank = questionBank;
    leaderboard = normalizeLeaderboard(leaderboardData);
    reviews = normalizeReviews(questionReviews);
    selectedQuestionId = bank.questions[0].id;
    render();
  } catch (error) {
    renderError(error instanceof Error ? error.message : "题库读取失败。");
  }
};

window.addEventListener("pagehide", () => {
  adminAuthorized = false;
});

window.addEventListener("pageshow", (event) => {
  if (!event.persisted) return;
  adminAuthorized = false;
  loginError = "";
  renderLogin();
});

if (hasAdminAccess()) {
  load();
} else {
  renderLogin();
}
