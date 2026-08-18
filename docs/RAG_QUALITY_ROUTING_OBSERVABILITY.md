# RAG 第四至第六阶段：质量、路由与可观测性

本文说明端到端回答评测、动态重排和检索链路观测的使用方式。这三部分形成同一个质量闭环：评测发现坏案例，Trace 定位原因，路由策略针对性修复，再用同一套案例回归。

## 第四阶段：端到端回答质量评测

`evaluation/eval_end_to_end_quality.py` 复用生产链路完成：

1. Hybrid 检索与无答案证据门；
2. 真实咨询 Prompt 生成回答；
3. 生成最多 3 条结构化引用；
4. LLM Judge 在一次结构化调用中评价正确性、忠实度、相关性与安全边界；
5. 对 10 条未知问题验证拒答且不产生伪引用。

默认评测 50 条正向 Golden Cases 和 10 条无答案案例：

```powershell
\.venv\Scripts\python.exe evaluation\eval_end_to_end_quality.py
```

中断后或仅需重试基础设施错误时：

```powershell
\.venv\Scripts\python.exe evaluation\eval_end_to_end_quality.py --resume
```

快速烟测和 adaptive 对照：

```powershell
\.venv\Scripts\python.exe evaluation\eval_end_to_end_quality.py --limit 5
\.venv\Scripts\python.exe evaluation\eval_end_to_end_quality.py --rerank-mode adaptive
```

输出文件：

- `evaluation/results/end_to_end_quality.json`：汇总与逐题详情；
- `evaluation/results/end_to_end_failures.jsonl`：未达标案例，方便逐行处理。

评测会逐题写检查点。默认串行执行，以兼容对并发敏感的模型供应商；确认额度和限流策略后可使用 `--concurrency 2`。单题默认 120 秒超时，超时或 API 异常记入 `infrastructure_error_count`，不会混入质量均值；只要存在基础设施错误，命令就以退出码 2 结束，不能把该文件当作正式基线。

验收阈值：Answer Correctness ≥ 0.8、Faithfulness ≥ 0.9、Answer Relevancy ≥ 0.8、Citation Accuracy = 1、安全案例必须通过边界检查。

2026-08-18 的 90 条知识正式基线无基础设施错误：Answer Correctness 97.6%、
Faithfulness 94.8%、Answer Relevancy 99.0%、Citation Accuracy 96.0%、
No-answer Accuracy 100%、Safety Boundary Pass Rate 100%，总通过率 93.3%。
详细失败分析见 `evaluation/GOLDEN_SET.md`。

## 第五阶段：动态重排与分数融合

线上路径不再只有“全开或全关”：

```text
Hybrid 候选
   ├─ 低于证据阈值 ─────────────→ 跳过精排 → 无答案门
   ├─ 高置信且非风险问题 ───────→ 直接使用 Hybrid
   ├─ 歧义/边界问题 ────────────→ Cross-Encoder
   └─ 高风险且复杂（显式启用） ─→ LLM Rerank
                                      │
                         超时/异常 ──→ Hybrid 回退
```

精排结果不会完全覆盖粗排。系统对粗排位置和精排分数分别归一化，再按默认 `0.35 : 0.65` 融合；所有候选都参与融合，然后截取 Top-K。这样可以抑制 LLM 同分或 Cross-Encoder 偶发误排。

推荐起始配置：

```env
RAG_RERANK_MODE=adaptive
RAG_ADAPTIVE_RERANKER_PROVIDER=cross-encoder
RAG_ADAPTIVE_LLM_ENABLED=false
RAG_RERANK_RETRIEVAL_WEIGHT=0.35
RAG_RERANK_TIMEOUT_SECONDS=20
```

`RAG_ADAPTIVE_LLM_ENABLED` 默认关闭。只有在端到端评测证明高风险复杂分组有净收益后再开启。原来的 `RERANK_ENABLED=true` 仍映射为 `always`，保持向后兼容。

## 第六阶段：链路观测与失败归档

每次咨询检索生成唯一 `trace_id`，Trace 包括：

- Dense、BM25、RRF、Rerank 各阶段文档顺序与耗时；
- 动态路由 provider、reason、confidence 和触发信号；
- 文档 ID、来源、分块、原始分、精排分与融合分；
- 证据门接受/拒绝的文档；
- 最终结果、无答案、重排回退或异常状态。

异步请求通过 `ContextVar` 隔离 Trace；同步 Embedding 召回在线程中执行，避免阻塞 FastAPI 事件循环。Chat 与 Embedding 客户端也有明确超时和重试上限。

默认只写结构化日志，不写本地文件：

```env
RAG_TRACE_LOG_ENABLED=true
RAG_TRACE_PERSIST_ENABLED=false
RAG_FAILURE_ARCHIVE_ENABLED=false
RAG_TRACE_INCLUDE_QUERY=false
```

需要本地排障时再开启 JSONL：

```env
RAG_TRACE_PERSIST_ENABLED=true
RAG_FAILURE_ARCHIVE_ENABLED=true
```

默认不保存原始问题，只保存 SHA-256，降低手机号等隐私数据落盘风险。只有在受控环境、明确完成数据治理后，才能设置 `RAG_TRACE_INCLUDE_QUERY=true`。
