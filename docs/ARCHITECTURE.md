# 当前架构（As-built）

本文档根据当前工作区实际代码整理，基线为 `version.json` 中的 `1.4.11`。它记录“现在怎么工作”，不把未来理想架构写成现状。

## 1. Runtime overview

项目是一个本机单机教学应用：浏览器负责页面和交互，Python 进程负责静态文件、HTTP API 和本地 JSON 持久化。

```text
Windows EXE / BAT / python run_server.py
              |
              v
       launcher.py（可选 Tk 启动窗口）
              |
              v
   run_server.py -> ThreadingHTTPServer -> 127.0.0.1
                                      |
                         +------------+------------+
                         v                         v
                    index.html                 admin.html
                    学生端浏览器                管理后台浏览器
                         |                         |
                         +-------- /api ----------+
                                      |
                         JSON 文件 + 本地用户目录
```

- 源码直接运行可以调用 `python tools/run_server.py --port 8000`；根目录 BAT 负责探测 Python、复用本项目健康服务并打开浏览器。
- Windows 免 Python 包的入口是 PyInstaller 生成的 `文言实词限时训练.exe`。它启动 `launcher.py` 的 Tk 窗口，在后台线程运行 `run_server.main(..., --no-browser)`，再由浏览器打开学生页；“打开管理后台”先在 Tk 原生窗口验证密码，创建内存中的一次性 launch ticket，浏览器用 fragment ticket 换取短寿命 adminToken。
- 服务只绑定 `127.0.0.1`，不是局域网服务器，也没有远程数据库或云同步。正式服务关闭浏览器密码登录；只有显式传入 `--allow-browser-admin-login` 的源码调试服务才暴露旧的 `/api/admin-auth` 能力。
- `launcher.py` 通过 `/api/health` 检查 `ok` 和 `app == "wenyan-word-training"`，避免把同端口的其他 HTTP 服务误当成本项目。

## 2. 浏览器脚本与加载顺序

`index.html` 和 `admin.html` 使用原生 classic scripts，所有脚本均带 `defer`。因此脚本在 HTML 解析完成后执行，并保持标签出现顺序；它们不是 ES module，也没有 import/export 模块边界。

### 学生页 `index.html`

实际顺序是：

1. `scoring.js`
2. `question_identity.js`
3. `feedback-effects.js`
4. `student-shared.js`
5. `student-records.js`
6. `student-quiz.js`
7. `student-pk.js`
8. `app.js`
9. `pk-finish-effects.js`

前面的脚本通过 `window.WenyanScoring`、`window.WenyanQuestionIdentity`、`window.WenyanFeedbackEffects` 或共享 global function/variable 为后面的脚本提供能力。`app.js` 最后负责读取题库并启动页面；`pk-finish-effects.js` 虽然最后加载，但使用 MutationObserver 观察 PK 结果 DOM，因此在结果出现后接管终场展示。

### 管理页 `admin.html`

实际顺序是：

1. `scoring.js`
2. `question_identity.js`
3. `admin-guide-data.js`
4. `admin-shared.js`
5. `admin-auth.js`
6. `admin-questions.js`
7. `admin-reviews.js`
8. `admin-records.js`
9. `admin-update.js`
10. `admin-settings.js`
11. `admin.js`

`admin.js` 位于最后，负责初始化后台共享状态、加载数据、渲染 shell 和根据 tab 分发。各 `admin-*.js` 在 global scope 中提供页面渲染和事件绑定函数。

## 3. 学生前端模块图

| 模块 | 当前真实职责 |
| --- | --- |
| `app.js` | 查询 `#app`、持有跨模块全局状态、启动题库加载、恢复/清理离开提示；不是完整业务模块 |
| `student-shared.js` | HTML 转义、时间格式化、字号 100%-200%、随机打乱和公共学生样式状态 |
| `student-quiz.js` | 题库校验、教材/文章多选、可答题筛选、单人 session、绝对截止时间、提交、计分事件接入、正确自动跳题、错误反馈、结果保存和结果页 |
| `student-records.js` | 排行榜加载/排序/展示、答题记录快照构建、`POST /api/quiz-results` 保存；其中仍保留学生记录列表/详情渲染函数，但服务端读取接口要求管理员授权，当前首页不公开调用它们 |
| `student-pk.js` | PK 设置、同一题目集合、双方独立打乱、倒计时/限时、双方提交和计分、PK 记录构建、结果页和 `POST /api/pk-results` |
| `scoring.js` | 唯一计分规则实现，向浏览器暴露 `WenyanScoring` |
| `question_identity.js` | 题目核心/详细身份、出现位置、重复组、导入合并和候选题处理，兼容 Node 测试 |
| `feedback-effects.js` | 局部 Canvas 的正确/超级正确/错误/超级错误粒子效果；控制器可播放、停止、resize、destroy |
| `pk-finish-effects.js` / `.css` | 只复制显示层的 PK 终场效果，使用 transition key 避免保存状态重绘时重复播放，不修改比赛 state |

单人答题的大致状态流：

```text
loadBank -> startSelection -> startGame
                         -> answering
submitAnswer -> calculateScoreEvent -> correct-feedback / wrong-highlight
correct-feedback -> 自动 nextQuestion 或 finishGame
wrong-highlight -> wrong-feedback（原位反馈区）-> 手动 nextQuestion 或完成
finishGame -> result -> 保存答题记录 + 可选排行榜
```

计时器只更新倒计时 DOM；session 保存绝对 `deadlineAt`，提交、跳题和结束时再次核对状态与截止时间。

## 4. 管理前端模块图

| 模块 | 当前真实职责 |
| --- | --- |
| `admin.js` | `adminApp`、API 地址表、共享后台 state、加载题库/排行榜/记录/审查/历史、shell/tab 渲染、全局事件路由、页面生命周期 |
| `admin-shared.js` | 后台 normalizer、状态/可用性判断、日期格式化、鉴权 fetch、ETag 和通用 HTML 工具 |
| `admin-auth.js` | 锁定页、启动器 ticket 交换、仅源码调试时的浏览器登录、内存 token、登出和 pagehide 清理 |
| `admin-questions.js` | 题目列表/编辑/新增/删除、原句选词和出现位置、题库 JSON 模板/说明、导入合并/替换、导入导出历史、撤销导入、目录/教材册/题型维护 |
| `admin-reviews.js` | 待审/已确认/待修改/跳过和重复候选审查、发布状态同步、审查保存 |
| `admin-records.js` | 排行榜编辑、答题记录查看、折叠/恢复/批量处理、记录 JSON 导入导出；答题记录不提供删除 |
| `admin-settings.js` | 题型/教材册/文章设置、计分方式和答题时长设置；管理员密码不属于浏览器后台 |
| `admin-update.js` | 更新状态轮询、检查更新、更新确认和状态弹窗 |
| `admin-guide-data.js` | 按当前后台目录和题型生成 JSON 模板与合并后的 Markdown 导入说明 |

后台写操作由服务端再校验，前端的可用性提示不是安全边界。

## 5. 服务端模块图

`tools/run_server.py` 是 HTTP orchestrator：创建 `ThreadingHTTPServer`、设置 `WRITE_LOCK`、初始化文件、注册更新管理器、实现 GET/POST/PUT/PATCH/DELETE 路由、鉴权和兼容导出。它还保留一些旧 import surface 的 wrapper，以便现有测试和旧代码继续调用。

| 模块 | 当前真实职责 |
| --- | --- |
| `server_config.py` | 运行根目录、版本、题库/审查/历史路径、用户数据路径、默认配置、保留上限和密码 hash 常量 |
| `server_auth.py` | SHA-256 密码校验、不可变检修凭据校验、内存 session token、过期和全部撤销、一次性 launch ticket、密码修改领域函数 |
| `server_storage.py` | JSON 读取、先备份、临时文件 flush/fsync、`os.replace` 原子写入、备份轮转 |
| `server_validators.py` | 题库、题目、目录、题型、计分、排行榜、solo/PK 记录、历史和审查的纯校验/归一化/身份规则 |
| `server_questions.py` | 题库和审查文件路径配置、题库初始化、审查同步、导入历史、合并/替换撤销 |
| `server_records.py` | 答题记录和排行榜迁移/读取/留存、solo 结果幂等保存、PK 结果按 matchId 幂等保存 |
| `update_service.py` | 读取 GitHub stable release、版本/资产/sha256 检查、下载和启动更新助手 |
| `update_helper.py` | 读取更新 manifest、拒绝数据/题库/路径穿越、备份被替换代码、原子覆盖、失败回滚、重启 |
| `launcher.py` | Tk 启动窗口、服务启动/接管/健康轮询、学生页打开、原生管理员验证、原生密码修改、浏览器打开和退出时停止服务 |
| `build_release.py` | 版本读取、代码 allowlist 复制、PyInstaller 构建、源码/Windows ZIP、update manifest 和 SHA256 |

主要依赖方向是：

```text
run_server -> server_config
           -> server_auth -> server_storage
           -> server_questions -> server_validators / server_storage
           -> server_records -> server_validators / server_storage
           -> update_service
launcher -> run_server
build_release -> version.json + 显式 runtime allowlist
update_helper -> update-manifest.json（只处理代码包）
```

## 6. API 和生命周期

当前健康接口返回应用名、版本和 `apiVersion: 1`。主要公开读取接口是 `/api/questions` 和 `/api/leaderboard`；学生结果写入使用 `/api/quiz-results` 和 `/api/pk-results`。管理员读取/写入题库、审查、历史、排行榜、答题记录、设置和更新接口需要内存 token。

### Solo lifecycle

1. 学生页 GET `/api/questions`，服务端从本机题库读 JSON；空白题库也能返回。
2. 前端按教材册和文章多选构造候选，排除 `abnormal`、`candidate`、`needs_revision`、未处理重复候选等不可答题目。
3. `startGame()` 生成 session、复制/打乱题目和选项、记录开始时间与绝对 `deadlineAt`，并把计分配置快照放入 session。
4. 第一次有效选项调用 `calculateScoreEvent()`；结果 detail 进入答题记录。答对进入短反馈后自动下一题，答错先短暂红/绿显示，再由学生手动进入下一题。
5. 全答完、提前交卷或到时进入结果页。答题记录只从 `answerDetails` 生成实际作答题快照；`POST /api/quiz-results` 在服务端写记录，带姓名时可以在同一个锁内追加全局排行榜。
6. 结果保存按记录 ID 幂等；页面重绘或保存状态变化不能重新保存同一条成绩。

### PK lifecycle

1. 从同一学生选择范围获得可答题集合；按“比时间”或“比题数”创建 match。
2. PK 记录共享题目 ID 集合，但 player1/player2 各自独立打乱题目和选项；倒计时后双方在同一比赛阶段作答。
3. 每个玩家有独立分数、连击、当前题、答题明细和完成状态，使用相同 `calculateScoreEvent()`。
4. 双方结束后计算比分结果。分数先比较；比题数模式分数相同时，双方用时差超过 500ms 才用更短用时决胜，否则平局。
5. 结果页终场动效复制显示层，正式 PK state 不由动效修改；`POST /api/pk-results` 按 `matchId` 幂等保存，PK 记录不进入普通排行榜。

## 7. 持久化位置和数据边界

### 源码运行

- `data/questions.json`：实际题库。
- `data/question-reviews.json`：教师审查状态。
- `data/question-bank-history.json`：导入/导出/撤销审计历史。
- `data/backups/`：题库、审查和历史等应用旁 JSON 的自动备份。

### EXE 运行

PyInstaller 解包后的运行根目录由 `sys._MEIPASS` 决定，题库和旁路数据位于 EXE 所在目录的 `data/`。发布包不带这批数据，首次运行会创建空白题库。

### 跨版本用户数据

`%LOCALAPPDATA%/WenyanQuiz/` 保存：

- `leaderboard.json` 与 `backups/`；旧源码旁 `data/leaderboard.json` 只用于首次迁移；
- `answer-records.json` 与 `answer-records-backups/`；
- `admin-settings.json` 及其备份；
- `service.pid`、更新结果和更新备份等运行辅助文件。

普通答题和 PK 记录共享最近 30 天、最多 100 条的总保留额度；折叠是 `archived` 状态，不是删除。自动备份最多 100 份且不超过 90 天。

### Public/private boundary

- Git 仓库只公开 `public-data/questions.json` 这份按文章保留 1-3 题的体验样例，以及 `public-data/README.md`；本机 `data/` 题库、审查、历史和生成素材由 `.gitignore` 排除。
- `build_release.py` 的公开源码包和 Windows 包通过显式 allowlist 构建，不复制 `public-data`，也不复制 `data/`；`safe_relative()` 额外拒绝题库相关路径。
- 教师完整题库包只能作为本机私有产物管理，不能进入 Git、GitHub 或公开 Release。

## 8. 发布架构

`tools/build_release.py` 只从 `version.json` 读取版本。当前 `RUNTIME_WEB_FILES` 包含两个 HTML、学生/后台 JS、两个 CSS、`version.json` 和应用图标；`RUNTIME_PYTHON_FILES` 包含服务、启动器、更新器和所有 `server_*.py`。`SOURCE_FILES` 由这些运行清单加 `.gitignore` 和使用说明组成。

当前清单的实际内容如下；以后新增运行依赖必须在对应清单中显式加入：

```text
RUNTIME_WEB_FILES
  index.html
  admin.html
  app.js
  admin.js
  scoring.js
  question_identity.js
  feedback-effects.js
  student-shared.js
  student-records.js
  student-quiz.js
  student-pk.js
  admin-guide-data.js
  admin-shared.js
  admin-auth.js
  admin-questions.js
  admin-reviews.js
  admin-records.js
  admin-update.js
  admin-settings.js
  pk-finish-effects.css
  pk-finish-effects.js
  style.css
  admin.css
  version.json
  assets/wenyan-word-training.ico

RUNTIME_PYTHON_FILES
  tools/server_config.py
  tools/server_auth.py
  tools/server_storage.py
  tools/server_validators.py
  tools/server_questions.py
  tools/server_records.py
  tools/run_server.py
  tools/launcher.py
  tools/update_helper.py
  tools/update_service.py
```

- `--source-only` 生成源码 ZIP；`--github-only` 生成 Windows 更新包；无模式时两者都生成。
- Windows 构建在构建机上调用外部 `pyinstaller`，生成带图标的 launcher/updater EXE；运行时不需要 Python。
- 每个包生成 `update-manifest.json` 和 SHA-256 校验文件。更新服务只选择稳定 GitHub Release 的匹配资产；更新助手备份并原子替换清单中的代码文件。

## 9. 更新架构

`run_server.py` 创建 `UpdateManager`。管理员后台通过 `/api/update-status`、`/api/update-check`、`/api/update-apply` 与它交互。源码工作区有未提交改动时不自动替换；下载包需匹配大小和 SHA-256。

`update_helper.py` 只接受带 `update-manifest.json` 的 ZIP，拒绝 `data/`、`release/`、`.git`、题库相关文件和路径穿越；替换前将旧代码备份到用户数据目录，写入新 manifest 后启动新版并轮询本地 `/api/health`，只有应用名和目标版本均匹配才写成功结果。新版启动失败时会停止本次启动的进程、回滚程序文件并尝试重启旧版。冻结版更新助手先复制到 `%LOCALAPPDATA%/WenyanQuiz/updater-runtime/<随机目录>/` 后再运行，避免覆盖正在运行的自身。更新流程不应触及题库、排行榜、答题记录和管理员配置。

## 10. 已知架构折中

- classic scripts 共享 global scope，加载顺序是隐式依赖；这是当前稳定运行方式，修改时必须尊重顺序，不在本任务中改成 module bundling。
- `app.js`、`admin.js`、`run_server.py` 仍是编排入口而不是纯粹 dependency injection；服务端还保留兼容 wrapper。
- `student-quiz.js`、`admin-questions.js`、`server_validators.py` 较大但职责内部仍有一致性；今后若拆分，必须保持 global/API 和 schema 兼容。
- `student-records.js` 中的学生端历史记录渲染和 legacy route 可能是历史兼容残留；目前不能仅凭“首页未调用”删除，需单独确认缓存页兼容策略。
- `build_release.py` 的 runtime 清单需要人工维护；这保证了隐私边界，但新增运行文件容易漏列，必须依赖测试和发布检查。

## 11. 当前仓库树摘要

运行代码在根目录的 HTML/JS/CSS、`tools/` 服务和构建脚本；测试在 `tests/` 与 `tools/test_*.js`；公开样例在 `public-data/`；`data/`、`release/`、`release-build-v*/` 和缓存目录是本机运行/构建产物，按 `.gitignore` 不属于公开代码交付面。当前本地还存在空的 `demos/` 目录，但它没有运行清单文件，也不是正式运行依赖。
