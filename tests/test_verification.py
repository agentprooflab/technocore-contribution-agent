from evals.run_context_eval import evaluate
from verification.run import (
    compare,
    content_attributable_io_calls,
    dashboard_contract_failures,
    golden_payload_digest_mismatches,
    run_gates,
)


def test_comparator_semantics_are_enforced() -> None:
    assert compare(0, "equal", 0)
    assert not compare(1, "equal", 0)
    assert compare(500, "greater_than_or_equal", 500)
    assert not compare(499, "greater_than_or_equal", 500)
    assert compare(0, "less_than_or_equal", 0)


def test_fixed_golden_payload_matches() -> None:
    assert golden_payload_digest_mismatches() == 0


def test_hostile_content_attempts_no_instrumented_io() -> None:
    count, calls = content_attributable_io_calls()
    assert count == 0
    assert calls == []


def test_dashboard_contract_probe_reports_no_failures() -> None:
    _report, dashboard = evaluate()
    assert dashboard_contract_failures(dashboard) == []


def test_every_registered_vertical_slice_is_executed() -> None:
    report = run_gates()
    assert report["schema"] == "technocore-verification-result/v3"
    assert [item["id"] for item in report["slices"]] == ["S1", "S2"]
    assert all(item["result"] == "pass" for item in report["slices"])
    assert {check["id"] for check in report["checks"]} >= {"S2-RUNTIME-INSTALLER"}
