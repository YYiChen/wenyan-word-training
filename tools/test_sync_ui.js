// tools/test_sync_ui.js: unit tests for the pure sync UI helpers in
// admin-sync.js.  The script is loaded with node:vm; the helpers under test
// touch no DOM, so no browser stubs are needed.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "..", "admin-sync.js"), "utf-8");
const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(
  `${source}\nthis.__exposed = { SYNC_PHASE_LABELS, syncStatusBadgeText, adaptSyncConflict, countUnresolvedSync, buildSyncChoices };`,
  sandbox,
);
const api = sandbox.__exposed;

// Badge text per §31/§87 (value-compared: arrays cross the vm realm boundary).
assert.equal(api.syncStatusBadgeText({ phase: "disabled" }), "同步：已关闭");
assert.equal(api.syncStatusBadgeText({ phase: "connected" }), "同步：已连接");
assert.equal(api.syncStatusBadgeText({ phase: "offline", pendingLocal: 5 }), "同步：离线 · 5 项待同步");
assert.equal(api.syncStatusBadgeText({ phase: "offline", pendingLocal: 0 }), "同步：离线");
assert.equal(api.syncStatusBadgeText({ phase: "conflict", openConflicts: 2 }), "同步：2 个冲突");
assert.equal(api.syncStatusBadgeText(null), "同步：已关闭");

// Conflict cards carry source device (§52) and local display context.
const bank = {
  questions: [{ id: "q1", word: "利", sentence: "金就砺则利。", articleId: "article-1" }],
  catalog: [{ id: "article-1", title: "劝学" }],
};
const card = api.adaptSyncConflict({
  conflict_id: "sc_1",
  kind: "review",
  entity_kind: "review",
  entity_id: "q1",
  server_value: { kind: "review", value: { status: "passed" } },
  incoming_value: { kind: "review", value: { status: "needs_revision" } },
  source_device: "讲台电脑",
  source_username: "teacher01",
  created_at: "2026-01-01T00:00:00",
}, bank);
assert.equal(card.id, "sc_1");
assert.equal(card.source, "讲台电脑");
assert.equal(card.display.word, "利");
assert.equal(card.display.article, "劝学");
assert.equal(card.serverValue.status, "passed");
assert.equal(card.incomingValue.status, "needs_revision");
assert.equal(api.adaptSyncConflict(null, bank), null);

// Unresolved counting: only server/incoming move a conflict forward;
// skip defers it, so it still counts as needing attention.
const cards = [{ id: "a" }, { id: "b" }];
assert.equal(api.countUnresolvedSync(cards, {}), 2);
assert.equal(api.countUnresolvedSync(cards, { a: "server" }), 1);
assert.equal(api.countUnresolvedSync(cards, { a: "server", b: "skip" }), 1);
assert.equal(api.countUnresolvedSync(cards, { a: "server", b: "incoming" }), 0);

// Batch builders accept only server/incoming.
assert.equal(Object.keys(api.buildSyncChoices(cards, "server")).length, 2);
assert.equal(Object.keys(api.buildSyncChoices(cards, "sideways")).length, 0);

console.log("sync ui tests passed");
