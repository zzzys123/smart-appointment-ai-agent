"""
聊天处理器

提供两种实现：
1. LangGraph 版本（默认）- 使用 StateGraph 编排 Agent 协作
2. 传统版本（备用）- 使用手写状态机

通过环境变量 USE_LANGGRAPH=true/false 切换（默认 true）
"""

import os
import uuid

# 全局 session_id（单用户场景）
global_session_id = str(uuid.uuid4())

# 判断是否使用 LangGraph
USE_LANGGRAPH = os.getenv("USE_LANGGRAPH", "true").lower() != "false"


async def ProcessUserInput_stream(user_input, state=None, context=None):
    """
    处理用户输入的统一入口

    Args:
        user_input: 用户输入
        state: 当前对话状态（兼容性参数）
        context: 可选的多轮对话上下文（兼容性参数）

    Yields:
        str: 流式输出的 token
    """
    if USE_LANGGRAPH:
        # LangGraph 版本
        from agents.graph_agent import process_user_input_graph
        async for token in process_user_input_graph(user_input, session_id=global_session_id):
            yield token
    else:
        # 传统版本（保留向后兼容）
        from agents.task_classification_agent import TaskClassificationAgent
        from agents.appointment_agent import AppointmentAgent
        from agents.consultant_agent import ConsultantAgent

        # 懒初始化传统 agent
        global _legacy_task_agent
        if "_legacy_task_agent" not in globals() or _legacy_task_agent is None:
            _legacy_task_agent = TaskClassificationAgent(
                AppointmentAgent(session_id=global_session_id),
                ConsultantAgent(session_id=global_session_id)
            )

        async for token in _legacy_task_agent.classify_task_stream(user_input):
            yield token


_legacy_task_agent = None
