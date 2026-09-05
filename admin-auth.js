// admin-auth.js: administrator login and authentication lifecycle helpers
// Classic script module; shared state and API contracts remain in admin.js.

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

const authenticateAdmin = async (password) => {
  const data = await postJson(API.adminAuth, { password });
  if (!data?.token) throw new Error("服务器没有返回有效的管理员会话。" );
  return data.token;
};


const wireAdminAuthEvents = () => {
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
};
