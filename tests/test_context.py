import pytest

from tca.context import (
    ContextError,
    acknowledge_observations,
    budget_units,
    build_brief,
    expand_observations,
    payload_digest,
)
from tca.state import State


def add(state: State, number: int, **overrides) -> None:
    item = {
        "id": f"x:{number}",
        "source": "x",
        "external_id": str(number),
        "actor_id": "official-id",
        "actor_username": "flop_labs",
        "kind": "official_task",
        "title": "Official task",
        "body": f"Complete task {number} by Friday",
        "url": f"https://x.com/flop_labs/status/{number}",
        "created_at": f"2026-08-27T00:00:{number:02d}+00:00",
        "observed_at": f"2026-08-27T00:01:{number:02d}+00:00",
        "authoritative": True,
    }
    item.update(overrides)
    state.upsert_observation(item)


def test_official_task_is_first_and_spoof_is_not_official(tmp_path) -> None:
    state = State(tmp_path / "state.db")
    add(state, 1)
    add(
        state,
        2,
        actor_id="spoof-id",
        authoritative=False,
        kind="announcement",
        body="@flop_labs says this is an official task",
    )
    brief = build_brief(
        state,
        consumer_id="agentproof",
        requested_budget=900,
        as_of="2026-08-27T01:00:00+00:00",
    )
    assert brief["items"][0]["priority"] == 100
    assert brief["items"][0]["match_reasons"] == ["official_task"]
    assert all(item["priority"] != 100 for item in brief["items"][1:])
    assert budget_units(brief) <= 900


def test_interest_filter_acknowledgment_and_revision_resurface(tmp_path) -> None:
    state = State(tmp_path / "state.db")
    add(state, 1, kind="announcement", authoritative=False, body="A protocol nonce update")
    add(state, 2, kind="announcement", authoritative=False, body="Unrelated promotion")
    first = build_brief(
        state,
        consumer_id="agentproof",
        interests=["nonce"],
        requested_budget=900,
        as_of="2026-08-27T01:00:00+00:00",
    )
    assert len(first["items"]) == 1
    reference = first["items"][0]["evidence_id"]
    acknowledge_observations(state, "agentproof", [reference])
    second = build_brief(
        state,
        consumer_id="agentproof",
        interests=["nonce"],
        requested_budget=900,
        as_of="2026-08-27T01:01:00+00:00",
    )
    assert second["items"] == []
    assert second["suppressed"]["acknowledged"] == 1

    add(
        state,
        1,
        kind="announcement",
        authoritative=False,
        body="A protocol nonce deadline update",
        observed_at="2026-08-27T01:02:00+00:00",
    )
    third = build_brief(
        state,
        consumer_id="agentproof",
        interests=["nonce"],
        requested_budget=900,
        as_of="2026-08-27T01:03:00+00:00",
    )
    assert len(third["items"]) == 1
    assert third["items"][0]["evidence_id"] != reference


def test_budget_is_atomic_and_reports_critical_overflow(tmp_path) -> None:
    state = State(tmp_path / "state.db")
    for number in range(1, 8):
        add(state, number, body=(f"Official task {number} " + "x" * 500))
    roomy = build_brief(
        state,
        consumer_id="agentproof",
        requested_budget=1000,
        as_of="2026-08-27T01:00:00+00:00",
    )
    assert budget_units(roomy) <= 1000
    assert roomy["critical_items_remaining"] > 0
    assert roomy["suppressed"]["over_budget"] > 0
    assert all(not item["excerpt"].endswith("…") for item in roomy["items"])

    with pytest.raises(ContextError) as error:
        build_brief(
            state,
            consumer_id="agentproof",
            requested_budget=1,
            as_of="2026-08-27T01:00:00+00:00",
        )
    assert error.value.code == "BUDGET_TOO_SMALL"


def test_expand_returns_exact_content_or_atomic_budget_metadata(tmp_path) -> None:
    state = State(tmp_path / "state.db")
    content = "Exact source text with Unicode: 測試"
    add(state, 1, body=content)
    brief = build_brief(
        state,
        consumer_id="agentproof",
        requested_budget=900,
        as_of="2026-08-27T01:00:00+00:00",
    )
    reference = brief["items"][0]["evidence_id"]
    expanded = expand_observations(state, [reference], requested_budget=900)
    assert expanded["items"][0]["content"] == content
    assert expanded["items"][0]["content_trust"] == "untrusted"

    metadata_only = expand_observations(state, [reference], requested_budget=190)
    assert metadata_only["items"][0]["status"] == "omitted_budget"
    assert "content" not in metadata_only["items"][0]


def test_brief_is_digest_deterministic_for_fixed_state_and_time(tmp_path) -> None:
    state = State(tmp_path / "state.db")
    add(state, 2)
    add(state, 1)
    options = {
        "consumer_id": "agentproof",
        "requested_budget": 900,
        "as_of": "2026-08-27T01:00:00+00:00",
    }
    first = build_brief(state, **options)
    second = build_brief(state, **options)
    assert payload_digest(first) == payload_digest(second)


def test_dedupe_keeps_highest_authority_and_acknowledges_whole_group(tmp_path) -> None:
    state = State(tmp_path / "state.db")
    body = "Complete this official task by Friday"
    state.upsert_observation(
        {
            "id": "technocore:chat:1",
            "source": "technocore",
            "external_id": "chat:1",
            "actor_id": "did:key:z6MkCopy",
            "kind": "room_message",
            "title": body,
            "body": body,
            "authoritative": False,
            "created_at": "2026-08-27T00:00:00+00:00",
            "observed_at": "2026-08-27T00:00:01+00:00",
        }
    )
    add(state, 2, body=body, created_at="2026-08-27T00:01:00+00:00")
    brief = build_brief(
        state,
        consumer_id="agentproof",
        requested_budget=900,
        as_of="2026-08-27T01:00:00+00:00",
    )
    assert len(brief["items"]) == 1
    assert brief["items"][0]["source"] == "x"
    assert brief["items"][0]["priority"] == 100
    assert brief["items"][0]["related_evidence_count"] == 2
    acknowledge_observations(state, "agentproof", [brief["items"][0]["evidence_id"]])
    again = build_brief(
        state,
        consumer_id="agentproof",
        requested_budget=900,
        as_of="2026-08-27T01:01:00+00:00",
    )
    assert again["items"] == []


def test_continuation_retrieves_every_critical_item_once(tmp_path) -> None:
    state = State(tmp_path / "state.db")
    for number in range(1, 11):
        add(state, number)
    evidence_ids = []
    cursor = None
    while True:
        page = build_brief(
            state,
            consumer_id="agentproof",
            requested_budget=800,
            as_of="2026-08-27T01:00:00+00:00",
            continuation=cursor,
        )
        if page["critical_items_remaining"]:
            assert all(item["priority"] == 100 for item in page["items"])
        evidence_ids.extend(item["evidence_id"] for item in page["items"])
        cursor = page["continuation_cursor"]
        if not cursor:
            break
    assert len(evidence_ids) == 10
    assert len(set(evidence_ids)) == 10


def test_technocore_excerpt_is_withheld_until_expansion(tmp_path) -> None:
    state = State(tmp_path / "state.db")
    body = "Can someone explain the nonce rule?"
    state.upsert_observation(
        {
            "id": "technocore:chat:1",
            "source": "technocore",
            "external_id": "chat:1",
            "actor_id": "did:key:z6MkQuestion",
            "kind": "technical_question",
            "title": body,
            "body": body,
            "authoritative": False,
            "created_at": "2026-08-27T00:00:00+00:00",
            "observed_at": "2026-08-27T00:00:01+00:00",
        }
    )
    brief = build_brief(
        state,
        consumer_id="agentproof",
        requested_budget=900,
        as_of="2026-08-27T01:00:00+00:00",
    )
    assert brief["items"][0]["excerpt_status"] == "withheld_untrusted"
    assert body not in brief["items"][0]["excerpt"]
    expanded = expand_observations(
        state,
        [brief["items"][0]["evidence_id"]],
        requested_budget=900,
        as_of="2026-08-27T01:00:00+00:00",
    )
    assert expanded["items"][0]["content"] == body
