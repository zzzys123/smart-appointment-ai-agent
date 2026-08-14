# Redis 集成说明

Redis 在本项目中负责临时状态和跨进程协调，SQLite/SQL 数据库仍然是预约业务数据的最终数据源。

## 功能

- 按 `session_id` 隔离并保存预约对话草稿，默认 TTL 为 1 小时。
- 同一会话请求串行执行，避免用户重复点击造成 Agent 状态竞争。
- 聊天接口按会话限流，默认每分钟 10 次。
- 预约写入前获取技师分布式锁，并在锁内重新检查时间冲突。
- 相同会话、技师和时间段的重复预约使用幂等键去重。

Redis 不可用时，应用会记录警告并退化为单进程内存锁。该模式适合本地开发，但不能为多个应用实例提供互斥保证。

## 本地启动

```bash
docker compose up -d redis
```

在 `.env` 中启用：

```dotenv
REDIS_ENABLED=true
REDIS_URL=redis://localhost:6379/0
```

然后正常启动 FastAPI 应用。启动日志出现 `Redis coordination is enabled` 表示连接成功。

## Key 结构

```text
smart-appointment:session:{session_id}
smart-appointment:lock:chat:{session_id}
smart-appointment:rate_limit:chat:{session_id}
smart-appointment:lock:appointment:{technician_id}:schedule
smart-appointment:idempotency:{sha256}
```

浏览器将会话 ID 保存到 `localStorage`，并通过请求体和 `X-Session-ID` 请求头发送。点击“清空对话”会生成新的会话 ID。

## 一致性边界

Redis 锁关闭了应用层的“先查空闲、后写入”竞态窗口，但数据库仍应负责最终一致性。若部署为高并发生产系统，建议将 SQLite 升级为 PostgreSQL，并增加数据库级时间段排他约束。
