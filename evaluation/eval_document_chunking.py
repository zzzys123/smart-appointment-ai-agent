"""离线比较整篇文档索引与中文分块索引的检索效果。

该脚本不访问数据库、Embedding 或 LLM API。它复用生产代码中的
``ChineseTextChunker``，再以一个轻量 BM25 实现对同一组查询分别检索：

1. Whole document：每篇 Markdown 是一个候选项；
2. Chunked passage：每个中文分块是一个候选项。

核心指标要求命中的具体候选片段同时满足正确 ``source_id`` 且包含预期证据文本，
而不是只要同一来源的任意片段出现就算成功。脚本输出 Evidence Recall@1、@3、
@10 与 Evidence MRR，并报告候选文本长度和 Top-3 上下文成本。分块不保证提高
BM25 Top-1；它的价值在于缩小送入模型的上下文，并为 Hybrid/Rerank 提供细粒度
候选。Dense、Hybrid 与 Rerank 仍应由项目原有评估脚本验证。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))



def _load_chunker_class():
    """直接加载分块模块，避免 ``services.__init__`` 拉入模型 SDK。"""
    module_path = PROJECT_ROOT / "services" / "document_ingestion_service.py"
    spec = importlib.util.spec_from_file_location(
        "_document_ingestion_service_eval", module_path
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载中文分块器：{module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.ChineseTextChunker


ChineseTextChunker = _load_chunker_class()


DEFAULT_CORPUS_DIR = PROJECT_ROOT / "knowledge_documents"
DEFAULT_CASES_FILE = Path(__file__).with_name("document_chunking_cases.json")
FRONT_MATTER_PATTERN = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)
CODE_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)+|[a-z]+|\d+(?:\.\d+)?", re.I)
CJK_PATTERN = re.compile(r"[\u3400-\u9fff]+")


@dataclass(frozen=True)
class SearchItem:
    source_id: str
    source_name: str
    title: str
    content: str
    chunk_index: int = 0

    @property
    def searchable_text(self) -> str:
        return f"{self.title}\n{self.content}".strip()


def _parse_front_matter(text: str, fallback_id: str) -> tuple[Dict[str, str], str]:
    """解析本演示语料使用的简单 YAML front matter，不引入 YAML 依赖。"""
    match = FRONT_MATTER_PATTERN.match(text.lstrip("\ufeff"))
    if not match:
        return {"source_id": fallback_id}, text

    metadata: Dict[str, str] = {"source_id": fallback_id}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip('"\'')
    return metadata, text[match.end() :].strip()


def _load_corpus(corpus_dir: Path, chunk_size: int, chunk_overlap: int):
    if not corpus_dir.is_dir():
        raise FileNotFoundError(f"知识文档目录不存在：{corpus_dir}")

    chunker = ChineseTextChunker(chunk_size, chunk_overlap)
    whole_items: List[SearchItem] = []
    chunk_items: List[SearchItem] = []
    for path in sorted(corpus_dir.glob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        raw = path.read_text(encoding="utf-8-sig")
        metadata, body = _parse_front_matter(raw, path.stem)
        source_id = metadata.get("source_id", path.stem)
        title = metadata.get("title", path.stem)
        whole_items.append(SearchItem(source_id, path.name, title, body))
        for chunk in chunker.split_text(body, source_name=path.name):
            chunk_items.append(
                SearchItem(
                    source_id=source_id,
                    source_name=path.name,
                    title=chunk.title,
                    content=chunk.content,
                    chunk_index=chunk.chunk_index,
                )
            )
    if not whole_items:
        raise ValueError(f"目录中没有可评估的 Markdown 文档：{corpus_dir}")
    return whole_items, chunk_items


def _tokenize(text: str) -> List[str]:
    """保留编码/数字，并为连续中文生成单字与二元词元。"""
    normalized = text.lower().replace("`", " ")
    tokens = CODE_PATTERN.findall(normalized)
    for span in CJK_PATTERN.findall(normalized):
        tokens.extend(span)
        tokens.extend(span[index : index + 2] for index in range(len(span) - 1))
    return tokens


class SimpleBM25:
    """仅供离线评估使用的 BM25，避免引入评估专用依赖。"""

    def __init__(self, texts: Iterable[str], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.documents = [_tokenize(text) for text in texts]
        self.term_counts = [Counter(tokens) for tokens in self.documents]
        self.lengths = [len(tokens) for tokens in self.documents]
        self.avg_length = sum(self.lengths) / len(self.lengths) if self.lengths else 1.0
        document_frequency = Counter()
        for counts in self.term_counts:
            document_frequency.update(counts.keys())
        count = len(self.documents)
        self.idf = {
            term: math.log(1.0 + (count - frequency + 0.5) / (frequency + 0.5))
            for term, frequency in document_frequency.items()
        }

    def scores(self, query: str) -> List[float]:
        query_counts = Counter(_tokenize(query))
        scores: List[float] = []
        for counts, length in zip(self.term_counts, self.lengths):
            score = 0.0
            norm = self.k1 * (1.0 - self.b + self.b * length / self.avg_length)
            for term, query_frequency in query_counts.items():
                frequency = counts.get(term, 0)
                if not frequency:
                    continue
                score += (
                    self.idf.get(term, 0.0)
                    * frequency
                    * (self.k1 + 1.0)
                    / (frequency + norm)
                    * query_frequency
                )
            scores.append(score)
        return scores


def _rank_items(index: SimpleBM25, items: Sequence[SearchItem], query: str):
    """按相关度返回正分候选；零分结果不应伪装成召回。"""
    scores = index.scores(query)
    ranked_indices = sorted(
        (index_ for index_ in range(len(items)) if scores[index_] > 0),
        key=lambda index_: (-scores[index_], index_),
    )
    return [(items[index_], scores[index_]) for index_ in ranked_indices]


def _result_summary(
    item: SearchItem, score: float, expected_source: str, expected_text: str
) -> Dict:
    contains_expected = expected_text in item.searchable_text
    return {
        "source_id": item.source_id,
        "source_name": item.source_name,
        "title": item.title,
        "chunk_index": item.chunk_index,
        "score": round(score, 6),
        "char_count": len(item.searchable_text),
        "contains_expected": contains_expected,
        "is_evidence": item.source_id == expected_source and contains_expected,
    }


def _evaluate(items: Sequence[SearchItem], cases: Sequence[Dict]) -> Dict:
    index = SimpleBM25(item.searchable_text for item in items)
    details = []
    for case in cases:
        ranking = _rank_items(index, items, case["query"])
        expected_source = case["expected_source_id"]
        expected_text = case["expected_contains"]
        source_rank = next(
            (
                rank
                for rank, (item, _) in enumerate(ranking, start=1)
                if item.source_id == expected_source
            ),
            0,
        )
        evidence_rank = next(
            (
                rank
                for rank, (item, _) in enumerate(ranking, start=1)
                if item.source_id == expected_source
                and expected_text in item.searchable_text
            ),
            0,
        )
        details.append(
            {
                **case,
                "rank": evidence_rank,
                "source_rank": source_rank,
                "top3": [
                    _result_summary(item, score, expected_source, expected_text)
                    for item, score in ranking[:3]
                ],
            }
        )

    total = len(details)
    return {
        "recall_at_1": sum(row["rank"] == 1 for row in details) / total,
        "recall_at_3": sum(0 < row["rank"] <= 3 for row in details) / total,
        "recall_at_10": sum(0 < row["rank"] <= 10 for row in details) / total,
        "mrr": sum(1.0 / row["rank"] if row["rank"] else 0.0 for row in details) / total,
        "source_recall_at_1": sum(row["source_rank"] == 1 for row in details) / total,
        "source_recall_at_3": sum(
            0 < row["source_rank"] <= 3 for row in details
        ) / total,
        "source_recall_at_10": sum(
            0 < row["source_rank"] <= 10 for row in details
        ) / total,
        "source_mrr": sum(
            1.0 / row["source_rank"] if row["source_rank"] else 0.0
            for row in details
        ) / total,
        "average_candidate_chars": sum(
            len(item.searchable_text) for item in items
        ) / len(items),
        "average_top3_context_chars": sum(
            sum(result["char_count"] for result in row["top3"])
            for row in details
        ) / total,
        "details": details,
    }


def _group_metrics(details: Sequence[Dict]) -> Dict[str, Dict[str, float]]:
    grouped: Dict[str, List[Dict]] = defaultdict(list)
    for row in details:
        grouped[row["group"]].append(row)
    result = {}
    for group, rows in grouped.items():
        total = len(rows)
        result[group] = {
            "recall_at_1": sum(row["rank"] == 1 for row in rows) / total,
            "recall_at_3": sum(0 < row["rank"] <= 3 for row in rows) / total,
            "recall_at_10": sum(0 < row["rank"] <= 10 for row in rows) / total,
            "mrr": sum(1.0 / row["rank"] if row["rank"] else 0.0 for row in rows) / total,
        }
    return result


def _validate_cases(
    cases: Sequence[Dict],
    whole_items: Sequence[SearchItem],
    chunk_items: Sequence[SearchItem],
) -> None:
    corpus = {item.source_id: item.content for item in whole_items}
    errors = []
    for case in cases:
        source_id = case["expected_source_id"]
        if source_id not in corpus:
            errors.append(f"{case['id']}: 不存在来源 {source_id}")
        elif case.get("expected_contains") not in corpus[source_id]:
            errors.append(
                f"{case['id']}: 期望片段不在 {source_id} 中：{case.get('expected_contains')}"
            )
        elif not any(
            item.source_id == source_id
            and case["expected_contains"] in item.searchable_text
            for item in chunk_items
        ):
            errors.append(
                f"{case['id']}: 期望片段被切跨分块边界：{case['expected_contains']}"
            )
    if errors:
        raise ValueError("评估用例与语料不一致：\n- " + "\n- ".join(errors))


def _print_report(whole: Dict, chunked: Dict, document_count: int, chunk_count: int) -> None:
    print("=" * 100)
    print(f"文档分块检索评估：{document_count} 篇原文 -> {chunk_count} 个分块")
    print("=" * 100)
    print(
        f"{'索引方式':<20}{'Evidence@1':>12}{'Evidence@3':>12}"
        f"{'Evidence@10':>13}{'MRR':>12}"
    )
    print("-" * 100)
    for name, result in (("Whole document", whole), ("Chunked passage", chunked)):
        print(
            f"{name:<20}{result['recall_at_1']:>11.1%}"
            f"{result['recall_at_3']:>12.1%}{result['recall_at_10']:>13.1%}"
            f"{result['mrr']:>12.3f}"
        )

    print("\n辅助来源指标（只要求 source_id 正确，不要求具体片段含证据）：")
    print(
        f"{'索引方式':<20}{'Source@1':>12}{'Source@3':>12}"
        f"{'Source@10':>13}{'Source MRR':>12}"
    )
    print("-" * 100)
    for name, result in (("Whole document", whole), ("Chunked passage", chunked)):
        print(
            f"{name:<20}{result['source_recall_at_1']:>11.1%}"
            f"{result['source_recall_at_3']:>12.1%}"
            f"{result['source_recall_at_10']:>13.1%}{result['source_mrr']:>12.3f}"
        )

    whole_top3_chars = whole["average_top3_context_chars"]
    chunk_top3_chars = chunked["average_top3_context_chars"]
    compression = 1.0 - chunk_top3_chars / whole_top3_chars if whole_top3_chars else 0.0
    print("\n候选粒度与上下文成本（按字符数，含标题）：")
    print(f"{'索引方式':<20}{'平均候选长度':>16}{'平均Top-3上下文':>20}")
    print("-" * 100)
    for name, result in (("Whole document", whole), ("Chunked passage", chunked)):
        print(
            f"{name:<20}{result['average_candidate_chars']:>16.1f}"
            f"{result['average_top3_context_chars']:>20.1f}"
        )
    print(f"Chunked Top-3 相对 Whole Top-3 的上下文字符压缩率：{compression:.1%}")

    print("\n逐条首个证据命中排名（- 表示未命中）：")
    print(
        f"{'用例':<33}{'分组':<24}{'Whole证据':>10}"
        f"{'Chunk证据':>10}{'Chunk来源':>10}"
    )
    print("-" * 100)
    for before, after in zip(whole["details"], chunked["details"]):
        whole_rank = before["rank"] or "-"
        chunk_rank = after["rank"] or "-"
        chunk_source_rank = after["source_rank"] or "-"
        print(
            f"{before['id']:<33}{before['group']:<24}"
            f"{str(whole_rank):>10}{str(chunk_rank):>10}"
            f"{str(chunk_source_rank):>10}"
        )

    print("\nTop-3 具体候选（source#chunk，* 表示来源正确且片段含预期证据）：")
    for before, after in zip(whole["details"], chunked["details"]):
        print(f"- {before['id']}")
        print(f"  Whole: {_format_top3(before['top3'])}")
        print(f"  Chunk: {_format_top3(after['top3'])}")

    print("\nChunked passage 分组指标：")
    for group, metrics in _group_metrics(chunked["details"]).items():
        print(
            f"- {group:<24} Recall@1={metrics['recall_at_1']:.1%}, "
            f"Recall@3={metrics['recall_at_3']:.1%}, "
            f"Recall@10={metrics['recall_at_10']:.1%}, MRR={metrics['mrr']:.3f}"
        )
    print("=" * 100)


def _format_top3(results: Sequence[Dict]) -> str:
    if not results:
        return "(无正分候选)"
    return ", ".join(
        f"{row['source_id']}#{row['chunk_index']}"
        f"{'*' if row['is_evidence'] else ''}:{row['score']:.3f}"
        for row in results
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS_DIR)
    parser.add_argument("--cases-file", type=Path, default=DEFAULT_CASES_FILE)
    parser.add_argument("--chunk-size", type=int, default=500)
    parser.add_argument("--chunk-overlap", type=int, default=100)
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()

    cases = json.loads(args.cases_file.read_text(encoding="utf-8-sig"))
    whole_items, chunk_items = _load_corpus(
        args.corpus_dir, args.chunk_size, args.chunk_overlap
    )
    _validate_cases(cases, whole_items, chunk_items)
    whole = _evaluate(whole_items, cases)
    chunked = _evaluate(chunk_items, cases)
    _print_report(whole, chunked, len(whole_items), len(chunk_items))

    if args.json_output:
        payload = {
            "config": {
                "corpus_dir": str(args.corpus_dir),
                "cases_file": str(args.cases_file),
                "chunk_size": args.chunk_size,
                "chunk_overlap": args.chunk_overlap,
                "document_count": len(whole_items),
                "chunk_count": len(chunk_items),
            },
            "whole_document": whole,
            "chunked_passage": chunked,
        }
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"JSON 报告已写入：{args.json_output}")


if __name__ == "__main__":
    main()
