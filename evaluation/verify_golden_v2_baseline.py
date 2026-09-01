"""Verify frozen Golden v2 Dev and Holdout baseline artifacts."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.golden_v2_protocol import validate_complete_result


MANIFEST = ROOT / "evaluation" / "GOLDEN_V2_BASELINE_MANIFEST.json"


def _sha256(path: Path) -> str:
    canonical = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(canonical).hexdigest()


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    failures = []
    for split, expected in manifest["results"].items():
        path = ROOT / expected["path"]
        if not path.is_file():
            failures.append(f"缺少基线文件: {expected['path']}")
            continue
        actual_hash = _sha256(path)
        if actual_hash != expected["sha256_lf_normalized"]:
            failures.append(
                f"基线哈希失败: {expected['path']}\n"
                f"  expected={expected['sha256_lf_normalized']}\n"
                f"  actual={actual_hash}"
            )
        try:
            result = json.loads(path.read_text(encoding="utf-8"))
            validate_complete_result(result, split)
        except (json.JSONDecodeError, ValueError) as exc:
            failures.append(f"基线内容失败: {expected['path']}: {exc}")

    if failures:
        print("Golden v2 基线校验失败：")
        print("\n".join(f"- {failure}" for failure in failures))
        return 1
    print("Golden v2 基线校验通过：40 条 Dev + 20 条 Holdout；成本状态为未采集。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
