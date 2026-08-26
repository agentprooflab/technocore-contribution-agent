from dataclasses import replace
from pathlib import Path
from urllib.error import URLError

from tca.config import load_config
from tca.context import coverage_report
from tca.observer import _observe_technocore, observe
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
            {
                "room": "technocore",
                "first_seq": 14,
                "last_seq": 15,
                "messages": [message(14), message(15)],
            },
            {"room": "chat", "first_seq": 20, "last_seq": 20, "messages": [message(20)]},
            {"room": "chat", "first_seq": 20, "last_seq": 20, "messages": []},
            {
                "room": "did-key-method",
                "first_seq": 30,
                "last_seq": 30,
                "messages": [message(30)],
            },
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


def test_confirmed_rewind_recovers_into_fresh_local_epoch(tmp_path, monkeypatch) -> None:
    state = State(tmp_path / "state.db")
    cfg = config()
    room = cfg.observer.rooms[0]
    state.commit_observation_page(
        source="technocore",
        scope=room,
        epoch=0,
        expected_cursor=None,
        observations=[],
        coverage_ranges=[(1, 100, "observed")],
        next_cursor="100",
    )
    responses = iter(
        [
            {"room": room, "first_seq": 1, "last_seq": 2, "messages": [message(1), message(2)]},
            {"room": "chat", "first_seq": 0, "last_seq": 0, "messages": []},
            {"room": "did-key-method", "first_seq": 0, "last_seq": 0, "messages": []},
            {"room": room, "first_seq": 1, "last_seq": 2, "messages": [message(2)]},
            {"room": room, "first_seq": 1, "last_seq": 2, "messages": [message(1), message(2)]},
            {"room": "chat", "first_seq": 0, "last_seq": 0, "messages": []},
            {"room": "did-key-method", "first_seq": 0, "last_seq": 0, "messages": []},
        ]
    )
    monkeypatch.setattr("tca.observer._get_json", lambda _url: next(responses))

    assert _observe_technocore(cfg, state) == 0
    assert state.source_cursor("technocore", room)["state"] == "epoch_ambiguous"
    first_report = next(
        item for item in coverage_report(state) if item["scope"] == room and item["epoch"] == 0
    )
    assert (first_report["status"], first_report["cursor"]) == ("epoch_ambiguous", "100")
    assert _observe_technocore(cfg, state) == 2
    cursor = state.source_cursor("technocore", room)
    assert (cursor["epoch"], cursor["cursor"], cursor["state"]) == (1, "2", "active")
    assert state.current_observation(f"technocore:{room}:1:1")["external_id"] == f"{room}:1"
    epochs = {
        row["epoch"]
        for row in state.coverage_rows()
        if row["source"] == "technocore" and row["scope"] == room
    }
    assert epochs == {0, 1}
    reports = {item["epoch"]: item for item in coverage_report(state) if item["scope"] == room}
    assert (reports[0]["cursor"], reports[0]["observed"]) == (None, 100)
    assert (reports[1]["cursor"], reports[1]["observed"]) == ("2", 2)


def test_recovered_cursor_only_epoch_is_distinct_from_historical_coverage(tmp_path) -> None:
    state = State(tmp_path / "state.db")
    state.commit_observation_page(
        source="technocore",
        scope="chat",
        epoch=0,
        expected_cursor=None,
        observations=[],
        coverage_ranges=[(1, 50, "observed")],
        next_cursor="50",
        cursor_state="epoch_ambiguous",
    )
    assert (
        state.recover_ambiguous_epoch(
            source="technocore",
            scope="chat",
            expected_epoch=0,
            expected_cursor="50",
        )
        == 1
    )
    reports = {item["epoch"]: item for item in coverage_report(state) if item["scope"] == "chat"}
    assert sorted(reports) == [0, 1]
    assert (reports[0]["cursor"], reports[0]["observed"]) == (None, 50)
    assert reports[0]["status"] == "complete_for_observed_window"
    assert (reports[1]["cursor"], reports[1]["observed"], reports[1]["status"]) == (
        None,
        0,
        "active",
    )


def test_second_response_rewind_cannot_downgrade_private_scope(tmp_path, monkeypatch) -> None:
    state = State(tmp_path / "state.db")
    base = config()
    cfg = replace(base, observer=replace(base.observer, rooms=("mb-p-secret",)))
    state.commit_observation_page(
        source="technocore",
        scope="mb-p-secret",
        epoch=0,
        expected_cursor=None,
        observations=[],
        coverage_ranges=[(1, 100, "observed")],
        next_cursor="100",
        exposure_class="restricted",
    )
    responses = iter(
        [
            {"room": "mb-p-secret", "first_seq": 100, "last_seq": 100, "messages": [message(100)]},
            {
                "room": "mb-p-secret",
                "first_seq": 1,
                "last_seq": 2,
                "messages": [message(1), message(2)],
            },
        ]
    )
    monkeypatch.setattr("tca.observer._get_json", lambda _url: next(responses))
    _observe_technocore(cfg, state)
    cursor = state.source_cursor("technocore", "mb-p-secret")
    assert cursor["state"] == "epoch_ambiguous"
    assert cursor["exposure_class"] == "restricted"
    assert {row["exposure_class"] for row in state.coverage_rows()} == {"restricted"}


def test_backlog_after_returned_window_is_pending_not_complete(tmp_path, monkeypatch) -> None:
    state = State(tmp_path / "state.db")
    responses = iter(
        [
            {
                "room": "technocore",
                "first_seq": 1,
                "last_seq": 1000,
                "messages": [message(1), message(2)],
            },
            {"room": "chat", "first_seq": 0, "last_seq": 0, "messages": []},
            {"room": "did-key-method", "first_seq": 0, "last_seq": 0, "messages": []},
        ]
    )
    monkeypatch.setattr("tca.observer._get_json", lambda _url: next(responses))
    _observe_technocore(config(), state)
    report = next(item for item in coverage_report(state) if item["scope"] == "technocore")
    assert report["status"] == "pending"
    assert report["pending_fetch"] == 998


def test_failed_collection_replaces_prior_healthy_source_status(tmp_path, monkeypatch) -> None:
    state = State(tmp_path / "state.db")
    cfg = config()
    for room in cfg.observer.rooms:
        state.commit_observation_page(
            source="technocore",
            scope=room,
            epoch=0,
            expected_cursor=None,
            observations=[],
            coverage_ranges=[(1, 1, "observed")],
            next_cursor="1",
        )
    monkeypatch.setattr("tca.observer._github_observations", lambda _config: [])
    monkeypatch.setattr("tca.observer._x_observations", lambda _config: [])
    monkeypatch.setattr(
        "tca.observer._observe_technocore",
        lambda _config, _state: (_ for _ in ()).throw(URLError("down")),
    )
    result = observe(cfg, state)
    assert "technocore" in result["errors"]
    reports = coverage_report(state)
    room_reports = [item for item in reports if item["source"] == "technocore"]
    assert room_reports
    assert all(item["status"] == "unavailable" for item in room_reports)
    assert any(item["source"] == "x" and item["status"] == "sampled" for item in reports)
    assert any(item["source"] == "github" and item["status"] == "sampled" for item in reports)
