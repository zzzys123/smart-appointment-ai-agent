# RAG 无答案兜底与引用展示

## 目标

向量检索即使面对知识库未覆盖的问题也会返回 Top-K。项目因此不再把“有候选”视为“有证据”，而是在生成前检查 Dense/BM25 原始分；没有候选达到阈值时直接拒答，不调用 LLM，也不生成引用。

## 判定流程

```text
Dense + BM25 召回
        ↓
RRF 排序（只负责融合名次）
        ↓
检查每个候选的原始通道分数
        ↓
dense_score >= 0.60 或 bm25_score >= 8.0 ?
        ├─ 是：保留候选 → LLM 严格依据证据回答 → 附加引用
        └─ 否：固定无答案提示，不调用 LLM，不附引用
```

不能直接对 RRF 分数设置相关度阈值：RRF 只使用排名，所有被召回候选都会得到正分，并不表达向量相似度或关键词匹配强度。实现会在 `retrieval` 元数据中同时保留 `dense_score`、`bm25_score`、各自排名和 `rrf_score`。

## 配置

```dotenv
RAG_NO_ANSWER_ENABLED=true
RAG_DENSE_MIN_SCORE=0.60
RAG_BM25_MIN_SCORE=8.0
```

阈值基于当前 90 条知识和 Qwen `text-embedding-v3` 校准。更换 Embedding 模型、分块参数或业务语料后必须重新评估，不能把当前数值当成通用常数。

运行门控冒烟评估（会调用 Qwen Embedding）：

```powershell
.\.venv\Scripts\python.exe evaluation\eval_no_answer_gate.py
```

当前结果：17/17 条已知问题保留至少一个候选；2/2 条未覆盖问题（游泳池、免费 WiFi）被拒答。样本量很小，只能作为回归冒烟，不代表完整的线上拒答准确率。

## 固定兜底

没有证据时 `ResponseGenerator` 直接返回：

> 抱歉，当前知识库中没有足够的信息回答这个问题。为避免提供不准确的信息，建议您联系门店工作人员进一步确认。

这个路径不会调用聊天模型，避免模型使用参数记忆编造门店事实，也节省一次生成费用。

## 引用协议

有证据且生成成功时，后端在回答流末尾追加：

```text
[SOURCES][咨询机器人][{"source_id":"KB-SERVICE-001",...}]
```

每个来源最多包含：

- `source_id`
- `source_name`
- `title`
- `category`
- `chunk_number`（面向用户，从 1 开始）
- `chunk_count`
- `document_id`

React 增量解析流时只把 `[REPLY]` 显示为答案，把 `[SOURCES]` 渲染为独立的“参考来源”区域。JSON 可能被网络分片截断，解析器会等待下一块数据后重试，不显示半截引用。

引用表示“这些候选被用于生成”，不等同于句子级事实归因。进一步提升可做：句子到证据的精确绑定、可点击展开原文、引用正确率评估和管理员反馈闭环。
