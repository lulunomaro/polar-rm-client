"""Report static architecture and enforce reviewed, dependency-free ratchets.

    python tools/quality_gate.py
    python tools/quality_gate.py --json tmp_quality/architecture.json --markdown tmp_quality/architecture.md

Checks never update the baseline. Intentional budget changes require a reviewed
source diff, reason, and focused behavior/mutation tests; see docs/quality_gates.md.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.quality.gates import check_report, load_baseline, render_markdown
from tools.quality.metrics import measure_tree

DEFAULT_BASELINE = ROOT / "tools" / "quality" / "baseline.json"


def write_report(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=ROOT, help="Source checkout to measure; does not import it")
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--expect-source-sha256", help="Require this measured source-tree digest; never fetches or rewrites source")
    parser.add_argument("--json", type=Path, help="Optional full machine-readable measurements")
    parser.add_argument("--markdown", type=Path, help="Optional human-readable hotspot report")
    args = parser.parse_args(argv)
    try:
        if args.expect_source_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", args.expect_source_sha256):
            raise ValueError("Expected source SHA-256 must contain exactly 64 lowercase hexadecimal characters")
        baseline = load_baseline(args.baseline)
        report = measure_tree(args.root.resolve())
        violations = check_report(report, baseline)
        if args.expect_source_sha256 is not None and report["source_tree_sha256"] != args.expect_source_sha256:
            violations.append(f"Measured source SHA-256 differs: expected {args.expect_source_sha256}, "
                              f"got {report['source_tree_sha256']}")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"FAIL: quality check could not establish evidence: {exc}")
        return 2
    report["gate"] = {"status": "FAIL" if violations else "PASS", "violations": violations,
                      "baseline_revision": baseline["source_revision"]}
    if args.json:
        write_report(args.json, json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    if args.markdown:
        write_report(args.markdown, render_markdown(report, violations))
    summary = report["summary"]
    for violation in violations:
        print("FAIL: " + violation)
    print(f"{'FAIL' if violations else 'PASS'}: static quality gates; {summary['parsed_files']} Python modules, "
          f"{summary['functions']} functions, {len(report['runtime_cycles'])} initialization cycles; "
          "no runtime or hardware qualification")
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
