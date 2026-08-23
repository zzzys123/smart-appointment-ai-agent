# Appointment Service

Spring Boot 预约领域服务。当前已提供技师查询、指定时段可用性查询，以及带事务、幂等和并发冲突控制的预约创建能力；Python Agent 已支持通过配置在本地 SQLite 实现和该 Java 服务之间切换。

## 本地启动

先启动 MySQL：

```powershell
docker compose up -d mysql
```

使用 Java 21 和 Maven 3.9+ 启动：

```powershell
cd backend-java
mvn spring-boot:run
```

也可以从仓库根目录同时启动 Java 服务和 MySQL：

```powershell
docker compose up --build appointment-service mysql
```

MySQL 容器默认映射到宿主机 `127.0.0.1:3307`，避免与本机已有的 MySQL `3306` 端口冲突；容器内部仍使用 `3306`。如需更换宿主机端口，可设置 `MYSQL_HOST_PORT`。

可用地址：

- Swagger UI：<http://127.0.0.1:8080/swagger-ui.html>
- OpenAPI JSON：<http://127.0.0.1:8080/v3/api-docs>
- 健康检查：<http://127.0.0.1:8080/actuator/health>

## 只读接口

```http
GET /internal/v1/technicians
GET /internal/v1/technicians/{id}
GET /internal/v1/technicians/available?startTime=2026-08-24T14:00:00&durationMinutes=60
```

可用性接口还支持可选的 `gender` 和 `strength` 查询参数。时长范围为 30～480 分钟，并且必须是 30 分钟的整数倍。

## 创建预约

```http
POST /internal/v1/appointments
Idempotency-Key: <唯一请求键>
Content-Type: application/json
```

```json
{
  "userId": "default_user",
  "sessionId": "session-123",
  "technicianId": 1,
  "serviceName": "肩颈按摩",
  "startTime": "2026-09-30T10:00:00",
  "durationMinutes": 60
}
```

开始时间必须位于未来，并对齐到整点或半点。首次创建成功返回 HTTP `201`；使用相同 `Idempotency-Key` 重复提交会返回原预约且不重复写库，HTTP 状态为 `200`；不同请求争抢同一技师的重叠时段会返回 HTTP `409` 和错误码 `APPOINTMENT_SLOT_CONFLICT`。

预约写入使用 Spring 事务，并将持续时间拆分为 30 分钟时间片。MySQL 唯一约束 `(technician_id, slot_start)` 是并发冲突的最终防线；数据库中的 `idempotency_key` 唯一索引负责防止重复提交生成多笔预约。

## Python Agent 接入

直接在宿主机启动 Python 时，可以在 `.env` 中选择预约后端：

```env
APPOINTMENT_BACKEND=java
JAVA_APPOINTMENT_BASE_URL=http://127.0.0.1:8080
JAVA_APPOINTMENT_TIMEOUT_SECONDS=3
```

切回原来的 Python + SQLite 实现：

```env
APPOINTMENT_BACKEND=local
```

Docker Compose 会把 Java 服务地址设置为 `http://appointment-service:8080`；如果没有显式设置 `APPOINTMENT_BACKEND`，Compose 中的 Python 服务默认选择 `java`。

Java 模式下，技师列表、档期查询和最终预约写入都走 `AppointmentGateway`，Python 不再写本地预约排班。Java 返回 `409` 时，Agent 会提示时段刚被占用并保留已填写信息；超时或连接失败时会明确说明本次没有创建预约。系统不会在 Java 故障后自动改写 SQLite，避免产生两个互相不一致的预约事实源。

## 测试

```powershell
cd backend-java
mvn test
```

Java 测试包含 H2 Repository 测试、MockMvc Controller 测试，以及通过 Testcontainers 启动 MySQL 8.4 的真实数据库事务与并发测试。运行完整 Java 测试需要 Docker 可用。

Python 网关契约与 Agent 错误映射测试从仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_appointment_gateway.py `
  tests/test_appointment_gateway_agent.py -q
```

完整的一键启动、Swagger 幂等/冲突演示、trace 日志和 Java 故障恢复步骤见 [`../docs/JAVA_APPOINTMENT_OPERATIONS_AND_DEMO.md`](../docs/JAVA_APPOINTMENT_OPERATIONS_AND_DEMO.md)。

本机没有安装 Maven 时，也可以在 `backend-java` 目录使用 Maven 容器执行：

```powershell
docker run --rm `
  -e TESTCONTAINERS_HOST_OVERRIDE=host.docker.internal `
  -v "${PWD}:/workspace" `
  -v "appointment-maven-cache:/root/.m2" `
  -v "/var/run/docker.sock:/var/run/docker.sock" `
  -w /workspace `
  maven:3.9.9-eclipse-temurin-21-alpine mvn -B test
```
