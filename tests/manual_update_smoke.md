# Windows 免 Python 更新手工验证

该清单用于验证真实 Windows EXE 的 `vA → vB` 更新闭环。不要在含真实教师题库的正式目录上做失败测试；先复制一个临时安装目录，并准备临时 `data/`、`%LOCALAPPDATA%/WenyanQuiz/` 数据。

## 成功路径

1. 准备旧版 vA 的免 Python 包，在临时目录旁创建 `data/questions.json`、排行榜、答题记录和管理员设置。
2. 启动 vA，确认学生页、后台、题库和本机数据可读；记录 `/api/health` 返回的 `app` 与 `version`。
3. 将 vB 的公开 Windows ZIP 作为 GitHub Release 资产，确保包含 `update-manifest.json`、主程序 EXE 和更新助手 EXE。
4. 在 vA 后台点击“检查更新”，确认当前版本和最新版本来自服务状态；点击“立即更新”。
5. 观察旧启动窗口优雅关闭，确认安装目录中的更新助手不是直接执行本次覆盖事务，而是从 `%LOCALAPPDATA%/WenyanQuiz/updater-runtime/` 下的随机临时目录运行。
6. 确认 vB 启动后 `/api/health` 返回 `ok: true`、正确的 `app` 和目标 `version`；启动窗口显示目标版本和更新成功提示，后台 Header 也显示同一版本。
7. 确认题库、排行榜、答题记录、管理员设置仍然存在，且 `update-manifest.json` 已是 vB。

## 失败回滚路径

1. 使用临时 vB 包，故意让新版启动后无法通过健康检查，或把测试版启动参数指向不会返回目标版本的临时服务。
2. 从 vA 发起更新，等待健康检查超时。
3. 确认新版进程被停止，旧程序文件和旧 `update-manifest.json` 恢复，新增程序文件被移除。
4. 确认旧版重新启动并通过旧版本健康检查；启动窗口显示“更新失败，已回滚”。
5. 确认题库、排行榜、答题记录和管理员设置没有变化。
6. 检查 `%LOCALAPPDATA%/WenyanQuiz/update-result.json` 被启动窗口消费，`update.log` 记录了目标版本、阶段、异常和回滚结果，但不含密码、token、launch ticket 或题库内容。

## 补充检查

- 重复启动新 EXE 时，旧服务只在 `/api/health` 确认属于本项目时才会被接管。
- 关闭启动窗口后服务结束；浏览器不会因为更新器而自动打开 `admin.html`。
- 更新包不能包含 `data/`、`public-data/`、题库、审查、记录、排行榜或路径穿越内容。
- 回滚和清理失败时保留日志及备份，不扫描或删除安装目录中的未知文件。
