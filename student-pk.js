// student-pk.js: two-player PK setup, gameplay, records and result flow
// Functions intentionally use the classic-script global scope for compatibility.

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
    '<button class="pk-mode-choice ', pkSetup.mode === "time" ? "active" : "", '" type="button" data-pk-mode="time"><strong>比时间</strong><span>规定时间内连续作答，时间到比较总分；同分即平局。</span></button>',
    '<button class="pk-mode-choice ', pkSetup.mode === "questions" ? "active" : "", '" type="button" data-pk-mode="questions"><strong>比题数</strong><span>双方都答完规定题数后比较总分；同分再比总用时，用时差 ≤ 0.5 秒为平局，否则更快者胜。</span></button>',
    '</div>',
    '<div class="pk-setting-block ', pkSetup.mode === "time" ? "" : "hidden", '" data-pk-settings="time"><span class="pk-setting-label">比赛时间</span><div class="pk-choice-row">',
    PK_TIME_OPTIONS.map((seconds) => '<button class="pk-setting-choice ' + (pkSetup.timeLimitSeconds === seconds ? "active" : "") + '" type="button" data-pk-time="' + seconds + '">' + seconds + ' 秒</button>').join(""),
    '</div></div>',
    '<div class="pk-setting-block ', pkSetup.mode === "questions" ? "" : "hidden", '" data-pk-settings="questions"><span class="pk-setting-label">比赛题数</span><div class="pk-choice-row">',
    PK_QUESTION_OPTIONS.map((count) => '<button class="pk-setting-choice ' + (pkSetup.questionLimit === count ? "active" : "") + '" type="button" data-pk-count="' + count + '" ' + (count > availableCount ? "disabled" : "") + '>' + count + ' 题</button>').join(""),
    '</div><small class="pk-setting-help">题数模式需要至少有足够的可用题目；题目不足的选项已禁用。</small></div>',
    '<p class="pk-setup-rule">计分方式沿用当前课堂规则；PK 分数不会写入普通排行榜。</p>',
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
