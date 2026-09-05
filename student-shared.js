// student-shared.js: student-facing formatting, font scale and shuffle helpers
// Functions intentionally use the classic-script global scope for compatibility.

const FONT_SCALE_STORAGE_KEY = "wenyan-quiz-font-scale";
const FONT_SCALE_MIN = 1;
const FONT_SCALE_MAX = 2;
const FONT_SCALE_STEP = 0.1;
const DEFAULT_FONT_SCALE = 1;

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

const formatPkDuration = (milliseconds) => {
  const safeMilliseconds = Math.max(0, Number(milliseconds) || 0);
  return `${(safeMilliseconds / 1000).toFixed(2)} 秒`;
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
  else if (pkMatch?.phase === "playing") renderPkShell();
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
