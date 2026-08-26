from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path

from tca.context import budget_units, build_brief, payload_digest
from tca.state import State, canonical_json

ROOT = Path(__file__).parents[1]
CORPUS_PATH = ROOT / "evals" / "official_corpus.json"
REPORT_PATH = ROOT / "reports" / "context-eval-latest.json"
DASHBOARD_PATH = ROOT / "reports" / "dashboard-context.json"
SLICE_PATH = ROOT / "verification" / "slice-1.json"


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_corpus() -> dict:
    return json.loads(CORPUS_PATH.read_text())


def populate(state: State, corpus: dict) -> tuple[list[str], list[str]]:
    positives: list[str] = []
    negatives: list[str] = []
    repeat = int(corpus["repeat_each_template"])
    deadlines = corpus["deadlines"]
    case_number = 0
    for template in corpus["positive_templates"]:
        for repeat_index in range(repeat):
            case_number += 1
            case_id = f"positive-{case_number:03d}"
            positives.append(case_id)
            state.upsert_observation(
                {
                    "id": f"x:{1000 + case_number}",
                    "source": "x",
                    "external_id": str(1000 + case_number),
                    "actor_id": "2062193216074715136",
                    "actor_username": "flop_labs",
                    "kind": "official_task",
                    "title": "Official task",
                    "body": template.format(
                        case=case_id,
                        deadline=deadlines[repeat_index % len(deadlines)],
                    ),
                    "authoritative": True,
                    "created_at": f"2026-08-27T00:{case_number:02d}:00+00:00",
                    "observed_at": f"2026-08-27T01:{case_number:02d}:00+00:00",
                    "raw": {"fixture_id": case_id},
                }
            )
    for template in corpus["negative_templates"]:
        for repeat_index in range(repeat):
            case_number += 1
            negative_number = len(negatives) + 1
            case_id = f"negative-{negative_number:03d}"
            negatives.append(case_id)
            state.upsert_observation(
                {
                    "id": f"x:{1000 + case_number}",
                    "source": "x",
                    "external_id": str(1000 + case_number),
                    "actor_id": f"spoof-{negative_number}",
                    "actor_username": "flop_labs",
                    "kind": "announcement",
                    "title": "Community post",
                    "body": template.format(
                        case=case_id,
                        deadline=deadlines[repeat_index % len(deadlines)],
                    ),
                    "authoritative": False,
                    "created_at": f"2026-08-27T02:{negative_number:02d}:00+00:00",
                    "observed_at": f"2026-08-27T03:{negative_number:02d}:00+00:00",
                    "raw": {"fixture_id": case_id},
                }
            )
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


def evaluate() -> tuple[dict, dict, dict]:
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
        brief = build_brief(
            state,
            consumer_id="eval-agent",
            requested_budget=max(raw_units, 50000),
            as_of=corpus["fixed_as_of"],
        )
        official_items = [item for item in brief["items"] if item["priority"] == 100]
        returned_fixture_ids = set()
        for item in official_items:
            observation_id = item["event_id"]
            row = state.current_observation(observation_id)
            returned_fixture_ids.add(json.loads(row["raw_json"])["fixture_id"])
        false_negatives = sorted(set(positives) - returned_fixture_ids)
        false_positives = sorted(returned_fixture_ids.intersection(negatives))
        brief_units = budget_units(brief)
        consumer_reduction = 1 - (brief_units / raw_units)

        baseline_requests_n5 = 5 * 5
        broker_requests_n5 = 5 + 5
        amortized_request_reduction_n5 = 1 - (broker_requests_n5 / baseline_requests_n5)
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
            "consumer_context_reduction_basis_points": round(consumer_reduction * 10000),
            "request_model": {
                "rooms": 5,
                "consumers": 5,
                "baseline_source_requests": baseline_requests_n5,
                "broker_source_plus_consumer_requests": broker_requests_n5,
                "amortized_reduction_basis_points": round(amortized_request_reduction_n5 * 10000),
            },
            "brief_payload_sha256": payload_digest(brief),
            "claims": {
                "scope": "this digest-pinned synthetic and mutated fixture corpus only",
                "official_recall": f"{len(positives) - len(false_negatives)}/{len(positives)}",
                "official_false_positives": len(false_positives),
            },
        }
        passed = (
            not false_negatives
            and not false_positives
            and consumer_reduction >= 0.50
            and amortized_request_reduction_n5 >= 0.50
        )
        slice_report = {
            "schema": "technocore-verification-result/v1",
            "slice": 1,
            "input_sha256": report["corpus_sha256"],
            "checks": [
                {
                    "id": "S1-OFFICIAL-CORPUS",
                    "numerator": len(positives) - len(false_negatives),
                    "denominator": len(positives),
                    "false_positives": len(false_positives),
                    "threshold": f"{len(positives)}/{len(positives)} and 0 false positives",
                    "result": "pass" if not false_negatives and not false_positives else "fail",
                },
                {
                    "id": "S1-CONTEXT-REDUCTION",
                    "observed_basis_points": report["consumer_context_reduction_basis_points"],
                    "threshold_basis_points": 5000,
                    "result": "pass" if consumer_reduction >= 0.50 else "fail",
                },
                {
                    "id": "S1-REQUEST-MODEL-N5",
                    "observed_basis_points": report["request_model"][
                        "amortized_reduction_basis_points"
                    ],
                    "threshold_basis_points": 5000,
                    "result": "pass" if amortized_request_reduction_n5 >= 0.50 else "fail",
                },
            ],
            "result": "pass" if passed else "fail",
        }
        dashboard = build_brief(
            state,
            consumer_id="dashboard-demo",
            requested_budget=1800,
            as_of=corpus["fixed_as_of"],
        )
        return report, slice_report, dashboard


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--write", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    report, slice_report, dashboard = evaluate()
    if args.write:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        DASHBOARD_PATH.write_text(json.dumps(dashboard, indent=2, sort_keys=True) + "\n")
        SLICE_PATH.write_text(json.dumps(slice_report, indent=2, sort_keys=True) + "\n")
    if args.verify:
        if not REPORT_PATH.exists() or not SLICE_PATH.exists():
            raise SystemExit("verification reports are missing; run with --write")
        if json.loads(REPORT_PATH.read_text()) != report:
            raise SystemExit("context evaluation report does not reproduce")
        if json.loads(SLICE_PATH.read_text()) != slice_report:
            raise SystemExit("slice verification report does not reproduce")
        if not DASHBOARD_PATH.exists() or json.loads(DASHBOARD_PATH.read_text()) != dashboard:
            raise SystemExit("dashboard context does not reproduce")
    print(canonical_json({"report": report, "slice": slice_report}))
    if slice_report["result"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
