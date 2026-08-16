"""分类过滤必须覆盖完整候选集。"""

import asyncio

from services.retriever.base import BaseRetriever


class _OrderedRetriever(BaseRetriever):
    @property
    def name(self):
        return "test"

    def build_index(self, documents):
        self._cache_docs(documents)

    def _recall(self, query, candidate_k, trace=None):
        return [(doc_id, 1.0 / doc_id) for doc_id in range(1, candidate_k + 1)]


def test_category_filter_recalls_target_outside_normal_candidate_window():
    retriever = _OrderedRetriever()
    retriever.rerank_enabled = False
    retriever.build_index([
        {"id": 1, "content": "其他一", "category": "其他"},
        {"id": 2, "content": "其他二", "category": "其他"},
        {"id": 3, "content": "其他三", "category": "其他"},
        {"id": 4, "content": "目标", "category": "安全须知"},
    ])

    results = asyncio.run(
        retriever.search("目标", top_k=1, category="安全须知")
    )

    assert [result["id"] for result in results] == [4]


def test_category_filter_caps_reranker_candidates():
    class FakeReranker:
        seen_count = 0

        async def rerank(self, query, candidates, top_k):
            self.seen_count = len(candidates)
            return candidates[:top_k]

    retriever = _OrderedRetriever()
    retriever.rerank_enabled = True
    retriever._reranker = FakeReranker()
    retriever.build_index([
        {"id": index, "content": str(index), "category": "目标"}
        for index in range(1, 31)
    ])

    asyncio.run(retriever.search("目标", top_k=1, category="目标"))

    assert retriever._reranker.seen_count == 10
