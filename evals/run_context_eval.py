from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path

from tca.config import OfficialXAccount
from tca.context import ContextError, budget_units, build_brief, coverage_report, payload_digest
from tca.observer import _x_posts_to_observations
from tca.state import State, canonical_json

ROOT = Path(__file__).parents[1]
CORPUS_PATH = ROOT / "evals" / "official_corpus.json"
REPORT_PATH = ROOT / "reports" / "context-eval-latest.json"
DASHBOARD_PATH = ROOT / "reports" / "dashboard-context.json"


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_corpus() -> dict:
    return json.loads(CORPUS_PATH.read_text())


def populate(state: State, corpus: dict) -> tuple[list[str], list[str]]:
    positives: list[str] = []
    negatives: list[str] = []
    repeat = int(corpus["repeat_each_template"])
    deadlines = corpus["deadlines"]
    account = OfficialXAccount(username="flop_labs", user_id="2062193216074715136")
    case_number = 0
    for template in corpus["positive_templates"]:
        for repeat_index in range(repeat):
            case_number += 1
            case_id = f"positive-{case_number:03d}"
            positives.append(case_id)
            post = {
                "id": str(1000 + case_number),
                "authorId": account.user_id,
                "author": {"username": account.username},
                "text": template.format(
                    case=case_id,
                    deadline=deadlines[repeat_index % len(deadlines)],
                ),
                "createdAt": f"2026-08-27T00:{case_number:02d}:00+00:00",
                "fixture_id": case_id,
            }
            for item in _x_posts_to_observations(
                account,
                [post],
                observed_at=f"2026-08-27T01:{case_number:02d}:00+00:00",
            ):
                state.upsert_observation(item)
    for template in corpus["negative_templates"]:
        for repeat_index in range(repeat):
            case_number += 1
            negative_number = len(negatives) + 1
            case_id = f"negative-{negative_number:03d}"
            negatives.append(case_id)
            post = {
                "id": str(1000 + case_number),
                "authorId": f"spoof-{negative_number}",
                "author": {"username": account.username},
                "text": template.format(
                    case=case_id,
                    deadline=deadlines[repeat_index % len(deadlines)],
                ),
                "createdAt": f"2026-08-27T02:{negative_number:02d}:00+00:00",
                "fixture_id": case_id,
            }
            for item in _x_posts_to_observations(
                account,
                [post],
                observed_at=f"2026-08-27T03:{negative_number:02d}:00+00:00",
            ):
                state.upsert_observation(item)
    for number in range(int(corpus["noise_messages"])):
        text = (
            "Repeated community greeting with no actionable change."
            if number % 3
            else f"Ordinary room chatter sample {number % 7}."
        )
        state.upsert_observation(
            {
                "id": f"technocore:chat:0:{number + 1}",
                "source": "technocore",
                "external_id": f"chat:{number + 1}",
                "actor_id": f"did:key:z6MkNoise{number:04d}",
                "kind": "room_message",
                "title": text,
                "body": text,
                "authoritative": False,
                "created_at": f"2026-08-27T04:{number % 60:02d}:00+00:00",
                "observed_at": f"2026-08-27T05:{number % 60:02d}:00+00:00",
                "raw": {"fixture_id": f"noise-{number:03d}"},
            }
        )
    state.commit_observation_page(
        source="technocore",
        scope="chat",
        epoch=0,
        expected_cursor=None,
        observations=[],
        coverage_ranges=[
            (1, 180, "observed"),
            (181, 190, "confirmed_lost"),
            (191, int(corpus["noise_messages"]), "observed"),
        ],
        next_cursor=str(corpus["noise_messages"]),
    )
    return positives, negatives


def dashboard_budget_curve(state: State, as_of: str) -> list[dict]:
    curve: list[dict] = []
    for budget in range(300, 1801, 50):
        try:
            brief = build_brief(
                state,
                consumer_id="dashboard-demo",
                requested_budget=budget,
                as_of=as_of,
            )
        except ContextError as exc:
            curve.append(
                {
                    "budget": budget,
                    "evidence_ids": [],
                    "estimated_used": 0,
                    "critical_items_remaining": 30,
                    "error": exc.code,
                }
            )
            continue
        curve.append(
            {
                "budget": budget,
                "evidence_ids": [item["evidence_id"] for item in brief["items"]],
                "estimated_used": brief["budget"]["estimated_used"],
                "critical_items_remaining": brief["critical_items_remaining"],
                "error": None,
            }
        )
    return curve


def evaluate() -> tuple[dict, dict]:
    corpus = load_corpus()
    with tempfile.TemporaryDirectory(prefix="tca-context-eval-") as directory:
        state = State(Path(directory) / "state.db")
        positives, negatives = populate(state, corpus)
        observations = state.current_observations(public_only=True)
        raw_payload = [
            {
                "id": row["id"],
                "external_id": row["external_id"],
                "body": row["body"],
                "title": row["title"],
                "source": row["source"],
                "actor_id": row["actor_id"],
                "actor_username": row["actor_username"],
                "kind": row["kind"],
                "url": row["url"],
                "created_at": row["created_at"],
                "authoritative": bool(row["authoritative"]),
                "raw": json.loads(row["raw_json"]),
                "revision": row["revision_digest"],
            }
            for row in observations
        ]
        raw_units = budget_units(raw_payload)
        pages = []
        continuation = None
        while True:
            page = build_brief(
                state,
                consumer_id="eval-agent",
                requested_budget=800,
                as_of=corpus["fixed_as_of"],
                continuation=continuation,
            )
            pages.append(page)
            continuation = page["continuation_cursor"]
            if not continuation:
                break
            if len(pages) > 100:
                raise RuntimeError("brief pagination did not terminate")
        official_items = [
            item for page in pages for item in page["items"] if item["priority"] == 100
        ]
        returned_fixture_ids = set()
        for item in official_items:
            observation_id = item["event_id"]
            row = state.current_observation(observation_id)
            returned_fixture_ids.add(json.loads(row["raw_json"])["fixture_id"])
        false_negatives = sorted(set(positives) - returned_fixture_ids)
        false_positives = sorted(returned_fixture_ids.intersection(negatives))
        brief_units = sum(budget_units(page) for page in pages)
        consumer_reduction = 1 - (brief_units / raw_units)
        report = {
            "schema": "tca-context-eval/v1",
            "corpus_path": "evals/official_corpus.json",
            "corpus_sha256": file_sha256(CORPUS_PATH),
            "fixed_as_of": corpus["fixed_as_of"],
            "positive_cases": len(positives),
            "negative_cases": len(negatives),
            "false_negatives": false_negatives,
            "false_positives": false_positives,
            "raw_budget_units": raw_units,
            "brief_budget_units": brief_units,
            "brief_pages_at_800_units": len(pages),
            "consumer_context_reduction_basis_points": round(consumer_reduction * 10000),
            "brief_payload_sha256": payload_digest(pages),
            "claims": {
                "scope": "this digest-pinned synthetic and mutated fixture corpus only",
                "official_recall": f"{len(positives) - len(false_negatives)}/{len(positives)}",
                "official_false_positives": len(false_positives),
            },
        }
        dashboard = build_brief(
            state,
            consumer_id="dashboard-demo",
            requested_budget=1800,
            as_of=corpus["fixed_as_of"],
        )
        dashboard["coverage"] = coverage_report(state, include_ranges=True)
        dashboard["snapshot_kind"] = "synthetic_repetition_stress_fixture"
        dashboard["budget_curve"] = dashboard_budget_curve(state, corpus["fixed_as_of"])
        return report, dashboard


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--write", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    report, dashboard = evaluate()
    if args.write:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        DASHBOARD_PATH.write_text(json.dumps(dashboard, indent=2, sort_keys=True) + "\n")
    if args.verify:
        if not REPORT_PATH.exists():
            raise SystemExit("verification reports are missing; run with --write")
        if json.loads(REPORT_PATH.read_text()) != report:
            raise SystemExit("context evaluation report does not reproduce")
        if not DASHBOARD_PATH.exists() or json.loads(DASHBOARD_PATH.read_text()) != dashboard:
            raise SystemExit("dashboard context does not reproduce")
    print(canonical_json({"report": report}))
    if (
        report["false_negatives"]
        or report["false_positives"]
        or report["consumer_context_reduction_basis_points"] < 5000
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
