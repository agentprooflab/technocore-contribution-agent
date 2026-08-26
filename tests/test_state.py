from datetime import UTC, datetime, timedelta

from tca.state import State


def test_shadow_gate_and_nonce_recovery(tmp_path) -> None:
    state = State(tmp_path / "state.db")
    start = datetime(2026, 8, 26, tzinfo=UTC)
    assert state.shadow_remaining(48, start) == timedelta(hours=48)
    assert not state.shadow_complete(48, start + timedelta(hours=47, minutes=59))
    assert state.shadow_complete(48, start + timedelta(hours=48))
    assert state.next_nonce("room:technocore", floor=100) == 100
    assert state.next_nonce("room:technocore", floor=50) == 101


def test_observation_deduplicates(tmp_path) -> None:
    state = State(tmp_path / "state.db")
    observation = {
        "id": "x:1",
        "source": "x",
        "external_id": "1",
        "kind": "official_task",
        "title": "task",
        "body": "specific task",
        "authoritative": True,
    }
    assert state.upsert_observation(observation)
    assert not state.upsert_observation(observation)
