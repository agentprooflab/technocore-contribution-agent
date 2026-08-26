import json

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
    rendered = render_site(tmp_path, context, {})
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
    assert build_site(evidence, site, check=True)
