(function attachScoringApi(root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.WenyanScoring = api;
}(typeof globalThis === "object" ? globalThis : this, () => {
  const DEFAULT_SCORING_CONFIG = Object.freeze({
    mode: "fixed",
    baseCorrect: 1,
    baseWrongPenalty: 1,
    correctStreakAfter: 2,
    correctStreakScore: 2,
    wrongStreakAfter: 2,
    wrongStreakPenalty: 2,
  });
  const MAX_STREAK_THRESHOLD = 5;

  const asNonNegativeInteger = (value, fallback) => {
    const number = Number(value);
    return Number.isInteger(number) && number >= 0 ? number : fallback;
  };

  const asPositiveInteger = (value, fallback, maximum = Number.POSITIVE_INFINITY) => {
    const number = Number(value);
    return Number.isInteger(number) && number >= 1 && number <= maximum ? number : fallback;
  };

  const normalizeScoringConfig = (quizDefaults = {}) => {
    const defaults = quizDefaults && typeof quizDefaults === "object" ? quizDefaults : {};
    const raw = defaults.scoring && typeof defaults.scoring === "object"
      ? defaults.scoring
      : (Object.prototype.hasOwnProperty.call(defaults, "mode") ? defaults : {});
    const legacyWrongPenalty = Math.abs(Number(defaults.wrongScore));
    return {
      mode: raw.mode === "streak" ? "streak" : "fixed",
      baseCorrect: asNonNegativeInteger(raw.baseCorrect ?? defaults.correctScore, DEFAULT_SCORING_CONFIG.baseCorrect),
      baseWrongPenalty: asNonNegativeInteger(
        raw.baseWrongPenalty ?? (Number.isFinite(legacyWrongPenalty) ? legacyWrongPenalty : null),
        DEFAULT_SCORING_CONFIG.baseWrongPenalty,
      ),
      correctStreakAfter: asPositiveInteger(raw.correctStreakAfter, DEFAULT_SCORING_CONFIG.correctStreakAfter, MAX_STREAK_THRESHOLD),
      correctStreakScore: asNonNegativeInteger(raw.correctStreakScore, DEFAULT_SCORING_CONFIG.correctStreakScore),
      wrongStreakAfter: asPositiveInteger(raw.wrongStreakAfter, DEFAULT_SCORING_CONFIG.wrongStreakAfter, MAX_STREAK_THRESHOLD),
      wrongStreakPenalty: asNonNegativeInteger(raw.wrongStreakPenalty, DEFAULT_SCORING_CONFIG.wrongStreakPenalty),
    };
  };

  const serializeScoringConfig = (config) => {
    const normalized = normalizeScoringConfig({ scoring: config });
    return { ...normalized };
  };

  const formatScoreDelta = (delta) => {
    const number = Number(delta) || 0;
    return `${number >= 0 ? "+" : ""}${number}`;
  };

  const calculateScoreEvent = (quizDefaultsOrConfig, isCorrect, previousStreaks = {}) => {
    const config = quizDefaultsOrConfig?.scoring
      ? normalizeScoringConfig(quizDefaultsOrConfig)
      : normalizeScoringConfig({ scoring: quizDefaultsOrConfig });
    const previousCorrect = asNonNegativeInteger(previousStreaks.correctStreak, 0);
    const previousWrong = asNonNegativeInteger(previousStreaks.wrongStreak, 0);
    const correctStreak = isCorrect ? previousCorrect + 1 : 0;
    const wrongStreak = isCorrect ? 0 : previousWrong + 1;
    const isSuper = config.mode === "streak" && (
      isCorrect
        ? correctStreak > config.correctStreakAfter
        : wrongStreak > config.wrongStreakAfter
    );
    const delta = isCorrect
      ? (isSuper ? config.correctStreakScore : config.baseCorrect)
      : -(isSuper ? config.wrongStreakPenalty : config.baseWrongPenalty);
    const kind = isCorrect ? "correct" : "wrong";
    const tier = isSuper ? "streak" : "base";
    return {
      mode: config.mode,
      kind,
      tier,
      scoreDelta: delta,
      correctStreak,
      wrongStreak,
      isSuper,
      label: isCorrect
        ? (isSuper ? "连击加分" : "基础加分")
        : (isSuper ? "连续错误扣分" : "基础扣分"),
      scoreText: formatScoreDelta(delta),
    };
  };

  return {
    DEFAULT_SCORING_CONFIG,
    MAX_STREAK_THRESHOLD,
    normalizeScoringConfig,
    serializeScoringConfig,
    calculateScoreEvent,
    formatScoreDelta,
  };
}));
