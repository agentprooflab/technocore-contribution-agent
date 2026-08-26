import json

import pytest

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


@pytest.mark.parametrize(
    "arguments",
    [
        {"budget_units": True},
        {"budget_units": 100001},
        {"as_of": {"not": "a string"}},
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
