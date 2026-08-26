import json
from dataclasses import replace
from pathlib import Path

from tca.bundles import PrepareOptions, prepare
from tca.config import load_config
from tca.state import State


def test_prepare_creates_digest_locked_bundle(tmp_path) -> None:
    project = Path(__file__).parents[1]
    config = load_config(project / "config" / "targets.toml")
    config = replace(config, observer=replace(config.observer, state_dir=tmp_path))
    state = State(tmp_path / "state.db")
    state.upsert_observation(
        {
            "id": "x:task",
            "source": "x",
            "external_id": "task",
            "kind": "official_task",
            "title": "task",
            "body": "official task body",
            "url": "https://x.com/flop_labs/status/1",
            "authoritative": True,
        }
    )
    state.add_candidate(
        {
            "id": "cand-task",
            "observation_id": "x:task",
            "category": "official_task",
            "priority": 100,
            "status": "ready",
            "reason": "official",
            "created_at": "2026-08-26T00:00:00+00:00",
        }
    )
    result = prepare(
        config,
        state,
        "cand-task",
        PrepareOptions(
            kind="testnet_task",
            artifact_url="https://github.com/example/repo",
            technocore_room="chat",
            technocore_text="Completed a reproducible testnet task with public evidence.",
        ),
    )
    bundle = json.loads(Path(result["path"]).read_text())
    assert bundle["candidate"]["priority"] == 100
    assert bundle["candidate"]["external_id"] == "task"
    assert bundle["actions"][0]["room"] == "chat"
    assert bundle["security"]["requires_batch_approval"] is True
    assert state.bundle(result["id"])["sha256"] == result["sha256"]
