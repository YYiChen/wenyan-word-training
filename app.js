const app = document.querySelector("#app");
const {
  calculateScoreEvent,
  formatScoreDelta,
  normalizeScoringConfig,
} = window.WenyanScoring;

const FALLBACK_CONFIG = {
  durationSeconds: 120,
  correctScore: 1,
  wrongScore: -1,
  scoring: {
    mode: "fixed",
    baseCorrect: 1,
    baseWrongPenalty: 1,
    correctStreakAfter: 2,
    correctStreakScore: 2,
    wrongStreakAfter: 2,
    wrongStreakPenalty: 2,
  },
};
const MIN_DURATION_SECONDS = 10;
const MAX_DURATION_SECONDS = 3600;
const FONT_SCALE_STORAGE_KEY = "wenyan-quiz-font-scale";
const FONT_SCALE_MIN = 1;
const FONT_SCALE_MAX = 2;
const FONT_SCALE_STEP = 0.1;
const DEFAULT_FONT_SCALE = 1;
const QUIZ_SESSION_STORAGE_KEY = "wenyan-quiz-active-session";
let bank = null;
let timerId = null;
let state = null;
let startSelection = { volumes: ["all"], articleIds: ["all"] };
let leaderboard = [];
let feedbackEffectsController = null;
let feedbackEffectPlayedKey = "";

const escapeHtml = (value) => String(value ?? "")
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#039;");

const formatSeconds = (seconds) => {
  const safeSeconds = Math.max(0, Number(seconds) || 0);
  const minutes = Math.floor(safeSeconds / 60);
  const remainder = safeSeconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
};

const formatPercent = (value) => `${Math.round((Number(value) || 0) * 100)}%`;

const clampFontScale = (value) => Math.min(FONT_SCALE_MAX, Math.max(FONT_SCALE_MIN, Number(value) || DEFAULT_FONT_SCALE));

function readStoredFontScale() {
  try {
    return clampFontScale(Number(localStorage.getItem(FONT_SCALE_STORAGE_KEY)) || DEFAULT_FONT_SCALE);
  } catch {
    return DEFAULT_FONT_SCALE;
  }
}

const formatFontScale = (value) => `${Math.round(clampFontScale(value) * 100)}%`;

const saveStoredFontScale = (value) => {
  try {
    localStorage.setItem(FONT_SCALE_STORAGE_KEY, String(clampFontScale(value)));
  } catch {
    // Private browsing or browser policy may disable local storage; the current page still works.
  }
};

const setQuizFontScale = (value) => {
  quizFontScale = Math.round(clampFontScale(value) * 10) / 10;
  saveStoredFontScale(quizFontScale);
  if (state?.screen === "quiz") renderQuiz();
};

const adjustQuizFontScale = (delta) => setQuizFontScale(quizFontScale + delta);

const quizCardStyle = () => {
  const scale = quizFontScale;
  return [
    `--quiz-base-font-size: ${15 * scale}px`,
    `--quiz-title-font-size: clamp(${28 * scale}px, ${4 * scale}vw, ${42 * scale}px)`,
  ].join("; ");
};

let quizFontScale = readStoredFontScale();

const shuffle = (items) => {
  const result = [...items];
  for (let index = result.length - 1; index > 0; index -= 1) {
    const swapIndex = Math.floor(Math.random() * (index + 1));
    [result[index], result[swapIndex]] = [result[swapIndex], result[index]];
  }
  return result;
};

const normalizeLeaderboard = (entries) => (Array.isArray(entries) ? entries : [])
  .filter((entry) => entry && typeof entry.name === "string" && Number.isFinite(Number(entry.score)))
  .map((entry, index) => ({
    id: String(entry.id || `legacy-score-${Number(entry.createdAt) || 0}-${index}`).trim(),
    recordId: entry.recordId ? String(entry.recordId).trim() : "",
    name: entry.name.trim().slice(0, 20),
    score: Number(entry.score),
    createdAt: Number(entry.createdAt) || 0,
    context: entry.context && typeof entry.context === "object" ? entry.context : null,
  }))
  .filter((entry) => entry.name)
  .sort((left, right) => right.score - left.score || left.createdAt - right.createdAt || left.id.localeCompare(right.id));

const loadLeaderboard = async () => {
  const response = await fetch("./api/leaderboard", { cache: "no-store" });
  const result = await response.json().catch(() => null);
  if (!response.ok || !Array.isArray(result)) {
    throw new Error("排行榜文件读取失败。");
  }
  leaderboard = normalizeLeaderboard(result);
};

const createAnswerRecordId = () => `record-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;

const clearQuizRecovery = () => {
  try {
    sessionStorage.removeItem(QUIZ_SESSION_STORAGE_KEY);
  } catch {
    // Session storage may be disabled; the current quiz still works.
  }
};

const saveQuizRecovery = (session = state) => {
  if (!session || session.screen !== "quiz") return;
  try {
    const snapshot = { ...session, recordSavePromise: null };
    sessionStorage.setItem(QUIZ_SESSION_STORAGE_KEY, JSON.stringify(snapshot));
  } catch {
    // A storage quota or private-mode failure must not interrupt answering.
  }
};

const readQuizRecovery = () => {
  try {
    const raw = sessionStorage.getItem(QUIZ_SESSION_STORAGE_KEY);
    if (!raw) return null;
    const snapshot = JSON.parse(raw);
    if (!snapshot || snapshot.screen !== "quiz" || !Array.isArray(snapshot.questions)) return null;
    if (!Number.isFinite(Number(snapshot.deadlineAt)) || Number(snapshot.deadlineAt) <= Date.now()) {
      clearQuizRecovery();
      return null;
    }
    snapshot.recordSavePromise = null;
    return snapshot;
  } catch {
    clearQuizRecovery();
    return null;
  }
};

const getQuizConfig = () => {
  const quizDefaults = bank?.quizDefaults || {};
  const duration = Number(quizDefaults.durationSeconds);
  const durationSeconds = Number.isInteger(duration) && duration >= MIN_DURATION_SECONDS && duration <= MAX_DURATION_SECONDS
    ? duration
    : FALLBACK_CONFIG.durationSeconds;
  return {
    ...FALLBACK_CONFIG,
    ...quizDefaults,
    durationSeconds,
    scoring: normalizeScoringConfig(quizDefaults),
  };
};

const scoringModeLabel = (mode) => mode === "streak" ? "连续表现模式" : "固定计分模式";

const scoringSummary = (config) => config.scoring.mode === "streak"
  ? `基础答对 +${config.scoring.baseCorrect} · 基础答错 -${config.scoring.baseWrongPenalty} · 第 ${config.scoring.correctStreakAfter + 1} 次连续答对起 +${config.scoring.correctStreakScore} · 第 ${config.scoring.wrongStreakAfter + 1} 次连续答错起 -${config.scoring.wrongStreakPenalty}`
  : `答对 +${config.scoring.baseCorrect} · 答错 -${config.scoring.baseWrongPenalty}`;

const buildQuizContext = (session) => {
  const books = getBooks();
  const articles = getCatalog().filter((article) => session.articleIds.includes(article.id));
  return {
    volumes: session.volumeLabels.map((label) => {
      const book = books.find((item) => item.label === label);
      return { id: book?.id || "", label };
    }),
    articles: articles.map((article) => ({ id: article.id, label: article.title })),
    durationSeconds: session.durationSeconds,
    scoring: { ...session.scoringConfig },
  };
};

const buildAnswerRecord = (session = state) => {
  const usedSeconds = Math.min(
    session.durationSeconds,
    Math.max(0, Math.floor((session.finishedAt - session.startedAt) / 1000)),
  );
  const answeredQuestions = session.answerDetails.map((detail) => {
    const question = session.questions[detail.questionIndex];
    return {
      ...question,
      quizIndex: detail.questionIndex,
      selectedKey: detail.selectedKey,
      isCorrect: detail.isCorrect,
      scoreDelta: detail.scoreDelta,
      scoreTier: detail.tier || null,
      scoreLabel: detail.label || null,
      correctStreak: detail.correctStreak || 0,
      wrongStreak: detail.wrongStreak || 0,
    };
  });
  return {
    id: session.recordId || createAnswerRecordId(),
    name: "",
    score: session.score,
    startedAt: session.startedAt,
    finishedAt: session.finishedAt,
    usedSeconds,
    completedAll: session.completedAll,
    scoring: { ...session.scoringConfig },
    context: buildQuizContext(session),
    questions: answeredQuestions,
  };
};

const ensureAnswerRecordSaved = async (session = state, name = "", addToLeaderboard = false) => {
  if (!session) return null;
  if (session.recordSaved && (!name || session.recordName === name)) return session.recordId || null;
  if (session.recordSavePromise) return session.recordSavePromise;
  session.recordSaveStatus = "saving";
  session.recordSavePromise = (async () => {
    const response = await fetch("./api/quiz-results", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        record: buildAnswerRecord(session),
        name: String(name || "").trim(),
        addToLeaderboard: Boolean(addToLeaderboard),
      }),
    });
    const payload = await response.json().catch(() => null);
    if (!response.ok || !payload?.ok) throw new Error(payload?.error || "答题记录保存失败。");
    session.recordId = payload.data.record.id;
    session.recordSaved = true;
    session.recordSaveStatus = "saved";
    session.recordName = payload.data.record.name;
    session.scoreSaved = Boolean(payload.data.leaderboardSaved) || session.scoreSaved;
    return session.recordId;
  })();
  try {
    return await session.recordSavePromise;
  } catch (error) {
    session.recordSaveStatus = "error";
    session.recordSaveError = error instanceof Error ? error.message : "答题记录保存失败。";
    throw error;
  } finally {
    session.recordSavePromise = null;
  }
};

const getWordOccurrences = (sentence, word) => {
  const source = String(sentence || "");
  const target = String(word || "");
  if (!source || !target) return [];
  const occurrences = [];
  let start = 0;
  while (start < source.length) {
    const index = source.indexOf(target, start);
    if (index < 0) break;
    occurrences.push({ start: index, end: index + target.length });
    start = index + target.length;
  }
  return occurrences;
};

const validateBank = (payload) => {
  if (!payload || !Array.isArray(payload.questions)) {
    throw new Error("题库格式无效：缺少 questions 数组。");
  }

  const questions = payload.questions;
  const invalidQuestion = questions.find((question) => (
    !question
    || !question.id
    || !question.sentence
    || !question.articleId
    || !question.word
    || !Array.isArray(question.options)
    || question.options.length !== 4
    || !question.answer
    || !question.options.some((option) => option.key === question.answer)
  ));

  if (invalidQuestion) {
    throw new Error(`题目 ${invalidQuestion.number ?? "未知"} 的数据不完整。`);
  }

  const catalogById = new Map(
    (Array.isArray(payload.catalog) ? payload.catalog : [])
      .filter((article) => article && article.id)
      .map((article) => [article.id, article]),
  );
  const normalizedQuestions = questions.map((question) => {
    const article = catalogById.get(question.articleId);
    if (article?.volume && question.volume && article.volume !== question.volume) {
      throw new Error(`题目 ${question.number ?? "未知"} 的教材册与所属篇目不一致。`);
    }
    const occurrences = getWordOccurrences(question.sentence, question.word);
    let underlineIssue = "";
    let targetOccurrence = question.targetOccurrence == null ? 1 : Number(question.targetOccurrence);
    const targetStart = question.targetStart == null ? null : Number(question.targetStart);
    if (question.targetOccurrence == null && Number.isInteger(targetStart) && targetStart >= 0) {
      const startIndex = occurrences.findIndex((occurrence) => occurrence.start === targetStart);
      if (startIndex >= 0) targetOccurrence = startIndex + 1;
    }
    if (occurrences.length === 0) {
      underlineIssue = "考查实词不在原句中";
    } else if (!Number.isInteger(targetOccurrence) || targetOccurrence < 1 || targetOccurrence > occurrences.length) {
      underlineIssue = "targetOccurrence 与原句中的实际出现次数不一致";
    } else if (targetStart != null && (!Number.isInteger(targetStart) || targetStart !== occurrences[targetOccurrence - 1].start)) {
      underlineIssue = "targetStart 与考查实词位置不一致";
    }
    if (!underlineIssue) return question;
    return {
      ...question,
      reviewStatus: "abnormal",
      reviewNote: `${String(question.reviewNote || "").trim()}${question.reviewNote ? "；" : ""}系统检测：${underlineIssue}，请人工复核。`,
    };
  });

  return { ...payload, questions: normalizedQuestions };
};

const getCatalog = () => Array.isArray(bank?.catalog) ? bank.catalog : [];

const getBooks = () => {
  const configured = Array.isArray(bank?.books) ? bank.books : [];
  const books = [];
  const labels = new Set();
  configured.forEach((book) => {
    if (!book || typeof book.label !== "string" || !book.label.trim()) return;
    const label = book.label.trim();
    if (labels.has(label)) return;
    labels.add(label);
    books.push({ id: String(book.id || "").trim(), label, order: Number(book.order) || books.length + 1 });
  });
  getCatalog().forEach((article) => {
    const label = String(article.volume || "").trim();
    if (!label || labels.has(label)) return;
    labels.add(label);
    books.push({ id: "", label, order: books.length + 1 });
  });
  return books.sort((left, right) => left.order - right.order || left.label.localeCompare(right.label, "zh-CN"));
};

const formatArticleLabel = (title) => {
  const value = String(title || "课内文章");
  return value.startsWith("《") ? value : `《${value}》`;
};

const normalizeSelection = (values, allowedValues) => {
  const allowed = [...allowedValues];
  if (Array.isArray(values) && values.includes("all")) return allowed;
  const allowedSet = new Set(allowed);
  return [...new Set(Array.isArray(values) ? values : [])]
    .filter((value) => allowedSet.has(value));
};

const getAvailableArticles = (volumes = ["all"]) => {
  const selectedVolumes = Array.isArray(volumes) ? volumes : [volumes];
  return getCatalog().filter((article) => (
    selectedVolumes.includes("all") || selectedVolumes.includes(article.volume)
  ));
};

const getSelectedQuestions = () => {
  const selectedVolumes = new Set(startSelection.volumes);
  const selectedArticleIds = new Set(startSelection.articleIds);
  return bank.questions.filter((question) => (
    !["abnormal", "candidate", "needs_revision"].includes(question.reviewStatus)
    && !["pending", "skipped"].includes(question.duplicateReview?.status)
    && selectedVolumes.has(question.volume) && selectedArticleIds.has(question.articleId)
  ));
};

const renderFilterChoices = (group, items, selectedValues, allLabel, getValue, getLabel, getMeta) => {
  const itemValues = items.map(getValue);
  const allSelected = itemValues.length > 0 && itemValues.every((value) => selectedValues.includes(value));
  const choices = [
    { value: "all", label: allLabel, meta: "一键勾选全部" },
    ...items.map((item) => ({
      value: getValue(item),
      label: getLabel(item),
      meta: getMeta(item),
    })),
  ];
  return choices.map((choice) => `
    <label class="filter-choice">
      <input type="checkbox" data-filter-group="${group}" value="${escapeHtml(choice.value)}" ${(choice.value === "all" ? allSelected : selectedValues.includes(choice.value)) ? "checked" : ""} />
      <span class="filter-choice-box">
        <span class="filter-choice-check" aria-hidden="true">✓</span>
        <span class="filter-choice-text"><strong>${escapeHtml(choice.label)}</strong><small>${escapeHtml(choice.meta || "")}</small></span>
      </span>
    </label>
  `).join("");
};

const describeSelection = (selectedValues, items, allLabel, getValue, getLabel) => {
  const itemValues = items.map(getValue);
  const selectedCount = itemValues.filter((value) => selectedValues.includes(value)).length;
  if (itemValues.length > 0 && selectedCount === itemValues.length) return `${allLabel}（${itemValues.length}项）`;
  if (selectedValues.length === 0) return "未选择";
  return `已选 ${selectedCount} 项`;
};

const renderError = (message) => {
  app.innerHTML = `
    <section class="state-screen error-screen" aria-labelledby="error-title">
      <div class="error-card">
        <div class="loading-mark" aria-hidden="true">!</div>
        <p class="eyebrow">题库加载失败</p>
        <h1 id="error-title">暂时无法开始</h1>
        <p class="error-copy muted">${escapeHtml(message)}<br />请先启动本地服务，再打开学生答题页。</p>
        <button class="secondary-button" type="button" data-action="reload">重新加载</button>
      </div>
    </section>
  `;
  app.querySelector('[data-action="reload"]').addEventListener("click", loadBank);
};

const renderStart = () => {
  const config = getQuizConfig();
  const hasQuestions = bank.questions.length > 0;
  const abnormalCount = bank.questions.filter((question) => question.reviewStatus === "abnormal").length;
  const candidateCount = bank.questions.filter((question) => question.reviewStatus === "candidate").length;
  const volumes = getBooks().map((book) => book.label);
  const selectedVolumes = normalizeSelection(startSelection.volumes, volumes);
  const availableArticles = getAvailableArticles(selectedVolumes);
  const selectedArticleIds = normalizeSelection(
    startSelection.articleIds,
    availableArticles.map((article) => article.id),
  );
  startSelection = { volumes: selectedVolumes, articleIds: selectedArticleIds };
  const hasPlayableQuestions = getSelectedQuestions().length > 0;
  const volumeSummary = describeSelection(
    selectedVolumes,
    volumes.map((volume) => ({ value: volume, label: volume })),
    "全部教材册",
    (item) => item.value,
    (item) => item.label,
  );
  const articleSummary = describeSelection(
    selectedArticleIds,
    availableArticles,
    "所选教材册内全部文章",
    (article) => article.id,
    (article) => article.title,
  );
  app.innerHTML = `
    <section class="start-layout" aria-labelledby="start-title">
      <div class="start-copy">
        <div class="brand-mark" aria-hidden="true">文</div>
        <p class="eyebrow">文言实词 · 限时训练</p>
        <h1 id="start-title">在语境里，<br />认出那个词。</h1>
        <p class="start-subtitle">从教材原句中辨认实词义项。在 ${formatSeconds(config.durationSeconds)} 内连续答题，看看你能拿到多少分。</p>
        <p class="start-note">${hasQuestions ? `题库覆盖必修与选择性必修教材的课内文章；${candidateCount ? `${candidateCount} 道候选题待教师复核，` : ""}${abnormalCount ? `${abnormalCount} 道划线异常题已自动跳过，` : ""}${hasPlayableQuestions ? "答题时只改变选项顺序。" : "当前所选范围没有可答题目，请更换教材册或篇目。"}` : "当前未内置题库，请先进入管理后台导入题库后再开始答题。"}</p>
      </div>
      <div class="start-card">
        <p class="card-label">选择训练范围</p>
        <p class="filter-note">${hasQuestions ? "教材册和篇目都可以多选；勾选“全部”会直接勾上下面的所有项目。" : "当前是空白题库，导入题库后这里会显示教材册和篇目。"}</p>
        <fieldset class="filter-field">
          <legend>教材册 <span>可多选</span></legend>
          <div class="filter-choice-list volume-choice-list" role="group" aria-label="选择教材册">
            ${renderFilterChoices(
              "volume",
              volumes.map((volume) => ({ value: volume, label: volume })),
              selectedVolumes,
              "全部教材册",
              (item) => item.value,
              (item) => item.label,
              () => "整合所有教材范围",
            )}
          </div>
        </fieldset>
        <fieldset class="filter-field">
          <legend>篇目 <span>可多选</span></legend>
          <div class="filter-choice-list article-choice-list" role="group" aria-label="选择文章">
            ${renderFilterChoices(
              "article",
              availableArticles,
              selectedArticleIds,
              "全部文章",
              (article) => article.id,
              (article) => article.title,
              (article) => [article.volume, article.unit].filter(Boolean).join(" · "),
            )}
          </div>
        </fieldset>
        <p class="filter-summary" aria-live="polite"><strong>当前范围</strong><span>教材册：${escapeHtml(volumeSummary)}</span><span>篇目：${escapeHtml(articleSummary)}</span></p>
        <p class="card-label rule-label">本局规则</p>
        <div class="rule-list" aria-label="答题规则">
          <div class="rule-item"><span>答题时间</span><strong>${formatSeconds(config.durationSeconds)}</strong></div>
          <div class="rule-item"><span>计分机制</span><strong>${scoringModeLabel(config.scoring.mode)}</strong></div>
          <div class="rule-item rule-item-wide"><span>本局规则</span><strong>${escapeHtml(scoringSummary(config))}</strong></div>
        </div>
        <div class="button-stack">
          <button class="primary-button" type="button" data-action="start" ${hasPlayableQuestions ? "" : "disabled"}>开始答题</button>
          <button class="secondary-button" type="button" data-action="leaderboard">查看排行榜</button>
        </div>
        <a class="admin-link" href="./admin.html">进入管理后台</a>
      </div>
    </section>
  `;
  const getCheckedValues = (group) => [...app.querySelectorAll(`[data-filter-group="${group}"]:checked`)]
    .filter((input) => input.value !== "all")
    .map((input) => input.value);
  const updateFilterSummary = () => {
    const selectedVolumeValues = getCheckedValues("volume");
    const currentArticles = getAvailableArticles(selectedVolumeValues);
    const selectedArticleValues = getCheckedValues("article");
    const summary = app.querySelector(".filter-summary");
    if (!summary) return;
    summary.innerHTML = `
      <strong>当前范围</strong>
      <span>教材册：${escapeHtml(describeSelection(selectedVolumeValues, volumes.map((volume) => ({ value: volume, label: volume })), "全部教材册", (item) => item.value, (item) => item.label))}</span>
      <span>篇目：${escapeHtml(describeSelection(selectedArticleValues, currentArticles, "所选教材册内全部文章", (article) => article.id, (article) => article.title))}</span>
    `;
    const startButton = app.querySelector('[data-action="start"]');
    if (startButton) startButton.disabled = getSelectedQuestions().length === 0;
  };
  const syncSelectAll = (group, changedInput) => {
    const allInput = app.querySelector(`[data-filter-group="${group}"][value="all"]`);
    const itemInputs = [...app.querySelectorAll(`[data-filter-group="${group}"]`)]
      .filter((input) => input.value !== "all");
    if (changedInput.value === "all") {
      itemInputs.forEach((input) => { input.checked = changedInput.checked; });
    } else if (allInput) {
      allInput.checked = itemInputs.length > 0 && itemInputs.every((input) => input.checked);
    }
    return itemInputs.filter((input) => input.checked).map((input) => input.value);
  };
  app.querySelectorAll('[data-filter-group="volume"]').forEach((input) => {
    input.addEventListener("change", () => {
      const previousArticleIds = [...startSelection.articleIds];
      const previousAvailableArticleIds = getAvailableArticles(startSelection.volumes).map((article) => article.id);
      const previousArticlesWereAll = previousAvailableArticleIds.length > 0
        && previousAvailableArticleIds.every((articleId) => previousArticleIds.includes(articleId));
      startSelection.volumes = syncSelectAll("volume", input);
      const currentArticles = getAvailableArticles(startSelection.volumes);
      const currentArticleIds = currentArticles.map((article) => article.id);
      const retainedArticleIds = previousArticlesWereAll
        ? currentArticleIds
        : previousArticleIds.filter((articleId) => currentArticleIds.includes(articleId));
      startSelection.articleIds = currentArticleIds.length === 0
        ? []
        : previousArticleIds.length === 0
          ? currentArticleIds
          : retainedArticleIds.length ? retainedArticleIds : currentArticleIds;
      renderStart();
    });
  });
  app.querySelectorAll('[data-filter-group="article"]').forEach((input) => {
    input.addEventListener("change", () => {
      startSelection.articleIds = syncSelectAll("article", input);
      updateFilterSummary();
    });
  });
  app.querySelector('[data-action="start"]').addEventListener("click", startGame);
  app.querySelector('[data-action="leaderboard"]').addEventListener("click", renderLeaderboard);
};

const renderLeaderboard = async () => {
  app.innerHTML = `<section class="state-screen loading-screen"><div class="loading-mark" aria-hidden="true">…</div><p class="eyebrow">往次成绩</p><h1>正在读取排行榜</h1></section>`;
  try {
    await loadLeaderboard();
  } catch (error) {
    renderError(error instanceof Error ? error.message : "排行榜读取失败。");
    return;
  }
  const entries = leaderboard;
  app.innerHTML = `
    <section class="state-screen leaderboard-screen" aria-labelledby="leaderboard-title">
      <div class="leaderboard-card">
        <p class="eyebrow">往次成绩</p>
        <h1 id="leaderboard-title">排行榜</h1>
        <p class="leaderboard-intro">本机保存的历史成绩，按分数从高到低排列；同分时先提交者优先。</p>
        ${entries.length === 0 ? `
          <div class="leaderboard-empty">还没有成绩记录，完成第一局后就会出现在这里。</div>
        ` : `
          <div class="leaderboard-list" aria-label="历史成绩排行榜">
            ${entries.map((entry, index) => `
              <div class="leaderboard-row">
                <span class="leaderboard-rank">${index + 1}</span>
                <span class="leaderboard-name"><strong>${escapeHtml(entry.name)}</strong><small>${escapeHtml(formatLeaderboardContext(entry.context))}</small></span>
                <strong class="leaderboard-score">${entry.score} 分</strong>
              </div>
            `).join("")}
          </div>
        `}
        <div class="button-stack">
          <button class="primary-button" type="button" data-action="start">开始答题</button>
          <button class="secondary-button" type="button" data-action="home">返回首页</button>
        </div>
      </div>
    </section>
  `;
  app.querySelector('[data-action="start"]').addEventListener("click", startGame);
  app.querySelector('[data-action="home"]').addEventListener("click", renderStart);
};

const getCurrentQuestion = () => state.questions[state.currentIndex];

const renderSentence = (question) => {
  const sentence = String(question.sentence || "");
  const word = String(question.word || "");
  const occurrences = getWordOccurrences(sentence, word);
  if (occurrences.length === 0) return escapeHtml(sentence);
  let targetOccurrence = question.targetOccurrence == null ? 1 : Number(question.targetOccurrence);
  if (question.targetOccurrence == null && Number.isInteger(Number(question.targetStart))) {
    const startIndex = occurrences.findIndex((occurrence) => occurrence.start === Number(question.targetStart));
    if (startIndex >= 0) targetOccurrence = startIndex + 1;
  }
  const selected = Number.isInteger(targetOccurrence) && targetOccurrence >= 1 && targetOccurrence <= occurrences.length
    ? targetOccurrence
    : 1;
  let cursor = 0;
  let result = "";
  occurrences.forEach((occurrence, index) => {
    result += escapeHtml(sentence.slice(cursor, occurrence.start));
    const className = index + 1 === selected ? "target-word target-word-selected" : "target-word target-word-other";
    result += `<mark class="${className}" data-occurrence="${index + 1}">${escapeHtml(word)}</mark>`;
    cursor = occurrence.end;
  });
  return result + escapeHtml(sentence.slice(cursor));
};

const renderContext = (question) => {
  const context = Array.isArray(question.context) ? question.context : [];
  if (context.length === 0) return "";
  return `
    <div class="context-block" aria-label="题目前置信息">
      ${context.map((line) => `<p>${escapeHtml(line)}</p>`).join("")}
    </div>
  `;
};

const renderSupportingItems = (question, showMeaning) => {
  const items = Array.isArray(question.supportingItems) ? question.supportingItems : [];
  if (items.length === 0) return "";
  return `
    <div class="supporting-items" aria-label="题目材料">
      ${items.map((item) => `
        <div class="supporting-item">
          <span class="supporting-key">${escapeHtml(item.key)}</span>
          <span>
            ${escapeHtml(item.text)}
            ${showMeaning && item.meaning ? `<span class="supporting-meaning">释义：${escapeHtml(item.meaning)}</span>` : ""}
          </span>
        </div>
      `).join("")}
    </div>
  `;
};

const optionClass = (option) => {
  if (!state.answeredCurrent) return "";
  const answerDetail = state.answerDetails[state.answerDetails.length - 1];
  const tierClass = answerDetail?.tier === "streak" ? " super-result" : "";
  if (option.key === getCurrentQuestion().answer) return `correct${tierClass}`;
  if (option.key === state.selectedKey) return `wrong${tierClass}`;
  return "";
};

const renderOptions = (question) => question.options.map((option) => {
  const selected = state.selectedKey === option.key;
  return `
    <button class="option-button ${selected ? "selected" : ""} ${optionClass(option)}" type="button" data-option="${escapeHtml(option.key)}" ${state.answeredCurrent ? "disabled" : ""}>
      <span class="option-key">${escapeHtml(option.key)}</span>
      <span>${escapeHtml(option.text)}</span>
    </button>
  `;
}).join("");

const getFeedbackEffectType = (isCorrect, answerDetail) => {
  if (answerDetail?.tier === "streak") return isCorrect ? "super-correct" : "super-wrong";
  return isCorrect ? "correct" : "wrong";
};

const destroyFeedbackEffects = () => {
  feedbackEffectsController?.destroy();
  feedbackEffectsController = null;
};

const mountFeedbackEffects = () => {
  const stage = app.querySelector("[data-feedback-effects-stage]");
  const canvas = app.querySelector("[data-feedback-effects-canvas]");
  if (!stage || !canvas || !window.WenyanFeedbackEffects?.create || !state?.answeredCurrent) return;

  const effectType = stage.dataset.feedbackEffect;
  feedbackEffectsController = window.WenyanFeedbackEffects.create({
    stage,
    canvas,
    onResult(type) {
      stage.classList.remove("result-correct", "result-super-correct", "result-wrong", "result-super-wrong");
      void stage.offsetWidth;
      stage.classList.add(`result-${type}`);
    },
  });

  const effectKey = `${state.currentIndex}:${state.answerDetails.length}`;
  if (effectType && effectKey !== feedbackEffectPlayedKey) {
    feedbackEffectsController.play(effectType);
    feedbackEffectPlayedKey = effectKey;
  }
};

const renderFeedback = (question) => {
  if (!state.answeredCurrent) return "";
  const isCorrect = state.selectedKey === question.answer;
  const answerDetail = state.answerDetails[state.answerDetails.length - 1];
  const isSuper = answerDetail?.tier === "streak";
  const selectedOption = question.options.find((option) => option.key === state.selectedKey);
  const answerOption = question.options.find((option) => option.key === question.answer);
  const resultClass = isSuper ? `super ${isCorrect ? "super-correct" : "super-wrong"}` : "base";
  const scoreLabel = answerDetail?.label || (isCorrect ? "基础加分" : "基础扣分");
  const scoreText = answerDetail ? answerDetail.scoreText : formatScoreDelta(isCorrect ? state.scoringConfig.baseCorrect : -state.scoringConfig.baseWrongPenalty);
  const streakText = state.scoringConfig.mode === "streak"
    ? isCorrect
      ? `当前连续答对 ${answerDetail?.correctStreak || 0} 题`
      : `当前连续答错 ${answerDetail?.wrongStreak || 0} 题`
    : "";
  const effectType = getFeedbackEffectType(isCorrect, answerDetail);
  return `
    <div class="feedback-panel feedback-stage ${isCorrect ? "success" : "error"} ${resultClass} result-${effectType}" data-feedback-effects-stage data-feedback-effect="${effectType}" role="status" aria-live="polite">
      <canvas class="feedback-effects-canvas" data-feedback-effects-canvas aria-hidden="true"></canvas>
      <div class="feedback-content">
        <div class="score-event" aria-label="${escapeHtml(`${scoreLabel} ${scoreText} 分`)}">
          <span class="score-event-icon" aria-hidden="true">${isCorrect ? (isSuper ? "★" : "✓") : (isSuper ? "!" : "×")}</span>
          <span class="score-event-copy"><strong>${escapeHtml(scoreLabel)}</strong><b>${escapeHtml(scoreText)} 分</b></span>
        </div>
        <div class="feedback-title">${isCorrect ? "回答正确！" : "回答错误"}</div>
        ${streakText ? `<p class="feedback-streak">${escapeHtml(streakText)}</p>` : ""}
        ${!isCorrect ? `<p class="feedback-answer">你的选择：${escapeHtml(selectedOption?.key || "未选择")}　正确答案：${escapeHtml(answerOption?.key || question.answer)}</p>` : ""}
        <p>${escapeHtml(question.explanation || "本题暂无补充解析。")}</p>
        <button class="primary-button" type="button" data-action="next">${state.currentIndex + 1 >= state.questions.length ? "查看成绩" : "下一题"}</button>
      </div>
    </div>
  `;
};

const renderQuiz = () => {
  const question = getCurrentQuestion();
  const timerClass = state.remainingSeconds <= 10 ? "danger" : state.remainingSeconds <= 30 ? "warning" : "";
  const isContextMeaning = !question.type || question.type === "context_meaning";
  const questionTitle = isContextMeaning
    ? renderSentence(question)
    : escapeHtml(question.stem || question.sentence || "请选择答案");
  const questionPrompt = isContextMeaning
    ? `句中“${escapeHtml(question.word || "")}”的意思是：`
    : escapeHtml(question.stem ? "请选择最符合题意的一项：" : "请选择答案：");

  destroyFeedbackEffects();
  app.innerHTML = `
    <section class="quiz-shell" aria-labelledby="question-title">
      <header class="topbar">
        <div class="brand-lockup">
          <div class="brand-mark" aria-hidden="true">文</div>
          <div><strong>文言实词限时训练</strong><span>${escapeHtml(question.volume || "教材范围")} · 限时模式</span></div>
        </div>
        <div class="score-strip">
          <div class="score-box"><span>当前得分</span><strong>${state.score}</strong></div>
          <div class="timer-box ${timerClass}" aria-live="polite"><span>剩余时间</span><strong>${formatSeconds(state.remainingSeconds)}</strong></div>
          <div class="quiz-font-controls" role="group" aria-label="答题字号调节">
            <button class="font-control-button" type="button" data-action="font-decrease" aria-label="减小答题字号" ${quizFontScale <= FONT_SCALE_MIN ? "disabled" : ""}>A−</button>
            <span class="font-scale-value" aria-live="polite">${formatFontScale(quizFontScale)}</span>
            <button class="font-control-button" type="button" data-action="font-increase" aria-label="增大答题字号" ${quizFontScale >= FONT_SCALE_MAX ? "disabled" : ""}>A+</button>
            <button class="font-reset-button" type="button" data-action="font-reset">重置</button>
          </div>
        </div>
      </header>
      <div class="quiz-meta"><span>第 ${state.currentIndex + 1} 题</span><span>${escapeHtml(question.article || "课内文章")}</span></div>
      <article class="quiz-card" style="${quizCardStyle()}">
        <div class="question-kicker">考查实词：${escapeHtml(question.word || "未标注")}</div>
        <h1 id="question-title" class="question-title">${questionTitle}</h1>
        <p class="question-source">——${escapeHtml(formatArticleLabel(question.article))}</p>
        <p class="question-prompt">${questionPrompt}</p>
        ${renderContext(question)}
        ${renderSupportingItems(question, state.answeredCurrent)}
        <div class="option-list" role="group" aria-label="答案选项">${renderOptions(question)}</div>
        ${renderFeedback(question)}
        <div class="quiz-actions">
          ${!state.answeredCurrent ? `<p class="quiz-footnote">选择一个选项提交答案</p>` : ""}
          <button class="secondary-button finish-button" type="button" data-action="finish">提前交卷</button>
        </div>
      </article>
    </section>
  `;

  app.querySelectorAll("[data-option]").forEach((button) => {
    button.addEventListener("click", () => submitAnswer(button.dataset.option));
  });
  const nextButton = app.querySelector('[data-action="next"]');
  if (nextButton) nextButton.addEventListener("click", nextQuestion);
  app.querySelector('[data-action="finish"]').addEventListener("click", finishEarly);
  app.querySelector('[data-action="font-decrease"]').addEventListener("click", () => adjustQuizFontScale(-FONT_SCALE_STEP));
  app.querySelector('[data-action="font-increase"]').addEventListener("click", () => adjustQuizFontScale(FONT_SCALE_STEP));
  app.querySelector('[data-action="font-reset"]').addEventListener("click", () => setQuizFontScale(DEFAULT_FONT_SCALE));
  mountFeedbackEffects();
};

const formatLeaderboardContext = (context) => {
  if (!context || typeof context !== "object") return "历史成绩 · 规则未知";
  const volumes = Array.isArray(context.volumes) ? context.volumes.map((item) => item?.label).filter(Boolean) : [];
  const articles = Array.isArray(context.articles) ? context.articles.map((item) => item?.label).filter(Boolean) : [];
  const scope = volumes.length ? volumes.join("、") : "历史范围未知";
  const articleText = articles.length > 2 ? `${articles.slice(0, 2).join("、")}等${articles.length}篇` : articles.join("、");
  const duration = Number(context.durationSeconds) > 0 ? `限时 ${formatSeconds(context.durationSeconds)}` : "时长未知";
  const mode = scoringModeLabel(context.scoring?.mode);
  return `${scope}${articleText ? ` · ${articleText}` : ""} · ${duration} · ${mode}`;
};

const canLeaveResult = (session) => {
  if (!session || session.recordSaveStatus !== "error") return true;
  return window.confirm("本次答题记录尚未保存，离开后需要重新答题才能补救。确定离开吗？");
};

const renderResult = (session = state) => {
  if (!session) return;
  destroyFeedbackEffects();
  const duration = session.durationSeconds;
  const usedSeconds = Math.min(duration, Math.max(0, Math.floor((session.finishedAt - session.startedAt) / 1000)));
  const total = session.answered;
  const accuracy = total === 0 ? 0 : session.correct / total;
  const resultLabel = session.completedAll
    ? "所有题目已答完"
    : session.finishReason === "manual"
      ? "已提前交卷"
      : "本次答题结束";
  const resultMeta = session.completedAll
    ? "本局已完成"
    : session.finishReason === "manual"
      ? "你选择了提前交卷"
      : "时间到，答题结束";
  app.innerHTML = `
    <section class="state-screen result-screen" aria-labelledby="result-title">
      <div class="result-card">
        <p class="eyebrow">${resultLabel}</p>
        <h1 id="result-title">答得怎么样？</h1>
        <div class="result-score">${session.score}<small>分</small></div>
        <div class="result-stats">
          <div class="result-stat"><strong>${session.correct}</strong><span>答对</span></div>
          <div class="result-stat"><strong>${session.wrong}</strong><span>答错</span></div>
          <div class="result-stat"><strong>${formatPercent(accuracy)}</strong><span>正确率</span></div>
        </div>
        <p class="result-meta">${resultMeta} · 用时 ${formatSeconds(usedSeconds)}</p>
        ${session.recordSaveStatus === "saving" ? `<p class="saved-note">正在保存本次答题记录…</p>` : ""}
        ${session.recordSaveStatus === "error" ? `<p class="record-save-error" role="alert">答题记录尚未保存，请再次提交姓名或留空重试。</p>` : ""}
        ${session.scoreSaved ? `
          <p class="saved-note">已将本次答题记录保存，并计入排行榜。</p>
        ` : session.recordNameFinalized && session.recordName !== "未命名" ? `
          <p class="saved-note">本次答题记录已保存，姓名：${escapeHtml(session.recordName)}。</p>
        ` : `
          <form class="score-form" data-action="score-form">
            <label for="player-name">姓名（可不填；不填则显示“未命名”）</label>
            <div class="score-form-row">
              <input id="player-name" name="name" type="text" maxlength="20" placeholder="请输入名字，可留空" autocomplete="off" />
              <button class="primary-button" type="submit">保存姓名并加入排行</button>
            </div>
          </form>
        `}
        <div class="button-stack">
          <button class="${session.scoreSaved ? "primary-button" : "secondary-button"}" type="button" data-action="leaderboard">查看排行榜</button>
          <button class="secondary-button" type="button" data-action="restart">再来一局</button>
          <button class="secondary-button" type="button" data-action="home">返回首页</button>
        </div>
      </div>
    </section>
  `;
  app.querySelector('[data-action="restart"]').addEventListener("click", () => {
    session.screen = "replaced";
    clearQuizRecovery();
    startGame();
  });
  app.querySelector('[data-action="leaderboard"]').addEventListener("click", () => {
    if (!canLeaveResult(session)) return;
    session.screen = "leaderboard";
    renderLeaderboard();
  });
  app.querySelector('[data-action="home"]').addEventListener("click", () => {
    if (!canLeaveResult(session)) return;
    session.screen = "home";
    renderStart();
  });
  const scoreForm = app.querySelector('[data-action="score-form"]');
  if (scoreForm) {
    scoreForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const name = String(new FormData(scoreForm).get("name") || "").trim();
      const submitButton = scoreForm.querySelector('button[type="submit"]');
      submitButton.disabled = true;
      submitButton.textContent = "正在保存…";
      try {
        await ensureAnswerRecordSaved(session, name, Boolean(name));
        session.recordName = name || "未命名";
        session.recordNameFinalized = Boolean(name);
        if (state === session && session.screen === "result") renderResult(session);
      } catch (error) {
        submitButton.disabled = false;
        submitButton.textContent = "保存姓名并加入排行";
        window.alert(error instanceof Error ? error.message : "成绩保存失败。");
      }
    });
  }
};

const updateQuizTimerDisplay = () => {
  const timerBox = app.querySelector(".timer-box");
  const timerValue = timerBox?.querySelector("strong");
  if (!timerBox || !timerValue || !state) return;
  timerValue.textContent = formatSeconds(state.remainingSeconds);
  timerBox.classList.toggle("danger", state.remainingSeconds <= 10);
  timerBox.classList.toggle("warning", state.remainingSeconds > 10 && state.remainingSeconds <= 30);
};

const startTimer = () => {
  window.clearInterval(timerId);
  const session = state;
  timerId = window.setInterval(() => {
    if (state !== session || session.screen !== "quiz") {
      window.clearInterval(timerId);
      return;
    }
    const remainingMilliseconds = session.deadlineAt - Date.now();
    session.remainingSeconds = Math.max(0, Math.ceil(remainingMilliseconds / 1000));
    if (remainingMilliseconds <= 0) {
      const completedAll = session.answeredCurrent && session.currentIndex + 1 >= session.questions.length;
      finishGame(completedAll ? "completed" : "timeout");
      return;
    }
    updateQuizTimerDisplay();
  }, 250);
};

const shuffleQuestionOptions = (question) => {
  const optionKeys = ["A", "B", "C", "D"];
  const shuffled = shuffle(question.options);
  const originalAnswer = question.answer;
  let answer = "A";
  const options = shuffled.map((option, index) => {
    const key = optionKeys[index];
    if (option.key === originalAnswer) answer = key;
    return { ...option, key };
  });
  return { ...question, options, answer };
};

const refreshBankBeforeStart = async () => {
  const response = await fetch("./api/questions", { cache: "no-store" });
  if (!response.ok) throw new Error(`题库文件读取失败（${response.status}）。`);
  bank = validateBank(await response.json());
};

const startGame = async () => {
  if (state?.screen === "quiz") return;
  if (state?.screen === "result") state.screen = "replaced";
  clearQuizRecovery();
  try {
    await refreshBankBeforeStart();
  } catch (error) {
    window.alert(error instanceof Error ? `读取最新题库和计分规则失败：${error.message}` : "读取最新题库和计分规则失败。");
    return;
  }
  const config = getQuizConfig();
  if (startSelection.volumes.length === 0) {
    window.alert("请至少勾选一本教材册，或点击“全部教材册”。");
    return;
  }
  if (startSelection.articleIds.length === 0) {
    window.alert("请至少勾选一篇文章，或点击“全部文章”。");
    return;
  }
  const selectedQuestions = getSelectedQuestions();
  if (selectedQuestions.length === 0) {
    window.alert("这个范围暂时没有可用题目，请换一个教材册或篇目。");
    return;
  }
  state = {
    screen: "quiz",
    questions: shuffle(selectedQuestions).map(shuffleQuestionOptions),
    currentIndex: 0,
    score: 0,
    answered: 0,
    correct: 0,
    wrong: 0,
    remainingSeconds: config.durationSeconds,
    durationSeconds: config.durationSeconds,
    deadlineAt: Date.now() + config.durationSeconds * 1000,
    sessionId: createAnswerRecordId(),
    volumeLabels: [...startSelection.volumes],
    articleIds: [...startSelection.articleIds],
    scoringConfig: { ...config.scoring },
    correctStreak: 0,
    wrongStreak: 0,
    selectedKey: null,
    answeredCurrent: false,
    completedAll: false,
    finishReason: "in_progress",
    scoreSaved: false,
    recordId: null,
    recordSaved: false,
    recordSaveStatus: "pending",
    recordSavePromise: null,
    recordSaveError: "",
    recordName: "未命名",
    recordNameFinalized: false,
    answerDetails: [],
    finishedAt: 0,
    startedAt: Date.now(),
  };
  feedbackEffectPlayedKey = "";
  renderQuiz();
  startTimer();
  saveQuizRecovery();
};

const submitAnswer = (key) => {
  if (!state || state.screen !== "quiz" || state.answeredCurrent) return;
  if (Date.now() >= state.deadlineAt) {
    const completedAll = state.answeredCurrent && state.currentIndex + 1 >= state.questions.length;
    finishGame(completedAll ? "completed" : "timeout");
    return;
  }
  state.remainingSeconds = Math.max(0, Math.ceil((state.deadlineAt - Date.now()) / 1000));
  const question = getCurrentQuestion();
  state.selectedKey = key;
  state.answeredCurrent = true;
  state.answered += 1;
  const isCorrect = key === question.answer;
  const scoreEvent = calculateScoreEvent(state.scoringConfig, isCorrect, {
    correctStreak: state.correctStreak,
    wrongStreak: state.wrongStreak,
  });
  state.correctStreak = scoreEvent.correctStreak;
  state.wrongStreak = scoreEvent.wrongStreak;
  state.answerDetails.push({
    questionIndex: state.currentIndex,
    questionId: question.id,
    selectedKey: key,
    isCorrect,
    ...scoreEvent,
  });
  if (isCorrect) {
    state.score += scoreEvent.scoreDelta;
    state.correct += 1;
  } else {
    state.score += scoreEvent.scoreDelta;
    state.wrong += 1;
  }
  renderQuiz();
  saveQuizRecovery();
};

const nextQuestion = () => {
  if (!state || !state.answeredCurrent) return;
  if (state.currentIndex + 1 >= state.questions.length) {
    finishGame("completed");
    return;
  }
  state.currentIndex += 1;
  state.selectedKey = null;
  state.answeredCurrent = false;
  renderQuiz();
  saveQuizRecovery();
};

const finishEarly = () => {
  if (!state || state.screen !== "quiz") return;
  const confirmed = window.confirm("确定要提前交卷吗？当前成绩将作为本局成绩保存。");
  if (!confirmed) return;
  const completedAll = state.answeredCurrent && state.currentIndex + 1 >= state.questions.length;
  finishGame(completedAll ? "completed" : "manual");
};

const finishGame = (reason = "timeout") => {
  const session = state;
  if (!session || session.screen === "result") return;
  if (reason === "manual" && Date.now() >= session.deadlineAt) reason = "timeout";
  window.clearInterval(timerId);
  session.completedAll = reason === "completed";
  session.finishReason = reason;
  session.finishedAt = Date.now();
  session.screen = "result";
  clearQuizRecovery();
  renderResult(session);
  ensureAnswerRecordSaved(session).then(() => {
    if (state === session && session.screen === "result") renderResult(session);
  }).catch((error) => {
    session.recordSaveError = error instanceof Error ? error.message : "答题记录保存失败。";
    session.recordSaveStatus = "error";
    if (state === session && session.screen === "result") renderResult(session);
  });
};

window.addEventListener("beforeunload", (event) => {
  if (state?.screen !== "quiz") return;
  event.preventDefault();
  event.returnValue = "";
});

const loadBank = async () => {
  try {
    const response = await fetch("./api/questions", { cache: "no-store" });
    if (!response.ok) throw new Error(`题库文件读取失败（${response.status}）。`);
    bank = validateBank(await response.json());
    renderStart();
    const recovery = readQuizRecovery();
    if (recovery && window.confirm("检测到一局尚未结束的答题，是否恢复？")) {
      state = recovery;
      renderQuiz();
      startTimer();
    } else {
      clearQuizRecovery();
    }
  } catch (error) {
    renderError(error instanceof Error ? error.message : "题库文件读取失败。");
  }
};

loadBank();
