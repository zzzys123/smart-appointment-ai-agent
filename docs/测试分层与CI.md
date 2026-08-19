# 测试分层与 CI

项目测试分为三层，目的是让日常回归稳定、快速且不产生模型 API 费用，同时仍保留真实链路验证入口。

## 测试层级

| 标记 | 用途 | 默认执行 |
|---|---|---|
| `unit` | 纯函数、单模块和 Mock 隔离测试 | 是 |
| `integration` | SQLite、本地服务或多个模块组成的确定性测试 | 是 |
| `online` | 可能调用真实 LLM、Embedding 或外部服务的测试 | 否 |

没有显式标记的旧测试会暂时归入 `unit`。新增测试应主动选择最符合其边界的标记。

## 本地命令

```powershell
# 默认回归：执行全部 unit + integration，跳过 online
.\.venv\Scripts\python.exe -m pytest -q

# 分层执行
.\.venv\Scripts\python.exe -m pytest -q -m unit
.\.venv\Scripts\python.exe -m pytest -q -m integration

# 显式运行真实模型测试；需要有效 API Key，可能产生费用
.\.venv\Scripts\python.exe -m pytest -q -m online --run-online
```

`--run-online` 是费用保护开关。只有 `-m online` 而没有该开关时，测试仍会被跳过。

## CI 门禁

GitHub Actions 在 `main`、`feat/**`、`agent/**` 的 Push，以及面向 `main` 的 Pull Request 上执行：

1. 完整离线 Python 测试，并列出未执行的在线测试。
2. Golden v2 文件与冻结基线完整性校验。
3. 知识基线校验：离线测试固定 10 条内置知识，清单固定 10 篇源文档生成的 80 个分块，合计 90 条。
4. React/TypeScript 生产构建。
5. Gitleaks 仓库历史密钥扫描。
6. Docker Compose 配置校验与镜像构建。

在线测试不作为普通 PR 的强制门禁。需要验证真实模型链路时，由开发者在有密钥和预算控制的环境中显式执行并保存评测结果。

当前本地确定性快照为 **90 passed、15 skipped**；15 条均为显式保留的在线测试。

## 知识库清单校验

```powershell
.\.venv\Scripts\python.exe scripts\manage_knowledge.py verify-manifest
```

该命令只比较 `knowledge_documents/MANIFEST.json` 与版本库中的 10 篇知识文档、80 个分块哈希和数量；发现缺失、内容漂移或分块变化时返回非零退出码，适合 CI 使用。另有离线单元测试固定 10 条内置知识的数量与基本字段。
