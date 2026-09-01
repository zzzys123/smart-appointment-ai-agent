# RAG 检索升级任务文档

> 目标:将 Modular-RAG-MCP-Server 的核心检索能力融入本项目的知识咨询模块,
> 把当前"单阶段稠密检索"升级为"混合召回 + RRF 融合 + 重排 + 可插拔 + 可观测 + Ragas 评估"。

---

## 背景:当前 RAG 现状

当前咨询链路(`services/knowledge_service.py` + `agents/consultant/`):

```
用户问题 → embed_query → FAISS IndexFlatIP 向量搜索 → top_k=3 → 塞进 prompt → LLM 生成
```

**短板**:只有 Dense 向量检索,无稀疏检索、无融合、无重排、无检索抽象、无链路追踪,
评估只有召回率。中文专有名词 + 价格数字场景下,纯向量对精确匹配偏弱。

**已有基础**:`config/model_provider.py` 已是工厂模式(LLM / Embedding 可切换),
可插拔思路可直接复用扩展。

---

## 总体验收标准

- 每一步都能独立运行 `python -m evaluation.run_all` 验证,不破坏现有功能
- 保留向后兼容:默认行为可回退到原纯向量检索
- 每步记录改造前后的评估数据对比(召回率 / 精度 / Faithfulness)

---

## 第一步:BM25 检索 + RRF 融合

**目标**:在 Dense 向量检索之外增加 BM25 稀疏检索,用 RRF(Reciprocal Rank Fusion)融合两路结果。

**改动点**
- 依赖:新增 `rank_bm25`(或基于 jieba 自实现 BM25),中文分词用 `jieba`
- `services/knowledge_service.py`:
  - 构建索引时,除 FAISS 外,额外构建 BM25 语料(对 content + keywords 做 jieba 分词)
  - `search()` 内部:
    1. Dense 召回 top_n(如 10)
    2. BM25 召回 top_n(如 10)
    3. RRF 融合:`score = Σ 1/(k + rank_i)`,k 取 60
    4. 返回融合后 top_k
- 增删改文档时,同步重建 BM25 语料

**验收**
- 运行 `python -m evaluation.eval_rag_retrieval`,记录混合检索 vs 纯向量的召回率对比
- 命中此前未命中的专有名词类查询(价格、地址等)

---

## 第二步:Rerank 精排层(先 LLM Rerank)

**目标**:在混合召回(top 10)后加一层重排,取精排 top 3,形成"粗排 → 精排"两段式。

**改动点**
- 新增 `services/reranker.py`(或 `agents/consultant/reranker.py`):
  - `LLMReranker`:复用 `create_chat_model`,让 LLM 对候选逐一/批量打分,按分数排序
  - 预留 `CrossEncoderReranker` 接口(后续可换本地重排模型,如 `cross-encoder/ms-marco-MiniLM-L-6-v2`)
- `knowledge_service.search()` 或检索器中:融合后 → rerank → 返回 top_k
- 加开关:`RERANK_ENABLED`(默认可关,便于对比)

**验收**
- 运行评估,记录加 rerank 前后 top-3 精度 / 片段命中率提升
- 观察 rerank 前后排名变化(为第四步 trace 铺垫)

---

## 第三步:抽象 BaseRetriever 接口 + 配置切换

**目标**:仿照 `model_provider` 工厂,把检索策略做成可插拔,配置一键切换。

**改动点**
- 新增 `services/retriever/` 模块:
  - `base.py`:`BaseRetriever` 抽象接口(`initialize()` / `search()`)
  - `dense_retriever.py`:纯向量检索
  - `hybrid_retriever.py`:混合召回 + RRF(+ 可选 rerank)
  - `factory.py`:`create_retriever()` 按配置返回实例
- 配置:`.env` 新增 `RETRIEVER_STRATEGY=dense|hybrid`(默认 hybrid)
- `KnowledgeService` 改为持有一个 `retriever` 实例,`search()` 委托给它
- `.env.example` 补充新配置项说明

**验收**
- 切换 `RETRIEVER_STRATEGY` 后行为正确,无需改代码
- 做 A/B 对比:dense vs hybrid vs hybrid+rerank 三组召回率数据

---

## 第四步:检索链路 Trace + 评估接入 Ragas

**目标**:检索全链路白盒化,评估从"只算召回率"升级到 Ragas 指标。

**改动点**
- Trace:
  - 新增轻量 trace 结构,记录每阶段中间态:
    dense 召回 / sparse 召回 / RRF 融合后 / rerank 前后排名变化 / 耗时
  - 把 `knowledge_retriever.py` 现有 print 替换为结构化 trace(可选落库或返回给上层)
- Ragas 评估:
  - `evaluation/` 新增 `eval_ragas.py`:
    - 指标:Faithfulness(答案忠于检索内容)、Answer Relevancy、Context Recall
    - 复用 `create_chat_model` / `create_embedding_model` 作为 Ragas 的 judge
  - 接入 `evaluation/run_all.py`

**验收**
- 检索日志能清晰看到"为什么选这几条""rerank 起了什么作用"
- Ragas 报告输出各项指标,量化对比升级前后质量

---

## 落地顺序与产出

| 步骤 | 核心产出 | 验证方式 |
|------|---------|---------|
| 第一步 | BM25 + RRF 融合 | 召回率对比 |
| 第二步 | LLM Rerank 两段式 | top-3 精度对比 |
| 第三步 | BaseRetriever 可插拔 | 配置切换 + A/B 对比 |
| 第四步 | Trace + Ragas | 链路可视 + 质量报告 |

> 每步完成后在本文件末尾追加"完成记录 + 评估数据",作为简历量化素材。

---

## 完成记录

### 第一步:BM25 + RRF 融合 ✅(2026-07-20)

**改动摘要**
- 依赖:新增 `jieba`、`rank_bm25`(已加入 `requirements.txt`)
- `services/knowledge_service.py`:
  - 新增 `_tokenize_zh()` 中文分词(jieba + 去符号)
  - `_build_vector_index()` 在构建 FAISS 的同时构建 `BM25Okapi` 语料
  - `search()` 按 `RETRIEVER_STRATEGY`(dense|hybrid,默认 hybrid)分流
  - 新增 `_dense_search_ids()` / `_hybrid_search_ids()`:Dense 召回 + BM25 召回 → RRF 融合(k=60)
  - 保留向后兼容:`RETRIEVER_STRATEGY=dense` 可回退原纯向量行为
- 新增 `evaluation/eval_retrieval_compare.py`:Dense vs Hybrid 细粒度对比(first_hit_rank / MRR)

**评估数据对比**

| 指标 | Dense(原) | Hybrid(BM25+RRF) |
|------|----------|------------------|
| Hit@3 召回率 | 100.0% (12/12) | 100.0% (12/12) |
| MRR | 1.000 | 1.000 |

**结论 / 说明**
- 功能正确、无回归:混合检索通过全部用例,BM25 索引确认生效(10 篇文档均入库,RRF 分数正常)。
- **在现有 10 条知识库上无法体现增量价值**:知识库仅 10 条、主题区分度高,纯向量已能把正确文档排第 1(MRR=1.0),指标饱和;即使构造精确匹配的对抗性用例(价格/门牌号/专有名词,见 `eval_bm25_adversarial.py`),Dense/Sparse/Hybrid 仍均为 MRR=1.0——**根因是检索空间太小,不是代码问题**。

**BM25 增益的受控验证(`eval_bm25_synthetic.py`)**
构造 40 条"模板一致、仅工号不同"的高混淆技师简介(纯向量对精确编号几乎无法区分),用精确工号查询对比:

| 指标 | Dense | Sparse(BM25) | Hybrid(RRF) |
|------|-------|--------------|-------------|
| Hit@3 召回率 | 100% | 100% | 100% |
| **MRR** | **0.750** | **1.000** | **1.000** |

- "工号7号""预约工号31""5号技师"三条查询,**Dense 把正确文档排到第 2 位**(文档语义高度相似,embedding 淹没了数字信号),**BM25 与 Hybrid 均排第 1**。
- **Hybrid 通过 RRF 融合修复了纯向量的漏排,MRR 从 0.750 → 1.000(+33%)**,验证了混合检索在"语义相近、区分点在精确 token"场景下的真实价值。

**给简历/面试的说法**
- 不要说"混合检索让召回率从 X% 提到 Y%"(现有库上是持平的,经不起追问)。
- 可以说"针对纯向量在专有名词/精确编号上的漏排问题,引入 BM25 + RRF 混合检索,在高混淆语料上将 MRR 从 0.75 提升到 1.0",并能讲清机制与实验设计——这更真实、更有深度。
- 后续若要在业务库上拿到量化收益,需扩充知识库规模与混淆度(相近价格/名称/术语的竞争文档)。

**新增评估脚本**
- `evaluation/eval_retrieval_compare.py`:主库上 Dense vs Hybrid 的 first_hit_rank / MRR 对比
- `evaluation/eval_bm25_adversarial.py`:主库上精确匹配类对抗性用例(Dense/Sparse/Hybrid 三方)
- `evaluation/eval_bm25_synthetic.py`:高混淆合成库受控实验(证明 BM25+RRF 增益)

---

### 第二步:LLM Rerank 精排层 ✅(2026-07-20)

**改动摘要**
- 新增 `services/reranker.py`:
  - `BaseReranker` 抽象接口(为第三步可插拔铺垫)
  - `LLMReranker`:复用 `create_chat_model`(Qwen),用 `with_structured_output` 让 LLM 对候选逐一打 0-10 分后重排;异常时回退粗排原始顺序(容错)
  - `CrossEncoderReranker`:本地重排（sentence-transformers 批量推理、延迟加载、异常回退）
  - `create_reranker()` 工厂,`RERANKER_PROVIDER=llm|cross-encoder`
- `services/knowledge_service.py`:
  - `search()` 改为两段式:粗排召回(候选池 top_k*4)→ 可选精排 rerank → 取 top_k
  - 新增开关 `RERANK_ENABLED`(默认 false,避免增加实时查询延迟/额度)
  - `_get_reranker()` 延迟初始化
- API:仅用项目已有的 Qwen(chat),无需额外 key

**评估数据对比(主库,`eval_rerank_compare.py`)**

用"话题相关但意图不同"的查询(问价格 vs 问效果)对比 Hybrid vs Hybrid+Rerank:

| 指标 | Hybrid | Hybrid + LLM Rerank |
|------|--------|---------------------|
| Hit@3 召回率 | 100% | 100% |
| **MRR** | **0.917** | **1.000** |

- "足底按摩价格贵不贵":Hybrid 把含价格的正确文档排第 2,**Rerank 精排后提到第 1**。
- 两条反向校验(问效果)Rerank 后 top-1 仍为效果文档,**未乱排**,说明重排器正确理解了意图。
- Rerank 将 MRR 从 0.917 → 1.000(+9%)。

**结论 / 说明**
- 功能正确、无回归:开启/关闭 rerank 均通过,默认关闭不影响现有实时咨询。
- 小库上提升幅度有限(候选少、语义差异小);Rerank 的价值在**大候选池 + 语义细微差异**场景更显著。
- 面试说法:"在混合召回后引入 LLM Rerank 两段式精排,针对'话题相关但意图不同'的查询纠正粗排错误,MRR 从 0.92 提升到 1.0",并能讲清粗排/精排的成本权衡(默认关闭、按需开启)。

**新增评估脚本(第二步)**
- `evaluation/eval_rerank_compare.py`:Hybrid vs Hybrid+LLM Rerank 的 first_hit_rank / MRR 对比

---

### 第三步:BaseRetriever 抽象接口 + 配置切换 ✅(2026-07-20)

**改动摘要(纯本地重构,不额外消耗 API)**
- 新增 `services/retriever/` 模块,把检索逻辑从 `KnowledgeService` 解耦:
  - `base.py`:`BaseRetriever` 抽象基类。用**模板方法**统一 `search()` 流程(候选池大小 → 分类过滤 → 可选 rerank → top_k);子类只需实现 `build_index()` 与 `_recall()`
  - `dense_retriever.py`:`DenseRetriever`(FAISS 向量检索)
  - `hybrid_retriever.py`:`HybridRetriever`(向量 + BM25 + RRF;`_tokenize_zh` / `RRF_K` 迁移至此)
  - `factory.py`:`create_retriever()` 按 `RETRIEVER_STRATEGY` 返回实例
  - `__init__.py`:统一导出
- `services/knowledge_service.py`:
  - 职责收敛为**数据管理**(默认知识库、embedding 生成/回写、增删改查)
  - 持有 `self.retriever = create_retriever()`;`search()` 委托检索器;`_build_vector_index()` 改为"确保 embedding 就绪 → `retriever.build_index(documents)`"
  - 保留 `RRF_K` / `_tokenize_zh` 的 re-export,向后兼容
- 配置:`.env.example` 新增 `RETRIEVER_STRATEGY` / `RERANK_ENABLED` / `RERANKER_PROVIDER` 说明
- 评估脚本全部改用 `create_retriever(strategy)`,直接展示"配置切换、零代码改动"

**验证**
- 无回归:默认 hybrid 跑 `eval_rag_retrieval` 仍 100%;`eval_rerank_compare` 仍 MRR 0.917→1.000
- 切换正确:`RETRIEVER_STRATEGY=dense` → `retriever.name=dense`;`=hybrid` → `hybrid`,均正常建索引与检索,无需改代码
- 架构收益:新增一种检索策略(如纯 BM25、或换向量库)只需实现 `build_index` + `_recall` 两个方法并在工厂注册,符合开闭原则

**面试说法**
- "将检索层抽象为可插拔的 `BaseRetriever`(工厂模式 + 模板方法),Dense/Hybrid 通过配置一键切换,零代码改动;新增策略只需实现两个抽象方法。这与业界(如 LlamaIndex/LangChain Retriever 抽象)一致,也便于做 A/B 检索实验。"

---

### 第四步:检索链路 Trace + RAG 质量评估 ✅(2026-07-20)

**改动摘要**
- **链路追踪(纯本地)**:
  - 新增 `services/retriever/trace.py`:`RetrievalTrace` / `StageTrace`,记录每阶段(dense 召回 / sparse 召回 / RRF 融合 / rerank 前后)输出的文档 id 与耗时,提供 `summary()` 可读摘要与 `to_dict()`
  - `base.py` 的 `search()` 全程构建并填充 trace,存 `self.last_trace`;`_recall()` 签名加 `trace` 参数,dense/hybrid 各子阶段计时写入
  - `KnowledgeService.get_last_trace()` 暴露;`knowledge_retriever.py` 打印结构化链路摘要替代零散 print
- **RAG 质量评估**:
  - ⚠️ **ragas 库与项目 LangChain 1.x 不兼容**(ragas 0.4.3 硬 import 已废弃的 `langchain_community.chat_models.vertexai`,import 即崩溃)。强行修依赖会威胁 langgraph 等核心环境,故**改用自研 LLM-as-judge 评估器**,复用 Qwen 实现 Ragas 同款核心指标:
    - `evaluation/rag_evaluator.py`:`RagJudge`
      - **Faithfulness(忠实度)**:拆答案为原子陈述,逐条判断是否被检索上下文支持 → 支持占比。衡量幻觉/编造
      - **Answer Relevancy(相关性)**:LLM 对"答案是否切题"打 0-1 分
      - 均用 `with_structured_output` 稳定输出 + 异常兜底
    - `evaluation/eval_rag_quality.py`:检索→生成答案→评估的完整闭环入口
    - 接入 `evaluation/run_all.py` 作为 [4/4],综合报告新增"RAG 忠实度/相关性"行(fast 模式跳过)

**验证**
- Trace 实测:`query='全身推拿多少钱'` 输出链路 —— dense_recall 1758ms(调 embedding API)、sparse_recall 0ms、rrf_fusion 0ms,直观暴露"向量检索是延迟瓶颈、BM25/RRF 本地几乎零耗时"
- RAG 质量实测(4 条咨询问题):Faithfulness 平均 **1.00**、Answer Relevancy 平均 **1.00**,答案忠于检索内容且切题,无编造
- run_all 综合报告正确渲染新增指标行,无回归

**结论 / 说明**
- 依赖冲突是真实工程问题,处理方式(不硬修、自研替代、保护主环境)本身就是好的面试素材
- 自研评估器完整体现了 Faithfulness/Relevancy 的**指标原理**,面试反而能讲得比"调了个库"更深
- Trace 让 RAG 从黑盒变白盒,可讲"如何定位坏 case、如何发现向量检索是延迟大头"

**面试说法**
- "为解决 RAG 黑盒问题,我为检索链路加了全程 trace,记录 dense/sparse/RRF/rerank 每阶段的输出与耗时;评估上实现了 Faithfulness、Answer Relevancy 两个 LLM-as-judge 指标,建立基于数据的反馈回路。因 ragas 与项目 LangChain 版本冲突,我自研了评估器,既规避了依赖风险,也更清楚每个指标的计算原理。"

**新增文件(第四步)**
- `services/retriever/trace.py`、`evaluation/rag_evaluator.py`、`evaluation/eval_rag_quality.py`

---

## 附录 A:混合检索(向量 + BM25 + RRF 融合)详解

### 为什么要混合?两种检索各有死穴

| 检索方式 | 原理 | 强项 | 死穴 |
|---------|------|------|------|
| **Dense 稠密检索(向量)** | Embedding 把文本变成语义向量,算向量距离找最近 | 懂语义:"按摩能放松吗"能匹配"推拿舒缓肌肉疲劳"(无字面重合) | 对专有名词/数字/编号不敏感("工号17"与"工号18"向量几乎一样近) |
| **BM25 稀疏检索(关键词)** | 基于词频 TF + 逆文档频率 IDF 的关键词统计匹配 | 精确匹配:"120元""中关村27号""工号17"一击命中 | 不懂语义:搜"放松"找不到只含"舒缓"的文档 |

一句话:**Dense 懂意思但不精确,BM25 精确但不懂意思**。混合检索让两者各召回一批再融合,取长补短。

### Dense 向量检索(项目实现)
```python
query_array = np.array([embed_input(query)]).astype("float32")  # Qwen 生成查询向量
_, dense_indices = self.index.search(query_array, n)            # FAISS 内积相似度检索
dense_ranking = [self.document_ids[idx] for idx in dense_indices[0] ...]
```

### BM25 稀疏检索(项目实现)
```python
bm25_scores = self.bm25.get_scores(_tokenize_zh(query))  # jieba 分词后算 BM25 分
order = np.argsort(bm25_scores)[::-1][:candidate_n]
sparse_ranking = [self.bm25_doc_ids[i] for i in order if bm25_scores[i] > 0]  # 丢弃 0 分
```
BM25 分数直觉:查询词在文档中**出现越多分越高**(TF);但**越常见的词越不值钱**(IDF,如"的""我们"被压低);再做**文档长度归一化**,避免长文档占便宜。

### 关键难点:两个分数没法直接比 → 用 RRF
- Dense 分数(内积相似度)可能 0.2~0.9;BM25 分数(词频统计)可能 0~15+
- **量纲/分布完全不同**,直接相加会让 BM25 主导;归一化又麻烦且不稳定
- **RRF 的聪明之处:不看分数,只看排名(rank)** —— 排名最稳定,"排第 1"就是"排第 1"

### RRF 融合公式与实现
$$\text{RRF}(d) = \sum_{i} \frac{1}{k + \text{rank}_i(d)}, \quad k=60$$

```python
fused = {}
for ranking in (dense_ranking, sparse_ranking):
    for rank, doc_id in enumerate(ranking, start=1):
        fused[doc_id] = fused.get(doc_id, 0.0) + 1.0 / (RRF_K + rank)
ranked = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)
```

**为什么这个公式好:**
1. **排名越靠前贡献越大,但差距递减**:第1名 `1/61≈0.0164`、第2名 `1/62≈0.0161`、第10名 `1/70≈0.0143`,尾部衰减平缓。
2. **k=60 起"削峰"作用**:若用 `1/rank`,第1名=1.0、第2名=0.5 差一倍太悬殊,单路第一就能碾压;加 60 后单路第一无法一票定音,**需两路都认可的文档才能冲到最前**。
3. **两路都召回 = 双份加分**:语义相关又关键词命中的文档得分最高,正是我们想要的结果。

### 例子:查询"全身推拿多少钱"
- Dense 召回:① 全身推拿效果doc ② 服务项目doc(含120元)
- BM25 召回:① 服务项目doc(keywords含"多少钱") ② 全身推拿效果doc
- RRF 融合:
  - 服务项目doc = 1/(60+2) + 1/(60+1) ≈ 0.0325
  - 效果doc = 1/(60+1) + 1/(60+2) ≈ 0.0325(接近,故需再接 rerank 精排分胜负)
- 而在"工号"实验中,BM25 对精确编号压倒性命中,RRF 能把 dense 漏排的正确文档拉回第1(MRR 0.75→1.0)。

### 定位:属于 RAG 的"粗排/召回"阶段
```
查询 → ┌─ Dense 召回(语义) ─┐
       │                    ├─ RRF 融合 → 候选集 →(可选)Rerank 精排 → Top-K
       └─ BM25 召回(关键词) ─┘
```
- Dense + BM25 保证**查全率**;RRF 用排名融合**回避分数不可比问题**,无需调参/归一化,鲁棒简单
- 后接 Rerank 做**查准率**精排(见附录 B / 第二步)

---

## 附录 B:Rerank 精排方法详解

### Rerank 解决什么问题
- 粗排(混合检索 + RRF)追求**查全率**:快速从全库捞出一批候选(如 top 10),宁多勿漏,但难免混入"看似相关、其实不对"的文档。
- Rerank(重排)是第二阶段**精排**,追求**查准率**:对这一小批候选做深度、细粒度的相关性判断,把真正最相关的顶到最前。
- 核心是**两段式架构**:`粗排(低成本泛召回,处理全库)→ 精排(高成本精过滤,只处理 top-N)`。精排很贵,不可能对全库每篇都做,所以先缩小到 10 篇再精雕。

### 本质区别:Bi-Encoder vs Cross-Encoder(面试必考)

| | Bi-Encoder(双塔,粗排用) | Cross-Encoder(交叉编码,精排用) |
|---|---|---|
| 编码方式 | 查询与文档**分别独立**编码成向量,再算距离 | 查询+文档**拼在一起**送入模型做深度注意力交互 |
| 速度 | 快(文档向量可离线预存) | 慢(每个候选都要现算,不能预存) |
| 精度 | 粗(编码时查询/文档"未见过对方") | 高(深度交互,能捕捉细微意图差异) |
| 用途 | 全库召回 | 小批候选精排 |

一句话:**Bi-Encoder 快但粗,Cross-Encoder 准但慢** → 所以用 Bi-Encoder 召回、Cross-Encoder 精排。

### 两种主流实现
1. **Cross-Encoder 专用重排模型**(工业界主流):如 `bge-reranker`、`cross-encoder/ms-marco-MiniLM`,输入 (query, doc) 输出相关性分数;本地跑、不花 API 钱,但需下载模型。项目已实现 `CrossEncoderReranker`，默认模型可配置且失败时回退粗排。
2. **LLM Rerank**(第二步已实现):让 LLM(Qwen)当裁判给候选打分;复用现有 LLM、理解力强,但每次查询多一次 LLM 调用,慢且耗 token。

### 项目实现(`services/reranker.py` 的 LLMReranker)
```python
# ① 结构化输出保证格式稳定
class _RankItem(BaseModel):
    index: int      # 候选编号
    score: float    # 相关性 0-10
self.structured_llm = self.llm.with_structured_output(_RerankResult)

# ② 候选打包成带编号列表，让 LLM 逐一打分
candidates_text = "\n".join(f"[{i}] {doc['content']}" for i, doc in enumerate(documents, 1))
result = await self.structured_llm.ainvoke([("system", 提示), ("human", user_prompt)])

# ③ 按分重排 + 容错兜底
score_map = {item.index: item.score for item in result.rankings}
for i, doc in enumerate(documents, 1):
    doc["rerank_score"] = score_map.get(i, -1.0)   # LLM 漏评的给低分排后面
scored.sort(key=lambda d: d["rerank_score"], reverse=True)
# 若 LLM 整体调用失败：except → return documents[:top_k]  回退粗排顺序，绝不让检索崩
```

### 成本权衡:为什么默认关闭
Rerank 每次查询多一次 LLM 调用,对流式实时咨询会增加首字延迟、消耗 token。故 `RERANK_ENABLED` **默认 false**,对答案质量敏感/评估时才开。这是工程判断点:精度 vs 延迟的权衡,做成可选开关而非无脑全开。

### 效果回顾
第二步:"足底按摩价格贵不贵",粗排把含价格的正确文档排第 2,LLM Rerank 理解"问价格"意图后提到第 1,MRR 0.917 → 1.000。这正是 Cross-Encoder/LLM 深度交互才能捕捉的细微意图差异。

---

## 附录 C:Ragas / LLM-as-judge 与 Faithfulness、Answer Relevancy 原理

### LLM-as-judge(用大模型当裁判)
用能力强的 LLM 评判另一个模型/系统的输出质量,替代人工打分。对开放式生成,字面重叠指标(BLEU/ROUGE)失效("120元/小时"vs"每小时一百二"),LLM 能按语义评。

**三种形式**:
- Pointwise 单点打分(我们的 Answer Relevancy 属此)
- Pairwise 两两比较(模型 PK / 竞技场排名)
- Reference-based / Reference-free(是否需要标准答案;Ragas 主打 reference-free)

**已知偏见(面试考点)**:位置偏见、冗长偏见(偏爱长答案)、自我偏好偏见、不稳定性(故设 `temperature=0`)。

### Ragas 是什么
专门评估 RAG 的开源框架,把 RAG 拆成"检索"和"生成"两段分别给指标,大量指标 reference-free,背后主要靠 LLM-as-judge。

| 类别 | 指标 | 评什么 | 需标准答案 |
|------|------|--------|-----------|
| 生成质量 | Faithfulness | 答案是否忠于检索内容(幻觉) | 否 |
| | Answer Relevancy | 答案是否切题 | 否 |
| 检索质量 | Context Precision | 有用上下文是否排前 | 通常需要 |
| | Context Recall | 该检索的是否都检索到 | 需要 |

本项目做前两个(生成质量,无需人工标注)。因 ragas 与项目 LangChain 1.x 冲突,自研了同款指标。

### Faithfulness(忠实度)计算原理
衡量**幻觉**:答案说的每一点能否在检索上下文找到依据。

**算法(两步,与 Ragas 原版一致)**:
1. 拆解陈述:LLM 把答案拆成一条条原子陈述(每条一个事实点)
2. 逐条判定:LLM 判断每条陈述是否被上下文支持(NLI 式),上下文没提/矛盾的记为不支持

$$\text{Faithfulness} = \frac{\text{被支持的陈述数}}{\text{陈述总数}}$$

**例子**:上下文「全身推拿 120元/60分钟」,答案「120元,60分钟,还送茶水」→ 拆成 ①120元✅ ②60分钟✅ ③送茶水❌(幻觉) → 2/3 ≈ 0.67

**代码**(`rag_evaluator.py`):`_Claim(statement, supported)` 结构化输出,`score = supported/total`。提示词强调"只依据上下文、不要用世界知识脑补",否则幻觉检测失效。

### Answer Relevancy(答案相关性)计算原理
衡量**是否切题**(不管对错,只管是否针对问题在答)。

**Ragas 原版:反向生成法**
1. 让 LLM 从答案反推 n 个"它像在回答什么问题"
2. 反推问题与原问题分别 Embedding,算 cosine 相似度
3. 取平均;并检测答案是否含糊推诿(是则压低)

$$\text{Answer Relevancy} = \frac{1}{n}\sum_{i=1}^{n}\cos(\text{原问题}, \text{反推问题}_i)$$
直觉:答案切题 → 反推出的问题与原问题相似;跑题 → 相差远。

**我们的实现:直接打分法(简化版)**
让 LLM 直接给"答案对问题的相关性"打 0~1 分(`_RelevancyResult(score, reason)`)。
- 取舍:Ragas 反推法更客观(embedding 量化)但更贵更慢;直接打分更省成本但依赖单次主观判断
- 面试话术:"知道反推法更严谨,但基于小项目成本做了权衡"

### 完整闭环(`eval_rag_quality.py`)
```
问题 → 检索上下文 → 基于上下文生成答案
     → Faithfulness(答案, 上下文)  # 防幻觉
     → Answer Relevancy(问题, 答案) # 防跑题
```
一句话:**Faithfulness 盯「上下文↔答案」防幻觉,Answer Relevancy 盯「问题↔答案」防跑题**。
