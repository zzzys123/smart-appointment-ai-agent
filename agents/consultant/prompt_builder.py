"""
提示词构建器

负责构建各种类型的提示词
"""

from typing import List, Dict, Any


class PromptBuilder:
    """提示词构建器"""
    
    def __init__(self):
        self.system_prompt = self._create_system_prompt()
        self.classification_prompt_template = self._create_classification_prompt_template()
    
    def _create_system_prompt(self) -> str:
        """创建系统提示词"""
        return (
            "你是一个推拿房的前台接待员，负责为客户解答关于推拿服务、预约、价格、营业时间、地址、交通等相关问题。"
            "我会为你提供经过相关度过滤的知识库信息，请严格依据这些信息回答，不要使用模型记忆补充门店事实。"
            "如果资料没有明确支持某个结论，就说明当前知识库信息不足，并建议用户联系门店确认。"
            "涉及业务规则、项目名称、金额、时间和适用条件时，尽量沿用证据原文，不要把宽泛表述擅自具体化或扩大。"
            "请用专业、礼貌、简洁的语言回复用户。"
            "如果用户的问题与推拿房服务完全无关（如天气、股票、新闻等），请礼貌地告知用户你只能回答推拿相关问题。"
            "回答正文中不要自行编造来源编号，系统会在回答后自动附加引用。"
        )
    
    def _create_classification_prompt_template(self) -> str:
        """创建分类提示词模板"""
        return (
            "你是一个分类器，判断用户输入是否是关于推拿店的咨询类问题。\n"
            "咨询类问题包括：推拿服务、价格、营业时间、服务项目、技师情况、店铺地址、交通路线、联系方式、店铺环境等。\n"
            "非咨询类问题包括：预约服务（我要预约、帮我安排等）、取消预约、天气、股票、新闻等完全无关的话题。\n"
            "如果是咨询类问题，回答'YES'。如果是预约类问题或完全无关问题，回答'NO'。\n"
            "只回答YES或NO。\n\n"
            "用户输入：{user_input}"
        )
    
    def build_consultation_prompt(self, user_input: str, knowledge_docs: List[Dict[str, Any]]) -> str:
        """构建咨询提示词"""
        context = self._build_knowledge_context(knowledge_docs)
        return f"{self.system_prompt}\n\n{context}\n用户问题：{user_input}\n\n请回答用户的问题。"
    
    def build_classification_prompt(self, user_input: str) -> str:
        """构建分类提示词"""
        return self.classification_prompt_template.format(user_input=user_input)
    
    def _build_knowledge_context(self, knowledge_docs: List[Dict[str, Any]]) -> str:
        """构建知识库上下文"""
        if not knowledge_docs:
            return "没有找到达到证据阈值的知识库信息。请不要推测或补充答案。"
        
        context = "\n以下是相关的知识库信息：\n"
        for i, doc in enumerate(knowledge_docs, 1):
            context += f"{i}. {self.format_knowledge_document(doc)}\n"
        context += "\n请只基于以上信息回答；资料未明确支持的内容不要推测。\n"
        
        return context

    @staticmethod
    def format_knowledge_document(document: Dict[str, Any]) -> str:
        """Format one evidence item identically for generation and evaluation."""
        metadata = []
        if document.get("source_id"):
            metadata.append(f"来源编号: {document['source_id']}")
        if document.get("source_name"):
            metadata.append(f"来源: {document['source_name']}")
        if document.get("title"):
            metadata.append(f"章节: {document['title']}")
        chunk_index = document.get("chunk_index")
        chunk_count = document.get("chunk_count")
        if chunk_index is not None and chunk_count:
            metadata.append(f"分块: {int(chunk_index) + 1}/{chunk_count}")

        metadata_prefix = f"[{' | '.join(metadata)}]\n" if metadata else ""
        return f"{metadata_prefix}{document.get('content', '')}"
