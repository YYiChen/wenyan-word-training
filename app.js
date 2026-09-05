const app = document.querySelector("#app");
const {
  calculateScoreEvent,
  formatScoreDelta,
  normalizeScoringConfig,
} = window.WenyanScoring;

const FALLBACK_CONFIG = {
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
};
const MIN_DURATION_SECONDS = 10;
const MAX_DURATION_SECONDS = 3600;
const QUIZ_SESSION_STORAGE_KEY = "wenyan-quiz-active-session";
let bank = null;
let timerId = null;
let state = null;
let startSelection = { volumes: ["all"], articleIds: ["all"] };
let leaderboard = [];
let answerRecords = [];
let answerRecordsWarning = "";
let feedbackEffectsController = null;
let feedbackEffectPlayedKey = "";
let gameStarting = false;
let feedbackTransitionTimerId = null;
let feedbackTransitionSequence = 0;
let pkMatch = null;
let pkTimerId = null;
let pkCountdownTimerId = null;
let pkEffectControllers = { player1: null, player2: null };
let pkRecordsViewMode = "solo";

window.addEventListener("beforeunload", (event) => {
  if (state?.screen !== "quiz" && !["countdown", "playing"].includes(pkMatch?.phase)) return;
  event.preventDefault();
  event.returnValue = "";
});

const loadBank = async () => {
  try {
    const response = await fetch("./api/questions", { cache: "no-store" });
    if (!response.ok) throw new Error(`题库文件读取失败（${response.status}）。`);
    bank = validateBank(await response.json());
    renderStart();
    const recovery = readQuizRecovery();
    if (recovery && window.confirm("检测到一局尚未结束的答题，是否恢复？")) {
      state = recovery;
      renderQuiz();
      startTimer();
      if (state.answeredCurrent && currentFeedbackPhase(state) === "correct-feedback") {
        scheduleFeedbackTransition(
          state,
          state.currentIndex,
          state.selectedKey,
          true,
          state.answerDetails[state.answerDetails.length - 1],
        );
      }
    } else {
      clearQuizRecovery();
    }
  } catch (error) {
    renderError(error instanceof Error ? error.message : "题库文件读取失败。");
  }
};

loadBank();
