"""
响应生成器

负责生成AI响应内容
"""

import json
from typing import Dict, Any, AsyncGenerator, List
from langchain_core.language_models.chat_models import BaseChatModel
from services.model_usage import usage_stage
from .prompt_builder import PromptBuilder


class ResponseGenerator:
    """响应生成器"""

    NO_ANSWER_MESSAGE = (
        "抱歉，当前知识库中没有足够的信息回答这个问题。"
        "为避免提供不准确的信息，建议您联系门店工作人员进一步确认。"
    )
    
    def __init__(self, llm: BaseChatModel):
        self.llm = llm
        self.prompt_builder = PromptBuilder()
    
    async def generate_response(self, user_input: str, knowledge_docs: list) -> str:
        """生成标准响应"""
        if not knowledge_docs:
            return self.NO_ANSWER_MESSAGE
        try:
            prompt = self.prompt_builder.build_consultation_prompt(user_input, knowledge_docs)
            with usage_stage("answer"):
                response = await self.llm.ainvoke([{"role": "user", "content": prompt}])
            return response.content
        except Exception as e:
            return f"抱歉，处理您的问题时出现了错误。请稍后再试。"
    
    async def generate_response_stream(self, user_input: str, knowledge_docs: list) -> AsyncGenerator[str, None]:
        """生成流式响应"""
        if not knowledge_docs:
            yield "[REPLY][咨询机器人]"
            for char in self.NO_ANSWER_MESSAGE:
                yield char
            return

        try:
            prompt = self.prompt_builder.build_consultation_prompt(user_input, knowledge_docs)
            with usage_stage("answer"):
                response = await self.llm.ainvoke([{"role": "user", "content": prompt}])
            content = response.content
            
            # 只在开头添加一次REPLY标签，然后逐字符输出
            yield "[REPLY][咨询机器人]"
            for char in content:
                yield char

            sources_marker = self.build_sources_marker(knowledge_docs)
            if sources_marker:
                yield sources_marker
                
        except Exception as e:
            error_msg = f"抱歉，处理您的问题时出现了错误：{str(e)}"
            yield "[REPLY][咨询机器人]"
            for char in error_msg:
                yield char

    @staticmethod
    def build_sources(knowledge_docs: list) -> List[Dict[str, Any]]:
        """Create a compact, deduplicated list of user-facing citations."""
        sources: List[Dict[str, Any]] = []
        seen = set()
        for document in knowledge_docs:
            raw_chunk_index = document.get("chunk_index")
            chunk_number = (
                int(raw_chunk_index) + 1 if raw_chunk_index is not None else None
            )
            source = {
                "source_id": document.get("source_id"),
                "source_name": document.get("source_name"),
                "title": document.get("title"),
                "category": document.get("category"),
                "chunk_number": chunk_number,
                "chunk_count": document.get("chunk_count"),
                "document_id": document.get("id"),
            }
            key = (
                source["source_id"],
                source["source_name"],
                source["title"],
                source["chunk_number"],
                source["document_id"],
            )
            if key in seen:
                continue
            seen.add(key)
            sources.append(source)
            if len(sources) == 3:
                break
        return sources

    @classmethod
    def build_sources_marker(cls, knowledge_docs: list) -> str:
        sources = cls.build_sources(knowledge_docs)
        if not sources:
            return ""
        payload = json.dumps(sources, ensure_ascii=False, separators=(",", ":"))
        return f"[SOURCES][咨询机器人]{payload}"
    
    def create_unrelated_message(self) -> str:
        """创建与咨询无关的回复消息"""
        return "[THOUGHT][咨询机器人] 咨询机器人：这个问题不是咨询类问题，我将转回给归类机器人处理。"
