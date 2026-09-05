// admin-settings.js: settings, scoring and administrator password workflows
// Classic script module; shared state and API contracts remain in admin.js.

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
