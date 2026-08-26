import json

from evals.run_context_eval import evaluate
from tca.site import build_site, render_site


def test_failed_evidence_remains_visible(tmp_path) -> None:
    (tmp_path / "bad.json").write_text(json.dumps({"schema": "bad"}))
    html = render_site(tmp_path)
    assert "bad.json" in html
    assert "failed" in html
    assert "missing fields" in html


def test_context_content_is_escaped_and_csp_blocks_network(tmp_path) -> None:
    hostile = '</script><img src="https://canary.invalid/x" onerror="alert(1)">'
    context = {
        "items": [
            {
                "priority": 50,
                "source": "technocore",
                "excerpt": hostile,
                "match_reasons": ["question_observed"],
            }
        ],
        "coverage": [],
    }
    rendered = render_site(tmp_path, {"brief": context}, {})
    assert hostile not in rendered
    assert "&lt;/script&gt;" in rendered
    assert "connect-src 'none'" in rendered
    assert "innerHTML" not in rendered


def test_site_assets_reproduce(tmp_path) -> None:
    evidence = tmp_path / "evidence"
    site = tmp_path / "docs"
    evidence.mkdir()
    assert build_site(evidence, site)
    assert (site / "index.html").exists()
    assert (site / "app.css").exists()
    assert (site / "app.js").exists()
    assert (site / "schemas" / "context-brief-v1.schema.json").exists()
    assert (site / "schemas" / "context-dashboard-v1.schema.json").exists()
    assert build_site(evidence, site, check=True)


def test_dashboard_renders_dynamic_claims_and_complete_suppression(tmp_path) -> None:
    report, dashboard = evaluate()
    rendered = render_site(tmp_path, dashboard, report)
    metrics = dashboard["metrics"]
    brief = dashboard["brief"]
    assert metrics["official_recall"] in rendered
    assert f"{metrics['reduction_basis_points'] / 100:.2f}%" in rendered
    assert f"Deferred by this page budget: {brief['suppressed']['over_budget']}" in rendered
    assert "Synthetic repetition-stress snapshot" in rendered


def test_dashboard_budget_curve_matches_unmodified_brief() -> None:
    _report, dashboard = evaluate()
    brief = dashboard["brief"]
    point = next(
        item for item in dashboard["budget_curve"] if item["budget"] == brief["budget"]["requested"]
    )
    assert point["estimated_used"] == brief["budget"]["estimated_used"]
    assert point["critical_items_remaining"] == brief["critical_items_remaining"]
    assert point["over_budget"] == brief["suppressed"]["over_budget"]
    assert point["evidence_ids"] == [item["evidence_id"] for item in brief["items"]]
