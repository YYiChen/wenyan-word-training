(() => {
  "use strict";

  const app = document.querySelector("#app");
  if (!app) return;

  const RESULT_SELECTOR = ".pk-result-screen.is-animating";
  const SHELL_SELECTOR = ".pk-shell";
  const OVERLAY_ID = "pk-finish-transition-layer";
  const FULL_DURATION_MS = 4200;
  const REDUCED_DURATION_MS = 180;

  let latestShellSnapshot = null;
  let activeTransitionKey = "";
  let transitionTimerId = null;
  let settleTimerId = null;

  const prefersReducedMotion = () => (
    window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true
  );

  const clearTransitionTimers = () => {
    if (transitionTimerId !== null) {
      window.clearTimeout(transitionTimerId);
      transitionTimerId = null;
    }
    if (settleTimerId !== null) {
      window.clearTimeout(settleTimerId);
      settleTimerId = null;
    }
  };

  const removeExistingOverlay = () => {
    clearTransitionTimers();
    document.getElementById(OVERLAY_ID)?.remove();
    document.body.classList.remove("pk-finish-transition-active");
  };

  const sanitizeSnapshot = (snapshot) => {
    snapshot.removeAttribute("aria-labelledby");
    snapshot.setAttribute("aria-hidden", "true");
    snapshot.classList.add("pk-finish-snapshot");

    snapshot.querySelectorAll("button, input, select, textarea, a").forEach((element) => {
      element.setAttribute("tabindex", "-1");
      element.setAttribute("aria-hidden", "true");
      if ("disabled" in element) element.disabled = true;
    });

    // Canvas pixels are not copied by cloneNode. Removing them avoids a blank
    // rectangle sitting above the frozen answer state during the finale.
    snapshot.querySelectorAll("canvas").forEach((canvas) => canvas.remove());
    return snapshot;
  };

  const captureShell = () => {
    const shell = app.querySelector(SHELL_SELECTOR);
    if (!shell) return;
    latestShellSnapshot = sanitizeSnapshot(shell.cloneNode(true));
  };

  const captureShellSoon = () => {
    // app.js handles the answer synchronously on pointerdown and redraws that
    // player's half immediately. Capture on the next task so the frozen frame
    // includes the final score and red/green answer feedback.
    window.setTimeout(captureShell, 0);
    // In timed PK, the automatic advance happens a few hundred milliseconds
    // later. Capture once more after the feedback window so a timeout finale
    // freezes the actually visible current question rather than an older card.
    window.setTimeout(captureShell, 700);
  };

  const readOutcome = (resultScreen) => {
    const winner = resultScreen.classList.contains("winner-player1")
      ? "player1"
      : resultScreen.classList.contains("winner-player2")
        ? "player2"
        : "draw";
    const resultPlayers = [...resultScreen.querySelectorAll(".pk-result-player")];
    const scores = resultPlayers.map((player) => (
      player.querySelector("strong")?.textContent?.trim() || "0"
    ));
    const title = resultScreen.querySelector("#pk-result-title")?.textContent?.trim()
      || (winner === "draw" ? "平局" : winner === "player1" ? "玩家 1 获胜" : "玩家 2 获胜");
    const mode = resultScreen.querySelector(".pk-result-mode")?.textContent?.trim() || "双人 PK";
    return {
      winner,
      title,
      mode,
      score1: scores[0] || "0",
      score2: scores[1] || "0",
    };
  };

  const makeTransitionKey = (resultScreen, outcome) => [
    outcome.winner,
    outcome.score1,
    outcome.score2,
    resultScreen.querySelector(".pk-result-meta")?.textContent?.trim() || "",
  ].join("|");

  const makeConfetti = (container, count, draw = false) => {
    const fragment = document.createDocumentFragment();
    for (let index = 0; index < count; index += 1) {
      const piece = document.createElement("i");
      piece.className = "pk-finale-confetti-piece";
      const angle = (360 / count) * index + ((index * 17) % 23) - 11;
      const distance = 180 + (index % 7) * 34;
      const delay = (index % 9) * 18;
      const width = 6 + (index % 4) * 2;
      const height = 14 + (index % 5) * 4;
      const spin = 240 + (index % 6) * 105;
      const drift = ((index % 5) - 2) * 18;
      piece.style.setProperty("--pk-angle", `${angle}deg`);
      piece.style.setProperty("--pk-distance", `${distance}px`);
      piece.style.setProperty("--pk-delay", `${delay}ms`);
      piece.style.setProperty("--pk-piece-w", `${width}px`);
      piece.style.setProperty("--pk-piece-h", `${height}px`);
      piece.style.setProperty("--pk-spin", `${spin}deg`);
      piece.style.setProperty("--pk-drift", `${drift}px`);
      if (draw) piece.classList.add(index % 2 ? "is-draw-left" : "is-draw-right");
      fragment.append(piece);
    }
    container.append(fragment);
  };

  const makeSparkles = (container, count) => {
    const fragment = document.createDocumentFragment();
    for (let index = 0; index < count; index += 1) {
      const sparkle = document.createElement("b");
      sparkle.className = "pk-finale-sparkle";
      sparkle.style.setProperty("--pk-spark-angle", `${index * (360 / count)}deg`);
      sparkle.style.setProperty("--pk-spark-delay", `${(index % 6) * 55}ms`);
      sparkle.style.setProperty("--pk-spark-distance", `${115 + (index % 4) * 34}px`);
      fragment.append(sparkle);
    }
    container.append(fragment);
  };

  const createAnnouncement = (outcome) => {
    const announcement = document.createElement("div");
    announcement.className = "pk-finale-announcement";
    announcement.setAttribute("aria-hidden", "true");

    const kicker = outcome.winner === "draw"
      ? "MATCH COMPLETE"
      : outcome.winner === "player1"
        ? "PLAYER 1 · WINNER"
        : "PLAYER 2 · WINNER";
    const hero = outcome.winner === "draw"
      ? "平 局"
      : outcome.winner === "player1"
        ? "玩家 1 胜利"
        : "玩家 2 胜利";

    announcement.innerHTML = [
      '<div class="pk-finale-crown" aria-hidden="true">', outcome.winner === "draw" ? "✦" : "♛", '</div>',
      '<span class="pk-finale-kicker">', kicker, '</span>',
      '<strong class="pk-finale-title">', hero, '</strong>',
      '<div class="pk-finale-score"><span>', outcome.score1, '</span><em>:</em><span>', outcome.score2, '</span></div>',
      '<small class="pk-finale-mode">', outcome.mode, '</small>',
    ].join("");
    return announcement;
  };

  const markSnapshotOutcome = (snapshot, outcome) => {
    const player1 = snapshot.querySelector('[data-pk-player-panel="player1"]');
    const player2 = snapshot.querySelector('[data-pk-player-panel="player2"]');
    const score1 = snapshot.querySelector('[data-pk-score="player1"]');
    const score2 = snapshot.querySelector('[data-pk-score="player2"]');
    const side1 = score1?.closest(".pk-score-side");
    const side2 = score2?.closest(".pk-score-side");
    const clock = snapshot.querySelector(".pk-clock");
    const exit = snapshot.querySelector(".pk-exit-button");
    const footer = snapshot.querySelector(".pk-footer");

    if (score1) score1.textContent = outcome.score1;
    if (score2) score2.textContent = outcome.score2;
    if (clock) {
      const clockLabel = clock.querySelector("span");
      const clockValue = clock.querySelector("strong");
      const clockHelp = clock.querySelector("small");
      if (clockLabel) clockLabel.textContent = "比赛结束";
      if (clockValue) clockValue.textContent = "FINISH";
      if (clockHelp) clockHelp.textContent = "最终比分已锁定";
    }
    exit?.classList.add("pk-finale-hide-control");
    footer?.classList.add("pk-finale-hide-control");

    [player1, player2, side1, side2].forEach((element) => {
      element?.classList.remove("pk-finale-winner", "pk-finale-loser", "pk-finale-draw");
    });

    if (outcome.winner === "draw") {
      player1?.classList.add("pk-finale-draw");
      player2?.classList.add("pk-finale-draw");
      side1?.classList.add("pk-finale-draw");
      side2?.classList.add("pk-finale-draw");
    } else {
      const winnerPanel = outcome.winner === "player1" ? player1 : player2;
      const loserPanel = outcome.winner === "player1" ? player2 : player1;
      const winnerSide = outcome.winner === "player1" ? side1 : side2;
      const loserSide = outcome.winner === "player1" ? side2 : side1;
      winnerPanel?.classList.add("pk-finale-winner");
      loserPanel?.classList.add("pk-finale-loser");
      winnerSide?.classList.add("pk-finale-winner");
      loserSide?.classList.add("pk-finale-loser");
    }
  };

  const startFinishTransition = (resultScreen) => {
    if (!latestShellSnapshot || document.getElementById(OVERLAY_ID)) return;

    const outcome = readOutcome(resultScreen);
    const transitionKey = makeTransitionKey(resultScreen, outcome);
    if (transitionKey === activeTransitionKey) return;
    activeTransitionKey = transitionKey;

    removeExistingOverlay();

    const overlay = document.createElement("div");
    overlay.id = OVERLAY_ID;
    overlay.className = [
      "pk-finish-transition-layer",
      outcome.winner === "player1" ? "pk-finish-player1" : outcome.winner === "player2" ? "pk-finish-player2" : "pk-finish-draw",
    ].join(" ");
    overlay.setAttribute("aria-hidden", "true");

    const frozenShell = latestShellSnapshot.cloneNode(true);
    markSnapshotOutcome(frozenShell, outcome);

    const atmosphere = document.createElement("div");
    atmosphere.className = "pk-finale-atmosphere";
    const burst = document.createElement("div");
    burst.className = "pk-finale-burst";
    makeConfetti(burst, outcome.winner === "draw" ? 40 : 52, outcome.winner === "draw");
    makeSparkles(burst, 16);

    const impact = document.createElement("div");
    impact.className = "pk-finale-impact";
    const announcement = createAnnouncement(outcome);

    overlay.append(frozenShell, atmosphere, impact, burst, announcement);
    document.body.append(overlay);
    document.body.classList.add("pk-finish-transition-active");

    // Two frames make the browser commit the frozen classroom screen before
    // applying the large grid/scale transformations. That creates a visible
    // morph instead of an instant final-state paint.
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => overlay.classList.add("is-live"));
    });

    const duration = prefersReducedMotion() ? REDUCED_DURATION_MS : FULL_DURATION_MS;
    const settleDelay = prefersReducedMotion() ? 80 : 3550;
    settleTimerId = window.setTimeout(() => {
      settleTimerId = null;
      overlay.classList.add("is-settling");
    }, settleDelay);

    transitionTimerId = window.setTimeout(() => {
      transitionTimerId = null;
      overlay.remove();
      document.body.classList.remove("pk-finish-transition-active");
      // Keep the key until another real PK shell appears. This prevents the
      // result page's save-status rerenders from replaying the finale.
    }, duration);
  };

  const observer = new MutationObserver(() => {
    const shell = app.querySelector(SHELL_SELECTOR);
    if (shell) {
      // A newly rendered PK shell means a new match/round is active. Allow a
      // future result to play even if the eventual scores match a prior game.
      activeTransitionKey = "";
      captureShell();
      return;
    }

    const result = app.querySelector(RESULT_SELECTOR);
    if (result) startFinishTransition(result);
  });

  observer.observe(app, { childList: true });

  // Keep a current frozen frame after each actual player touch without cloning
  // the full shell on the 100 ms scoreboard timer.
  document.addEventListener("pointerdown", (event) => {
    const option = event.target.closest?.(".pk-shell [data-pk-option]");
    if (option) captureShellSoon();
  });

  document.addEventListener("click", (event) => {
    const control = event.target.closest?.(".pk-shell [data-action^='font-']");
    if (control) captureShellSoon();
  });

  window.addEventListener("pagehide", removeExistingOverlay);
})();
