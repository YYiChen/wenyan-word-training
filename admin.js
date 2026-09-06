const adminApp = document.querySelector("#admin-app");

const API = {
  questions: "./api/admin-question-bank",
  leaderboard: "./api/leaderboard",
  answerRecords: "./api/answer-records",
  answerRecordsImport: "./api/answer-records/import",
  questionReviews: "./api/question-reviews",
  questionBankHistory: "./api/question-bank-history",
  questionBankExport: "./api/question-bank-export",
  questionBankImport: "./api/question-bank-import",
  questionBankPreview: "./api/question-bank-import/preview",
  questionBankApply: "./api/question-bank-import/apply",
  questionBankRevoke: "./api/question-bank-history/revoke",
  adminAuth: "./api/admin-auth",
  adminLaunchSession: "./api/admin-launch-session",
  health: "./api/health",
  updateStatus: "./api/update-status",
  updateCheck: "./api/update-check",
  updateApply: "./api/update-apply",
};

const {
  calculateScoreEvent,
  normalizeScoringConfig,
  serializeScoringConfig,
  formatScoreDelta,
  MAX_STREAK_THRESHOLD,
} = window.WenyanScoring;
const {
  rebuildDuplicateReviews,
  mergeQuestionsByContent,
} = window.WenyanQuestionIdentity;

const MIN_DURATION_SECONDS = 10;
const MAX_DURATION_SECONDS = 3600;

// 这是本机课堂场景下的后台入口；服务端会再次校验，但不是完整账户系统。

const DEFAULT_QUESTION_TYPES = [
  { id: "context_meaning", label: "语境释义题", description: "根据原句判断加点实词在语境中的意思。" },
  { id: "single_choice", label: "普通单选题", description: "使用题干和四个选项完成单项选择。" },
  { id: "select_correct", label: "选择正确项", description: "从四个选项中选择正确的释义。" },
  { id: "select_incorrect", label: "选择错误项", description: "从四个选项中选择错误的释义。" },
];

let bank = null;
let leaderboard = [];
let answerRecords = [];
let reviews = {};
let questionBankHistory = [];
let activeTab = "review";
let selectedQuestionId = null;
let creatingQuestion = false;
let filters = { volume: "all", articleId: "all", query: "" };
let reviewFilters = { volume: "all", articleId: "all", status: "all", duplicate: "all", query: "" };
let expandedReviewId = null;
const savingReviewIds = new Set();
let statusMessage = "";
let loginError = "";
let adminAuthorized = false;
let adminToken = null;
let appVersion = null;
let questionBankEtag = null;
let leaderboardEtag = null;
let updateStatus = { phase: "idle", available: false, currentVersion: null, latestVersion: null, progress: 0 };
let updateModalOpen = false;
let updatePromptDismissed = false;
let updatePollTimer = null;
let updateRestarting = false;
let manualUpdateCheck = false;
let showArchivedRecords = false;
let showQuestionBankHistory = false;
const selectedAnswerRecordIds = new Set();

const render = () => {
  const isReviewTab = activeTab === "review";
  const pageTitle = {
    review: "快速审查题目",
    questions: "管理题库与成绩",
    settings: "题库结构设置",
    scoring: "计分机制",
    leaderboard: "排行榜管理",
    records: "答题记录",
  }[activeTab] || "管理后台";
  const pageSubtitle = isReviewTab
    ? "按顺序滚动查看全部题目，优先核对系统标出的正确答案。"
    : activeTab === "scoring"
        ? "选择当前全局计分机制；新开的训练局会使用保存后的规则。"
      : "题库修改会写入应用数据；排行榜写入电脑用户目录，浏览器关闭、刷新或更换浏览器后仍会保留。";
  adminApp.innerHTML = `
    <header class="admin-header">
      <div>
        <p class="eyebrow">文言实词 · 管理后台 <span class="admin-version">${appVersion ? `v${escapeHtml(appVersion)}` : "版本未知"}</span></p>
        <h1>${pageTitle}</h1>
        <p class="admin-subtitle">${pageSubtitle}</p>
      </div>
      <div class="admin-header-actions">
        <button class="admin-secondary" type="button" data-action="check-update" ${UPDATE_BUSY_PHASES.has(updateStatus.phase) ? "disabled" : ""}>${UPDATE_BUSY_PHASES.has(updateStatus.phase) ? escapeHtml(updatePhaseLabel(updateStatus.phase)) : "检查更新"}</button>
        <button class="admin-secondary" type="button" data-action="logout">退出后台</button>
        <a class="admin-home-link" href="./index.html">返回学生答题页</a>
      </div>
    </header>
    <p class="admin-notice">服务仅监听本机。题库备份在 <code>data/backups</code>；排行榜和答题记录保存在 <code>%LOCALAPPDATA%/WenyanQuiz</code>，升级应用压缩包不会覆盖它们。</p>
    <nav class="admin-tabs" aria-label="管理功能">
      <button class="admin-tab ${activeTab === "review" ? "active" : ""}" type="button" data-tab="review">快速审查</button>
      <button class="admin-tab ${activeTab === "questions" ? "active" : ""}" type="button" data-tab="questions">题库管理</button>
      <button class="admin-tab ${activeTab === "settings" ? "active" : ""}" type="button" data-tab="settings">题库结构</button>
      <button class="admin-tab ${activeTab === "scoring" ? "active" : ""}" type="button" data-tab="scoring">计分机制</button>
      <button class="admin-tab ${activeTab === "leaderboard" ? "active" : ""}" type="button" data-tab="leaderboard">排行榜管理</button>
      <button class="admin-tab ${activeTab === "records" ? "active" : ""}" type="button" data-tab="records">答题记录</button>
    </nav>
    ${activeTab === "review" ? renderReviewTab() : activeTab === "questions" ? renderQuestionTab() : activeTab === "settings" ? renderSettingsTab() : activeTab === "scoring" ? renderScoringTab() : activeTab === "records" ? renderAnswerRecordsTab() : renderLeaderboardTab()}
    ${renderUpdateModal()}
    ${typeof renderQuestionImportDialog === "function" ? renderQuestionImportDialog() : ""}
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

const wireEvents = () => {
  wireUpdateEvents();
  wireAdminAuthEvents();
  if (typeof wireQuestionImportDialogEvents === "function") wireQuestionImportDialogEvents();
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
    wireQuestionEvents();
    return;
  }
  if (activeTab === "settings") {
    wireSettingsEvents();
    return;
  }
  if (activeTab === "scoring") {
    wireScoringEvents();
    return;
  }
  if (activeTab === "records") {
    wireRecordEvents();
    return;
  }
  wireLeaderboardEvents();
};

const load = async () => {
  try {
    const [healthData, questionBank, leaderboardData, questionReviews, answerRecordData, questionBankHistoryData] = await Promise.all([
      fetchJson(API.health),
      fetchJson(API.questions),
      fetchJson(API.leaderboard),
      fetchJson(API.questionReviews),
      fetchJson(API.answerRecords),
      fetchJson(API.questionBankHistory),
    ]);
    appVersion = typeof healthData?.version === "string" && healthData.version.trim()
      ? healthData.version.trim()
      : null;
    if (!Array.isArray(questionBank.questions)) throw new Error("题库格式无效：缺少 questions 数组。");
    bank = questionBank;
    leaderboard = normalizeLeaderboard(leaderboardData);
    reviews = normalizeReviews(questionReviews);
    answerRecords = normalizeAnswerRecords(answerRecordData);
    questionBankHistory = normalizeQuestionBankHistory(questionBankHistoryData);
    selectedQuestionId = bank.questions[0]?.id || null;
    render();
    startUpdateMonitoring();
  } catch (error) {
    renderError(error instanceof Error ? error.message : "题库读取失败。");
  }
};

window.addEventListener("pagehide", () => {
  adminAuthorized = false;
  adminToken = null;
  stopUpdatePolling();
  updateModalOpen = false;
});

window.addEventListener("pageshow", (event) => {
  if (!event.persisted) return;
  adminAuthorized = false;
  adminToken = null;
  loginError = "";
  renderLogin();
});

if (hasAdminAccess()) {
  load();
} else {
  renderLogin();
}
