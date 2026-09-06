# 安全开发与发布流程

本文档给以后维护者、Codex、Claude Code 和人工开发者使用。所有命令均按当前 Windows 仓库路径和现有脚本确认，默认在仓库根目录执行。

## 1. 开始修改前

1. `git status --short --branch`，确认当前分支、未提交改动和用户私有文件；不覆盖不属于本次需求的改动。
2. 阅读 `AGENTS.md`、本文件、`docs/ARCHITECTURE.md` 和 `docs/INVARIANTS.md`。
3. 用 `rg` 找到真实调用方、HTML 加载顺序、API 路由和测试；不要根据文件名猜 ownership。
4. 判断需求属于学生端、后台、服务端、发布/更新还是数据迁移；只修改对应模块。
5. 明确是否触碰 schema、计分、截止时间、记录留存、排行榜、PK、公私数据边界或更新器；任一项属于高风险，要先补测试/迁移方案。
6. 若要删代码，先检查全仓库引用、classic-script 加载、兼容 wrapper、发布清单和旧缓存风险；未经明确需求不要删除。

## 2. 学生端功能流程

适用文件：`student-shared.js`、`student-quiz.js`、`student-records.js`、`student-pk.js`、`feedback-effects.js`、`pk-finish-effects.js` 和必要的 `app.js` 接线。

1. 先确认现有 state、生命周期和 `calculateScoreEvent()` 调用点；计分、题目随机、题库筛选、deadline 和保存逻辑不要复制。
2. 单人功能放 `student-quiz.js`，PK 放 `student-pk.js`，公共格式/字号放 `student-shared.js`，效果放对应效果文件；`app.js` 只保留启动和跨模块协调。
3. 异步操作绑定当前 session/match；返回首页、重新开始、结束比赛时清理 timer、recovery、Canvas controller 和旧 Promise 的 UI 回调。
4. 先做无题库/空筛选/最后一题/到时/快速点击等边界，再做正常路径。
5. 浏览器手工检查 100%、150%、200% 字号、窄窗口、长选项、长解析、减少动态效果设置，并确认没有横向滚动。

## 3. 管理后台功能流程

适用文件：`admin-questions.js`、`admin-reviews.js`、`admin-records.js`、`admin-settings.js`、`admin-update.js`，必要时才改 `admin.js`。

1. 先确认后台当前 tab、共享 state、`adminAuthorized`、ETag 和对应 API。
2. 题库编辑必须经过前端和服务端 validator；文章、教材册、题型用 ID 关联，显示名称不能代替 ID。
3. 导入/导出必须保持历史审计、合并去重、候选审查和公开/私有边界；不要直接写 `data/questions.json` 绕过 API。
4. 记录只能折叠/恢复和批量处理；当前服务明确拒绝答题记录 DELETE。排行榜整体保存必须保留 ETag 冲突提示。
5. 管理员密码登录和修改属于 `tools/launcher.py` 与 `tools/server_auth.py`；必须经过当前密码/不可变检修凭据校验，使用现有原子写入后撤销现有 token。不要在浏览器后台重新加入密码表单，也不要把密码或 hash 打进日志、文档、release。
6. 空白题库是合法状态；没有题目、记录、历史或审查项时必须有合理空状态，不能访问数组第一个元素。

## 4. 新增服务端 endpoint 流程

1. 先在 `run_server.py` 确认方法、鉴权、请求大小、错误状态和现有 API 风格；明确学生公开接口与管理员接口边界。
2. 可复用的验证放 `server_validators.py`；JSON 原子写盘放 `server_storage.py`；题库/审查放 `server_questions.py`；记录/排行榜放 `server_records.py`；不要把业务实现塞进路由函数。
3. 所有共享写操作使用现有 `WRITE_LOCK`；不要在 service 内另造不一致的锁或直接替换路径常量。
4. 写接口考虑重复请求、ETag 冲突、旧字段、空数据、损坏文件和失败回滚；明确是否需要备份和历史事件。
5. 为正常、无权限、错误 JSON、旧 schema、并发/幂等和持久化重启增加测试；再做真实 HTTP 联调。

## 5. 新增运行文件或资源

1. 先判断它是浏览器运行文件、Python 运行文件、构建工具、测试还是本机私有素材。
2. 浏览器运行文件加入 `tools/build_release.py` 的 `RUNTIME_WEB_FILES`；Python 运行文件加入 `RUNTIME_PYTHON_FILES`。两者会通过 `SOURCE_FILES` 进入源码包，不能只放在目录里。
3. HTML `src/href` 的本地引用必须落在 runtime manifest；资源不要引用 Downloads、用户目录或外部 CDN，除非需求明确允许。
4. 运行 `python -m unittest discover -s tests -q`，其中 `tests/test_runtime_assets.py` 会检查清单无重复、文件存在、HTML 本地资源和 `version.json` 来源。
5. 用发布脚本实际生成包，并检查 ZIP 条目没有 `data/`、题库、审查、历史、release 或本机配置。

## 6. 修改 persistence/schema 的流程

1. 在 `server_validators.py` 找到旧数据的读取、归一化和验证入口，先列出旧字段、默认值和失败行为。
2. 优先新增可选字段，保留旧字段读取；若必须改变含义，增加明确 schema/version、迁移前备份、失败回滚和兼容读取。
3. 同时更新浏览器构建快照、服务端 validator、导入/导出、管理员 UI、README/规范文档和测试。
4. 明确留存、归档、备份、更新助手是否会触碰它；任何更新包不得覆盖用户数据。
5. 在临时目录做重启验证：保存 → 关闭服务 → 再启动 → 读取；不要只用内存对象测试。

## 7. 可执行测试与检查命令

以下命令从仓库根目录运行：

```powershell
python -m unittest discover -s tests -q
node tools\test_scoring.js
node tools\test_question_identity.js
git diff --check
```

JavaScript 语法检查：

```powershell
Get-ChildItem -LiteralPath . -File -Filter '*.js' |
  ForEach-Object { node --check $_.FullName }
```

Python 编译检查：

```powershell
Get-ChildItem -LiteralPath 'tools' -File -Filter '*.py' |
  ForEach-Object { python -m py_compile $_.FullName }
```

当前自动测试覆盖：

- 空白题库初始化与验证、题库目录约束；
- 题目重复身份、出现位置、审查状态和异常划线隔离；
- solo/PK 结果字段、实际作答题快照、匿名/命名保存、幂等、排行榜排序、PK 不入总榜；
- 记录 30 天/100 条共享留存、折叠恢复和备份相关边界；
- 题库历史追加/撤销；
- runtime manifest、HTML 本地资源和 `version.json`；
- 更新版本选择、摘要校验、更新 ZIP 路径安全、回滚和用户数据保护；
- Node 计分规则和题目身份函数。

当前没有浏览器自动化测试，也没有 CI；上面测试不能替代 Edge/Chrome 手工验证。

## 8. 源码运行和 HTTP 联调

源码服务命令：

```powershell
python tools\run_server.py --port 8000
```

源码调试若确实需要旧的浏览器密码登录，必须显式使用：

```powershell
python tools\run_server.py --port 8000 --allow-browser-admin-login
```

该参数只用于开发调试；正式 Windows 启动器和免 Python 版本不得传入。正式管理员入口是启动器的“打开管理后台”按钮，浏览器通过一次性 launch ticket 换取内存会话。

然后访问：

- `http://127.0.0.1:8000/`
- `http://127.0.0.1:8000/admin.html`
- `http://127.0.0.1:8000/api/health`

不要用“双击 `index.html`”作为验收方式，因为浏览器页面需要本地服务提供 `/api` 和持久化写入。测试结束使用现有关闭 BAT、启动器关闭按钮或向 `/api/shutdown` 发送 POST，并确认没有遗留服务占用端口。

## 9. Release 和 Windows 检查

构建脚本只接受显式输出目录，版本从 `version.json` 读取：

```powershell
python tools\build_release.py --output .\release-build-v<version>-check
```

只构建源码包或 GitHub Windows 更新包：

```powershell
python tools\build_release.py --output .\release-build-v<version>-source --source-only
python tools\build_release.py --output .\release-build-v<version>-github --github-only
```

Windows 构建要求构建机能找到外部 `pyinstaller`；运行生成的 EXE 不要求安装 Python。构建后至少检查：

1. ZIP 有正确版本名、`update-manifest.json` 和 SHA256 文件；
2. 公开源码/Windows ZIP 不含 `data/`、`public-data/`、`questions.json`、审查、历史、排行榜、答题记录和本机配置；
3. EXE 图标、启动窗口、学生页、后台页和退出服务可用；
4. 以全新目录首次运行能创建空白题库并进入后台；
5. 在同一电脑已有用户数据时升级，题库、排行榜、答题记录和密码保持不变；
6. 更新助手拒绝题库路径、`data`、路径穿越和未在 manifest 声明的文件。

教师私发包必须另行生成和检查，只留在本机或采用安全传输；不能把它复制到 Git staging、GitHub Release 或公开更新资产。

## 10. 手工回归清单

- 空白题库：首页能打开，开始按钮正确禁用，后台能新增/导入第一份题库。
- 单人答题：多选范围、答题、正确自动下一题、错误解析手动下一题、提前交卷、最后一题、到时。
- 反馈：四种 Canvas 效果只播放一次；快速点击、切题、刷新恢复和 200% 字号不串局、不重复计分、不阻塞按钮。
- 排行榜：匿名/命名、同分先提交者优先、范围和计分快照、PK 不入榜。
- 后台：密码登录/退出/改密重登；题目新增/编辑/删除；原句多次考点选词；导入合并/替换/撤销；审查发布；记录折叠、恢复、批量、导入导出；并发 ETag 冲突。
- 持久化：关闭浏览器/服务再启动后数据仍存在；损坏记录不会阻断新答题；备份可看到并按规则轮转。
- PK：双方独立题目状态、比时间/比题数、平局、最后一题同时完成、终场动画和减少动态效果。
- 更新/启动：端口已有其他 HTTP 服务时不误接管；旧本项目服务可被接管；EXE 无 CMD 黑框、图标正确、关闭窗口能停服务。

## 11. Release checklist

1. `git status` 和 `git diff --name-only` 确认改动范围，确认没有私有 `data/` 或教师包。
2. 确认 `version.json` 是本次唯一版本来源；如需发版，先按需求更新它并同步发布说明，不能仅改目录名。
3. 运行 Python 单元测试、两个 Node 测试、JS/Python 语法检查、`git diff --check`。
4. 构建源码/Windows 包，检查 manifest、ZIP 条目、题库排除和 SHA256。
5. 做新目录首次启动、本机已有数据升级和关闭/重启测试。
6. 检查公开仓库内容、GitHub Release 资产和更新资产均不带完整题库、记录、排行榜、审查、历史、密码或本机路径。
7. 记录已测环境、未测项目和回滚方式，再提交单一目的 commit。

## 12. 高风险改变

以下改变必须单独评估并增加回归，不得作为顺手整理：

- 修改 `scoring.js`、计分 snapshot、连击阈值、PK 判胜规则或 deadline；
- 修改 `server_validators.py`、题库 schema、题目 ID/重复身份、导入去重和审查发布状态；
- 修改 `/api/quiz-results`、`/api/pk-results`、管理员鉴权、ETag、幂等或记录/排行榜结构；
- 修改 30 天/100 条记录留存、折叠语义、备份/原子写盘或数据目录；
- 修改 `RUNTIME_*` 发布清单、`safe_relative()`、更新 manifest、更新助手或公开/教师包边界；
- 修改 classic script 加载顺序、global 名称或 orchestrator 状态；
- 把浏览器端逻辑改成新框架、引入第三方运行时或一次性重构大文件；
- 删除兼容 wrapper、旧字段、疑似 dead code 或历史 release 产物。

## 13. 依赖与 CI 建议

当前仓库没有 `requirements.txt` 或 `requirements-dev.txt`。从实际 imports/build script 确认的依赖是：

- 运行服务、测试、更新器和大部分工具使用 Python 标准库；
- `tools/convert_word_question_bank.py` 导入第三方 `python-docx`（模块名 `docx`）；
- `tools/generate_app_icon.py` 导入第三方 Pillow（模块名 `PIL`）；
- Windows release 构建通过 PATH 查找外部 `pyinstaller` 命令；源码没有固定其版本；
- Node 测试只使用 Node 内置 `node:assert/strict`。

建议以后增加一个仅供开发/构建使用的 `requirements-dev.txt`，至少记录 `python-docx`、`Pillow` 和经验证的 `PyInstaller` 版本；本次不创建，避免在没有确定兼容版本时凭印象锁版本。运行产品本身不需要这些包，Windows release 用户也不需要 Python。

当前不存在 `.github/workflows/`。建议以后增加最小 CI，但本次不创建：在 Windows runner 上安装 Python 和 Node，运行上述 Python/Node 测试、JS/Python 语法检查、runtime asset 检查和 `git diff --check`；PyInstaller 全量构建可作为手动 release 或单独 workflow，避免每次普通提交引入构建平台复杂度。
## 题库 Schema v4 开发约束

- `data/questions.json` 是唯一完整题库；审查写入 `workflow.reviews`，重复处理写入 `workflow.duplicateResolutions`。
- 学生使用 `/api/questions` 投影，管理员使用 `/api/admin-question-bank` 完整视图；不要把管理员视图直接返回学生端。
- 普通 JSON 必须使用 `wenyan-question-import` 1.0；导入应先调用 preview，再带 `baseEtag` 调 apply。服务端负责生成新题 ID、分类重复和校验，浏览器不得自行决定合并结果。
- 编辑语义字段后必须让审查回到待审；`targetStart`、`reviewStatus`、`duplicateReview` 等旧字段只能出现在迁移/兼容层或历史快照中，不能写入 v4 canonical question。
