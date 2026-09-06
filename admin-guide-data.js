// admin-guide-data.js: v4 question-import guide shared by the admin page.
const QUESTION_BANK_TEMPLATE_EXAMPLE = {
  format: "wenyan-question-import", schemaVersion: "1.0", title: "请填写题目集合名称",
  description: "普通新增导入题全部进入待审，不携带题目 ID 或审查状态。",
  questionTypes: [{ id: "context_meaning", label: "语境释义题", description: "根据原句判断实词在语境中的意思。" }],
  books: [{ id: "book_001", label: "请填写教材册名称", order: 1 }],
  catalog: [{ id: "article_001", bookId: "book_001", unit: "请填写单元", title: "请填写文章名称", author: "" }],
  questions: [{ number: 1, type: "context_meaning", articleId: "article_001", word: "利", sentence: "金就砺则利。", targetOccurrence: 1, stem: "", options: [{ key: "A", text: "锋利" }, { key: "B", text: "利益" }, { key: "C", text: "有利" }, { key: "D", text: "顺利" }], answer: "A", explanation: "利：锋利。" }],
};

const QUESTION_BANK_FORMAT_GUIDE = `# JSON模版导入说明（v4）

本文件同时是格式说明和可复制模板。普通新增题使用 \`wenyan-question-import\` 1.0 格式，保存为 UTF-8 的 .json 后，在后台选择“新增导入题库（合并）”。

## 最重要的规则

1. 不要填写 bankId、question id、workflow、reviewStatus、duplicateReview、targetStart、article、volume、unit。
2. type 必须填写 questionTypes[].id；articleId 必须填写 catalog[].id；catalog[].bookId 必须填写 books[].id。ID 是程序识别值，label/title 只是显示名称，不能混用。
3. 每道题只设置一个 word。word 在 sentence 中出现多次时，用从 1 开始的 targetOccurrence 指明考查哪一次；系统会实时计算划线位置。
4. 每道题必须有 A、B、C、D 四个不重复选项，answer 只能是其中一个 key。新题由服务端生成本机 ID，统一进入“待审”。
5. 同一篇文章增加题目时，复制已有 catalog 的 articleId；不要复制已有题目的 id（模板不要求填写 id）。

## 字段结构

- 顶层必须是 format、schemaVersion、questionTypes、books、catalog、questions；format 固定为 wenyan-question-import，schemaVersion 固定为 1.0。
- books 项目：id、label，id 在本题库内唯一。
- catalog 项目：id、bookId、title；bookId 必须引用 books.id，可选 unit、author。
- questions 项目：type、articleId、word、sentence、targetOccurrence、options、answer、explanation；number 可填作显示顺序，但系统会处理冲突。
- source、context、supportingItems 等来源或辅助信息可以作为可选字段保留。

## 审查和合并

普通导入文件中的“已确认”信息不可信，导入后必须由老师在快速审查中确认。系统会先生成导入预览，再按文章、考点词、原句和出现位置识别核心题目；完全相同会跳过，细节不同的同核心题会成为重复候选。完整题库迁移请使用后台“导出当前完整题库”，它会保留 bankId、题目 ID、审查状态和重复处理结果。

## 常见错误

- 把文章名称填进 articleId，或把教材名称填进 bookId；
- 使用 targetStart 或旧版 reviewStatus；
- word 不在 sentence 中，或 targetOccurrence 超过出现次数；
- 选项不是恰好四项、key 重复、answer 不在 A-D；
- 同一题复制旧 id，或把两个考点词合并成一个 word。

## 可直接复制的最小示例

下面的 JSON 还需要把目录 ID 替换成当前后台列出的真实 ID：

\`\`\`json
${JSON.stringify(QUESTION_BANK_TEMPLATE_EXAMPLE, null, 2)}
\`\`\`
`;
