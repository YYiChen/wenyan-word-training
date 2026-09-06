// admin-questions.js: question catalog, editor, import and question CRUD helpers
// Classic script module; shared state and API contracts remain in admin.js.

const getCatalog = () => Array.isArray(bank?.catalog) ? bank.catalog : [];
const getQuestion = (id) => bank?.questions.find((question) => question.id === id) || null;
const getArticle = (id) => getCatalog().find((article) => article.id === id) || null;

let pendingQuestionImport = null;
let questionImportApplying = false;

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
    bookId: "book_001",
    unit: "请填写单元",
    title: "请填写文章名称",
    author: "",
  };
  const exampleQuestion = {
    ...QUESTION_BANK_TEMPLATE_EXAMPLE.questions[0],
    articleId: article.id,
  };
  return {
    _templateInstructions: {
      purpose: "本文件用于制作可导入的文言实词四选一题库；下方目录中的 id 是程序识别用的稳定标识，label、title 是给人看的名称。",
      idVsName: "生成题目时，question.type 使用 questionTypes[].id，question.articleId 使用 catalog[].id；不要把中文名称直接填入这两个字段。篇目使用 catalog.bookId 关联教材册。",
      mergeRule: "普通新增导入不填写题目 ID；系统按篇目 ID、考察词、原句和 targetOccurrence 判断核心，再按题目细节识别完全重复、修改和重复候选。新题自动分配本机 ID，并进入待审。",
      occurrenceRule: "targetOccurrence 从 1 开始，表示 word 在 sentence 中第几次出现；不填写 targetStart，后台根据原句和考点实时定位。",
    },
    format: "wenyan-question-import",
    schemaVersion: "1.0",
    title: "请填写题库名称",
    description: "请说明适用年级、教材范围、教学进度和题目来源。",
    questionTypes: getQuestionTypes(),
    books: getBooks(),
    catalog: getCatalog().map((item) => ({
      ...item,
      bookId: item.bookId || getBooks().find((book) => book.label === item.volume)?.id || "",
      volume: undefined,
    })).map(({ volume, ...item }) => item),
    quizDefaults: {
      durationSeconds: 120,
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

## 当前教材册目录（题目通过 catalog.bookId 间接归类，不要填写教材册名称）

| 教材册 ID | 教材册名称 | 排序 |
| --- | --- | ---: |
${getBooks().map((book) => `| ${book.id} | ${book.label} | ${book.order} |`).join("\n")}

## 当前篇目目录（题目的 articleId 使用左侧 ID）

| 篇目 ID | 篇目名称 | 所属教材册名称 | 单元 | 作者 |
| --- | --- | --- | --- | --- |
${getCatalog().map((article) => `| ${article.id} | ${article.title} | ${article.volume} | ${article.unit || ""} | ${article.author || ""} |`).join("\n")}

## 可直接复制的 JSON 模版

下面的代码块是一个可导入的最小模版。请保留题型、教材册和篇目目录中的稳定 ID；普通新增导入不填写题目 \`id\`，系统会按内容判断重复并为新增题目分配本机编号。

\`\`\`json
${JSON.stringify(createQuestionBankTemplate(), null, 2)}
\`\`\`
`;

const saveBank = async (nextBank, message) => {
  bank = await putJson(API.questions, nextBank);
  syncReviewsFromBank();
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

const renderQuestionImportDialog = () => {
  if (!pendingQuestionImport) return "";
  const { mode, sourceName, preview, strategy } = pendingQuestionImport;
  const summary = preview.summary || {};
  const conflicts = Array.isArray(preview.conflicts) ? preview.conflicts : [];
  const conflictLimit = 80;
  const conflictItems = conflicts.slice(0, conflictLimit).map((item) => {
    const subject = item.questionId || item.id || item.kind || "目录";
    return `<li><strong>${escapeHtml(subject)}</strong>：${escapeHtml(item.message || "需要关注的变化")}</li>`;
  }).join("");
  const conflictMore = conflicts.length > conflictLimit
    ? `<p class="question-import-more">另有 ${conflicts.length - conflictLimit} 项变化未展开，请应用前先核对导入文件。</p>`
    : "";
  const summaryRows = [
    ["导入题目", summary.importedTotal || 0],
    ["完全相同（跳过）", summary.exactDuplicates || 0],
    ["未发现重复的新题", summary.newQuestions || 0],
    ["同核心细节修改", summary.modified || 0],
    ["核心内容重大修改", summary.majorModified || 0],
    ["重复候选", summary.duplicateCandidates || 0],
    ["目录冲突", summary.directoryConflicts || 0],
    ["审查结论冲突", summary.reviewConflicts || 0],
  ];
  const needsStrategy = mode === "merge" && ((summary.modified || 0) + (summary.majorModified || 0) + (summary.reviewConflicts || 0) + (summary.directoryConflicts || 0) > 0);
  return `
    <div class="question-import-preview-backdrop" role="presentation">
      <section class="question-import-preview" role="dialog" aria-modal="true" aria-labelledby="question-import-preview-title">
        <div class="question-import-preview-heading">
          <div>
            <p class="eyebrow">导入前检查</p>
            <h2 id="question-import-preview-title">${mode === "replace" ? "确认替换题库" : "确认新增导入题库"}</h2>
            <p class="admin-subtitle">文件：${escapeHtml(sourceName)}。预览只读，确认应用后才会写入本机硬盘。</p>
          </div>
          <span class="question-import-preview-badge">${preview.sameBank ? "同一题库" : "外部题库"}</span>
        </div>
        <div class="question-import-summary-grid">
          ${summaryRows.map(([label, value]) => `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join("")}
        </div>
        ${mode === "replace"
          ? `<p class="question-import-warning">替换会用导入文件建立新的题库版本；当前题库会先自动备份，历史记录保留。此操作只建议用于完整题库恢复。</p>`
          : preview.sameBank
            ? `<p class="question-import-note">同一题库导入：默认保留本机的修改题目；未变化题目的已审结论优先于待审，双方均已审结且不一致时按下方策略处理；新题按导入文件的审查状态进入流程。</p>`
            : `<p class="question-import-note">外部题库只会新增未重复题目；外部文件中的审查结论不会直接继承，新题导入后进入待审，确认通过后才会给学生抽取。</p>`}
        ${needsStrategy ? `
          <fieldset class="question-import-strategy">
            <legend>遇到已有内容变化时</legend>
            <label><input type="radio" name="question-import-strategy" value="preserve_local" ${strategy === "preserve_local" ? "checked" : ""}> 保留本机版本（推荐）</label>
            <label><input type="radio" name="question-import-strategy" value="use_imported" ${strategy === "use_imported" ? "checked" : ""}> 使用导入版本（题目内容及审查状态以导入文件为准；导入端待审时会恢复待审）</label>
          </fieldset>
        ` : ""}
        <div class="question-import-review-summary">
          <span>审查状态：本机待审 ${preview.reviewSummary?.current?.pending || 0}，导入待审 ${preview.reviewSummary?.imported?.pending || 0}</span>
          <span>应用后的重复候选题需要在“快速审查”中处理</span>
        </div>
        ${conflicts.length ? `<div class="question-import-conflicts"><h3>需要留意的变化（${conflicts.length}）</h3><ul>${conflictItems}</ul>${conflictMore}</div>` : `<div class="question-import-no-conflicts">没有发现需要人工选择的目录或题目冲突。</div>`}
        <div class="question-import-actions">
          <button class="admin-secondary" type="button" data-action="cancel-question-import">取消</button>
          <button class="admin-primary" type="button" data-action="apply-question-import" ${questionImportApplying ? "disabled" : ""}>${questionImportApplying ? "正在写入…" : mode === "replace" ? "确认替换题库" : "确认新增导入"}</button>
        </div>
      </section>
    </div>
  `;
};

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

const downloadBank = async () => {
  const filename = "文言实词题库-当前完整题库.json";
  try {
    const result = await postJson(API.questionBankExport, { sourceName: filename });
    const exportedBank = result?.bank;
    if (!exportedBank || !Array.isArray(exportedBank.questions)) {
      throw new Error("服务器返回的完整题库格式无效。");
    }
    const blob = new Blob([JSON.stringify(exportedBank, null, 2)], { type: "application/json;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    link.click();
    URL.revokeObjectURL(url);
    questionBankHistory = normalizeQuestionBankHistory(result.history || questionBankHistory);
    statusMessage = "当前题库已导出，导出记录已保存。";
  } catch (error) {
    statusMessage = `题库导出失败：${error instanceof Error ? error.message : "未知错误"}`;
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

const validateImportedBankShape = (imported) => {
  // Legacy browser-side shape/merge helpers are retained for compatibility
  // with older cached admin pages. New imports always use server preview/apply
  // and the authoritative planner; do not call these helpers for new code.
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
    format: "wenyan-question-bank",
    schemaVersion: "4.0",
    bankId: base.bankId,
    workflow: base.workflow || { reviews: {}, duplicateResolutions: {} },
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
  if (!imported || typeof imported !== "object" || Array.isArray(imported)) {
    throw new Error("导入失败：文件必须是 JSON 对象。");
  }
  const preview = await postJson(API.questionBankPreview, { mode, sourceName: file.name, package: imported });
  pendingQuestionImport = { mode, sourceName: file.name, package: imported, preview, strategy: "preserve_local" };
  questionImportApplying = false;
  statusMessage = "";
  render();
  return true;
};

const applyQuestionImportPreview = async () => {
  if (!pendingQuestionImport || questionImportApplying) return;
  const request = pendingQuestionImport;
  questionImportApplying = true;
  render();
  try {
    const result = await postJson(API.questionBankApply, {
      mode: request.mode,
      strategy: request.strategy,
      sourceName: request.sourceName,
      package: request.package,
      baseEtag: request.preview.baseEtag,
    });
    if (request.mode === "replace") {
      bank = result.bank;
      questionBankHistory = normalizeQuestionBankHistory(result.history || questionBankHistory);
      const abnormalCount = getAbnormalQuestionCount(bank.questions);
      statusMessage = `已替换为 ${bank.questions.length} 道题${abnormalCount ? `；${abnormalCount} 道划线异常题已标记并跳过答题` : ""}`;
    } else {
      bank = result.bank;
      questionBankHistory = normalizeQuestionBankHistory(result.history || questionBankHistory);
      statusMessage = `合并完成：当前题库共 ${bank.questions.length} 道题，新导入题请在快速审查中确认。`;
    }
    syncReviewsFromBank();
    selectedQuestionId = bank.questions[0]?.id || null;
    creatingQuestion = false;
    pendingQuestionImport = null;
    questionImportApplying = false;
    render();
  } catch (error) {
    questionImportApplying = false;
    statusMessage = `题库导入失败：${error instanceof Error ? error.message : "未知错误"}`;
    render();
  }
};

const cancelQuestionImportPreview = () => {
  pendingQuestionImport = null;
  questionImportApplying = false;
  statusMessage = "";
  render();
};

const wireQuestionImportDialogEvents = () => {
  adminApp.querySelector('[data-action="cancel-question-import"]')?.addEventListener("click", cancelQuestionImportPreview);
  adminApp.querySelector('[data-action="apply-question-import"]')?.addEventListener("click", applyQuestionImportPreview);
  adminApp.querySelectorAll('input[name="question-import-strategy"]').forEach((input) => {
    input.addEventListener("change", () => {
      if (pendingQuestionImport) pendingQuestionImport.strategy = input.value;
    });
  });
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
    syncReviewsFromBank();
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
  if (getCatalog().some((article) => article.bookId === book.id)) throw new Error("这个教材册还有所属文章，不能删除；请先处理这些文章。");
  if (!window.confirm(`确定删除教材册“${book.label}”吗？`)) return;
  await saveBank({ ...bank, books: getBooks().filter((item) => item.id !== id) }, "教材册已删除。");
};

const saveArticle = async (form) => {
  const formData = new FormData(form);
  const id = formData.get("id").toString().trim();
  const title = formData.get("title").toString().trim();
  const bookId = formData.get("bookId").toString().trim();
  const unit = formData.get("unit").toString().trim();
  const author = formData.get("author").toString().trim();
  const book = getBooks().find((item) => item.id === bookId);
  if (!/^[A-Za-z][A-Za-z0-9_-]*$/.test(id)) throw new Error("文章 ID 必须以英文字母开头，只能包含字母、数字、下划线或短横线。");
  if (!book) throw new Error("请选择有效的所属教材册。");
  if (getCatalog().some((article) => article.id === id)) throw new Error("这个文章 ID 已经存在。");
  const article = { id, bookId: book.id, title, unit, author };
  await saveBank({ ...bank, catalog: [...getCatalog(), article] }, "新文章已保存。");
};

const deleteArticle = async (id) => {
  const article = getArticle(id);
  if (!article) return;
  if (bank.questions.some((question) => question.articleId === id)) throw new Error("这篇文章还有题目，不能删除；请先删除或转移相关题目。");
  if (!window.confirm(`确定删除文章“${article.title}”吗？`)) return;
  await saveBank({ ...bank, catalog: getCatalog().filter((item) => item.id !== id) }, "文章已删除。");
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
    // Empty IDs are intentional for new manual rows. The server assigns the
    // canonical q_* ID so the browser cannot become the identity authority.
    id: current?.id || "",
    number: current?.number || getNextQuestionNumber(),
    type: formData.get("type").toString(),
    word,
    articleId: article.id,
    sentence,
    targetOccurrence,
    explanation: formData.get("explanation").toString().trim(),
    answer: formData.get("answer").toString(),
    options,
    source: current?.source || {
      kind: "admin_created",
      title: "管理后台新增题目",
    },
  };
  for (const key of ["rule", "context", "supportingItems", "rawText"]) {
    if (current?.[key] !== undefined) updated[key] = current[key];
  }
  const stem = formData.get("stem").toString().trim();
  if (stem) updated.stem = stem;
  else delete updated.stem;
  if (!updated.word || !updated.sentence || !updated.explanation) throw new Error("实词、原句和解析不能为空。");

  const nextQuestions = current
    ? bank.questions.map((question) => question.id === updated.id ? updated : question)
    : [...bank.questions, updated];
  const nextBank = {
    ...bank,
    // The v4 server rebuilds duplicate resolutions from canonical questions.
    questions: nextQuestions,
  };
  bank = await putJson(API.questions, nextBank);
  syncReviewsFromBank();
  selectedQuestionId = bank.questions.find((question) => question.id === updated.id)
    ?.id || bank.questions.find((question) => question.number === updated.number && question.word === updated.word)?.id || null;
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
  // The v4 server removes stale duplicate resolutions and rebuilds remaining
  // groups during canonical validation.
  const nextQuestions = bank.questions.filter((question) => question.id !== current.id);
  bank = await putJson(API.questions, { ...bank, questions: nextQuestions });
  syncReviewsFromBank();
  creatingQuestion = false;
  selectedQuestionId = nextQuestions[Math.min(currentIndex, nextQuestions.length - 1)]?.id || null;
  statusMessage = "题目已删除，原有题号保持不变";
  render();
};


const wireQuestionEvents = () => {
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
        await importBankFromFile(file, importMode);
      } catch (error) {
        statusMessage = `题库导入预览失败：${error instanceof Error ? error.message : "未知错误"}`;
        render();
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

};
