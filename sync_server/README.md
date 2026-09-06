# 题库同步服务器（第一版）

单共享题库空间的协调中心，不是网络磁盘：客户端永远保留完整本机题库，
这里只做认证、revision 串行写入、三方合并、持久冲突与异地备份。

## 启动

```bat
python sync_server/server.py serve --host 0.0.0.0 --port 10001
```

第一版不需要 Windows Service。防火墙只开放选定的同步端口，
不要关闭整个防火墙，不要假设 80/443 可用。

数据目录默认 `./sync-server-data/`（可用 `--data-dir` 指定）：

```text
sync-server-data/
    sync.db            # SQLite（WAL）：用户、共享题库、revision、操作日志、冲突、备份元数据
    backups/           # 手工异地备份文件（服务器命名 bk_<hash>.json）
    db-backups/        # 启动时 SQLite 在线备份（保留最近 7 份）
    sync-server.log    # 运维日志：时间、账号、设备、操作、revision、冲突与错误
```

`sync.db` 与备份不要提交 Git。

## 账号管理（服务器管理员，CLI）

```bat
python sync_server/server.py user add teacher01
python sync_server/server.py user reset-password teacher01
python sync_server/server.py user disable teacher01
python sync_server/server.py user enable teacher01
python sync_server/server.py user list
```

密码交互式输入，不会进入 shell history。客户端没有注册、
改密码、找回密码入口。

## 安全模型

- 无 TLS：密码不明文传输（challenge-HMAC），请求/响应签名防伪造与重放，
  登录限流（单 IP+账号 5 次失败/分钟 → 拒绝 60 秒）。
- 服务器存 salt + 密码派生 key，不存明文密码。
- **题库同步内容没有 TLS 级机密性**，网络观察者可能看到；如需机密性请加 TLS。
- 日志不记密码、key、完整题库；健康接口只返回 ok/service/protocolVersion。

## API（协议版本 1，独立于 app 版本）

- `GET /api/v1/health`（公开，最小）
- `POST /api/v1/auth/challenge` / `POST /api/v1/auth/login`
- `GET /api/v1/sync/snapshot`（首次初始化与灾难恢复）
- `POST /api/v1/sync/bootstrap`（建基线；合并基线需 base_etag 一致）
- `POST /api/v1/sync/push`（operations，2MB 上限，500 条/批）
- `GET /api/v1/sync/changes?after=&limit=`（默认 500 上限）
- `GET /api/v1/sync/conflicts` / `POST /api/v1/sync/conflicts/resolve`
- `POST /api/v1/backup/upload`（50MB 上限，完整 v4 校验后原样保存，不碰 live revision）
- `GET /api/v1/backup/list` / `GET /api/v1/backup/download?id=`

写操作全串行（进程级写锁 + SQLite 事务）；每次 mutation 后 whole-bank
过 v4 validator，非法则整体回滚；operation_id 唯一保证幂等。
