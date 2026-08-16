"""
LangGraph Agent 编排器

用 LangGraph 的 StateGraph 替代手写的状态机（StateManager + AgentRouter + ClassificationProcessor），
将 Agent 协作流程定义为一张有向图：

    START → classify → appointment_node / consultation_node / unrelated_node → END

特性：
- 扩展新 Agent 只需要加节点 + 边，不用改 if/elif
- 状态自动管理，不需要手写 StateManager
- 通过 SqliteSaver checkpointer 实现对话历史持久化（服务重启后状态不丢失）
- 通过 thread_id 实现多用户对话隔离
- LangChain 官方主推方案，展示技术前沿性
"""

from __future__ import annotations

import os
import uuid
import sqlite3
import logging
from pathlib import Path
from typing import TypedDict, Literal, Annotated
from operator import add

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
import aiosqlite

from dotenv import load_dotenv
load_dotenv()

from config.model_provider import create_chat_model
from agents.appointment_agent import AppointmentAgent
from agents.consultant_agent import ConsultantAgent
from agents.task_classification.task_classifier import TaskClassifier

logger = logging.getLogger(__name__)


# ============================================================
# 1. 定义 Graph 的状态 Schema
# ============================================================

class AgentState(TypedDict):
    """Graph 状态定义 - 在节点之间传递的数据"""
    # 用户当前输入
    user_input: str
    # 任务分类结果
    category: str
    # 流式输出 token 的累积列表（用 Annotated + add 做 reducer，每个节点 append）
    output_tokens: Annotated[list[str], add]
    # 会话 ID
    session_id: str
    # 当前对话是否处于某个 Agent 的多轮流程中
    active_agent: str  # "none", "appointment", "consultation"


# ============================================================
# 2. Checkpointer 配置 - 对话持久化
# ============================================================

# 持久化数据库路径（与业务数据库分开，避免互相影响）
CHECKPOINT_DB_PATH = os.getenv(
    "CHECKPOINT_DB_PATH",
    "data/langgraph_checkpoints.db"
)


# ============================================================
# 3. 定义各个节点函数
# ============================================================

# Agent 实例按 session_id 管理，支持多用户隔离
_appointment_agents: dict[str, AppointmentAgent] = {}
_consultant_agents: dict[str, ConsultantAgent] = {}
_classifier: TaskClassifier | None = None


def _get_appointment_agent(session_id: str) -> AppointmentAgent:
    """获取或创建 AppointmentAgent 实例（按 session 隔离）"""
    if session_id not in _appointment_agents:
        _appointment_agents[session_id] = AppointmentAgent(session_id=session_id)
    return _appointment_agents[session_id]


def _get_consultant_agent(session_id: str) -> ConsultantAgent:
    """获取或创建 ConsultantAgent 实例（按 session 隔离）"""
    if session_id not in _consultant_agents:
        _consultant_agents[session_id] = ConsultantAgent(session_id=session_id)
    return _consultant_agents[session_id]


def _get_classifier() -> TaskClassifier:
    """获取或创建 TaskClassifier 实例"""
    global _classifier
    if _classifier is None:
        llm = create_chat_model(temperature=0)
        _classifier = TaskClassifier(llm)
    return _classifier


async def classify_node(state: AgentState) -> dict:
    """
    分类节点 - 判断用户意图
    
    如果当前已经在某个 Agent 的多轮流程中，跳过分类，直接沿用上次的 active_agent
    """
    active_agent = state.get("active_agent", "none")
    
    # 如果已经在多轮流程中，不重新分类
    if active_agent in ("appointment", "consultation"):
        return {"category": active_agent}
    
    # 进行意图分类
    classifier = _get_classifier()
    user_input = state["user_input"]
    category = await classifier.classify_task(user_input)
    
    # 映射分类结果
    if category == "appointment":
        return {"category": "appointment"}
    elif category == "query":
        return {"category": "consultation"}
    elif category == "pay":
        return {"category": "pay"}
    elif category == "statistics":
        return {"category": "statistics"}
    else:
        return {"category": "unrelated"}


async def appointment_node(state: AgentState) -> dict:
    """
    预约节点 - 调用 AppointmentAgent 处理预约流程
    """
    session_id = state.get("session_id", "default")
    agent = _get_appointment_agent(session_id)
    user_input = state["user_input"]
    from services.redis_service import get_redis_service

    redis_service = get_redis_service()
    session_state = await redis_service.get_session(session_id)
    agent.restore_snapshot(session_state.get("appointment_agent"))
    
    tokens = []
    tokens.append("[THOUGHT][归类机器人] 归类机器人：我发现这是一个预约任务，我将转给预约机器人处理。")
    
    try:
        async for token in agent.run_stream(user_input=user_input):
            tokens.append(token)
    except Exception as e:
        tokens.append(f"[ERROR]预约处理失败: {str(e)}")
        return {"output_tokens": tokens, "active_agent": "none"}
    finally:
        session_state["appointment_agent"] = agent.create_snapshot()
        await redis_service.save_session(session_id, session_state)
    
    # 判断预约是否完成：如果 agent.finished 或者状态回到 CLASSIFY
    if agent.finished or agent.last_run_completed:
        return {"output_tokens": tokens, "active_agent": "none"}
    else:
        # 预约流程还没结束（多轮对话中），标记 active_agent 为 appointment
        return {"output_tokens": tokens, "active_agent": "appointment"}


async def consultation_node(state: AgentState) -> dict:
    """
    咨询节点 - 调用 ConsultantAgent 处理知识问答
    """
    session_id = state.get("session_id", "default")
    agent = _get_consultant_agent(session_id)
    user_input = state["user_input"]
    
    tokens = []
    tokens.append("[THOUGHT][归类机器人] 归类机器人：我发现这是一个咨询任务，我将转给咨询机器人处理。")
    
    try:
        async with agent as ctx:
            async for token in ctx.consult_stream(user_input):
                tokens.append(token)
    except Exception as e:
        tokens.append(f"[ERROR]咨询处理失败: {str(e)}")
    
    # 咨询通常是一轮完成，重置 active_agent
    return {"output_tokens": tokens, "active_agent": "none"}


async def unrelated_node(state: AgentState) -> dict:
    """
    无关请求节点 - 友好拒绝并引导用户
    """
    reply = "暂不支持该类型任务。请只询问和按摩、预约相关的问题。"
    tokens = ["[REPLY][归类机器人]" + reply]
    return {"output_tokens": tokens, "active_agent": "none"}


async def pay_node(state: AgentState) -> dict:
    """
    支付处理节点 - 处理预约确认后的支付环节
    
    业务场景：预约确认后（如 appointment 机器人通知"用户已选择某位技师做某项目"），
    进入支付环节，生成订单信息和支付确认。
    """
    import re
    import time
    
    user_input = state["user_input"]
    tokens = []
    tokens.append("[THOUGHT][归类机器人] 归类机器人：我发现这是一个支付任务，我将进行支付处理。")
    
    # 服务项目对应价格（从知识库定义）
    service_prices = {
        "全身推拿": 120, "全身按摩": 120,
        "肩颈推拿": 80, "肩颈按摩": 80,
        "足底按摩": 100, "足底推拿": 100,
        "背部推拿": 90, "背部按摩": 90,
    }
    
    try:
        # 从消息中提取技师名和服务项目
        technician_name = None
        service_project = None
        
        # 尝试提取技师名（常见格式："选择了张伟技师"、"张伟技师做"）
        name_match = re.search(r'(?:选择了|预约了?)?(\w{2,4})(?:技师)?(?:做|的)', user_input)
        if name_match:
            technician_name = name_match.group(1)
        
        # 尝试提取服务项目
        for service_name in service_prices.keys():
            if service_name in user_input:
                service_project = service_name
                break
        
        # 如果没找到具体项目，用默认
        if not service_project:
            # 检查是否有 "按摩" 或 "推拿" 关键词
            if "按摩" in user_input or "推拿" in user_input:
                service_project = "全身推拿"
            else:
                service_project = "全身推拿"
        
        # 计算价格
        price = service_prices.get(service_project, 120)
        
        # 生成订单号
        order_id = f"ORD{int(time.time() * 1000) % 10000000:07d}"
        
        # 构建支付确认消息
        reply_parts = [f"\n机器人：支付确认信息\n"]
        reply_parts.append(f"{'─' * 30}\n")
        reply_parts.append(f"📋 订单号：{order_id}\n")
        if technician_name:
            reply_parts.append(f"👨‍⚕️ 技师：{technician_name}\n")
        reply_parts.append(f"💆 项目：{service_project}\n")
        reply_parts.append(f"💰 金额：¥{price}\n")
        reply_parts.append(f"{'─' * 30}\n")
        reply_parts.append(f"支付方式：微信支付 / 支付宝 / 到店现金\n")
        reply_parts.append(f"✅ 预约已确认，请在到店时完成支付。\n")
        
        tokens.append("[REPLY][支付机器人]" + "".join(reply_parts))
        
    except Exception as e:
        tokens.append(f"[REPLY][支付机器人]\n机器人：支付信息生成失败，请到店后直接咨询前台完成支付。\n")
    
    return {"output_tokens": tokens, "active_agent": "none"}


async def statistics_node(state: AgentState) -> dict:
    """
    统计处理节点 - 处理工作人员上报的服务完成通知
    
    业务场景：工作人员通知"某技师已完成本次服务"，
    系统更新技师排班状态、释放时间段、统计当日服务数。
    """
    import re
    from datetime import datetime
    from config.time_config import time_config
    from config.constants import busy_periods_dict
    
    user_input = state["user_input"]
    tokens = []
    tokens.append("[THOUGHT][归类机器人] 归类机器人：我发现这是一个统计任务，我将更新技师服务状态。")
    
    try:
        from services.appointment_service import AppointmentService
        appointment_service = AppointmentService()
        
        # 从消息中提取技师名
        # 常见格式："张伟技师已完成"、"8号技师已完成"、"工作人员通知：X号技师已完成"
        technician_name = None
        technician_id = None
        
        # 尝试匹配 "X号技师" 格式
        id_match = re.search(r'(\d+)号(?:技师)?', user_input)
        if id_match:
            technician_id = int(id_match.group(1))
        
        # 尝试匹配技师名（"张伟技师已完成" / "张伟已完成"）
        if not technician_id:
            name_match = re.search(r'(?:通知[：:]?\s*)?(\w{2,4}?)(?:技师)?(?:已完成|完成了)', user_input)
            if name_match:
                potential_name = name_match.group(1)
                if not potential_name.isdigit():
                    # 按名字查找
                    tech_info = appointment_service.get_technician_by_name(potential_name)
                    if tech_info:
                        technician_name = tech_info['name']
                        technician_id = tech_info['id']
        
        if technician_id:
            # 获取技师信息
            tech_info = appointment_service.get_technician_by_id(technician_id)
            if tech_info:
                technician_name = tech_info['name']
            
            # 获取该技师今天的排班记录
            today = time_config.now()
            schedules = appointment_service.get_technician_schedules(technician_id, today)
            
            # 找到当前正在进行的服务（status=busy 且时间最近的）
            current_busy = None
            for schedule in schedules:
                if schedule.get('status') == 'busy':
                    current_busy = schedule
                    break  # 取最早的 busy 记录
            
            # 更新排班状态
            updated = False
            if current_busy:
                from db.db_router import DatabaseRouter
                db_router = DatabaseRouter()
                updated = db_router.technicians.update_schedule_status(
                    current_busy['id'], 'completed'
                )
                
                # 释放内存中的忙碌时间段
                if technician_id_str := str(technician_id):
                    if technician_id_str in busy_periods_dict:
                        busy_periods_dict[technician_id_str] = [
                            p for p in busy_periods_dict[technician_id_str]
                            if p.get('start') != time_config.format_datetime(
                                datetime.fromisoformat(str(current_busy.get('start_time', ''))), "%H:%M"
                            )
                        ]
            
            # 统计今日完成的服务次数
            completed_count = sum(
                1 for s in schedules if s.get('status') == 'completed'
            ) + (1 if updated else 0)
            
            # 构建回复
            reply_parts = [f"\n机器人：服务完成确认\n"]
            reply_parts.append(f"{'─' * 30}\n")
            reply_parts.append(f"✅ 已记录：{technician_name or f'{technician_id}号技师'} 完成本次服务\n")
            if updated:
                reply_parts.append(f"📋 排班状态已更新为'已完成'\n")
                reply_parts.append(f"🕐 技师时间已释放，可接新预约\n")
            reply_parts.append(f"📊 今日已完成服务：{completed_count} 次\n")
            reply_parts.append(f"{'─' * 30}\n")
            
            tokens.append("[REPLY][统计机器人]" + "".join(reply_parts))
        else:
            tokens.append("[REPLY][统计机器人]\n机器人：无法识别技师信息，请确认消息格式（如'张伟技师已完成本次服务'）。\n")
    
    except Exception as e:
        tokens.append(f"[REPLY][统计机器人]\n机器人：处理服务完成通知时出错：{str(e)}。请稍后重试。\n")
    
    return {"output_tokens": tokens, "active_agent": "none"}


# ============================================================
# 4. 路由函数 - 决定从分类节点走向哪个处理节点
# ============================================================

def route_after_classify(state: AgentState) -> Literal["appointment_node", "consultation_node", "pay_node", "statistics_node", "unrelated_node"]:
    """根据分类结果路由到对应节点"""
    category = state.get("category", "unrelated")
    if category == "appointment":
        return "appointment_node"
    elif category == "consultation":
        return "consultation_node"
    elif category == "pay":
        return "pay_node"
    elif category == "statistics":
        return "statistics_node"
    else:
        return "unrelated_node"


# ============================================================
# 5. 构建 Graph
# ============================================================

def build_agent_graph() -> StateGraph:
    """
    构建 Agent 编排图
    
    图结构：
        START → classify_node → (条件路由)
                                  ├─ appointment_node → END
                                  ├─ consultation_node → END
                                  ├─ pay_node → END
                                  ├─ statistics_node → END
                                  └─ unrelated_node → END
    """
    graph = StateGraph(AgentState)
    
    # 添加节点
    graph.add_node("classify_node", classify_node)
    graph.add_node("appointment_node", appointment_node)
    graph.add_node("consultation_node", consultation_node)
    graph.add_node("pay_node", pay_node)
    graph.add_node("statistics_node", statistics_node)
    graph.add_node("unrelated_node", unrelated_node)
    
    # 添加边
    graph.add_edge(START, "classify_node")
    graph.add_conditional_edges(
        "classify_node",
        route_after_classify,
        {
            "appointment_node": "appointment_node",
            "consultation_node": "consultation_node",
            "pay_node": "pay_node",
            "statistics_node": "statistics_node",
            "unrelated_node": "unrelated_node",
        }
    )
    graph.add_edge("appointment_node", END)
    graph.add_edge("consultation_node", END)
    graph.add_edge("pay_node", END)
    graph.add_edge("statistics_node", END)
    graph.add_edge("unrelated_node", END)
    
    return graph


# ============================================================
# 6. 编译并暴露可调用的 Graph 实例（带 Checkpointer）
# ============================================================

_checkpointer: AsyncSqliteSaver | None = None
_compiled_graph = None


async def _get_checkpointer() -> AsyncSqliteSaver:
    """
    获取 SQLite Checkpointer 实例（懒加载）
    
    Checkpointer 的作用：
    - 将每次 graph 执行后的状态（包括 active_agent）持久化到 SQLite
    - 下次同一个 thread_id 的请求进来时，自动恢复上次的状态
    - 服务重启后对话状态不丢失
    """
    global _checkpointer
    if _checkpointer is None:
        checkpoint_parent = Path(CHECKPOINT_DB_PATH).expanduser().parent
        checkpoint_parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(CHECKPOINT_DB_PATH)
        _checkpointer = AsyncSqliteSaver(conn)
        await _checkpointer.setup()
        logger.info(f"✅ LangGraph Checkpointer 已初始化，持久化路径: {CHECKPOINT_DB_PATH}")
    return _checkpointer


async def get_compiled_graph():
    """
    获取编译后的 graph（带 checkpointer，懒加载）
    
    compile(checkpointer=...) 使得：
    - 每次 ainvoke 后自动保存状态到 SQLite
    - 下次相同 thread_id 的调用自动恢复状态
    """
    global _compiled_graph
    if _compiled_graph is None:
        graph = build_agent_graph()
        checkpointer = await _get_checkpointer()
        _compiled_graph = graph.compile(checkpointer=checkpointer)
        logger.info("✅ LangGraph Agent Graph 已编译（带持久化 checkpointer）")
    return _compiled_graph


async def process_user_input_graph(user_input: str, session_id: str = None):
    """
    通过 LangGraph 处理用户输入（流式输出）
    
    这是对外的主接口，替代原来的 ProcessUserInput_stream。
    
    改进点：
    - 使用 checkpointer 自动持久化对话状态，不再需要手动维护 active_agent 全局变量
    - 使用 thread_id 实现多用户隔离，不同 session 的对话互不影响
    - 服务重启后，用户的多轮对话可以无缝继续
    
    Args:
        user_input: 用户输入
        session_id: 会话 ID，用作 thread_id 实现多用户隔离
        
    Yields:
        str: 流式输出的 token
    """
    if session_id is None:
        session_id = str(uuid.uuid4())
    
    compiled = await get_compiled_graph()

    from services.redis_service import get_redis_service
    redis_service = get_redis_service()
    redis_state = await redis_service.get_session(session_id)
    
    # 构造初始状态
    # 注意：active_agent 不再需要手动从全局变量读取
    # checkpointer 会自动从上次的 checkpoint 中恢复 active_agent 的值
    initial_state: AgentState = {
        "user_input": user_input,
        "category": "",
        "output_tokens": [],
        "session_id": session_id,
        "active_agent": redis_state.get("active_agent", "none"),
    }
    
    # 使用 thread_id 配置实现多用户隔离
    # 相同 thread_id 的调用共享对话状态（包括 active_agent）
    # 不同 thread_id 的调用完全独立
    config = {
        "configurable": {
            "thread_id": session_id  # 用 session_id 作为 thread_id
        }
    }
    
    # 获取调用前的历史 token 数量（用于计算增量）
    # checkpointer 恢复的 state 中可能已经有历史 output_tokens
    try:
        prev_state = await compiled.aget_state(config)
        prev_token_count = len(prev_state.values.get("output_tokens", [])) if prev_state.values else 0
        if "active_agent" not in redis_state and prev_state.values:
            initial_state["active_agent"] = prev_state.values.get(
                "active_agent", "none"
            )
    except Exception:
        prev_token_count = 0
    
    # 执行 graph（checkpointer 自动恢复上次状态 + 保存本次结果）
    result = await compiled.ainvoke(initial_state, config=config)

    latest_redis_state = await redis_service.get_session(session_id)
    latest_redis_state["active_agent"] = result.get("active_agent", "none")
    await redis_service.save_session(session_id, latest_redis_state)
    
    # 只 yield 本次新增的 tokens（跳过历史累积部分）
    all_tokens = result.get("output_tokens", [])
    new_tokens = all_tokens[prev_token_count:]
    
    for token in new_tokens:
        yield token


# ============================================================
# 7. 管理接口
# ============================================================

async def reset_graph_state(session_id: str = None):
    """
    重置 graph 状态
    
    Args:
        session_id: 要重置的会话 ID，如果不传则重置所有
    """
    if session_id:
        # 重置特定用户的 Agent 实例
        if session_id in _appointment_agents:
            _appointment_agents[session_id].reset()
            del _appointment_agents[session_id]
        if session_id in _consultant_agents:
            del _consultant_agents[session_id]
        logger.info(f"已重置会话 {session_id} 的状态")
    else:
        # 重置所有
        for agent in _appointment_agents.values():
            agent.reset()
        _appointment_agents.clear()
        _consultant_agents.clear()
        logger.info("已重置所有会话状态")


def get_active_sessions() -> list[str]:
    """获取当前活跃的会话列表"""
    sessions = set()
    sessions.update(_appointment_agents.keys())
    sessions.update(_consultant_agents.keys())
    return list(sessions)
