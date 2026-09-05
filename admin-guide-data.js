// admin-guide-data.js: question-bank import template and format guide
// Classic script module; shared state and API contracts remain in admin.js.

const QUESTION_BANK_TEMPLATE_EXAMPLE = {
  schemaVersion: "3.0",
  title: "请填写题库名称",
  description: "请说明适用年级、教材范围和题目来源。",
  questionTypes: [
    { id: "context_meaning", label: "语境释义题", description: "根据原句判断实词在语境中的意思。" },
  ],
  books: [
    { id: "bx1", label: "必修上册", order: 1 },
  ],
  catalog: [
    { id: "bx1_article_001", volume: "必修上册", unit: "第三单元", title: "劝学", author: "荀子" },
  ],
  quizDefaults: {
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
  },
  questions: [
    {
      id: "bx1_article_001_001",
      number: 1,
      type: "context_meaning",
      articleId: "bx1_article_001",
      article: "劝学",
      volume: "必修上册",
      unit: "第三单元",
      word: "利",
      sentence: "金就砺则利。",
      targetStart: 4,
      targetOccurrence: 1,
      stem: "",
      options: [
        { key: "A", text: "锋利" },
        { key: "B", text: "利益" },
        { key: "C", text: "有利" },
        { key: "D", text: "顺利" },
      ],
      answer: "A",
      explanation: "利：锋利。",
    },
  ],
};

const QUESTION_BANK_FORMAT_GUIDE = `# JSON模版导入说明

这是“文言实词限时训练”的题库导入说明与可直接复制的 JSON 模版。请使用 UTF-8 编码保存为 .json 文件后，再在后台使用“新增导入题库（合并）”。本文件末尾附有当前系统完整的题型、教材册、篇目 ID 目录和一份可复制的 JSON 示例。

## 导入规则

1. 题库必须包含“questions”数组，且至少有一道题。
2. “catalog”是篇目目录，至少要有一篇文章；题目的“articleId”必须能在其中找到，题目的“article”和“volume”必须分别与该篇目的“title”和“volume”一致。
3. “books”是教材册/范围目录；“catalog[].volume”和“questions[].volume”必须使用对应教材册的“label”。
4. “questionTypes”是题型目录；题目的“type”必须使用其中的“id”。目前自定义题型会按普通四选一题显示。
5. “id”是题目的稳定本机编号；使用“新增导入题库（合并）”时，系统按文章、考点词、原句、考点位置和题目内容判断重复；新增题会自动使用新的本机编号，不依赖导入文件中的临时 ID。题号 number 也会保持稳定，删除题目后允许出现空号。
6. 每道题必须有四个选项，选项键必须恰好是 A、B、C、D，“answer”必须是其中一个键。

## 顶层字段

- “schemaVersion”：格式版本，建议填写“3.0”。
- “title”、“description”：题库名称和说明。
- “questionTypes”：题型数组，每项需要“id”、“label”，可选“description”。
- “books”：教材册数组，每项需要唯一“id”、“label”，可选“order”。
- “catalog”：文章数组，每项需要唯一“id”、“title”、“volume”，可选“unit”、“author”。
- “quizDefaults”：可选的训练设置。“durationSeconds” 是每局答题时长，范围为 10-3600 秒；旧题库可以继续使用“correctScore”和“wrongScore”；新格式建议使用“scoring”配置计分机制。
- “quizDefaults.scoring.mode”：填写“fixed”或“streak”。“fixed”是固定计分；“streak”是连续表现计分。
- “quizDefaults.scoring”：可配置“baseCorrect”、“baseWrongPenalty”、“correctStreakAfter”、“correctStreakScore”、“wrongStreakAfter”和“wrongStreakPenalty”。连续次数超过对应 After 值后，当前题开始使用对应的连击分或连续错误扣分；“correctStreakAfter”和“wrongStreakAfter”均为 1-5 题。
- “questions”：题目数组。
- “questions[].reviewStatus”：可选的发布状态；新导入题建议填写 “candidate”，候选题在教师快速审查通过前不会进入学生答题，通过后由后台改为 “verified”。划线异常题使用 “abnormal”，也不会进入答题。
- “questions[].duplicateReview”：可选的重复候选审查信息，格式为 { "status": "pending", "groupId": "duplicate-...", "relatedQuestionIds": ["本组题目 ID"] }；pending 和 skipped 题目不会进入学生答题，管理员在“快速审查”中处理后才会恢复。

## 题目字段

每道题至少填写：“id”、“type”、“articleId”、“article”、“volume”、“word”、“sentence”、“options”、“answer”、“explanation”。

- “word”可以是单字，也可以是一个不可再拆分的文言词语。
- “sentence”是包含考点词的原文句子。
- 每道题只设置一个考点词。若同一句中要考两个不同的词，请建立两道题，保留相同的 sentence，但分别填写各自的 word、targetStart、targetOccurrence、options、answer 和 explanation；不要把两个词拼成一个 word。
- “targetOccurrence”从 1 开始，表示 word 在 sentence 中第几次出现；如果原句中出现两次或更多次，必须填写正确的次数。只有一次时填写 1。
- “targetStart”是可选的、从 0 开始的字符位置，表示本题实际考查词语在 sentence 中的起点；如果填写，必须和 targetOccurrence 对应。通过管理后台“从原句中选择考查实词”保存的题目会自动写入它。
- “stem”可选；普通单选题可用它作为额外题干。
- “options”必须是四个对象，格式为 { "key": "A", "text": "释义" }。
- “explanation”请写出正确释义，便于答题后核对和后台审查。
- “source”、“context”、“supportingItems”等扩展字段可以保留，程序会原样保存。

## ID 和名称的区别

- ID 是程序内部使用的稳定本机编号，必须唯一；现有题目会保持原编号，合并导入的新题会由本机自动分配新的 ID。不要依赖导入文件中的临时 ID 判断题目是否重复。
- label 或 title 是页面显示名称，例如教材册 ID “xxbs”对应名称“选择性必修上册”，篇目 ID “xx2_suwu”对应名称“苏武传”。
- 题目的 type 必须填写题型 ID，不是题型名称；题目的 articleId 必须填写篇目 ID，不是文章名称；题目的 volume 填教材册名称，也就是 books[].label。
- 如果只是给一篇已经存在的文章增加新题，请从 catalog 中复制该文章的 article ID，所有新增题目使用新的 question ID，不要复制旧题目的 question ID。

## 教师按课导入的推荐流程

1. 在模板的 questionTypes、books、catalog 中找到要使用的题型 ID、教材册 ID/名称和文章 ID/名称。
2. 如果文章已经存在，只复用原有 catalog[].id；如果文章不存在，先新增教材册，再新增文章并给它一个新的稳定 ID。
3. 每道新增题可以填写任意临时 question ID；合并导入时，程序会统一改成本机自动分配的题目编号。
4. 写入 word、sentence 和 targetOccurrence；先从左到右数 word 在 sentence 中的出现次数。不要因为原句里有相同字词，就默认第一处一定是考点。若填写 targetStart，请按从 0 开始的字符位置填写；新导入题即使填写 verified，合并导入也会按 candidate 进入待复核状态。
5. 如果同一句还要考另一个不同的词，复制 sentence 新建另一道题，并为新题单独计算 targetStart 和 targetOccurrence。
6. 写入四个选项、正确答案和解析。三个干扰项应是同一个词的其他常见义项或相邻义项，不能只是随意拼凑。
7. 导入前先检查 articleId、type、volume 的对应关系；再使用“新增导入题库（合并）”。
8. 导入后在后台“快速审查”逐题核对原句、考点位置、答案和干扰项。

## 程序会拒绝的常见错误

- articleId 不在 catalog 中，或 type 不在 questionTypes 中。
- volume 不在 books 的 label 中，或文章的 volume 与题目的 volume 不一致。
- catalog 为空、篇目 title 与题目的 article 不一致，或题号 number 重复。
- word 在 sentence 中找不到，targetOccurrence 小于 1，或 targetOccurrence 大于实际出现次数。此类划线定位问题不会删除题目，服务端会将题目写成 reviewStatus: "abnormal" 并在答题时自动跳过。
- targetStart 不是从 0 开始的实际字符位置，或 targetStart 与 targetOccurrence 指向的出现位置不一致；请在后台题库管理中重新选择考点并保存。
- 同一句的不同考点没有拆成不同题目，或一题的 word 同时包含多个互不相连的词。
- question.id、catalog.id、books.id、questionTypes.id 在各自目录中重复。
- options 不是恰好四项，选项键不是 A、B、C、D，或选项文字重复。

## 最小示例

请直接参考本文件末尾的“可直接复制的 JSON 模版”。模版会列出当前题库的全部题型、教材册和篇目目录；生成多道题时，只需继续向“questions”数组添加题目，并保证篇目 ID、题型 ID 和题目字段对应正确。合并导入时题目 ID 只是临时编号，系统会自动分配本机编号。

## 合并导入说明

“新增导入题库（合并）”会把新篇目、新教材册、新题型和新题目加入当前题库。系统先按文章、考点词、原句和考点出现位置判断核心题目，再比较题型、题干、选项文字、正确答案对应文字和解析：完全一致的题目会跳过；细节不同的题目会保留并标记为“重复候选”，在管理员处理前不会进入答题。导入题统一使用本机自动分配的编号并标记为 candidate，快速审查点击“确认正确”后才发布为 verified；原题不会因为编号冲突被覆盖。导入前会自动备份当前题库。
`;
