from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parents[1]
REGISTRY_PATH = ROOT / "verification" / "slices.yaml"
REPORT_PATH = ROOT / "verification" / "slice-1.json"
EVAL_PATH = ROOT / "reports" / "context-eval-latest.json"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def run_gates() -> dict[str, Any]:
    registry = json.loads(REGISTRY_PATH.read_text())
    gates = registry["slices"][0]["gates"]
    checks = []
    for gate in gates:
        result = subprocess.run(
            gate["command"],
            cwd=ROOT,
            capture_output=True,
            check=False,
        )
        check = {
            "id": gate["id"],
            "class": gate["class"],
            "blocking": bool(gate["blocking"]),
            "argv": gate["command"],
            "exit_code": result.returncode,
            "stdout_sha256": sha256_bytes(result.stdout),
            "stderr_sha256": sha256_bytes(result.stderr),
            "metric": gate["metric"],
            "comparator": gate["comparator"],
            "threshold": gate["threshold"],
            "result": "pass" if result.returncode == 0 else "fail",
        }
        if gate["id"] in {"S1-OFFICIAL-CORPUS", "S1-CONTEXT-REDUCTION"}:
            evaluation = json.loads(EVAL_PATH.read_text())
            if gate["id"] == "S1-OFFICIAL-CORPUS":
                check["observed"] = {
                    "false_negatives": len(evaluation["false_negatives"]),
                    "false_positives": len(evaluation["false_positives"]),
                    "recall": evaluation["claims"]["official_recall"],
                }
            else:
                check["observed"] = evaluation["consumer_context_reduction_basis_points"]
        checks.append(check)
    return {
        "schema": "technocore-verification-result/v1",
        "slice": 1,
        "verified_commit": git("rev-parse", "HEAD"),
        "verified_tree": git("rev-parse", "HEAD^{tree}"),
        "registry_sha256": sha256_bytes(REGISTRY_PATH.read_bytes()),
        "input_sha256": sha256_bytes((ROOT / "evals" / "official_corpus.json").read_bytes()),
        "checks": checks,
        "result": "pass" if all(check["result"] == "pass" for check in checks) else "fail",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--check", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    report = run_gates()
    if args.write:
        REPORT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    if args.verify:
        if not REPORT_PATH.exists():
            raise SystemExit("slice report is missing; run with --write")
        committed = json.loads(REPORT_PATH.read_text())
        expected_ids = [check["id"] for check in report["checks"]]
        committed_ids = [check["id"] for check in committed.get("checks", [])]
        if committed_ids != expected_ids:
            raise SystemExit("slice report does not cover every registered gate")
        ancestor = subprocess.run(
            ["git", "merge-base", "--is-ancestor", committed["verified_commit"], "HEAD"],
            cwd=ROOT,
            check=False,
        )
        if ancestor.returncode != 0:
            raise SystemExit("verified commit is not an ancestor of HEAD")
    print(json.dumps(report, separators=(",", ":"), sort_keys=True))
    if report["result"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
