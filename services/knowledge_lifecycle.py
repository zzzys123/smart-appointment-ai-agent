"""Knowledge-document manifest, drift detection, synchronization and SQLite backup."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from .document_ingestion_service import (
    ChineseTextChunker,
    DocumentIngestionService,
    parse_front_matter,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DOCUMENT_DIR = PROJECT_ROOT / "knowledge_documents"
DEFAULT_MANIFEST_PATH = DEFAULT_DOCUMENT_DIR / "MANIFEST.json"
DEFAULT_BACKUP_DIR = PROJECT_ROOT / "data" / "backups" / "knowledge"
SUPPORTED_PATH_SUFFIXES = {".md", ".markdown", ".txt"}


def _document_paths(document_dir: Path) -> List[Path]:
    return sorted(
        path
        for path in document_dir.iterdir()
        if path.is_file()
        and path.suffix.lower() in SUPPORTED_PATH_SUFFIXES
        and path.name.lower() != "readme.md"
    )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def build_manifest(
    document_dir: Path = DEFAULT_DOCUMENT_DIR,
    *,
    chunk_size: int = 500,
    chunk_overlap: int = 100,
) -> Dict[str, Any]:
    """Build a deterministic manifest without loading an embedding model."""
    document_dir = Path(document_dir)
    if not document_dir.is_dir():
        raise FileNotFoundError(f"知识文档目录不存在：{document_dir}")
    chunker = ChineseTextChunker(chunk_size, chunk_overlap)
    files = []
    for path in _document_paths(document_dir):
        raw = path.read_bytes()
        text = raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        body, metadata = parse_front_matter(text)
        chunks = chunker.split_text(body, source_name=path.name)
        source_id = metadata.source_id or _sha256_bytes(
            path.name.casefold().encode("utf-8") + b"\0" + body.encode("utf-8")
        )
        files.append({
            "path": path.name,
            "source_id": source_id,
            "version": metadata.version,
            "category": metadata.category or "general",
            # Git may materialize LF or CRLF depending on the workstation.
            "file_sha256": _sha256_bytes(text.encode("utf-8")),
            "chunk_count": len(chunks),
            "chunk_sha256": [
                DocumentIngestionService._content_hash(chunk.content)
                for chunk in chunks
            ],
        })
    return {
        "schema_version": 1,
        "chunking": {"chunk_size": chunk_size, "chunk_overlap": chunk_overlap},
        "document_count": len(files),
        "chunk_count": sum(item["chunk_count"] for item in files),
        "files": files,
    }


def write_manifest(
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    *,
    document_dir: Path = DEFAULT_DOCUMENT_DIR,
    chunk_size: int = 500,
    chunk_overlap: int = 100,
) -> Dict[str, Any]:
    manifest = build_manifest(
        document_dir,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
    manifest_path = Path(manifest_path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def verify_manifest(
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    *,
    document_dir: Path = DEFAULT_DOCUMENT_DIR,
) -> Dict[str, Any]:
    manifest_path = Path(manifest_path)
    if not manifest_path.exists():
        return {"status": "missing", "matches": False, "path": str(manifest_path)}
    expected = json.loads(manifest_path.read_text(encoding="utf-8"))
    chunking = expected.get("chunking") or {}
    actual = build_manifest(
        document_dir,
        chunk_size=int(chunking.get("chunk_size", 500)),
        chunk_overlap=int(chunking.get("chunk_overlap", 100)),
    )
    return {
        "status": "ok" if actual == expected else "drifted",
        "matches": actual == expected,
        "path": str(manifest_path),
        "expected_document_count": expected.get("document_count", 0),
        "actual_document_count": actual["document_count"],
        "expected_chunk_count": expected.get("chunk_count", 0),
        "actual_chunk_count": actual["chunk_count"],
    }


def compare_database(manifest: Mapping[str, Any], documents: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    expected = {
        str(item["source_id"]): set(item.get("chunk_sha256") or [])
        for item in manifest.get("files", [])
    }
    actual: Dict[str, set[str]] = {}
    active_count = 0
    managed_active_count = 0
    for document in documents:
        if not document.get("is_active", True):
            continue
        active_count += 1
        source_id = document.get("source_id")
        content_hash = document.get("content_hash")
        if source_id and content_hash:
            actual.setdefault(str(source_id), set()).add(str(content_hash))
            if str(source_id) in expected:
                managed_active_count += 1
    missing_sources = sorted(set(expected) - set(actual))
    managed_extra_sources = sorted(set(actual) - set(expected))
    mismatched_sources = sorted(
        source_id
        for source_id in set(expected) & set(actual)
        if expected[source_id] != actual[source_id]
    )
    matched = not missing_sources and not mismatched_sources
    return {
        "status": "ok" if matched else "drifted",
        "matches": matched,
        "active_chunk_count": active_count,
        "managed_active_chunk_count": managed_active_count,
        "unmanaged_active_chunk_count": active_count - managed_active_count,
        "expected_chunk_count": int(manifest.get("chunk_count", 0)),
        "missing_sources": missing_sources,
        "mismatched_sources": mismatched_sources,
        # Extra sources may be manually managed, so they are reported but do not fail sync.
        "extra_sources": managed_extra_sources,
    }


def sqlite_path_from_url(db_url: str) -> Path:
    prefix = "sqlite:///"
    if not db_url.startswith(prefix):
        raise ValueError("备份与回滚目前仅支持 sqlite:/// 数据库")
    value = db_url[len(prefix):]
    if not value or value == ":memory:":
        raise ValueError("内存 SQLite 数据库不能备份或回滚")
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _sqlite_backup(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(source)) as source_conn, sqlite3.connect(str(target)) as target_conn:
        source_conn.backup(target_conn)


def create_backup(
    db_url: str = "sqlite:///data/smart_appointment.db",
    backup_dir: Path = DEFAULT_BACKUP_DIR,
) -> Dict[str, Any]:
    source = sqlite_path_from_url(db_url).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"SQLite 数据库不存在：{source}")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = Path(backup_dir).resolve() / f"knowledge-{timestamp}.db"
    counter = 1
    while target.exists():
        target = Path(backup_dir).resolve() / f"knowledge-{timestamp}-{counter}.db"
        counter += 1
    _sqlite_backup(source, target)
    return {
        "path": str(target),
        "size_bytes": target.stat().st_size,
        "sha256": _sha256_bytes(target.read_bytes()),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def restore_backup(
    backup_path: Path,
    *,
    db_url: str = "sqlite:///data/smart_appointment.db",
    backup_dir: Path = DEFAULT_BACKUP_DIR,
) -> Dict[str, Any]:
    allowed_root = Path(backup_dir).resolve()
    source = Path(backup_path).resolve()
    if source.parent != allowed_root or not source.is_file() or source.suffix.lower() != ".db":
        raise ValueError("只能回滚 data/backups/knowledge 目录中的 .db 快照")
    target = sqlite_path_from_url(db_url).resolve()
    safety_backup = create_backup(db_url, backup_dir)
    _sqlite_backup(source, target)
    return {"restored_from": str(source), "safety_backup": safety_backup}


async def lifecycle_status(knowledge_service, document_dir: Path = DEFAULT_DOCUMENT_DIR) -> Dict[str, Any]:
    manifest = build_manifest(document_dir)
    documents = await asyncio.to_thread(knowledge_service.db.get_all_documents, True)
    return {
        "manifest": verify_manifest(Path(document_dir) / "MANIFEST.json", document_dir=document_dir),
        "database": compare_database(manifest, documents),
        "source": {
            "document_count": manifest["document_count"],
            "chunk_count": manifest["chunk_count"],
        },
    }


async def sync_directory(
    knowledge_service,
    document_dir: Path = DEFAULT_DOCUMENT_DIR,
    *,
    chunk_size: int = 500,
    chunk_overlap: int = 100,
) -> Dict[str, Any]:
    ingestion = DocumentIngestionService(knowledge_service)
    totals = {"documents": 0, "chunks": 0, "imported": 0, "skipped": 0, "deactivated": 0, "changed": 0}
    results = []
    for path in _document_paths(Path(document_dir)):
        result = await ingestion.import_document(
            filename=path.name,
            content=path.read_text(encoding="utf-8-sig"),
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            rebuild_index=False,
        )
        results.append(result)
        totals["documents"] += 1
        totals["chunks"] += result["chunk_count"]
        totals["imported"] += result["imported_count"]
        totals["skipped"] += result["skipped_count"]
        totals["deactivated"] += result["deactivated_count"]
        totals["changed"] += result["changed_count"]
    if totals["changed"]:
        await knowledge_service.refresh_index()
    return {"totals": totals, "documents": results, "index_rebuilt": bool(totals["changed"])}
