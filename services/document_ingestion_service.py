"""Markdown/TXT 文档解析、中文分块与知识库导入服务。"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, TYPE_CHECKING, Union

if TYPE_CHECKING:
    from .knowledge_service import KnowledgeService


SUPPORTED_DOCUMENT_EXTENSIONS = {".md", ".markdown", ".txt"}
DEFAULT_CHUNK_SIZE = 500
DEFAULT_CHUNK_OVERLAP = 100
MIN_CHUNK_SIZE = 100
MAX_CHUNK_SIZE = 4000
MAX_CHUNKS_PER_DOCUMENT = 200


@dataclass(frozen=True)
class DocumentChunk:
    """一个可检索的文档分块。"""

    content: str
    title: str
    chunk_index: int = 0
    chunk_count: int = 1


@dataclass(frozen=True)
class DocumentFrontMatter:
    """知识文档支持的轻量 YAML front matter 字段。"""

    source_id: Optional[str] = None
    title: Optional[str] = None
    category: Optional[str] = None
    keywords: tuple[str, ...] = ()
    version: Optional[str] = None


def parse_front_matter(text: str) -> tuple[str, DocumentFrontMatter]:
    """剥离文件开头的简单 YAML front matter。

    只解析项目约定的标量和 ``keywords: [a, b]``，避免为此引入 YAML 依赖。
    如果文本不是以 ``---`` 开头则原样返回；如果已经出现 front matter 起始标记但
    内容畸形，则抛出明确错误，防止配置文本进入检索语料。
    """
    normalized = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    if not lines or lines[0].strip() != "---":
        return normalized, DocumentFrontMatter()

    closing_index = next(
        (index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
        None,
    )
    if closing_index is None:
        raise ValueError("YAML front matter 缺少结束分隔符 ---")

    supported = {"source_id", "title", "category", "keywords", "version"}
    values: Dict[str, object] = {}
    for line_number, raw_line in enumerate(lines[1:closing_index], start=2):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise ValueError(f"YAML front matter 第 {line_number} 行缺少冒号")
        key, raw_value = line.split(":", 1)
        key = key.strip()
        if key not in supported:
            continue
        raw_value = raw_value.strip()
        if key == "keywords":
            values[key] = _parse_keywords(raw_value, line_number)
        else:
            values[key] = _unquote_scalar(raw_value)

    source_id = _optional_string(values.get("source_id"))
    title = _optional_string(values.get("title"))
    category = _optional_string(values.get("category"))
    version = _optional_string(values.get("version"))
    if source_id and len(source_id) > 64:
        raise ValueError("YAML front matter 的 source_id 不能超过 64 个字符")
    if title and len(title) > 255:
        raise ValueError("YAML front matter 的 title 不能超过 255 个字符")
    if category and len(category) > 100:
        raise ValueError("YAML front matter 的 category 不能超过 100 个字符")
    if version and len(version) > 64:
        raise ValueError("YAML front matter 的 version 不能超过 64 个字符")

    body = "\n".join(lines[closing_index + 1:]).lstrip("\n")
    return body, DocumentFrontMatter(
        source_id=source_id,
        title=title,
        category=category,
        keywords=tuple(values.get("keywords", ())),
        version=version,
    )


def _unquote_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1].strip()
    return value


def _optional_string(value: object) -> Optional[str]:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _parse_keywords(value: str, line_number: int) -> tuple[str, ...]:
    if not (value.startswith("[") and value.endswith("]")):
        raise ValueError(
            f"YAML front matter 第 {line_number} 行的 keywords 必须使用 [a, b] 格式"
        )
    inner = value[1:-1].strip()
    if not inner:
        return ()
    try:
        parsed = next(csv.reader([inner], skipinitialspace=True, strict=True))
    except csv.Error as exc:
        raise ValueError("YAML front matter 的 keywords 格式无效") from exc
    keywords = tuple(
        keyword
        for item in parsed
        if (keyword := _unquote_scalar(item))
    )
    if any(len(keyword) > 100 for keyword in keywords):
        raise ValueError("YAML front matter 的单个 keyword 不能超过 100 个字符")
    return keywords


class ChineseTextChunker:
    """优先按 Markdown 标题、段落和中文句末符号切分文本。

    ``chunk_size`` 与 ``chunk_overlap`` 均按 Python 字符数计算，适合以中文为主的
    文档。只有在单句超过上限时才进行字符级切分。
    """

    _heading_pattern = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
    _separators = ("\n\n", "\n", "。", "！", "？", "；", ".", "!", "?")

    def __init__(
        self,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
        max_chunks: int = MAX_CHUNKS_PER_DOCUMENT,
    ) -> None:
        self.validate_parameters(chunk_size, chunk_overlap, max_chunks)
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.max_chunks = max_chunks

    @staticmethod
    def validate_parameters(
        chunk_size: int,
        chunk_overlap: int,
        max_chunks: int = MAX_CHUNKS_PER_DOCUMENT,
    ) -> None:
        if not isinstance(chunk_size, int) or isinstance(chunk_size, bool):
            raise ValueError("chunk_size 必须是整数")
        if not isinstance(chunk_overlap, int) or isinstance(chunk_overlap, bool):
            raise ValueError("chunk_overlap 必须是整数")
        if not MIN_CHUNK_SIZE <= chunk_size <= MAX_CHUNK_SIZE:
            raise ValueError(
                f"chunk_size 必须在 {MIN_CHUNK_SIZE} 到 {MAX_CHUNK_SIZE} 之间"
            )
        if chunk_overlap < 0 or chunk_overlap > chunk_size // 2:
            raise ValueError("chunk_overlap 必须大于等于 0 且不能超过 chunk_size 的 50%")
        if not isinstance(max_chunks, int) or isinstance(max_chunks, bool) or max_chunks < 1:
            raise ValueError("max_chunks 必须是大于 0 的整数")

    def split_text(
        self, text: str, source_name: Optional[str] = None
    ) -> List[DocumentChunk]:
        """将文本分成带标题和顺序元数据的分块。"""
        normalized = self._normalize_text(text)
        if not normalized:
            return []

        fallback_title = Path(source_name or "未命名文档").stem[:255]
        raw_chunks: List[tuple[str, str]] = []
        for title, section_text in self._iter_markdown_sections(
            normalized, fallback_title
        ):
            for content in self._iter_section_chunks(section_text):
                if content:
                    raw_chunks.append((title, content))
                    if len(raw_chunks) > self.max_chunks:
                        raise ValueError(
                            f"单个文档最多生成 {self.max_chunks} 个分块；"
                            "请调大 chunk_size 或拆分文件后再导入"
                        )

        chunk_count = len(raw_chunks)
        return [
            DocumentChunk(
                title=title,
                content=content,
                chunk_index=index,
                chunk_count=chunk_count,
            )
            for index, (title, content) in enumerate(raw_chunks)
        ]

    @staticmethod
    def _normalize_text(text: str) -> str:
        if not isinstance(text, str):
            raise ValueError("文档内容必须是文本")
        text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
        lines = [line.rstrip() for line in text.split("\n")]
        return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()

    def _iter_markdown_sections(
        self, text: str, fallback_title: str
    ) -> Iterator[tuple[str, str]]:
        current_title = fallback_title
        current_lines: List[str] = []
        yielded_section = False

        for raw_line in io.StringIO(text):
            line = raw_line.rstrip("\n")
            match = self._heading_pattern.match(line)
            if match:
                content = "\n".join(current_lines).strip()
                if content:
                    yield current_title, content
                    yielded_section = True
                current_title = (match.group(1).strip() or fallback_title)[:255]
                current_lines = []
            else:
                current_lines.append(line)

        content = "\n".join(current_lines).strip()
        if content:
            yield current_title, content
            yielded_section = True

        # 只有标题、没有正文时仍保留可检索内容。
        if not yielded_section and current_title:
            yield current_title, current_title

    def _iter_section_chunks(self, text: str) -> Iterator[str]:
        if len(text) <= self.chunk_size:
            content = text.strip()
            if content:
                yield content
            return

        start = 0
        text_length = len(text)
        while start < text_length:
            hard_end = min(start + self.chunk_size, text_length)
            end = hard_end
            if hard_end < text_length:
                # 递归尝试更强到更弱的语义边界。
                end = self._find_boundary(text, start, hard_end, 0)
            if end <= start:
                end = hard_end

            chunk = text[start:end].strip()
            if chunk:
                yield chunk
            if end >= text_length:
                break

            next_start = max(start + 1, end - self.chunk_overlap)
            # 避免新块以空白开始，同时不主动越过有意义的重叠内容。
            while next_start < end and text[next_start].isspace():
                next_start += 1
            start = next_start

    def _find_boundary(self, text: str, start: int, hard_end: int, level: int) -> int:
        if level >= len(self._separators):
            return hard_end
        separator = self._separators[level]
        minimum = start + max(self.chunk_size // 2, self.chunk_overlap + 1)
        position = text.rfind(separator, minimum, hard_end)
        if position >= 0:
            return position + len(separator)
        return self._find_boundary(text, start, hard_end, level + 1)


class DocumentIngestionService:
    """把 UTF-8 Markdown/TXT 文档分块并写入现有知识库。"""

    def __init__(
        self,
        knowledge_service: "KnowledgeService",
        embedding_fn: Optional[Callable[[str], List[float]]] = None,
    ) -> None:
        self.knowledge_service = knowledge_service
        if embedding_fn is None:
            # 延迟导入，允许中文分块器在没有模型 SDK/密钥时独立测试。
            from .text_embedding import embed_input

            embedding_fn = embed_input
        self.embedding_fn = embedding_fn

    async def import_document(
        self,
        filename: str,
        content: Union[str, bytes],
        category: str = "general",
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
        rebuild_index: bool = True,
        source_id: Optional[str] = None,
    ) -> Dict:
        """导入单个文档并返回写入、去重统计。"""
        safe_name = Path(filename or "").name
        suffix = Path(safe_name).suffix.lower()
        if suffix not in SUPPORTED_DOCUMENT_EXTENSIONS:
            raise ValueError("仅支持 UTF-8 编码的 .md、.markdown 和 .txt 文件")
        if len(safe_name) > 255:
            safe_name = f"{Path(safe_name).stem[:255 - len(suffix)]}{suffix}"
        if isinstance(content, bytes):
            try:
                text = content.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise ValueError("文件必须使用 UTF-8 编码") from exc
        elif isinstance(content, str):
            text = content
        else:
            raise ValueError("文档内容必须是文本或字节")
        if not text.strip():
            raise ValueError("文档内容不能为空")

        text, front_matter = parse_front_matter(text)
        if not text.strip():
            raise ValueError("文档正文不能为空")

        requested_source_id = (source_id or "").strip()
        if len(requested_source_id) > 64:
            raise ValueError("source_id 长度不能超过 64 个字符")
        if (
            requested_source_id
            and front_matter.source_id
            and requested_source_id != front_matter.source_id
        ):
            raise ValueError("表单 source_id 与 YAML front matter 的 source_id 不一致")

        requested_category = (category or "").strip()
        if not requested_category or requested_category.lower() == "general":
            category = front_matter.category or requested_category or "general"
        else:
            category = requested_category
        if len(category) > 100:
            raise ValueError("category 长度不能超过 100 个字符")

        chunker = ChineseTextChunker(chunk_size, chunk_overlap)
        chunks = chunker.split_text(text, source_name=safe_name)
        filename_title = Path(safe_name).stem[:255]
        if front_matter.title:
            chunks = [
                DocumentChunk(
                    content=chunk.content,
                    title=(
                        front_matter.title
                        if chunk.title == filename_title
                        else chunk.title
                    ),
                    chunk_index=chunk.chunk_index,
                    chunk_count=chunk.chunk_count,
                )
                for chunk in chunks
            ]
        # 显式来源编号用于版本替换；没有编号时使用内容寻址，避免两个恰好同名
        # 的无关文件互相覆盖。相同内容仍会得到相同 ID 并正常去重。
        source_id = requested_source_id or front_matter.source_id or hashlib.sha256(
            safe_name.casefold().encode("utf-8") + b"\0" + text.encode("utf-8")
        ).hexdigest()

        existing_by_hash = {}
        existing_documents = await asyncio.to_thread(
            self.knowledge_service.db.get_all_documents,
            True,
        )
        for document in existing_documents:
            content_hash = document.get("content_hash")
            if document.get("source_id") != source_id or not content_hash:
                continue
            if content_hash not in existing_by_hash or document.get("is_active"):
                existing_by_hash[content_hash] = document
        chunk_payloads = []
        seen_hashes = set()
        duplicate_count = 0
        for chunk in chunks:
            content_hash = self._content_hash(chunk.content)
            if content_hash in seen_hashes:
                duplicate_count += 1
                continue
            seen_hashes.add(content_hash)
            keywords = list(dict.fromkeys(
                ([chunk.title] if chunk.title else []) + list(front_matter.keywords)
            ))
            existing = existing_by_hash.get(content_hash)
            embedding = None
            if not existing or not existing.get("embedding") or existing.get("title") != chunk.title:
                embedding_text = f"{chunk.title}\n{chunk.content}".strip()
                # Embedding SDK 是同步调用，放入线程避免阻塞 FastAPI 事件循环。
                embedding = await asyncio.to_thread(self.embedding_fn, embedding_text)
            chunk_payloads.append({
                "content": chunk.content,
                "category": category,
                "keywords": keywords,
                "embedding": embedding,
                "source_name": safe_name,
                "title": chunk.title,
                "chunk_index": chunk.chunk_index,
                "chunk_count": chunk.chunk_count,
                "content_hash": content_hash,
                "version": front_matter.version,
            })

        async with self.knowledge_service.mutation_lock:
            sync_result = await asyncio.to_thread(
                self.knowledge_service.db.sync_source_chunks,
                source_id,
                chunk_payloads,
            )
            changed = sync_result["changed_count"] > 0
            index_rebuilt = bool(changed and rebuild_index)
            if index_rebuilt:
                await self.knowledge_service._build_vector_index()

        return {
            "source_id": source_id,
            "source_name": safe_name,
            "category": category,
            "chunk_size": chunk_size,
            "chunk_overlap": chunk_overlap,
            "title": front_matter.title,
            "keywords": list(front_matter.keywords),
            "version": front_matter.version,
            "chunk_count": len(chunks),
            "imported_count": sync_result["imported_count"],
            "skipped_count": sync_result["skipped_count"] + duplicate_count,
            "deactivated_count": sync_result["deactivated_count"],
            "changed_count": sync_result["changed_count"],
            "document_ids": sync_result["document_ids"],
            "index_rebuilt": index_rebuilt,
        }

    @staticmethod
    def _content_hash(content: str) -> str:
        normalized = re.sub(r"\s+", " ", content).strip()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
