const assert = require("node:assert/strict");
const {
  calculateScoreEvent,
  normalizeScoringConfig,
} = require("../scoring.js");

const fixed = normalizeScoringConfig({ correctScore: 1, wrongScore: -1 });
assert.deepEqual(
  [
    calculateScoreEvent(fixed, true),
    calculateScoreEvent(fixed, false),
  ].map((event) => event.scoreDelta),
  [1, -1],
);

const streak = normalizeScoringConfig({
  scoring: {
    mode: "streak",
    baseCorrect: 1,
    baseWrongPenalty: 1,
    correctStreakAfter: 2,
    correctStreakScore: 2,
    wrongStreakAfter: 2,
    wrongStreakPenalty: 2,
  },
});
assert.deepEqual(normalizeScoringConfig(streak), streak);

let streaks = { correctStreak: 0, wrongStreak: 0 };
const expected = [1, 1, 2, -1, -1, -2];
const results = [true, true, true, false, false, false].map((isCorrect) => {
  const event = calculateScoreEvent(streak, isCorrect, streaks);
  streaks = {
    correctStreak: event.correctStreak,
    wrongStreak: event.wrongStreak,
  };
  return event;
});
assert.deepEqual(results.map((event) => event.scoreDelta), expected);
assert.deepEqual(results.map((event) => event.tier), ["base", "base", "streak", "base", "base", "streak"]);
assert.equal(results[2].correctStreak, 3);
assert.equal(results[3].correctStreak, 0);
assert.equal(results[5].wrongStreak, 3);

const resetEvent = calculateScoreEvent(streak, true, { correctStreak: 2, wrongStreak: 0 });
assert.equal(resetEvent.scoreDelta, 2);
const afterWrong = calculateScoreEvent(streak, false, {
  correctStreak: resetEvent.correctStreak,
  wrongStreak: resetEvent.wrongStreak,
});
assert.equal(afterWrong.scoreDelta, -1);
assert.equal(afterWrong.correctStreak, 0);

console.log("scoring tests passed");
