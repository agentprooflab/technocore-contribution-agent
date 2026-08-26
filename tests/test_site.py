import json

from tca.site import render_site


def test_failed_evidence_remains_visible(tmp_path) -> None:
    (tmp_path / "bad.json").write_text(json.dumps({"schema": "bad"}))
    html = render_site(tmp_path)
    assert "bad.json" in html
    assert "failed" in html
    assert "missing fields" in html
