# 文言实词限时训练开发约束

本文件是本仓库中 AI 和人工开发者的第一阅读入口。它描述的是当前代码的硬边界，不是未来重构目标。

## 项目底线

- 技术栈是原生 HTML/CSS/JavaScript classic scripts、Python 标准库本地 HTTP 服务和 JSON 文件；没有前端框架、数据库或云端后端。除非需求明确要求，不引入 React/Vue、打包器、动画库或重型依赖。
- 先读 `docs/ARCHITECTURE.md`、`docs/INVARIANTS.md`、`docs/DEVELOPMENT.md`，再改代码。正确性、数据安全和现有行为优先于架构形式上的优雅。
- 一次需求只修改直接相关的模块；不要借功能开发顺手重构、改 UI 风格、改计分、改题库格式或清理历史代码。

## 模块 ownership

| 范围 | 归属模块 | 编排入口的边界 |
| --- | --- | --- |
| 学生单人答题 | `student-quiz.js`、`student-shared.js`、`student-records.js` | `app.js` 只负责全局状态、启动加载和入口协调，不继续堆单题业务 |
| 学生 PK | `student-pk.js`、`pk-finish-effects.js`、`pk-finish-effects.css` | 不把 PK 状态或终场效果塞回 `app.js` |
| 学生公共效果/规则 | `scoring.js`、`question_identity.js`、`feedback-effects.js` | 计分和题目身份规则只能有一个实现来源 |
| 管理后台 | `admin-*.js` | `admin.js` 只负责 shell、共享状态、tab 分发、加载和事件路由 |
| 本地服务 | `server_*.py`、`run_server.py` | `run_server.py` 负责 HTTP 路由、锁、启动初始化和兼容导出；验证、持久化、题库/记录业务放入对应 service |
| 启动/更新/发布 | `launcher.py`、`update_service.py`、`update_helper.py`、`build_release.py` | 启动生命周期、更新安全和发布白名单分开维护 |

## 新代码和 God File 规则

- 新学生功能优先放入对应 `student-*.js`；新后台功能优先放入对应 `admin-*.js`；新服务端业务优先放入对应 `server_*.py`。入口文件只增加最小调用和状态连接。
- 当前 classic script 依靠共享 global scope。新增全局名称必须唯一，并放在实际加载顺序允许的位置；不要通过隐式执行顺序制造新的循环依赖。
- 软性警戒线：`app.js`/`admin.js`/`run_server.py` 不应因单项功能明显膨胀；超过约 50-80 行的新业务、或向已超过 800 行的 cohesive 模块继续添加一大段逻辑时，应先评估抽出 service/module。警戒线用于 code review，不是为了本次任务强行拆文件。
- `admin-questions.js`、`student-quiz.js`、`server_validators.py` 等大文件目前仍是有边界的 cohesive module；不要只按行数机械拆分。

## 状态、API 和数据

- 只由 owner 修改共享状态；不要在多个 classic script 中各自复制 `bank`、session、auth token 或同一业务规则。
- API 和 JSON schema 默认向后兼容：新增字段应可选并有旧数据默认值；旧字段、旧记录和旧接口只有在明确迁移方案、测试和版本说明后才能移除。
- 本地数据属于用户：题库、审查记录、题库历史、排行榜、答题记录和管理员设置不能写入公开源码包、公开 release 或更新包。不要把密码、token、本机路径或真实题库提交 Git。
- `version.json` 是运行时、服务端和发布脚本的版本唯一来源。HTML 查询串中的缓存破坏参数不是版本真相，不能另建版本常量。
- 发布新增运行文件时，必须同步更新 `tools/build_release.py` 的 `RUNTIME_WEB_FILES` 或 `RUNTIME_PYTHON_FILES`，并通过 runtime asset 测试；不能依赖“目录里有文件”自动进入 release。

## dead code、测试和 diff

- 删除疑似 dead code 前必须 grep 全仓库引用、检查 HTML 加载、发布清单、兼容导出和测试，并单独说明删除理由；本次需求未授权时保留，不顺手删除。
- 改动后至少运行 `python -m unittest discover -s tests -q`、两个 Node 测试、JS `node --check`、Python `py_compile` 和 `git diff --check`；UI、服务启动、发布包或更新器改动还要做对应手工回归。
- 提交前检查 `git diff --name-only`，确保没有意外修改题库、`data/`、release 产物或本机配置。小 diff、单一目的、可回滚，比“大一统重构”更重要。

详细架构、业务契约和操作流程分别见 `docs/ARCHITECTURE.md`、`docs/INVARIANTS.md`、`docs/DEVELOPMENT.md`。
