"""Knowledge baseline lifecycle tests without embedding API calls."""

import json
import sqlite3

import pytest

from services.knowledge_lifecycle import (
    build_manifest,
    compare_database,
    create_backup,
    restore_backup,
    verify_manifest,
    write_manifest,
)


def test_default_knowledge_baseline_contains_ten_seed_entries(monkeypatch):
    import services.knowledge_service as knowledge_module

    class StubRouter:
        knowledge = object()

    monkeypatch.setattr(knowledge_module, "DatabaseRouter", lambda _db_path: StubRouter())
    monkeypatch.setattr(knowledge_module, "create_retriever", lambda: object())

    service = knowledge_module.KnowledgeService("sqlite:///:memory:")

    assert len(service.default_knowledge) == 10
    assert all(item["content"] and item["category"] and item["keywords"] for item in service.default_knowledge)


def _write_document(path, source_id="KB-TEST-001", body="# 规则\n\n至少提前两小时取消。"):
    path.write_text(
        f"---\nsource_id: {source_id}\ncategory: 测试\nversion: 1\n---\n{body}\n",
        encoding="utf-8",
    )


def test_manifest_is_deterministic_and_detects_source_drift(tmp_path):
    document_dir = tmp_path / "knowledge"
    document_dir.mkdir()
    _write_document(document_dir / "policy.md")
    manifest_path = document_dir / "MANIFEST.json"

    first = write_manifest(manifest_path, document_dir=document_dir)
    second = build_manifest(document_dir)

    assert first == second
    assert first["document_count"] == 1
    assert first["chunk_count"] == 1
    assert verify_manifest(manifest_path, document_dir=document_dir)["matches"] is True

    _write_document(document_dir / "policy.md", body="# 规则\n\n至少提前四小时取消。")
    result = verify_manifest(manifest_path, document_dir=document_dir)
    assert result["status"] == "drifted"
    assert result["matches"] is False


def test_manifest_hash_is_stable_across_lf_and_crlf(tmp_path):
    document_dir = tmp_path / "knowledge"
    document_dir.mkdir()
    path = document_dir / "policy.md"
    content = "---\nsource_id: KB-LINE-ENDINGS\n---\n# 规则\n\n正文\n"
    path.write_bytes(content.replace("\n", "\r\n").encode("utf-8"))
    crlf = build_manifest(document_dir)
    path.write_bytes(content.encode("utf-8"))
    lf = build_manifest(document_dir)

    assert crlf == lf


def test_database_comparison_keeps_unmanaged_seed_entries_visible():
    manifest = {
        "chunk_count": 1,
        "files": [{"source_id": "KB-1", "chunk_sha256": ["hash-1"]}],
    }
    documents = [
        {"source_id": "KB-1", "content_hash": "hash-1", "is_active": True},
        {"source_id": None, "content_hash": None, "is_active": True},
    ]

    status = compare_database(manifest, documents)

    assert status["matches"] is True
    assert status["active_chunk_count"] == 2
    assert status["managed_active_chunk_count"] == 1
    assert status["unmanaged_active_chunk_count"] == 1


def test_sqlite_backup_and_explicit_restore_create_safety_snapshot(tmp_path):
    database = tmp_path / "live.db"
    backup_dir = tmp_path / "backups"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE value_store (value TEXT)")
        connection.execute("INSERT INTO value_store VALUES ('before')")
    db_url = f"sqlite:///{database.as_posix()}"
    backup = create_backup(db_url, backup_dir)

    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE value_store SET value='after'")
    restored = restore_backup(
        backup["path"], db_url=db_url, backup_dir=backup_dir
    )

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT value FROM value_store").fetchone()[0] == "before"
    assert restored["safety_backup"]["path"] != backup["path"]


def test_restore_rejects_snapshot_outside_managed_backup_directory(tmp_path):
    database = tmp_path / "live.db"
    outside = tmp_path / "outside.db"
    sqlite3.connect(database).close()
    sqlite3.connect(outside).close()

    with pytest.raises(ValueError, match="只能回滚"):
        restore_backup(
            outside,
            db_url=f"sqlite:///{database.as_posix()}",
            backup_dir=tmp_path / "backups",
        )
