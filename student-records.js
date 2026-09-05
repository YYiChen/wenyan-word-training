// student-records.js: student leaderboard and answer-record loading, saving and rendering
// Functions intentionally use the classic-script global scope for compatibility.

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

