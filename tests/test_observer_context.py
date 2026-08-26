from pathlib import Path

from tca.config import load_config
from tca.observer import _observe_technocore
from tca.state import State


def config():
    return load_config(Path(__file__).parents[1] / "config" / "targets.toml")


def message(seq: int, text: str = "hello") -> dict:
    return {
        "seq": seq,
        "ts": f"2026-08-27T00:00:{seq % 60:02d}Z",
        "from": "did:key:z6MkTest",
        "text": text,
        "nonce": 1000 + seq,
    }


def test_incremental_room_collection_records_confirmed_gap(tmp_path, monkeypatch) -> None:
    state = State(tmp_path / "state.db")
    responses = iter(
        [
            {
                "room": "technocore",
                "first_seq": 10,
                "last_seq": 11,
                "messages": [message(10), message(11)],
            },
            {"room": "chat", "first_seq": 20, "last_seq": 20, "messages": [message(20)]},
            {"room": "did-key-method", "first_seq": 30, "last_seq": 30, "messages": [message(30)]},
            {
                "room": "technocore",
                "first_seq": 14,
                "last_seq": 15,
                "messages": [message(14), message(15)],
            },
            {"room": "chat", "first_seq": 20, "last_seq": 20, "messages": []},
            {"room": "did-key-method", "first_seq": 30, "last_seq": 30, "messages": []},
        ]
    )
    monkeypatch.setattr("tca.observer._get_json", lambda _url: next(responses))
    assert _observe_technocore(config(), state) == 4
    assert _observe_technocore(config(), state) == 2
    gaps = [
        row
        for row in state.coverage_rows()
        if row["scope"] == "technocore" and row["state"] == "confirmed_lost"
    ]
    assert [(row["start_value"], row["end_value"]) for row in gaps] == [(12, 13)]
    assert state.source_cursor("technocore", "technocore")["cursor"] == "15"


def test_cursor_rewind_is_isolated_as_ambiguous_epoch(tmp_path, monkeypatch) -> None:
    state = State(tmp_path / "state.db")
    state.commit_observation_page(
        source="technocore",
        scope="technocore",
        epoch=0,
        expected_cursor=None,
        observations=[],
        coverage_ranges=[(1, 100, "observed")],
        next_cursor="100",
    )
    responses = iter(
        [
            {
                "room": "technocore",
                "first_seq": 1,
                "last_seq": 2,
                "messages": [message(1), message(2)],
            },
            {"room": "chat", "first_seq": 0, "last_seq": 0, "messages": []},
            {"room": "did-key-method", "first_seq": 0, "last_seq": 0, "messages": []},
        ]
    )
    monkeypatch.setattr("tca.observer._get_json", lambda _url: next(responses))
    _observe_technocore(config(), state)
    cursor = state.source_cursor("technocore", "technocore")
    assert cursor["cursor"] == "100"
    assert cursor["state"] == "epoch_ambiguous"
