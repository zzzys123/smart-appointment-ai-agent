"""
用户输入解析器（Structured Output 版本）

使用 LLM 的 Function Calling / Structured Output 能力，
让模型直接返回 Pydantic 对象，而不是让模型自行输出 JSON 字符串再手动解析。

优势：
- 格式保证合法，不会出现 markdown 标记或多余文字
- 类型校验由 Pydantic 自动完成
- 字段描述即文档，Prompt 更简洁
- 消除 json.loads() 容错逻辑
"""

from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from langchain_core.chat_history import InMemoryChatMessageHistory
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, AIMessage


# ============================================================
# Pydantic Schema 定义 - 预约信息的结构化输出格式
# ============================================================

class AppointmentExtraction(BaseModel):
    """从用户输入中提取的预约相关信息"""
    
    gender: str = Field(
        default="未知",
        description="用户期望的技师性别。可选值：男、女、未知。如果用户没有提及性别偏好则为'未知'"
    )
    start_time: str = Field(
        default="未知",
        description=(
            "预约起始时间，必须转换为标准格式 YYYY-MM-DD HH:MM。"
            "例如用户说'今天下午3点'应转换为当天日期 15:00，'明天上午10点'应转换为明天日期 10:00。"
            "如果用户只说了时间没说日期，默认为今天。"
            "如果完全没有提及时间信息则为'未知'"
        )
    )
    duration: str = Field(
        default="未知",
        description=(
            "服务时长，统一转换为'X分钟'格式。"
            "例如：1小时→60分钟，2小时→120分钟，半小时→30分钟。"
            "如果用户没有明确说明时长则为'未知'"
        )
    )
    project: str = Field(
        default="未知",
        description="服务项目名称，如：全身按摩、肩颈按摩、足底按摩、背部推拿。如果用户没有指定则为'未知'"
    )
    preference: str = Field(
        default="无",
        description="用户对技师的偏好，如：力气大、力气小、手法轻柔。如果没有特殊偏好则为'无'"
    )
    technician_name: str = Field(
        default="未知",
        description="用户指定的技师姓名。例如：张伟、李小美。如果用户没有指定具体技师则为'未知'"
    )
    confirmation: str = Field(
        default="未知",
        description=(
            "仅当用户在回应技师推荐确认问题时填写。"
            "提取用户的确认意图，如：是、好、可以、不、不要。"
            "如果当前对话不涉及推荐确认则为'未知'"
        )
    )
    unrelated: bool = Field(
        default=False,
        description=(
            "用户的输入是否与预约服务完全无关（如问天气、闲聊、写诗等）。"
            "注意：对推荐技师的简短确认回复（是/不）不算无关"
        )
    )
    info_complete: bool = Field(
        default=False,
        description=(
            "预约所需信息是否已全部收集完成。判断标准："
            "1）如果指定了technician_name（不为'未知'）：需要start_time、project、duration都不为'未知'；"
            "2）如果没有指定technician_name：需要start_time、project、duration、gender都不为'未知'"
        )
    )
    missing_info: List[str] = Field(
        default_factory=list,
        description="缺失的信息字段列表，如：['start_time', 'gender']。如果info_complete为true则为空列表"
    )


# ============================================================
# InputParser 实现
# ============================================================

class InputParser:
    """
    用户输入解析器 - 使用 Structured Output（Function Calling）
    
    对比旧版：
    - 旧版：Prompt 教 LLM 输出 JSON → stream 收集文本 → json.loads() 解析 → 容错处理
    - 新版：Prompt + Pydantic Schema → LLM 直接返回结构化对象 → 类型安全，零容错代码
    """
    
    def __init__(self, llm: BaseChatModel):
        self.llm = llm
        # 绑定 Pydantic Schema，LLM 会通过 Function Calling 返回结构化数据
        self.structured_llm = llm.with_structured_output(AppointmentExtraction)
    
    def _build_extraction_prompt(self, user_input: str, history_str: str) -> str:
        """构建提取提示词"""
        from config.time_config import time_config
        current_date = time_config.current_date_str()
        current_datetime = time_config.current_datetime_str()
        
        return (
            f"你是一个预约机器人，负责从用户输入中提取预约相关信息。\n"
            f"当前日期是{current_date}，当前北京时间是{current_datetime}。\n\n"
            f"对话历史：\n{history_str}\n\n"
            f"用户最新输入：{user_input}\n\n"
            "请从用户输入中提取预约信息。注意事项：\n"
            "1. 时间转换：'今天下午3点'→当天15:00，'明天上午10点'→明天10:00，只说时间默认今天\n"
            "2. 时长统一为分钟：1小时→60分钟，2小时→120分钟\n"
            "3. 如果用户明确指定了技师姓名（如'张伟技师'），务必提取到 technician_name\n"
            "4. 如果用户在回应推荐技师的确认（如简短回复'是'、'好'、'不'），提取到 confirmation，不要标记为 unrelated\n"
            "5. 只有与预约完全无关的话题（天气、股票、写诗）才标记 unrelated=true\n"
            "6. 未提及的字段保持默认值（'未知'或'无'）"
        )
    
    def parse_stream(self, user_input: str, chat_history: InMemoryChatMessageHistory):
        """
        解析用户输入（兼容原接口的 generator 形式）
        
        注意：Structured Output 不支持真正的流式，因为需要完整的 function call 结果。
        但为了保持与 AppointmentAgent.run_stream() 的兼容性，
        我们仍然返回一个 generator，只是最终 yield 的是序列化后的 JSON。
        """
        # 添加用户消息到历史
        chat_history.add_message(HumanMessage(content=user_input))
        
        # 构建历史字符串
        history_str = "\n".join(
            [f"用户：{m.content}" if m.type == "human" else f"机器人：{m.content}" 
             for m in chat_history.messages[-10:]]  # 只保留最近 10 条避免 context 过长
        )
        
        # 构建 prompt
        prompt = self._build_extraction_prompt(user_input, history_str)
        
        # 调用 Structured Output（同步，因为原接口是 generator 不是 async generator）
        try:
            result: AppointmentExtraction = self.structured_llm.invoke(prompt)
            # 序列化为 JSON 字符串以兼容下游 parse_data()
            import json
            json_str = json.dumps(result.model_dump(), ensure_ascii=False)
            yield json_str
        except Exception as e:
            # 如果 structured output 失败，回退到默认值
            import json
            fallback = AppointmentExtraction().model_dump()
            yield json.dumps(fallback, ensure_ascii=False)
        
        # 添加到历史（记录提取结果摘要，而非原始 JSON）
        chat_history.add_message(AIMessage(content=f"[已提取预约信息]"))
    
    def parse_data(self, ai_content: str) -> Dict[str, Any]:
        """
        解析结构化输出结果并标准化字段值
        
        Structured output 保证了类型安全，但 LLM 对具体值的格式（如时间格式、
        时长表达）不一定完全符合下游期望，所以这里做标准化处理。
        """
        import json
        import re
        try:
            data = json.loads(ai_content)
        except (json.JSONDecodeError, TypeError):
            return AppointmentExtraction().model_dump()
        
        # 标准化 start_time：统一为 YYYY-MM-DD HH:MM 格式
        start_time = data.get("start_time", "未知")
        if start_time and start_time not in ("未知", "无", ""):
            # 处理 ISO 格式 (2026-06-07T15:00:00+08:00 → 2026-06-07 15:00)
            if "T" in start_time:
                start_time = start_time.split("T")[0] + " " + start_time.split("T")[1][:5]
            # 去掉可能的时区信息
            start_time = re.sub(r'[+-]\d{2}:\d{2}$', '', start_time).strip()
            # 确保格式正确
            if re.match(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}', start_time):
                data["start_time"] = start_time[:16]  # 截取到分钟
            else:
                data["start_time"] = "未知"
        else:
            data["start_time"] = "未知"
        
        # 标准化 duration：统一为 "X分钟" 字符串格式
        duration = data.get("duration", "未知")
        if duration and duration not in ("未知", "无", ""):
            if isinstance(duration, dict):
                # 处理 {"value": 60, "unit": "分钟"} 格式
                val = duration.get("value", 0)
                data["duration"] = f"{val}分钟" if val else "未知"
            elif isinstance(duration, str):
                # 提取数字
                nums = re.findall(r'\d+', duration)
                if nums:
                    minutes = int(nums[0])
                    # 如果包含"小时"，转换为分钟
                    if "小时" in duration:
                        minutes = minutes * 60
                    data["duration"] = f"{minutes}分钟"
                else:
                    data["duration"] = "未知"
        else:
            data["duration"] = "未知"
        
        # 标准化空值：统一 "" 和 "无" 为 "未知"（对于需要 "未知" 语义的字段）
        for field in ["gender", "start_time", "project", "technician_name"]:
            val = data.get(field, "未知")
            if val in ("", "无", None):
                data[field] = "未知"
        
        # preference 的空值统一为 "无"
        pref = data.get("preference", "无")
        if pref in ("", "未知", None):
            data["preference"] = "无"
        
        # confirmation 的空值统一为 "未知"
        conf = data.get("confirmation", "未知")
        if conf in ("", "无", None):
            data["confirmation"] = "未知"
        
        return data
