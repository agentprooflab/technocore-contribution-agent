import json

import pytest
from jsonschema import Draft202012Validator

from tca.context import build_brief, payload_digest
from tca.mcp import TOOLS, handle_request, strict_loads
from tca.state import State


def populated_state(tmp_path) -> State:
    state = State(tmp_path / "state.db")
    state.upsert_observation(
        {
            "id": "x:1",
            "source": "x",
            "external_id": "1",
            "actor_id": "official-id",
            "actor_username": "flop_labs",
            "kind": "official_task",
            "title": "Official task",
            "body": "Complete the official task",
            "authoritative": True,
            "created_at": "2026-08-27T00:00:00+00:00",
            "observed_at": "2026-08-27T00:01:00+00:00",
        }
    )
    return state


def request(method: str, params=None, request_id: int = 1):
    value = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        value["params"] = params
    return value


def test_mcp_exposes_only_read_only_tool_allowlist(tmp_path) -> None:
    state = populated_state(tmp_path)
    response = handle_request(state, "agentproof", request("tools/list"))
    names = [tool["name"] for tool in response["result"]["tools"]]
    assert names == ["get_relevant_updates", "expand_observations", "coverage_report"]
    assert names == [tool["name"] for tool in TOOLS]
    assert not {"publish", "sign", "post", "shell", "open_url"}.intersection(names)


def test_cli_domain_and_mcp_structured_content_are_identical(tmp_path) -> None:
    state = populated_state(tmp_path)
    arguments = {"budget_units": 900, "as_of": "2026-08-27T01:00:00+00:00"}
    response = handle_request(
        state,
        "agentproof",
        request("tools/call", {"name": "get_relevant_updates", "arguments": arguments}),
    )
    structured = response["result"]["structuredContent"]
    direct = build_brief(
        state,
        consumer_id="agentproof",
        requested_budget=900,
        as_of="2026-08-27T01:00:00+00:00",
    )
    assert payload_digest(structured) == payload_digest(direct)
    assert json.loads(response["result"]["content"][0]["text"]) == structured


def test_unknown_or_mutating_tool_name_fails_without_state_change(tmp_path) -> None:
    state = populated_state(tmp_path)
    before = state.counts()
    response = handle_request(
        state,
        "agentproof",
        request("tools/call", {"name": "publish", "arguments": {}}),
    )
    assert response["result"]["isError"] is True
    assert response["result"]["structuredContent"]["error"]["code"] == "METHOD_NOT_FOUND"
    assert state.counts() == before


def test_strict_json_rejects_duplicates_constants_and_depth() -> None:
    with pytest.raises(ValueError, match="duplicate JSON key"):
        strict_loads('{"id":1,"id":2}')
    with pytest.raises(ValueError, match="invalid JSON constant"):
        strict_loads('{"value":NaN}')
    value = "0"
    for _ in range(40):
        value = f"[{value}]"
    with pytest.raises(ValueError, match="nesting"):
        strict_loads(value)


def test_initialize_and_notifications_contract(tmp_path) -> None:
    state = populated_state(tmp_path)
    initialized = handle_request(
        state,
        "agentproof",
        request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        ),
    )
    assert initialized["result"]["serverInfo"]["name"] == "technocore-brief"
    assert (
        handle_request(
            state,
            "agentproof",
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        is None
    )
    invalid = handle_request(state, "agentproof", request("initialize", {}))
    assert invalid["error"]["code"] == -32602
    assert (
        handle_request(
            state,
            "agentproof",
            {
                "jsonrpc": "2.0",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
        )
        is None
    )
    invalid_types = handle_request(
        state,
        "agentproof",
        request(
            "initialize",
            {"protocolVersion": {}, "capabilities": [], "clientInfo": 1},
        ),
    )
    assert invalid_types["error"]["code"] == -32602


def test_request_ids_and_common_metadata_follow_mcp_contract(tmp_path) -> None:
    state = populated_state(tmp_path)
    invalid_id = handle_request(
        state,
        "agentproof",
        {"jsonrpc": "2.0", "id": {"invalid": True}, "method": "ping"},
    )
    assert invalid_id["error"]["code"] == -32600
    response = handle_request(
        state,
        "agentproof",
        request(
            "tools/call",
            {
                "name": "coverage_report",
                "arguments": {},
                "_meta": {"progressToken": "fixture"},
            },
        ),
    )
    assert response["result"]["isError"] is False


@pytest.mark.parametrize(
    "arguments",
    [
        {"budget_units": True},
        {"budget_units": 100001},
        {"as_of": {"not": "a string"}},
        {"since": {"not": "a string"}},
        {"since": "x" * 2049},
        {"interests": ["same"] * 33},
        {"mention_markers": ["x" * 81]},
        {"mention_markers": ["x" * 161]},
    ],
)
def test_declared_mcp_argument_bounds_are_enforced(tmp_path, arguments) -> None:
    state = populated_state(tmp_path)
    response = handle_request(
        state,
        "agentproof",
        request("tools/call", {"name": "get_relevant_updates", "arguments": arguments}),
    )
    assert response["result"]["isError"] is True
    assert response["result"]["structuredContent"]["error"]["code"] == "INVALID_ARGUMENTS"
    assert (
        handle_request(
            state,
            "agentproof",
            {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {}},
        )
        is None
    )


@pytest.mark.parametrize("token", ["W10", "Ingi", "MA", "bnVsbA"])
@pytest.mark.parametrize("field,prefix", [("continuation", "continue:v1:"), ("since", "brief:v2:")])
def test_non_object_cursor_payload_is_typed_and_server_survives(
    tmp_path, token, field, prefix
) -> None:
    state = populated_state(tmp_path)
    response = handle_request(
        state,
        "agentproof",
        request(
            "tools/call",
            {
                "name": "get_relevant_updates",
                "arguments": {field: prefix + token, "budget_units": 900},
            },
        ),
    )
    assert response["result"]["isError"] is True
    assert response["result"]["structuredContent"]["error"]["code"] == "INVALID_CURSOR"
    assert handle_request(state, "agentproof", request("ping"))["result"] == {}


def test_success_and_error_structured_content_match_output_schema(tmp_path) -> None:
    state = populated_state(tmp_path)
    tool = next(item for item in TOOLS if item["name"] == "get_relevant_updates")
    success = handle_request(
        state,
        "agentproof",
        request(
            "tools/call",
            {
                "name": "get_relevant_updates",
                "arguments": {
                    "budget_units": 900,
                    "as_of": "2026-08-27T01:00:00+00:00",
                },
            },
        ),
    )["result"]
    failure = handle_request(
        state,
        "agentproof",
        request(
            "tools/call",
            {"name": "get_relevant_updates", "arguments": {"budget_units": True}},
        ),
    )["result"]
    validator = Draft202012Validator(tool["outputSchema"])
    validator.validate(success["structuredContent"])
    validator.validate(failure["structuredContent"])
    assert failure["isError"] is True


def test_coverage_report_is_compact_and_paginated(tmp_path) -> None:
    state = populated_state(tmp_path)
    for number in range(5):
        state.commit_observation_page(
            source="technocore",
            scope=f"room-{number}",
            epoch=0,
            expected_cursor=None,
            observations=[],
            coverage_ranges=[(1, 10, "observed")],
            next_cursor="10",
        )

    def page(cursor=None):
        arguments = {"limit": 2}
        if cursor:
            arguments["cursor"] = cursor
        return handle_request(
            state,
            "agentproof",
            request(
                "tools/call",
                {"name": "coverage_report", "arguments": arguments},
            ),
        )["result"]

    first = page()
    second = page(first["structuredContent"]["next_cursor"])
    assert len(first["structuredContent"]["sources"]) == 2
    assert len(second["structuredContent"]["sources"]) == 2
    assert all("ranges" not in item for item in first["structuredContent"]["sources"])
    tool = next(item for item in TOOLS if item["name"] == "coverage_report")
    Draft202012Validator(tool["outputSchema"]).validate(first["structuredContent"])


def test_completed_brief_cursor_resumes_as_delta_without_redelivery(tmp_path) -> None:
    state = populated_state(tmp_path)

    def brief(arguments):
        return handle_request(
            state,
            "agentproof",
            request(
                "tools/call",
                {"name": "get_relevant_updates", "arguments": arguments},
            ),
        )["result"]

    first = brief({"budget_units": 900, "as_of": "2026-08-27T01:00:00+00:00"})["structuredContent"]
    assert [item["evidence_id"].split("@", 1)[0] for item in first["items"]] == ["x:1"]
    assert first["continuation_cursor"] is None
    assert first["brief_cursor"].startswith("brief:v2:")

    unchanged = brief(
        {
            "budget_units": 900,
            "as_of": "2026-08-27T01:01:00+00:00",
            "since": first["brief_cursor"],
        }
    )["structuredContent"]
    assert unchanged["items"] == []
    assert unchanged["brief_cursor"] == first["brief_cursor"]

    state.upsert_observation(
        {
            "id": "x:2",
            "source": "x",
            "external_id": "2",
            "actor_id": "official-id",
            "actor_username": "flop_labs",
            "kind": "official_task",
            "title": "Second official task",
            "body": "Complete the second official task",
            "authoritative": True,
            "created_at": "2026-08-27T01:02:00+00:00",
            "observed_at": "2026-08-27T01:02:01+00:00",
        }
    )
    delta = brief(
        {
            "budget_units": 900,
            "as_of": "2026-08-27T01:03:00+00:00",
            "since": first["brief_cursor"],
        }
    )["structuredContent"]
    assert [item["evidence_id"].split("@", 1)[0] for item in delta["items"]] == ["x:2"]
    assert delta["brief_cursor"] != first["brief_cursor"]
