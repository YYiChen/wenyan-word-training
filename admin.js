const adminApp = document.querySelector("#admin-app");

const API = {
  questions: "./api/questions",
  leaderboard: "./api/leaderboard",
  answerRecords: "./api/answer-records",
  answerRecordsImport: "./api/answer-records/import",
  questionReviews: "./api/question-reviews",
  questionBankHistory: "./api/question-bank-history",
  questionBankImport: "./api/question-bank-import",
  questionBankRevoke: "./api/question-bank-history/revoke",
  adminAuth: "./api/admin-auth",
  adminSettings: "./api/admin-settings",
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

const QUESTION_BANK_TEMPLATE_EXAMPLE = {
  schemaVersion: "3.0",
  title: "请填写题库名称",
  description: "请说明适用年级、教材范围和题目来源。",
  questionTypes: [
    { id: "context_meaning", label: "语境释义题", description: "根据原句判断实词在语境中的意思。" },
  ],
  books: [
    { id: "bx1", label: "必修上册", order: 1 },
  ],
  catalog: [
    { id: "bx1_article_001", volume: "必修上册", unit: "第三单元", title: "劝学", author: "荀子" },
  ],
  quizDefaults: {
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
  },
  questions: [
    {
      id: "bx1_article_001_001",
      number: 1,
      type: "context_meaning",
      articleId: "bx1_article_001",
      article: "劝学",
      volume: "必修上册",
      unit: "第三单元",
      word: "利",
      sentence: "金就砺则利。",
      targetStart: 4,
      targetOccurrence: 1,
      stem: "",
      options: [
        { key: "A", text: "锋利" },
        { key: "B", text: "利益" },
        { key: "C", text: "有利" },
        { key: "D", text: "顺利" },
      ],
      answer: "A",
      explanation: "利：锋利。",
    },
  ],
};

const QUESTION_BANK_FORMAT_GUIDE = `# JSON模版导入说明

这是“文言实词限时训练”的题库导入说明与可直接复制的 JSON 模版。请使用 UTF-8 编码保存为 .json 文件后，再在后台使用“新增导入题库（合并）”。本文件末尾附有当前系统完整的题型、教材册、篇目 ID 目录和一份可复制的 JSON 示例。

## 导入规则

1. 题库必须包含“questions”数组，且至少有一道题。
2. “catalog”是篇目目录，至少要有一篇文章；题目的“articleId”必须能在其中找到，题目的“article”和“volume”必须分别与该篇目的“title”和“volume”一致。
3. “books”是教材册/范围目录；“catalog[].volume”和“questions[].volume”必须使用对应教材册的“label”。
4. “questionTypes”是题型目录；题目的“type”必须使用其中的“id”。目前自定义题型会按普通四选一题显示。
5. “id”是题目的稳定本机编号；使用“新增导入题库（合并）”时，系统按文章、考点词、原句、考点位置和题目内容判断重复；新增题会自动使用新的本机编号，不依赖导入文件中的临时 ID。题号 number 也会保持稳定，删除题目后允许出现空号。
6. 每道题必须有四个选项，选项键必须恰好是 A、B、C、D，“answer”必须是其中一个键。

## 顶层字段

- “schemaVersion”：格式版本，建议填写“3.0”。
- “title”、“description”：题库名称和说明。
- “questionTypes”：题型数组，每项需要“id”、“label”，可选“description”。
- “books”：教材册数组，每项需要唯一“id”、“label”，可选“order”。
- “catalog”：文章数组，每项需要唯一“id”、“title”、“volume”，可选“unit”、“author”。
- “quizDefaults”：可选的训练设置。“durationSeconds” 是每局答题时长，范围为 10-3600 秒；旧题库可以继续使用“correctScore”和“wrongScore”；新格式建议使用“scoring”配置计分机制。
- “quizDefaults.scoring.mode”：填写“fixed”或“streak”。“fixed”是固定计分；“streak”是连续表现计分。
- “quizDefaults.scoring”：可配置“baseCorrect”、“baseWrongPenalty”、“correctStreakAfter”、“correctStreakScore”、“wrongStreakAfter”和“wrongStreakPenalty”。连续次数超过对应 After 值后，当前题开始使用对应的连击分或连续错误扣分；“correctStreakAfter”和“wrongStreakAfter”均为 1-5 题。
- “questions”：题目数组。
- “questions[].reviewStatus”：可选的发布状态；新导入题建议填写 “candidate”，候选题在教师快速审查通过前不会进入学生答题，通过后由后台改为 “verified”。划线异常题使用 “abnormal”，也不会进入答题。
- “questions[].duplicateReview”：可选的重复候选审查信息，格式为 { "status": "pending", "groupId": "duplicate-...", "relatedQuestionIds": ["本组题目 ID"] }；pending 和 skipped 题目不会进入学生答题，管理员在“快速审查”中处理后才会恢复。

## 题目字段

每道题至少填写：“id”、“type”、“articleId”、“article”、“volume”、“word”、“sentence”、“options”、“answer”、“explanation”。

- “word”可以是单字，也可以是一个不可再拆分的文言词语。
- “sentence”是包含考点词的原文句子。
- 每道题只设置一个考点词。若同一句中要考两个不同的词，请建立两道题，保留相同的 sentence，但分别填写各自的 word、targetStart、targetOccurrence、options、answer 和 explanation；不要把两个词拼成一个 word。
- “targetOccurrence”从 1 开始，表示 word 在 sentence 中第几次出现；如果原句中出现两次或更多次，必须填写正确的次数。只有一次时填写 1。
- “targetStart”是可选的、从 0 开始的字符位置，表示本题实际考查词语在 sentence 中的起点；如果填写，必须和 targetOccurrence 对应。通过管理后台“从原句中选择考查实词”保存的题目会自动写入它。
- “stem”可选；普通单选题可用它作为额外题干。
- “options”必须是四个对象，格式为 { "key": "A", "text": "释义" }。
- “explanation”请写出正确释义，便于答题后核对和后台审查。
- “source”、“context”、“supportingItems”等扩展字段可以保留，程序会原样保存。

## ID 和名称的区别

- ID 是程序内部使用的稳定本机编号，必须唯一；现有题目会保持原编号，合并导入的新题会由本机自动分配新的 ID。不要依赖导入文件中的临时 ID 判断题目是否重复。
- label 或 title 是页面显示名称，例如教材册 ID “xxbs”对应名称“选择性必修上册”，篇目 ID “xx2_suwu”对应名称“苏武传”。
- 题目的 type 必须填写题型 ID，不是题型名称；题目的 articleId 必须填写篇目 ID，不是文章名称；题目的 volume 填教材册名称，也就是 books[].label。
- 如果只是给一篇已经存在的文章增加新题，请从 catalog 中复制该文章的 article ID，所有新增题目使用新的 question ID，不要复制旧题目的 question ID。

## 教师按课导入的推荐流程

1. 在模板的 questionTypes、books、catalog 中找到要使用的题型 ID、教材册 ID/名称和文章 ID/名称。
2. 如果文章已经存在，只复用原有 catalog[].id；如果文章不存在，先新增教材册，再新增文章并给它一个新的稳定 ID。
3. 每道新增题可以填写任意临时 question ID；合并导入时，程序会统一改成本机自动分配的题目编号。
4. 写入 word、sentence 和 targetOccurrence；先从左到右数 word 在 sentence 中的出现次数。不要因为原句里有相同字词，就默认第一处一定是考点。若填写 targetStart，请按从 0 开始的字符位置填写；新导入题即使填写 verified，合并导入也会按 candidate 进入待复核状态。
5. 如果同一句还要考另一个不同的词，复制 sentence 新建另一道题，并为新题单独计算 targetStart 和 targetOccurrence。
6. 写入四个选项、正确答案和解析。三个干扰项应是同一个词的其他常见义项或相邻义项，不能只是随意拼凑。
7. 导入前先检查 articleId、type、volume 的对应关系；再使用“新增导入题库（合并）”。
8. 导入后在后台“快速审查”逐题核对原句、考点位置、答案和干扰项。

## 程序会拒绝的常见错误

- articleId 不在 catalog 中，或 type 不在 questionTypes 中。
- volume 不在 books 的 label 中，或文章的 volume 与题目的 volume 不一致。
- catalog 为空、篇目 title 与题目的 article 不一致，或题号 number 重复。
- word 在 sentence 中找不到，targetOccurrence 小于 1，或 targetOccurrence 大于实际出现次数。此类划线定位问题不会删除题目，服务端会将题目写成 reviewStatus: "abnormal" 并在答题时自动跳过。
- targetStart 不是从 0 开始的实际字符位置，或 targetStart 与 targetOccurrence 指向的出现位置不一致；请在后台题库管理中重新选择考点并保存。
- 同一句的不同考点没有拆成不同题目，或一题的 word 同时包含多个互不相连的词。
- question.id、catalog.id、books.id、questionTypes.id 在各自目录中重复。
- options 不是恰好四项，选项键不是 A、B、C、D，或选项文字重复。

## 最小示例

请直接参考本文件末尾的“可直接复制的 JSON 模版”。模版会列出当前题库的全部题型、教材册和篇目目录；生成多道题时，只需继续向“questions”数组添加题目，并保证篇目 ID、题型 ID 和题目字段对应正确。合并导入时题目 ID 只是临时编号，系统会自动分配本机编号。

## 合并导入说明

“新增导入题库（合并）”会把新篇目、新教材册、新题型和新题目加入当前题库。系统先按文章、考点词、原句和考点出现位置判断核心题目，再比较题型、题干、选项文字、正确答案对应文字和解析：完全一致的题目会跳过；细节不同的题目会保留并标记为“重复候选”，在管理员处理前不会进入答题。导入题统一使用本机自动分配的编号并标记为 candidate，快速审查点击“确认正确”后才发布为 verified；原题不会因为编号冲突被覆盖。导入前会自动备份当前题库。
`;

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
let questionBankEtag = null;
let updateStatus = { phase: "idle", available: false, currentVersion: "1.4.4", latestVersion: null, progress: 0 };
let updateModalOpen = false;
let updatePromptDismissed = false;
let updatePollTimer = null;
let updateRestarting = false;
let manualUpdateCheck = false;
let showArchivedRecords = false;
let showQuestionBankHistory = false;
const selectedAnswerRecordIds = new Set();

const REVIEW_STATUS_META = {
  pending: { label: "待审", className: "pending" },
  passed: { label: "已确认", className: "passed" },
  needs_revision: { label: "待修改", className: "needs-revision" },
  skipped: { label: "已跳过", className: "skipped" },
};

const DUPLICATE_REVIEW_STATUS_META = {
  pending: { label: "重复候选", className: "duplicate-pending" },
  kept: { label: "重复题已保留", className: "duplicate-kept" },
  skipped: { label: "重复题已跳过", className: "duplicate-skipped" },
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

const normalizeAnswerRecords = (entries) => (Array.isArray(entries) ? entries : [])
  .filter((record) => record && typeof record.id === "string" && Array.isArray(record.questions))
  .map((record) => ({
    id: record.id,
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
    questions: record.questions,
  }))
  .sort((left, right) => right.finishedAt - left.finishedAt);

const normalizeQuestionBankHistory = (payload) => {
  const events = Array.isArray(payload?.events) ? payload.events : [];
  return events
    .filter((event) => event && typeof event.id === "string" && ["import", "export", "revoke"].includes(event.kind))
    .map((event) => ({
      id: event.id,
      kind: event.kind,
      mode: event.mode === "replace" ? "replace" : "merge",
      sourceName: String(event.sourceName || "题库 JSON").trim() || "题库 JSON",
      targetEventId: String(event.targetEventId || "").trim(),
      targetSourceName: String(event.targetSourceName || "").trim(),
      createdAt: String(event.createdAt || "").trim(),
      questionCount: Math.max(0, Number(event.questionCount) || 0),
      questionCountBefore: Math.max(0, Number(event.questionCountBefore) || 0),
      questionCountAfter: Math.max(0, Number(event.questionCountAfter) || 0),
      addedQuestionIds: Array.isArray(event.addedQuestionIds) ? event.addedQuestionIds : [],
      addedArticleIds: Array.isArray(event.addedArticleIds) ? event.addedArticleIds : [],
      addedBookIds: Array.isArray(event.addedBookIds) ? event.addedBookIds : [],
      addedTypeIds: Array.isArray(event.addedTypeIds) ? event.addedTypeIds : [],
      revoked: Boolean(event.revoked),
      canRevoke: Boolean(event.canRevoke),
      revokeReason: String(event.revokeReason || "").trim(),
    }))
    .sort((left, right) => right.createdAt.localeCompare(left.createdAt));
};

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

const formatSeconds = (seconds) => {
  const safeSeconds = Math.max(0, Number(seconds) || 0);
  const minutes = Math.floor(safeSeconds / 60);
  const remainder = safeSeconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
};

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

const isQuestionAbnormal = (question) => question?.reviewStatus === "abnormal";
const getDuplicateReview = (question) => {
  const duplicateReview = question?.duplicateReview;
  const status = Object.hasOwn(DUPLICATE_REVIEW_STATUS_META, duplicateReview?.status)
    ? duplicateReview.status
    : null;
  if (!status) return null;
  return {
    status,
    groupId: String(duplicateReview.groupId || "").trim(),
    relatedQuestionIds: Array.isArray(duplicateReview.relatedQuestionIds)
      ? [...new Set(duplicateReview.relatedQuestionIds.filter((id) => typeof id === "string" && id.trim()))]
      : [],
  };
};
const isDuplicateReviewPending = (question) => getDuplicateReview(question)?.status === "pending";
const getDuplicateReviewCount = (questions = bank?.questions) => (Array.isArray(questions) ? questions : [])
  .filter(isDuplicateReviewPending).length;

const getQuestionAvailability = (question) => {
  if (isQuestionAbnormal(question)) return { label: "学生端跳过：划线异常", className: "blocked" };
  if (getQuestionReview(question?.id).status === "needs_revision") {
    return { label: "学生端跳过：待修改", className: "blocked" };
  }
  if (question?.reviewStatus === "candidate") return { label: "学生端跳过：候选题待复核", className: "blocked" };
  const duplicateReview = getDuplicateReview(question);
  if (duplicateReview?.status === "pending") return { label: "学生端跳过：重复候选待审", className: "blocked" };
  if (duplicateReview?.status === "skipped") return { label: "学生端跳过：重复题已标记跳过", className: "blocked" };
  return { label: "学生端可抽取（需在所选范围内）", className: "available" };
};

const getAbnormalQuestionCount = (questions = bank?.questions) => (Array.isArray(questions) ? questions : [])
  .filter(isQuestionAbnormal).length;

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
  adminApp.querySelector("#admin-login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const password = new FormData(event.currentTarget).get("password").toString();
    const submitButton = event.currentTarget.querySelector('button[type="submit"]');
    if (submitButton) {
      submitButton.disabled = true;
      submitButton.textContent = "正在验证…";
    }
    try {
      adminToken = await authenticateAdmin(password);
    } catch (error) {
      adminToken = null;
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

const getQuestionTypes = () => {
  const configured = Array.isArray(bank?.questionTypes) ? bank.questionTypes : [];
  const byId = new Map(DEFAULT_QUESTION_TYPES.map((type) => [type.id, { ...type }]));
  configured.forEach((type) => {
    if (!type || typeof type.id !== "string" || !type.id.trim() || typeof type.label !== "string" || !type.label.trim()) return;
    byId.set(type.id.trim(), {
      id: type.id.trim(),
      label: type.label.trim(),
      description: String(type.description || "").trim(),
    });
  });
  return [...byId.values()];
};

const getBooks = () => {
  const configured = Array.isArray(bank?.books) ? bank.books : [];
  const books = [];
  const ids = new Set();
  const labels = new Set();
  configured.forEach((book, index) => {
    if (!book || typeof book.id !== "string" || !book.id.trim() || typeof book.label !== "string" || !book.label.trim()) return;
    const id = book.id.trim();
    const label = book.label.trim();
    if (ids.has(id) || labels.has(label)) return;
    ids.add(id);
    labels.add(label);
    books.push({ id, label, order: Number(book.order) || index + 1 });
  });
  getCatalog().forEach((article, index) => {
    const label = String(article.volume || "").trim();
    if (!label || labels.has(label)) return;
    labels.add(label);
    books.push({ id: label, label, order: books.length + index + 1 });
  });
  return books.sort((left, right) => left.order - right.order || left.label.localeCompare(right.label, "zh-CN"));
};

const isBuiltInQuestionType = (id) => DEFAULT_QUESTION_TYPES.some((type) => type.id === id);

const createQuestionBankTemplate = () => {
  const article = getCatalog()[0] || {
    id: "bx1_article_001",
    volume: "必修上册",
    unit: "请填写单元",
    title: "请填写文章名称",
    author: "",
  };
  const exampleQuestion = {
    ...QUESTION_BANK_TEMPLATE_EXAMPLE.questions[0],
    articleId: article.id,
    article: article.title,
    volume: article.volume,
    unit: article.unit || "",
  };
  return {
    _templateInstructions: {
      purpose: "本文件用于制作可导入的文言实词四选一题库；下方目录中的 id 是程序识别用的稳定标识，label、title 是给人看的名称。",
      idVsName: "生成题目时，question.type 使用 questionTypes[].id，question.articleId 使用 catalog[].id；不要把中文名称直接填入这两个字段。question.volume 使用 books[].label。",
      mergeRule: "题目 ID 只是本机编号。新增导入题库（合并）按文章、考点、原句、出现位置和题目内容去重；完全重复会跳过，细节不同的同核心题会保留并标记为重复候选，导入题自动分配本机编号。",
      occurrenceRule: "targetOccurrence 从 1 开始，表示 word 在 sentence 中第几次出现；同一个词出现多次时必须明确填写。targetStart 可填写 word 在 sentence 中从 0 开始的字符起点，后台会校验两者一致；无法定位时题目会标记为 abnormal 并跳过答题，等待人工复核。",
    },
    schemaVersion: "3.0",
    title: "请填写题库名称",
    description: "请说明适用年级、教材范围、教学进度和题目来源。",
    questionTypes: getQuestionTypes(),
    books: getBooks(),
    catalog: getCatalog().map((item) => ({ ...item })),
    quizDefaults: {
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
    },
    questions: [exampleQuestion],
  };
};

const createQuestionBankImportGuide = () => `${QUESTION_BANK_FORMAT_GUIDE}

## 当前题型目录（请在题目中使用左侧 ID）

| 题型 ID | 页面名称 | 当前说明 |
| --- | --- | --- |
${getQuestionTypes().map((type) => `| ${type.id} | ${type.label} | ${type.description || "暂无说明"} |`).join("\n")}

## 当前教材册目录（请用教材册 ID 管理，题目的 volume 使用名称）

| 教材册 ID | 教材册名称 | 排序 |
| --- | --- | ---: |
${getBooks().map((book) => `| ${book.id} | ${book.label} | ${book.order} |`).join("\n")}

## 当前篇目目录（题目的 articleId 使用左侧 ID）

| 篇目 ID | 篇目名称 | 所属教材册名称 | 单元 | 作者 |
| --- | --- | --- | --- | --- |
${getCatalog().map((article) => `| ${article.id} | ${article.title} | ${article.volume} | ${article.unit || ""} | ${article.author || ""} |`).join("\n")}

## 可直接复制的 JSON 模版

下面的代码块是一个可导入的最小模版。请保留题型、教材册和篇目目录中的稳定 ID；合并导入时题目的 \`id\` 只是临时本机编号，系统会按内容判断重复并自动为新增题目分配本机编号。

\`\`\`json
${JSON.stringify(createQuestionBankTemplate(), null, 2)}
\`\`\`
`;

const saveBank = async (nextBank, message) => {
  bank = await putJson(API.questions, nextBank);
  reviews = normalizeReviews(await fetchJson(API.questionReviews));
  statusMessage = message;
  render();
};

const formatArticleLabel = (title) => {
  const value = String(title || "课内文章");
  return value.startsWith("《") ? value : `《${value}》`;
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

const renderTargetSentence = (sentence, word, targetOccurrence = 1, markClass = "target-word") => {
  const source = String(sentence || "");
  const target = String(word || "");
  const occurrences = getWordOccurrences(source, target);
  if (!occurrences.length) return escapeHtml(source);
  const selected = Math.min(Math.max(Number(targetOccurrence) || 1, 1), occurrences.length);
  let cursor = 0;
  let result = "";
  occurrences.forEach((occurrence, index) => {
    result += escapeHtml(source.slice(cursor, occurrence.start));
    const isSelected = index + 1 === selected;
    result += `<mark class="${markClass} ${isSelected ? "target-word-selected" : "target-word-other"}" data-occurrence="${index + 1}">${escapeHtml(target)}</mark>`;
    cursor = occurrence.end;
  });
  return result + escapeHtml(source.slice(cursor));
};

const getSelectionOffsets = (root) => {
  if (!root || typeof window === "undefined" || typeof window.getSelection !== "function") return null;
  const selection = window.getSelection();
  if (!selection || selection.rangeCount === 0 || selection.isCollapsed) return null;
  const range = selection.getRangeAt(0);
  if (!root.contains(range.commonAncestorContainer)) return null;
  const beforeStart = document.createRange();
  beforeStart.selectNodeContents(root);
  beforeStart.setEnd(range.startContainer, range.startOffset);
  const selected = range.toString();
  const trimmed = selected.trim();
  if (!trimmed) return null;
  const leadingWhitespace = selected.search(/\S/);
  return {
    start: beforeStart.toString().length + Math.max(leadingWhitespace, 0),
    text: trimmed,
  };
};

const getQuestionTargetStart = (question) => {
  const sentence = String(question?.sentence || "");
  const word = String(question?.word || "");
  const occurrences = getWordOccurrences(sentence, word);
  const explicitStart = Number(question?.targetStart);
  if (Number.isInteger(explicitStart) && explicitStart >= 0 && sentence.slice(explicitStart, explicitStart + word.length) === word) {
    return explicitStart;
  }
  return occurrences[(Number(question?.targetOccurrence) || 1) - 1]?.start ?? "";
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
    targetOccurrence: 1,
    stem: "",
    explanation: "",
    answer: "A",
    number: getNextQuestionNumber(),
    options: ["A", "B", "C", "D"].map((key) => ({ key, text: "" })),
  };
};

const fetchJson = async (url) => {
  const headers = adminToken ? { "X-Wenyan-Admin-Token": adminToken } : {};
  const response = await fetch(url, { cache: "no-store", headers });
  const payload = await response.json().catch(() => null);
  if (response.status === 401 && adminAuthorized) {
    adminAuthorized = false;
    adminToken = null;
    loginError = "管理员授权已失效，请重新登录。";
    renderLogin();
  }
  if (!response.ok) throw new Error(payload?.error || `读取失败（${response.status}）。`);
  if (url === API.questions) questionBankEtag = response.headers.get("ETag") || null;
  return payload;
};

const postJson = async (url, value) => {
  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(adminToken ? { "X-Wenyan-Admin-Token": adminToken } : {}),
    },
    body: JSON.stringify(value),
  });
  const payload = await response.json().catch(() => null);
  if (response.status === 401 && adminAuthorized) {
    adminAuthorized = false;
    adminToken = null;
    loginError = "管理员授权已失效，请重新登录。";
    renderLogin();
  }
  if (!response.ok || !payload?.ok) throw new Error(payload?.error || "请求失败。");
  if ([API.questionBankImport, API.questionBankRevoke].includes(url)) {
    questionBankEtag = response.headers.get("ETag") || questionBankEtag;
  }
  return payload.data;
};

const putJson = async (url, value) => {
  const headers = {
    "Content-Type": "application/json",
    ...(adminToken ? { "X-Wenyan-Admin-Token": adminToken } : {}),
  };
  if (url === API.questions && questionBankEtag) headers["If-Match"] = questionBankEtag;
  const response = await fetch(url, {
    method: "PUT",
    headers,
    body: JSON.stringify(value),
  });
  const payload = await response.json().catch(() => null);
  if (response.status === 401 && adminAuthorized) {
    adminAuthorized = false;
    adminToken = null;
    loginError = "管理员授权已失效，请重新登录。";
    renderLogin();
  }
  if (!response.ok || !payload?.ok) throw new Error(payload?.error || "保存失败。");
  if (url === API.questions) questionBankEtag = response.headers.get("ETag") || questionBankEtag;
  return payload.data;
};

const authenticateAdmin = async (password) => {
  const data = await postJson(API.adminAuth, { password });
  if (!data?.token) throw new Error("服务器没有返回有效的管理员会话。" );
  return data.token;
};

const patchJson = async (url, value) => {
  const response = await fetch(url, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      ...(adminToken ? { "X-Wenyan-Admin-Token": adminToken } : {}),
    },
    body: JSON.stringify(value),
  });
  const payload = await response.json().catch(() => null);
  if (response.status === 401 && adminAuthorized) {
    adminAuthorized = false;
    adminToken = null;
    loginError = "管理员授权已失效，请重新登录。";
    renderLogin();
  }
  if (!response.ok || !payload?.ok) throw new Error(payload?.error || "保存失败。");
  return payload.data;
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

const getVolumes = () => getBooks().map((book) => book.label);
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
             <span class="question-list-topline"><span>#${question.number}</span><span>${escapeHtml(question.word)}${getWordOccurrences(question.sentence, question.word).length > 1 ? ` · 第 ${Number(question.targetOccurrence) || 1} 处` : ""}${isQuestionAbnormal(question) ? `<em class="question-abnormal-badge">异常待复核</em>` : ""}${question.reviewStatus === "candidate" ? `<em class="question-candidate-badge">候选待复核</em>` : ""}${isDuplicateReviewPending(question) ? `<em class="duplicate-review-badge duplicate-review-pending">重复候选</em>` : ""}</span></span>
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
  const questionType = question.type || "context_meaning";
  return `
    <section class="admin-card editor-card" aria-label="题目编辑器">
      <div class="editor-title-row">
        <h2>${creatingQuestion ? "新增题目" : `编辑第 ${question.number} 题`}</h2>
        ${statusMessage ? `<span class="editor-status">${escapeHtml(statusMessage)}</span>` : ""}
      </div>
      ${isQuestionAbnormal(question) ? `<p class="editor-abnormal-warning" role="alert"><strong>这道题已标记为异常，答题时会自动跳过。</strong>${question.reviewNote ? ` ${escapeHtml(question.reviewNote)}` : " 请重新选择并保存原句中的考点，修复后即可恢复答题。"}</p>` : ""}
      <form id="question-editor">
        <div class="editor-grid">
          <label class="editor-field">题型
            <select class="admin-select" name="type">
              ${getQuestionTypes().map((type) => `<option value="${escapeHtml(type.id)}" ${questionType === type.id ? "selected" : ""}>${escapeHtml(type.label)}</option>`).join("")}
            </select>
          </label>
          <label class="editor-field full">所属文章
            <select class="admin-select" name="articleId">
              ${getCatalog().map((article) => `<option value="${escapeHtml(article.id)}" ${article.id === question.articleId ? "selected" : ""}>${escapeHtml(article.volume)} · ${escapeHtml(formatArticleLabel(article.title))}</option>`).join("")}
            </select>
          </label>
          <label class="editor-field full">原句
            <textarea class="admin-textarea" name="sentence" required>${escapeHtml(question.sentence)}</textarea>
          </label>
          <div class="sentence-target-picker full">
            <div class="sentence-target-heading">
              <div><strong>从原句中选择考查实词</strong><span>不需要再单独输入考查实词；请在下方原句中拖选词语，再点击“设为考点”。</span></div>
              <span class="selected-target-label" id="selected-target-label">${question.word ? `当前考点：${escapeHtml(question.word)}` : "当前考点：未选择"}</span>
            </div>
            <div class="sentence-target-preview" id="sentence-target-picker" tabindex="0" aria-label="原句考点选择区">${renderTargetSentence(question.sentence, question.word, question.targetOccurrence, "editor-target-word")}</div>
            <div class="sentence-target-actions">
              <button class="admin-secondary admin-compact-button" type="button" data-action="apply-selected-target">将选中文字设为考点</button>
              <button class="admin-secondary admin-compact-button" type="button" data-action="clear-selected-target">清除考点并重新选择</button>
            </div>
            <input type="hidden" name="word" value="${escapeHtml(question.word || "")}" />
            <input type="hidden" name="targetStart" value="${escapeHtml(getQuestionTargetStart(question))}" />
            <input type="hidden" name="targetOccurrence" value="${escapeHtml(question.targetOccurrence || "")}" />
            <p class="editor-help">原句中同一个字词出现多次时，选择区会把已选考点高亮，其他同词位置保留为淡色提示。修改原句后需要重新选择考点。</p>
          </div>
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

const formatQuestionBankHistoryDate = (value) => {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "时间未知" : new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
};

const renderQuestionBankHistory = () => `
  <section class="admin-card question-history-card" aria-label="题库导入导出历史">
    <div class="admin-card-header question-history-header">
      <div>
        <h2 class="admin-card-title">导入 / 导出历史</h2>
        <p class="admin-subtitle">历史记录只读，不能删除或修改。撤销导入会追加一条不可逆的撤销记录；功能上线前的导入没有可追溯批次，不能安全撤销。</p>
      </div>
      <span class="admin-count">${questionBankHistory.length} 条记录</span>
    </div>
    ${questionBankHistory.length ? `
      <div class="question-history-list">
        ${questionBankHistory.map((event) => {
          if (event.kind === "export") {
            return `
              <article class="question-history-row">
                <div class="question-history-kind export">导出题库</div>
                <div class="question-history-main">
                  <strong>${escapeHtml(event.sourceName)}</strong>
                  <span>${escapeHtml(formatQuestionBankHistoryDate(event.createdAt))} · JSON · ${event.questionCount} 道题</span>
                </div>
                <span class="question-history-status readonly">只读记录</span>
              </article>
            `;
          }
          if (event.kind === "revoke") {
            return `
              <article class="question-history-row">
                <div class="question-history-kind revoke">撤销导入</div>
                <div class="question-history-main">
                  <strong>${escapeHtml(event.targetSourceName || event.targetEventId)}</strong>
                  <span>${escapeHtml(formatQuestionBankHistoryDate(event.createdAt))} · 对应导入记录已撤销</span>
                </div>
                <span class="question-history-status readonly">不可逆操作</span>
              </article>
            `;
          }
          const detail = `${event.mode === "replace" ? "替换导入" : "合并导入"} · 题目 ${event.questionCountBefore} → ${event.questionCountAfter} · 新增 ${event.addedQuestionIds.length} 道题`;
          return `
            <article class="question-history-row ${event.revoked ? "is-revoked" : ""}">
              <div class="question-history-kind import">导入题库</div>
              <div class="question-history-main">
                <strong>${escapeHtml(event.sourceName)}</strong>
                <span>${escapeHtml(formatQuestionBankHistoryDate(event.createdAt))} · ${escapeHtml(detail)}</span>
              </div>
              <div class="question-history-action">
                ${event.revoked
                  ? `<span class="question-history-status revoked">已撤销</span>`
                  : event.canRevoke
                    ? `<button class="admin-danger admin-small-button" type="button" data-action="revoke-question-import" data-history-event-id="${escapeHtml(event.id)}">撤销导入</button>`
                    : `<span class="question-history-status" title="${escapeHtml(event.revokeReason)}">暂不可撤销</span>`}
              </div>
            </article>
          `;
        }).join("")}
      </div>
    ` : `<div class="editor-empty">暂无导入或导出历史。</div>`}
  </section>
`;

const renderQuestionTools = () => `
  <section class="admin-card question-tools" aria-label="题库文件工具">
    <div class="question-tools-copy">
      <h2 class="admin-card-title">题库文件</h2>
      <p>可以导出当前完整题库，也可以把另一份题库合并进来。所有写入都会先自动备份原文件，并记录导入批次。</p>
    </div>
    <div class="question-tools-actions">
      <button class="admin-secondary" type="button" data-action="export-bank">导出当前题库 JSON</button>
      <button class="admin-secondary" type="button" data-action="download-import-guide">下载 JSON模版导入说明</button>
      <button class="admin-secondary" type="button" data-action="toggle-question-history">${showQuestionBankHistory ? "收起导入/导出历史" : `查看导入/导出历史（${questionBankHistory.length}）`}</button>
      <button class="admin-primary" type="button" data-action="merge-bank">新增导入题库（合并）</button>
      <button class="admin-danger" type="button" data-action="replace-bank">导入并替换（谨慎）</button>
      <input id="question-bank-file" type="file" accept="application/json,.json" hidden />
    </div>
  </section>
`;

const renderQuestionTab = () => `${renderQuestionTools()}${showQuestionBankHistory ? renderQuestionBankHistory() : ""}<div class="admin-grid">${renderQuestionList()}${renderQuestionEditor()}</div>`;

const renderQuestionTypeSettings = () => `
  <section class="admin-card settings-card">
    <div class="settings-card-heading">
      <div>
        <h2 class="admin-card-title">题型管理</h2>
        <p>新增题型后，可在题目编辑器中直接选择。自定义题型当前使用四选一答题界面。</p>
      </div>
      <span class="admin-count">${getQuestionTypes().length} 种</span>
    </div>
    <form id="question-type-form" class="settings-form">
      <label class="editor-field">题型 ID
        <input class="admin-input" name="id" placeholder="例如：context_compare" pattern="[A-Za-z][A-Za-z0-9_-]*" required />
      </label>
      <label class="editor-field">显示名称
        <input class="admin-input" name="label" placeholder="例如：语境辨析题" maxlength="30" required />
      </label>
      <label class="editor-field full">题型说明（可选）
        <input class="admin-input" name="description" maxlength="120" placeholder="给管理员看的简短说明" />
      </label>
      <button class="admin-primary" type="submit">新增题型</button>
    </form>
    <div class="settings-list">
      ${getQuestionTypes().map((type) => `
        <div class="settings-list-item">
          <div><strong>${escapeHtml(type.label)}</strong><code>${escapeHtml(type.id)}</code>${type.description ? `<p>${escapeHtml(type.description)}</p>` : ""}</div>
          ${isBuiltInQuestionType(type.id) ? `<span class="settings-badge">内置</span>` : `<button class="admin-danger admin-compact-button" type="button" data-action="delete-question-type" data-type-id="${escapeHtml(type.id)}">删除</button>`}
        </div>
      `).join("")}
    </div>
  </section>
`;

const renderBookSettings = () => `
  <section class="admin-card settings-card">
    <div class="settings-card-heading">
      <div>
        <h2 class="admin-card-title">教材册管理</h2>
        <p>这里的教材册会成为篇目和训练范围的可选项。</p>
      </div>
      <span class="admin-count">${getBooks().length} 册</span>
    </div>
    <form id="book-form" class="settings-form">
      <label class="editor-field">教材册 ID
        <input class="admin-input" name="id" placeholder="例如：xxbx1" pattern="[A-Za-z][A-Za-z0-9_-]*" required />
      </label>
      <label class="editor-field">教材册名称
        <input class="admin-input" name="label" placeholder="例如：选择性必修上册" maxlength="40" required />
      </label>
      <label class="editor-field">排序（可选）
        <input class="admin-input" name="order" type="number" min="1" step="1" placeholder="自动排序" />
      </label>
      <button class="admin-primary" type="submit">新增教材册</button>
    </form>
    <div class="settings-list">
      ${getBooks().map((book) => `
        <div class="settings-list-item">
          <div><strong>${escapeHtml(book.label)}</strong><code>${escapeHtml(book.id)}</code><p>当前关联 ${getCatalog().filter((article) => article.volume === book.label).length} 篇文章</p></div>
          ${getCatalog().some((article) => article.volume === book.label) ? `<span class="settings-badge">使用中</span>` : `<button class="admin-danger admin-compact-button" type="button" data-action="delete-book" data-book-id="${escapeHtml(book.id)}">删除</button>`}
        </div>
      `).join("")}
    </div>
  </section>
`;

const renderArticleSettings = () => `
  <section class="admin-card settings-card settings-card-wide">
    <div class="settings-card-heading">
      <div>
        <h2 class="admin-card-title">所属文章管理</h2>
        <p>先新增教材册，再在这里新增文章；题目编辑器会自动显示新文章。</p>
      </div>
      <span class="admin-count">${getCatalog().length} 篇</span>
    </div>
    <form id="article-form" class="settings-form settings-form-article">
      <label class="editor-field">文章 ID
        <input class="admin-input" name="id" placeholder="例如：xxbx1_article_001" pattern="[A-Za-z][A-Za-z0-9_-]*" required />
      </label>
      <label class="editor-field">文章名称
        <input class="admin-input" name="title" placeholder="例如：五代史伶官传序" maxlength="80" required />
      </label>
      <label class="editor-field">所属教材册
        <select class="admin-select" name="volume" required>
          <option value="">请选择教材册</option>
          ${getBooks().map((book) => `<option value="${escapeHtml(book.label)}">${escapeHtml(book.label)}</option>`).join("")}
        </select>
      </label>
      <label class="editor-field">单元（可选）
        <input class="admin-input" name="unit" maxlength="40" placeholder="例如：第一单元" />
      </label>
      <label class="editor-field">作者（可选）
        <input class="admin-input" name="author" maxlength="40" placeholder="例如：欧阳修" />
      </label>
      <button class="admin-primary" type="submit">新增文章</button>
    </form>
    <div class="settings-list article-settings-list">
      ${getCatalog().map((article) => `
        <div class="settings-list-item">
          <div><strong>${escapeHtml(formatArticleLabel(article.title))}</strong><code>${escapeHtml(article.id)}</code><p>${escapeHtml(article.volume)}${article.unit ? ` · ${escapeHtml(article.unit)}` : ""}${article.author ? ` · ${escapeHtml(article.author)}` : ""} · 已有 ${bank.questions.filter((question) => question.articleId === article.id).length} 题</p></div>
          ${bank.questions.some((question) => question.articleId === article.id) ? `<span class="settings-badge">使用中</span>` : `<button class="admin-danger admin-compact-button" type="button" data-action="delete-article" data-article-id="${escapeHtml(article.id)}">删除</button>`}
        </div>
      `).join("")}
    </div>
  </section>
`;

const renderSecurityTab = () => `
  <section class="settings-intro admin-card">
    <h2 class="admin-card-title">管理员密码</h2>
    <p>在这里修改进入管理后台的密码。保存成功后当前后台授权会立即失效，需要使用新密码重新登录。</p>
  </section>
  <section class="admin-card settings-security-card">
    <div class="settings-card-heading">
      <div>
        <h2 class="admin-card-title">修改登录密码</h2>
        <p>密码保存在本机应用数据目录，不依赖浏览器缓存；修改完成后会立即要求重新登录。</p>
      </div>
      <span class="settings-badge">本机配置</span>
    </div>
    <form id="admin-password-form" class="settings-form settings-security-form">
      <label class="editor-field">当前密码
        <input class="admin-input" name="currentPassword" type="password" autocomplete="current-password" minlength="6" maxlength="64" required />
      </label>
      <label class="editor-field">新密码
        <input class="admin-input" name="newPassword" type="password" autocomplete="new-password" minlength="6" maxlength="64" required />
      </label>
      <label class="editor-field">确认新密码
        <input class="admin-input" name="confirmPassword" type="password" autocomplete="new-password" minlength="6" maxlength="64" required />
      </label>
      <button class="admin-primary" type="submit">保存新密码</button>
    </form>
    <p class="editor-help">密码长度为 6-64 个字符。当前版本只是本机后台入口，不能替代真正的账户系统。</p>
  </section>
`;

const renderSettingsTab = () => `
  <section class="settings-intro admin-card">
    <h2 class="admin-card-title">题库结构设置</h2>
    <p>教材册、所属文章和题型会直接保存到当前题库 JSON。删除正在被题目使用的项目会被阻止，避免题目失去归属。</p>
  </section>
  <div class="settings-grid">${renderQuestionTypeSettings()}${renderBookSettings()}${renderArticleSettings()}</div>
`;

const renderScoringPreview = (config) => {
  const correctBase = calculateScoreEvent(config, true, { correctStreak: 0, wrongStreak: 0 });
  const correctSuper = calculateScoreEvent(config, true, { correctStreak: config.correctStreakAfter, wrongStreak: 0 });
  const wrongBase = calculateScoreEvent(config, false, { correctStreak: 0, wrongStreak: 0 });
  const wrongSuper = calculateScoreEvent(config, false, { correctStreak: 0, wrongStreak: config.wrongStreakAfter });
  if (config.mode === "fixed") {
    return `<div class="scoring-preview-line"><span class="scoring-preview-label">固定计分</span><strong>答对 ${formatScoreDelta(correctBase.scoreDelta)} · 答错 ${formatScoreDelta(wrongBase.scoreDelta)}</strong></div>`;
  }
  return `
    <div class="scoring-preview-line"><span class="scoring-preview-label">连续答对</span><strong>${formatScoreDelta(correctBase.scoreDelta)} → … → 第 ${config.correctStreakAfter + 1} 题 ${formatScoreDelta(correctSuper.scoreDelta)}</strong></div>
    <div class="scoring-preview-line"><span class="scoring-preview-label">连续答错</span><strong>${formatScoreDelta(wrongBase.scoreDelta)} → … → 第 ${config.wrongStreakAfter + 1} 题 ${formatScoreDelta(wrongSuper.scoreDelta)}</strong></div>
  `;
};

const renderScoringTab = () => {
  const config = normalizeScoringConfig(bank.quizDefaults);
  const rawDuration = Number(bank.quizDefaults?.durationSeconds);
  const durationSeconds = Number.isInteger(rawDuration) && rawDuration >= MIN_DURATION_SECONDS && rawDuration <= MAX_DURATION_SECONDS
    ? rawDuration
    : 120;
  return `
    <section class="settings-intro admin-card">
      <h2 class="admin-card-title">计分机制</h2>
      <p>这里设置全局当前规则。保存后，新开的训练局会读取新规则；已经开始的训练局不会被中途改写。</p>
    </section>
    <form id="scoring-form" class="scoring-settings-form">
      <section class="admin-card scoring-card">
        <div class="settings-card-heading">
          <div>
            <h2 class="admin-card-title">选择当前机制</h2>
            <p>学生端不会选择机制，老师在这里切换后统一生效。</p>
          </div>
          <span class="settings-badge">全局设置</span>
        </div>
        <div class="scoring-mode-options" role="radiogroup" aria-label="选择计分机制">
          <div class="scoring-mode-card ${config.mode === "fixed" ? "selected" : ""}">
            <label class="scoring-mode-select">
              <input type="radio" name="mode" value="fixed" ${config.mode === "fixed" ? "checked" : ""} />
              <span class="scoring-mode-card-body">
                <strong class="scoring-mode-title">固定计分</strong>
                <span>每道题使用同一套基础加分和扣分。</span>
              </span>
            </label>
            <div class="scoring-mode-config">
              <span class="scoring-rule-caption">基础分</span>
              <p class="scoring-rule-sentence scoring-base-sentence">
                <span class="scoring-rule-line">答对每题加 <input class="admin-input scoring-inline-input" name="baseCorrect" type="number" min="0" max="1000" step="1" value="${config.baseCorrect}" required aria-label="固定计分答对基础分" /> 分</span>
                <span class="scoring-rule-line">答错每题扣 <input class="admin-input scoring-inline-input" name="baseWrongPenalty" type="number" min="0" max="1000" step="1" value="${config.baseWrongPenalty}" required aria-label="固定计分答错基础扣分" /> 分。</span>
              </p>
              <small>当前规则：答对 ${formatScoreDelta(config.baseCorrect)}，答错 ${formatScoreDelta(-config.baseWrongPenalty)}</small>
            </div>
          </div>
          <div class="scoring-mode-card ${config.mode === "streak" ? "selected" : ""}">
            <label class="scoring-mode-select">
              <input type="radio" name="mode" value="streak" ${config.mode === "streak" ? "checked" : ""} />
              <span class="scoring-mode-card-body">
                <strong class="scoring-mode-title">连续表现</strong>
                <span>连续答对进入连击加分，连续答错进入连续错误扣分。</span>
              </span>
            </label>
            <div class="scoring-mode-config">
              <span class="scoring-rule-caption">基础分</span>
              <p class="scoring-rule-sentence scoring-base-sentence">
                <span class="scoring-rule-line">答对每题加 <input class="admin-input scoring-inline-input" name="baseCorrect" type="number" min="0" max="1000" step="1" value="${config.baseCorrect}" required aria-label="连续表现答对基础分" /> 分</span>
                <span class="scoring-rule-line">答错每题扣 <input class="admin-input scoring-inline-input" name="baseWrongPenalty" type="number" min="0" max="1000" step="1" value="${config.baseWrongPenalty}" required aria-label="连续表现答错基础扣分" /> 分。</span>
              </p>
              <div class="scoring-streak-rules">
                <div class="scoring-streak-rule">
                  <span class="scoring-rule-caption">连续答对 · 连击加分</span>
                  <p class="scoring-rule-sentence">
                    <span class="scoring-rule-line">连续答对达到 <input class="admin-input scoring-inline-input" name="correctStreakAfter" type="number" min="1" max="${MAX_STREAK_THRESHOLD}" step="1" value="${config.correctStreakAfter}" required aria-label="连续答对题数" /> 题后</span>
                    <span class="scoring-rule-line">从下一题起每题加 <input class="admin-input scoring-inline-input" name="correctStreakScore" type="number" min="0" max="1000" step="1" value="${config.correctStreakScore}" required aria-label="连续答对加分" /> 分。</span>
                  </p>
                </div>
                <div class="scoring-streak-rule">
                  <span class="scoring-rule-caption">连续答错 · 连续错误扣分</span>
                  <p class="scoring-rule-sentence">
                    <span class="scoring-rule-line">连续答错达到 <input class="admin-input scoring-inline-input" name="wrongStreakAfter" type="number" min="1" max="${MAX_STREAK_THRESHOLD}" step="1" value="${config.wrongStreakAfter}" required aria-label="连续答错题数" /> 题后</span>
                    <span class="scoring-rule-line">从下一题起每题扣 <input class="admin-input scoring-inline-input" name="wrongStreakPenalty" type="number" min="0" max="1000" step="1" value="${config.wrongStreakPenalty}" required aria-label="连续答错扣分" /> 分。</span>
                  </p>
                </div>
              </div>
              <small>当前规则：基础 ${formatScoreDelta(config.baseCorrect)} / ${formatScoreDelta(-config.baseWrongPenalty)} · 连击 ${formatScoreDelta(config.correctStreakScore)} / 连错 ${formatScoreDelta(-config.wrongStreakPenalty)}</small>
            </div>
          </div>
        </div>
        <div class="scoring-duration-setting">
          <label class="editor-field">每局答题时长
            <span class="admin-number-with-unit"><input class="admin-input scoring-duration-input" name="durationSeconds" type="number" min="${MIN_DURATION_SECONDS}" max="${MAX_DURATION_SECONDS}" step="1" value="${durationSeconds}" required /><span>秒</span></span>
          </label>
          <p>可设置 ${MIN_DURATION_SECONDS} 秒至 ${Math.floor(MAX_DURATION_SECONDS / 60)} 分钟；保存后只对新开的训练局生效。</p>
        </div>
        <p class="scoring-settings-note">“达到 N 题后”表示第 N+1 题开始使用连击分；答对和答错会互相重置连续次数。</p>
        <div class="scoring-preview" aria-live="polite">
          <div class="scoring-preview-heading"><strong>规则预览</strong><span>当前：${config.mode === "streak" ? "连续表现模式" : "固定计分模式"}</span></div>
          <div data-scoring-preview>${renderScoringPreview(config)}</div>
        </div>
        <div class="editor-actions">
          <button class="admin-primary" type="submit">保存并启用</button>
        </div>
      </section>
    </form>
  `;
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
          <p class="admin-subtitle">答题记录可手动折叠或恢复；已折叠、未折叠分别只保留最近 1 个月内的最新 100 条，超出各自范围的记录会清除并在清除前备份。导入导出均使用本机 JSON 文件。</p>
        </div>
        <span class="admin-count">未折叠 ${answerRecords.length - archivedCount} / 100 · 已折叠 ${archivedCount} / 100</span>
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
                <strong>${escapeHtml(record.name)}</strong>
                <span>${escapeHtml(formatRecordDate(record.finishedAt))} · 用时 ${formatSeconds(record.usedSeconds)} · ${record.completedAll ? "全部答完" : "提前结束"}</span>
              </div>
              <div class="answer-record-admin-stats">
                <strong>${record.score} 分</strong>
                <span>答对 ${record.correctCount} · 答错 ${record.wrongCount} · 已答 ${record.answeredCount} 题</span>
              </div>
            </article>
          `).join("")}
        </div>
      `}
    </section>
  `;
};

const UPDATE_BUSY_PHASES = new Set(["checking", "downloading", "applying"]);

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
  failed: "更新失败，已保留当前版本",
}[phase] || "更新状态未知");

const renderUpdateModal = () => {
  if (!updateModalOpen || !updateStatus?.available) return "";
  const busy = UPDATE_BUSY_PHASES.has(updateStatus.phase) || updateRestarting;
  const canApply = updateStatus.canApply !== false;
  const note = updateStatus.notes || "此次 Release 未提供详细更新说明。";
  const blockedMessage = updateStatus.sourceClean === false
    ? "检测到源码目录存在未提交修改或本地文件变更。为避免覆盖你的代码，本次不会自动替换源码。"
    : "";
  const applyLabel = updateStatus.phase === "downloading"
    ? `正在下载 ${Math.max(0, Number(updateStatus.progress) || 0)}%`
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
        <p class="update-modal-meta">当前版本 ${escapeHtml(updateStatus.currentVersion || "未知")} · ${escapeHtml(formatUpdateDate(updateStatus.publishedAt))}</p>
        <h3>${escapeHtml(updateStatus.title || "GitHub Release")}</h3>
        <pre class="update-notes">${escapeHtml(note)}</pre>
        <p class="update-preserve-note">更新会自动重启本地服务，不会删除题库、排行榜或答题记录；正在答题的页面可能需要刷新。</p>
        ${blockedMessage ? `<p class="update-blocked-note">${escapeHtml(blockedMessage)}</p>` : ""}
        ${updateStatus.phase === "failed" ? `<p class="update-blocked-note">更新包下载、校验或启动更新助手失败，当前版本未被替换。</p>` : ""}
        <div class="update-modal-actions">
          <button class="admin-secondary" type="button" data-action="dismiss-update" ${busy ? "disabled" : ""}>稍后</button>
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

const render = () => {
  const isReviewTab = activeTab === "review";
  const pageTitle = {
    review: "快速审查题目",
    questions: "管理题库与成绩",
    settings: "题库结构设置",
    scoring: "计分机制",
    security: "管理员密码",
    leaderboard: "排行榜管理",
    records: "答题记录",
  }[activeTab] || "管理后台";
  const pageSubtitle = isReviewTab
    ? "按顺序滚动查看全部题目，优先核对系统标出的正确答案。"
    : activeTab === "security"
      ? "修改管理员密码后，当前后台授权会立即失效，需要使用新密码重新登录。"
      : activeTab === "scoring"
        ? "选择当前全局计分机制；新开的训练局会使用保存后的规则。"
      : "题库修改会写入应用数据；排行榜写入电脑用户目录，浏览器关闭、刷新或更换浏览器后仍会保留。";
  adminApp.innerHTML = `
    <header class="admin-header">
      <div>
        <p class="eyebrow">文言实词 · 管理后台</p>
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
      <button class="admin-tab ${activeTab === "security" ? "active" : ""}" type="button" data-tab="security">管理员密码</button>
      <button class="admin-tab ${activeTab === "leaderboard" ? "active" : ""}" type="button" data-tab="leaderboard">排行榜管理</button>
      <button class="admin-tab ${activeTab === "records" ? "active" : ""}" type="button" data-tab="records">答题记录</button>
    </nav>
    ${activeTab === "review" ? renderReviewTab() : activeTab === "questions" ? renderQuestionTab() : activeTab === "settings" ? renderSettingsTab() : activeTab === "scoring" ? renderScoringTab() : activeTab === "security" ? renderSecurityTab() : activeTab === "records" ? renderAnswerRecordsTab() : renderLeaderboardTab()}
    ${renderUpdateModal()}
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

const downloadBank = async () => {
  const filename = "文言实词题库-当前完整题库.json";
  const blob = new Blob([JSON.stringify(bank, null, 2)], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
  try {
    questionBankHistory = normalizeQuestionBankHistory(await postJson(API.questionBankHistory, {
      kind: "export",
      format: "json",
      sourceName: filename,
      questionCount: bank.questions.length,
    }));
    statusMessage = "当前题库已导出，导出记录已保存。";
  } catch (error) {
    statusMessage = `题库文件已下载，但导出历史记录保存失败：${error instanceof Error ? error.message : "未知错误"}`;
  }
  render();
};

const downloadTextFile = (filename, content, type) => {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
};

const downloadQuestionBankImportGuide = () => downloadTextFile(
  "JSON模版导入说明.md",
  createQuestionBankImportGuide(),
  "text/markdown;charset=utf-8",
);

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

const validateImportedBankShape = (imported) => {
  if (!imported || typeof imported !== "object" || !Array.isArray(imported.questions)) {
    throw new Error("导入失败：文件必须是包含 questions 数组的题库 JSON。");
  }
  if (!Array.isArray(imported.catalog)) {
    throw new Error("导入失败：题库 JSON 缺少 catalog 教材目录。");
  }
  if (imported.questionTypes !== undefined && !Array.isArray(imported.questionTypes)) {
    throw new Error("导入失败：questionTypes 必须是数组。");
  }
  if (imported.books !== undefined && !Array.isArray(imported.books)) {
    throw new Error("导入失败：books 必须是数组。");
  }
};

const sameRecordFields = (left, right, fields) => fields.every((field) => (
  String(left?.[field] ?? "").trim() === String(right?.[field] ?? "").trim()
));

const mergeQuestionBank = (base, imported) => {
  const baseTypes = getQuestionTypes().map((type) => ({ ...type }));
  const typeById = new Map(baseTypes.map((type) => [type.id, type]));
  (imported.questionTypes || []).forEach((type, index) => {
    if (!type || typeof type.id !== "string" || !type.id.trim() || typeof type.label !== "string" || !type.label.trim()) {
      throw new Error(`合并失败：第 ${index + 1} 个题型缺少 id 或 label。`);
    }
    const clean = { id: type.id.trim(), label: type.label.trim(), description: String(type.description || "").trim() };
    const existing = typeById.get(clean.id);
    if (existing && !sameRecordFields(existing, clean, ["label", "description"])) {
      throw new Error(`合并失败：题型 ID“${clean.id}”已存在，但名称或说明不一致。`);
    }
    if (!existing) typeById.set(clean.id, clean);
  });

  const baseBooks = getBooks().map((book) => ({ ...book }));
  const bookById = new Map(baseBooks.map((book) => [book.id, book]));
  const bookLabels = new Set(baseBooks.map((book) => book.label));
  (imported.books || []).forEach((book, index) => {
    if (!book || typeof book.id !== "string" || !book.id.trim() || typeof book.label !== "string" || !book.label.trim()) {
      throw new Error(`合并失败：第 ${index + 1} 个教材册缺少 id 或 label。`);
    }
    const clean = { id: book.id.trim(), label: book.label.trim(), order: Number(book.order) || baseBooks.length + index + 1 };
    const existing = bookById.get(clean.id);
    if (existing && existing.label !== clean.label) {
      throw new Error(`合并失败：教材册 ID“${clean.id}”已存在，但名称不一致。`);
    }
    if (!existing && bookLabels.has(clean.label)) {
      throw new Error(`合并失败：教材册名称“${clean.label}”已存在，请复用原有教材册。`);
    }
    if (!existing) {
      bookById.set(clean.id, clean);
      bookLabels.add(clean.label);
    }
  });

  const catalog = getCatalog().map((article) => ({ ...article }));
  const articleById = new Map(catalog.map((article) => [article.id, article]));
  (imported.catalog || []).forEach((article, index) => {
    if (!article || typeof article.id !== "string" || !article.id.trim() || typeof article.title !== "string" || !article.title.trim() || typeof article.volume !== "string" || !article.volume.trim()) {
      throw new Error(`合并失败：第 ${index + 1} 个篇目缺少 id、title 或 volume。`);
    }
    const clean = {
      ...article,
      id: article.id.trim(),
      title: article.title.trim(),
      volume: article.volume.trim(),
      unit: String(article.unit || "").trim(),
      author: String(article.author || "").trim(),
    };
    const existing = articleById.get(clean.id);
    if (existing && !sameRecordFields(existing, clean, ["title", "volume", "unit", "author"])) {
      throw new Error(`合并失败：篇目 ID“${clean.id}”已存在，但篇目信息不一致。`);
    }
    if (!existing) {
      articleById.set(clean.id, clean);
      catalog.push(clean);
    }
    if (!bookLabels.has(clean.volume)) {
      const inferred = { id: clean.volume, label: clean.volume, order: bookById.size + 1 };
      bookById.set(inferred.id, inferred);
      bookLabels.add(inferred.label);
    }
  });

  const mergedTypes = [...typeById.values()];
  const allowedTypes = new Set(mergedTypes.map((type) => type.id));
  const importedQuestions = imported.questions.map((question, index) => {
    if (!question || typeof question !== "object" || Array.isArray(question)) {
      throw new Error(`合并失败：第 ${index + 1} 道题不是对象。`);
    }
    const normalized = question.type ? question : { ...question, type: "context_meaning" };
    if (!allowedTypes.has(normalized.type)) {
      throw new Error(`合并失败：导入题目“${normalized.id || index + 1}”使用了未定义的题型“${normalized.type}”。`);
    }
    return normalized;
  });
  const questionMerge = mergeQuestionsByContent(base.questions, importedQuestions);

  const merged = {
    ...base,
    schemaVersion: imported.schemaVersion || base.schemaVersion || "3.0",
    questionTypes: mergedTypes,
    books: [...bookById.values()],
    catalog,
    questions: questionMerge.questions,
  };
  return {
    bank: merged,
    addedQuestions: questionMerge.newQuestions.length,
    skippedQuestions: questionMerge.skippedQuestions,
    duplicateCandidateGroups: questionMerge.duplicateCandidateGroups,
    duplicateCandidateQuestions: questionMerge.duplicateCandidateQuestions,
    renumberedQuestions: questionMerge.renumberedQuestions,
    addedArticles: catalog.length - getCatalog().length,
    addedBooks: bookById.size - baseBooks.length,
    addedTypes: typeById.size - baseTypes.length,
  };
};

const importBankFromFile = async (file, mode = "merge") => {
  let imported;
  try {
    imported = JSON.parse(await file.text());
  } catch (error) {
    throw new Error(`JSON 文件无法读取：${error instanceof Error ? error.message : "格式错误"}`);
  }
  validateImportedBankShape(imported);
  if (mode === "replace") {
    if (!window.confirm(`确定导入“${file.name}”吗？当前题库将被替换，原题库会先自动备份。`)) return false;
    const result = await postJson(API.questionBankImport, {
      mode,
      sourceName: file.name,
      bank: imported,
    });
    bank = result.bank;
    questionBankHistory = normalizeQuestionBankHistory(result.history);
    const abnormalCount = getAbnormalQuestionCount(bank.questions);
    statusMessage = `已替换为 ${bank.questions.length} 道题${abnormalCount ? `；${abnormalCount} 道划线异常题已标记并跳过答题` : ""}`;
  } else {
    const merged = mergeQuestionBank(bank, imported);
    if (!window.confirm(`确定把“${file.name}”新增到当前题库吗？将新增 ${merged.addedQuestions} 道题、跳过 ${merged.skippedQuestions} 道完全重复题、标记 ${merged.duplicateCandidateQuestions} 道重复候选题，并新增 ${merged.addedArticles} 篇文章、${merged.addedBooks} 册教材和 ${merged.addedTypes} 种题型；新增题会自动使用本机编号。`)) return false;
    const result = await postJson(API.questionBankImport, {
      mode,
      sourceName: file.name,
      bank: merged.bank,
    });
    bank = result.bank;
    questionBankHistory = normalizeQuestionBankHistory(result.history);
    const importedAbnormalCount = merged.addedQuestions > 0
      ? bank.questions.slice(-merged.addedQuestions).filter(isQuestionAbnormal).length
      : 0;
    statusMessage = `合并完成：新增 ${merged.addedQuestions} 道题，跳过 ${merged.skippedQuestions} 道完全重复题，标记 ${merged.duplicateCandidateQuestions} 道重复候选题，自动编号 ${merged.renumberedQuestions} 道${importedAbnormalCount ? `；其中 ${importedAbnormalCount} 道划线异常题已标记并跳过答题` : ""}`;
  }
  selectedQuestionId = bank.questions[0]?.id || null;
  creatingQuestion = false;
  render();
  return true;
};

const revokeQuestionBankImport = async (eventId) => {
  const event = questionBankHistory.find((item) => item.id === eventId && item.kind === "import");
  if (!event || !event.canRevoke || event.revoked) return;
  const scope = event.mode === "replace"
    ? "恢复到这次替换导入前的完整题库"
    : `移除本次导入新增的 ${event.addedQuestionIds.length} 道题及不再使用的目录项`;
  if (!window.confirm(`确定撤销“${event.sourceName}”吗？\n\n${scope}。此操作不可逆，历史记录本身不会删除，只会追加一条撤销记录。`)) return;
  const button = adminApp.querySelector(`[data-action="revoke-question-import"][data-history-event-id="${CSS.escape(eventId)}"]`);
  if (button) {
    button.disabled = true;
    button.textContent = "正在撤销…";
  }
  try {
    const result = await postJson(API.questionBankRevoke, { eventId });
    bank = result.bank;
    questionBankHistory = normalizeQuestionBankHistory(result.history);
    selectedQuestionId = bank.questions[0]?.id || null;
    creatingQuestion = false;
    statusMessage = `已撤销导入“${event.sourceName}”；历史记录已保留。`;
    render();
  } catch (error) {
    if (button) {
      button.disabled = false;
      button.textContent = "撤销导入";
    }
    window.alert(error instanceof Error ? error.message : "撤销导入失败。");
  }
};

const saveQuestionType = async (form) => {
  const formData = new FormData(form);
  const id = formData.get("id").toString().trim();
  const label = formData.get("label").toString().trim();
  const description = formData.get("description").toString().trim();
  if (!/^[A-Za-z][A-Za-z0-9_-]*$/.test(id)) throw new Error("题型 ID 必须以英文字母开头，只能包含字母、数字、下划线或短横线。");
  if (getQuestionTypes().some((type) => type.id === id)) throw new Error("这个题型 ID 已经存在。");
  await saveBank({ ...bank, questionTypes: [...getQuestionTypes(), { id, label, description }] }, "新题型已保存。");
};

const deleteQuestionType = async (id) => {
  const type = getQuestionTypes().find((item) => item.id === id);
  if (!type) return;
  if (isBuiltInQuestionType(id)) throw new Error("内置题型不能删除。");
  if (bank.questions.some((question) => question.type === id)) throw new Error("这个题型正在被题目使用，不能删除；请先修改相关题目。");
  if (!window.confirm(`确定删除题型“${type.label}”吗？`)) return;
  await saveBank({ ...bank, questionTypes: getQuestionTypes().filter((item) => item.id !== id) }, "题型已删除。");
};

const saveBook = async (form) => {
  const formData = new FormData(form);
  const id = formData.get("id").toString().trim();
  const label = formData.get("label").toString().trim();
  const order = Number(formData.get("order")) || getBooks().length + 1;
  if (!/^[A-Za-z][A-Za-z0-9_-]*$/.test(id)) throw new Error("教材册 ID 必须以英文字母开头，只能包含字母、数字、下划线或短横线。");
  if (getBooks().some((book) => book.id === id || book.label === label)) throw new Error("这个教材册 ID 或名称已经存在。");
  await saveBank({ ...bank, books: [...getBooks(), { id, label, order }] }, "新教材册已保存。");
};

const deleteBook = async (id) => {
  const book = getBooks().find((item) => item.id === id);
  if (!book) return;
  if (getCatalog().some((article) => article.volume === book.label)) throw new Error("这个教材册还有所属文章，不能删除；请先处理这些文章。");
  if (!window.confirm(`确定删除教材册“${book.label}”吗？`)) return;
  await saveBank({ ...bank, books: getBooks().filter((item) => item.id !== id) }, "教材册已删除。");
};

const saveArticle = async (form) => {
  const formData = new FormData(form);
  const id = formData.get("id").toString().trim();
  const title = formData.get("title").toString().trim();
  const volume = formData.get("volume").toString().trim();
  const unit = formData.get("unit").toString().trim();
  const author = formData.get("author").toString().trim();
  if (!/^[A-Za-z][A-Za-z0-9_-]*$/.test(id)) throw new Error("文章 ID 必须以英文字母开头，只能包含字母、数字、下划线或短横线。");
  if (!getBooks().some((book) => book.label === volume)) throw new Error("请选择有效的所属教材册。");
  if (getCatalog().some((article) => article.id === id)) throw new Error("这个文章 ID 已经存在。");
  const article = { id, title, volume, unit, author };
  await saveBank({ ...bank, catalog: [...getCatalog(), article] }, "新文章已保存。");
};

const deleteArticle = async (id) => {
  const article = getArticle(id);
  if (!article) return;
  if (bank.questions.some((question) => question.articleId === id)) throw new Error("这篇文章还有题目，不能删除；请先删除或转移相关题目。");
  if (!window.confirm(`确定删除文章“${article.title}”吗？`)) return;
  await saveBank({ ...bank, catalog: getCatalog().filter((item) => item.id !== id) }, "文章已删除。");
};

const saveAdminPassword = async (form) => {
  const formData = new FormData(form);
  const currentPassword = formData.get("currentPassword").toString();
  const newPassword = formData.get("newPassword").toString();
  const confirmPassword = formData.get("confirmPassword").toString();
  if (newPassword !== confirmPassword) throw new Error("两次输入的新密码不一致。");
  await putJson(API.adminSettings, { currentPassword, newPassword });
  adminAuthorized = false;
  activeTab = "review";
  statusMessage = "";
  loginError = "管理员密码已修改，请使用新密码重新登录。";
  renderLogin();
};

const saveQuestion = async (form) => {
  const current = creatingQuestion ? null : getQuestion(selectedQuestionId);
  if (!creatingQuestion && !current) return;
  const formData = new FormData(form);
  const article = getArticle(formData.get("articleId"));
  const options = ["A", "B", "C", "D"].map((key) => ({ key, text: formData.get(`option-${key}`).toString().trim() }));
  const optionTexts = options.map((option) => option.text);
  const word = formData.get("word").toString().trim();
  const sentence = formData.get("sentence").toString().trim();
  const targetStart = Number(formData.get("targetStart"));
  let targetOccurrence = Number(formData.get("targetOccurrence"));
  const occurrences = getWordOccurrences(sentence, word);
  const occurrenceCount = occurrences.length;
  if (!article) throw new Error("请选择有效的所属文章。");
  if (!optionTexts.every(Boolean) || new Set(optionTexts).size !== 4) throw new Error("四个选项必须填写且不能重复。");
  if (!word || !sentence || occurrenceCount === 0) throw new Error("请先在下方原句中拖选考查实词，再点击“将选中文字设为考点”。");
  if (!Number.isInteger(targetStart) || targetStart < 0 || sentence.slice(targetStart, targetStart + word.length) !== word) {
    throw new Error("考查实词定位无效，请重新在下方原句中拖选并设为考点。");
  }
  const selectedIndex = occurrences.findIndex((occurrence) => occurrence.start === targetStart);
  if (selectedIndex < 0) throw new Error("考查实词定位无效，请重新在下方原句中拖选并设为考点。");
  targetOccurrence = selectedIndex + 1;

  const updated = {
    ...(current || {}),
    id: current?.id || createQuestionId(),
    number: current?.number || getNextQuestionNumber(),
    type: formData.get("type").toString(),
    word,
    articleId: article.id,
    article: article.title,
    volume: article.volume,
    unit: article.unit,
    sentence,
    targetStart,
    targetOccurrence,
    explanation: formData.get("explanation").toString().trim(),
    answer: formData.get("answer").toString(),
    options,
    reviewStatus: current?.reviewStatus === "candidate" ? "candidate" : current ? "admin_edited" : "admin_created",
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
  const duplicateMerge = rebuildDuplicateReviews(nextQuestions, {
    resetQuestionIds: [updated.id],
  });
  const nextBank = {
    ...bank,
    questions: duplicateMerge.questions,
  };
  bank = await putJson(API.questions, nextBank);
  reviews = normalizeReviews(await fetchJson(API.questionReviews));
  selectedQuestionId = updated.id;
  creatingQuestion = false;
  statusMessage = current ? "已保存到 questions.json" : "新题目已保存到 questions.json";
  render();
};

const readScoringForm = (form) => {
  const formData = new FormData(form);
  const mode = formData.get("mode").toString();
  if (!["fixed", "streak"].includes(mode)) throw new Error("请选择有效的计分机制。");
  const activeModeCard = [...form.querySelectorAll(".scoring-mode-card")].find((card) => card.querySelector('input[name="mode"]')?.value === mode);
  const readActiveNumber = (name, minimum, maximum = 1000) => {
    const input = activeModeCard?.querySelector(`[name="${name}"]`) || form.querySelector(`[name="${name}"]`);
    const value = Number(input?.value);
    if (!Number.isInteger(value) || value < minimum || value > maximum) {
      throw new Error(`${name} 必须是 ${minimum === 1 ? `1-${maximum}` : `0-${maximum}`} 的整数。`);
    }
    return value;
  };
  const readNumber = (name, minimum, maximum = 1000) => {
    const value = Number(formData.get(name));
    if (!Number.isInteger(value) || value < minimum || value > maximum) {
      throw new Error(`${name} 必须是 ${minimum === 1 ? `1-${maximum}` : `0-${maximum}`} 的整数。`);
    }
    return value;
  };
  const durationSeconds = readNumber("durationSeconds", MIN_DURATION_SECONDS, MAX_DURATION_SECONDS);
  return {
    durationSeconds,
    scoring: serializeScoringConfig({
      mode,
      baseCorrect: readActiveNumber("baseCorrect", 0),
      baseWrongPenalty: readActiveNumber("baseWrongPenalty", 0),
      correctStreakAfter: readActiveNumber("correctStreakAfter", 1, MAX_STREAK_THRESHOLD),
      correctStreakScore: readActiveNumber("correctStreakScore", 0),
      wrongStreakAfter: readActiveNumber("wrongStreakAfter", 1, MAX_STREAK_THRESHOLD),
      wrongStreakPenalty: readActiveNumber("wrongStreakPenalty", 0),
    }),
  };
};

const saveScoringSettings = async (form) => {
  const { durationSeconds, scoring } = readScoringForm(form);
  const quizDefaults = bank.quizDefaults && typeof bank.quizDefaults === "object" ? bank.quizDefaults : {};
  await saveBank({
    ...bank,
    quizDefaults: {
      ...quizDefaults,
      durationSeconds,
      correctScore: scoring.baseCorrect,
      wrongScore: -scoring.baseWrongPenalty,
      scoring,
    },
  }, "计分机制已保存并启用；新开的训练局将使用新规则。");
};

const deleteSelectedQuestion = async () => {
  const current = getQuestion(selectedQuestionId);
  if (!current) return;
  if (bank.questions.length <= 1) throw new Error("题库至少要保留一道题，不能删除最后一道题。");
  if (!window.confirm(`确定删除第 ${current.number} 题“${current.word}”吗？删除前会自动备份题库。`)) return;

  const currentIndex = bank.questions.findIndex((question) => question.id === current.id);
  const remainingQuestions = bank.questions.filter((question) => question.id !== current.id);
  const duplicateMerge = rebuildDuplicateReviews(remainingQuestions, {
    resetQuestionIds: [current.id],
  });
  const nextQuestions = duplicateMerge.questions;
  bank = await putJson(API.questions, { ...bank, questions: nextQuestions });
  reviews = normalizeReviews(await fetchJson(API.questionReviews));
  creatingQuestion = false;
  selectedQuestionId = nextQuestions[Math.min(currentIndex, nextQuestions.length - 1)]?.id || null;
  statusMessage = "题目已删除，原有题号保持不变";
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

const wireScoringEvents = () => {
  const form = adminApp.querySelector("#scoring-form");
  if (!form) return;
  const preview = form.querySelector("[data-scoring-preview]");
  const syncMirroredBaseFields = (source) => {
    if (!source.matches('[name="baseCorrect"], [name="baseWrongPenalty"]')) return;
    form.querySelectorAll(`[name="${source.name}"]`).forEach((input) => {
      if (input !== source) input.value = source.value;
    });
  };
  const readActiveValue = (name) => {
    const selectedMode = form.querySelector('input[name="mode"]:checked')?.value;
    const selectedCard = [...form.querySelectorAll(".scoring-mode-card")].find((card) => card.querySelector('input[name="mode"]')?.value === selectedMode);
    return selectedCard?.querySelector(`[name="${name}"]`)?.value ?? "";
  };
  const syncModeCards = () => {
    const selectedMode = form.querySelector('input[name="mode"]:checked')?.value;
    form.querySelectorAll(".scoring-mode-card").forEach((card) => {
      card.classList.toggle("selected", card.querySelector('input[name="mode"]')?.value === selectedMode);
    });
    const config = normalizeScoringConfig({ scoring: {
      mode: selectedMode,
      baseCorrect: readActiveValue("baseCorrect"),
      baseWrongPenalty: readActiveValue("baseWrongPenalty"),
      correctStreakAfter: readActiveValue("correctStreakAfter"),
      correctStreakScore: readActiveValue("correctStreakScore"),
      wrongStreakAfter: readActiveValue("wrongStreakAfter"),
      wrongStreakPenalty: readActiveValue("wrongStreakPenalty"),
    } });
    if (preview) preview.innerHTML = renderScoringPreview(config);
    const modeLabel = form.querySelector(".scoring-preview-heading span");
    if (modeLabel) modeLabel.textContent = `当前：${config.mode === "streak" ? "连续表现模式" : "固定计分模式"}`;
  };
  form.querySelectorAll(".scoring-mode-card").forEach((card) => card.addEventListener("click", (event) => {
    if (event.target.closest('input:not([type="radio"])')) return;
    const radio = card.querySelector('input[name="mode"]');
    if (radio && !radio.checked) {
      radio.checked = true;
      radio.dispatchEvent(new Event("change", { bubbles: true }));
    }
  }));
  form.querySelectorAll("input").forEach((input) => input.addEventListener("input", () => {
    syncMirroredBaseFields(input);
    syncModeCards();
  }));
  form.querySelectorAll('input[name="mode"]').forEach((input) => input.addEventListener("change", syncModeCards));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submitButton = form.querySelector('button[type="submit"]');
    if (submitButton) {
      submitButton.disabled = true;
      submitButton.textContent = "正在保存…";
    }
    try {
      await saveScoringSettings(form);
    } catch (error) {
      if (submitButton) {
        submitButton.disabled = false;
        submitButton.textContent = "保存并启用";
      }
      window.alert(error instanceof Error ? error.message : "计分机制保存失败。");
    }
  });
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

const wireSettingsEvents = () => {
  const bindForm = (selector, save, buttonText) => {
    const form = adminApp.querySelector(selector);
    form?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const submitButton = form.querySelector('button[type="submit"]');
      if (submitButton) {
        submitButton.disabled = true;
        submitButton.textContent = "正在保存…";
      }
      try {
        await save(form);
      } catch (error) {
        if (submitButton) {
          submitButton.disabled = false;
          submitButton.textContent = buttonText;
        }
        window.alert(error instanceof Error ? error.message : "保存失败。");
      }
    });
  };
  bindForm("#question-type-form", saveQuestionType, "新增题型");
  bindForm("#book-form", saveBook, "新增教材册");
  bindForm("#article-form", saveArticle, "新增文章");

  adminApp.querySelectorAll('[data-action="delete-question-type"]').forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        await deleteQuestionType(button.dataset.typeId);
      } catch (error) {
        window.alert(error instanceof Error ? error.message : "删除题型失败。");
      }
    });
  });
  adminApp.querySelectorAll('[data-action="delete-book"]').forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        await deleteBook(button.dataset.bookId);
      } catch (error) {
        window.alert(error instanceof Error ? error.message : "删除教材册失败。");
      }
    });
  });
  adminApp.querySelectorAll('[data-action="delete-article"]').forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        await deleteArticle(button.dataset.articleId);
      } catch (error) {
        window.alert(error instanceof Error ? error.message : "删除文章失败。");
      }
    });
  });
};

const wireSecurityEvents = () => {
  const form = adminApp.querySelector("#admin-password-form");
  form?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submitButton = form.querySelector('button[type="submit"]');
    if (submitButton) {
      submitButton.disabled = true;
      submitButton.textContent = "正在保存…";
    }
    try {
      await saveAdminPassword(form);
    } catch (error) {
      if (submitButton) {
        submitButton.disabled = false;
        submitButton.textContent = "保存新密码";
      }
      window.alert(error instanceof Error ? error.message : "密码保存失败。");
    }
  });
};

const wireEvents = () => {
  adminApp.querySelector('[data-action="check-update"]')?.addEventListener("click", checkForUpdates);
  adminApp.querySelector('[data-action="logout"]')?.addEventListener("click", () => {
    const token = adminToken;
    if (token) {
      fetch("./api/admin-logout", {
        method: "POST",
        headers: { "X-Wenyan-Admin-Token": token, "Content-Type": "application/json" },
        body: "{}",
        keepalive: true,
      }).catch(() => {});
    }
    adminAuthorized = false;
    adminToken = null;
    stopUpdatePolling();
    updateModalOpen = false;
    loginError = "";
    renderLogin();
  });
  adminApp.querySelector('[data-action="dismiss-update"]')?.addEventListener("click", () => {
    updatePromptDismissed = true;
    updateModalOpen = false;
    render();
  });
  adminApp.querySelector('[data-action="apply-update"]')?.addEventListener("click", applyAvailableUpdate);
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
    const wordInput = editor?.elements.namedItem("word");
    const sentenceInput = editor?.elements.namedItem("sentence");
    const targetStartInput = editor?.elements.namedItem("targetStart");
    const occurrenceInput = editor?.elements.namedItem("targetOccurrence");
    const targetPicker = editor?.querySelector("#sentence-target-picker");
    const targetLabel = editor?.querySelector("#selected-target-label");
    const applyTargetButton = editor?.querySelector('[data-action="apply-selected-target"]');
    const clearTargetButton = editor?.querySelector('[data-action="clear-selected-target"]');
    let lastPickedSelection = null;
    const initialQuestion = creatingQuestion ? null : getQuestion(selectedQuestionId);
    const updateTargetPicker = () => {
      if (!wordInput || !sentenceInput || !targetStartInput || !occurrenceInput || !targetPicker || !targetLabel) return;
      const sentence = sentenceInput.value.trim();
      const word = wordInput.value.trim();
      const occurrence = Number(occurrenceInput.value) || 1;
      targetPicker.innerHTML = word && sentence
        ? renderTargetSentence(sentence, word, occurrence, "editor-target-word")
        : escapeHtml(sentence);
      targetLabel.textContent = word ? `当前考点：${word}` : "当前考点：未选择";
    };
    const clearTargetSelection = () => {
      if (!wordInput || !targetStartInput || !occurrenceInput) return;
      lastPickedSelection = null;
      wordInput.value = "";
      targetStartInput.value = "";
      occurrenceInput.value = "";
      updateTargetPicker();
    };
    const applySelectedTarget = () => {
      if (!wordInput || !sentenceInput || !targetStartInput || !occurrenceInput || !targetPicker) return;
      const picked = getSelectionOffsets(targetPicker) || lastPickedSelection;
      if (!picked) {
        window.alert("请先在下方原句中拖选要考查的字词。");
        return;
      }
      const sentence = sentenceInput.value.trim();
      const word = picked.text;
      if (sentence.slice(picked.start, picked.start + word.length) !== word) {
        window.alert("选中的内容与原句不一致，请重新选择原句中的连续字词。");
        return;
      }
      const occurrences = getWordOccurrences(sentence, word);
      const selectedIndex = occurrences.findIndex((occurrence) => occurrence.start === picked.start);
      if (selectedIndex < 0) {
        window.alert("没有定位到这次选择，请重新拖选原句中的字词。");
        return;
      }
      wordInput.value = word;
      targetStartInput.value = String(picked.start);
      occurrenceInput.value = String(selectedIndex + 1);
      updateTargetPicker();
      window.getSelection()?.removeAllRanges();
    };
    const rememberTargetSelection = () => {
      const picked = getSelectionOffsets(targetPicker);
      if (picked) lastPickedSelection = picked;
    };
    sentenceInput?.addEventListener("input", clearTargetSelection);
    targetPicker?.addEventListener("mouseup", () => window.setTimeout(rememberTargetSelection, 0));
    targetPicker?.addEventListener("keyup", rememberTargetSelection);
    targetPicker?.addEventListener("contextmenu", (event) => event.preventDefault());
    applyTargetButton?.addEventListener("click", applySelectedTarget);
    clearTargetButton?.addEventListener("click", clearTargetSelection);
    if (initialQuestion?.word && sentenceInput?.value.trim() === String(initialQuestion.sentence || "").trim() && !wordInput?.value) {
      wordInput.value = initialQuestion.word;
      targetStartInput.value = String(getQuestionTargetStart(initialQuestion));
      occurrenceInput.value = initialQuestion.targetOccurrence ? String(initialQuestion.targetOccurrence) : "";
    }
    updateTargetPicker();
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
    adminApp.querySelector('[data-action="download-import-guide"]')?.addEventListener("click", downloadQuestionBankImportGuide);
    adminApp.querySelector('[data-action="toggle-question-history"]')?.addEventListener("click", () => {
      showQuestionBankHistory = !showQuestionBankHistory;
      render();
    });
    adminApp.querySelectorAll('[data-action="revoke-question-import"]').forEach((button) => {
      button.addEventListener("click", () => revokeQuestionBankImport(button.dataset.historyEventId));
    });
    const mergeButton = adminApp.querySelector('[data-action="merge-bank"]');
    const replaceButton = adminApp.querySelector('[data-action="replace-bank"]');
    const fileInput = adminApp.querySelector("#question-bank-file");
    mergeButton?.addEventListener("click", () => {
      if (!fileInput) return;
      fileInput.dataset.importMode = "merge";
      fileInput.click();
    });
    replaceButton?.addEventListener("click", () => {
      if (!fileInput) return;
      fileInput.dataset.importMode = "replace";
      fileInput.click();
    });
    fileInput?.addEventListener("change", async () => {
      const file = fileInput.files?.[0];
      if (!file) return;
      const importMode = fileInput.dataset.importMode || "merge";
      const activeImportButton = importMode === "replace" ? replaceButton : mergeButton;
      if (activeImportButton) {
        activeImportButton.disabled = true;
        activeImportButton.textContent = "正在导入…";
      }
      try {
        const imported = await importBankFromFile(file, importMode);
        if (!imported) {
          if (activeImportButton) {
            activeImportButton.disabled = false;
            activeImportButton.textContent = importMode === "replace" ? "导入并替换（谨慎）" : "新增导入题库（合并）";
          }
        }
      } catch (error) {
        window.alert(error instanceof Error ? error.message : "导入题库失败。");
        if (activeImportButton) {
          activeImportButton.disabled = false;
          activeImportButton.textContent = importMode === "replace" ? "导入并替换（谨慎）" : "新增导入题库（合并）";
        }
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

  if (activeTab === "settings") {
    wireSettingsEvents();
    return;
  }

  if (activeTab === "scoring") {
    wireScoringEvents();
    return;
  }

  if (activeTab === "security") {
    wireSecurityEvents();
    return;
  }

  if (activeTab === "records") {
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
    const [questionBank, leaderboardData, questionReviews, answerRecordData, questionBankHistoryData] = await Promise.all([
      fetchJson(API.questions),
      fetchJson(API.leaderboard),
      fetchJson(API.questionReviews),
      fetchJson(API.answerRecords),
      fetchJson(API.questionBankHistory),
    ]);
    if (!Array.isArray(questionBank.questions) || !questionBank.questions.length) throw new Error("题库中没有可编辑的题目。");
    bank = questionBank;
    leaderboard = normalizeLeaderboard(leaderboardData);
    reviews = normalizeReviews(questionReviews);
    answerRecords = normalizeAnswerRecords(answerRecordData);
    questionBankHistory = normalizeQuestionBankHistory(questionBankHistoryData);
    selectedQuestionId = bank.questions[0].id;
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
