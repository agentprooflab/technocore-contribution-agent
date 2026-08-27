from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from tca.context import budget_units
from tca.evidence import load_record, verify_record

SITE_CSS = """
:root{color-scheme:dark;--black:#080907;--ink:#f2f0e7;--paper:#d9d6c8;--acid:#d7ff00;--orange:#ff5a1f;--dim:#8d9084;--line:#3b3d36;--line-hot:#707368;--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;--sans:Arial,Helvetica,sans-serif}
*{box-sizing:border-box}html{scroll-behavior:smooth;background:var(--black)}body{margin:0;background:var(--black);color:var(--ink);font:15px/1.45 var(--sans);overflow-x:hidden}body:before{content:"";position:fixed;inset:0;pointer-events:none;z-index:20;opacity:.09;background:repeating-linear-gradient(0deg,transparent 0 3px,#fff 4px);mix-blend-mode:overlay}a{color:inherit;text-decoration:none}.shell{width:min(1320px,calc(100% - 40px));margin:auto}.mono{font-family:var(--mono)}
.ticker{overflow:hidden;border-block:1px solid var(--line);background:var(--acid);color:var(--black);font:700 11px/30px var(--mono);letter-spacing:.12em;white-space:nowrap}.ticker-track{display:inline-flex;min-width:max-content;animation:ticker 24s linear infinite}.ticker-track span{padding-right:42px}.topbar{height:74px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--line)}.brand{font:800 18px var(--mono);letter-spacing:-.04em}.brand b{color:var(--acid)}.top-meta{display:flex;gap:24px;color:var(--dim);font:700 10px var(--mono);letter-spacing:.1em}.top-meta strong{color:var(--ink)}
.hero{position:relative;min-height:680px;border-bottom:1px solid var(--line);display:flex;align-items:flex-end;overflow:hidden}.hero-art{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;object-position:center;opacity:.78;filter:contrast(1.08) saturate(.92);animation:hero-drift 18s ease-in-out infinite alternate}.hero:after{content:"";position:absolute;inset:0;background:linear-gradient(90deg,#080907 0%,#080907eb 36%,#0809072b 72%,#080907 100%)}.hero-scan{position:absolute;z-index:2;inset:-30% 0 auto;height:25%;background:linear-gradient(180deg,transparent,#d7ff001f,transparent);animation:scan 7s linear infinite}.hero-copy{position:relative;z-index:3;width:min(900px,90%);padding:70px 0 76px}.kicker,.section-id{font:800 11px var(--mono);letter-spacing:.16em;color:var(--acid);text-transform:uppercase}.hero h1{font-size:clamp(58px,10vw,142px);line-height:.78;letter-spacing:-.085em;margin:18px 0 30px;text-transform:uppercase}.hero h1 em{font-style:normal;color:var(--acid)}.lede{max-width:650px;font-size:clamp(18px,2.2vw,28px);line-height:1.2;margin:0;color:var(--paper)}.hero-actions{display:flex;gap:10px;margin-top:34px;flex-wrap:wrap}.button{display:inline-flex;align-items:center;min-height:44px;padding:0 16px;border:1px solid var(--ink);font:800 11px var(--mono);letter-spacing:.08em;text-transform:uppercase}.button.primary{background:var(--acid);border-color:var(--acid);color:var(--black)}.button:hover{background:var(--ink);color:var(--black)}.hero-stamp{position:absolute;z-index:4;right:3%;bottom:46px;width:150px;height:150px;border:1px solid var(--acid);border-radius:50%;display:grid;place-items:center;text-align:center;color:var(--acid);font:800 11px/1.35 var(--mono);letter-spacing:.12em;transform:rotate(10deg);animation:stamp 12s linear infinite}
.metrics{display:grid;grid-template-columns:repeat(4,1fr);border-bottom:1px solid var(--line)}.metric{padding:28px 22px 30px;border-right:1px solid var(--line);min-height:160px}.metric:last-child{border-right:0}.metric strong{display:block;font:800 clamp(38px,5vw,72px)/.9 var(--mono);letter-spacing:-.08em}.metric strong.acid{color:var(--acid)}.metric span{display:block;margin-top:18px;color:var(--dim);font:700 10px var(--mono);letter-spacing:.12em;text-transform:uppercase}.metric small{display:block;color:var(--paper);margin-top:6px}
.section{padding:96px 0;border-bottom:1px solid var(--line)}.section-head{display:grid;grid-template-columns:130px 1fr minmax(260px,440px);gap:24px;align-items:start;margin-bottom:46px}.section h2{font-size:clamp(38px,6vw,82px);line-height:.88;letter-spacing:-.065em;text-transform:uppercase;margin:0}.section-head p{color:var(--paper);font-size:17px;margin:0}.section-id{color:var(--orange)}
.thesis{display:grid;grid-template-columns:1.25fr .75fr;border:1px solid var(--line)}.thesis-main{padding:42px;border-right:1px solid var(--line)}.thesis-main p{font-size:clamp(26px,4vw,58px);line-height:1.02;letter-spacing:-.045em;margin:0}.thesis-main mark{background:var(--acid);color:var(--black);padding:0 .08em}.thesis-side{display:grid}.thesis-step{padding:24px;border-bottom:1px solid var(--line)}.thesis-step:last-child{border-bottom:0}.thesis-step b{display:block;color:var(--acid);font:800 12px var(--mono);margin-bottom:8px}.thesis-step p{margin:0;color:var(--paper)}.not-oracle{margin-top:14px;padding:14px 18px;border:1px solid var(--orange);color:var(--orange);font:800 11px var(--mono);letter-spacing:.08em;text-transform:uppercase}
.transform-grid{display:grid;grid-template-columns:1fr 1.05fr 1fr;border:1px solid var(--line)}.transform-panel{position:relative;min-width:0;padding:26px;border-right:1px solid var(--line)}.transform-panel:last-child{border-right:0}.transform-label{display:block;margin-bottom:22px;color:var(--orange);font:800 10px var(--mono);letter-spacing:.12em}.raw-row{padding:13px 0;border-top:1px solid var(--line);font:11px/1.5 var(--mono)}.raw-row b{display:block;margin-bottom:5px;color:var(--acid)}.raw-row span{color:var(--paper)}.rule-list{margin:0;padding:0;list-style:none;counter-reset:rules}.rule-list li{position:relative;padding:11px 0 11px 34px;border-top:1px solid var(--line);color:var(--paper)}.rule-list li:before{counter-increment:rules;content:counter(rules,decimal-leading-zero);position:absolute;left:0;color:var(--acid);font:800 10px var(--mono)}.compiled-card{border:1px solid var(--line-hot);padding:20px;background:#11130e}.compiled-card .excerpt{font-size:18px;line-height:1.25;margin:16px 0}.compiled-card code{display:block;color:var(--dim);font:10px/1.5 var(--mono);overflow-wrap:anywhere}.flow-arrow{display:block;margin:20px 0;color:var(--acid);font:900 28px var(--mono)}.ranking{margin-top:18px;border:1px solid var(--line)}.ranking-head{padding:15px 18px;border-bottom:1px solid var(--line);font:800 10px var(--mono);letter-spacing:.1em}.rank-row{display:grid;grid-template-columns:90px 1fr 1fr;gap:18px;padding:13px 18px;border-bottom:1px solid var(--line);align-items:center}.rank-row:last-child{border-bottom:0}.rank-score{color:var(--acid);font:900 18px var(--mono)}.rank-rule{font-weight:700}.rank-proof{color:var(--dim);font:11px/1.4 var(--mono)}.trust-grid{display:grid;grid-template-columns:repeat(4,1fr);margin-top:18px;border:1px solid var(--line)}.trust-grid article{padding:19px;border-right:1px solid var(--line)}.trust-grid article:last-child{border-right:0}.trust-grid b{display:block;margin-bottom:9px;color:var(--orange);font:800 10px var(--mono);letter-spacing:.08em}.trust-grid p{margin:0;color:var(--paper);font-size:13px}.contract-link{display:flex;align-items:center;gap:18px;margin-top:18px;color:var(--dim);font:11px var(--mono)}
.demo-guide{display:grid;grid-template-columns:repeat(3,1fr);border:1px solid var(--line);border-bottom:0}.demo-guide article{padding:20px;border-right:1px solid var(--line)}.demo-guide article:last-child{border-right:0}.demo-guide b{display:block;color:var(--acid);font:800 11px var(--mono);letter-spacing:.1em}.demo-guide p{margin:10px 0 0;color:var(--paper)}.brief-grid{display:grid;grid-template-columns:380px 1fr;border:1px solid var(--line)}.controls{background:var(--acid);color:var(--black);padding:30px;min-height:500px}.controls label{display:block;font:800 11px var(--mono);letter-spacing:.12em;text-transform:uppercase}.budget-number{font:900 clamp(72px,8vw,116px)/.82 var(--mono);letter-spacing:-.1em;margin:26px 0 8px}.budget-unit{font:800 11px var(--mono);letter-spacing:.12em}.budget-explain{margin:14px 0 0;max-width:280px;font:600 12px/1.45 var(--mono)}.controls input{width:100%;margin:42px 0 22px;accent-color:var(--black)}.controls output{display:grid;grid-template-columns:1fr 1fr;gap:14px;border-top:1px solid #08090755;padding-top:20px;font:700 11px/1.35 var(--mono)}.controls output span{display:block;margin-bottom:7px;font-size:9px;letter-spacing:.1em}.controls output strong{display:block;font-size:31px}.budget-bar{height:4px;background:#08090733;margin-top:24px}.budget-bar i{display:block;width:var(--budget-progress,33.33%);height:100%;background:var(--black);transition:width .25s ease}.curve-point{display:none}.item-wrap{padding:0}.list-head{min-height:62px;display:flex;justify-content:space-between;align-items:center;padding:0 22px;border-bottom:1px solid var(--line);font:800 10px var(--mono);letter-spacing:.12em;color:var(--dim)}.demo-legend{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;padding:15px 22px;border-bottom:1px solid var(--line);color:var(--dim);font:10px/1.4 var(--mono)}.demo-legend b{color:var(--ink)}.item-list{display:grid}.attention{padding:22px;border-bottom:1px solid var(--line);transition:background .2s ease,opacity .2s ease}.attention:last-child{border-bottom:0}.attention:hover{background:#14160f}.attention[hidden]{display:none}.attention-top{display:flex;justify-content:space-between;gap:12px}.priority{font:900 12px var(--mono);color:var(--acid)}.source{font:800 10px var(--mono);letter-spacing:.1em;color:var(--dim);text-transform:uppercase}.attention .excerpt{font-size:20px;line-height:1.2;margin:16px 0;color:var(--ink);overflow-wrap:anywhere}.chips{display:flex;gap:8px;flex-wrap:wrap}.chip{font:700 10px var(--mono);border-left:2px solid var(--orange);padding-left:7px;color:var(--paper)}.evidence-id{display:block;margin-top:17px;color:var(--dim);font:10px/1.5 var(--mono);overflow-wrap:anywhere}.demo-next{padding:17px 22px;border-top:1px solid var(--line);color:var(--paper);font:11px/1.5 var(--mono)}.demo-next b{color:var(--orange)}.fixture-note{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:16px;color:var(--dim);font:11px/1.5 var(--mono)}
.coverage-grid{border-top:1px solid var(--line)}.coverage{display:grid;grid-template-columns:minmax(190px,1fr) 190px 1.3fr;gap:18px;align-items:center;padding:20px 0;border-bottom:1px solid var(--line)}.coverage strong{font:800 13px var(--mono)}.coverage-status{font:800 10px var(--mono);letter-spacing:.08em;text-transform:uppercase}.coverage-status.partial{color:#ffc14a}.coverage-status.complete_for_observed_window{color:var(--acid)}.coverage-status.epoch_ambiguous{color:var(--orange)}.coverage-data{color:var(--dim);font:11px/1.5 var(--mono)}
.pipeline{display:grid;grid-template-columns:repeat(5,1fr);border:1px solid var(--line)}.stage{padding:24px 20px;min-height:170px;border-right:1px solid var(--line)}.stage:last-child{border-right:0}.stage b{display:block;color:var(--orange);font:800 11px var(--mono);margin-bottom:46px}.stage strong{display:block;font-size:18px;text-transform:uppercase}.stage p{color:var(--dim);margin:8px 0 0}.install{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--line);border:1px solid var(--line)}.code-block{background:var(--black);padding:24px}.code-label{color:var(--acid);font:800 10px var(--mono);letter-spacing:.1em}.code{margin:18px 0 0;overflow:auto;color:var(--paper);font:12px/1.7 var(--mono);white-space:pre-wrap}
.proof{display:grid;grid-template-columns:repeat(3,1fr);border:1px solid var(--line)}.proof article{padding:30px;border-right:1px solid var(--line);min-height:210px}.proof article:last-child{border-right:0}.proof b{display:block;color:var(--acid);font:900 clamp(40px,6vw,76px)/.9 var(--mono);letter-spacing:-.08em}.proof p{margin-top:26px;color:var(--paper)}.proof small{color:var(--dim);font-family:var(--mono)}
.table-wrap{border:1px solid var(--line);overflow-x:auto}.evidence-table{width:100%;border-collapse:collapse;min-width:780px}.evidence-table th,.evidence-table td{padding:15px 14px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top;font-family:var(--mono)}.evidence-table th{color:var(--dim);font-size:10px;text-transform:uppercase;letter-spacing:.08em}.valid{color:var(--acid)}.failed{color:var(--orange)}footer{padding:36px 0 60px;color:var(--dim);display:flex;justify-content:space-between;font:11px var(--mono);letter-spacing:.06em}.reveal{opacity:0;transform:translateY(24px);transition:opacity .7s ease,transform .7s ease}.reveal.in{opacity:1;transform:none}
@keyframes ticker{to{transform:translateX(-50%)}}@keyframes hero-drift{from{transform:scale(1.02) translateX(0)}to{transform:scale(1.08) translateX(-1.5%)}}@keyframes scan{to{transform:translateY(520%)}}@keyframes stamp{to{transform:rotate(370deg)}}
@media(max-width:900px){.top-meta span:nth-child(-n+2){display:none}.hero{min-height:620px}.hero-stamp{display:none}.metrics{grid-template-columns:1fr 1fr}.metric:nth-child(2){border-right:0}.section-head{grid-template-columns:90px 1fr}.section-head p{grid-column:2}.thesis,.brief-grid,.transform-grid{grid-template-columns:1fr}.thesis-main,.transform-panel{border-right:0;border-bottom:1px solid var(--line)}.controls{min-height:auto}.demo-guide,.trust-grid{grid-template-columns:1fr 1fr}.demo-guide article,.trust-grid article{border-bottom:1px solid var(--line)}.coverage{grid-template-columns:1fr 150px}.coverage-data{grid-column:1/-1}.pipeline{grid-template-columns:1fr 1fr}.stage{border-bottom:1px solid var(--line)}.install{grid-template-columns:1fr}.proof{grid-template-columns:1fr}.proof article{border-right:0;border-bottom:1px solid var(--line)}}
@media(max-width:560px){.shell{width:min(100% - 24px,1320px)}.topbar{height:60px}.hero{min-height:560px}.hero-copy{padding:54px 0}.hero h1{font-size:58px}.metrics{grid-template-columns:1fr}.metric{border-right:0;border-bottom:1px solid var(--line)}.section{padding:68px 0}.section-head{display:block}.section-id{display:block;margin-bottom:18px}.section-head p{margin-top:20px}.thesis-main{padding:24px}.fixture-note,.demo-legend,.demo-guide,.trust-grid{grid-template-columns:1fr}.rank-row{grid-template-columns:60px 1fr}.rank-proof{grid-column:2}.controls output{grid-template-columns:1fr}.coverage{grid-template-columns:1fr}.pipeline{grid-template-columns:1fr}.stage{border-right:0}.top-meta{gap:0}.button{width:100%;justify-content:center}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}.ticker-track,.hero-art,.hero-scan,.hero-stamp{animation:none}.reveal{opacity:1;transform:none;transition:none}.budget-bar i{transition:none}}
"""

SITE_JS = """
const slider=document.querySelector('#budget');const shown=document.querySelector('#shown');
const budgetValue=document.querySelector('#budget-value');const cards=[...document.querySelectorAll('.attention')];
const critical=document.querySelector('#critical-left');const curve=[...document.querySelectorAll('.curve-point')];
const budgetPanel=document.querySelector('.controls');const pageBudget=document.querySelector('#page-budget');
function applyBudget(){if(!slider)return;const budget=Number(slider.value);budgetValue.textContent=budget;
const point=curve.find(item=>Number(item.dataset.budget)===budget);const ids=new Set(point?point.dataset.ids.split(',').filter(Boolean):[]);
let count=0;for(const card of cards){const fits=ids.has(card.dataset.evidence);card.hidden=!fits;if(fits)count+=1}
shown.textContent=count;critical.textContent=point?point.dataset.critical:'—';
if(pageBudget)pageBudget.textContent=`${budget.toLocaleString()} UNIT PAGE`;
budgetPanel?.style.setProperty('--budget-progress',`${((budget-300)/1500)*100}%`)}
if(slider){slider.addEventListener('input',applyBudget);applyBudget()}
const reduced=window.matchMedia('(prefers-reduced-motion: reduce)').matches;
if(!reduced&&'IntersectionObserver'in window){const observer=new IntersectionObserver(entries=>{for(const entry of entries){if(entry.isIntersecting){entry.target.classList.add('in');observer.unobserve(entry.target)}}},{threshold:.12});for(const item of document.querySelectorAll('.reveal'))observer.observe(item)}else{for(const item of document.querySelectorAll('.reveal'))item.classList.add('in')}
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
            f'<article class="attention reveal" data-units="{budget_units(item)}" '
            f'data-evidence="{evidence_id}">'
            '<div class="attention-top">'
            f'<span class="priority">PRIORITY / {int(item.get("priority", 0)):03d}</span>'
            f'<span class="source">SOURCE / {html.escape(str(item.get("source", "")))}</span>'
            "</div>"
            f'<p class="excerpt">{html.escape(str(item.get("excerpt", "")))}</p>'
            f'<div class="chips">{reasons}</div>'
            f'<code class="evidence-id">PROOF / {evidence_id}</code>'
            "</article>"
        )
    return "".join(cards) or '<p class="evidence-id">NO SIGNALS IN THIS SNAPSHOT.</p>'


def _coverage_cards(context: dict[str, Any]) -> str:
    cards: list[str] = []
    for item in context.get("coverage", []):
        status = str(item.get("status", "unknown"))
        cards.append(
            '<article class="coverage reveal">'
            f"<strong>{html.escape(str(item.get('source', '')))} · "
            f"{html.escape(str(item.get('scope', '')))}</strong>"
            f'<span class="coverage-status {html.escape(status)}">{html.escape(status.replace("_", " "))}</span>'
            f'<div class="coverage-data">OBSERVED {int(item.get("observed", 0))} / '
            f"Known missing {int(item.get('known_missing', 0))} · "
            f"Unknown {int(item.get('unknown_gap', 0))} · "
            f"Pending {int(item.get('pending_fetch', 0))}<br>"
            f"RANGES {html.escape(str(item.get('ranges', 'see coverage tool')))}</div>"
            "</article>"
        )
    return "".join(cards) or '<p class="evidence-id">COVERAGE BEGINS AFTER OBSERVATION.</p>'


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
    first_item = items[0] if items else {}
    first_excerpt = html.escape(str(first_item.get("excerpt", "No compiled signal.")))
    first_evidence = html.escape(str(first_item.get("evidence_id", "no-evidence-id")))
    first_reason = html.escape(str((first_item.get("match_reasons") or ["no_match_reason"])[0]))
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
<meta http-equiv="Content-Security-Policy" content="default-src 'self'; img-src 'self'; style-src 'self'; script-src 'self'; object-src 'none'; frame-src 'none'; connect-src 'none'; base-uri 'none'">
<meta name="description" content="Technocore signal without the context burn: ranked, resumable, evidence-backed updates for agents.">
<title>Technocore Brief // Signal, not noise</title><link rel="stylesheet" href="app.css"></head><body>
<div class="ticker" aria-label="Product status"><div class="ticker-track">
<span>READ-ONLY // NO WALLET // NO AIRDROP CLAIM // EVERY SIGNAL LINKS TO PROOF //</span><span>READ-ONLY // NO WALLET // NO AIRDROP CLAIM // EVERY SIGNAL LINKS TO PROOF //</span>
</div></div>
<header class="shell topbar"><div class="brand">AGENTPROOF<b>//BRIEF</b></div><div class="top-meta"><span>BUILD <strong>0.2</strong></span><span>MODE <strong>EVIDENCE</strong></span><span>STATUS <strong>PRE-RELEASE</strong></span></div></header>
<main class="shell">
<section class="hero"><img class="hero-art" src="assets/signal-noise-hero-v1.jpg" alt="" role="presentation"><div class="hero-scan"></div>
<div class="hero-copy"><div class="kicker">Technocore intelligence // built for agents</div><h1>Stop reading<br><em>everything.</em></h1>
<p class="lede">Your agent does not need the whole firehose. It needs the next actionable fact, why it matters, and the receipt.</p>
<div class="hero-actions"><a class="button primary" href="#brief">Run the brief ↓</a><a class="button" href="#proof">Check the receipts ↗</a></div></div>
<div class="hero-stamp" aria-hidden="true">SIGNAL<br>OVER<br>NOISE</div></section>

<section class="metrics" aria-label="Verified fixture metrics">
<article class="metric reveal"><strong class="acid">{recall}</strong><span>Official tasks caught</span><small>Across {page_count} bounded pages</small></article>
<article class="metric reveal"><strong>{false_positives}</strong><span>False official signals</span><small>Against mutated account envelopes</small></article>
<article class="metric reveal"><strong>{reduction / 100:.2f}%</strong><span>Less context burned</span><small>On the pinned fixture baseline</small></article>
<article class="metric reveal"><strong>{known_missing}</strong><span>Known missing sequences</span><small>Gaps stay visible</small></article></section>

<section class="section" id="what"><div class="section-head reveal"><span class="section-id">01 / THE BET</span><h2>Read less.<br>Miss nothing critical.</h2><p>Technocore Brief converts noisy public activity into a small evidence queue an agent can resume, budget, and verify.</p></div>
<div class="thesis reveal"><div class="thesis-main"><p>Raw chat burns context. Rigid summaries lose nuance. The brief returns <mark>ranked evidence</mark> and lets the agent expand only what matters.</p></div>
<div class="thesis-side"><div class="thesis-step"><b>01 // WATCH</b><p>Observe allowlisted public sources and preserve exact revisions.</p></div><div class="thesis-step"><b>02 // CUT</b><p>Drop duplicates, acknowledged work, spoofed authority, and low-value chatter.</p></div><div class="thesis-step"><b>03 // PROVE</b><p>Return a stable evidence ID, coverage, and exact expansion path.</p></div></div></div>
<div class="not-oracle reveal">Not an indexer. Not a reputation score. Not an eligibility oracle. No vibes dressed up as certainty.</div></section>

<section class="section" id="method"><div class="section-head reveal"><span class="section-id">02 / PROOF CHAIN</span><h2>From raw chat<br>to ranked evidence.</h2><p>This is a deterministic compiler, not an AI summary. Every output field comes from stored source data or a published rule you can inspect.</p></div>
<div class="transform-grid reveal"><article class="transform-panel"><span class="transform-label">A / RAW ENVELOPES</span><div class="raw-row"><b>X POST</b><span>authorId + username + text + timestamp + URL</span></div><div class="raw-row"><b>GITHUB ISSUE</b><span>repo + author association + state + title + body</span></div><div class="raw-row"><b>TECHNOCORE MESSAGE</b><span>room + sequence + DID + nonce + body</span></div><span class="flow-arrow">↓</span><p>Adapters normalize these different shapes into one observation contract. Raw source text remains untrusted data.</p></article>
<article class="transform-panel"><span class="transform-label">B / DETERMINISTIC RULES</span><ol class="rule-list"><li>Verify source identity and public/private exposure.</li><li>Hash material fields into an immutable revision; edits create a new revision.</li><li>Remove quarantined, acknowledged, irrelevant, and exact-duplicate records.</li><li>Assign priority and match reasons from the published ladder below.</li><li>Sort by priority, time, source, and evidence ID.</li><li>Pack complete records into the requested budget; never cut one mid-claim.</li></ol></article>
<article class="transform-panel"><span class="transform-label">C / COMPILED OUTPUT</span><div class="compiled-card"><div class="attention-top"><span class="priority">PRIORITY / {int(first_item.get("priority", 0)):03d}</span><span class="source">REASON / {first_reason}</span></div><p class="excerpt">{first_excerpt}</p><code>PROOF / {first_evidence}</code></div><span class="flow-arrow">↓</span><p>The evidence ID resolves to the exact stored revision. Coverage and cursors say what was observed, what may be missing, and where the next page starts.</p></article></div>
<div class="ranking reveal"><div class="ranking-head">THE PRIORITY LADDER // HIGHEST APPLICABLE RULE WINS</div><div class="rank-row"><span class="rank-score">P100</span><span class="rank-rule">Verified official X task</span><span class="rank-proof">Numeric account ID + username must both match the allowlist.</span></div><div class="rank-row"><span class="rank-score">P090</span><span class="rank-rule">Exact consumer mention</span><span class="rank-proof">Raises urgency, not authority. Untrusted content stays withheld.</span></div><div class="rank-row"><span class="rank-score">P075</span><span class="rank-rule">Maintainer GitHub issue</span><span class="rank-proof">Server association OWNER / MEMBER / COLLABORATOR or configured maintainer.</span></div><div class="rank-row"><span class="rank-score">P070</span><span class="rank-rule">Verified official X announcement</span><span class="rank-proof">Same two-part identity check; not classified as a task.</span></div><div class="rank-row"><span class="rank-score">P050</span><span class="rank-rule">Technical question observed</span><span class="rank-proof">Question kind only; body remains untrusted until explicit expansion.</span></div><div class="rank-row"><span class="rank-score">P040</span><span class="rank-rule">Matches configured interest</span><span class="rank-proof">Literal bounded interest match, recorded in match_reasons.</span></div><div class="rank-row"><span class="rank-score">P010</span><span class="rank-rule">Other new public room delta</span><span class="rank-proof">Lowest-priority awareness; filtered when a focused profile is active.</span></div></div>
<div class="trust-grid reveal"><article><b>PRIORITY ≠ TRUTH</b><p>The score answers “look at this first,” never “believe this.”</p></article><article><b>NO LLM IN THE EVIDENCE PATH</b><p>Provenance, hashing, filtering, ranking, and budget packing are deterministic.</p></article><article><b>CHAT IS NEVER AUTHORITY</b><p>Technocore bodies are withheld automatically and require explicit expansion.</p></article><article><b>VERIFY IT YOURSELF</b><p>Expand the proof ID for exact content; inspect coverage before trusting silence.</p></article></div><div class="contract-link reveal"><a class="button" href="context-broker.md">READ THE FULL CONTRACT ↗</a><span>Canonical encoding, identity, coverage, budget, and claim scope.</span></div></section>

<section class="section" id="brief"><div class="section-head reveal"><span class="section-id">03 / LIVE DEMO</span><h2>Turn the<br>firehose down.</h2><p>This simulates one agent request. Set the maximum response size, then watch the compiler choose which complete signals fit now and which P100 signals continue to the next page.</p></div>
<div class="demo-guide reveal"><article><b>01 / SET THE CAP</b><p>Pick how much context this single response may consume. This fixture fits {len(items)} signals at the full 1,800-unit cap.</p></article><article><b>02 / SEE WHAT FITS</b><p>Only complete evidence objects appear. Nothing is sliced mid-claim.</p></article><article><b>03 / KEEP GOING</b><p>Critical signals that do not fit remain counted and resumable on the next page.</p></article></div>
<div class="brief-grid reveal"><aside class="controls"><label for="budget">1 / Maximum response size</label><div class="budget-number" id="budget-value">800</div><div class="budget-unit">MODEL-NEUTRAL BUDGET UNITS</div><p class="budget-explain">One unit is roughly three bytes of canonical JSON. It is a deterministic size cap—not a promise about model tokens.</p>
<input id="budget" type="range" min="300" max="1800" step="50" value="800" aria-controls="attention-list"><div class="budget-bar"><i></i></div>
<output aria-live="polite"><div><span>THIS RESPONSE</span><strong id="shown">0</strong> COMPLETE SIGNALS</div><div><span>NEXT PAGE</span><strong id="critical-left">0</strong> P100 SIGNALS WAIT</div></output>{curve_nodes}</aside>
<div class="item-wrap"><div class="list-head"><span>2 / WHAT FITS RIGHT NOW</span><span id="page-budget">800 UNIT PAGE</span></div><div class="demo-legend"><span><b>PRIORITY 100</b><br>Authenticated official task</span><span><b>MATCH REASON</b><br>Why the compiler selected it</span><span><b>PROOF ID</b><br>Stable handle for exact expansion</span></div><div class="item-list" id="attention-list">{_attention_cards(context)}</div><div class="demo-next"><b>3 / CONTINUE, DON'T RE-SCAN.</b> The response returns a continuation cursor. The next call resumes this same snapshot until every critical signal has been delivered.</div></div></div>
<div class="fixture-note"><span>CURATED SYNTHETIC CONTRIBUTION SCENARIOS // NOT LIVE TASKS</span><span>SUPPRESSED / ACK {int(suppressed.get("acknowledged", 0))} · DUPES {int(suppressed.get("duplicates", 0))} · LOW {int(suppressed.get("low_relevance", 0))} · QUARANTINED {int(suppressed.get("quarantined", 0))} · DEFERRED {int(suppressed.get("over_budget", 0))}</span></div></section>

<section class="section"><div class="section-head reveal"><span class="section-id">04 / COVERAGE</span><h2>Silence can<br>wreck a thesis.</h2><p>An empty result means nothing without collection coverage. Observed, pending, unknown, and confirmed-lost ranges stay separate.</p></div>
<div class="coverage-grid">{_coverage_cards({"coverage": coverage_details})}</div></section>

<section class="section"><div class="section-head reveal"><span class="section-id">05 / OPERATIONS</span><h2>Evidence<br>before vibes.</h2><p>The same proof chain runs on every page. Interpretation stays with the consuming agent.</p></div>
<div class="pipeline reveal"><div class="stage"><b>01</b><strong>Observe</strong><p>Allowlisted public sources.</p></div><div class="stage"><b>02</b><strong>Preserve</strong><p>Immutable material revisions.</p></div><div class="stage"><b>03</b><strong>Select</strong><p>Observable match reasons.</p></div><div class="stage"><b>04</b><strong>Budget</strong><p>Atomic evidence packing.</p></div><div class="stage"><b>05</b><strong>Expand</strong><p>Exact content on demand.</p></div></div></section>

<section class="section"><div class="section-head reveal"><span class="section-id">06 / RUN IT</span><h2>Plug in.<br>Keep your keys.</h2><p>No wallet, fresh DID, or model provider is required for evidence mode. The MCP surface is read-only.</p></div>
<div class="install reveal"><div class="code-block"><div class="code-label">AGENT / MCP</div><pre class="code">{html.escape(install_mcp)}</pre></div><div class="code-block"><div class="code-label">OPERATOR / CLI</div><pre class="code">{html.escape(install_cli)}</pre></div></div></section>

<section class="section" id="proof"><div class="section-head reveal"><span class="section-id">07 / RECEIPTS</span><h2>Claims, with<br>the maths attached.</h2><p>Every number below is scoped to the digest-pinned synthetic corpus. No population-wide reliability or reward claim.</p></div>
<div class="proof reveal"><article><b>{recall}</b><p>Official-source positives retained.</p><small>{page_count} pages × {page_budget} units</small></article><article><b>{false_positives}</b><p>Hard-negative official false positives.</p><small>Identity requires account ID + handle</small></article><article><b>{reduction / 100:.2f}%</b><p>Context reduction versus a nonduplicative raw baseline.</p><small>Fixture-specific, not literal model tokens</small></article></div></section>

<section class="section"><div class="section-head reveal"><span class="section-id">08 / LEDGER</span><h2>Work history<br>that verifies.</h2><p>Failed or unverifiable evidence stays visible. Nothing gets silently deleted to make the chart look clean.</p></div>
<div class="table-wrap reveal"><table class="evidence-table"><thead><tr><th>Record</th><th>Status</th><th>Kind</th><th>Published</th><th>Artifact</th><th>Verification</th></tr></thead><tbody>{_evidence_rows(evidence_dir)}</tbody></table></div></section>
</main>
<footer class="shell"><span>AGENTPROOF // TECHNOCORE BRIEF</span><span>INDEPENDENT · PUBLIC DATA · ZERO ELIGIBILITY CLAIMS</span></footer>
<script src="app.js"></script></body></html>"""


def build_site(
    evidence_dir: Path,
    site_dir: Path,
    *,
    context_path: Path | None = None,
    evaluation_path: Path | None = None,
    check: bool = False,
) -> bool:
    project_root = Path(__file__).parents[1]
    public_schemas = project_root / "schemas"
    hero_asset = project_root / "assets" / "signal-noise-hero-v1.jpg"
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
    binary_files = {"assets/signal-noise-hero-v1.jpg": hero_asset.read_bytes()}
    if check:
        text_ok = all(
            (site_dir / name).exists() and (site_dir / name).read_text() == value
            for name, value in files.items()
        )
        binary_ok = all(
            (site_dir / name).exists() and (site_dir / name).read_bytes() == value
            for name, value in binary_files.items()
        )
        return text_ok and binary_ok
    site_dir.mkdir(parents=True, exist_ok=True)
    for name, value in files.items():
        destination = site_dir / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(value)
    for name, value in binary_files.items():
        destination = site_dir / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(value)
    return True
