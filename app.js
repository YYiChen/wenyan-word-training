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
let answerRecords = [];
let answerRecordsWarning = "";
let feedbackEffectsController = null;
let feedbackEffectPlayedKey = "";
let gameStarting = false;
let feedbackTransitionTimerId = null;
let feedbackTransitionSequence = 0;
let pkMatch = null;
let pkTimerId = null;
let pkCountdownTimerId = null;
let pkEffectControllers = { player1: null, player2: null };
let pkRecordsViewMode = "solo";

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

const formatPkDuration = (milliseconds) => {
  const safeMilliseconds = Math.max(0, Number(milliseconds) || 0);
  return `${(safeMilliseconds / 1000).toFixed(2)} 秒`;
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
  else if (pkMatch?.phase === "playing") renderPkShell();
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

const normalizeAnswerRecords = (entries) => (Array.isArray(entries) ? entries : [])
  .filter((record) => record && typeof record.id === "string" && Array.isArray(record.questions))
  .map((record) => ({
    recordType: record.recordType === "pk" ? "pk" : "solo",
    id: record.id,
    matchId: String(record.matchId || "").trim(),
    name: String(record.name || "未命名").trim().slice(0, 20) || "未命名",
    score: Number(record.score) || 0,
    startedAt: Number(record.startedAt) || 0,
    finishedAt: Number(record.finishedAt) || 0,
    usedSeconds: Math.max(0, Number(record.usedSeconds) || 0),
    completedAll: Boolean(record.completedAll),
    answeredCount: Math.max(0, Number(record.answeredCount) || 0),
    correctCount: Math.max(0, Number(record.correctCount) || 0),
    wrongCount: Math.max(0, Number(record.wrongCount) || 0),
    archived: Boolean(record.archived),
    archivedAt: Math.max(0, Number(record.archivedAt) || 0),
    scoring: record.scoring ? normalizeScoringConfig(record.scoring) : null,
    context: record.context && typeof record.context === "object" ? record.context : null,
    pkMode: record.pkMode === "questions" ? "questions" : record.pkMode === "time" ? "time" : null,
    timeLimitSeconds: Number(record.timeLimitSeconds) || null,
    questionLimit: Number(record.questionLimit) || null,
    sharedQuestionIds: Array.isArray(record.sharedQuestionIds) ? record.sharedQuestionIds : [],
    players: Array.isArray(record.players) ? record.players : [],
    questions: record.questions,
  }))
  .sort((left, right) => {
    const leftTime = left.finishedAt || left.startedAt;
    const rightTime = right.finishedAt || right.startedAt;
    return rightTime - leftTime;
  });

const formatRecordDate = (timestamp) => {
  if (!timestamp) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(timestamp));
};

const loadStudentAnswerRecords = async () => {
  answerRecordsWarning = "";
  try {
    const response = await fetch("./api/student-answer-records", { cache: "no-store" });
    const result = await response.json().catch(() => null);
    if (!response.ok || !Array.isArray(result)) {
      throw new Error("答题记录文件读取失败。");
    }
    answerRecords = normalizeAnswerRecords(result).filter((record) => !record.archived);
    if (response.headers.get("X-Wenyan-Records-Status") === "unavailable") {
      answerRecordsWarning = "历史答题记录暂时不可读取，但不影响开始新的训练。";
    }
  } catch {
    answerRecords = [];
    answerRecordsWarning = "历史答题记录暂时不可读取，但不影响开始新的训练。";
  }
};

const getAnswerRecord = (recordId) => answerRecords.find((record) => record.id === recordId) || null;

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
    snapshot.sessionId = String(snapshot.sessionId || snapshot.recordId || createAnswerRecordId());
    snapshot.recordSavePromise = null;
    const recoveryQuestion = snapshot.questions[Number(snapshot.currentIndex) || 0];
    snapshot.feedbackPhase = snapshot.answeredCurrent
      ? (snapshot.selectedKey === recoveryQuestion?.answer ? "correct-feedback" : "wrong-feedback")
      : "answering";
    snapshot.feedbackAdvancing = false;
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

const scoringModeLabel = (mode) => {
  if (mode === "streak") return "连续表现模式";
  if (mode === "fixed") return "固定计分模式";
  return "规则未知";
};

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
    id: session.sessionId || session.recordId || createAnswerRecordId(),
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
  const normalizedName = String(name || "").trim().slice(0, 20);
  const wantsLeaderboard = Boolean(addToLeaderboard) && Boolean(normalizedName);
  const requirementsSatisfied = () => (
    session.recordSaved
    && (!normalizedName || session.recordName === normalizedName)
    && (!wantsLeaderboard || session.scoreSaved)
  );

  while (!requirementsSatisfied()) {
    const pending = session.recordSavePromise;
    if (pending) {
      try {
        await pending;
      } catch (error) {
        // A named submission arriving behind an anonymous save must get its
        // own retry. For an anonymous save, surface the original failure.
        if (!normalizedName && !wantsLeaderboard) throw error;
      }
      if (requirementsSatisfied()) return session.recordId || session.sessionId || null;
      continue;
    }

    session.sessionId = session.sessionId || session.recordId || createAnswerRecordId();
    session.recordSaveStatus = "saving";
    session.recordSaveError = "";
    const requestPromise = (async () => {
      const response = await fetch("./api/quiz-results", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          record: buildAnswerRecord(session),
          name: normalizedName,
          addToLeaderboard: wantsLeaderboard,
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
    session.recordSavePromise = requestPromise;
    try {
      await requestPromise;
    } catch (error) {
      session.recordSaveStatus = "error";
      session.recordSaveError = error instanceof Error ? error.message : "答题记录保存失败。";
      throw error;
    } finally {
      if (session.recordSavePromise === requestPromise) session.recordSavePromise = null;
    }
  }
  return session.recordId || session.sessionId || null;
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
  setPkShellClass(false);
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
          <button class="secondary-button pk-entry-button" type="button" data-action="pk" ${hasPlayableQuestions ? "" : "disabled"}>双人 PK</button>
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
  app.querySelector('[data-action="pk"]').addEventListener("click", () => renderPkSetup());
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

const recordOptionClass = (option, recordQuestion) => {
  if (option.key === recordQuestion.answer) return "correct";
  if (option.key === recordQuestion.selectedKey) return "wrong";
  return "";
};

const getAnsweredRecordQuestions = (record) => {
  const questions = (Array.isArray(record?.questions) ? record.questions : [])
    .filter((question) => question && question.selectedKey != null);
  const hasCompleteQuizOrder = questions.length > 0
    && questions.every((question) => Number.isInteger(Number(question.quizIndex)) && Number(question.quizIndex) >= 0);
  if (!hasCompleteQuizOrder) return questions;
  return questions
    .map((question, originalIndex) => ({ question, originalIndex }))
    .sort((left, right) => Number(left.question.quizIndex) - Number(right.question.quizIndex) || left.originalIndex - right.originalIndex)
    .map(({ question }) => question);
};

const renderRecordQuestion = (question, index) => {
  const selectedOption = question.options?.find((option) => option.key === question.selectedKey);
  const answerOption = question.options?.find((option) => option.key === question.answer);
  const status = question.isCorrect ? "回答正确" : "回答错误";
  return `
    <article class="record-question-card">
      <div class="record-question-heading">
        <strong>第 ${index + 1} 题</strong>
        <span class="record-question-status ${question.isCorrect ? "correct" : "wrong"}">${status}</span>
      </div>
      ${question.scoreDelta != null ? `<p class="record-question-score ${question.scoreTier === "streak" ? "streak" : "base"}"><strong>${escapeHtml(question.scoreLabel || "本题得分")}</strong><span>${escapeHtml(formatScoreDelta(question.scoreDelta))} 分</span>${question.correctStreak || question.wrongStreak ? `<small>${question.correctStreak ? `连续答对 ${question.correctStreak} 题` : `连续答错 ${question.wrongStreak} 题`}</small>` : ""}</p>` : ""}
      <p class="record-question-meta">${escapeHtml(question.article || "课内文章")} · 考查实词：${escapeHtml(question.word || "未标注")}</p>
      ${question.stem ? `<p class="record-question-stem">${escapeHtml(question.stem)}</p>` : ""}
      <p class="record-question-sentence">${renderSentence(question)}</p>
      <div class="record-option-list">
        ${(Array.isArray(question.options) ? question.options : []).map((option) => `
          <div class="record-option ${recordOptionClass(option, question)}">
            <span class="option-key">${escapeHtml(option.key)}</span>
            <span>${escapeHtml(option.text)}</span>
          </div>
        `).join("")}
      </div>
      <p class="record-question-answer">你的回答：${escapeHtml(selectedOption?.text || "未作答")}　正确答案：${escapeHtml(answerOption?.text || "未记录")}</p>
      <p class="record-question-explanation">解析：${escapeHtml(question.explanation || "暂无解析")}</p>
    </article>
  `;
};

const renderPkRecordQuestion = (question, index) => renderRecordQuestion(question, index);

const renderPkRecordDetail = (record) => {
  const modeLabel = record.pkMode === "questions" ? "比题数" : "比时间";
  const players = [...(Array.isArray(record.players) ? record.players : [])]
    .sort((left, right) => String(left.playerId).localeCompare(String(right.playerId)));
  const playerSections = players.map((player, playerIndex) => [
    '<section class="pk-record-player">',
    '<div class="pk-record-player-heading"><h2>玩家 ', String(playerIndex + 1), '</h2><strong>',
    String(player.score), ' 分</strong></div>',
    '<p class="record-detail-meta">答对 ', String(player.correctCount || 0), ' · 答错 ',
    String(player.wrongCount || 0), ' · 已答 ', String(player.answeredCount || 0),
    ' · 用时 ', formatPkDuration(player.usedMilliseconds || Number(player.usedSeconds || 0) * 1000), '</p>',
    '<div class="record-question-list">',
    player.questions?.length
      ? player.questions.map((question, questionIndex) => renderPkRecordQuestion(question, questionIndex)).join("")
      : '<div class="records-empty">该玩家没有保存已作答题目。</div>',
    '</div></section>',
  ].join("")).join("");
  app.innerHTML = [
    '<section class="state-screen record-detail-screen" aria-labelledby="record-detail-title">',
    '<div class="record-detail-card pk-record-detail-card">',
    '<button class="record-back-button" type="button" data-action="records">← 返回答题记录</button>',
    '<div class="record-detail-header"><div><p class="eyebrow">完整 PK 比赛记录</p>',
    '<h1 id="record-detail-title">双人 PK · ', modeLabel,
    '</h1></div><strong class="record-detail-score">',
    players.length === 2 ? String(players[0].score) + " : " + String(players[1].score) : "—",
    '</strong></div>',
    '<div class="record-detail-meta"><span>比赛时间：', escapeHtml(formatRecordDate(record.finishedAt || record.startedAt)),
    '</span><span>模式：', modeLabel, '</span><span>双方合计作答：', String(record.answeredCount || 0),
    ' 题</span><span>共同题目：', String(record.sharedQuestionIds?.length || 0),
    ' 题</span><span>计分机制：', escapeHtml(scoringModeLabel(record.scoring?.mode)),
    '</span><span>训练范围：', escapeHtml(formatLeaderboardContext(record.context)), '</span></div>',
    '<div class="pk-record-player-list">', playerSections, '</div>',
    '<div class="button-stack"><button class="secondary-button" type="button" data-action="home">返回首页</button></div>',
    '</div></section>',
  ].join("");
  app.querySelector('[data-action="records"]').addEventListener("click", renderAnswerRecords);
  app.querySelector('[data-action="home"]').addEventListener("click", renderStart);
};

const renderAnswerRecords = async () => {
  app.innerHTML = `<section class="state-screen loading-screen"><div class="loading-mark" aria-hidden="true">…</div><p class="eyebrow">本机历史答题</p><h1>正在读取答题记录</h1></section>`;
  try {
    await loadStudentAnswerRecords();
  } catch (error) {
    app.innerHTML = `
      <section class="state-screen error-screen" aria-labelledby="records-error-title">
        <div class="error-card">
          <div class="loading-mark" aria-hidden="true">!</div>
          <p class="eyebrow">答题记录</p>
          <h1 id="records-error-title">暂时无法读取</h1>
          <p class="error-copy muted">${escapeHtml(error instanceof Error ? error.message : "答题记录读取失败。")}</p>
          <div class="button-stack">
            <button class="secondary-button" type="button" data-action="home">返回首页</button>
          </div>
        </div>
      </section>
    `;
    app.querySelector('[data-action="home"]').addEventListener("click", renderStart);
    return;
  }

  const soloRecords = answerRecords.filter((record) => record.recordType !== "pk");
  const pkRecords = answerRecords.filter((record) => record.recordType === "pk");
  const visibleRecords = pkRecordsViewMode === "pk" ? pkRecords : soloRecords;
  app.innerHTML = `
    <section class="state-screen records-screen" aria-labelledby="records-title">
      <div class="records-card">
        <button class="record-back-button" type="button" data-action="home">← 返回首页</button>
        <p class="eyebrow">本机历史答题</p>
        <h1 id="records-title">答题记录</h1>
        <p class="records-intro">这里只显示教师尚未折叠的记录。点击一条记录可查看完整题目和作答情况。</p>
        ${answerRecordsWarning ? `<p class="records-warning" role="status">${escapeHtml(answerRecordsWarning)}</p>` : ""}
        <div class="record-mode-tabs" role="tablist" aria-label="记录类型">
          <button class="record-mode-tab ${pkRecordsViewMode === "solo" ? "active" : ""}" type="button" data-record-mode="solo" role="tab" aria-selected="${pkRecordsViewMode === "solo"}">单人训练 <span>${soloRecords.length}</span></button>
          <button class="record-mode-tab ${pkRecordsViewMode === "pk" ? "active" : ""}" type="button" data-record-mode="pk" role="tab" aria-selected="${pkRecordsViewMode === "pk"}">双人 PK <span>${pkRecords.length}</span></button>
        </div>
        ${visibleRecords.length === 0 ? `
          <div class="records-empty">还没有可查看的答题记录，完成一局训练后会自动保存。</div>
        ` : `
          <div class="records-list" aria-label="历史答题记录">
            ${visibleRecords.map((record) => `
              <button class="record-row" type="button" data-record-id="${escapeHtml(record.id)}">
                <span class="record-row-main"><strong>${record.recordType === "pk" ? "双人 PK" : escapeHtml(record.name)}</strong><small>${record.recordType === "pk" ? (record.pkMode === "questions" ? "比题数" : "比时间") + " · " : ""}${escapeHtml(formatRecordDate(record.finishedAt || record.startedAt))}</small></span>
                <span class="record-row-stats"><strong>${record.recordType === "pk" && record.players?.length === 2 ? String(record.players[0].score) + " : " + String(record.players[1].score) : record.score + " 分"}</strong><small>${record.recordType === "pk" ? "双方合计已答 " + record.answeredCount + " 题" : "用时 " + formatSeconds(record.usedSeconds) + " · 已答 " + record.answeredCount + " 题"}</small></span>
                <span class="record-row-arrow" aria-hidden="true">›</span>
              </button>
            `).join("")}
          </div>
        `}
        <div class="button-stack">
          <button class="primary-button" type="button" data-action="start">开始答题</button>
        </div>
      </div>
    </section>
  `;
  app.querySelectorAll("[data-record-id]").forEach((button) => {
    button.addEventListener("click", () => {
      const record = getAnswerRecord(button.dataset.recordId);
      if (record?.recordType === "pk") renderPkRecordDetail(record);
      else renderAnswerRecordDetail(button.dataset.recordId);
    });
  });
  app.querySelectorAll("[data-record-mode]").forEach((button) => {
    button.addEventListener("click", () => {
      pkRecordsViewMode = button.dataset.recordMode === "pk" ? "pk" : "solo";
      renderAnswerRecords();
    });
  });
  app.querySelector('[data-action="start"]').addEventListener("click", startGame);
  app.querySelector('[data-action="home"]').addEventListener("click", renderStart);
};

const renderAnswerRecordDetail = (recordId) => {
  const record = getAnswerRecord(recordId);
  if (!record) {
    renderAnswerRecords();
    return;
  }
  if (record.recordType === "pk") {
    renderPkRecordDetail(record);
    return;
  }
  const answeredQuestions = getAnsweredRecordQuestions(record);
  app.innerHTML = `
    <section class="state-screen record-detail-screen" aria-labelledby="record-detail-title">
      <div class="record-detail-card">
        <button class="record-back-button" type="button" data-action="records">← 返回答题记录</button>
        <div class="record-detail-header">
          <div>
            <p class="eyebrow">完整答题记录</p>
            <h1 id="record-detail-title">${escapeHtml(record.name)}</h1>
          </div>
          <strong class="record-detail-score">${record.score} 分</strong>
        </div>
        <div class="record-detail-meta">
          <span>答题时间：${escapeHtml(formatRecordDate(record.finishedAt || record.startedAt))}</span>
          <span>用时：${formatSeconds(record.usedSeconds)}</span>
          <span>计分机制：${escapeHtml(scoringModeLabel(record.scoring?.mode))}</span>
          <span>训练范围：${escapeHtml(formatLeaderboardContext(record.context))}</span>
          <span>答对：${record.correctCount}</span>
          <span>答错：${record.wrongCount}</span>
          <span>回答题目：${answeredQuestions.length}</span>
        </div>
        <div class="record-question-list">
          ${answeredQuestions.length
            ? answeredQuestions.map((question, index) => renderRecordQuestion(question, index)).join("")
            : `<div class="records-empty">本局没有完成作答的题目。</div>`}
        </div>
        <div class="button-stack">
          <button class="secondary-button" type="button" data-action="home">返回首页</button>
        </div>
      </div>
    </section>
  `;
  app.querySelector('[data-action="records"]').addEventListener("click", renderAnswerRecords);
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

const clearFeedbackTransition = () => {
  if (feedbackTransitionTimerId !== null) {
    window.clearTimeout(feedbackTransitionTimerId);
    feedbackTransitionTimerId = null;
  }
  feedbackTransitionSequence += 1;
};

const currentFeedbackPhase = (session = state) => {
  if (!session?.answeredCurrent) return "answering";
  if (session.feedbackPhase) return session.feedbackPhase;
  const question = session.questions?.[session.currentIndex];
  return session.selectedKey === question?.answer ? "correct-feedback" : "wrong-feedback";
};

const reducedMotionEnabled = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

const feedbackTransitionDelay = (isCorrect, answerDetail) => {
  if (reducedMotionEnabled()) return isCorrect ? 110 : 80;
  if (isCorrect) return answerDetail?.tier === "streak" ? 600 : 400;
  return answerDetail?.tier === "streak" ? 430 : 300;
};

const scheduleFeedbackTransition = (session, questionIndex, selectedKey, isCorrect, answerDetail) => {
  clearFeedbackTransition();
  const sequence = feedbackTransitionSequence;
  const expectedPhase = isCorrect ? "correct-feedback" : "wrong-highlight";
  feedbackTransitionTimerId = window.setTimeout(() => {
    feedbackTransitionTimerId = null;
    if (
      sequence !== feedbackTransitionSequence
      || state !== session
      || session.screen !== "quiz"
      || session.currentIndex !== questionIndex
      || session.selectedKey !== selectedKey
      || !session.answeredCurrent
      || session.feedbackAdvancing
      || currentFeedbackPhase(session) !== expectedPhase
    ) return;

    if (Date.now() >= session.deadlineAt) {
      const completedAll = isCorrect && questionIndex + 1 >= session.questions.length;
      finishGame(completedAll ? "completed" : "timeout");
      return;
    }

    if (isCorrect) {
      nextQuestion(true);
      return;
    }

    session.feedbackPhase = "wrong-feedback";
    renderQuiz();
    saveQuizRecovery(session);
  }, feedbackTransitionDelay(isCorrect, answerDetail));
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

const renderFeedback = (question, mode = "full") => {
  if (!state.answeredCurrent) return "";
  const isCorrect = state.selectedKey === question.answer;
  const phase = currentFeedbackPhase(state);
  const visualOnly = mode === "visual-only";
  const compactCorrect = isCorrect && phase === "correct-feedback";
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
    <div class="feedback-panel feedback-stage ${isCorrect ? "success" : "error"} ${resultClass} result-${effectType} ${visualOnly ? "feedback-visual-only" : ""} ${compactCorrect ? "feedback-compact" : ""} ${!isCorrect && !visualOnly ? "feedback-error-card" : ""}" data-feedback-effects-stage data-feedback-effect="${effectType}" role="${visualOnly ? "presentation" : "status"}" aria-live="${visualOnly ? "off" : "polite"}" ${visualOnly ? "aria-hidden=\"true\"" : ""}>
      <canvas class="feedback-effects-canvas" data-feedback-effects-canvas aria-hidden="true"></canvas>
      <div class="feedback-content">
        ${visualOnly ? "" : `
          <div class="score-event" aria-label="${escapeHtml(`${scoreLabel} ${scoreText} 分`)}">
            <span class="score-event-icon" aria-hidden="true">${isCorrect ? (isSuper ? "★" : "✓") : (isSuper ? "!" : "×")}</span>
            <span class="score-event-copy"><strong>${escapeHtml(scoreLabel)}</strong><b>${escapeHtml(scoreText)} 分</b></span>
          </div>
          <div class="feedback-title">${isCorrect ? "回答正确！" : "回答错误"}</div>
          ${streakText ? `<p class="feedback-streak">${escapeHtml(streakText)}</p>` : ""}
          ${!isCorrect ? `
            <div class="feedback-answer-grid" aria-label="本题作答对照">
              <div class="feedback-answer-item">
                <span>你的选择</span>
                <strong><b>${escapeHtml(selectedOption?.key || "未选择")}</b>${escapeHtml(selectedOption?.text || "未选择")}</strong>
              </div>
              <div class="feedback-answer-item feedback-answer-correct">
                <span>正确答案</span>
                <strong><b>${escapeHtml(answerOption?.key || question.answer)}</b>${escapeHtml(answerOption?.text || "未记录")}</strong>
              </div>
            </div>
            <div class="feedback-explanation">
              <span>解析</span>
              <p>${escapeHtml(question.explanation || "本题暂无补充解析。")}</p>
            </div>
            <div class="feedback-actions">
              <button class="primary-button" type="button" data-action="next">${state.currentIndex + 1 >= state.questions.length ? "查看成绩" : "下一题"} <span aria-hidden="true">→</span></button>
            </div>
          ` : compactCorrect ? "" : `<p>${escapeHtml(question.explanation || "本题暂无补充解析。")}</p>`}
        `}
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
  const feedbackPhase = currentFeedbackPhase(state);
  const showWrongFeedback = feedbackPhase === "wrong-feedback";
  const showCorrectFeedback = feedbackPhase === "correct-feedback";
  const showWrongHighlight = feedbackPhase === "wrong-highlight";

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
        <div class="answer-interaction answer-interaction-${feedbackPhase}">
          ${showWrongFeedback
            ? renderFeedback(question)
            : `
              <div class="option-list" role="group" aria-label="答案选项">${renderOptions(question)}</div>
              ${showCorrectFeedback ? renderFeedback(question, "compact") : ""}
              ${showWrongHighlight ? renderFeedback(question, "visual-only") : ""}
            `}
        </div>
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
  if (nextButton) nextButton.addEventListener("click", () => nextQuestion(false));
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

const prepareToLeaveResult = async (session) => {
  if (!session) return false;
  if (session.recordSavePromise || session.recordSaveStatus === "saving" || session.recordSaveStatus === "pending") {
    try {
      if (session.recordSavePromise) {
        await session.recordSavePromise;
      } else {
        await ensureAnswerRecordSaved(session);
      }
    } catch {
      // The failure state below gives the student an explicit retry/leave choice.
    }
  }
  if (session.recordSaveStatus !== "error") return session.recordSaveStatus === "saved";
  if (state === session && session.screen === "result") renderResult(session);
  return window.confirm("本次答题记录尚未成功保存。如果现在离开，本局记录可能无法恢复。确定离开吗？");
};

const leaveResult = async (session, destination) => {
  if (!session || session.leaveInProgress) return;
  session.leaveInProgress = true;
  const actionButtons = ["leaderboard", "restart", "home"]
    .map((action) => app.querySelector(`[data-action="${action}"]`))
    .filter(Boolean);
  actionButtons.forEach((button) => { button.disabled = true; });
  const canLeave = await prepareToLeaveResult(session);
  if (!canLeave) {
    session.leaveInProgress = false;
    if (state === session && session.screen === "result") renderResult(session);
    return;
  }
  if (destination === "restart") {
    const started = await startGame();
    if (!started) {
      session.leaveInProgress = false;
      if (state === session && session.screen === "result") renderResult(session);
      return;
    }
    session.screen = "replaced";
    return;
  }
  session.screen = destination;
  if (destination === "leaderboard") {
    await renderLeaderboard();
  } else {
    renderStart();
  }
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
        ${session.recordSaveStatus === "error" ? `<p class="record-save-error" role="alert">答题记录尚未成功保存，请再次提交姓名或留空重试；离开本页时还会再次确认。</p>` : ""}
        ${session.scoreSaved ? `
          <p class="saved-note">已将本次答题记录保存，并计入排行榜。</p>
        ` : session.recordNameFinalized && session.recordName !== "未命名" ? `
          <p class="saved-note">本次答题记录已保存，姓名：${escapeHtml(session.recordName)}。</p>
        ` : `
          <form class="score-form" data-action="score-form">
            <label for="player-name">姓名（可不填；不填则显示“未命名”）</label>
            <div class="score-form-row">
              <input id="player-name" name="name" type="text" maxlength="20" placeholder="请输入名字，可留空" autocomplete="off" />
              <button class="primary-button" type="submit" ${session.leaveInProgress || session.recordSaveStatus === "saving" ? "disabled" : ""}>保存姓名并加入排行</button>
            </div>
          </form>
        `}
        <div class="button-stack">
          <button class="${session.scoreSaved ? "primary-button" : "secondary-button"}" type="button" data-action="leaderboard" ${session.leaveInProgress ? "disabled" : ""}>查看排行榜</button>
          <button class="secondary-button" type="button" data-action="restart" ${session.leaveInProgress ? "disabled" : ""}>再来一局</button>
          <button class="secondary-button" type="button" data-action="home" ${session.leaveInProgress ? "disabled" : ""}>返回首页</button>
        </div>
      </div>
    </section>
  `;
  app.querySelector('[data-action="restart"]').addEventListener("click", () => leaveResult(session, "restart"));
  app.querySelector('[data-action="leaderboard"]').addEventListener("click", () => leaveResult(session, "leaderboard"));
  app.querySelector('[data-action="home"]').addEventListener("click", () => leaveResult(session, "home"));
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
      const waitingForWrongFeedback = ["wrong-highlight", "wrong-feedback"].includes(currentFeedbackPhase(session));
      const completedAll = session.answeredCurrent
        && !waitingForWrongFeedback
        && session.currentIndex + 1 >= session.questions.length;
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
  if (gameStarting || state?.screen === "quiz") return false;
  gameStarting = true;
  clearFeedbackTransition();
  const startButton = app.querySelector('[data-action="start"]');
  if (startButton) {
    startButton.disabled = true;
    startButton.textContent = "正在开始…";
  }
  try {
    clearQuizRecovery();
    await refreshBankBeforeStart();
    const config = getQuizConfig();
    if (startSelection.volumes.length === 0) {
      window.alert("请至少勾选一本教材册，或点击“全部教材册”。");
      return false;
    }
    if (startSelection.articleIds.length === 0) {
      window.alert("请至少勾选一篇文章，或点击“全部文章”。");
      return false;
    }
    const selectedQuestions = getSelectedQuestions();
    if (selectedQuestions.length === 0) {
      window.alert("这个范围暂时没有可用题目，请换一个教材册或篇目。");
      return false;
    }
    const startedAt = Date.now();
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
      deadlineAt: startedAt + config.durationSeconds * 1000,
      sessionId: createAnswerRecordId(),
      volumeLabels: [...startSelection.volumes],
      articleIds: [...startSelection.articleIds],
      scoringConfig: { ...config.scoring },
      correctStreak: 0,
      wrongStreak: 0,
      selectedKey: null,
      answeredCurrent: false,
      feedbackPhase: "answering",
      feedbackAdvancing: false,
      finishPromptOpen: false,
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
      startedAt,
    };
    feedbackEffectPlayedKey = "";
    renderQuiz();
    startTimer();
    saveQuizRecovery();
    return true;
  } catch (error) {
    window.alert(error instanceof Error ? `读取最新题库和计分规则失败：${error.message}` : "读取最新题库和计分规则失败。");
    return false;
  } finally {
    finishStartGame();
  }
};

const finishStartGame = () => {
  gameStarting = false;
  const startButtonAfterAttempt = app.querySelector('[data-action="start"]');
  if (startButtonAfterAttempt && state?.screen !== "quiz") {
    startButtonAfterAttempt.disabled = getSelectedQuestions().length === 0;
    startButtonAfterAttempt.textContent = "开始答题";
  }
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
  state.feedbackPhase = isCorrect ? "correct-feedback" : "wrong-highlight";
  state.feedbackAdvancing = false;
  renderQuiz();
  saveQuizRecovery();
  scheduleFeedbackTransition(state, state.currentIndex, key, isCorrect, scoreEvent);
};

const nextQuestion = (automatic = false) => {
  if (!state || state.screen !== "quiz" || !state.answeredCurrent || state.feedbackAdvancing) return;
  const phase = currentFeedbackPhase(state);
  if (automatic ? phase !== "correct-feedback" : phase !== "wrong-feedback") return;
  state.feedbackAdvancing = true;
  clearFeedbackTransition();
  if (state.currentIndex + 1 >= state.questions.length) {
    finishGame("completed");
    return;
  }
  state.currentIndex += 1;
  state.selectedKey = null;
  state.answeredCurrent = false;
  state.feedbackPhase = "answering";
  state.feedbackAdvancing = false;
  renderQuiz();
  saveQuizRecovery();
};

const finishEarly = () => {
  if (!state || state.screen !== "quiz" || state.finishPromptOpen) return;
  state.finishPromptOpen = true;
  const confirmed = window.confirm("确定要提前交卷吗？当前成绩将作为本局成绩保存。");
  if (!confirmed) {
    state.finishPromptOpen = false;
    return;
  }
  const completedAll = state.answeredCurrent && state.currentIndex + 1 >= state.questions.length;
  finishGame(completedAll ? "completed" : "manual");
};

const finishGame = (reason = "timeout") => {
  const session = state;
  if (!session || session.screen === "result") return;
  clearFeedbackTransition();
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

const PK_TIME_OPTIONS = [30, 60, 90, 120];
const PK_QUESTION_OPTIONS = [5, 10, 15, 20, 30];
let pkSetup = { mode: "time", timeLimitSeconds: 60, questionLimit: 10 };
let pkStarting = false;
let pkSetupError = "";

const pkPlayerDefaults = (playerId, questions) => ({
  playerId,
  questions,
  currentIndex: 0,
  score: 0,
  answeredCount: 0,
  correctCount: 0,
  wrongCount: 0,
  correctStreak: 0,
  wrongStreak: 0,
  selectedKey: null,
  answeredCurrent: false,
  answerDetails: [],
  finished: false,
  finishedAt: 0,
  completedAt: 0,
  usedMilliseconds: 0,
  feedbackToken: 0,
  feedbackPhase: "answering",
});

const getPkPlayer = (playerId) => pkMatch?.players?.[playerId] || null;

const setPkShellClass = (enabled) => {
  app.classList.toggle("app-shell-pk", Boolean(enabled));
};

const destroyPkEffect = (playerId) => {
  pkEffectControllers[playerId]?.destroy();
  pkEffectControllers[playerId] = null;
};

const destroyPkEffects = () => {
  destroyPkEffect("player1");
  destroyPkEffect("player2");
};

const clearPkTimers = () => {
  if (pkTimerId !== null) {
    window.clearInterval(pkTimerId);
    pkTimerId = null;
  }
  if (pkCountdownTimerId !== null) {
    window.clearInterval(pkCountdownTimerId);
    pkCountdownTimerId = null;
  }
};

const getPkAvailableQuestions = () => (
  bank && Array.isArray(bank.questions) ? getSelectedQuestions() : []
);

const renderPkSetup = (errorMessage = "") => {
  // Keep this renderer safe even if an old cached handler accidentally passes
  // a browser event object instead of a setup error string.
  pkSetupError = typeof errorMessage === "string" ? errorMessage : "";
  clearFeedbackTransition();
  window.clearInterval(timerId);
  destroyFeedbackEffects();
  clearQuizRecovery();
  clearPkTimers();
  destroyPkEffects();
  pkMatch = null;
  pkStarting = false;
  setPkShellClass(true);
  const availableCount = getPkAvailableQuestions().length;
  const hasEnoughQuestions = pkSetup.mode !== "questions" || availableCount >= pkSetup.questionLimit;
  const canStart = availableCount > 0 && hasEnoughQuestions;
  const scopeText = (startSelection.volumes.length ? startSelection.volumes.join("、") : "未选择教材册")
    + " · " + (startSelection.articleIds.length ? startSelection.articleIds.length + " 篇文章" : "未选择篇目");
  app.innerHTML = [
    '<section class="state-screen pk-setup-screen" aria-labelledby="pk-setup-title">',
    '<div class="pk-setup-card">',
    '<button class="record-back-button" type="button" data-action="pk-home">← 返回首页</button>',
    '<p class="eyebrow">课堂双人对战</p><h1 id="pk-setup-title">双人 PK</h1>',
    '<p class="pk-setup-intro">两位同学共用当前训练范围，各自看到相同题目集合，但题目顺序和选项顺序独立随机。</p>',
    '<div class="pk-scope-summary"><span>当前范围</span><strong>', escapeHtml(scopeText),
    '</strong><small>当前可用题目：', String(availableCount), ' 道</small></div>',
    pkSetupError ? '<p class="pk-setup-error" role="alert">' + escapeHtml(pkSetupError) + '</p>' : '',
    '<div class="pk-mode-grid" role="tablist" aria-label="选择 PK 模式">',
    '<button class="pk-mode-choice ', pkSetup.mode === "time" ? "active" : "", '" type="button" data-pk-mode="time"><strong>比时间</strong><span>在规定时间内尽可能多答题</span></button>',
    '<button class="pk-mode-choice ', pkSetup.mode === "questions" ? "active" : "", '" type="button" data-pk-mode="questions"><strong>比题数</strong><span>完成规定题数后比较分数</span></button>',
    '</div>',
    '<div class="pk-setting-block ', pkSetup.mode === "time" ? "" : "hidden", '" data-pk-settings="time"><span class="pk-setting-label">比赛时间</span><div class="pk-choice-row">',
    PK_TIME_OPTIONS.map((seconds) => '<button class="pk-setting-choice ' + (pkSetup.timeLimitSeconds === seconds ? "active" : "") + '" type="button" data-pk-time="' + seconds + '">' + seconds + ' 秒</button>').join(""),
    '</div></div>',
    '<div class="pk-setting-block ', pkSetup.mode === "questions" ? "" : "hidden", '" data-pk-settings="questions"><span class="pk-setting-label">比赛题数</span><div class="pk-choice-row">',
    PK_QUESTION_OPTIONS.map((count) => '<button class="pk-setting-choice ' + (pkSetup.questionLimit === count ? "active" : "") + '" type="button" data-pk-count="' + count + '" ' + (count > availableCount ? "disabled" : "") + '>' + count + ' 题</button>').join(""),
    '</div><small class="pk-setting-help">题数模式需要至少有足够的可用题目；题目不足的选项已禁用。</small></div>',
    '<p class="pk-setup-rule">计分方式沿用教师后台当前规则；PK 分数不会写入普通排行榜。</p>',
    '<div class="button-stack"><button class="primary-button" type="button" data-action="pk-start" ', canStart ? "" : "disabled", '>进入倒计时</button></div>',
    '</div></section>',
  ].join("");
  app.querySelectorAll("[data-pk-mode]").forEach((button) => {
    button.addEventListener("click", () => {
      pkSetup.mode = button.dataset.pkMode === "questions" ? "questions" : "time";
      renderPkSetup();
    });
  });
  app.querySelectorAll("[data-pk-time]").forEach((button) => {
    button.addEventListener("click", () => {
      pkSetup.timeLimitSeconds = Number(button.dataset.pkTime);
      renderPkSetup();
    });
  });
  app.querySelectorAll("[data-pk-count]").forEach((button) => {
    button.addEventListener("click", () => {
      if (button.disabled) return;
      pkSetup.questionLimit = Number(button.dataset.pkCount);
      renderPkSetup();
    });
  });
  app.querySelector('[data-action="pk-home"]').addEventListener("click", renderStart);
  app.querySelector('[data-action="pk-start"]').addEventListener("click", startPkMatch);
};

const createPkMatch = (selectedQuestions) => {
  const commonQuestions = pkSetup.mode === "questions"
    ? shuffle(selectedQuestions).slice(0, pkSetup.questionLimit)
    : selectedQuestions;
  const matchId = "pk-" + Date.now() + "-" + Math.random().toString(36).slice(2, 10);
  return {
    screen: "pk",
    phase: "countdown",
    matchId,
    mode: pkSetup.mode,
    timeLimitSeconds: pkSetup.mode === "time" ? pkSetup.timeLimitSeconds : null,
    questionLimit: pkSetup.mode === "questions" ? pkSetup.questionLimit : null,
    countdownValue: "3",
    countdownIndex: 0,
    startedAt: 0,
    deadlineAt: 0,
    finishedAt: 0,
    finishReason: "in_progress",
    volumeLabels: [...startSelection.volumes],
    articleIds: [...startSelection.articleIds],
    scoringConfig: { ...getQuizConfig().scoring },
    sharedQuestionIds: commonQuestions.map((question) => question.id),
    players: {
      player1: pkPlayerDefaults("player1", shuffle(commonQuestions).map(shuffleQuestionOptions)),
      player2: pkPlayerDefaults("player2", shuffle(commonQuestions).map(shuffleQuestionOptions)),
    },
    recordSaveStatus: "pending",
    recordSaveError: "",
    recordSavePromise: null,
    recordSaved: false,
  };
};

const renderPkCountdown = () => {
  setPkShellClass(true);
  app.innerHTML = [
    '<section class="state-screen pk-countdown-screen" aria-live="assertive">',
    '<div class="pk-countdown-card"><p class="eyebrow">双人 PK 即将开始</p>',
    '<div class="pk-countdown-number">', escapeHtml(pkMatch?.countdownValue || "3"),
    '</div><p>双方准备好后，比赛时间从“开始！”开始计算。</p></div></section>',
  ].join("");
};

const startPkCountdown = () => {
  renderPkCountdown();
  const sequence = ["3", "2", "1", "开始!"];
  pkMatch.countdownIndex = 0;
  pkMatch.countdownValue = sequence[0];
  pkCountdownTimerId = window.setInterval(() => {
    if (!pkMatch || pkMatch.phase !== "countdown") {
      clearPkTimers();
      return;
    }
    pkMatch.countdownIndex += 1;
    if (pkMatch.countdownIndex >= sequence.length) {
      clearPkTimers();
      startPkPlaying();
      return;
    }
    pkMatch.countdownValue = sequence[pkMatch.countdownIndex];
    renderPkCountdown();
  }, 650);
};

const startPkMatch = async () => {
  if (pkStarting) return;
  pkStarting = true;
  const button = app.querySelector('[data-action="pk-start"]');
  if (button) {
    button.disabled = true;
    button.textContent = "正在读取题库…";
  }
  try {
    await refreshBankBeforeStart();
    const available = getPkAvailableQuestions();
    if (available.length === 0) throw new Error("当前范围没有可用题目。");
    if (pkSetup.mode === "questions" && available.length < pkSetup.questionLimit) {
      throw new Error("题库中只有 " + available.length + " 道可用题目，无法进行 " + pkSetup.questionLimit + " 题 PK。");
    }
    pkMatch = createPkMatch(available);
    startPkCountdown();
  } catch (error) {
    renderPkSetup(error instanceof Error ? error.message : "PK 题库读取失败。");
  } finally {
    pkStarting = false;
  }
};

const startPkPlaying = () => {
  if (!pkMatch || pkMatch.phase !== "countdown") return;
  const startedAt = Date.now();
  pkMatch.phase = "playing";
  pkMatch.startedAt = startedAt;
  pkMatch.deadlineAt = pkMatch.mode === "time" ? startedAt + pkMatch.timeLimitSeconds * 1000 : 0;
  renderPkShell();
  startPkTimer();
};

const pkDisplayTime = () => {
  if (!pkMatch?.startedAt) return "00:00";
  if (pkMatch.mode === "time") return formatSeconds(Math.max(0, Math.ceil((pkMatch.deadlineAt - Date.now()) / 1000)));
  return formatSeconds(Math.max(0, Math.floor((Date.now() - pkMatch.startedAt) / 1000)));
};

const updatePkScoreboard = () => {
  if (!pkMatch) return;
  const players = pkMatch.players;
  const scoreDifference = players.player1.score - players.player2.score;
  ["player1", "player2"].forEach((playerId) => {
    const player = players[playerId];
    const score = app.querySelector('[data-pk-score="' + playerId + '"]');
    const progress = app.querySelector('[data-pk-progress="' + playerId + '"]');
    const status = app.querySelector('[data-pk-status="' + playerId + '"]');
    const lead = app.querySelector('[data-pk-lead="' + playerId + '"]');
    const side = score?.closest(".pk-score-side");
    const isLeading = scoreDifference !== 0 && (playerId === "player1" ? scoreDifference > 0 : scoreDifference < 0);
    if (score) score.textContent = String(player.score);
    if (progress) progress.textContent = player.answeredCount + " / " + player.questions.length;
    if (status) status.textContent = player.finished ? "已完成 · 等待对手" : player.correctCount + " 对 · " + player.wrongCount + " 错";
    if (side) side.classList.toggle("is-leading", isLeading);
    if (lead) {
      lead.hidden = !isLeading;
      lead.textContent = isLeading ? "领先" : "";
    }
  });
  const clock = app.querySelector("[data-pk-clock]");
  if (clock) clock.textContent = pkDisplayTime();
  const label = app.querySelector("[data-pk-mode-label]");
  if (label) label.textContent = pkMatch.mode === "time" ? "比时间 · " + pkMatch.timeLimitSeconds + " 秒" : "比题数 · " + pkMatch.questionLimit + " 题";
};

const pkQuestionPrompt = (question) => {
  const isContextMeaning = !question.type || question.type === "context_meaning";
  return isContextMeaning
    ? "句中“" + escapeHtml(question.word || "") + "”的意思是："
    : escapeHtml(question.stem ? "请选择最符合题意的一项：" : "请选择答案：");
};

const renderPkOptions = (player, question) => question.options.map((option) => {
  let resultClass = "";
  if (player.answeredCurrent) {
    if (option.key === question.answer) resultClass = "correct";
    else if (option.key === player.selectedKey) resultClass = "wrong";
  }
  return [
    '<button class="option-button pk-option-button ', resultClass,
    '" type="button" data-pk-option="', escapeHtml(option.key), '" ',
    player.answeredCurrent || player.finished || pkMatch.phase !== "playing" ? "disabled" : "",
    '><span class="option-key">', escapeHtml(option.key), '</span><span>', escapeHtml(option.text), '</span></button>',
  ].join("");
}).join("");

const pkFeedbackText = (player, question) => {
  if (!player.answeredCurrent) return "";
  const detail = player.answerDetails[player.answerDetails.length - 1];
  const isCorrect = player.selectedKey === question.answer;
  const selected = question.options.find((option) => option.key === player.selectedKey);
  const answer = question.options.find((option) => option.key === question.answer);
  return [
    '<div class="pk-feedback-badge ', isCorrect ? "correct" : "wrong", '">',
    '<strong>', isCorrect ? "回答正确" : "回答错误", '</strong><span>',
    escapeHtml(detail?.scoreText || ""), ' 分</span>',
    !isCorrect ? '<small>你的选择：' + escapeHtml(selected?.text || "未选择") + ' · 正确答案：' + escapeHtml(answer?.text || "未记录") + '</small>' : "",
    '</div>',
  ].join("");
};

const mountPkEffect = (playerId, effectType, effectKey) => {
  const stage = app.querySelector('[data-pk-stage="' + playerId + '"]');
  const canvas = app.querySelector('[data-pk-canvas="' + playerId + '"]');
  if (!stage || !canvas || !effectType || !window.WenyanFeedbackEffects?.create) return;
  destroyPkEffect(playerId);
  const controller = window.WenyanFeedbackEffects.create({
    stage,
    canvas,
    onResult(type) {
      stage.classList.remove("result-correct", "result-super-correct", "result-wrong", "result-super-wrong");
      void stage.offsetWidth;
      stage.classList.add("result-" + type);
    },
  });
  pkEffectControllers[playerId] = controller;
  if (pkMatch?.phase === "playing") {
    controller.play(effectType);
    stage.dataset.effectKey = effectKey;
  }
};

const renderPkPlayerPanel = (playerId) => {
  if (!pkMatch) return;
  const player = getPkPlayer(playerId);
  const panel = app.querySelector('[data-pk-player-panel="' + playerId + '"]');
  if (!panel || !player) return;
  destroyPkEffect(playerId);
  const question = player.questions[player.currentIndex];
  if (!question || player.finished) {
    panel.innerHTML = [
      '<div class="pk-player-heading"><span class="pk-player-label">', playerId === "player1" ? "玩家 1" : "玩家 2",
      '</span><span class="pk-player-waiting">本侧已完成</span></div>',
      '<div class="pk-waiting-panel"><strong>等待对手完成</strong><p>请等待另一位同学完成，比赛结束后统一结算。</p></div>',
    ].join("");
    return;
  }
  const isContextMeaning = !question.type || question.type === "context_meaning";
  const title = isContextMeaning ? renderSentence(question) : escapeHtml(question.stem || question.sentence || "请选择答案");
  const answerDetail = player.answerDetails[player.answerDetails.length - 1];
  const effectType = player.answeredCurrent ? getFeedbackEffectType(player.selectedKey === question.answer, answerDetail) : "";
  const effectKey = pkMatch.matchId + ":" + playerId + ":" + player.currentIndex + ":" + player.answerDetails.length;
  panel.innerHTML = [
    '<div class="pk-player-heading"><span class="pk-player-label">', playerId === "player1" ? "玩家 1" : "玩家 2",
    '</span><span class="pk-player-question">第 ', String(player.currentIndex + 1), ' / ', String(player.questions.length), ' 题</span></div>',
    '<div class="pk-question-card" style="', quizCardStyle(), '">',
    '<div class="question-kicker">考查实词：', escapeHtml(question.word || "未标注"), '</div>',
    '<h2 class="pk-question-title">', title, '</h2>',
    '<p class="question-source">——', escapeHtml(formatArticleLabel(question.article)), '</p>',
    '<p class="question-prompt">', pkQuestionPrompt(question), '</p>',
    renderContext(question),
    renderSupportingItems(question, player.answeredCurrent),
    '<div class="pk-option-list" role="group" aria-label="玩家选项">', renderPkOptions(player, question), '</div>',
    '<div class="pk-feedback-stage ', effectType ? "result-" + effectType : "", '" data-pk-stage="', playerId,
    '" data-pk-effect="', effectType, '" data-pk-effect-key="', escapeHtml(effectKey), '">',
    '<canvas class="feedback-effects-canvas" data-pk-canvas="', playerId, '" aria-hidden="true"></canvas>',
    '<div class="pk-feedback-content">', pkFeedbackText(player, question), '</div></div>',
    '</div>',
  ].join("");
  if (effectType) mountPkEffect(playerId, effectType, effectKey);
  panel.querySelectorAll("[data-pk-option]").forEach((button) => {
    button.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      submitPkAnswer(playerId, button.dataset.pkOption);
    });
  });
};

const renderPkShell = () => {
  if (!pkMatch) return;
  setPkShellClass(true);
  app.innerHTML = [
    '<section class="pk-shell" aria-labelledby="pk-title">',
    '<header class="pk-topbar"><div><p class="eyebrow">课堂双人对战</p><h1 id="pk-title">双人 PK</h1>',
    '<span class="pk-mode-label" data-pk-mode-label></span></div>',
    '<div class="pk-scoreboard">',
    '<div class="pk-score-side pk-score-side-left"><span>玩家 1</span><strong data-pk-score="player1">0</strong><small data-pk-status="player1">0 对 · 0 错</small><em class="pk-lead-badge" data-pk-lead="player1" hidden>领先</em><b data-pk-progress="player1">0 / 0</b></div>',
    '<div class="pk-clock"><span>比赛时间</span><strong data-pk-clock>00:00</strong><small>双方共用同一时钟</small></div>',
    '<div class="pk-score-side pk-score-side-right"><span>玩家 2</span><strong data-pk-score="player2">0</strong><small data-pk-status="player2">0 对 · 0 错</small><em class="pk-lead-badge" data-pk-lead="player2" hidden>领先</em><b data-pk-progress="player2">0 / 0</b></div>',
    '</div><button class="pk-exit-button" type="button" data-action="pk-exit">结束比赛</button></header>',
    '<div class="pk-divider" aria-hidden="true"></div>',
    '<div class="pk-player-grid"><section class="pk-player-panel" data-pk-player-panel="player1"></section><section class="pk-player-panel" data-pk-player-panel="player2"></section></div>',
    '<div class="pk-footer"><button class="font-control-button" type="button" data-action="font-decrease">A−</button><span class="font-scale-value">字号 ', formatFontScale(quizFontScale), '</span><button class="font-control-button" type="button" data-action="font-increase">A+</button><button class="font-reset-button" type="button" data-action="font-reset">重置</button></div>',
    '</section>',
  ].join("");
  renderPkPlayerPanel("player1");
  renderPkPlayerPanel("player2");
  updatePkScoreboard();
  app.querySelector('[data-action="pk-exit"]').addEventListener("click", exitPkMatch);
  app.querySelector('[data-action="font-decrease"]').addEventListener("click", () => adjustQuizFontScale(-FONT_SCALE_STEP));
  app.querySelector('[data-action="font-increase"]').addEventListener("click", () => adjustQuizFontScale(FONT_SCALE_STEP));
  app.querySelector('[data-action="font-reset"]').addEventListener("click", () => setQuizFontScale(DEFAULT_FONT_SCALE));
};

const startPkTimer = () => {
  window.clearInterval(pkTimerId);
  const match = pkMatch;
  pkTimerId = window.setInterval(() => {
    if (pkMatch !== match || match.phase !== "playing") {
      window.clearInterval(pkTimerId);
      pkTimerId = null;
      return;
    }
    if (match.mode === "time" && Date.now() >= match.deadlineAt) {
      finishPkMatch("timeout");
      return;
    }
    updatePkScoreboard();
  }, 100);
};

const pkFeedbackDelay = (isCorrect, detail) => {
  if (reducedMotionEnabled()) return 80;
  if (detail?.tier === "streak") return isCorrect ? 520 : 460;
  return isCorrect ? 340 : 360;
};

const advancePkPlayer = (playerId, expectedIndex, expectedToken) => {
  if (!pkMatch || pkMatch.phase !== "playing") return;
  const player = getPkPlayer(playerId);
  if (!player || player.currentIndex !== expectedIndex || player.feedbackToken !== expectedToken || !player.answeredCurrent) return;
  if (pkMatch.mode === "time" && Date.now() >= pkMatch.deadlineAt) {
    finishPkMatch("timeout");
    return;
  }
  player.feedbackPhase = "answering";
  player.answeredCurrent = false;
  player.selectedKey = null;
  if (player.currentIndex + 1 >= player.questions.length) {
    player.finished = true;
    player.finishedAt = player.completedAt || Date.now();
    player.usedMilliseconds = Math.max(0, player.finishedAt - pkMatch.startedAt);
  } else {
    player.currentIndex += 1;
  }
  renderPkPlayerPanel(playerId);
  updatePkScoreboard();
  if (pkMatch.mode === "questions" && pkMatch.players.player1.finished && pkMatch.players.player2.finished) {
    finishPkMatch("completed");
  }
};

const submitPkAnswer = (playerId, key) => {
  if (!pkMatch || pkMatch.phase !== "playing") return;
  const player = getPkPlayer(playerId);
  if (!player || player.finished || player.answeredCurrent) return;
  if (pkMatch.mode === "time" && Date.now() >= pkMatch.deadlineAt) {
    finishPkMatch("timeout");
    return;
  }
  const question = player.questions[player.currentIndex];
  if (!question || !question.options.some((option) => option.key === key)) return;
  const isCorrect = key === question.answer;
  const scoreEvent = calculateScoreEvent(pkMatch.scoringConfig, isCorrect, {
    correctStreak: player.correctStreak,
    wrongStreak: player.wrongStreak,
  });
  player.selectedKey = key;
  player.answeredCurrent = true;
  player.feedbackPhase = isCorrect ? "correct-feedback" : "wrong-highlight";
  player.answeredCount += 1;
  player.score += scoreEvent.scoreDelta;
  player.correctStreak = scoreEvent.correctStreak;
  player.wrongStreak = scoreEvent.wrongStreak;
  if (isCorrect) player.correctCount += 1;
  else player.wrongCount += 1;
  player.answerDetails.push({
    questionIndex: player.currentIndex,
    questionId: question.id,
    selectedKey: key,
    isCorrect,
    ...scoreEvent,
  });
  if (player.currentIndex + 1 >= player.questions.length) {
    // Completion time is the moment the final answer is accepted, not the
    // later moment when the short feedback animation finishes.
    player.completedAt = Date.now();
  }
  player.feedbackToken += 1;
  const expectedIndex = player.currentIndex;
  const expectedToken = player.feedbackToken;
  renderPkPlayerPanel(playerId);
  updatePkScoreboard();
  window.setTimeout(
    () => advancePkPlayer(playerId, expectedIndex, expectedToken),
    pkFeedbackDelay(isCorrect, scoreEvent),
  );
};

const getPkOutcome = (match) => {
  const first = match.players.player1;
  const second = match.players.player2;
  if (first.score > second.score) return { winner: "player1", label: "玩家 1 获胜" };
  if (second.score > first.score) return { winner: "player2", label: "玩家 2 获胜" };
  if (match.mode === "questions" && Math.abs(first.usedMilliseconds - second.usedMilliseconds) > 500) {
    return first.usedMilliseconds < second.usedMilliseconds
      ? { winner: "player1", label: "玩家 1 获胜（用时更短）" }
      : { winner: "player2", label: "玩家 2 获胜（用时更短）" };
  }
  return { winner: "draw", label: "平局" };
};

const buildPkPlayerRecord = (player) => ({
  playerId: player.playerId,
  score: player.score,
  answeredCount: player.answeredCount,
  correctCount: player.correctCount,
  wrongCount: player.wrongCount,
  usedMilliseconds: player.usedMilliseconds,
  usedSeconds: Math.max(0, Math.floor(player.usedMilliseconds / 1000)),
  completed: player.finished,
  finishedAt: player.finishedAt,
  questions: player.answerDetails.map((detail) => ({
    ...player.questions[detail.questionIndex],
    quizIndex: detail.questionIndex,
    selectedKey: detail.selectedKey,
    isCorrect: detail.isCorrect,
    scoreDelta: detail.scoreDelta,
    scoreTier: detail.tier || null,
    scoreLabel: detail.label || null,
    correctStreak: detail.correctStreak || 0,
    wrongStreak: detail.wrongStreak || 0,
  })),
});

const buildPkRecord = (match) => ({
  id: "pk-" + match.matchId,
  matchId: match.matchId,
  recordType: "pk",
  startedAt: match.startedAt,
  finishedAt: match.finishedAt,
  pkMode: match.mode,
  timeLimitSeconds: match.timeLimitSeconds,
  questionLimit: match.questionLimit,
  sharedQuestionIds: [...match.sharedQuestionIds],
  scoring: { ...match.scoringConfig },
  context: buildQuizContext({
    volumeLabels: match.volumeLabels,
    articleIds: match.articleIds,
    durationSeconds: match.timeLimitSeconds || 0,
    scoringConfig: match.scoringConfig,
  }),
  players: [buildPkPlayerRecord(match.players.player1), buildPkPlayerRecord(match.players.player2)],
  questions: [],
});

const savePkRecord = async (match) => {
  if (!match || match.recordSaved) return;
  if (match.recordSavePromise) return match.recordSavePromise;
  match.recordSaveStatus = "saving";
  match.recordSaveError = "";
  const requestPromise = (async () => {
    const response = await fetch("./api/pk-results", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ record: buildPkRecord(match) }),
    });
    const payload = await response.json().catch(() => null);
    if (!response.ok || !payload?.ok) throw new Error(payload?.error || "PK 比赛记录保存失败。");
    match.recordSaved = true;
    match.recordSaveStatus = "saved";
    return payload.data?.record || null;
  })();
  match.recordSavePromise = requestPromise;
  try {
    await requestPromise;
  } catch (error) {
    match.recordSaveStatus = "error";
    match.recordSaveError = error instanceof Error ? error.message : "PK 比赛记录保存失败。";
    throw error;
  } finally {
    if (match.recordSavePromise === requestPromise) match.recordSavePromise = null;
  }
};

const renderPkResult = () => {
  if (!pkMatch) return;
  const outcome = getPkOutcome(pkMatch);
  const first = pkMatch.players.player1;
  const second = pkMatch.players.player2;
  const animating = pkMatch.phase === "result-animation";
  const outcomeClass = outcome.winner === "player1"
    ? "winner-player1"
    : outcome.winner === "player2"
      ? "winner-player2"
      : "is-draw";
  setPkShellClass(true);
  app.innerHTML = [
    '<section class="state-screen pk-result-screen ', animating ? "is-animating " : "", outcomeClass, '" aria-labelledby="pk-result-title">',
    '<div class="pk-result-card"><div class="pk-result-celebration" aria-hidden="true">', Array.from({ length: 14 }, () => "<i></i>").join(""), '</div><p class="eyebrow">双人 PK 结算</p>',
    '<h1 id="pk-result-title">', escapeHtml(outcome.label), '</h1>',
    '<p class="pk-result-mode">', pkMatch.mode === "time" ? "比时间 · " + pkMatch.timeLimitSeconds + " 秒" : "比题数 · " + pkMatch.questionLimit + " 题", '</p>',
    '<div class="pk-result-scoreboard"><div class="pk-result-player ', outcome.winner === "player1" ? "winner" : outcome.winner === "draw" ? "draw" : "loser", '"><span>玩家 1</span><strong>', String(first.score), '</strong><small>答对 ', String(first.correctCount), ' · 答错 ', String(first.wrongCount), '</small></div>',
    '<div class="pk-result-vs">VS</div><div class="pk-result-player ', outcome.winner === "player2" ? "winner" : outcome.winner === "draw" ? "draw" : "loser", '"><span>玩家 2</span><strong>', String(second.score), '</strong><small>答对 ', String(second.correctCount), ' · 答错 ', String(second.wrongCount), '</small></div></div>',
    '<p class="pk-result-meta">比赛用时 ', formatPkDuration(pkMatch.finishedAt - pkMatch.startedAt), ' · 本场不计入普通排行榜</p>',
    pkMatch.recordSaveStatus === "saving" ? '<p class="saved-note">正在保存 PK 比赛记录…</p>' : "",
    pkMatch.recordSaveStatus === "saved" ? '<p class="saved-note">PK 比赛记录已保存到本机。</p>' : "",
    pkMatch.recordSaveStatus === "error" ? '<p class="record-save-error">比赛记录保存失败：' + escapeHtml(pkMatch.recordSaveError) + '</p><button class="secondary-button pk-retry-record" type="button" data-action="pk-retry" ' + (animating ? "disabled" : "") + '>重新保存比赛记录</button>' : "",
    '<div class="button-stack"><button class="primary-button" type="button" data-action="pk-again" ', animating ? "disabled" : "", '>再来一场</button>',
    '<button class="secondary-button" type="button" data-action="pk-home" ', animating ? "disabled" : "", '>返回首页</button></div>',
    '</div></section>',
  ].join("");
  app.querySelector('[data-action="pk-again"]')?.addEventListener("click", () => {
    pkMatch = null;
    renderPkSetup();
  });
  app.querySelector('[data-action="pk-retry"]')?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    button.textContent = "正在重试…";
    try {
      await savePkRecord(pkMatch);
    } catch {
      // The result page keeps the retry action available.
    } finally {
      if (pkMatch) renderPkResult();
    }
  });
  app.querySelector('[data-action="pk-home"]')?.addEventListener("click", () => {
    pkMatch = null;
    renderStart();
  });
};

const finishPkMatch = (reason = "completed") => {
  const match = pkMatch;
  if (!match || !["playing", "countdown"].includes(match.phase)) return;
  clearPkTimers();
  destroyPkEffects();
  match.phase = "result-animation";
  match.finishReason = reason;
  match.finishedAt = Date.now();
  Object.values(match.players).forEach((player) => {
    player.feedbackToken += 1;
    if (!player.finished) {
      const completedAllQuestions = player.completedAt > 0 && player.answerDetails.length >= player.questions.length;
      if (completedAllQuestions) player.finished = true;
      player.finishedAt = completedAllQuestions ? player.completedAt : match.finishedAt;
      player.usedMilliseconds = Math.max(0, player.finishedAt - match.startedAt);
    }
  });
  renderPkResult();
  void savePkRecord(match).then(() => {
    if (pkMatch === match && match.phase === "result-animation") renderPkResult();
  }).catch(() => {
    if (pkMatch === match && match.phase === "result-animation") renderPkResult();
  });
  window.setTimeout(() => {
    if (pkMatch !== match || match.phase !== "result-animation") return;
    match.phase = "result";
    renderPkResult();
  }, reducedMotionEnabled() ? 100 : 1800);
};

const exitPkMatch = () => {
  if (!pkMatch) {
    renderStart();
    return;
  }
  if (pkMatch.phase === "result" || pkMatch.phase === "result-animation") {
    clearPkTimers();
    destroyPkEffects();
    pkMatch = null;
    renderStart();
    return;
  }
  if (!window.confirm("比赛正在进行，确定结束本场比赛吗？本场不会保存为正式 PK 记录。")) return;
  clearPkTimers();
  destroyPkEffects();
  pkMatch = null;
  renderStart();
};

window.addEventListener("beforeunload", (event) => {
  if (state?.screen !== "quiz" && !["countdown", "playing"].includes(pkMatch?.phase)) return;
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
      if (state.answeredCurrent && currentFeedbackPhase(state) === "correct-feedback") {
        scheduleFeedbackTransition(
          state,
          state.currentIndex,
          state.selectedKey,
          true,
          state.answerDetails[state.answerDetails.length - 1],
        );
      }
    } else {
      clearQuizRecovery();
    }
  } catch (error) {
    renderError(error instanceof Error ? error.message : "题库文件读取失败。");
  }
};

loadBank();
