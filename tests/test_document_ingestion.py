"""不依赖外部 LLM 的文档分块和导入测试。"""

import asyncio

import pytest

from services.document_ingestion_service import (
    ChineseTextChunker,
    DocumentIngestionService,
    parse_front_matter,
)


def test_chunker_preserves_heading_metadata_and_size_limit():
    text = "# 预约政策\n\n" + "提前预约可以锁定技师。" * 30 + "\n\n## 取消规则\n\n请提前四小时取消。"
    chunks = ChineseTextChunker(chunk_size=120, chunk_overlap=20).split_text(
        text, source_name="门店政策.md"
    )

    assert len(chunks) >= 3
    assert chunks[0].title == "预约政策"
    assert chunks[-1].title == "取消规则"
    assert all(len(chunk.content) <= 120 for chunk in chunks)
    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))
    assert all(chunk.chunk_count == len(chunks) for chunk in chunks)


@pytest.mark.parametrize(
    ("chunk_size", "chunk_overlap"),
    [(99, 0), (4001, 0), (500, -1), (500, 251)],
)
def test_chunker_rejects_invalid_parameters(chunk_size, chunk_overlap):
    with pytest.raises(ValueError):
        ChineseTextChunker(chunk_size, chunk_overlap)


class _FakeRepository:
    def __init__(self):
        self.documents = []

    def get_all_documents(self, include_inactive=False):
        if include_inactive:
            return list(self.documents)
        return [doc for doc in self.documents if doc.get("is_active", True)]

    def sync_source_chunks(self, source_id, payloads):
        existing = {
            doc["content_hash"]: doc
            for doc in self.documents
            if doc["source_id"] == source_id and doc.get("is_active", True)
        }
        desired = {payload["content_hash"] for payload in payloads}
        imported = skipped = deactivated = changed = 0
        ids = []
        for payload in payloads:
            document = existing.get(payload["content_hash"])
            if document:
                skipped += 1
                update = {key: value for key, value in payload.items() if value is not None}
                if any(document.get(key) != value for key, value in update.items()):
                    changed += 1
                document.update(update)
            else:
                document = dict(payload, source_id=source_id, is_active=True)
                document["id"] = len(self.documents) + 1
                self.documents.append(document)
                imported += 1
                changed += 1
            ids.append(document["id"])
        for document in self.documents:
            if (
                document["source_id"] == source_id
                and document.get("is_active", True)
                and document["content_hash"] not in desired
            ):
                document["is_active"] = False
                deactivated += 1
                changed += 1
        return {
            "document_ids": ids,
            "imported_count": imported,
            "skipped_count": skipped,
            "deactivated_count": deactivated,
            "changed_count": changed,
        }


class _FakeKnowledgeService:
    def __init__(self):
        self.db = _FakeRepository()
        self.rebuild_count = 0
        self.mutation_lock = asyncio.Lock()

    async def _build_vector_index(self):
        self.rebuild_count += 1


def test_import_document_saves_metadata_and_skips_duplicate_chunks():
    knowledge_service = _FakeKnowledgeService()
    ingestion = DocumentIngestionService(
        knowledge_service,
        embedding_fn=lambda text: [float(len(text)), 1.0],
    )
    payload = "# 会员规则\n\n充值一千元赠送一百五十元。".encode("utf-8")

    first = asyncio.run(
        ingestion.import_document("会员规则.md", payload, category="会员服务")
    )
    second = asyncio.run(
        ingestion.import_document("会员规则.md", payload, category="会员服务")
    )

    assert first["imported_count"] == 1
    assert first["changed_count"] == 1
    assert first["skipped_count"] == 0
    assert second["imported_count"] == 0
    assert second["changed_count"] == 0
    assert second["skipped_count"] == 1
    assert knowledge_service.rebuild_count == 1
    saved = knowledge_service.db.documents[0]
    assert saved["source_name"] == "会员规则.md"
    assert saved["title"] == "会员规则"
    assert saved["chunk_index"] == 0
    assert saved["chunk_count"] == 1
    assert len(saved["content_hash"]) == 64


def test_explicit_source_id_replaces_version_without_same_name_collision():
    knowledge_service = _FakeKnowledgeService()
    ingestion = DocumentIngestionService(
        knowledge_service, embedding_fn=lambda _: [1.0]
    )

    first = asyncio.run(ingestion.import_document(
        "普通政策.txt", "旧版规则：至少提前两小时取消。", source_id="TXT-POLICY-1"
    ))
    second = asyncio.run(ingestion.import_document(
        "普通政策.txt", "新版规则：至少提前四小时取消。", source_id="TXT-POLICY-1"
    ))

    assert first["source_id"] == second["source_id"]
    assert second["imported_count"] == 1
    assert second["deactivated_count"] == 1
    active = [
        doc for doc in knowledge_service.db.documents
        if doc.get("is_active", True)
    ]
    assert [doc["content"] for doc in active] == ["新版规则：至少提前四小时取消。"]

    unrelated = asyncio.run(ingestion.import_document(
        "普通政策.txt", "同名但属于另一份资料。"
    ))
    assert unrelated["source_id"] != first["source_id"]
    assert unrelated["deactivated_count"] == 0


def test_metadata_only_change_is_reported_for_batch_refresh():
    knowledge_service = _FakeKnowledgeService()
    ingestion = DocumentIngestionService(
        knowledge_service, embedding_fn=lambda _: [1.0]
    )

    asyncio.run(ingestion.import_document(
        "政策.md", "# 取消规则\n\n至少提前四小时。", category="预约政策"
    ))
    changed = asyncio.run(ingestion.import_document(
        "政策.md", "# 取消规则\n\n至少提前四小时。", category="售后政策"
    ))

    assert changed["imported_count"] == 0
    assert changed["deactivated_count"] == 0
    assert changed["changed_count"] == 1
    assert knowledge_service.rebuild_count == 2


def test_import_rejects_non_utf8_and_unsupported_extension():
    ingestion = DocumentIngestionService(
        _FakeKnowledgeService(), embedding_fn=lambda _: [1.0]
    )
    with pytest.raises(ValueError, match="UTF-8"):
        asyncio.run(ingestion.import_document("规则.txt", b"\xff\xfe"))
    with pytest.raises(ValueError, match="仅支持"):
        asyncio.run(ingestion.import_document("规则.pdf", b"content"))


def test_import_rejects_more_than_200_chunks_before_embedding():
    embedding_calls = []
    ingestion = DocumentIngestionService(
        _FakeKnowledgeService(),
        embedding_fn=lambda text: embedding_calls.append(text) or [1.0],
    )
    text = "\n".join(f"## 条款{i}\n第{i}条规则。" for i in range(201))

    with pytest.raises(ValueError, match="最多生成 200 个分块"):
        asyncio.run(ingestion.import_document("超长规则.md", text))
    assert embedding_calls == []


def test_chunker_stops_during_generation_at_configured_limit():
    chunker = ChineseTextChunker(
        chunk_size=100, chunk_overlap=50, max_chunks=3
    )
    with pytest.raises(ValueError, match="最多生成 3 个分块"):
        chunker.split_text("这是一个很长的句子。" * 1000, source_name="大文件.txt")


def test_front_matter_is_removed_and_metadata_is_adopted():
    knowledge_service = _FakeKnowledgeService()
    ingestion = DocumentIngestionService(
        knowledge_service, embedding_fn=lambda _: [1.0, 0.0]
    )
    payload = """---
source_id: KB-POLICY-009
title: 门店取消政策
category: 预约政策
keywords: [取消, 改期, 四小时]
version: 2.1
---

顾客如需取消，应至少提前四小时通知门店。
"""

    result = asyncio.run(
        ingestion.import_document("取消政策.md", payload, category="general")
    )

    assert result["source_id"] == "KB-POLICY-009"
    assert result["category"] == "预约政策"
    assert result["version"] == "2.1"
    saved = knowledge_service.db.documents[0]
    assert saved["source_id"] == "KB-POLICY-009"
    assert saved["title"] == "门店取消政策"
    assert saved["category"] == "预约政策"
    assert saved["version"] == "2.1"
    assert saved["keywords"] == ["门店取消政策", "取消", "改期", "四小时"]
    assert "source_id:" not in saved["content"]
    assert saved["content"] == "顾客如需取消，应至少提前四小时通知门店。"


def test_same_source_new_version_deactivates_removed_chunks_atomically():
    knowledge_service = _FakeKnowledgeService()
    ingestion = DocumentIngestionService(
        knowledge_service, embedding_fn=lambda _: [1.0, 0.0]
    )
    first = """---
source_id: KB-SYNC-001
version: 1
---
# 规则一
旧规则一。
# 规则二
旧规则二。
"""
    second = """---
source_id: KB-SYNC-001
version: 2
---
# 规则一
新规则一。
"""

    first_result = asyncio.run(ingestion.import_document("规则.md", first))
    second_result = asyncio.run(ingestion.import_document("规则.md", second))

    assert first_result["imported_count"] == 2
    assert second_result["imported_count"] == 1
    assert second_result["deactivated_count"] == 2
    active = knowledge_service.db.get_all_documents()
    assert len(active) == 1
    assert active[0]["content"] == "新规则一。"
    assert active[0]["version"] == "2"


def test_identical_content_from_different_sources_is_not_globally_deduplicated():
    knowledge_service = _FakeKnowledgeService()
    ingestion = DocumentIngestionService(
        knowledge_service, embedding_fn=lambda _: [1.0]
    )
    template = "---\nsource_id: {source_id}\n---\n相同的安全免责声明。"

    first = asyncio.run(ingestion.import_document(
        "来源一.md", template.format(source_id="SOURCE-ONE")
    ))
    second = asyncio.run(ingestion.import_document(
        "来源二.md", template.format(source_id="SOURCE-TWO")
    ))

    assert first["imported_count"] == 1
    assert second["imported_count"] == 1
    assert len(knowledge_service.db.get_all_documents()) == 2


def test_malformed_front_matter_has_clear_error():
    with pytest.raises(ValueError, match="缺少结束分隔符"):
        parse_front_matter("---\ntitle: 未闭合\n正文")
    with pytest.raises(ValueError, match="keywords 必须使用"):
        parse_front_matter("---\nkeywords: 取消, 改期\n---\n正文")
