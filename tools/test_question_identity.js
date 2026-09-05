const assert = require("node:assert/strict");
const {
  makeCoreSignature,
  makeDetailSignature,
  rebuildDuplicateReviews,
  mergeQuestionsByContent,
} = require("../question_identity.js");

const makeQuestion = (overrides = {}) => ({
  id: "bx-basic-001",
  number: 1,
  type: "context_meaning",
  articleId: "article-a",
  article: "劝学",
  volume: "必修上册",
  word: "利",
  sentence: "金就砺则利。",
  targetOccurrence: 1,
  targetStart: 4,
  stem: "",
  options: [
    { key: "A", text: "锋利" },
    { key: "B", text: "利益" },
    { key: "C", text: "有利" },
    { key: "D", text: "顺利" },
  ],
  answer: "A",
  explanation: "利：锋利。",
  source: { title: "课下注释" },
  reviewStatus: "passed",
  ...overrides,
});

const base = [makeQuestion()];

const exactWithDifferentId = makeQuestion({ id: "external-001", number: 99, source: { title: "外部题库" } });
const reorderedOptions = makeQuestion({
  id: "external-002",
  options: [
    { key: "D", text: "顺利" },
    { key: "B", text: "利益" },
    { key: "A", text: "锋利" },
    { key: "C", text: "有利" },
    ],
  answer: "A",
});
const exactResult = mergeQuestionsByContent(base, [exactWithDifferentId, reorderedOptions]);
assert.equal(exactResult.questions.length, 1);
assert.equal(exactResult.skippedQuestions, 2);
assert.equal(exactResult.newQuestions.length, 0);

const sameIdDifferentCore = makeQuestion({
  id: "bx-basic-001",
  articleId: "article-b",
  article: "师说",
  sentence: "师道之不传也久矣。",
  word: "传",
  targetStart: 5,
});
const newCoreResult = mergeQuestionsByContent(base, [sameIdDifferentCore]);
assert.equal(newCoreResult.questions.length, 2);
assert.equal(newCoreResult.questions[1].id, "bx-basic-002");
assert.equal(newCoreResult.questions[1].duplicateReview, undefined);

const variant = makeQuestion({
  id: "external-003",
  explanation: "利：锋利、锐利。",
});
const variantResult = mergeQuestionsByContent(base, [variant]);
assert.equal(variantResult.questions.length, 2);
assert.equal(variantResult.duplicateCandidateGroups, 1);
assert.equal(variantResult.duplicateCandidateQuestions, 2);
assert.equal(variantResult.questions[0].duplicateReview.status, "pending");
assert.equal(variantResult.questions[1].duplicateReview.status, "pending");
assert.deepEqual(
  variantResult.questions[0].duplicateReview.relatedQuestionIds,
  ["bx-basic-001", "bx-basic-002"],
);
assert.equal(variantResult.questions[0].duplicateReview.groupId, variantResult.questions[1].duplicateReview.groupId);

const resolvedBase = variantResult.questions.map((question) => ({
  ...question,
  duplicateReview: { ...question.duplicateReview, status: "kept" },
}));
const unrelated = makeQuestion({
  id: "bx-basic-003",
  articleId: "article-c",
  article: "逍遥游",
  sentence: "且举世誉之而不加劝。",
  word: "誉",
  targetStart: 5,
});
const preservedResult = rebuildDuplicateReviews([...resolvedBase, unrelated]);
assert.deepEqual(
  preservedResult.questions.slice(0, 2).map((question) => question.duplicateReview.status),
  ["kept", "kept"],
  "未受影响的候选组应保留原审查状态",
);

const edited = { ...resolvedBase[1], word: "完全不同", sentence: "另一个句子。", targetStart: 0, targetOccurrence: 1 };
const editedResult = rebuildDuplicateReviews([resolvedBase[0], edited], { resetQuestionIds: [edited.id] });
assert.equal(editedResult.questions[0].duplicateReview, undefined);
assert.equal(editedResult.questions[1].duplicateReview, undefined);

const deletionResult = rebuildDuplicateReviews([resolvedBase[1]], { resetQuestionIds: [resolvedBase[0].id] });
assert.equal(deletionResult.questions[0].duplicateReview, undefined);

const repeatedVariants = mergeQuestionsByContent(base, [
  variant,
  makeQuestion({ id: "external-004", stem: "请结合语境判断。" }),
]);
assert.equal(repeatedVariants.questions.length, 3);
assert.equal(repeatedVariants.skippedQuestions, 0);
assert.equal(repeatedVariants.duplicateCandidateGroups, 1);
assert.equal(repeatedVariants.duplicateCandidateQuestions, 3);
assert.deepEqual(
  repeatedVariants.questions.map((question) => question.id),
  ["bx-basic-001", "bx-basic-002", "bx-basic-003"],
);

const secondOccurrence = makeQuestion({
  id: "external-005",
  sentence: "利则进，利则退。",
  targetOccurrence: 2,
  targetStart: 5,
});
const occurrenceResult = mergeQuestionsByContent([makeQuestion({ sentence: "利则进，利则退。", targetOccurrence: 1, targetStart: 0 })], [secondOccurrence]);
assert.equal(occurrenceResult.questions.length, 2);
assert.notEqual(makeCoreSignature(occurrenceResult.questions[0]), makeCoreSignature(occurrenceResult.questions[1]));

assert.equal(
  makeDetailSignature(makeQuestion()).includes("课下注释"),
  false,
  "来源不应参与详细内容指纹",
);

console.log("question identity tests passed");
