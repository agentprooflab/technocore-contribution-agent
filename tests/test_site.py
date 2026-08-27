import json

from evals.run_context_eval import evaluate
from tca.site import SITE_CSS, SITE_JS, build_site, render_site


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
    assert (site / "assets" / "signal-noise-hero-v1.jpg").exists()
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
    assert f"DEFERRED {brief['suppressed']['over_budget']}" in rendered
    assert "CURATED SYNTHETIC CONTRIBUTION SCENARIOS" in rendered


def test_dashboard_uses_plain_language_editorial_hierarchy_and_motion_fallback(tmp_path) -> None:
    report, dashboard = evaluate()
    rendered = render_site(tmp_path, dashboard, report)
    assert "Stop reading" in rendered
    assert "Turn the<br>firehose down" in rendered
    assert "This simulates one agent request" in rendered
    assert "MODEL-NEUTRAL BUDGET UNITS" in rendered
    assert "Only complete evidence objects appear" in rendered
    assert "continuation cursor" in rendered
    assert "Evidence<br>before vibes" in rendered
    assert "Not an indexer. Not a reputation score. Not an eligibility oracle." in rendered
    assert 'src="assets/signal-noise-hero-v1.jpg"' in rendered
    assert "prefers-reduced-motion:reduce" in SITE_CSS
    assert "IntersectionObserver" in SITE_JS
    assert "innerHTML" not in SITE_JS


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
