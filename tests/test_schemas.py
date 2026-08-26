import json
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from evals.run_context_eval import evaluate
from tca.context import ContextError, build_brief, expand_observations
from tca.state import State

ROOT = Path(__file__).parents[1]


def registry_and_schemas():
    schemas = [json.loads(path.read_text()) for path in sorted((ROOT / "schemas").glob("*.json"))]
    registry = Registry().with_resources(
        (schema["$id"], Resource.from_contents(schema)) for schema in schemas
    )
    return registry, {schema["$id"].rsplit("/", 1)[-1]: schema for schema in schemas}


def validate(name: str, payload: dict) -> None:
    registry, schemas = registry_and_schemas()
    Draft202012Validator(schemas[name], registry=registry).validate(payload)


def test_brief_expansion_and_error_validate_against_public_schemas(tmp_path) -> None:
    state = State(tmp_path / "state.db")
    state.upsert_observation(
        {
            "id": "x:1",
            "source": "x",
            "external_id": "1",
            "actor_id": "official",
            "kind": "official_task",
            "title": "Task",
            "body": "Complete the task",
            "authoritative": True,
            "observed_at": "2026-08-27T00:00:00+00:00",
        }
    )
    brief = build_brief(
        state,
        consumer_id="schema-test",
        requested_budget=900,
        as_of="2026-08-27T01:00:00+00:00",
    )
    validate("context-brief-v1.schema.json", brief)
    for item in brief["items"]:
        validate("context-observation-v1.schema.json", item)
    expansion = expand_observations(
        state,
        [brief["items"][0]["evidence_id"]],
        requested_budget=900,
    )
    validate("context-expansion-v1.schema.json", expansion)
    error = ContextError("TEST", "test").payload()
    validate("context-error-v1.schema.json", error)


def test_dashboard_envelope_and_nested_brief_validate() -> None:
    _report, dashboard = evaluate()
    validate("context-dashboard-v1.schema.json", dashboard)
    validate("context-brief-v1.schema.json", dashboard["brief"])


def test_public_docs_publish_exact_schema_bytes() -> None:
    for source in sorted((ROOT / "schemas").glob("*.json")):
        published = ROOT / "docs" / "schemas" / source.name
        assert published.read_bytes() == source.read_bytes()
