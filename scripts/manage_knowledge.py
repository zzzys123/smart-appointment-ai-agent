"""Manage the versioned knowledge baseline without Redis or Docker."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.knowledge_lifecycle import (  # noqa: E402
    DEFAULT_BACKUP_DIR,
    DEFAULT_DOCUMENT_DIR,
    DEFAULT_MANIFEST_PATH,
    build_manifest,
    create_backup,
    lifecycle_status,
    restore_backup,
    sync_directory,
    write_manifest,
)


def _print(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


async def _async_main(args) -> None:
    if args.command == "manifest":
        result = write_manifest(args.manifest_path, document_dir=args.document_dir) if args.write else build_manifest(args.document_dir)
        _print(result)
        return
    if args.command == "backup":
        _print(create_backup(args.db_url, args.backup_dir))
        return
    if args.command == "restore":
        if not args.confirm_restore:
            raise SystemExit("回滚会覆盖当前 SQLite；请显式传入 --confirm-restore")
        _print(restore_backup(args.backup_path, db_url=args.db_url, backup_dir=args.backup_dir))
        return

    from services.knowledge_service import KnowledgeService

    service = KnowledgeService(db_path=args.db_url)
    if args.command == "status":
        _print(await lifecycle_status(service, args.document_dir))
    elif args.command == "rebuild-index":
        await service.initialize()
        await service.refresh_index()
        _print({"status": "ok", "documents": service.retriever.num_docs})
    elif args.command == "sync":
        preview = await lifecycle_status(service, args.document_dir)
        if not args.apply:
            _print({"mode": "dry_run", **preview})
            return
        await service.initialize()
        backup = create_backup(args.db_url, args.backup_dir)
        result = await sync_directory(service, args.document_dir)
        _print({"mode": "applied", "backup": backup, "sync": result})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url", default="sqlite:///data/smart_appointment.db")
    parser.add_argument("--document-dir", type=Path, default=DEFAULT_DOCUMENT_DIR)
    parser.add_argument("--manifest-path", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUP_DIR)
    sub = parser.add_subparsers(dest="command", required=True)
    manifest = sub.add_parser("manifest")
    manifest.add_argument("--write", action="store_true")
    sub.add_parser("status")
    sync = sub.add_parser("sync")
    sync.add_argument("--apply", action="store_true", help="实际同步；默认只预览")
    sub.add_parser("backup")
    restore = sub.add_parser("restore")
    restore.add_argument("backup_path", type=Path)
    restore.add_argument("--confirm-restore", action="store_true")
    sub.add_parser("rebuild-index")
    asyncio.run(_async_main(parser.parse_args()))


if __name__ == "__main__":
    main()
