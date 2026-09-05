(function exposeQuestionIdentity(root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.WenyanQuestionIdentity = api;
})(typeof window !== "undefined" ? window : globalThis, () => {
  const normalizeText = (value) => String(value ?? "").trim().replace(/\s+/g, " ");

  const getOccurrences = (sentence, word) => {
    const source = String(sentence ?? "");
    const target = String(word ?? "");
    if (!source || !target) return [];
    const occurrences = [];
    let start = 0;
    while (start < source.length) {
      const index = source.indexOf(target, start);
      if (index < 0) break;
      occurrences.push(index);
      start = index + target.length;
    }
    return occurrences;
  };

  const getTargetOccurrence = (question) => {
    const rawOccurrence = Number(question?.targetOccurrence);
    if (Number.isInteger(rawOccurrence) && rawOccurrence >= 1) return rawOccurrence;
    const targetStart = Number(question?.targetStart);
    if (Number.isInteger(targetStart) && targetStart >= 0) {
      const occurrence = getOccurrences(question?.sentence, question?.word).indexOf(targetStart);
      if (occurrence >= 0) return occurrence + 1;
    }
    return 1;
  };

  const getOptionTexts = (question) => (Array.isArray(question?.options) ? question.options : [])
    .map((option) => normalizeText(option?.text))
    .filter(Boolean)
    .sort((left, right) => left.localeCompare(right, "zh-CN"));

  const getCorrectOptionText = (question) => {
    const answer = String(question?.answer ?? "").trim();
    return normalizeText((question?.options || []).find((option) => option?.key === answer)?.text);
  };

  const makeCoreSignature = (question) => JSON.stringify([
    normalizeText(question?.articleId),
    normalizeText(question?.word),
    normalizeText(question?.sentence),
    getTargetOccurrence(question),
  ]);

  const makeDetailSignature = (question) => JSON.stringify([
    normalizeText(question?.type || "context_meaning"),
    normalizeText(question?.stem),
    getOptionTexts(question),
    getCorrectOptionText(question),
    normalizeText(question?.explanation),
  ]);

  const hash = (value) => {
    let result = 2166136261;
    for (let index = 0; index < value.length; index += 1) {
      result ^= value.charCodeAt(index);
      result = Math.imul(result, 16777619);
    }
    return (result >>> 0).toString(36);
  };

  const makeDuplicateGroupId = (coreSignature) => `duplicate-${hash(coreSignature)}`;

  const DUPLICATE_REVIEW_STATUSES = new Set(["pending", "kept", "skipped"]);

  const normalizeDuplicateReview = (duplicateReview) => {
    if (!duplicateReview || typeof duplicateReview !== "object") return null;
    if (!DUPLICATE_REVIEW_STATUSES.has(duplicateReview.status)) return null;
    const relatedQuestionIds = Array.isArray(duplicateReview.relatedQuestionIds)
      ? [...new Set(duplicateReview.relatedQuestionIds
        .filter((id) => typeof id === "string" && id.trim())
        .map((id) => id.trim()))]
      : [];
    const groupId = String(duplicateReview.groupId || "").trim();
    if (!groupId || relatedQuestionIds.length < 2) return null;
    return {
      status: duplicateReview.status,
      groupId,
      relatedQuestionIds,
    };
  };

  const sameIdSet = (left, right) => {
    const leftSet = new Set(left || []);
    const rightSet = new Set(right || []);
    return leftSet.size === rightSet.size && [...leftSet].every((id) => rightSet.has(id));
  };

  const rebuildDuplicateReviews = (questions, { resetQuestionIds = [] } = {}) => {
    const source = (Array.isArray(questions) ? questions : []).map((question) => ({ ...question }));
    const resetIds = new Set(resetQuestionIds.filter((id) => typeof id === "string"));
    const previousReviews = new Map();
    const affectedGroupIds = new Set();

    source.forEach((question) => {
      const review = normalizeDuplicateReview(question.duplicateReview);
      if (!review) return;
      previousReviews.set(question.id, review);
      if (resetIds.has(question.id) || review.relatedQuestionIds.some((id) => resetIds.has(id))) {
        affectedGroupIds.add(review.groupId);
      }
    });

    const rebuilt = source.map((question) => {
      const next = { ...question };
      delete next.duplicateReview;
      return next;
    });
    const groups = new Map();
    rebuilt.forEach((question) => {
      const coreSignature = makeCoreSignature(question);
      const detailSignature = makeDetailSignature(question);
      if (!groups.has(coreSignature)) groups.set(coreSignature, new Map());
      const detailGroups = groups.get(coreSignature);
      if (!detailGroups.has(detailSignature)) detailGroups.set(detailSignature, []);
      detailGroups.get(detailSignature).push(question);
    });

    let duplicateCandidateGroups = 0;
    let duplicateCandidateQuestions = 0;
    groups.forEach((detailGroups, coreSignature) => {
      if (detailGroups.size < 2) return;
      duplicateCandidateGroups += 1;
      const groupQuestions = [...detailGroups.values()].flat();
      const relatedQuestionIds = groupQuestions.map((question) => question.id);
      const groupId = makeDuplicateGroupId(coreSignature);
      const unchangedGroup = groupQuestions.every((question) => {
        const previous = previousReviews.get(question.id);
        return previous
          && previous.groupId === groupId
          && sameIdSet(previous.relatedQuestionIds, relatedQuestionIds);
      });
      const shouldReset = affectedGroupIds.has(groupId)
        || groupQuestions.some((question) => resetIds.has(question.id))
        || !unchangedGroup;
      groupQuestions.forEach((question) => {
        const previous = previousReviews.get(question.id);
        question.duplicateReview = {
          status: shouldReset ? "pending" : previous.status,
          groupId,
          relatedQuestionIds,
        };
      });
      duplicateCandidateQuestions += groupQuestions.length;
    });

    return {
      questions: rebuilt,
      duplicateCandidateGroups,
      duplicateCandidateQuestions,
    };
  };

  const mergeQuestionsByContent = (baseQuestions, importedQuestions) => {
    const mergedQuestions = (Array.isArray(baseQuestions) ? baseQuestions : []).map((question) => ({ ...question }));
    const newQuestions = [];
    const existingContent = new Set(mergedQuestions.map((question) => `${makeCoreSignature(question)}\u0000${makeDetailSignature(question)}`));
    const usedIds = new Set(mergedQuestions.map((question) => String(question.id || "")));
    let nextNumber = Math.max(0, ...mergedQuestions.map((question) => Number(question.number) || 0)) + 1;
    const nextId = () => {
      let id = `bx-basic-${String(nextNumber).padStart(3, "0")}`;
      while (usedIds.has(id)) {
        nextNumber += 1;
        id = `bx-basic-${String(nextNumber).padStart(3, "0")}`;
      }
      nextNumber += 1;
      usedIds.add(id);
      return id;
    };

    let skippedQuestions = 0;
    (Array.isArray(importedQuestions) ? importedQuestions : []).forEach((question) => {
      const contentKey = `${makeCoreSignature(question)}\u0000${makeDetailSignature(question)}`;
      if (existingContent.has(contentKey)) {
        skippedQuestions += 1;
        return;
      }
      const { id: _importedId, number: _importedNumber, duplicateReview: _importedDuplicateReview, ...content } = question;
      const added = { ...content, reviewStatus: "candidate", id: nextId(), number: nextNumber - 1 };
      newQuestions.push(added);
      existingContent.add(contentKey);
    });

    const combinedQuestions = [...mergedQuestions, ...newQuestions];
    const duplicateMerge = rebuildDuplicateReviews(combinedQuestions, {
      resetQuestionIds: newQuestions.map((question) => question.id),
    });
    const newQuestionIds = new Set(newQuestions.map((question) => question.id));

    return {
      questions: duplicateMerge.questions,
      newQuestions: duplicateMerge.questions.filter((question) => newQuestionIds.has(question.id)),
      skippedQuestions,
      duplicateCandidateGroups: duplicateMerge.duplicateCandidateGroups,
      duplicateCandidateQuestions: duplicateMerge.duplicateCandidateQuestions,
      renumberedQuestions: newQuestions.length,
    };
  };

  return {
    getTargetOccurrence,
    makeCoreSignature,
    makeDetailSignature,
    makeDuplicateGroupId,
    rebuildDuplicateReviews,
    mergeQuestionsByContent,
  };
});
