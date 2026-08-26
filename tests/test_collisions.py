from tca.context import check_collisions
from tca.state import State


def test_github_exact_issue_reference_is_confirmed(tmp_path) -> None:
    state = State(tmp_path / "state.db")
    state.upsert_observation(
        {
            "id": "github:repo:issue:12",
            "source": "github",
            "external_id": "12",
            "kind": "issue",
            "title": "Bug",
            "body": "Reproduction",
            "authoritative": False,
            "observed_at": "2026-08-27T00:00:00+00:00",
        }
    )
    state.upsert_observation(
        {
            "id": "github:repo:pr:13",
            "source": "github",
            "external_id": "13",
            "kind": "pull_request",
            "title": "Fixes #12",
            "body": "Regression test included",
            "authoritative": False,
            "observed_at": "2026-08-27T00:01:00+00:00",
        }
    )
    result = check_collisions(state, "github:repo:issue:12")
    assert result["collision_state"] == "confirmed"
    assert result["matches"][0]["rule"] == "github_exact_issue_reference"


def test_closed_unmerged_pull_request_does_not_block_issue(tmp_path) -> None:
    state = State(tmp_path / "state.db")
    state.upsert_observation(
        {
            "id": "github:repo:issue:12",
            "source": "github",
            "external_id": "12",
            "kind": "issue",
            "title": "Bug",
            "body": "Reproduction",
            "authoritative": False,
            "observed_at": "2026-08-27T00:00:00+00:00",
            "source_state": "open",
        }
    )
    state.upsert_observation(
        {
            "id": "github:repo:pr:13",
            "source": "github",
            "external_id": "13",
            "kind": "pull_request",
            "title": "Fixes #12",
            "body": "Closed without merge",
            "authoritative": False,
            "observed_at": "2026-08-27T00:01:00+00:00",
            "source_state": "closed",
        }
    )
    result = check_collisions(state, "github:repo:issue:12")
    assert result["collision_state"] == "none_observed"
    assert result["matches"] == []


def test_merged_pull_request_remains_a_confirmed_resolution(tmp_path) -> None:
    state = State(tmp_path / "state.db")
    state.upsert_observation(
        {
            "id": "github:repo:issue:12",
            "source": "github",
            "external_id": "12",
            "kind": "issue",
            "title": "Bug",
            "body": "Reproduction",
            "authoritative": False,
            "observed_at": "2026-08-27T00:00:00+00:00",
            "source_state": "open",
        }
    )
    state.upsert_observation(
        {
            "id": "github:repo:pr:13",
            "source": "github",
            "external_id": "13",
            "kind": "pull_request",
            "title": "Fixes #12",
            "body": "Merged fix",
            "authoritative": False,
            "observed_at": "2026-08-27T00:01:00+00:00",
            "raw": {
                "state": "closed",
                "pull_request": {"merged_at": "2026-08-27T00:02:00+00:00"},
            },
        }
    )
    result = check_collisions(state, "github:repo:issue:12")
    assert result["collision_state"] == "confirmed"
    assert result["matches"][0]["rule"] == "github_exact_issue_reference"


def test_technocore_exact_reply_reference_is_confirmed(tmp_path) -> None:
    state = State(tmp_path / "state.db")
    for seq, body in ((263, "How are keys verified?"), (270, "Re chat seq 263: offline.")):
        state.upsert_observation(
            {
                "id": f"technocore:chat:0:{seq}",
                "source": "technocore",
                "external_id": f"chat:{seq}",
                "actor_id": f"did:key:z6Mk{seq}",
                "kind": "technical_question" if seq == 263 else "room_message",
                "title": body,
                "body": body,
                "authoritative": False,
                "created_at": f"2026-08-27T00:{seq % 60:02d}:00+00:00",
                "observed_at": f"2026-08-27T01:{seq % 60:02d}:00+00:00",
            }
        )
    state.commit_observation_page(
        source="technocore",
        scope="chat",
        epoch=0,
        expected_cursor=None,
        observations=[],
        coverage_ranges=[(263, 270, "observed")],
        next_cursor="270",
    )
    result = check_collisions(state, "technocore:chat:0:263")
    assert result["collision_state"] == "confirmed"
    assert result["matches"][0]["rule"] == "technocore_exact_source_reference"
