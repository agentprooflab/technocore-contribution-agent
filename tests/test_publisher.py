from dataclasses import replace
from pathlib import Path

import pytest

from tca.config import load_config
from tca.identity import Identity, MemorySecretStore
from tca.publisher import publish_bundle
from tca.state import State


def test_publication_is_blocked_during_shadow(tmp_path) -> None:
    project = Path(__file__).parents[1]
    config = load_config(project / "config" / "targets.toml")
    config = replace(
        config,
        observer=replace(config.observer, state_dir=tmp_path),
        identity=replace(
            config.identity,
            github_account="pseudonymous-org",
            x_account="pseudonymous-x",
        ),
    )
    state = State(tmp_path / "state.db")
    state.ensure_shadow_started()
    identity = Identity(MemorySecretStore(bytes.fromhex("03" * 32)))
    with pytest.raises(RuntimeError, match="shadow gate"):
        publish_bundle(config, state, identity, "missing", "bad", approved=True)
