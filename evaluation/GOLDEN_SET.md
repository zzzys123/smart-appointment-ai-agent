# RAG Golden Set v2（已冻结）

## 版本冻结说明

- 版本号：`golden-v2`
- 冻结日期：2026-08-18
- 用例规模：50 条有答案 + 10 条无答案，共 60 条
- 配套知识基线：90 条活动知识记录
- 完整性清单：`GOLDEN_V2_MANIFEST.json`
- 哈希口径：文本换行先统一为 LF，再计算 SHA-256，避免 Windows/Linux 误差
- 端到端通过阈值：Answer Correctness ≥ 0.8、Faithfulness ≥ 0.9、
  Answer Relevancy ≥ 0.8、Citation Accuracy = 1.0；安全案例还要求
  Safety Boundary Score ≥ 0.8。

Golden v2 不要求 60/60。验收重点是无基础设施错误、安全与无答案指标不退化、
总体质量相对基线非劣，以及延迟和调用成本可接受。当前用例、评分器或关键 Prompt
发生任何实质变化时，必须创建新版本并更新校验清单，不能继续沿用 `golden-v2`。

运行以下命令可校验冻结文件、知识源和用例数量：

```powershell
.venv\Scripts\python.exe evaluation\verify_golden_v2.py
```

本页后文保存的 2026-08-18 Qwen-Plus 结果属于冻结前的历史基线。由于 Golden v2
统一了生成器与 Judge 的证据上下文，并把安全判断规范为 0-1 分数，因此历史结果
可用于方向参考，但不能与 Golden v2 新结果做严格的逐点提升声明。

当前评测集与 90 条活动知识记录配套使用：

- `document_chunking_cases.json`：50 条有答案用例，必须命中指定
  `source_id` 且具体分块包含 `expected_contains` 才算证据命中。
- `no_answer_cases.json`：10 条知识库没有答案的查询，用于校准无答案门槛。
- 语料规模：10 条内置默认知识 + 10 篇源文档生成的 80 个分块。

## 用例分组

| 分组 | 数量 | 目的 |
| --- | ---: | --- |
| `dense_semantic` | 10 | 口语、隐喻和语义改写 |
| `bm25_exact` | 12 | 工号、编码、金额、地址和距离 |
| `rerank_interference` | 12 | 价格、体验、安全、流程等相似文档干扰 |
| `chunking_cross_section` | 10 | 局部规则与跨章节业务信息 |
| `policy_boundary` | 6 | 医疗、支付、隐私和承诺边界 |

## 2026-08-18 基线

| 策略 | Evidence R@1 | R@3 | R@5 | R@10 | MRR@10 | 平均延迟 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Dense | 72.0% | 92.0% | 94.0% | 96.0% | 0.824 | 约 0.88s |
| Hybrid | 78.0% | 94.0% | 96.0% | 100.0% | 0.867 | 约 0.87s |
| Hybrid + `BAAI/bge-reranker-base`（CPU） | 78.0% | 94.0% | 96.0% | 98.0% | 0.867 | 约 11.85s |
| Hybrid + LLM Rerank | 72.0% | 90.0% | 96.0% | 100.0% | 0.827 | 约 17.11s |

Cross-Encoder 在 `rerank_interference` 分组将 MRR 从 0.762 提升到 0.813，
在 `chunking_cross_section` 分组从 0.837 提升到 0.950；但
`dense_semantic` 从 0.833 降到 0.711。该零样本模型当前保留为可选策略，默认
不开启。下一步应使用本 Golden Set 构造 hard negatives 做领域微调，或按查询类型
选择性启用重排。

LLM Rerank 相对 Hybrid 提升 8 条、下降 10 条、其余 32 条排名不变。
`chunking_cross_section` 的 MRR 从 0.837 提升到 0.883，
`rerank_interference` 从 0.762 提升到 0.781；但 `dense_semantic` 从 0.833
下降到 0.567。当前 LLM 与 Cross-Encoder 都不适合全量默认启用，Hybrid 是总体
质量和延迟更平衡的默认策略。

无答案 Dense 阈值由 0.60 调整为 0.66 后，50/50 条已知查询被保留，10/10 条
未知查询被拒绝。替换 Embedding 模型或知识语料后必须重新校准，不能沿用该数值。

## 2026-08-18 端到端回答基线

正式基线使用 90 条知识、Hybrid、`rerank_mode=off`、Top-3 上下文，覆盖 50 条
有答案案例和 10 条无答案案例。所有 60 个案例均获得有效结果，
`infrastructure_error_count=0`。

| 指标 | 结果 |
| --- | ---: |
| Answer Correctness | 97.6% |
| Faithfulness | 94.8% |
| Answer Relevancy | 99.0% |
| Citation Accuracy | 96.0% |
| No-answer Accuracy | 100.0% |
| Safety Boundary Pass Rate | 100.0% |
| 全部案例通过率 | 93.3%（56/60） |
| 有答案案例通过率 | 92.0%（46/50） |
| 平均离线评测延迟 | 18.19s |
| P95 离线评测延迟 | 69.44s |

离线延迟包含检索、一次回答生成和一次 LLM Judge，不代表线上用户回答延迟。
模型服务存在明显长尾，因此评测器使用逐题检查点、基础设施错误隔离和
`--resume` 断点续跑；Judge 使用 JSON Mode、关闭 Qwen 思考模式并限制输出，
避免结构化工具调用在边缘案例中持续生成。

分组通过率：`policy_boundary` 100%，`bm25_exact` 91.7%，
`rerank_interference` 91.7%，`dense_semantic` 90%，
`chunking_cross_section` 90%。4 个真实失败为：

- `exact_technician_008`：答案虽声明不保证改善睡眠，但“助眠效果较好”仍比证据更强；
- `semantic_full_body_avoid_area`：把“用户可要求避开足部”写成服务必然“会避开”；
- `rerank_member_priority_boundary`：未命中会员不能占用已确认排班的政策证据；
- `chunk_combo_overtime`：编造按分钟折算加钟费用和前台确认流程。

逐题结果见 `results/end_to_end_quality.json`，失败归档见
`results/end_to_end_failures.jsonl`。

## 运行方式

```powershell
.venv\Scripts\python.exe evaluation\eval_document_chunking.py `
  --json-output evaluation\results\document_chunking.json

.venv\Scripts\python.exe evaluation\eval_retrieval_strategies.py `
  --json-output evaluation\results\retrieval_strategies.json

.venv\Scripts\python.exe evaluation\eval_reranker_strategies.py
.venv\Scripts\python.exe evaluation\eval_no_answer_gate.py
.venv\Scripts\python.exe evaluation\eval_end_to_end_quality.py
```

长任务中断或存在 `infrastructure_error` 时，使用
`evaluation\eval_end_to_end_quality.py --resume` 仅重试基础设施错误和未完成案例；
真实质量失败会保留，不会因反复裁判而被覆盖。

`eval_reranker_strategies.py --include-llm` 会把候选知识文本发送到配置的 Chat API，
并产生额外调用成本；默认只运行 Dense、Hybrid 和本地 Cross-Encoder。
