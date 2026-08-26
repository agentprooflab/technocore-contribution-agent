from evals.run_context_eval import BASELINE_NAME, evaluate


def test_evaluation_uses_fair_baseline_and_raw_identity_variants() -> None:
    report, _dashboard = evaluate()
    assert report["baseline"]["name"] == BASELINE_NAME
    assert report["false_negatives"] == []
    assert report["false_positives"] == []
    assert set(report["negative_envelope_variants"]) == {
        "homoglyph_username",
        "missing_actor_id",
        "missing_username",
        "wrong_actor_and_username",
        "wrong_actor_id",
        "wrong_username",
    }


def test_evaluation_budget_curve_is_integral_and_compiler_inclusive() -> None:
    report, dashboard = evaluate()
    assert report["budget_integrity_failures"] == []
    assert report["brief_budget_units"] == dashboard["metrics"]["compiled_budget_units"]
    assert report["raw_budget_units"] == dashboard["metrics"]["raw_budget_units"]
    assert report["brief_pages"] == dashboard["metrics"]["page_count"]
    for point in dashboard["budget_curve"]:
        if point["error"] is None:
            assert point["estimated_used"] <= point["budget"]
            assert point["payload_sha256"] is not None
