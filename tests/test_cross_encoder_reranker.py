"""Cross-Encoder 重排器的离线单元测试。"""

import asyncio

from services.reranker import CrossEncoderReranker, create_reranker


class _FakeCrossEncoder:
    def __init__(self, scores):
        self.scores = scores
        self.calls = []

    def predict(self, pairs, batch_size, show_progress_bar):
        self.calls.append(
            {
                "pairs": pairs,
                "batch_size": batch_size,
                "show_progress_bar": show_progress_bar,
            }
        )
        return self.scores


def test_cross_encoder_reranks_candidates_and_preserves_metadata():
    model = _FakeCrossEncoder([0.1, 0.9, 0.4])
    reranker = CrossEncoderReranker(
        model_name="local-test-model", model=model, batch_size=8
    )
    documents = [
        {"id": 1, "title": "价格", "content": "价格说明", "score": 0.8},
        {"id": 2, "title": "禁忌", "content": "急性损伤不要按摩", "score": 0.7},
        {"id": 3, "title": "体验", "content": "肩颈项目体验", "score": 0.6},
    ]

    results = asyncio.run(reranker.rerank("肩膀刚扭伤能按摩吗", documents, 2))

    assert [item["id"] for item in results] == [2, 3]
    assert [item["rank"] for item in results] == [1, 2]
    assert results[0]["rerank"] == {
        "provider": "cross-encoder",
        "model": "local-test-model",
        "score": results[0]["rerank_score"],
        "original_rank": 2,
    }
    assert results[0]["score"] == 0.7
    assert model.calls[0]["batch_size"] == 8
    assert model.calls[0]["show_progress_bar"] is False
    assert model.calls[0]["pairs"][1] == [
        "肩膀刚扭伤能按摩吗",
        "禁忌\n急性损伤不要按摩",
    ]
    assert "rerank_score" not in documents[1]


def test_cross_encoder_keeps_original_order_for_equal_scores():
    reranker = CrossEncoderReranker(model=_FakeCrossEncoder([[0.5], [0.5]]))
    documents = [{"id": 7, "content": "甲"}, {"id": 8, "content": "乙"}]

    results = asyncio.run(reranker.rerank("问题", documents, 2))

    assert [item["id"] for item in results] == [7, 8]


def test_cross_encoder_falls_back_when_inference_fails():
    class BrokenModel:
        def predict(self, *args, **kwargs):
            raise RuntimeError("model unavailable")

    reranker = CrossEncoderReranker(model=BrokenModel())
    documents = [{"id": 1, "content": "甲"}, {"id": 2, "content": "乙"}]

    results = asyncio.run(reranker.rerank("问题", documents, 1))

    assert results[0]["id"] == 1
    assert results[0]["rank"] == 1
    assert results[0]["rerank_fallback"] is True
    assert results[0]["rerank_error"] == "model unavailable"


def test_cross_encoder_factory_accepts_aliases():
    for provider in ("cross-encoder", "cross_encoder", "crossencoder"):
        assert isinstance(create_reranker(provider), CrossEncoderReranker)


def test_cross_encoder_rejects_invalid_batch_size():
    try:
        CrossEncoderReranker(model=_FakeCrossEncoder([]), batch_size=-1)
    except ValueError as exc:
        assert "BATCH_SIZE" in str(exc)
    else:
        raise AssertionError("expected ValueError")
