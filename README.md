# Smart Appointment AI Agent

一个面向按摩门店场景的智能预约与咨询系统。基于 FastAPI、LangChain、LangGraph、FAISS 和 SQLite，用多 Agent 协作架构模拟智能前台：自动理解用户意图，分发给对应 Agent 处理，完成预约、咨询、支付、统计等高频业务。

![System Architecture](./architecture%20.jpg)

---

## 核心特性

- **多 Agent 协作**：任务分类 → 预约 / 咨询 / 支付 / 统计 / 无关处理，5 类意图全覆盖
- **LangGraph 编排**：用 StateGraph 替代手写状态机，图定义集中，扩展一个新 Agent 只需改 1 处
- **混合检索 RAG**：Dense 向量（FAISS + Qwen Embedding）+ BM25 稀疏（jieba 分词）双路召回，RRF 融合，兼顾语义匹配与专有名词/数字精确匹配
- **两段式精排**：混合召回后可选接 LLM Rerank 精排（`with_structured_output` 打分重排，异常自动回退粗排），"粗排泛召回 → 精排精过滤"
- **可插拔检索器**：`BaseRetriever` 抽象（工厂 + 模板方法），`dense` / `hybrid` 策略通过配置一键切换，零代码改动，新增策略只需实现两个方法
- **链路可观测**：检索全链路 Trace，记录 dense / sparse / RRF / rerank 各阶段输出与耗时，RAG 从黑盒变白盒
- **Structured Output**：用 `with_structured_output` + Pydantic Schema 替代 JSON Prompt，输出格式 100% 合法
- **对话持久化**：AsyncSqliteSaver checkpointer，服务重启后多轮对话不丢失
- **多用户隔离**：`thread_id` 机制，不同用户的对话状态完全独立
- **量化评估**：意图分类准确率 96.9%、RAG Top-3 召回率 100%、预约流程通过率 100%，并自研 LLM-as-judge 评估器（Faithfulness / Answer Relevancy）

---

## 技术栈

| 类别 | 技术 |
|------|------|
| 后端框架 | FastAPI、Uvicorn |
| AI 框架 | LangChain 1.3.x、LangGraph 1.2.x |
| 大模型接入 | OpenAI 兼容协议（Qwen、DeepSeek、Zhipu、OpenAI、Azure OpenAI） |
| 检索（RAG） | FAISS + text-embedding-v3（Dense）、rank_bm25 + jieba（Sparse）、RRF 融合、LLM Rerank |
| 数据库 | SQLite、SQLAlchemy |
| 外部工具 | OpenWeatherMap（天气）、MCP |
| 前端 | Jinja2 模板、静态 CSS |

---

## 系统架构

采用严格五层架构，下层不能反向调用上层：

```
Web Layer      →  app.py, web/：页面路由、启动入口
API Layer      →  api/：接口编排、请求处理
Agents Layer   →  agents/：LangGraph StateGraph + 各 Agent
Services Layer →  services/：业务逻辑、可插拔检索器（retriever/）、重排、推荐算法
DB Layer       →  db/：SQLAlchemy 模型、Repository 模式
```

### LangGraph 编排图

```
START → classify_node（LLM 意图分类）
              │
              ├── appointment  → appointment_node  → END
              ├── consultation → consultation_node → END
              ├── pay          → pay_node          → END
              ├── statistics   → statistics_node   → END
              └── other        → unrelated_node    → END
```

多轮对话通过 `active_agent` 字段 + SQLite checkpointer 实现跨请求状态保持。

### RAG 检索流水线

咨询 Agent 的知识问答走可插拔检索器，`hybrid` 策略下为两段式：

```
              ┌─ Dense 召回（FAISS 向量，语义匹配）─┐
用户问题  →   │                                     ├─ RRF 融合 → 候选池 →（可选）LLM Rerank 精排 → Top-K → LLM 生成
              └─ BM25 召回（jieba 分词，精确匹配）─┘
```

- **粗排**：Dense + BM25 双路召回保查全率，RRF 按排名融合（k=60），回避两路分数量纲不可比问题
- **精排**：`RERANK_ENABLED=true` 时用 LLM 对候选打分重排，保查准率（默认关闭，权衡延迟/额度）
- **可切换**：`RETRIEVER_STRATEGY=dense` 可一键回退纯向量检索
- **可观测**：每次检索记录各阶段中间态与耗时，`KnowledgeService.get_last_trace()` 可取用

---

## 快速开始

### 环境要求

- Python 3.10+
- 一个支持 OpenAI 兼容协议的大模型 API Key（推荐阿里云百炼 Qwen，有免费额度）

### 安装

```bash
# 1. 创建虚拟环境
python -m venv .venv

# Windows PowerShell
.venv\Scripts\Activate.ps1

# macOS / Linux
source .venv/bin/activate

# 2. 安装依赖
pip install -r requirements.txt
```

### 配置

```bash
# Windows
Copy-Item .env.example .env

# macOS / Linux
cp .env.example .env
```

编辑 `.env`，填入你的 API Key：

```env
MODEL_PROVIDER=qwen
LLM_API_KEY=your_llm_api_key_here
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen-plus

EMBEDDING_PROVIDER=qwen
EMBEDDING_API_KEY=your_embedding_api_key_here
EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
EMBEDDING_MODEL=text-embedding-v3

OPENWEATHER_API_KEY=your_openweather_api_key_here  # 可选

# ---- RAG 检索配置（可选，均有默认值）----
RETRIEVER_STRATEGY=hybrid   # dense（纯向量）| hybrid（向量 + BM25 + RRF），默认 hybrid
RERANK_ENABLED=false        # 是否在召回后加一层精排，默认 false（避免增加实时延迟/额度）
RERANKER_PROVIDER=llm       # llm（复用 Chat 模型打分）| cross-encoder（本地模型，待实现）
```

> 使用 Qwen 时，聊天和 Embedding 用同一个 API Key 即可。其他提供商见 `.env.example` 注释。
> RAG 检索相关配置留空即用默认值，无需额外 Key。

### 启动

```bash
python -m uvicorn app:app --host 127.0.0.1 --port 8000 --reload
```

启动后访问：
- **Web 界面**：http://127.0.0.1:8000
- **API 文档**：http://127.0.0.1:8000/docs

---

## 主要页面

| 页面 | 地址 | 功能 |
|------|------|------|
| 聊天预约 | `/` | 主对话入口，支持预约、咨询、意图切换 |
| 技师管理 | `/technician` | 查看所有技师信息 |
| 技师排班 | `/technician_schedule` | 今日排班与忙碌时段 |
| 知识库管理 | `/knowledge` | 增删改查知识条目，自动重建向量索引 |
| 用户行为分析 | `/user_behavior` | 偏好分析、个性化回访提醒 |

---

## 对话示例

**预约流程（多轮）**
```
用户：我想预约推拿
AI：请问您想什么时间预约？需要哪种服务项目？

用户：今天下午3点，全身推拿，60分钟，要女技师
AI：已为您匹配技师小美，今天北京天气晴朗，出门注意防晒，期待为您服务～
```

**知识咨询**
```
用户：你们全身推拿多少钱？
AI：全身推拿售价 120 元 / 60 分钟，特别适合久坐办公室的上班族...
```

**支付处理**
```
用户：预约已确认，用户选择了张伟技师做全身推拿
AI：📋 订单号：ORD4082031 | 技师：张伟 | 项目：全身推拿 | 金额：¥120
```

---

## 评估结果

```
运行评估：python -m evaluation.run_all
```

| 评估维度 | 测试数量 | 结果 |
|---------|---------|------|
| 意图分类准确率 | 32 条 | **96.9%**（31/32） |
| RAG 检索 Top-3 召回率 | 12 组 | **100%**（12/12） |
| 预约流程场景通过率 | 8 个场景 | **100%**（8/8） |
| RAG 忠实度（Faithfulness） | 4 条 | **1.00**（无编造） |
| RAG 答案相关性（Answer Relevancy） | 4 条 | **1.00**（不跑题） |

### 混合检索 / 重排的受控对比

现有知识库仅 10 条、主题区分度高，纯向量已能把正确文档排第 1（召回率饱和）。为验证混合检索与重排的真实价值，构造了针对性实验：

| 场景 | 对比 | MRR 变化 |
|------|------|---------|
| 高混淆合成库（模板一致、仅工号不同的技师简介，精确编号查询） | Dense → Hybrid(BM25+RRF) | **0.75 → 1.00** |
| 主库对抗性用例（话题相关但意图不同，如问价格 vs 问效果） | Hybrid → Hybrid + LLM Rerank | **0.917 → 1.00** |

> 结论：在"语义相近、区分点在精确 token"场景，BM25 + RRF 修复了纯向量的漏排；在"话题相关但意图不同"场景，LLM Rerank 纠正了粗排错误。对比脚本见 `evaluation/eval_bm25_synthetic.py`、`evaluation/eval_rerank_compare.py`。

---

## 项目结构

```
├── agents/
│   ├── graph_agent.py              # LangGraph 编排器（含 checkpointer）
│   ├── appointment_agent.py        # 预约 Agent
│   ├── consultant_agent.py         # RAG 咨询 Agent
│   ├── user_behavior_agent.py      # 用户行为分析 Agent
│   ├── task_classification_agent.py# 旧版状态机（保留兼容）
│   ├── appointment/                # InputParser（Structured Output）
│   ├── consultant/                 # 知识检索、回答生成
│   └── user_behavior/              # 行为记录、偏好分析
├── api/                            # 接口编排层
├── services/                       # 业务逻辑层
│   ├── knowledge_service.py        # 知识库数据管理（持有可插拔检索器）
│   ├── reranker.py                 # 重排器（LLMReranker + Cross-Encoder 占位）
│   ├── text_embedding.py           # Qwen Embedding 封装
│   └── retriever/                  # 可插拔检索器模块
│       ├── base.py                 # BaseRetriever 抽象（工厂 + 模板方法）
│       ├── dense_retriever.py      # 纯向量检索
│       ├── hybrid_retriever.py     # 向量 + BM25 + RRF 融合
│       ├── factory.py              # create_retriever() 按配置切换
│       └── trace.py                # 检索链路 Trace
├── db/                             # 数据持久化层（Repository 模式）
├── config/                         # 配置（模型工厂、数据库、常量）
├── evaluation/                     # 量化评估模块
│   ├── test_cases.py               # 测试用例
│   ├── eval_classification.py      # 意图分类评估
│   ├── eval_rag_retrieval.py       # RAG 检索评估
│   ├── eval_appointment_flow.py    # 预约流程评估
│   ├── eval_retrieval_compare.py   # Dense vs Hybrid 对比
│   ├── eval_bm25_adversarial.py    # 精确匹配对抗性用例
│   ├── eval_bm25_synthetic.py      # 高混淆合成库受控实验（证明 BM25+RRF 增益）
│   ├── eval_rerank_compare.py      # Hybrid vs Hybrid+Rerank 对比
│   ├── rag_evaluator.py            # 自研 LLM-as-judge 评估器
│   ├── eval_rag_quality.py         # Faithfulness / Answer Relevancy 评估
│   └── run_all.py                  # 一键评估
├── docs/
│   ├── LANGGRAPH_MIGRATION.md      # LangGraph 改造详解
│   └── STRUCTURED_OUTPUT_MIGRATION.md
├── web/                            # Jinja2 模板 + 静态资源
├── data/                           # SQLite 数据库 + checkpointer
├── tests/                          # 单元测试
├── app.py                          # FastAPI 应用入口
├── requirements.txt
└── .env.example
```

---

## 切换实现

通过环境变量可以在 LangGraph 和旧版手写状态机之间切换：

```env
USE_LANGGRAPH=true   # 默认，使用 LangGraph
USE_LANGGRAPH=false  # 切换到旧版 TaskClassificationAgent
```

---

## 改进历程

| 改进项 | 核心变化 |
|--------|---------|
| LangGraph 编排 | 4 文件 400 行手写状态机 → 1 文件图定义，扩展性验证 |
| Structured Output | JSON Prompt 容错 → Function Calling 协议，输出 100% 合法 |
| 对话持久化 | 内存状态重启丢失 → SQLite checkpointer |
| 多用户隔离 | 全局单实例互串 → thread_id + 按 session 隔离 |
| 量化评估 | 无指标 → 分类 97%、RAG 100%、预约 100% |
| 业务闭环 | 5 类意图 2 类有 handler → 全覆盖 |
| RAG 检索升级 | 单阶段稠密检索 → 混合召回(BM25+RRF) + LLM Rerank 精排 + 可插拔检索器 + 链路 Trace |
| RAG 质量评估 | 只算召回率 → 自研 LLM-as-judge（Faithfulness / Answer Relevancy） |
