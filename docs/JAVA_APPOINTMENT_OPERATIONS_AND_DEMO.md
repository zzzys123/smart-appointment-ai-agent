# Java 预约链路启动、测试与故障演示

## 1. 演示目标

这套步骤用于证明完整调用链已经真实运行：

```text
React → FastAPI / LangGraph → AppointmentGateway
      → Spring Boot → MySQL
```

其中 Python 负责自然语言理解和流程编排，Java 负责技师档期、预约事务、幂等和最终冲突裁决。

## 2. 一键启动

在仓库根目录执行：

```powershell
docker compose up -d --build
docker compose ps
```

四个服务都应显示 `healthy`：

```text
app
appointment-service
mysql
redis
```

访问地址：

- React 工作台：<http://127.0.0.1:8000/ui/>
- Python 健康检查：<http://127.0.0.1:8000/health>
- Java Swagger UI：<http://127.0.0.1:8080/swagger-ui.html>
- Java 健康检查：<http://127.0.0.1:8080/actuator/health>

检查 Python 容器实际使用的预约后端和 PyTorch 构建：

```powershell
docker compose exec app python -c "import os, torch; print(os.getenv('APPOINTMENT_BACKEND')); print(os.getenv('JAVA_APPOINTMENT_BASE_URL')); print(torch.__version__); print(torch.cuda.is_available())"
```

预期包含：

```text
java
http://appointment-service:8080
2.6.0+cpu
False
```

## 3. Swagger 正常预约演示

先在 Swagger 调用：

```http
GET /internal/v1/technicians
```

记下一位技师 ID，然后调用：

```http
GET /internal/v1/technicians/available
```

示例参数：

```text
startTime = 2033-07-08T14:00:00
durationMinutes = 60
```

确认技师可用后调用：

```http
POST /internal/v1/appointments
Idempotency-Key: swagger-stage4-demo-001
X-Trace-Id: swagger-stage4-created
```

```json
{
  "userId": "swagger_user",
  "sessionId": "swagger-stage4-session",
  "technicianId": 1,
  "serviceName": "肩颈按摩",
  "startTime": "2033-07-08T14:00:00",
  "durationMinutes": 60
}
```

首次提交应返回 HTTP `201` 和预约号。

## 4. 幂等重放演示

不修改请求体和 `Idempotency-Key`，只把 `X-Trace-Id` 改为：

```text
swagger-stage4-replayed
```

再次执行。预期：

- HTTP `200`
- 预约号与第一次相同
- MySQL 不新增第二笔预约

幂等键解决的是“同一个业务请求被重复提交”。

## 5. 并发冲突演示

保持技师、开始时间和时长不变，把请求改成另一个业务请求：

```text
Idempotency-Key: swagger-stage4-conflict-001
X-Trace-Id: swagger-stage4-conflict
sessionId: swagger-stage4-other-session
```

预期返回：

```text
HTTP 409
code = APPOINTMENT_SLOT_CONFLICT
```

不同幂等键代表不同请求。此时由 MySQL 唯一时间片约束保证同一技师同一时段最多一个请求成功。

## 6. 查看链路日志

```powershell
docker compose logs --tail=100 appointment-service
```

按 trace 筛选：

```powershell
docker compose logs --no-color appointment-service |
  Select-String "swagger-stage4-"
```

成功、重放和冲突日志分别包含：

```text
traceId
sessionId
technicianId
appointmentNo
outcome=created|replayed
reason（冲突原因）
```

日志不记录用户输入、API Key 或完整请求正文。

## 7. Java 故障演示

临时停止 Java 服务：

```powershell
docker compose stop appointment-service
```

此时通过聊天页面提交完整预约，Agent 应提示：

```text
预约服务暂时无法连接，本次没有创建预约。
```

不得出现“预约成功”，Python 也不会自动写回 SQLite。这样可以避免 Java 和 Python 各自保存一份互相不一致的预约。

恢复 Java：

```powershell
docker compose start appointment-service
docker compose ps appointment-service
```

等待状态恢复为 `healthy` 后可以继续预约。

## 8. 自动化测试

Python 完整离线回归：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

只运行阶段三/四网关测试：

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_appointment_gateway.py `
  tests/test_appointment_gateway_agent.py -q
```

本机没有 Maven 时，通过 Docker 运行 Java + MySQL Testcontainers：

```powershell
docker run --rm `
  -e TESTCONTAINERS_HOST_OVERRIDE=host.docker.internal `
  -v "${PWD}:/workspace" `
  -v "appointment-maven-cache:/root/.m2" `
  -v "/var/run/docker.sock:/var/run/docker.sock" `
  -w /workspace/backend-java `
  maven:3.9.9-eclipse-temurin-21-alpine mvn -B test
```

当前验收基线：

- Python：`98 passed, 15 skipped`
- Java：`19 passed, 0 failed, 0 skipped`

## 9. 停止与数据安全

停止容器但保留 SQLite、MySQL 和 Redis 数据：

```powershell
docker compose down
```

不要把下面的命令当作普通停止命令：

```powershell
docker compose down -v
```

`-v` 会删除 Compose 持久化卷，仅在明确需要清空所有演示数据时使用。

## 10. 常见问题

### Java Swagger 打不开

```powershell
docker compose ps appointment-service mysql
docker compose logs --tail=100 appointment-service
```

### Python 提示预约服务不可用

确认 Compose 中的配置是：

```text
APPOINTMENT_BACKEND=java
JAVA_APPOINTMENT_BASE_URL=http://appointment-service:8080
```

容器内不能使用 `127.0.0.1:8080` 访问另一个容器。

### 预约总是返回 400

检查以下规则：

- 开始时间位于未来
- 开始分钟为 `00` 或 `30`
- 秒和微秒为 `0`
- 时长为 30～480 分钟且是 30 的整数倍

### 镜像为什么使用 CPU PyTorch

容器内的 Cross-Encoder 不是默认必开能力，完整演示不依赖 NVIDIA GPU。使用 CPU wheel 可以避免把 CUDA Toolkit、cuDNN 和 NCCL 等大型运行库装入镜像；如果未来明确采用 GPU 部署，应单独维护 GPU 镜像，而不是让同一个镜像隐式猜测硬件环境。
