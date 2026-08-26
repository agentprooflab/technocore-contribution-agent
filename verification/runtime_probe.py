from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
PROBES = (
    (
        "final-path-health-legacy-rollback",
        "tests/test_installer.py::test_installer_executes_final_runtime_and_rolls_back_after_activation",
    ),
    (
        "fresh-runtime-does-not-reuse-tampered-current",
        "tests/test_installer.py::test_fresh_install_never_executes_or_reuses_tampered_current_runtime",
    ),
    (
        "non-symlink-pointer-refusal",
        "tests/test_installer.py::test_installer_refuses_non_symlink_runtime_pointer",
    ),
    (
        "trusted-parent-symlink-refusal",
        "tests/test_installer.py::test_installer_rejects_symlinked_trusted_parent_before_mutation",
    ),
    (
        "trusted-parent-owner-refusal",
        "tests/test_installer.py::test_installer_rejects_wrong_owner_trusted_parent_before_mutation",
    ),
    (
        "first-install-registration-without-health-rollback",
        "tests/test_installer.py::test_registration_without_successful_observer_run_rolls_back_first_install",
    ),
    (
        "required-source-and-freshness-health-gate",
        "tests/test_installer.py::test_failed_or_stale_required_source_report_cannot_create_health",
    ),
    (
        "fatal-previous-job-recovery-failure",
        "tests/test_installer.py::test_rollback_surfaces_fatal_error_when_previous_job_cannot_be_restarted",
    ),
    (
        "candidate-bootstrap-failure-restores-previous",
        "tests/test_installer.py::test_candidate_bootstrap_failure_restores_loaded_previous_job_without_false_fatal",
    ),
    (
        "public-only-unattended-ranking",
        "tests/test_ranking.py::test_unattended_ranking_ignores_restricted_observations_and_collisions",
    ),
)


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def run_probes() -> dict:
    probes = []
    installer_tests = (ROOT / "tests/test_installer.py").read_text()
    discovered = {
        f"tests/test_installer.py::{name}"
        for name in re.findall(r"^def (test_[a-z0-9_]+)\(", installer_tests, flags=re.MULTILINE)
    }
    configured = {
        node_id for _, node_id in PROBES if node_id.startswith("tests/test_installer.py::")
    }
    missing = sorted(discovered - configured)
    probes.append(
        {
            "id": "installer-probe-registry-complete",
            "node_id": "verification.runtime_probe:PROBES",
            "exit_code": int(bool(missing)),
            "missing_node_ids": missing,
            "stdout_sha256": sha256(b""),
            "stderr_sha256": sha256(b""),
            "result": "fail" if missing else "pass",
        }
    )
    for probe_id, node_id in PROBES:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", node_id],
            cwd=ROOT,
            capture_output=True,
            check=False,
        )
        probes.append(
            {
                "id": probe_id,
                "node_id": node_id,
                "exit_code": result.returncode,
                "stdout_sha256": sha256(result.stdout),
                "stderr_sha256": sha256(result.stderr),
                "result": "pass" if result.returncode == 0 else "fail",
            }
        )
    failures = [probe["id"] for probe in probes if probe["result"] != "pass"]
    return {
        "schema": "technocore-runtime-probe/v1",
        "probes": probes,
        "failures": failures,
        "result": "pass" if not failures else "fail",
    }


def main() -> None:
    report = run_probes()
    print(json.dumps(report, separators=(",", ":"), sort_keys=True))
    if report["result"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
