const adminApp = document.querySelector("#admin-app");

const API = {
  questions: "./api/questions",
  leaderboard: "./api/leaderboard",
  answerRecords: "./api/answer-records",
  answerRecordsImport: "./api/answer-records/import",
  questionReviews: "./api/question-reviews",
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
} = window.WenyanScoring;

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
2. “catalog”是篇目目录，题目的“articleId”必须能在其中找到。
3. “books”是教材册/范围目录；“catalog[].volume”和“questions[].volume”必须使用对应教材册的“label”。
4. “questionTypes”是题型目录；题目的“type”必须使用其中的“id”。目前自定义题型会按普通四选一题显示。
5. 每道题的“id”必须唯一。使用“新增导入题库（合并）”时，和现有题库重复的题目 ID 会跳过，不会覆盖原题。
6. 每道题必须有四个选项，选项键必须恰好是 A、B、C、D，“answer”必须是其中一个键。

## 顶层字段

- “schemaVersion”：格式版本，建议填写“3.0”。
- “title”、“description”：题库名称和说明。
- “questionTypes”：题型数组，每项需要“id”、“label”，可选“description”。
- “books”：教材册数组，每项需要唯一“id”、“label”，可选“order”。
- “catalog”：文章数组，每项需要唯一“id”、“title”、“volume”，可选“unit”、“author”。
- “quizDefaults”：可选的训练设置。旧题库可以继续使用“correctScore”和“wrongScore”；新格式建议使用“scoring”配置计分机制。
- “quizDefaults.scoring.mode”：填写“fixed”或“streak”。“fixed”是固定计分；“streak”是连续表现计分。
- “quizDefaults.scoring”：可配置“baseCorrect”、“baseWrongPenalty”、“correctStreakAfter”、“correctStreakScore”、“wrongStreakAfter”和“wrongStreakPenalty”。连续次数超过对应 After 值后，当前题开始使用对应的连击分或连续错误扣分。
- “questions”：题目数组。

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

- ID 是程序内部识别用的稳定值，必须唯一，建议只使用英文字母、数字、下划线和短横线，并以英文字母开头。ID 一旦用于正式题库，后续不要随意改名。
- label 或 title 是页面显示名称，例如教材册 ID “xxbs”对应名称“选择性必修上册”，篇目 ID “xx2_suwu”对应名称“苏武传”。
- 题目的 type 必须填写题型 ID，不是题型名称；题目的 articleId 必须填写篇目 ID，不是文章名称；题目的 volume 填教材册名称，也就是 books[].label。
- 如果只是给一篇已经存在的文章增加新题，请从 catalog 中复制该文章的 article ID，所有新增题目使用新的 question ID，不要复制旧题目的 question ID。

## 教师按课导入的推荐流程

1. 在模板的 questionTypes、books、catalog 中找到要使用的题型 ID、教材册 ID/名称和文章 ID/名称。
2. 如果文章已经存在，只复用原有 catalog[].id；如果文章不存在，先新增教材册，再新增文章并给它一个新的稳定 ID。
3. 每道新增题使用全局唯一的 question ID，例如“bx1_quxue_teacher_001”。
4. 写入 word、sentence 和 targetOccurrence；先从左到右数 word 在 sentence 中的出现次数。不要因为原句里有相同字词，就默认第一处一定是考点。若填写 targetStart，请按从 0 开始的字符位置填写。
5. 如果同一句还要考另一个不同的词，复制 sentence 新建另一道题，并为新题单独计算 targetStart 和 targetOccurrence。
6. 写入四个选项、正确答案和解析。三个干扰项应是同一个词的其他常见义项或相邻义项，不能只是随意拼凑。
7. 导入前先检查 articleId、type、volume 的对应关系；再使用“新增导入题库（合并）”。
8. 导入后在后台“快速审查”逐题核对原句、考点位置、答案和干扰项。

## 程序会拒绝的常见错误

- articleId 不在 catalog 中，或 type 不在 questionTypes 中。
- volume 不在 books 的 label 中，或文章的 volume 与题目的 volume 不一致。
- word 在 sentence 中找不到，targetOccurrence 小于 1，或 targetOccurrence 大于实际出现次数。
- targetStart 不是从 0 开始的实际字符位置，或 targetStart 与 targetOccurrence 指向的出现位置不一致。
- 同一句的不同考点没有拆成不同题目，或一题的 word 同时包含多个互不相连的词。
- question.id、catalog.id、books.id、questionTypes.id 在各自目录中重复。
- options 不是恰好四项，选项键不是 A、B、C、D，或选项文字重复。

## 最小示例

请直接参考本文件末尾的“可直接复制的 JSON 模版”。模版会列出当前题库的全部题型、教材册和篇目目录；生成多道题时，只需继续向“questions”数组添加题目，并保证 ID、篇目 ID、题型 ID 对应正确。

## 合并导入说明

“新增导入题库（合并）”会把新篇目、新教材册、新题型和新题目加入当前题库；同 ID 的题目不会覆盖旧题，导入前会自动备份当前题库。需要修改原题时，请在后台编辑，或先导出当前题库后生成新的完整文件，再使用“导入并替换（谨慎）”。
`;

let bank = null;
let leaderboard = [];
let answerRecords = [];
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
let updateStatus = { phase: "idle", available: false, currentVersion: "1.3.0", latestVersion: null, progress: 0 };
let updateModalOpen = false;
let updatePromptDismissed = false;
let updatePollTimer = null;
let updateRestarting = false;
let showArchivedRecords = false;
const selectedAnswerRecordIds = new Set();

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
      await authenticateAdmin(password);
    } catch (error) {
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
      mergeRule: "使用后台的新增导入题库（合并）时，重复 question.id 不会覆盖旧题；请为新增题目使用全局唯一 ID。",
       occurrenceRule: "targetOccurrence 从 1 开始，表示 word 在 sentence 中第几次出现；同一个词出现多次时必须明确填写。targetStart 可填写 word 在 sentence 中从 0 开始的字符起点，后台会校验两者一致。",
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

下面的代码块是一个可导入的最小模版。请保留目录中的稳定 ID；新增题目时只需向 \`questions\` 数组继续添加题目，并为每道题使用新的全局唯一 \`id\`。

\`\`\`json
${JSON.stringify(createQuestionBankTemplate(), null, 2)}
\`\`\`
`;

const saveBank = async (nextBank, message) => {
  bank = await putJson(API.questions, nextBank);
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
    targetOccurrence: 1,
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

const postJson = async (url, value) => {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(value),
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok || !payload?.ok) throw new Error(payload?.error || "请求失败。");
  return payload.data;
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

const authenticateAdmin = async (password) => postJson(API.adminAuth, { password });

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

const importAnswerRecordsJson = async (records) => {
  const response = await fetch(API.answerRecordsImport, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
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
  }).sort((left, right) => {
    const leftPending = getQuestionReview(left.id).status === "pending" ? 0 : 1;
    const rightPending = getQuestionReview(right.id).status === "pending" ? 0 : 1;
    return leftPending - rightPending
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
  const meta = getReviewStatusMeta(review.status);
  const isSaving = savingReviewIds.has(question.id);
  const expanded = expandedReviewId === question.id;
  const sourceTitle = question.source?.title || "暂无来源说明";
  const occurrenceCount = getWordOccurrences(question.sentence, question.word).length;
  const occurrenceLabel = occurrenceCount > 1 ? ` · 第 ${Number(question.targetOccurrence) || 1} 处/共 ${occurrenceCount} 处` : "";
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
          <span class="review-word">考查实词：${escapeHtml(question.word)}${escapeHtml(occurrenceLabel)}</span>
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
             <span class="question-list-topline"><span>#${question.number}</span><span>${escapeHtml(question.word)}${getWordOccurrences(question.sentence, question.word).length > 1 ? ` · 第 ${Number(question.targetOccurrence) || 1} 处` : ""}</span></span>
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
              ${getQuestionTypes().map((type) => `<option value="${escapeHtml(type.id)}" ${question.type === type.id ? "selected" : ""}>${escapeHtml(type.label)}</option>`).join("")}
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

const renderQuestionTools = () => `
  <section class="admin-card question-tools" aria-label="题库文件工具">
    <div class="question-tools-copy">
      <h2 class="admin-card-title">题库文件</h2>
      <p>可以导出当前完整题库，也可以把另一份题库合并进来。所有写入都会先自动备份原文件。</p>
    </div>
    <div class="question-tools-actions">
      <button class="admin-secondary" type="button" data-action="export-bank">导出当前题库 JSON</button>
      <button class="admin-secondary" type="button" data-action="download-import-guide">下载 JSON模版导入说明</button>
      <button class="admin-primary" type="button" data-action="merge-bank">新增导入题库（合并）</button>
      <button class="admin-danger" type="button" data-action="replace-bank">导入并替换（谨慎）</button>
      <input id="question-bank-file" type="file" accept="application/json,.json" hidden />
    </div>
  </section>
`;

const renderQuestionTab = () => `${renderQuestionTools()}<div class="admin-grid">${renderQuestionList()}${renderQuestionEditor()}</div>`;

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
  return `
    <div class="scoring-preview-line"><span class="scoring-preview-label">连续答对</span><strong>${formatScoreDelta(correctBase.scoreDelta)} → … → 第 ${config.correctStreakAfter + 1} 次 ${formatScoreDelta(correctSuper.scoreDelta)}</strong></div>
    <div class="scoring-preview-line"><span class="scoring-preview-label">连续答错</span><strong>${formatScoreDelta(wrongBase.scoreDelta)} → … → 第 ${config.wrongStreakAfter + 1} 次 ${formatScoreDelta(wrongSuper.scoreDelta)}</strong></div>
  `;
};

const renderScoringTab = () => {
  const config = normalizeScoringConfig(bank.quizDefaults);
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
          <label class="scoring-mode-card ${config.mode === "fixed" ? "selected" : ""}">
            <input type="radio" name="mode" value="fixed" ${config.mode === "fixed" ? "checked" : ""} />
            <span class="scoring-mode-card-body">
              <strong>固定计分</strong>
              <span>每道题使用同一套基础加分和扣分。</span>
              <small>当前示例：答对 ${formatScoreDelta(config.baseCorrect)}，答错 ${formatScoreDelta(-config.baseWrongPenalty)}</small>
            </span>
          </label>
          <label class="scoring-mode-card ${config.mode === "streak" ? "selected" : ""}">
            <input type="radio" name="mode" value="streak" ${config.mode === "streak" ? "checked" : ""} />
            <span class="scoring-mode-card-body">
              <strong>连续表现</strong>
              <span>连续答对进入连击加分，连续答错进入连续错误扣分。</span>
              <small>答对 ${formatScoreDelta(config.baseCorrect)} / 连击 ${formatScoreDelta(config.correctStreakScore)} · 答错 ${formatScoreDelta(-config.baseWrongPenalty)} / 连错 ${formatScoreDelta(-config.wrongStreakPenalty)}</small>
            </span>
          </label>
        </div>
      </section>
      <section class="admin-card scoring-card">
        <div class="settings-card-heading">
          <div>
            <h2 class="admin-card-title">分值与连续条件</h2>
            <p>“达到 N 题后”表示第 N+1 题开始使用连击分；答对和答错会互相重置连续次数。</p>
          </div>
        </div>
        <div class="scoring-field-groups">
          <fieldset class="scoring-field-group">
            <legend>基础分</legend>
            <label class="editor-field">答对一题加
              <span class="admin-number-with-unit"><input class="admin-input" name="baseCorrect" type="number" min="0" max="1000" step="1" value="${config.baseCorrect}" required /><span>分</span></span>
            </label>
            <label class="editor-field">答错一题扣
              <span class="admin-number-with-unit"><input class="admin-input" name="baseWrongPenalty" type="number" min="0" max="1000" step="1" value="${config.baseWrongPenalty}" required /><span>分</span></span>
            </label>
          </fieldset>
          <fieldset class="scoring-field-group">
            <legend>连续答对 · 连击加分</legend>
            <label class="editor-field">连续答对达到
              <span class="admin-number-with-unit"><input class="admin-input" name="correctStreakAfter" type="number" min="1" max="1000" step="1" value="${config.correctStreakAfter}" required /><span>题后</span></span>
            </label>
            <label class="editor-field">从下一题起每题加
              <span class="admin-number-with-unit"><input class="admin-input" name="correctStreakScore" type="number" min="0" max="1000" step="1" value="${config.correctStreakScore}" required /><span>分</span></span>
            </label>
          </fieldset>
          <fieldset class="scoring-field-group">
            <legend>连续答错 · 连续错误扣分</legend>
            <label class="editor-field">连续答错达到
              <span class="admin-number-with-unit"><input class="admin-input" name="wrongStreakAfter" type="number" min="1" max="1000" step="1" value="${config.wrongStreakAfter}" required /><span>题后</span></span>
            </label>
            <label class="editor-field">从下一题起每题扣
              <span class="admin-number-with-unit"><input class="admin-input" name="wrongStreakPenalty" type="number" min="0" max="1000" step="1" value="${config.wrongStreakPenalty}" required /><span>分</span></span>
            </label>
          </fieldset>
        </div>
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
          <p class="admin-subtitle">答题记录只能折叠或恢复，不能从系统中删除；折叠前会自动备份，导入导出均使用本机 JSON 文件。</p>
        </div>
        <span class="admin-count">未折叠 ${answerRecords.length - archivedCount} 条 · 已折叠 ${archivedCount} 条</span>
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

const pollUpdateStatus = async (autoPrompt = false) => {
  try {
    const next = await fetchJson(API.updateStatus);
    applyUpdateStatus(next, autoPrompt);
    if (UPDATE_BUSY_PHASES.has(next.phase)) {
      updatePollTimer = window.setTimeout(() => pollUpdateStatus(autoPrompt), 800);
    }
  } catch {
    stopUpdatePolling();
    if (!updateRestarting) {
      updateStatus = { ...updateStatus, phase: "unavailable", available: false };
      render();
    }
  }
};

const startUpdateMonitoring = () => {
  stopUpdatePolling();
  void pollUpdateStatus(true);
};

const checkForUpdates = async () => {
  updateRestarting = false;
  updateStatus = { ...updateStatus, phase: "checking", available: false, progress: 0 };
  updateModalOpen = false;
  render();
  try {
    const next = await postJson(API.updateCheck, {});
    applyUpdateStatus(next, false);
    if (next.available) {
      updatePromptDismissed = false;
      updateModalOpen = true;
      render();
    }
    if (UPDATE_BUSY_PHASES.has(next.phase)) {
      updatePollTimer = window.setTimeout(() => pollUpdateStatus(false), 500);
    }
  } catch {
    updateStatus = { ...updateStatus, phase: "unavailable", available: false };
    render();
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
        <button class="admin-secondary" type="button" data-action="check-update">检查更新</button>
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

const downloadBank = () => {
  const blob = new Blob([JSON.stringify(bank, null, 2)], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "文言实词题库-当前完整题库.json";
  link.click();
  URL.revokeObjectURL(url);
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
  statusMessage = `答题记录导入完成：新增 ${result.addedCount} 条，跳过 ${result.skippedCount} 条重复记录`;
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

  const existingIds = new Set(base.questions.map((question) => question.id));
  const importedIds = new Set();
  const newQuestions = [];
  let skippedQuestions = 0;
  imported.questions.forEach((question, index) => {
    if (!question || typeof question.id !== "string" || !question.id.trim()) {
      throw new Error(`合并失败：第 ${index + 1} 道题缺少 id。`);
    }
    if (importedIds.has(question.id)) throw new Error(`合并失败：导入文件内存在重复题目 ID“${question.id}”。`);
    importedIds.add(question.id);
    if (existingIds.has(question.id)) {
      skippedQuestions += 1;
      return;
    }
    newQuestions.push(question);
  });

  const mergedTypes = [...typeById.values()];
  const allowedTypes = new Set(mergedTypes.map((type) => type.id));
  newQuestions.forEach((question, index) => {
    if (!allowedTypes.has(question.type)) {
      throw new Error(`合并失败：新增题目“${question.id || index + 1}”使用了未定义的题型“${question.type}”。`);
    }
  });

  const merged = {
    ...base,
    schemaVersion: imported.schemaVersion || base.schemaVersion || "3.0",
    questionTypes: mergedTypes,
    books: [...bookById.values()],
    catalog,
    questions: reindexQuestions([...base.questions, ...newQuestions]),
  };
  return {
    bank: merged,
    addedQuestions: newQuestions.length,
    skippedQuestions,
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
    bank = await putJson(API.questions, imported);
    statusMessage = `已替换为 ${bank.questions.length} 道题`;
  } else {
    const merged = mergeQuestionBank(bank, imported);
    if (!window.confirm(`确定把“${file.name}”新增到当前题库吗？将新增 ${merged.addedQuestions} 道题、${merged.addedArticles} 篇文章、${merged.addedBooks} 册教材和 ${merged.addedTypes} 种题型；重复题目 ID 将跳过。`)) return false;
    bank = await putJson(API.questions, merged.bank);
    statusMessage = `合并完成：新增 ${merged.addedQuestions} 道题，跳过 ${merged.skippedQuestions} 道重复题`;
  }
  selectedQuestionId = bank.questions[0]?.id || null;
  creatingQuestion = false;
  render();
  return true;
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

const readScoringForm = (form) => {
  const formData = new FormData(form);
  const mode = formData.get("mode").toString();
  if (!["fixed", "streak"].includes(mode)) throw new Error("请选择有效的计分机制。");
  const readNumber = (name, minimum) => {
    const value = Number(formData.get(name));
    if (!Number.isInteger(value) || value < minimum || value > 1000) {
      throw new Error(`${name} 必须是 ${minimum === 1 ? "1-1000" : "0-1000"} 的整数。`);
    }
    return value;
  };
  return serializeScoringConfig({
    mode,
    baseCorrect: readNumber("baseCorrect", 0),
    baseWrongPenalty: readNumber("baseWrongPenalty", 0),
    correctStreakAfter: readNumber("correctStreakAfter", 1),
    correctStreakScore: readNumber("correctStreakScore", 0),
    wrongStreakAfter: readNumber("wrongStreakAfter", 1),
    wrongStreakPenalty: readNumber("wrongStreakPenalty", 0),
  });
};

const saveScoringSettings = async (form) => {
  const scoring = readScoringForm(form);
  const quizDefaults = bank.quizDefaults && typeof bank.quizDefaults === "object" ? bank.quizDefaults : {};
  await saveBank({
    ...bank,
    quizDefaults: {
      ...quizDefaults,
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

const wireScoringEvents = () => {
  const form = adminApp.querySelector("#scoring-form");
  if (!form) return;
  const preview = form.querySelector("[data-scoring-preview]");
  const syncModeCards = () => {
    const selectedMode = form.querySelector('input[name="mode"]:checked')?.value;
    form.querySelectorAll(".scoring-mode-card").forEach((card) => {
      card.classList.toggle("selected", card.querySelector('input[name="mode"]')?.value === selectedMode);
    });
    const raw = Object.fromEntries(new FormData(form).entries());
    const config = normalizeScoringConfig({ scoring: {
      mode: raw.mode,
      baseCorrect: raw.baseCorrect,
      baseWrongPenalty: raw.baseWrongPenalty,
      correctStreakAfter: raw.correctStreakAfter,
      correctStreakScore: raw.correctStreakScore,
      wrongStreakAfter: raw.wrongStreakAfter,
      wrongStreakPenalty: raw.wrongStreakPenalty,
    } });
    if (preview) preview.innerHTML = renderScoringPreview(config);
    const modeLabel = form.querySelector(".scoring-preview-heading span");
    if (modeLabel) modeLabel.textContent = `当前：${config.mode === "streak" ? "连续表现模式" : "固定计分模式"}`;
  };
  form.querySelectorAll("input").forEach((input) => input.addEventListener("input", syncModeCards));
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
    adminAuthorized = false;
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
    const [questionBank, leaderboardData, questionReviews, answerRecordData] = await Promise.all([
      fetchJson(API.questions),
      fetchJson(API.leaderboard),
      fetchJson(API.questionReviews),
      fetchJson(API.answerRecords),
    ]);
    if (!Array.isArray(questionBank.questions) || !questionBank.questions.length) throw new Error("题库中没有可编辑的题目。");
    bank = questionBank;
    leaderboard = normalizeLeaderboard(leaderboardData);
    reviews = normalizeReviews(questionReviews);
    answerRecords = normalizeAnswerRecords(answerRecordData);
    selectedQuestionId = bank.questions[0].id;
    render();
    startUpdateMonitoring();
  } catch (error) {
    renderError(error instanceof Error ? error.message : "题库读取失败。");
  }
};

window.addEventListener("pagehide", () => {
  adminAuthorized = false;
  stopUpdatePolling();
  updateModalOpen = false;
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
