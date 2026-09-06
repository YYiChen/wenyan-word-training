// admin-auth.js: launcher ticket exchange and optional source-dev login
// Classic script module; the formal browser flow never receives an admin password.

const hasAdminAccess = () => adminAuthorized;

const renderLockedScreen = (message = "请在“文言实词限时训练”程序窗口中点击“打开管理后台”并完成管理员验证。") => {
  adminApp.innerHTML = `
    <section class="admin-loading" aria-labelledby="admin-lock-title">
      <div class="admin-login-card admin-locked-card">
        <p class="eyebrow">文言实词 · 管理后台</p>
        <h1 id="admin-lock-title">管理后台已锁定</h1>
        <p class="admin-subtitle">${escapeHtml(message)}</p>
        <a class="admin-home-link" href="./index.html">返回学生答题页</a>
      </div>
    </section>
  `;
};

const renderBrowserDevLogin = () => {
  adminApp.innerHTML = `
    <section class="admin-loading" aria-labelledby="admin-login-title">
      <div class="admin-login-card">
        <p class="eyebrow">文言实词 · 开发调试入口</p>
        <h1 id="admin-login-title">请输入管理密码</h1>
        <p class="admin-subtitle">当前服务显式开启了源码开发登录；正式 Windows 版本不会显示此入口。</p>
        <form class="admin-login-form" id="admin-login-form" autocomplete="off">
          <label class="editor-field" for="admin-dev-password">管理密码
            <input class="admin-input" id="admin-dev-password" type="password" autocomplete="off" readonly required spellcheck="false" autocapitalize="off" />
          </label>
          ${loginError ? `<p class="admin-login-error" role="alert">${escapeHtml(loginError)}</p>` : ""}
          <button class="admin-primary" type="submit">进入管理后台</button>
        </form>
        <a class="admin-home-link" href="./index.html">返回学生答题页</a>
      </div>
    </section>
  `;
  const loginForm = adminApp.querySelector("#admin-login-form");
  const passwordInput = loginForm.querySelector("#admin-dev-password");
  const activatePasswordInput = () => {
    passwordInput.readOnly = false;
  };
  ["pointerdown", "focus", "keydown"].forEach((eventName) => {
    passwordInput.addEventListener(eventName, activatePasswordInput);
  });
  loginForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const password = passwordInput.value;
    passwordInput.value = "";
    passwordInput.readOnly = true;
    const submitButton = event.currentTarget.querySelector('button[type="submit"]');
    if (submitButton) {
      submitButton.disabled = true;
      submitButton.textContent = "正在验证…";
    }
    try {
      adminToken = await authenticateBrowserDevAdmin(password);
    } catch (error) {
      adminToken = null;
      loginError = "密码不正确，请重新输入。";
      renderBrowserDevLogin();
      return;
    }
    adminAuthorized = true;
    loginError = "";
    load();
  });
};

const clearLaunchFragment = () => {
  const hash = window.location.hash || "";
  if (!hash.startsWith("#launch=")) return "";
  let ticket = "";
  try {
    ticket = decodeURIComponent(hash.slice("#launch=".length));
  } catch {
    ticket = "";
  }
  history.replaceState(null, "", `${window.location.pathname}${window.location.search}`);
  return ticket;
};

const readAuthCapabilities = async () => {
  const response = await fetch("./api/health", { cache: "no-store" });
  const payload = await response.json().catch(() => null);
  if (!response.ok || !payload?.ok) throw new Error("本地服务暂时不可用。");
  return payload;
};

const exchangeLaunchTicket = async (ticket) => {
  const response = await fetch(API.adminLaunchSession, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ticket }),
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok || !payload?.ok || !payload?.data?.token) {
    throw new Error(payload?.error || "管理员启动授权已失效。");
  }
  return payload.data.token;
};

const authenticateBrowserDevAdmin = async (password) => {
  const data = await postJson(API.adminAuth, { password });
  if (!data?.token) throw new Error("服务器没有返回有效的管理员会话。");
  return data.token;
};

const renderLogin = () => {
  const ticket = clearLaunchFragment();
  if (ticket) {
    renderLockedScreen("正在验证 Windows 启动窗口的管理员授权，请稍候。");
    exchangeLaunchTicket(ticket)
      .then((token) => {
        adminToken = token;
        adminAuthorized = true;
        loginError = "";
        load();
      })
      .catch((error) => {
        adminToken = null;
        adminAuthorized = false;
        renderLockedScreen(error instanceof Error ? error.message : "管理员启动授权已失效，请从 Windows 启动窗口重新进入。");
      });
    return;
  }

  renderLockedScreen();
  readAuthCapabilities()
    .then((capabilities) => {
      if (capabilities.browserAdminLoginAllowed === true) {
        renderBrowserDevLogin();
      }
    })
    .catch(() => {
      // Keep the locked screen. It is the safe default when capability discovery fails.
    });
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
