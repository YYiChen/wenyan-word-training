// tools/test_review_conflict_queue.js: unit tests for the review-conflict
// queue helpers in admin-questions.js.  The script is loaded with node:vm;
// the helpers under test touch no DOM, so no browser stubs are needed.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "..", "admin-questions.js"), "utf-8");
const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(`${source}\nthis.__exposed = { REVIEW_RESOLUTION_CHOICES, REVIEW_RESOLUTION_LABELS, getReviewConflictsFromPreview, buildReviewConflictResolutions, countUnresolvedReviewConflicts, resetReviewConflictQueue };`, sandbox);
const api = sandbox.__exposed;

const conflicts = [
  { kind: "review", conflictId: "review-conflict-aaa", questionId: "qa" },
  { kind: "review", conflictId: "review-conflict-bbb", questionId: "qb" },
  { kind: "question", questionId: "qc", classification: "modified" },
  { kind: "review", questionId: "qd" },
];

// conflict rendering source: only kind=review entries with a conflictId.
// (Compared by value: arrays cross the vm realm boundary.)
const renderedIds = api.getReviewConflictsFromPreview({ conflicts }).map((item) => item.conflictId);
assert.equal(renderedIds.length, 2);
assert.equal(renderedIds.join(","), "review-conflict-aaa,review-conflict-bbb");
assert.equal(api.getReviewConflictsFromPreview({ conflicts: [] }).length, 0);
assert.equal(api.getReviewConflictsFromPreview(null).length, 0);

// all local / all incoming / all skip batch selection.
for (const choice of ["local", "incoming", "skip"]) {
  const built = api.buildReviewConflictResolutions(conflicts.slice(0, 2), choice);
  assert.equal(Object.keys(built).length, 2);
  assert.equal(built["review-conflict-aaa"], choice);
  assert.equal(built["review-conflict-bbb"], choice);
}
assert.equal(Object.keys(api.buildReviewConflictResolutions(conflicts.slice(0, 2), "sideways")).length, 0);

// per-item selection and unresolved counter.
const queue = api.getReviewConflictsFromPreview({ conflicts });
assert.equal(api.countUnresolvedReviewConflicts(queue, {}), 2);
assert.equal(api.countUnresolvedReviewConflicts(queue, { "review-conflict-aaa": "local" }), 1);
assert.equal(
  api.countUnresolvedReviewConflicts(queue, { "review-conflict-aaa": "local", "review-conflict-bbb": "skip" }),
  0,
);
assert.equal(api.countUnresolvedReviewConflicts(queue, { "review-conflict-aaa": "bogus" }), 2);

// Labels distinguish "skip for now" from the skipped review status.
assert.equal(api.REVIEW_RESOLUTION_LABELS.skip, "本次暂不处理");
assert.ok(api.REVIEW_RESOLUTION_LABELS.skip.indexOf("跳过") === -1);

console.log("review conflict queue tests passed");
