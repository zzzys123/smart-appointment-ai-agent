"""Verify the frozen Golden v2 files and knowledge sources."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "evaluation" / "GOLDEN_V2_MANIFEST.json"


def _sha256(path: Path) -> str:
    # Git may materialize text files with CRLF on Windows and LF elsewhere.
    # Golden hashes describe canonical content, not the checkout's EOL style.
    canonical = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(canonical).hexdigest()


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    failures = []
    checked = 0
    groups = (
        manifest["frozen_files_sha256"],
        manifest["knowledge_sources_sha256"],
    )
    for expected_hashes in groups:
        for relative_path, expected in expected_hashes.items():
            path = ROOT / relative_path
            checked += 1
            if not path.is_file():
                failures.append(f"缺少文件: {relative_path}")
                continue
            actual = _sha256(path)
            if actual != expected:
                failures.append(
                    f"校验失败: {relative_path}\n"
                    f"  expected={expected}\n  actual={actual}"
                )

    positive = json.loads(
        (ROOT / "evaluation" / "document_chunking_cases.json").read_text(
            encoding="utf-8"
        )
    )
    negative = json.loads(
        (ROOT / "evaluation" / "no_answer_cases.json").read_text(
            encoding="utf-8"
        )
    )
    if len(positive) != manifest["cases"]["positive_count"]:
        failures.append(f"有答案用例数量变化: {len(positive)}")
    if len(negative) != manifest["cases"]["negative_count"]:
        failures.append(f"无答案用例数量变化: {len(negative)}")

    if failures:
        print("Golden v2 校验失败：")
        print("\n".join(f"- {failure}" for failure in failures))
        return 1

    print(
        f"Golden v2 校验通过：{checked} 个文件，"
        f"{len(positive)} 条有答案 + {len(negative)} 条无答案用例。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
