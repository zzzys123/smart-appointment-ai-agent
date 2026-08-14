"""把 knowledge_documents 下的演示 Markdown 文档批量分块并导入知识库。"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.document_ingestion_service import (  # noqa: E402
    ChineseTextChunker,
    DocumentIngestionService,
    parse_front_matter,
)


DEFAULT_DOCUMENT_DIR = PROJECT_ROOT / "knowledge_documents"


def _document_paths(document_dir: Path):
    paths = [
        path
        for path in document_dir.glob("*.md")
        if path.name.lower() != "readme.md"
    ]
    return sorted(paths)


async def _run(args) -> None:
    if not args.document_dir.is_dir():
        raise FileNotFoundError(f"演示文档目录不存在：{args.document_dir}")
    paths = _document_paths(args.document_dir)
    if not paths:
        raise ValueError(f"目录中没有可导入的 Markdown 文档：{args.document_dir}")

    if args.dry_run:
        chunker = ChineseTextChunker(args.chunk_size, args.chunk_overlap)
        total = 0
        for path in paths:
            text = path.read_text(encoding="utf-8-sig")
            body, _ = parse_front_matter(text)
            count = len(chunker.split_text(body, source_name=path.name))
            total += count
            print(f"[预览] {path.name}: {count} 个分块")
        print(f"预览完成：{len(paths)} 篇文档，共 {total} 个分块；未写入数据库。")
        return

    # 实际入库时才加载模型与向量检索依赖；--dry-run 保持纯标准库可运行。
    from services.knowledge_service import KnowledgeService

    knowledge_service = KnowledgeService(db_path=args.db_url)
    await knowledge_service.initialize()
    ingestion_service = DocumentIngestionService(knowledge_service)

    imported = skipped = deactivated = changed = chunks = 0
    for path in paths:
        content = path.read_text(encoding="utf-8-sig")
        result = await ingestion_service.import_document(
            filename=path.name,
            content=content,
            category=args.default_category,
            chunk_size=args.chunk_size,
            chunk_overlap=args.chunk_overlap,
            rebuild_index=False,
        )
        chunks += result["chunk_count"]
        imported += result["imported_count"]
        skipped += result["skipped_count"]
        deactivated += result["deactivated_count"]
        changed += result["changed_count"]
        print(
            f"[完成] {path.name}: 分块 {result['chunk_count']}，"
            f"新增 {result['imported_count']}，跳过 {result['skipped_count']}，"
            f"停用旧块 {result['deactivated_count']}"
        )

    if changed:
        await knowledge_service.refresh_index()
        print("已在全部文档同步完成后统一重建检索索引。")

    print(
        f"导入完成：{len(paths)} 篇文档，共 {chunks} 个分块，"
        f"新增 {imported}，去重跳过 {skipped}，停用旧块 {deactivated}。"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--document-dir", type=Path, default=DEFAULT_DOCUMENT_DIR)
    parser.add_argument("--db-url", default="sqlite:///data/smart_appointment.db")
    parser.add_argument(
        "--default-category",
        default="general",
        help="无 front matter category 时使用的分类（默认 general）",
    )
    parser.add_argument("--chunk-size", type=int, default=500)
    parser.add_argument("--chunk-overlap", type=int, default=100)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅输出分块数量，不初始化数据库或调用 Embedding API",
    )
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
