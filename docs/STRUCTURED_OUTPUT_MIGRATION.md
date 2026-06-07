# Function Calling / Structured Output 改造说明

## 一、改造背景

### 原来的问题

预约 Agent 的 `InputParser` 需要从用户自然语言中提取结构化的预约信息（时间、项目、时长、性别偏好等）。原实现方式是：

```python
# 旧版：让 LLM 自己输出 JSON 字符串
prompt = """
请你只输出纯JSON格式，不要添加任何markdown标记如```json或```...
{
  "gender": "技师性别（如男/女/未知）",
  "start_time": "预约起始时间...",
  ...
}
再次强调：只输出纯JSON，不要有任何代码块标记或其他文字。
"""

# stream 收集 LLM 输出的文本
ai_content = ""
for token in chain.stream(input):
    ai_content += token

# 手动 json.loads 解析
try:
    data = json.loads(ai_content)
except json.JSONDecodeError:
    data = fallback_defaults  # 容错
```

**这种方式的问题**：

1. **格式不保证**：LLM 偶尔会加 ` ```json ` 标记、多余解释文字、或输出不合法的 JSON
2. **容错代码多余**：需要 try/except + fallback 逻辑来处理解析失败
3. **Prompt 臃肿**：大量篇幅在教 LLM "输出格式"，而不是"业务逻辑"
4. **类型不安全**：`json.loads` 返回 dict，字段类型完全靠运行时猜测

---

## 二、Structured Output 是什么

Structured Output（结构化输出）是 OpenAI 在 2024 年推出的能力，底层基于 **Function Calling**。核心思想是：

> 不要让 LLM "自己想怎么输出就怎么输出"，而是告诉它"你必须按照这个 Schema 返回数据"。

技术原理：
1. 你定义一个 JSON Schema（或 Pydantic Model）
2. API 调用时把 Schema 传给模型
3. 模型的输出被**约束**在 Schema 范围内，保证格式合法
4. 返回的数据直接可以反序列化为对象

在 LangChain 中，通过 `llm.with_structured_output(PydanticModel)` 一行代码实现。

---

## 三、改造后的实现

### 3.1 定义 Pydantic Schema

```python
from pydantic import BaseModel, Field
from typing import List

class AppointmentExtraction(BaseModel):
    """从用户输入中提取的预约相关信息"""
    
    gender: str = Field(
        default="未知",
        description="用户期望的技师性别。可选值：男、女、未知"
    )
    start_time: str = Field(
        default="未知",
        description="预约起始时间，格式 YYYY-MM-DD HH:MM"
    )
    duration: str = Field(
        default="未知",
        description="服务时长，统一转换为'X分钟'格式"
    )
    project: str = Field(
        default="未知",
        description="服务项目名称，如：全身按摩、肩颈按摩"
    )
    preference: str = Field(
        default="无",
        description="用户对技师的偏好，如：力气大、力气小"
    )
    technician_name: str = Field(
        default="未知",
        description="用户指定的技师姓名"
    )
    confirmation: str = Field(
        default="未知",
        description="对推荐技师的确认回复（是/好/不）"
    )
    unrelated: bool = Field(
        default=False,
        description="用户输入是否与预约完全无关"
    )
    info_complete: bool = Field(
        default=False,
        description="预约信息是否已完整收集"
    )
    missing_info: List[str] = Field(
        default_factory=list,
        description="缺失的信息字段列表"
    )
```

**Schema 即文档**：每个字段的 `description` 既是给 LLM 的指令，也是给开发者的文档。

### 3.2 绑定 Schema 到 LLM

```python
class InputParser:
    def __init__(self, llm):
        self.llm = llm
        # 一行代码绑定，LLM 调用后直接返回 AppointmentExtraction 对象
        self.structured_llm = llm.with_structured_output(AppointmentExtraction)
```

### 3.3 调用方式

```python
def parse_stream(self, user_input, chat_history):
    prompt = self._build_extraction_prompt(user_input, history_str)
    
    # 直接返回 Pydantic 对象，格式保证合法
    result: AppointmentExtraction = self.structured_llm.invoke(prompt)
    
    # 序列化为 JSON（兼容下游接口）
    json_str = json.dumps(result.model_dump(), ensure_ascii=False)
    yield json_str
```

### 3.4 后处理标准化

虽然 Structured Output 保证了格式合法（是合法的 JSON），但 LLM 对具体**值的内容**（如时间格式用 ISO 还是自定义格式）仍有不确定性。所以加了一个标准化层：

```python
def parse_data(self, ai_content: str) -> dict:
    data = json.loads(ai_content)  # 100% 不会失败
    
    # 标准化时间格式：ISO → "YYYY-MM-DD HH:MM"
    # 标准化时长：{value:60, unit:"分钟"} → "60分钟"
    # 标准化空值：""/"无" → "未知"
    
    return data
```

---

## 四、改造前后对比

### 4.1 代码对比

| 维度 | 旧版（JSON Prompt） | 新版（Structured Output） |
|------|------|------|
| Prompt 行数 | 45 行（大量格式说明） | 15 行（只说业务逻辑） |
| 解析代码 | `json.loads` + try/except + fallback | `result.model_dump()`（Pydantic 保证类型） |
| 容错逻辑 | 必须有，LLM 随时可能输出非法格式 | 几乎不需要，Schema 约束了输出 |
| 类型安全 | dict[str, Any]，运行时才知道类型 | AppointmentExtraction 对象，IDE 有提示 |
| 总行数 | 97 行 | 160 行（含 Schema 定义和标准化，但逻辑更清晰） |

### 4.2 可靠性对比

旧版容易失败的场景：
```
# LLM 实际输出（加了 markdown 标记）:
```json
{"gender": "男", "start_time": "2026-06-07 15:00", ...}
```

# json.loads() 会失败，因为前后有 ```json 和 ```
```

新版不可能出现这种情况，因为 LLM 通过 Function Calling 协议返回数据，不是作为文本输出。

### 4.3 Prompt 对比

**旧版 Prompt（截取关键部分）**：
```
重要：请你只输出纯JSON格式，不要添加任何markdown标记如```json或```，不要添加任何其他文字说明...
再次强调：只输出纯JSON，不要有任何代码块标记或其他文字。
```

**新版 Prompt**：
```
请从用户输入中提取预约信息。注意事项：
1. 时间转换：'今天下午3点'→当天15:00
2. 时长统一为分钟：1小时→60分钟
3. 如果用户指定了技师姓名，务必提取到 technician_name
4. 只有完全无关的话题才标记 unrelated=true
```

新版 Prompt 不再需要花大量篇幅教 LLM 输出格式，可以专注在业务逻辑上。

---

## 五、技术原理：Function Calling 如何保证格式

```
┌─────────┐    请求（含 JSON Schema）     ┌─────────┐
│  Client │  ─────────────────────────→  │   LLM   │
│         │                               │         │
│         │  ← function_call response ──  │  (被约  │
│         │    {arguments: {...}}          │  束输出) │
└─────────┘                               └─────────┘
```

1. Client 在 API 请求中附带 `tools` 参数，定义函数的参数 Schema
2. LLM 生成 token 时，输出被约束在 Schema 的 JSON 语法内（通过 constrained decoding）
3. 返回的 `function_call.arguments` 是 **保证合法的 JSON 字符串**
4. Client 直接 `json.loads()` + Pydantic 校验

这种方式叫做 **Constrained Generation（受约束生成）**，是在模型推理的 logits 层面做的约束，不是后处理。

---

## 六、兼容性说明

### 对下游代码的影响

`InputParser` 的对外接口（`parse_stream` + `parse_data`）没有变化：
- `parse_stream` 仍然返回 Generator，yield 的是 JSON 字符串
- `parse_data` 仍然接收字符串，返回 dict

所以 `AppointmentAgent.run_stream()` 和 `AppointmentProcessor` 的代码完全不需要改动。

### 模型兼容性

`with_structured_output` 要求模型支持 Function Calling / Tool Use。以下模型支持：
- ✅ Qwen (通义千问) - 本项目使用
- ✅ OpenAI GPT-3.5/4
- ✅ DeepSeek
- ✅ Zhipu (智谱)
- ✅ Azure OpenAI

如果模型不支持 Function Calling，LangChain 会自动 fallback 到 JSON 模式。

---

## 七、面试话术

> "原来的 InputParser 用 Prompt 教 LLM 输出 JSON 字符串，但 LLM 偶尔会加 markdown 标记或多余文字导致 json.loads 失败，需要容错代码处理。我用 LangChain 的 `with_structured_output` 重构了这部分，它底层走 Function Calling 协议，在模型推理阶段就约束了输出格式，保证返回的一定是合法的结构化数据。代码从'教 LLM 输出格式'变成了'定义 Pydantic Schema + 专注业务逻辑'，Prompt 精简了 60%，容错代码基本删除了。"
