# RAG 新旧检索方式对比报告

> 本文保留 2026-08-14 的 17 条初始实验记录。当前 50 条严格证据、10 条
> 无答案及 Cross-Encoder 对比结果以
> [`evaluation/GOLDEN_SET.md`](../evaluation/GOLDEN_SET.md) 为准。

测试日期：2026-08-14

## 1. 结论

当前新增知识库已经完成核心回归测试、离线分块评估，以及基于真实数据库和真实 Qwen Embedding 的检索策略对比。

在本次 17 条严格证据用例上，新方案 **Hybrid（Dense + BM25 + RRF）** 相比旧方案 **Dense（纯向量）**：

- Evidence Recall@1：88.2% → 88.2%，持平；
- Evidence Recall@3：94.1% → 94.1%，持平；
- Evidence Recall@5：94.1% → 100.0%，提升 5.9 个百分点；
- Evidence Recall@10：94.1% → 100.0%，提升 5.9 个百分点；
- Evidence MRR@10：0.912 → 0.924，提升 0.012。

因此，当前数据支持把 **分块后的 Hybrid 检索作为默认方案**。它没有损失 Top-1/Top-3 表现，并补回了纯向量检索漏掉的一个跨章节证据。由于评估集只有 17 条，暂时不能把这组结果解释为线上普遍提升，后续仍需积累真实用户问题持续回归。

## 2. 测试对象与口径

### 2.1 数据状态

| 项目 | 数量 |
|---|---:|
| 数据库有效知识条目 | 90 |
| 原有默认知识 | 10 |
| 新增长文档 | 10 |
| 新增文档分块 | 80 |
| 有 Embedding 的条目 | 90 |
| 检索评估问题 | 17 |

新增用例分为四组：

- `dense_semantic`：4 条口语化、同义表达问题；
- `bm25_exact`：5 条工号、业务编码、金额等精确匹配问题；
- `rerank_interference`：4 条主题相似但答案来源不同的问题；
- `chunking_cross_section`：4 条跨章节或易受相邻内容干扰的问题。

### 2.2 新旧方案

| 方案 | 实现 | 说明 |
|---|---|---|
| 旧：Dense | Qwen `text-embedding-v3` + FAISS 内积 | 依靠语义向量排序 |
| 新：Hybrid | Dense + jieba/BM25 + RRF | 融合语义召回和关键词精确召回 |

本次关闭 Rerank，只改变召回策略，避免把 LLM 精排的效果和费用混入比较。两种策略使用同一个数据库、同一组文档向量、同一批查询和同一个 Top-K。

### 2.3 严格证据指标

“Evidence 命中”要求某个返回分块同时满足：

1. `source_id` 是期望来源；
2. 该具体分块的标题或正文包含预期答案片段 `expected_contains`。

这比“只要返回了正确来源就算命中”更严格。例如检索到营业时间文档、却没有检索到包含“22:00 是结束营业时间”的分块，不计为证据命中。

- Evidence Recall@K：前 K 个结果中出现可回答证据的用例比例；
- Evidence MRR@10：首个证据排名倒数的均值，越接近 1 越好；
- Source Recall@K：只检查来源是否正确，作为辅助诊断指标；
- 延迟：包含一次远程查询 Embedding 和本地检索的端到端时间。

## 3. Dense 与 Hybrid 实测结果

### 3.1 总体指标

| 指标 | Dense | Hybrid | 变化 |
|---|---:|---:|---:|
| Evidence Recall@1 | 88.2% | 88.2% | 0.0 pp |
| Evidence Recall@3 | 94.1% | 94.1% | 0.0 pp |
| Evidence Recall@5 | 94.1% | **100.0%** | **+5.9 pp** |
| Evidence Recall@10 | 94.1% | **100.0%** | **+5.9 pp** |
| Evidence MRR@10 | 0.912 | **0.924** | **+0.012** |
| Source Recall@3 | 94.1% | **100.0%** | **+5.9 pp** |
| 平均延迟 | 3109 ms | 4304 ms | +1195 ms |
| P50 延迟 | 3528 ms | 3842 ms | +315 ms |

延迟数据只跑了一轮，而且 Dense 与 Hybrid 是顺序调用同一个远程 Embedding 服务。两者每个查询都只调用一次 Embedding，Hybrid 新增的 BM25/RRF 是本地计算；因此当前延迟差值容易受网络波动影响，不能直接归因于 BM25。若要形成可靠性能结论，应进行交错顺序、多轮预热后的 P50/P95 压测。

### 3.2 分组结果

以下为 Evidence Recall：

| 用例组 | 数量 | Dense @1 | Hybrid @1 | Dense @3 | Hybrid @3 | Dense @10 | Hybrid @10 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 语义表达 | 4 | 100% | 100% | 100% | 100% | 100% | 100% |
| 编码/数字精确匹配 | 5 | 80% | 80% | 100% | 100% | 100% | 100% |
| 相似主题干扰 | 4 | 100% | 100% | 100% | 100% | 100% | 100% |
| 跨章节 | 4 | 75% | 75% | 75% | 75% | 75% | **100%** |

本次差异集中在 `chunk_store_last_start`：

> 问题：晚上十点还能开始做一个小时的全身项目吗？
>
> 期望证据：22:00 是结束营业时间。

- Dense：正确来源排第 6，但前 10 个结果都没有包含预期答案的具体分块；
- Hybrid：正确来源排第 2，包含答案的具体分块排第 5。

这说明 Hybrid 的价值不仅是找对文档，还能通过关键词通道把正确的局部分块补进候选池。不过它没有把该证据提升到 Top-3，后续可用 Rerank 或针对营业时间类问题改进分词和查询扩展。

## 4. 整篇索引与分块索引对比

为单独验证分块效果，还使用同一批 10 篇原文和 17 个问题进行了离线 BM25 对照：旧方式每篇文档作为一个候选，新方式把文档切为 80 个候选分块。

| 指标 | 整篇文档 | 分块 Passage |
|---|---:|---:|
| Evidence Recall@1 | **88.2%** | 76.5% |
| Evidence Recall@3 | 100.0% | 100.0% |
| Evidence Recall@10 | 100.0% | 100.0% |
| Evidence MRR | **0.941** | 0.863 |
| 平均单候选字符数 | 976.5 | **114.5** |
| 平均 Top-3 上下文字符数 | 2985.5 | **389.5** |

分块没有提升纯 BM25 的 Top-1，原因是候选从 10 篇增加到 80 块后，同一来源的多个相近分块会互相竞争。但它把 Top-3 上下文从 2985.5 字符降到 389.5 字符，压缩 **87.0%**，同时保持 Recall@3 和 Recall@10 为 100%。

因此分块的主要收益不是单独提高 BM25 Top-1，而是：

- 给 LLM 更短、更聚焦的证据，减少无关上下文和 Token；
- 让 Hybrid/Rerank 能在段落粒度上排序；
- 为来源、章节、分块序号追踪提供基础；
- 长文局部修改后可按来源同步和哈希去重。

## 5. 已执行的验证

### 5.1 自动化回归

执行命令：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_document_ingestion.py tests\test_retriever_category_filter.py tests\test_shared_knowledge_service.py -q
```

结果：**19 passed，1 个 SQLAlchemy 2.0 弃用警告，无失败**。

覆盖内容包括中文分块、front matter、导入校验、重复跳过、来源版本同步、最大分块保护、分类召回和共享知识服务。这里没有把依赖真实 LLM 输出且结果可能波动的旧 Agent 集成测试混入确定性回归。

### 5.2 可复现评估

离线整篇/分块评估：

```powershell
.\.venv\Scripts\python.exe evaluation\eval_document_chunking.py `
  --json-output evaluation\results\document_chunking.json
```

真实 Dense/Hybrid 评估（会调用 Qwen Embedding 34 次）：

```powershell
.\.venv\Scripts\python.exe evaluation\eval_retrieval_strategies.py `
  --json-output evaluation\results\retrieval_strategies.json
```

机器可读的逐用例结果保存在：

- `evaluation/results/document_chunking.json`
- `evaluation/results/retrieval_strategies.json`

## 6. 局限与下一步

当前结论需要注意：

- 17 条用例规模较小，而且来自为本项目构造的知识文档；
- 没有人工标注的多相关答案集合，因此这里的 Recall 是“是否召回指定证据”，不是传统信息检索中的全量相关文档召回率；
- 只跑了一轮远程 API 延迟，不适合用于性能容量规划；
- Rerank 默认关闭，本报告没有评估它对 Top-3 的增益与额外成本。

建议下一步从实际对话日志中脱敏采集 50～100 条查询，人工标注可接受证据；保留本报告的固定 17 条冒烟集，再增加真实分布回归集。随后对 `Dense`、`Hybrid`、`Hybrid + Rerank` 三档同时评估 Recall@K、MRR、回答正确率、P95 延迟和单次调用成本。
