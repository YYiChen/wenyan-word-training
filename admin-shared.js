// admin-shared.js: normalizers, availability helpers and authenticated request helpers
// Classic script module; shared state and API contracts remain in admin.js.

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

const normalizeAnswerRecords = (entries) => (Array.isArray(entries) ? entries : [])
  .filter((record) => record && typeof record.id === "string" && Array.isArray(record.questions))
  .map((record) => ({
    recordType: record.recordType === "pk" ? "pk" : "solo",
    matchId: String(record.matchId || "").trim(),
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
    pkMode: record.pkMode === "questions" ? "questions" : record.pkMode === "time" ? "time" : null,
    players: Array.isArray(record.players) ? record.players : [],
    sharedQuestionIds: Array.isArray(record.sharedQuestionIds) ? record.sharedQuestionIds : [],
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
  if (url === API.leaderboard) leaderboardEtag = response.headers.get("ETag") || null;
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
  if (url === API.leaderboard && leaderboardEtag) headers["If-Match"] = leaderboardEtag;
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
  if (url === API.leaderboard) leaderboardEtag = response.headers.get("ETag") || leaderboardEtag;
  return payload.data;
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

