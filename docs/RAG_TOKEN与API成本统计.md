# RAG Token 与 API 成本统计

本阶段为模型调用增加统一用量统计，目标是让 Golden 和后续线上观测同时具备质量、延迟、成本
三类数据。统计器不保存 Prompt、答案或 API Key，只保存聚合数字。

## 统计范围

| 阶段 | 统计方式 | 说明 |
| --- | --- | --- |
| `answer` | 服务端响应 `usage` | RAG 回答生成的输入、输出和总 Token |
| `judge` | 服务端响应 `usage` | Golden 的 LLM Judge，不属于线上用户回答成本 |
| `llm_rerank` | 服务端响应 `usage` | 只有启用 LLM Rerank 时产生 |
| `chat` | 服务端响应 `usage` | 未进一步分类的聊天模型调用 |
| `embedding` | 字符数估算 | LangChain Embedding 接口只返回向量，不暴露服务端 usage |

Embedding 默认用 `字符数 / 2.0` 估算 Token，并在结果中标记
`token_source=estimated_from_characters`。它适合成本趋势观察，不应冒充供应商账单的精确值。可通过
`EMBEDDING_ESTIMATED_CHARS_PER_TOKEN` 调整比例。

## 费用配置

价格经常变化，因此仓库不硬编码 Qwen、DeepSeek 或 OpenAI 单价。请根据当前供应商账单配置：

```env
LLM_INPUT_CNY_PER_1M_TOKENS=
LLM_OUTPUT_CNY_PER_1M_TOKENS=
EMBEDDING_CNY_PER_1M_TOKENS=
```

单位均为“人民币元 / 100 万 Token”。缺少本次运行需要的任一单价时，结果会写入：

```json
{
  "status": "pricing_not_configured",
  "total_cny": null,
  "cny_per_case": null
}
```

系统不会把未知价格当成 0。若只能从控制台获得整次运行账单，可以继续通过 Golden 命令的
`--estimated-cost-cny` 写入人工核对后的总费用；结果会同时保留自动统计摘要。

线上咨询默认输出一条 `rag_model_usage` 结构化日志。可选持久化配置：

```env
MODEL_USAGE_LOG_ENABLED=true
MODEL_USAGE_PERSIST_ENABLED=false
MODEL_USAGE_PATH=data/observability/model_usage.jsonl
```

事件只包含 Trace ID、Session SHA-256、分阶段聚合用量和费用状态，不包含原始问题、Prompt、答案
或原始 Session ID。开启 JSONL 后可离线汇总单次咨询费用、失败调用率及 Token 分布。

## Golden 输出

以后通过 `evaluation/run_golden_v2.py` 运行的小范围 Dev 冒烟或正式候选，会在 JSON 中增加：

- `usage.stages`：各阶段调用次数、失败次数与 Token；
- `usage.totals`：本次运行聚合用量；
- `cost`：计算状态、总费用和单例费用；
- Embedding 字符数及估算 Token，便于识别估算误差。

使用 `--resume` 时会把上一次已保存的 usage 与本次重试合并，因此成本包含失败尝试和重试产生的
全部已知调用；不会只保留最后一次运行。如果供应商成功返回结果但完全没有 Token usage，费用状态
会是 `usage_not_reported`，同样不会按 0 元处理。

已经冻结的 2026-08-19 Golden v2 基线不会为了补 Token 数据而重跑。其成本继续保持
`not_collected`。下一次候选评测先用少量 Dev 用例验证 usage，再决定是否建立带成本的新基线。

只发起一次短 Chat 和一次 Embedding 的冒烟命令如下；它不运行 Golden 用例，但会产生两次很小的
API 调用：

```powershell
.venv\Scripts\python.exe evaluation\smoke_model_usage.py
```

## 隐私与限制

- 统计器不记录 Prompt、问题和回答正文；
- 回调只聚合模型返回的 usage 元数据；
- 重试是否计费由供应商决定，本地只能记录 LangChain 最终回调和失败次数；
- Embedding 调用次数是逻辑请求数，SDK 内部若再次拆批，可能和供应商 HTTP 请求数不同；
- 最终财务核对应以供应商账单为准。
