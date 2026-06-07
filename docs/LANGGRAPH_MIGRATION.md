# LangGraph 改造说明

## 一、什么是 LangGraph

LangGraph 是 LangChain 团队在 2024 年推出的 **Agent 编排框架**，核心思想是用**有向图（Graph）**来定义 Agent 之间的协作流程。

传统的 LangChain Agent 是"链式"的（Chain），一条链从头走到尾。但真实业务中，Agent 协作往往是有分支、有循环、有条件判断的——更像一张图而不是一条线。LangGraph 就是为了解决这个问题。

**核心概念**：
- **Node（节点）**：图中的处理单元，每个节点是一个函数，负责一项具体工作
- **Edge（边）**：节点之间的连接，定义执行顺序
- **Conditional Edge（条件边）**：根据状态动态决定下一步走哪个节点
- **State（状态）**：在所有节点之间共享和传递的数据结构
- **Reducer**：定义多个节点写同一个字段时如何合并（如 append 列表）

---

## 二、为什么要从手写状态机迁移到 LangGraph

### 原来的实现方式

在改造前，Agent 编排靠 4 个手写类协同工作：

```
TaskClassificationAgent（主控）
├── TaskClassifier        → LLM 判断意图
├── StateManager          → 手写状态枚举 + if/elif 状态转换
├── AgentRouter           → 根据分类结果分发到对应 Agent
└── ClassificationProcessor → 协调以上三者的流程
```

代码量大约 400+ 行，分散在 4 个文件里。

### 迁移后的实现方式

```python
graph = StateGraph(AgentState)
graph.add_node("classify_node", classify_node)
graph.add_node("appointment_node", appointment_node)
graph.add_node("consultation_node", consultation_node)
graph.add_node("unrelated_node", unrelated_node)

graph.add_edge(START, "classify_node")
graph.add_conditional_edges("classify_node", route_after_classify, {...})
graph.add_edge("appointment_node", END)
graph.add_edge("consultation_node", END)
graph.add_edge("unrelated_node", END)
```

一个文件，约 200 行，图结构一目了然。

---

## 三、LangGraph 在本项目中的具体实现

### 3.1 状态定义

```python
from typing import TypedDict, Annotated
from operator import add

class AgentState(TypedDict):
    user_input: str                          # 用户当前输入
    category: str                            # 分类结果
    output_tokens: Annotated[list[str], add] # 累积输出（reducer: 追加）
    session_id: str                          # 会话 ID
    active_agent: str                        # 当前活跃 Agent（多轮对话用）
```

**关键设计**：`output_tokens` 使用了 `Annotated[list[str], add]` 作为 reducer。这意味着每个节点返回的 `output_tokens` 不会覆盖，而是 **追加** 到列表中。这样不同节点（思考提示 + 实际回复）的输出可以自然拼接。

### 3.2 节点定义

每个节点是一个异步函数，接收当前 State，返回需要更新的字段：

```python
async def classify_node(state: AgentState) -> dict:
    """分类节点 - 判断用户意图"""
    # 如果已在多轮流程中，跳过重新分类
    if state.get("active_agent") in ("appointment", "consultation"):
        return {"category": state["active_agent"]}
    
    # 调用 LLM 分类
    classifier = _get_classifier()
    category = await classifier.classify_task(state["user_input"])
    
    # 映射分类结果
    if category == "appointment":
        return {"category": "appointment"}
    elif category == "query":
        return {"category": "consultation"}
    else:
        return {"category": "unrelated"}


async def appointment_node(state: AgentState) -> dict:
    """预约节点 - 调用 AppointmentAgent"""
    agent = _get_appointment_agent(state["session_id"])
    tokens = ["[THOUGHT]归类机器人：这是一个预约任务..."]
    
    async for token in agent.run_stream(user_input=state["user_input"]):
        tokens.append(token)
    
    # 判断预约是否完成，决定是否保持 active_agent
    active = "none" if agent.finished else "appointment"
    return {"output_tokens": tokens, "active_agent": active}


async def consultation_node(state: AgentState) -> dict:
    """咨询节点 - 调用 ConsultantAgent"""
    # ... 类似结构


async def unrelated_node(state: AgentState) -> dict:
    """无关请求节点 - 友好拒绝"""
    return {
        "output_tokens": ["[REPLY]暂不支持该类型任务..."],
        "active_agent": "none"
    }
```

### 3.3 路由函数

```python
def route_after_classify(state: AgentState) -> str:
    """条件路由 - 根据分类结果选择下一个节点"""
    category = state.get("category", "unrelated")
    if category == "appointment":
        return "appointment_node"
    elif category == "consultation":
        return "consultation_node"
    else:
        return "unrelated_node"
```

### 3.4 图构建与编译

```python
def build_agent_graph() -> StateGraph:
    graph = StateGraph(AgentState)
    
    # 注册节点
    graph.add_node("classify_node", classify_node)
    graph.add_node("appointment_node", appointment_node)
    graph.add_node("consultation_node", consultation_node)
    graph.add_node("unrelated_node", unrelated_node)
    
    # 定义边
    graph.add_edge(START, "classify_node")
    graph.add_conditional_edges(
        "classify_node",
        route_after_classify,
        {
            "appointment_node": "appointment_node",
            "consultation_node": "consultation_node",
            "unrelated_node": "unrelated_node",
        }
    )
    graph.add_edge("appointment_node", END)
    graph.add_edge("consultation_node", END)
    graph.add_edge("unrelated_node", END)
    
    return graph

# 编译时注入 checkpointer，实现对话持久化
checkpointer = await _get_checkpointer()  # AsyncSqliteSaver
compiled_graph = build_agent_graph().compile(checkpointer=checkpointer)

# 调用时传入 thread_id，实现多用户隔离
config = {"configurable": {"thread_id": session_id}}
result = await compiled_graph.ainvoke(initial_state, config=config)
```

### 3.5 执行流程图

```
┌───────┐
│ START │
└───┬───┘
    │
    ▼
┌──────────────┐
│ classify_node │  ← LLM 判断意图
└──────┬───────┘
       │
       │ (条件路由)
       ├─── category == "appointment" ──→ ┌──────────────────┐
       │                                   │ appointment_node  │ → END
       │                                   └──────────────────┘
       │
       ├─── category == "consultation" ─→ ┌───────────────────┐
       │                                   │ consultation_node  │ → END
       │                                   └───────────────────┘
       │
       ├─── category == "pay" ──────────→ ┌──────────┐
       │                                   │ pay_node  │ → END
       │                                   └──────────┘
       │
       ├─── category == "statistics" ───→ ┌─────────────────┐
       │                                   │ statistics_node  │ → END
       │                                   └─────────────────┘
       │
       └─── otherwise ──────────────────→ ┌────────────────┐
                                           │ unrelated_node  │ → END
                                           └────────────────┘
```

---

## 四、LangGraph vs 原始 LangChain 手写方式的对比

### 4.1 代码组织

| 维度 | 手写状态机 | LangGraph |
|------|-----------|-----------|
| 文件数量 | 4 个文件（classifier, state_manager, agent_router, classification_processor） | 1 个文件（graph_agent.py） |
| 代码行数 | ~400 行 | ~200 行 |
| 流程可读性 | 需要在 4 个文件间跳转才能理解完整流程 | 图定义集中在一处，一眼看清节点和边 |
| 状态管理 | 手动维护 SharedState + StateEnum + if/elif | 声明式 TypedDict，框架自动传递 |

### 4.2 扩展性

**场景：新增一个"支付 Agent"**

手写状态机需要改动：
1. `constants.py` — 加 `StateEnum.PAY`
2. `state_manager.py` — 加 `transition_to_payment()`、`is_in_payment_flow()`
3. `agent_router.py` — 加 `route_to_payment()` 方法
4. `classification_processor.py` — 在 `process_task_stream()` 里加 elif 分支
5. `task_classification_agent.py` — 构造函数加 payment_agent 参数

LangGraph 只需要：
1. 写一个 `payment_node()` 函数
2. `graph.add_node("payment_node", payment_node)`
3. 路由函数里加一个 `elif category == "pay": return "payment_node"`

**3 处改动 vs 5 处改动**，而且 LangGraph 的改动全部集中在一个文件里。

**实际验证**：在本项目中，我们后续补齐了 `pay_node` 和 `statistics_node`，只在 `graph_agent.py` 中新增了两个节点函数和路由分支，没有改动任何其他文件。这验证了 LangGraph 的扩展性优势。

### 4.3 多轮对话状态

| 维度 | 手写状态机 | LangGraph |
|------|-----------|-----------|
| 状态存储 | 全局变量 `SharedState.value` | State 对象的 `active_agent` 字段 |
| 持久化 | 需要自己实现（存 Redis/SQLite） | ✅ 已通过 `AsyncSqliteSaver` checkpointer 实现，状态存入 `data/langgraph_checkpoints.db` |
| 多用户隔离 | 需要手动按 session_id 维护多份状态 | ✅ 已通过 `thread_id` 参数实现，Agent 实例按 session 独立管理 |
| 服务重启恢复 | 状态丢失，需要重新开始对话 | ✅ checkpointer 自动恢复，多轮对话可无缝继续 |

### 4.4 调试与可视化

| 维度 | 手写状态机 | LangGraph |
|------|-----------|-----------|
| 调试 | print 日志，需要手动追踪状态流转 | 支持 LangSmith 追踪每个节点的输入输出 |
| 可视化 | 无 | `graph.get_graph().draw_mermaid()` 生成流程图 |
| 重放 | 不支持 | checkpointer 支持任意步骤重放 |

### 4.5 性能

两种方式在性能上**没有显著差别**。瓶颈在 LLM API 调用（~1-3秒），而不在编排层（~几毫秒）。LangGraph 多了一层抽象但开销忽略不计。

---

## 五、LangGraph 的优势总结

1. **声明式编排**：用"图"描述 Agent 协作，比命令式的 if/elif 更清晰
2. **关注点分离**：每个节点只管自己的事，路由逻辑独立为路由函数
3. **原生持久化**：checkpointer 机制开箱即用，天然支持对话历史持久化和多用户隔离
4. **生态整合**：LangSmith 追踪、LangGraph Platform 部署、LangGraph Studio 可视化调试
5. **循环支持**：如果未来需要"Agent 自我反思 → 重试"这类循环逻辑，StateGraph 原生支持 cycle

---

## 六、LangGraph 的局限

1. **学习曲线**：需要理解 State/Reducer/Conditional Edge 等概念，比直接写 if/elif 门槛高
2. **调试复杂性**：当图变大（10+ 节点），调试节点间的数据传递比线性代码更难
3. **版本迭代快**：LangGraph 还在快速演进，API 可能变化（从 0.x 到 1.x 有 breaking changes）
4. **过度工程化风险**：对于只有 2-3 个 Agent 的简单场景，手写状态机其实也够用。LangGraph 的优势在中大规模场景更明显

---

## 七、本项目的迁移策略

### 7.1 渐进式迁移，保留回退能力

```python
# api/chat_handler.py
USE_LANGGRAPH = os.getenv("USE_LANGGRAPH", "true").lower() != "false"

if USE_LANGGRAPH:
    from agents.graph_agent import process_user_input_graph
    # ... 走 LangGraph
else:
    from agents.task_classification_agent import TaskClassificationAgent
    # ... 走手写状态机
```

通过环境变量 `USE_LANGGRAPH=false` 可以随时切回旧版本，这是生产迁移的最佳实践。

### 7.2 保持原有代码不删除

原来的 `task_classification/` 目录下的 4 个文件全部保留，没有改动。这样做的好处：
- 对比学习：面试时可以展示"前后对比"
- 安全回退：如果 LangGraph 版本有问题可以秒切回去
- 测试验证：评估套件对两种实现都能跑

### 7.3 已落地的 LangGraph 进阶能力

在基础图编排完成后，进一步实现了对话持久化和多用户隔离：

| 能力 | 实现方式 | 状态 |
|------|---------|------|
| 对话持久化 | `AsyncSqliteSaver` checkpointer，数据存储在 `data/langgraph_checkpoints.db` | ✅ 已实现 |
| 多用户隔离 | `ainvoke(state, config={"configurable": {"thread_id": session_id}})` | ✅ 已实现 |
| 执行追踪 | 接入 LangSmith，自动记录每个节点的输入输出 | 🔜 后续接入 |
| 流程可视化 | `graph.get_graph().draw_mermaid_png()` | 🔜 后续接入 |

#### 7.3.1 对话持久化实现细节

```python
import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

CHECKPOINT_DB_PATH = "data/langgraph_checkpoints.db"

async def _get_checkpointer() -> AsyncSqliteSaver:
    conn = await aiosqlite.connect(CHECKPOINT_DB_PATH)
    checkpointer = AsyncSqliteSaver(conn)
    await checkpointer.setup()
    return checkpointer

# 编译时注入 checkpointer
graph = build_agent_graph()
checkpointer = await _get_checkpointer()
compiled_graph = graph.compile(checkpointer=checkpointer)
```

**效果**：
- 每次 `ainvoke` 执行后，`active_agent` 等状态自动写入 SQLite
- 服务重启后，用户的多轮对话可以无缝继续（比如预约说到一半重启，回来接着聊）
- 不再需要手动维护 `_current_active_agent` 全局变量

#### 7.3.2 多用户隔离实现细节

```python
# 每次调用时传入 thread_id
config = {
    "configurable": {
        "thread_id": session_id  # 用 session_id 作为 thread_id
    }
}
result = await compiled_graph.ainvoke(initial_state, config=config)
```

**效果**：
- 不同 `session_id` 的用户各自拥有独立的对话状态
- user_001 在预约流程中，不会影响 user_002 的咨询流程
- Agent 实例也按 session_id 隔离存储（字典替代原来的单例全局变量）

```python
# 改造前：单例全局变量
_appointment_agent: AppointmentAgent | None = None

# 改造后：按 session 隔离
_appointment_agents: dict[str, AppointmentAgent] = {}

def _get_appointment_agent(session_id: str) -> AppointmentAgent:
    if session_id not in _appointment_agents:
        _appointment_agents[session_id] = AppointmentAgent(session_id=session_id)
    return _appointment_agents[session_id]
```

#### 7.3.3 新增依赖

```
# requirements.txt 新增
langgraph-checkpoint-sqlite>=3.0.0
```

#### 7.3.4 可选环境变量

```env
# 默认值为 data/langgraph_checkpoints.db，一般不需要修改
CHECKPOINT_DB_PATH=data/langgraph_checkpoints.db
```

---

## 八、补齐 Pay 和 Statistics 节点

在 LangGraph 改造的基础上，我们补齐了 `pay` 和 `statistics` 两个分类对应的处理节点，使得 TaskClassifier 能分出的 5 个类别全部有完整的处理逻辑。

### 8.1 Pay 节点（支付处理）

**业务场景**：预约确认后，appointment 机器人通知"用户已选择某位技师做某项目"。

**处理逻辑**：
1. 用正则从消息中提取技师名和服务项目
2. 根据服务项目查询价格（全身推拿 ¥120、肩颈 ¥80、足底 ¥100、背部 ¥90）
3. 生成唯一订单号（基于时间戳）
4. 返回结构化的支付确认信息

**示例输出**：
```
输入: "预约已确认，用户选择了张伟技师做全身推拿"
输出:
  📋 订单号：ORD4082031
  👨‍⚕️ 技师：张伟
  💆 项目：全身推拿
  💰 金额：¥120
  支付方式：微信支付 / 支付宝 / 到店现金
  ✅ 预约已确认，请在到店时完成支付。
```

### 8.2 Statistics 节点（统计处理）

**业务场景**：工作人员通知"某技师已完成当前任务"。

**处理逻辑**：
1. 用正则从消息中提取技师名或编号
2. 通过 AppointmentService 查询技师信息
3. 查找该技师当前正在进行的服务（status=busy 的排班记录）
4. 更新排班状态为 `completed`
5. 释放内存中的忙碌时间段
6. 统计该技师今日已完成的服务次数
7. 返回确认消息

**示例输出**：
```
输入: "张伟技师已完成本次服务"
输出:
  ✅ 已记录：张伟 完成本次服务
  📋 排班状态已更新为'已完成'
  🕐 技师时间已释放，可接新预约
  📊 今日已完成服务：3 次
```

### 8.3 扩展验证

补齐这两个节点的过程验证了 LangGraph 的扩展性：
- **只改了 1 个文件**（`graph_agent.py`）
- **新增约 120 行代码**（两个节点函数 + 路由分支）
- **没有改动任何其他模块**
- **评估结果不变**：96.9% 分类准确率，5 个类别全部有处理逻辑

---

## 九、面试话术

> "原来的系统用手写状态机管理 Agent 协作，代码分散在 4 个文件约 400 行，扩展新 Agent 需要改 5 处。我用 LangGraph 重构了编排层，把整个协作流程定义为一张有向图，代码收敛到 1 个文件约 200 行。后来补齐 pay 和 statistics 两个节点时，只在一个文件里加了 120 行代码就完成了，验证了架构的扩展性。我还落地了 checkpointer 持久化和多用户隔离——用 AsyncSqliteSaver 把对话状态自动存到 SQLite，服务重启后多轮对话不丢失；用 thread_id 实现不同用户的对话完全独立。迁移过程中做了向后兼容设计，通过环境变量可以秒切回旧实现。"


---

## 九、LangGraph 完整运行流程图

### 9.1 全局视角：从用户输入到最终响应

```
┌─────────────────────────────────────────────────────────────────────┐
│                        用户在浏览器输入消息                            │
└────────────────────────────────┬────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Web 层：POST /chat/stream                                           │
│  ├── web/routes.py 接收请求                                          │
│  └── 调用 api/chat_handler.py → ProcessUserInput_stream()            │
└────────────────────────────────┬────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│  chat_handler.py 判断模式                                            │
│  ├── USE_LANGGRAPH=true  → agents/graph_agent.py                     │
│  └── USE_LANGGRAPH=false → agents/task_classification_agent.py       │
└────────────────────────────────┬────────────────────────────────────┘
                                 │ (默认走 LangGraph)
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│  graph_agent.py → process_user_input_graph()                         │
│                                                                      │
│  1. 初始化 AsyncSqliteSaver checkpointer（懒加载，首次调用时创建）     │
│  2. 构造 AgentState 初始状态                                          │
│  3. 调用 compiled_graph.ainvoke(state, config={thread_id: session_id})│
│     ├─ checkpointer 自动恢复该 thread_id 上次的 active_agent 状态     │
│     └─ 执行完成后自动保存新状态到 SQLite                              │
│  4. 逐个 yield output_tokens                                         │
└────────────────────────────────┬────────────────────────────────────┘
                                 │
                                 ▼
                    ┌────── LangGraph 内部执行 ──────┐
                    │                                │
                    ▼                                │
```

### 9.2 LangGraph StateGraph 内部执行流程

```
                        ┌─────────┐
                        │  START  │
                        └────┬────┘
                             │
                             ▼
                  ┌────────────────────┐
                  │   classify_node    │
                  │                    │
                  │ 1. 检查 active_agent│
                  │    ├─ 如果在多轮中  │
                  │    │  跳过分类      │
                  │    │  直接沿用上次  │
                  │    └─ 否则调用 LLM  │
                  │       判断意图      │
                  │                    │
                  │ 输出: category      │
                  └────────┬───────────┘
                           │
                           │ route_after_classify()
                           │ 根据 category 选择路径
                           │
            ┌──────────────┼──────────────┐
            │              │              │
            ▼              ▼              ▼
  ┌─────────────────┐  ┌──────────────────┐  ┌────────────────┐
  │appointment_node │  │consultation_node │  │ unrelated_node │
  │                 │  │                  │  │                │
  │ 1.获取Agent实例 │  │ 1.获取Agent实例  │  │ 返回友好拒绝   │
  │ 2.调用          │  │ 2.初始化知识库   │  │ 引导回正题     │
  │   run_stream()  │  │ 3.调用           │  │                │
  │ 3.收集tokens    │  │   consult_stream │  │ active_agent   │
  │ 4.判断是否完成  │  │ 4.收集tokens     │  │   = "none"     │
  │   ├─完成:       │  │ 5.重置状态       │  │                │
  │   │ active=none │  │                  │  │                │
  │   └─未完成:     │  │ active_agent     │  │                │
  │     active=     │  │   = "none"       │  │                │
  │     appointment │  │                  │  │                │
  └────────┬────────┘  └────────┬─────────┘  └───────┬────────┘
           │                    │                     │
           ▼                    ▼                     ▼
        ┌─────┐             ┌─────┐              ┌─────┐
        │ END │             │ END │              │ END │
        └─────┘             └─────┘              └─────┘
```

### 9.3 多轮对话状态流转

```
第 1 轮：用户说 "我想预约"
─────────────────────────────────────────
active_agent = "none"
    → classify_node 调用 LLM 判断 → category = "appointment"
    → appointment_node 执行 → 信息不完整，追问时间
    → active_agent 设为 "appointment"

第 2 轮：用户说 "今天下午3点"
─────────────────────────────────────────
active_agent = "appointment"  ← 上一轮保留
    → classify_node 检测到 active_agent，跳过分类
    → appointment_node 继续执行 → 追问项目和时长
    → active_agent 保持 "appointment"

第 3 轮：用户说 "全身推拿，60分钟，要女技师"
─────────────────────────────────────────
active_agent = "appointment"
    → classify_node 跳过分类
    → appointment_node 执行 → 信息齐全，匹配技师，预约成功
    → agent.finished = True
    → active_agent 重置为 "none"

第 4 轮：用户说 "你们几点关门"
─────────────────────────────────────────
active_agent = "none"  ← 已重置
    → classify_node 调用 LLM 重新分类 → category = "consultation"
    → consultation_node 执行 RAG 检索 + 生成回答
    → active_agent = "none"
```

### 9.4 预约节点内部详细流程

```
┌─────────────── appointment_node ────────────────────┐
│                                                      │
│  AppointmentAgent.run_stream(user_input)             │
│                                                      │
│  ┌──────────────────┐                               │
│  │ InputParser      │  LLM 从自然语言提取结构化数据   │
│  │ (Qwen-Plus 调用) │  → {time, project, duration,   │
│  └────────┬─────────┘    gender, technician_name}    │
│           │                                          │
│           ▼                                          │
│  ┌──────────────────────┐                           │
│  │ update_history       │  更新预约信息字典           │
│  │ 检查是否信息齐全     │                            │
│  └────────┬─────────────┘                           │
│           │                                          │
│      ┌────┴────┐                                    │
│      │         │                                    │
│   齐全      不齐全                                   │
│      │         │                                    │
│      ▼         ▼                                    │
│  ┌────────┐  ┌──────────────────┐                   │
│  │匹配技师│  │ 生成追问消息     │                    │
│  │检查排班│  │ "请问您想什么时间│                    │
│  │写入数据│  │  做哪个项目？"   │                    │
│  └────┬───┘  └──────────────────┘                   │
│       │                                              │
│       ▼                                              │
│  ┌──────────────────────────┐                       │
│  │ 调用天气 Agent (Tool Use)│                        │
│  │ 获取北京天气             │                        │
│  │ 生成温馨提示             │                        │
│  └──────────────────────────┘                       │
│                                                      │
└──────────────────────────────────────────────────────┘
```

### 9.5 咨询节点内部详细流程

```
┌────────────── consultation_node ────────────────────┐
│                                                      │
│  ConsultantAgent.consult_stream(user_input)          │
│                                                      │
│  ┌──────────────────────┐                           │
│  │ ConsultationClassifier│  LLM 判断是否咨询相关     │
│  └────────┬─────────────┘                           │
│           │                                          │
│      ┌────┴────┐                                    │
│      │         │                                    │
│    相关      不相关 → 转交归类机器人                  │
│      │                                              │
│      ▼                                              │
│  ┌──────────────────────┐                           │
│  │ KnowledgeRetriever   │                           │
│  │                      │                           │
│  │ 1. Embedding 向量化   │  ← text-embedding-v3     │
│  │    用户问题           │                           │
│  │ 2. FAISS 相似度搜索   │  ← 纯数学计算            │
│  │ 3. 返回 Top-K 文档    │                           │
│  └────────┬─────────────┘                           │
│           │                                          │
│           ▼                                          │
│  ┌──────────────────────┐                           │
│  │ ResponseGenerator    │                           │
│  │                      │                           │
│  │ 1. 构建 Prompt       │                           │
│  │    (检索结果+问题)    │                           │
│  │ 2. Qwen-Plus 生成    │  ← LLM 调用              │
│  │ 3. 流式输出回答       │                           │
│  └──────────────────────┘                           │
│                                                      │
└──────────────────────────────────────────────────────┘
```

### 9.6 数据流全景

```
┌────────────────────────────────────────────────────────────────┐
│                         AgentState                              │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│  user_input: "我想今天3点预约女技师做全身推拿60分钟"              │
│                     │                                          │
│                     ▼                                          │
│  classify_node 写入:                                           │
│  category: "appointment"                                       │
│                     │                                          │
│                     ▼                                          │
│  appointment_node 写入:                                        │
│  output_tokens: [                                              │
│    "[THOUGHT] 这是预约任务...",                                  │
│    "[REPLY] 已为您预约技师小美..."                               │
│  ]                                                             │
│  active_agent: "none"  (预约完成，重置)                         │
│                                                                │
├────────────────────────────────────────────────────────────────┤
│  Reducer 机制：output_tokens 使用 add 追加                      │
│  → 多个节点的输出自动拼接，不会互相覆盖                          │
└────────────────────────────────────────────────────────────────┘
```
