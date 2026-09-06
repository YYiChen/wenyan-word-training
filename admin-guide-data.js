// admin-guide-data.js: v4 question-import guide shared by the admin page.
const QUESTION_BANK_TEMPLATE_EXAMPLE = {
  format: "wenyan-question-import", schemaVersion: "1.0", title: "请填写题目集合名称",
  description: "普通新增导入题全部进入待审；本文件只提供题库内容，不提供 bankId、题目 ID 或审查状态。",
  questionTypes: [{ id: "context_meaning", label: "语境释义题", description: "根据原句判断实词在语境中的意思。" }],
  books: [{ id: "book_001", label: "请填写教材册名称", order: 1 }],
  catalog: [{ id: "article_001", bookId: "book_001", unit: "请填写单元", title: "请填写文章名称", author: "" }],
  questions: [{ number: 1, type: "context_meaning", articleId: "article_001", word: "利", sentence: "金就砺则利。", targetOccurrence: 1, stem: "", options: [{ key: "A", text: "锋利" }, { key: "B", text: "利益" }, { key: "C", text: "有利" }, { key: "D", text: "顺利" }], answer: "A", explanation: "利：锋利。" }],
};

const QUESTION_BANK_FORMAT_GUIDE = `# JSON模版导入说明（v4）

本文件同时是格式说明和可复制模板。请使用 UTF-8 编码保存为 .json，在后台选择“新增导入题库（合并）”。这是给老师或其他 AI 制作“新增题目”的格式，不是完整题库备份格式。

## 一、ID 和名称必须分开

每个目录项都有两个概念：

- “id” 是程序识别用的稳定唯一标识，只能在引用字段中填写；
- “label”“title”“author” 是显示给老师和学生看的文字，不能代替 ID。

生成题目时，请按下面的关系选择 ID：

| 要填写的字段 | 正确来源 | 用途 |
| --- | --- | --- |
| “question.type” | “questionTypes[].id” | 题型 ID，例如 “context_meaning” |
| “question.articleId” | “catalog[].id” | 篇目 ID，不是文章名称 |
| “catalog[].bookId” | “books[].id” | 教材册 ID，不是教材册名称 |

例如，表格中显示“必修上册”“劝学”，但题目里应填写对应的 “bookId” 和 “articleId”，不能填写“必修上册”或“劝学”这两个名称。请优先复制本说明末尾目录中的左侧 ID，不要自行改写已有 ID。

## 二、导入文件的固定格式

顶层必须包含：

- “format”：固定写 “wenyan-question-import”；
- “schemaVersion”：固定写字符串 “1.0”；
- “questions”：题目数组。

为了让 AI 能正确选择文章、教材册和题型，建议同时保留本说明中列出的 “questionTypes”“books”“catalog” 三个目录。若题目只引用当前题库已经存在的目录，也可以省略目录，服务器会使用当前题库目录；但新增教材册或新增文章时，必须在同一文件中提供对应目录及正确的 ID。

普通新增导入文件不要填写：

- 顶层 “bankId”“workflow”；
- 题目的 “id”“reviewStatus”“duplicateReview”；
- 旧版 “targetStart”“article”“volume” 字段。

题目 ID 会由本机服务生成。导入文件里的“答案”是题目内容的一部分，但不会自动成为老师已经确认的审查结论；新题始终进入“待审”。

## 三、题型、教材册和篇目目录

### 题型目录 “questionTypes”

每项至少包含 “id” 和 “label”，可选 “description”。题目中的 “type” 必须等于某一项的 “id”。不要把页面名称写进 “type”。

### 教材册目录 “books”

每项至少包含 “id” 和 “label”，可选 “order”。同一题库中 “id” 和名称都应保持唯一。新增题目属于某册时，先在这里找到册 ID。

### 篇目目录 “catalog”

每项必须包含 “id”“bookId”“title”，可选 “unit”“author”。其中 “bookId” 必须引用 “books[].id”。题目的 “articleId” 必须引用这里的 “id”。

## 四、题目字段和考察词规则

每道题至少包含：

- “type”“articleId”：按上面的目录关系填写；
- “word”：一个最基本的考察单位，可以是单字，也可以是一个固定的文言词语；
- “sentence”：完整原句；
- “targetOccurrence”：从 1 开始，表示 “word” 在该原句中第几次出现；
- “options”：恰好四项，key 必须分别为 A、B、C、D；
- “answer”：只能填写 A、B、C、D 之一；
- “explanation”：释义或判断依据，建议填写清楚。

如果一句话中同一个考察词出现两次或三次，必须分别制作题目或明确不同的 “targetOccurrence”。例如 “word” 为“爱”，原句出现两次时，第一处填 1，第二处填 2；不要用 “targetStart”，后台会根据原句自动定位和高亮。若一句话同时考察两个不同的词，应制作两条题目，每条只填写一个 “word” 和对应的 “targetOccurrence”。

“number” 可以填写建议题号，但合并导入时若与当前题库冲突，系统会自动安排新题号。不要填写旧题目的 “id”，也不要把两个考察词拼成一个 “word”。

## 五、导入、预览和审查逻辑

后台会先生成预览，不会在预览阶段写入硬盘。服务器会按“篇目 ID + 考察词 + 原句 + 出现位置”判断题目的核心身份：

1. 完全相同的题会跳过，避免重复导入；
2. 同一核心但选项、答案或解析不同，会显示为修改或重复候选；
3. 外部新增题会生成新的本机题目 ID，并进入待审；
4. 同 bankId 的完整导出文件可以用于恢复同一题库的题目 ID、目录和审查状态，后台会让老师选择保留本机结论还是使用导入结论；
5. 合并导入记录会进入历史，之后可以在未发生后续冲突时撤销；撤销不可逆。

请在快速审查中检查原句、考察词高亮位置、四个选项和正确答案，确认后才会进入学生端可抽取范围。

## 六、完整题库导出格式

“导出当前题库 JSON”得到的是 “wenyan-question-bank” 4.0 完整文件，包含 “bankId”、所有稳定目录 ID、题目 ID、“workflow.reviews” 和 “workflow.duplicateResolutions”。它适合备份、同一题库迁移和版本回滚；不要把它当成给 AI 新增题目的简化模板。给 AI 新增题目时，请使用本文件规定的 “wenyan-question-import” 1.0。

## 七、常见错误

- 把文章名称填进 “articleId”，或把教材名称填进 “bookId”；
- 使用 “targetStart”，或者把 “unit” 误删后再写成 “volume”；
- “word” 不在 “sentence” 中，或 “targetOccurrence” 超过真实出现次数；
- 选项不是恰好四项、key 重复、选项文字重复、“answer” 不在 A-D；
- 同一篇文章中把两个考察词合并到一个题目，或复制旧题目 ID；
- 新增文章时没有提供对应的 “books[].id”“catalog[].id”，导致题目无法正确归类。

## 八、可直接复制的最小示例

下面的 JSON 还需要把目录 ID 替换成当前后台列出的真实 ID；名称只用于帮助人阅读，真正提交时请检查所有引用字段使用的是左侧 ID：

\`\`\`json
${JSON.stringify(QUESTION_BANK_TEMPLATE_EXAMPLE, null, 2)}
\`\`\`
`;
