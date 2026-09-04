const app = document.querySelector("#app");

const FALLBACK_CONFIG = {
  durationSeconds: 120,
  correctScore: 1,
  wrongScore: -1,
};
const FONT_SCALE_STORAGE_KEY = "wenyan-quiz-font-scale";
const FONT_SCALE_MIN = 1;
const FONT_SCALE_MAX = 1.8;
const FONT_SCALE_STEP = 0.1;
const DEFAULT_FONT_SCALE = 1;
let bank = null;
let timerId = null;
let state = null;
let startSelection = { volumes: ["all"], articleIds: ["all"] };
let leaderboard = [];

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
  .map((entry) => ({
    name: entry.name.trim().slice(0, 20),
    score: Number(entry.score),
    createdAt: Number(entry.createdAt) || 0,
  }))
  .filter((entry) => entry.name)
  .sort((left, right) => right.score - left.score || right.createdAt - left.createdAt);

const readLeaderboard = () => [...leaderboard];

const saveLeaderboard = async (entries) => {
  const response = await fetch("./api/leaderboard", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(entries),
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok || !result.ok) {
    throw new Error(result.error || "排行榜保存失败。");
  }
  leaderboard = normalizeLeaderboard(result.data);
};

const loadLeaderboard = async () => {
  const response = await fetch("./api/leaderboard", { cache: "no-store" });
  const result = await response.json().catch(() => null);
  if (!response.ok || !Array.isArray(result)) {
    throw new Error("排行榜文件读取失败。");
  }
  leaderboard = normalizeLeaderboard(result);
};

const saveLeaderboardEntry = async (name, score) => {
  const entries = readLeaderboard();
  entries.push({ name: name.trim().slice(0, 20), score: Number(score), createdAt: Date.now() });
  await saveLeaderboard(entries);
};

const validateBank = (payload) => {
  if (!payload || !Array.isArray(payload.questions) || payload.questions.length === 0) {
    throw new Error("题库中没有可用题目。");
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

  return payload;
};

const getCatalog = () => Array.isArray(bank?.catalog) ? bank.catalog : [];

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
    selectedVolumes.has(question.volume) && selectedArticleIds.has(question.articleId)
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
  const config = { ...FALLBACK_CONFIG, ...(bank.quizDefaults || {}) };
  const volumes = [...new Set(getCatalog().map((article) => article.volume))];
  const selectedVolumes = normalizeSelection(startSelection.volumes, volumes);
  const availableArticles = getAvailableArticles(selectedVolumes);
  const selectedArticleIds = normalizeSelection(
    startSelection.articleIds,
    availableArticles.map((article) => article.id),
  );
  startSelection = { volumes: selectedVolumes, articleIds: selectedArticleIds };
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
        <p class="start-subtitle">从教材原句中辨认实词义项。两分钟内连续答题，看看你能拿到多少分。</p>
        <p class="start-note">题库覆盖必修与选择性必修五册教材的课内文章；每道题的干扰项已审核固定，答题时只改变选项顺序。</p>
      </div>
      <div class="start-card">
        <p class="card-label">选择训练范围</p>
        <p class="filter-note">教材册和篇目都可以多选；勾选“全部”会直接勾上下面的所有项目。</p>
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
          <div class="rule-item"><span>答对一题</span><strong>+${config.correctScore} 分</strong></div>
          <div class="rule-item"><span>答错一题</span><strong>${config.wrongScore} 分</strong></div>
        </div>
        <div class="button-stack">
          <button class="primary-button" type="button" data-action="start">开始答题</button>
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

const renderLeaderboard = () => {
  const entries = readLeaderboard();
  app.innerHTML = `
    <section class="state-screen leaderboard-screen" aria-labelledby="leaderboard-title">
      <div class="leaderboard-card">
        <p class="eyebrow">往次成绩</p>
        <h1 id="leaderboard-title">排行榜</h1>
        <p class="leaderboard-intro">本机题库文件保存的历史成绩，按分数从高到低排列。</p>
        ${entries.length === 0 ? `
          <div class="leaderboard-empty">还没有成绩记录，完成第一局后就会出现在这里。</div>
        ` : `
          <div class="leaderboard-list" aria-label="历史成绩排行榜">
            ${entries.map((entry, index) => `
              <div class="leaderboard-row">
                <span class="leaderboard-rank">${index + 1}</span>
                <span class="leaderboard-name">${escapeHtml(entry.name)}</span>
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
  const sentence = escapeHtml(question.sentence || "");
  const target = escapeHtml(question.word || "");
  return target ? sentence.replaceAll(target, `<mark class="target-word">${target}</mark>`) : sentence;
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
  if (option.key === getCurrentQuestion().answer) return "correct";
  if (option.key === state.selectedKey) return "wrong";
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

const renderFeedback = (question) => {
  if (!state.answeredCurrent) return "";
  const config = { ...FALLBACK_CONFIG, ...(bank.quizDefaults || {}) };
  const isCorrect = state.selectedKey === question.answer;
  const selectedOption = question.options.find((option) => option.key === state.selectedKey);
  const answerOption = question.options.find((option) => option.key === question.answer);
  return `
    <div class="feedback-panel ${isCorrect ? "success" : "error"}" role="status">
      <div class="feedback-title">${isCorrect ? `回答正确！ +${config.correctScore} 分` : `回答错误！ ${config.wrongScore} 分`}</div>
      ${!isCorrect ? `<p class="feedback-answer">你的选择：${escapeHtml(selectedOption?.key || "未选择")}　正确答案：${escapeHtml(answerOption?.key || question.answer)}</p>` : ""}
      <p>${escapeHtml(question.explanation || "本题暂无补充解析。")}</p>
      <button class="primary-button" type="button" data-action="next">${state.currentIndex + 1 >= state.questions.length ? "查看成绩" : "下一题"}</button>
    </div>
  `;
};

const renderQuiz = () => {
  const question = getCurrentQuestion();
  const timerClass = state.remainingSeconds <= 10 ? "danger" : state.remainingSeconds <= 30 ? "warning" : "";
  const isContextMeaning = question.type === "context_meaning";
  const questionTitle = isContextMeaning
    ? renderSentence(question)
    : escapeHtml(question.stem || question.sentence || "请选择答案");
  const questionPrompt = isContextMeaning
    ? `句中“${escapeHtml(question.word || "")}”的意思是：`
    : escapeHtml(question.stem ? "请选择最符合题意的一项：" : "请选择答案：");

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
};

const renderResult = () => {
  const duration = { ...FALLBACK_CONFIG, ...(bank.quizDefaults || {}) }.durationSeconds;
  const usedSeconds = Math.min(duration, Math.max(0, Math.floor((Date.now() - state.startedAt) / 1000)));
  const total = state.answered;
  const accuracy = total === 0 ? 0 : state.correct / total;
  const resultLabel = state.completedAll
    ? "所有题目已答完"
    : state.finishReason === "manual"
      ? "已提前交卷"
      : "本次答题结束";
  const resultMeta = state.completedAll
    ? "本局已完成"
    : state.finishReason === "manual"
      ? "你选择了提前交卷"
      : "时间到，答题结束";
  app.innerHTML = `
    <section class="state-screen result-screen" aria-labelledby="result-title">
      <div class="result-card">
        <p class="eyebrow">${resultLabel}</p>
        <h1 id="result-title">答得怎么样？</h1>
        <div class="result-score">${state.score}<small>分</small></div>
        <div class="result-stats">
          <div class="result-stat"><strong>${state.correct}</strong><span>答对</span></div>
          <div class="result-stat"><strong>${state.wrong}</strong><span>答错</span></div>
          <div class="result-stat"><strong>${formatPercent(accuracy)}</strong><span>正确率</span></div>
        </div>
        <p class="result-meta">${resultMeta} · 用时 ${formatSeconds(usedSeconds)}</p>
        ${state.scoreSaved ? `
          <p class="saved-note">已将本次成绩计入排行榜。</p>
        ` : `
          <form class="score-form" data-action="score-form">
            <label for="player-name">写下你的名字，加入排行榜</label>
            <div class="score-form-row">
              <input id="player-name" name="name" type="text" maxlength="20" placeholder="请输入名字" autocomplete="off" required />
              <button class="primary-button" type="submit">加入排行</button>
            </div>
          </form>
        `}
        <div class="button-stack">
          <button class="${state.scoreSaved ? "primary-button" : "secondary-button"}" type="button" data-action="leaderboard">查看排行榜</button>
          <button class="secondary-button" type="button" data-action="restart">再来一局</button>
          <button class="secondary-button" type="button" data-action="home">返回首页</button>
        </div>
      </div>
    </section>
  `;
  app.querySelector('[data-action="restart"]').addEventListener("click", startGame);
  app.querySelector('[data-action="leaderboard"]').addEventListener("click", renderLeaderboard);
  app.querySelector('[data-action="home"]').addEventListener("click", renderStart);
  const scoreForm = app.querySelector('[data-action="score-form"]');
  if (scoreForm) {
    scoreForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const name = new FormData(scoreForm).get("name").toString().trim();
      if (!name) return;
      const submitButton = scoreForm.querySelector('button[type="submit"]');
      submitButton.disabled = true;
      submitButton.textContent = "正在保存…";
      try {
        await saveLeaderboardEntry(name, state.score);
        state.scoreSaved = true;
        renderResult();
      } catch (error) {
        submitButton.disabled = false;
        submitButton.textContent = "加入排行";
        window.alert(error instanceof Error ? error.message : "成绩保存失败。");
      }
    });
  }
};

const startTimer = () => {
  window.clearInterval(timerId);
  timerId = window.setInterval(() => {
    const duration = { ...FALLBACK_CONFIG, ...(bank.quizDefaults || {}) }.durationSeconds;
    const elapsed = Math.floor((Date.now() - state.startedAt) / 1000);
    state.remainingSeconds = Math.max(0, duration - elapsed);
    if (state.remainingSeconds === 0) {
      const completedAll = state.answeredCurrent && state.currentIndex + 1 >= state.questions.length;
      finishGame(completedAll ? "completed" : "timeout");
      return;
    }
    renderQuiz();
  }, 1000);
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

const startGame = () => {
  const config = { ...FALLBACK_CONFIG, ...(bank.quizDefaults || {}) };
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
    selectedKey: null,
    answeredCurrent: false,
    completedAll: false,
    finishReason: "in_progress",
    scoreSaved: false,
    startedAt: Date.now(),
  };
  renderQuiz();
  startTimer();
};

const submitAnswer = (key) => {
  if (!state || state.answeredCurrent || state.remainingSeconds <= 0) return;
  const question = getCurrentQuestion();
  const config = { ...FALLBACK_CONFIG, ...(bank.quizDefaults || {}) };
  state.selectedKey = key;
  state.answeredCurrent = true;
  state.answered += 1;
  if (key === question.answer) {
    state.score += Number(config.correctScore);
    state.correct += 1;
  } else {
    state.score += Number(config.wrongScore);
    state.wrong += 1;
  }
  renderQuiz();
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
};

const finishEarly = () => {
  if (!state || state.screen !== "quiz") return;
  const confirmed = window.confirm("确定要提前交卷吗？当前成绩将作为本局成绩保存。");
  if (!confirmed) return;
  const completedAll = state.answeredCurrent && state.currentIndex + 1 >= state.questions.length;
  finishGame(completedAll ? "completed" : "manual");
};

const finishGame = (reason = "timeout") => {
  if (!state || state.screen === "result") return;
  window.clearInterval(timerId);
  state.completedAll = reason === "completed";
  state.finishReason = reason;
  state.screen = "result";
  renderResult();
};

const loadBank = async () => {
  try {
    const [response] = await Promise.all([
      fetch("./api/questions", { cache: "no-store" }),
      loadLeaderboard(),
    ]);
    if (!response.ok) throw new Error(`题库文件读取失败（${response.status}）。`);
    bank = validateBank(await response.json());
    renderStart();
  } catch (error) {
    renderError(error instanceof Error ? error.message : "题库文件读取失败。");
  }
};

loadBank();
