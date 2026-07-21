"""
RAG 质量评估器（自研 LLM-as-judge）

说明：ragas 库当前版本与项目的 LangChain 1.x 不兼容（硬依赖已废弃的
langchain_community vertexai 路径），为避免破坏 langgraph 等核心环境，
这里用项目现有的 Qwen 自研实现 Ragas 同款的两个核心指标：

- Faithfulness（忠实度）：答案中的陈述是否都能被检索到的上下文支持
  → 拆解答案为原子陈述(claims)，逐条判断是否被 context 支持，
     faithfulness = 支持的 claim 数 / 总 claim 数。衡量"有没有编造/幻觉"。
- Answer Relevancy（答案相关性）：答案是否切题回应了用户问题。

复用 config.model_provider 的工厂，全程只用 Qwen，不引入额外依赖与 API key。
"""

import logging
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from config.model_provider import create_chat_model

logger = logging.getLogger(__name__)


# ---- 结构化输出 Schema ----
class _Claim(BaseModel):
    statement: str = Field(description="从答案中拆解出的一条原子陈述")
    supported: bool = Field(description="该陈述是否能被给定上下文支持（True=被支持，False=上下文无依据/矛盾）")


class _FaithfulnessResult(BaseModel):
    claims: List[_Claim] = Field(description="答案拆解出的所有原子陈述及其是否被上下文支持")


class _RelevancyResult(BaseModel):
    score: float = Field(description="答案对问题的相关性分数，0-1，1=完全切题，0=答非所问")
    reason: str = Field(description="给出该分数的简要理由")


_FAITH_PROMPT = (
    "你是严格的 RAG 质量审查员。给定【上下文】和一段【答案】，请：\n"
    "1. 把答案拆解为若干条原子陈述（claim），每条只表达一个事实点；\n"
    "2. 对每条陈述判断它是否能被【上下文】支持（supported=True/False）；\n"
    "3. 只依据上下文判断，不要用你自己的世界知识；上下文没提到的即视为不支持。"
)

_REL_PROMPT = (
    "你是严格的 RAG 质量审查员。请评估【答案】对【问题】的相关性（是否切题回应了问题）：\n"
    "- 1.0 表示完全切题、直接回答了问题；\n"
    "- 0.5 表示部分相关或答非所重点；\n"
    "- 0.0 表示答非所问。\n"
    "只评估相关性，不评估答案是否正确。"
)


class RagJudge:
    """基于 LLM 的 RAG 质量评估器（可插拔 Evaluator 的一种实现）。"""

    def __init__(self, llm=None):
        self.llm = llm or create_chat_model(temperature=0)
        self._faith_llm = self.llm.with_structured_output(_FaithfulnessResult)
        self._rel_llm = self.llm.with_structured_output(_RelevancyResult)

    async def faithfulness(self, answer: str, contexts: List[str]) -> Dict:
        """忠实度：支持的 claim 占比。"""
        if not answer.strip():
            return {"score": 0.0, "claims": []}
        ctx = "\n".join(f"- {c}" for c in contexts) or "(无上下文)"
        user = f"【上下文】\n{ctx}\n\n【答案】\n{answer}"
        try:
            result: _FaithfulnessResult = await self._faith_llm.ainvoke(
                [("system", _FAITH_PROMPT), ("human", user)]
            )
            claims = result.claims
            n = len(claims)
            supported = sum(1 for c in claims if c.supported)
            score = supported / n if n else 0.0
            return {
                "score": score,
                "supported": supported,
                "total": n,
                "claims": [(c.statement, c.supported) for c in claims],
            }
        except Exception as e:
            logger.error(f"faithfulness 评估失败: {e}")
            return {"score": 0.0, "claims": [], "error": str(e)}

    async def answer_relevancy(self, question: str, answer: str) -> Dict:
        """答案相关性：LLM 打分 0-1。"""
        if not answer.strip():
            return {"score": 0.0, "reason": "空答案"}
        user = f"【问题】\n{question}\n\n【答案】\n{answer}"
        try:
            result: _RelevancyResult = await self._rel_llm.ainvoke(
                [("system", _REL_PROMPT), ("human", user)]
            )
            score = max(0.0, min(1.0, float(result.score)))
            return {"score": score, "reason": result.reason}
        except Exception as e:
            logger.error(f"answer_relevancy 评估失败: {e}")
            return {"score": 0.0, "reason": f"评估异常: {e}"}


async def generate_answer(llm, query: str, contexts: List[str]) -> str:
    """基于检索上下文生成答案（模拟 RAG 生成阶段），供评估使用。"""
    ctx = "\n".join(f"- {c}" for c in contexts) or "(无检索结果)"
    prompt = (
        "你是按摩推拿门店的智能客服。请仅依据以下知识回答用户问题，"
        "不要编造知识以外的信息。\n\n"
        f"【知识】\n{ctx}\n\n【用户问题】\n{query}\n\n请简洁作答："
    )
    resp = await llm.ainvoke(prompt)
    return getattr(resp, "content", str(resp))
