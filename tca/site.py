from __future__ import annotations

import html
import json
from pathlib import Path

from tca.evidence import load_record, verify_record


def render_site(evidence_dir: Path) -> str:
    rows: list[str] = []
    for path in sorted(evidence_dir.glob("*.json")):
        try:
            record = load_record(path)
            valid, errors = verify_record(record)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            record = {}
            valid, errors = False, [str(exc)]
        status = "valid" if valid else "failed"
        artifact = str(record.get("artifact_url", ""))
        artifact_cell = (
            f'<a rel="noopener noreferrer" href="{html.escape(artifact)}">artifact</a>'
            if artifact.startswith("https://")
            else "-"
        )
        rows.append(
            "<tr>"
            f"<td>{html.escape(path.name)}</td>"
            f'<td class="{status}">{status}</td>'
            f"<td>{html.escape(str(record.get('kind', 'unknown')))}</td>"
            f"<td>{html.escape(str(record.get('published_at', '')))}</td>"
            f"<td>{artifact_cell}</td>"
            f"<td>{html.escape('; '.join(errors))}</td>"
            "</tr>"
        )
    body = "\n".join(rows) or '<tr><td colspan="6">No evidence records yet.</td></tr>'
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Technocore contribution evidence</title>
<style>
body{{font:16px/1.5 system-ui,sans-serif;max-width:1100px;margin:3rem auto;
padding:0 1rem;color:#172033}}
table{{width:100%;border-collapse:collapse}}
th,td{{padding:.65rem;border:1px solid #d8deea;text-align:left;vertical-align:top}}
th{{background:#f3f6fb}}
.valid{{color:#137333;font-weight:700}}.failed{{color:#b3261e;font-weight:700}}
code{{background:#f3f6fb;padding:.15rem .3rem}}small{{color:#5f6b7a}}
</style>
</head>
<body>
<h1>Technocore contribution evidence</h1>
<p>Every record is checked against <code>technocore-contribution-evidence/v1</code>
and its Ed25519 DID signature. Failed records remain visible.</p>
<table>
<thead><tr><th>Record</th><th>Status</th><th>Kind</th><th>Published</th>
<th>Artifact</th><th>Verification</th></tr></thead>
<tbody>{body}</tbody>
</table>
<p><small>Generated locally. No private key or browser credential is used by this page.</small></p>
</body>
</html>
"""


def build_site(evidence_dir: Path, site_dir: Path, check: bool = False) -> bool:
    content = render_site(evidence_dir)
    destination = site_dir / "index.html"
    if check:
        return destination.exists() and destination.read_text() == content
    site_dir.mkdir(parents=True, exist_ok=True)
    destination.write_text(content)
    return True
