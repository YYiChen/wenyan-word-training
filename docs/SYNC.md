# 远程题库实时同步与异地备份（第一版）

适用：办公室电脑、教室电脑、讲台可信学生电脑（2～3 台）共同审同一份题库。
不同步学生答题、成绩、排行，只同步题库本身。

## 架构一句话

```text
questions.json
     ↑↓ (shadow diff → operations)
sync shadow/diff
     ↑↓ (后台 sync worker, 约 2 秒一轮, push 先于 pull)
local Python sync worker
     ↑↓ (challenge-HMAC 认证 + 请求签名)
remote sync server
     ↑↓
SQLite current snapshot + revision log

另一路（与同步完全分开）：

questions.json
     ↓ (手动上传完整 JSON)
remote immutable backup file
```

## Local-first（最高不变量）

- 每台电脑永远保留完整 `data/questions.json`；服务器宕机、断网都不影响答题、PK、审查、排行榜。
- 同步默认关闭；旧版升级后行为与过去完全一样，不会要求登录，不会自动上传。
- 所有本机修改先写本地、UI 立即成功；后台 worker 再把变化送出去。
- 学生浏览器只连 localhost，从不直连远程服务器。

## 日常同步是增量，不是整库上传

- worker 每轮：确认会话 → 比较 shadow 与本机 → 先 push 本机 operations → 再 pull 服务器 changes → 落盘 → 更新 shadow 与 lastRevision。
- 完整题库上传只用于“异地备份”与首次初始化，不参与日常同步。
- operationId 由（clientId + 实体 + base/new 哈希）确定性生成；服务器已接受但客户端崩溃未更新 shadow 时，重发是幂等 no-op。

## 冲突规则（与手工导入一致）

- pending 最低：单边 pending 自动采用另一边的人工结论。
- 相同非 pending status：不是冲突，保留服务器整体。
- 不同非 pending status：生成服务器持久冲突，不 last-write-wins。
- 题目内容双方各改：生成内容冲突，服务器保持原值，本机保留本机修改。
- 有未解决冲突的题目在所有同步客户端的学生题池中暂时不可答（`availability.reason = sync_conflict`，派生值，不写入题库）。
- 解决冲突后服务器产生新 revision，各端 pull 到 resolved 事件后解除屏蔽。

## 首次连接三种情况

1. 服务器空：确认后把本机题库作为共享基线上传（revision 从 1 开始，保留本机 bankId）。
2. 本机空：先备份本机，再下载服务器快照写入本机。
3. 两边都有：必须先预览合并（复用 v4 merge 引擎，以服务器为共享基准、保留服务器 bankId）；审查冲突必须逐项明确选择服务器/ 本机，选“稍后处理”则暂停本次连接，不启用同步。

## 安全说明（必读）

- 密码不明文传输：challenge-response（PBKDF2-SHA256 派生 key + HMAC 证明），请求带单调序号与 body 哈希签名，防伪造、防重放；响应同样签名。
- 服务器只存 salt + 密码派生 key，不存明文密码；登录失败 5 次/分钟锁定 60 秒。
- **没有 TLS，题库同步内容不具备 TLS 级网络机密性。** 如需机密性，未来加 TLS。
- Windows 打包版凭据（派生的 auth material）经 DPAPI 加密存 `sync-credential.bin`；源码版/非 Windows 只放进程内存，重启后重输密码。
- 同步配置、shadow、冲突缓存里不出现密码与题库正文之外的秘密；`sync.log` 不记密码、key、完整 bank。

## 异地备份

- 后台“同步与备份”卡片 → 上传当前完整题库备份：只写备份文件与元数据，不碰共享题库、不增加 revision。
- 备份列表显示时间、题数、bankId、四种审查计数、大小、上传设备；下载只给文件，不自动导入本机。

## 文件位置（本机）

- `%LOCALAPPDATA%/WenyanQuiz/sync-settings.json`：开关、地址、端口、账号、clientId、设备名、lastRevision（无密码）。
- `%LOCALAPPDATA%/WenyanQuiz/sync-shadow.json`：最后确认一致的题库快照（同步基线）。
- `%LOCALAPPDATA%/WenyanQuiz/sync-state.json`：屏蔽题、冲突抑制、状态快照。
- `%LOCALAPPDATA%/WenyanQuiz/sync-credential.bin`：DPAPI 加密的登录材料。
- `%LOCALAPPDATA%/WenyanQuiz/sync.log`：同步日志。

以上文件都不进入 Git、Release 与更新包。

## 运维注意

- bankId 漂移（本机题库被完整替换）会暂停自动同步，需重新初始化；同步启用期间“导入并替换”被阻止，需先断开同步。
- 普通“新增导入题库（合并）”允许，合并结果经 diff 自动同步，并提示“本地保存后同步到共享服务器”。
- 服务器重启不丢数据（SQLite 持久化），客户端自动重连；更新到未来版本前，v4 题库完整保留，同步默认关闭。
