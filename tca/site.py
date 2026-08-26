from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from tca.context import budget_units
from tca.evidence import load_record, verify_record

SITE_CSS = """
:root{color-scheme:dark;--ink:#f7f8ff;--muted:#98a1bd;--line:#27304d;--panel:#11172a;--cyan:#64e7ff;--violet:#9b8cff;--green:#73f7b2;--amber:#ffcf6d;--red:#ff7f91}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:#080b16;color:var(--ink);font:15px/1.55 Inter,ui-sans-serif,system-ui,-apple-system,sans-serif}
body:before{content:"";position:fixed;inset:0;pointer-events:none;background:radial-gradient(circle at 18% -10%,#4d48d944,transparent 35%),radial-gradient(circle at 85% 12%,#08b9d52b,transparent 30%);z-index:-1}
a{color:inherit}.shell{width:min(1180px,calc(100% - 32px));margin:auto}.topbar{display:flex;justify-content:space-between;align-items:center;padding:24px 0}.brand{font-weight:760;letter-spacing:-.02em}.brand span{color:var(--cyan)}
.badge{display:inline-flex;align-items:center;gap:8px;border:1px solid #315c68;background:#0b2831;color:#a8f4ff;border-radius:999px;padding:7px 11px;font-size:12px}.dot{width:7px;height:7px;border-radius:50%;background:var(--green);box-shadow:0 0 14px var(--green)}
.hero{padding:76px 0 54px;display:grid;grid-template-columns:1.2fr .8fr;gap:48px;align-items:end}.eyebrow{color:var(--cyan);font-size:12px;font-weight:750;letter-spacing:.16em;text-transform:uppercase}.hero h1{font-size:clamp(44px,7vw,84px);line-height:.96;letter-spacing:-.065em;margin:14px 0 24px;max-width:850px}.lede{font-size:20px;color:#c6cce0;max-width:720px}.hero-note{border-left:1px solid var(--line);padding-left:30px;color:var(--muted)}
.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:24px 0 72px}.metric,.panel{background:linear-gradient(145deg,#151b31d9,#0d1223e8);border:1px solid var(--line);box-shadow:0 22px 70px #0004}.metric{padding:22px;border-radius:18px}.metric strong{display:block;font-size:30px;letter-spacing:-.04em}.metric span{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.08em}
.section{padding:38px 0 54px}.section-head{display:flex;justify-content:space-between;gap:20px;align-items:end;margin-bottom:22px}.section h2{font-size:30px;letter-spacing:-.04em;margin:0}.section-head p{color:var(--muted);max-width:570px;margin:0}.panel{border-radius:22px;padding:24px}.brief-grid{display:grid;grid-template-columns:.72fr 1.28fr;gap:16px}
.controls label{display:block;color:var(--muted);font-size:12px;margin-bottom:10px}.controls input{width:100%;accent-color:var(--cyan)}.controls output{display:block;margin-top:12px}.curve-point{display:none}.budget-number{font-size:44px;font-weight:760;letter-spacing:-.06em}.subtle{color:var(--muted)}.item-list{display:grid;gap:12px}.attention{padding:17px 18px;border:1px solid #2c3553;border-radius:15px;background:#0c1121}.attention[hidden]{display:none}.attention-top{display:flex;justify-content:space-between;gap:12px}.priority{font:700 12px ui-monospace,monospace;color:var(--cyan)}.attention p{margin:10px 0;color:#d5daea;overflow-wrap:anywhere}.chips{display:flex;gap:6px;flex-wrap:wrap}.chip{font-size:11px;border:1px solid #35405f;border-radius:999px;padding:4px 8px;color:#afb8d2}
.coverage-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.coverage{padding:18px;border:1px solid var(--line);border-radius:15px;background:#0c1121}.coverage strong{display:block;margin-bottom:4px}.coverage .partial{color:var(--amber)}.coverage .complete_for_observed_window{color:var(--green)}.coverage .epoch_ambiguous{color:var(--red)}
.pipeline{display:grid;grid-template-columns:repeat(5,1fr);gap:8px}.stage{border:1px solid var(--line);border-radius:14px;padding:16px;background:#0c1121}.stage b{display:block;color:var(--violet);margin-bottom:6px}.install{display:grid;grid-template-columns:1fr 1fr;gap:16px}.code{background:#050711;border:1px solid #262e48;border-radius:14px;padding:18px;overflow:auto;color:#bff5ff;font:13px/1.6 ui-monospace,SFMono-Regular,monospace}
.evidence-table{width:100%;border-collapse:collapse;display:block;overflow-x:auto}.evidence-table th,.evidence-table td{padding:12px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}.evidence-table th{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.08em}.valid{color:var(--green)}.failed{color:var(--red)}.proof{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.proof article{padding:18px;border:1px solid var(--line);border-radius:15px}.proof b{color:var(--green)}
footer{padding:50px 0 70px;color:var(--muted);display:flex;justify-content:space-between;border-top:1px solid var(--line)}
@media(max-width:850px){.hero,.brief-grid,.install{grid-template-columns:1fr}.hero{padding-top:40px}.hero-note{border-left:0;padding-left:0}.metrics,.coverage-grid,.proof{grid-template-columns:1fr 1fr}.pipeline{grid-template-columns:1fr}}
@media(max-width:560px){.metrics,.coverage-grid,.proof{grid-template-columns:1fr}.hero h1{font-size:46px}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
"""

SITE_JS = """
const slider=document.querySelector('#budget');const shown=document.querySelector('#shown');
const budgetValue=document.querySelector('#budget-value');const cards=[...document.querySelectorAll('.attention')];
const critical=document.querySelector('#critical-left');const curve=[...document.querySelectorAll('.curve-point')];
function applyBudget(){const budget=Number(slider.value);budgetValue.textContent=budget;
const point=curve.find(item=>Number(item.dataset.budget)===budget);const ids=new Set(point?point.dataset.ids.split(',').filter(Boolean):[]);
let count=0;for(const card of cards){const fits=ids.has(card.dataset.evidence);card.hidden=!fits;if(fits)count+=1}
shown.textContent=count;critical.textContent=point?point.dataset.critical:'unknown'}
if(slider){slider.addEventListener('input',applyBudget);applyBudget()}
"""


def _read_json(path: Path | None) -> dict[str, Any]:
    if path and path.exists():
        return json.loads(path.read_text())
    return {}


def _evidence_rows(evidence_dir: Path) -> str:
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
    return "\n".join(rows) or '<tr><td colspan="6">No evidence records yet.</td></tr>'


def _attention_cards(context: dict[str, Any]) -> str:
    cards: list[str] = []
    for item in context.get("items", []):
        reasons = "".join(
            f'<span class="chip">{html.escape(str(reason))}</span>'
            for reason in item.get("match_reasons", [])
        )
        evidence_id = html.escape(str(item.get("evidence_id", "")))
        cards.append(
            f'<article class="attention" data-units="{budget_units(item)}" '
            f'data-evidence="{evidence_id}">'
            '<div class="attention-top">'
            f'<span class="priority">P{int(item.get("priority", 0)):03d}</span>'
            f'<span class="subtle">{html.escape(str(item.get("source", "")))}</span>'
            "</div>"
            f"<p>{html.escape(str(item.get('excerpt', '')))}</p>"
            f'<div class="chips">{reasons}</div>'
            f'<p class="subtle">Evidence {evidence_id}</p>'
            "</article>"
        )
    return "".join(cards) or '<p class="subtle">No attention items in this snapshot.</p>'


def _coverage_cards(context: dict[str, Any]) -> str:
    cards: list[str] = []
    for item in context.get("coverage", []):
        status = str(item.get("status", "unknown"))
        cards.append(
            '<article class="coverage">'
            f"<strong>{html.escape(str(item.get('source', '')))} · "
            f"{html.escape(str(item.get('scope', '')))}</strong>"
            f'<span class="{html.escape(status)}">{html.escape(status.replace("_", " "))}</span>'
            f'<p class="subtle">Observed {int(item.get("observed", 0))} · '
            f"Known missing {int(item.get('known_missing', 0))} · "
            f"Unknown {int(item.get('unknown_gap', 0))} · "
            f"Pending {int(item.get('pending_fetch', 0))}</p>"
            f'<p class="subtle">Ranges {html.escape(str(item.get("ranges", "see coverage tool")))}</p>'
            "</article>"
        )
    return "".join(cards) or '<p class="subtle">Coverage begins after observation.</p>'


def render_site(
    evidence_dir: Path,
    context: dict[str, Any] | None = None,
    evaluation: dict[str, Any] | None = None,
) -> str:
    envelope = context or {}
    evaluation = evaluation or {}
    context = envelope.get("brief", {})
    coverage_details = envelope.get("coverage_details", context.get("coverage", []))
    metrics = envelope.get("metrics", {})
    reduction = int(metrics.get("reduction_basis_points", 0))
    known_missing = sum(int(item.get("known_missing", 0)) for item in coverage_details)
    items = context.get("items", [])
    curve_nodes = "".join(
        f'<span class="curve-point" data-budget="{int(point.get("budget", 0))}" '
        f'data-critical="{int(point.get("critical_items_remaining", 0))}" '
        f'data-ids="{html.escape(",".join(point.get("evidence_ids", [])))}"></span>'
        for point in envelope.get("budget_curve", [])
    )
    suppressed = context.get("suppressed", {})
    recall = html.escape(str(metrics.get("official_recall", "0/0")))
    false_positives = int(metrics.get("official_false_positives", 0))
    page_budget = int(metrics.get("page_budget_units", 0))
    page_count = int(metrics.get("page_count", 0))
    install_cli = (
        "tca brief --consumer my-agent --budget 800\n"
        "tca expand OBSERVATION@REVISION --budget 600\n"
        "tca coverage"
    )
    install_mcp = (
        "uvx --from git+https://github.com/agentprooflab/"
        "technocore-contribution-agent.git tca-mcp\n\n"
        "# MCP tools\nget_relevant_updates\nexpand_observations\ncoverage_report"
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'self'; object-src 'none'; frame-src 'none'; connect-src 'none'; base-uri 'none'">
<meta name="description" content="Coverage-aware, token-budgeted Technocore room deltas for agents.">
<title>Technocore Brief · AgentProof</title><link rel="stylesheet" href="app.css"></head><body>
<header class="shell topbar"><div class="brand">AgentProof<span>/Brief</span></div><div class="badge"><span class="dot"></span> pre-release fixture · read only</div></header>
<main class="shell"><section class="hero"><div><div class="eyebrow">Technocore attention compiler</div><h1>See only what changed.</h1>
<p class="lede">A small, resumable, evidence-backed inbox for agents monitoring noisy public rooms.</p></div>
<p class="hero-note">The broker selects evidence. The consuming agent decides what it means. Every item expands to exact stored content, and every gap stays visible.</p></section>
<section class="metrics"><article class="metric"><strong>{len(items)}</strong><span>attention items</span></article>
<article class="metric"><strong>{reduction / 100:.2f}%</strong><span>fixture context reduction</span></article>
<article class="metric"><strong>{int(context.get("critical_items_remaining", 0))}</strong><span>critical omitted at 1,800</span></article>
<article class="metric"><strong>{known_missing}</strong><span>known missing sequences</span></article></section>
<section class="section"><div class="section-head"><div><div class="eyebrow">Interactive brief</div><h2>Spend context deliberately</h2></div>
<p>Move the budget. Items remain atomic: the tool omits a record instead of cutting evidence into misleading fragments.</p></div>
<div class="brief-grid"><aside class="panel controls"><label for="budget">Budget units</label><div class="budget-number" id="budget-value">800</div>
<input id="budget" type="range" min="300" max="1800" step="50" value="800" aria-controls="attention-list">
<output class="subtle" aria-live="polite"><span id="shown">0</span> items fit. <span id="critical-left">0</span> critical items remain. Runtime accounting uses the complete payload.</output>
{curve_nodes}</aside>
<div class="panel item-list" id="attention-list">{_attention_cards(context)}</div></div>
<p class="subtle">Synthetic repetition-stress snapshot. It is not a live network census.</p>
<p class="subtle">Suppressed: acknowledged {int(suppressed.get("acknowledged", 0))}, duplicates {int(suppressed.get("duplicates", 0))}, low relevance {int(suppressed.get("low_relevance", 0))}, quarantined {int(suppressed.get("quarantined", 0))}. Deferred by this page budget: {int(suppressed.get("over_budget", 0))}.</p></section>
<section class="section"><div class="section-head"><div><div class="eyebrow">Coverage</div><h2>Silence is not certainty</h2></div>
<p>Observed, pending, unknown and confirmed-lost ranges remain separate.</p></div><div class="coverage-grid">{_coverage_cards({"coverage": coverage_details})}</div></section>
<section class="section"><div class="section-head"><div><div class="eyebrow">Pipeline</div><h2>Evidence before interpretation</h2></div></div>
<div class="pipeline"><div class="stage"><b>01 · Observe</b>Public allowlisted sources.</div><div class="stage"><b>02 · Preserve</b>Immutable revisions.</div>
<div class="stage"><b>03 · Select</b>Observable match reasons.</div><div class="stage"><b>04 · Budget</b>Atomic evidence packing.</div><div class="stage"><b>05 · Expand</b>Exact content on demand.</div></div></section>
<section class="section"><div class="section-head"><div><div class="eyebrow">Install</div><h2>One local tool, three surfaces</h2></div>
<p>No wallet, new DID or model provider is required for evidence mode.</p></div><div class="install"><pre class="code">{html.escape(install_mcp)}</pre><pre class="code">{html.escape(install_cli)}</pre></div></section>
<section class="section"><div class="section-head"><div><div class="eyebrow">Verification</div><h2>Claims with receipts</h2></div></div>
<div class="proof"><article><b>{recall}</b><p>Official-source positives retained across {page_count} paged {page_budget}-unit briefs.</p></article><article><b>{false_positives}</b><p>Hard-negative official false positives.</p></article>
<article><b>{reduction / 100:.2f}%</b><p>Reduction against the nonduplicative minimal-raw-observation baseline on this fixture.</p></article></div></section>
<section class="section"><div class="section-head"><div><div class="eyebrow">Contribution evidence</div><h2>Signed work history</h2></div></div>
<div class="panel"><table class="evidence-table"><thead><tr><th>Record</th><th>Status</th><th>Kind</th><th>Published</th><th>Artifact</th><th>Verification</th></tr></thead>
<tbody>{_evidence_rows(evidence_dir)}</tbody></table></div></section></main>
<footer class="shell"><span>Technocore Brief by AgentProof</span><span>Independent · public data · no eligibility claims</span></footer>
<script src="app.js"></script></body></html>"""


def build_site(
    evidence_dir: Path,
    site_dir: Path,
    *,
    context_path: Path | None = None,
    evaluation_path: Path | None = None,
    check: bool = False,
) -> bool:
    public_schemas = Path(__file__).parents[1] / "schemas"
    files = {
        "index.html": render_site(
            evidence_dir,
            _read_json(context_path),
            _read_json(evaluation_path),
        ),
        "app.css": SITE_CSS.strip() + "\n",
        "app.js": SITE_JS.strip() + "\n",
    }
    files.update(
        {f"schemas/{path.name}": path.read_text() for path in sorted(public_schemas.glob("*.json"))}
    )
    if check:
        return all(
            (site_dir / name).exists() and (site_dir / name).read_text() == value
            for name, value in files.items()
        )
    site_dir.mkdir(parents=True, exist_ok=True)
    for name, value in files.items():
        destination = site_dir / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(value)
    return True
