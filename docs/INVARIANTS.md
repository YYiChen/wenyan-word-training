# 产品、业务与数据不变量

本文档只记录当前代码和 README 能确认的契约。以后新增功能若需要改变其中一项，必须先明确说明影响、迁移、兼容和测试；不能把“看起来更合理”当作改变理由。

## 1. 运行边界

### 1.1 本机服务

- 服务默认只监听 `127.0.0.1`；当前产品不是联网协作服务，没有账号系统、云端题库或跨电脑排行榜。
- 浏览器只是客户端；凡是需要持久化的题库、记录、排行榜或设置，必须经过本地服务写文件，不能依赖 localStorage 作为唯一数据源。
- `/api/health` 的 `app` 为 `wenyan-word-training`，`version` 来自 `version.json`，`apiVersion` 当前为 `1`。启动器依赖这组身份信息接管同项目旧服务。
- 正式服务的 `browserAdminLoginAllowed` 必须为 `false`；只有源码启动时显式传入 `--allow-browser-admin-login` 才允许 `/api/admin-auth`。Windows 启动器不传该参数。
- 学生 Web 页面不提供 admin.html 导航入口。直接访问 admin.html 必须先显示锁定页；正式页面不生成管理员密码输入框。
- 管理员密码只在 Windows Launcher 的 Tk 原生窗口中输入或修改。浏览器只接收内存中的 adminToken，不接触真实密码。
- Launcher 交给浏览器的 launch ticket 使用高熵随机值、默认 30 秒 TTL、fragment 传递、单次消费、内存保存；服务重启会使全部 ticket 失效。ticket 交换成功后仍使用现有 `X-Wenyan-Admin-Token` 和 session TTL。

## 2. 题库与题目契约

- 题库顶层必须有 `questions` 数组；空数组是合法的 blank bank，可以让首次安装进入后台并导入题库。
- 非空题库需要可验证的 `catalog`、`books` 和题型引用；Schema v4 canonical 文档始终保存这三个数组。普通 `wenyan-question-import` 文件在所有引用都指向当前目录时可以省略目录，服务端会补用当前目录；一旦提供目录，题目的 `articleId`、`type` 和篇目的 `bookId` 必须按 ID 正确引用，显示名称不能代替 ID。
- 每道题保持四个 A/B/C/D 选项、一个正确答案、篇目/教材册、原句、考点词和解释等必要字段；`type` 对早期缺失的语境释义题兼容默认为 `context_meaning`。题目 ID 和题号在一个题库内不能重复。
- 同一句多次出现考点词时，v4 只保存 `targetOccurrence`，展示位置由服务端/浏览器根据原句实时计算；历史答题快照中的旧 `targetStart` 不迁移删除。
- `data/questions.json` 是 v4 完整题库的唯一真相源，快速审查写入 `workflow.reviews`；学生投影不得包含 workflow。
- 学生可答状态由题目定位诊断、审查状态和重复题处理结果派生，不持久化 `playable` 或 `abnormal`。
- `bankId` 保持题库 lineage，用于判断题目 ID 是否具有跨文件稳定身份意义；它不决定教师审查成果是否可以使用。普通新增导入由服务端生成新的题目 ID，不能伪造已确认状态。
- 题目导入必须经过服务端预览/应用并校验 ETag；不同题库的同名 ID 不得覆盖本机题目。题目内容同步和审查状态同步是两个不同的问题：为了同步 review，绝不能静默修改本机已有题目内容。
- 题目核心身份至少包含文章 ID、考点词、原句和出现位置；题目内容细节用于区分同核心不同题。导入不能只按外部 ID 去重。semantic fingerprint 用于不同 lineage 间识别 exact logical question。
- 合并导入的新题会进入候选/审查路径；`abnormal`、`candidate`、`needs_revision`、未处理的重复候选等状态不能被学生端抽取。已通过审查的题目才是正常学生题库。
- Pending 是唯一的“尚无人工结论”审查状态；passed/needs_revision/skipped 都是已产生的人工处理结果。内容完全一致时，pending 永远最低优先级：pending 对非 pending 自动采用对方整条 review（pending+pending → pending）。两个相同的非 pending status 不是冲突，保留本机完整 review；仅 status 不同才构成审查结论冲突，reviewedAt/note/suggestedAnswer/optionIssues 不同不算冲突，不做字段级混合。
- 两个不同的非 pending status 是阻塞式 Review Conflict，必须人工显式选择（保留本机 / 采用导入 / 本次暂不处理）；程序不能默认任一边。有未处理冲突时 Apply 返回 422，不得自动补齐。conflictId 由本机题 ID、导入题 ID、双方 status 和语义指纹确定性生成，Apply 时服务端重新计算 planner 并校验 ID。Review Conflict 只针对内容完全一致的同一道题；内容已变化的题走内容策略（保留本机内容+本机 review，或采用导入内容+导入 review），不再进入审查冲突队列。审查冲突处理永远不改变题目内容；内容冲突处理与审查冲突处理相互独立。
- “本次暂不处理”仅表示这次导入不处理该冲突，题目内容与本机 review 保持不变，绝不把 review 改成 skipped。
- 普通合并（merge）必须经过服务端预览/应用并校验 ETag；不同题库的同名 ID 不得覆盖本机题目。题目内容同步和审查状态同步是两个不同的问题：为了同步 review，绝不能静默修改本机已有题目内容。
- 完整题库 JSON（`wenyan-question-bank` 4.0，含 `workflow.reviews`/`workflow.duplicateResolutions`）可以在多台电脑之间迁移审查成果：same-bank 按稳定 ID 匹配（same ID + same semantic content 为未修改，same ID + different content 为内容修改）；different-bank 不信任对方 ID，先做目录映射，再按映射后的 semantic fingerprint 精确匹配同一道逻辑题并通过 questionMap 迁移 review，新题分配新的本机 ID 但随附其 review；same core 但细节不同只进入重复候选，不自动覆盖。合并始终保留本机 `bankId`。
- 普通 `wenyan-question-import` 只负责导入题目内容，永远不能让题目直接 passed：新增题一律 pending，即使 JSON 中伪造 workflow/reviewStatus/review 也不能继承；它也不能补充本机已有题的 review。
- 完整题库替换（replace）是整套取代本机：完整采用导入的 bankId、题目、workflow、目录和训练默认设置，不因 bankId 不同而清空 review；普通 question-import 替换则创建新 bankId，全部 pending 并重算重复。
- 重复处理决定（`workflow.duplicateResolutions`）同样是教师人工审查成果：本机已有 kept/skipped 决定不被自动覆盖；本机无决定时，只有经过目录/题目映射后语义成员完全一致的 group 才能补充对方决定并经 questionMap 转成本机 ID；成员变化则不继承。
- 题库变更需要通过服务端验证；题库写入会同步审查状态，导入/导出/撤销导入会保留只读历史。导入历史记录实际发生变化的 review（`updatedReviews` before/after）和重复处理决定（`updatedDuplicateResolutions`，旧事件缺省为空）；未被修改的本机审查不记 delta。撤销导入通过追加事件完成，不直接删除历史；若导入后相关审查又被人工修改，则 after 状态校验失败，`canRevoke` 为 false 并提示“本次导入影响的审查结果后来又被修改，无法安全撤销”。导入应用必须基于预览返回的题库 ETag，过期预览不得覆盖新版本。
- 题目可用性由服务端派生并提供稳定 `availability.reason`：`playable`、`invalid`、`review_pending`、`review_needs_revision`、`review_skipped`、`duplicate_pending` 或 `duplicate_skipped`；学生端只依据 `playable` 抽题，后台展示不得重新推导另一套可用性规则。

## 3. 计分、限时和答题状态

- `scoring.js`/服务端对应校验是计分规则的唯一来源；当前默认配置为 fixed 模式，基础答对 `+1`、基础答错扣 `1`。streak 模式允许配置连续答对/答错阈值与额外分值，阈值校验范围为 1-5。
- 单人题库配置的答题时长当前必须为 10-3600 秒；答题局保存绝对 `deadlineAt`，提交答案时重新检查截止时间，不能仅信任倒计时显示。
- 一道题一局只能有效提交一次；快速点击不能重复加分、扣分或追加答题明细。
- 单人答题只在答题记录中保存实际作答的题目快照；未作答题不能因为整局题目列表而进入记录。
- 答对和答错的反馈不改变计分事件；答对是短反馈后自动继续，答错保留手动继续和解析的交互约定。

## 4. 结果保存与幂等

- 学生单人结果使用 `/api/quiz-results`；请求中记录 ID 是幂等键。重复保存不能产生重复答题记录或重复排行榜成绩。
- 学生结果保存可以在同一写锁中同时保存答题记录和带姓名的排行榜成绩。匿名结果保存为“未命名”；后续有名字的重试不能被匿名重试覆盖。
- PK 结果使用 `/api/pk-results`，以 `matchId` 幂等；PK 记录单独保存，不进入普通排行榜。
- 管理员写操作需要服务端会话 token；学生端不应借用管理员写接口。历史兼容接口可以保留，但不能绕过服务端授权边界。

## 5. 排行榜

- 当前是一个本机总榜，普通训练成绩可以追加；PK 不进入该榜。
- 排序为分数降序、同分按提交时间/`createdAt` 升序、最后按 ID 稳定排序。条目带有教材/篇目范围、时长和计分配置快照；旧条目缺失上下文时应显示历史/未知，不能编造。
- 管理员整体保存排行榜时使用 ETag/`If-Match` 检测并发修改；冲突不能静默覆盖另一窗口的新成绩。
- 排行榜不适用答题记录的 30 天/100 条留存规则；当前代码没有为排行榜设置同样的自动删除上限，除非提出新需求不得自行增加。

## 6. PK 公平性与结果

- PK 双方取自同一可答题集合，并记录共同题目 ID；双方各自独立打乱顺序和选项，不能因为一方的作答状态改变另一方的题目状态。
- 双方各自拥有 score、连击、答题明细和完成状态；终场动效只能复制显示层，不能写回玩家 state。
- 比分先按分数判胜负。比题数模式分数相同时，仅当用时差超过 500ms 才以更短用时胜出，否则为平局；平局不能把任一方错误放大为胜者。
- 系统减少动态效果时仍必须给出结果并完成保存，业务结束不能依赖动画播放成功。

## 7. 答题记录与隐私

- 答题记录仅管理员读取；学生端不公开完整历史记录。legacy 学生记录路由也必须经过管理员授权。
- 记录支持折叠/恢复，不提供删除接口；批量处理只能改变折叠状态，管理员 UI 不得把折叠伪装成物理删除以外的可恢复语义。
- 普通训练和 PK 记录共享一个留存池：最近 30 天内按结束时间保留，最多 100 条。折叠与未折叠不是两套独立上限。
- 记录导入必须校验完整快照、ID 和字段；导入重复 ID 跳过或拒绝的具体行为由当前管理接口保持，不得绕过 validator。

## 8. 写盘、备份和故障处理

- JSON 写入流程是“已有文件先备份 → 临时文件写入并 flush/fsync → `os.replace` 原子替换”。新文件写成功后才进行自动备份清理。
- 自动备份最多保留最近 100 份，并清理超过 90 天的自动备份；清理失败不能把已经成功的主文件写入报告为失败。
- 题库/审查/历史备份在应用旁 `data/backups/`；排行榜在 `%LOCALAPPDATA%/WenyanQuiz/backups/`；答题记录在 `%LOCALAPPDATA%/WenyanQuiz/answer-records-backups/`。
- 答题记录文件损坏时，服务会尝试备份旧文件并创建空记录，使新答题不被历史记录阻断；这不是无损恢复保证，管理员应保留备份并核查日志。

## 9. 发布与更新隐私边界

- GitHub 仓库可以有公开体验题库 `public-data/questions.json`，但完整教师题库、审查数据、导入历史、生成素材、本机排行榜、答题记录和密码配置不能公开。
- 公开源码 ZIP、公开 Windows ZIP 和更新 ZIP 不包含 `data/`、`public-data/` 或任何题库文件；`build_release.py` 的运行清单和 `safe_relative()` 是发布边界。
- 完整题库教师包必须是本机私发产物，不进入 Git、GitHub、公开 Release 或更新包。
- 更新服务只替换清单中的代码文件；更新助手拒绝顶层 `data/`、`public-data/`、`release/`、`.git/`、明确的数据文件名（questions.json、question-reviews.json、question-bank-history.json 等）和路径穿越，并在替换前保留用户数据及代码备份。仅按文件名 substring 禁止是不允许的：`admin-questions.js`、`server_questions.py` 等合法代码文件必须可安装。冻结版更新助手必须从应用安装目录外的临时副本运行。
- 更新成功必须同时满足程序文件替换完成、新版本启动、健康接口 `ok == true`、应用名正确且版本等于目标版本；否则必须停止本次启动的新进程、回滚程序文件并尝试重启旧版本。
- 更新包永远不包含、也不管理教师数据目录。旧服务完全退出且新程序尚未启动时，更新助手拥有可恢复的更新前数据快照：完整 `install_dir/data/`（不跟随符号链接）加上启动可能迁移的 LocalAppData 文件（leaderboard、answer-records、admin-settings），保存在安装目录之外的 `update-backups/<timestamp>/user-data/`；成功后按“最近 10 份或 30 天”保留。
- 更新失败回滚时，先停止新程序，恢复程序文件，同时恢复更新前数据快照（恢复前先保留失败新版的数据状态），最后重启旧程序。只有程序文件与必需的更新前数据恢复都成功，`rolledBack` 才能为 true；数据自动恢复未完全成功时必须明确报告并给出备份路径，绝不能写“已安全回滚”。
- Launcher 重启服务只做 HTTP 服务生命周期重启：不删除、不重置题库、排行榜、答题记录、密码或配置；端口被非本项目程序占用时不得 kill/shutdown 对方；重启期间不并发第二个 worker。
- `update-result.json` 只保存版本、前一版本、成功/回滚状态、消息和时间等结果元数据；更新排查日志不得保存密码、token、launch ticket 或题库内容。
- 源码工作区有未提交改动时，自动更新不能覆盖本地源码。

## 10. 版本与兼容

- `version.json` 是服务端和发布脚本读取的唯一版本源；发布目录名、README 历史说明、HTML 缓存参数和用户数据文件名都不是版本源。
- 旧题目、旧排行榜、旧答题记录缺失新字段时由 validator 使用兼容默认值；不要无需求删除旧字段或强制一次性重写用户数据。
- 任何真正破坏 API/schema 的变更都必须先定义迁移和回滚，并增加对应测试；不能用“前端已经更新”代替兼容方案。

## 11. 远程同步不变量（第一版）

- Local v4 bank remains fully usable when remote sync is unavailable. 本机题库永远是可用的 authoritative copy；同步默认关闭。
- Sync is disabled by default. 旧版升级后行为不变，不自动连接、不自动上传、不要求登录。
- Sync never stores credentials inside the question-bank document. 题库 JSON 禁止出现 server IP/port、账号、密码、token、clientId、revision、同步时间；同步是运行环境状态。
- Remote sync operates on entity changes, not repeated whole-bank replacement. 日常只有增量 operations；整库只用于首次初始化、灾难恢复与手工备份。
- Full-bank remote backup is separate from live sync. 备份上传不改共享题库、不增加 revision、不产生 operation；下载不自动导入本机。
- Backup upload never changes live workspace revision.
- After bootstrap, all clients share one bank lineage. 不同 bankId 本机题库出现时暂停自动同步，不得把新 lineage 自动推上服务器；同步启用期间“导入并替换”被阻止。
- A different local bankId pauses sync instead of silently replacing remote data.
- Local changes are saved before remote synchronization. 本机先落盘、UI 先成功，worker 再发现并上传；崩溃重启后 diff 重新发现。
- Remote writes are serialized. 服务器写锁 + SQLite 单事务（读基线、校验、应用、验 v4、写库、涨 revision、记日志）。
- Operations are idempotent. operation_id 确定性生成并唯一约束；重复提交返回原结果，不涨 revision、不重复建冲突。
- Different non-pending review conclusions create a blocking shared conflict. 绝不 last-write-wins；冲突持久化在服务器，所有设备可见并可处理。
- Unresolved sync-conflict questions are not student-playable. 以派生 `availability.reason = sync_conflict` 叠加（invalid 优先），不写入题库；学生端只看 playable。
- Student records are never sent to the sync server. 排行榜、答题记录、PK、密码、会话、日志不同步；备份只接受 v4 题库。
- v1.4.13 packaged v4 data must survive an in-place update to future releases. 同步默认关闭且不改变更新数据边界。

## 12. Needs confirmation（不写死的内容）

以下事项当前代码无法充分证明，本文不把它们伪装成 invariant：

- 题库内容本身是否完全符合某一教材版本、浙江高考命题范围或教师教学大纲；代码只能验证结构和引用，不能验证教育内容正确性。
- 不同 Windows 版本、杀毒软件、受保护目录和浏览器组合下的 EXE 启动/写盘体验；需要在目标机器实测。
- 教师私发包的分发、备份恢复和跨电脑迁移流程；当前代码只定义公开包隔离和本机路径，不定义组织级备份策略。
- leaderboard 的长期清理、最大条数和跨版本排序迁移策略；当前代码没有与答题记录相同的 retention 规则。
- `student-records.js` 中未被当前学生首页调用的历史记录渲染是否仍服务于旧缓存页；在没有引用审计和兼容决定前不删除。
