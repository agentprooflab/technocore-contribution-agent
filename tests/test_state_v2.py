import json
import sqlite3

import pytest

from tca.context import coverage_report
from tca.state import (
    LEGACY_SCHEMA,
    SCHEMA_VERSION,
    V2_STATEMENTS,
    V3_STATEMENTS,
    StaleCursorError,
    State,
    canonical_json,
    sha256_text,
)


def observation(**overrides):
    item = {
        "id": "x:1",
        "source": "x",
        "external_id": "1",
        "actor_id": "official-id",
        "actor_username": "flop_labs",
        "kind": "official_task",
        "title": "task",
        "body": "Complete the task by Friday",
        "authoritative": True,
        "observed_at": "2026-08-27T00:00:00+00:00",
    }
    item.update(overrides)
    return item


def test_legacy_database_migrates_without_changing_existing_rows(tmp_path) -> None:
    path = tmp_path / "state.db"
    connection = sqlite3.connect(path)
    connection.executescript(LEGACY_SCHEMA)
    connection.execute(
        """INSERT INTO observations
        (id, source, external_id, kind, title, body, observed_at, authoritative, raw_json)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "x:legacy",
            "x",
            "legacy",
            "announcement",
            "legacy title",
            "legacy body",
            "2026-08-26T00:00:00+00:00",
            0,
            json.dumps({"legacy": True}),
        ),
    )
    connection.commit()
    connection.close()

    state = State(path)
    row = state.current_observation("x:legacy")
    assert row["body"] == "legacy body"
    with state.connect() as migrated:
        assert migrated.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert migrated.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert migrated.execute("PRAGMA foreign_key_check").fetchall() == []
        assert migrated.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_mutable_observation_creates_revision_and_resurfaces(tmp_path) -> None:
    state = State(tmp_path / "state.db")
    assert state.upsert_observation(observation())
    first = state.current_observation("x:1")
    state.acknowledge("agent-a", [("x:1", first["revision_digest"])])

    assert state.upsert_observation(
        observation(body="Complete the task by Saturday", observed_at="2026-08-27T01:00:00+00:00")
    )
    second = state.current_observation("x:1")
    assert first["revision_digest"] != second["revision_digest"]
    assert ("x:1", second["revision_digest"]) not in state.acknowledgments("agent-a")
    with state.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM observation_revisions WHERE observation_id = 'x:1'"
        ).fetchone()[0]
    assert count == 2


def test_immutable_conflict_is_quarantined_without_advancing_head(tmp_path) -> None:
    state = State(tmp_path / "state.db")
    item = observation(
        id="technocore:chat:0:1",
        source="technocore",
        external_id="chat:1",
        body="first",
        authoritative=False,
    )
    state.upsert_observation(item)
    first = state.current_observation(item["id"])
    assert state.upsert_observation({**item, "body": "conflict"})
    current = state.current_observation(item["id"])
    assert current["revision_digest"] == first["revision_digest"]
    with state.connect() as connection:
        conflict = connection.execute(
            """SELECT quarantine_reason FROM observation_revisions
            WHERE observation_id = ? AND revision_digest != ?""",
            (item["id"], first["revision_digest"]),
        ).fetchone()
    assert conflict["quarantine_reason"] == "immutable_conflict"


def test_page_commit_normalizes_coverage_and_rejects_stale_cursor(tmp_path) -> None:
    state = State(tmp_path / "state.db")
    state.commit_observation_page(
        source="technocore",
        scope="chat",
        epoch=0,
        expected_cursor=None,
        observations=[],
        coverage_ranges=[(100, 101, "observed"), (102, 109, "pending_fetch")],
        next_cursor="101",
    )
    state.commit_observation_page(
        source="technocore",
        scope="chat",
        epoch=0,
        expected_cursor="101",
        observations=[],
        coverage_ranges=[(102, 103, "confirmed_lost"), (104, 109, "observed")],
        next_cursor="109",
    )
    rows = [
        tuple(row[key] for key in ("start_value", "end_value", "state"))
        for row in state.coverage_rows()
    ]
    assert rows == [(100, 101, "observed"), (102, 103, "confirmed_lost"), (104, 109, "observed")]
    with pytest.raises(StaleCursorError):
        state.commit_observation_page(
            source="technocore",
            scope="chat",
            epoch=0,
            expected_cursor="101",
            observations=[],
            coverage_ranges=[],
            next_cursor="110",
        )


def test_cursor_coverage_exposure_is_monotonic_and_epoch_recovery_is_cas_safe(tmp_path) -> None:
    state = State(tmp_path / "state.db")
    state.commit_observation_page(
        source="technocore",
        scope="mb-p-secret",
        epoch=0,
        expected_cursor=None,
        observations=[],
        coverage_ranges=[(1, 1, "observed")],
        next_cursor="1",
        exposure_class="restricted",
    )
    state.commit_observation_page(
        source="technocore",
        scope="mb-p-secret",
        epoch=0,
        expected_cursor="1",
        observations=[],
        coverage_ranges=[(2, 2, "observed")],
        next_cursor="2",
        cursor_state="epoch_ambiguous",
        exposure_class="public",
    )
    assert state.source_cursor("technocore", "mb-p-secret")["exposure_class"] == "restricted"
    assert {row["exposure_class"] for row in state.coverage_rows()} == {"restricted"}

    next_epoch = state.recover_ambiguous_epoch(
        source="technocore",
        scope="mb-p-secret",
        expected_epoch=0,
        expected_cursor="2",
        exposure_class="public",
    )
    assert next_epoch == 1
    cursor = state.source_cursor("technocore", "mb-p-secret")
    assert (cursor["epoch"], cursor["cursor"], cursor["state"]) == (1, None, "active")
    assert cursor["exposure_class"] == "restricted"
    with pytest.raises(StaleCursorError):
        state.recover_ambiguous_epoch(
            source="technocore",
            scope="mb-p-secret",
            expected_epoch=0,
            expected_cursor="2",
        )


def test_volatile_raw_metadata_does_not_create_material_revision(tmp_path) -> None:
    state = State(tmp_path / "state.db")
    item = observation(raw={"text": "same", "likeCount": 1})
    assert state.upsert_observation(item)
    first = state.current_observation("x:1")["revision_digest"]
    assert not state.upsert_observation({**item, "raw": {"text": "same", "likeCount": 2}})
    second = state.current_observation("x:1")["revision_digest"]
    assert first == second


def test_more_restrictive_policy_correction_updates_current_projection(tmp_path) -> None:
    state = State(tmp_path / "state.db")
    item = observation(
        id="technocore:chat:1",
        source="technocore",
        external_id="chat:1",
        authoritative=False,
        exposure_class="public",
    )
    state.upsert_observation(item)
    assert state.current_observation(item["id"])["exposure_class"] == "public"
    assert not state.upsert_observation(
        {**item, "exposure_class": "restricted", "quarantine_reason": "policy_rescan"}
    )
    corrected = state.current_observation(item["id"])
    assert corrected["exposure_class"] == "restricted"
    assert corrected["quarantine_reason"] == "policy_rescan"
    assert state.current_observations(public_only=True) == []


def test_v2_coverage_migration_restores_only_structurally_public_scopes(tmp_path) -> None:
    path = tmp_path / "state.db"
    connection = sqlite3.connect(path)
    connection.executescript(LEGACY_SCHEMA)
    for statement in V2_STATEMENTS:
        connection.execute(statement)
    for source, scope in (
        ("technocore", "chat"),
        ("technocore", "mb-p-secret"),
        ("github", "agentprooflab/repo"),
        ("custom", "public-looking"),
    ):
        connection.execute(
            """INSERT INTO source_cursors
            (source, scope, epoch, cursor, state, updated_at)
            VALUES(?, ?, 0, '10', 'active', '2026-08-27T00:00:00Z')""",
            (source, scope),
        )
        connection.execute(
            """INSERT INTO coverage_ranges
            (source, scope, epoch, start_value, end_value, state, recorded_at)
            VALUES(?, ?, 0, 1, 10, 'observed', '2026-08-27T00:00:00Z')""",
            (source, scope),
        )
    connection.execute("PRAGMA user_version=2")
    connection.commit()
    connection.close()

    state = State(path)
    public_scopes = {(row["source"], row["scope"]) for row in coverage_report(state)}
    assert public_scopes == {("github", "agentprooflab/repo"), ("technocore", "chat")}

    state.commit_observation_page(
        source="technocore",
        scope="chat",
        epoch=0,
        expected_cursor="10",
        observations=[],
        coverage_ranges=[(11, 11, "observed")],
        next_cursor="11",
        exposure_class="public",
    )
    state.commit_observation_page(
        source="technocore",
        scope="mb-p-secret",
        epoch=0,
        expected_cursor="10",
        observations=[],
        coverage_ranges=[(11, 11, "observed")],
        next_cursor="11",
        exposure_class="public",
    )
    assert state.source_cursor("technocore", "chat")["exposure_class"] == "public"
    assert state.source_cursor("technocore", "mb-p-secret")["exposure_class"] == "restricted"


def test_v2_migration_collapses_epoch_zero_duplicate_ids(tmp_path) -> None:
    path = tmp_path / "state.db"
    connection = sqlite3.connect(path)
    connection.executescript(LEGACY_SCHEMA)
    for statement in V2_STATEMENTS:
        connection.execute(statement)
    material = canonical_json(
        {
            "source": "technocore",
            "external_id": "chat:7",
            "actor_id": "did:key:z6Mk",
            "actor_username": "did:key:z6Mk",
            "kind": "room_message",
            "title": "same",
            "body": "same",
            "url": None,
            "created_at": "2026-08-27T00:00:00Z",
            "authoritative": 0,
            "raw": {},
        }
    )
    digest = sha256_text(material)
    for observation_id in ("technocore:chat:7", "technocore:chat:0:7"):
        connection.execute(
            """INSERT INTO observations
            (id, source, external_id, actor_id, actor_username, kind, title, body, url,
             created_at, observed_at, authoritative, raw_json)
            VALUES(?, 'technocore', 'chat:7', 'did:key:z6Mk', 'did:key:z6Mk',
                    'room_message', 'same', 'same', NULL, ?, ?, 0, '{}')""",
            (observation_id, "2026-08-27T00:00:00Z", "2026-08-27T00:00:01Z"),
        )
        connection.execute(
            """INSERT INTO observation_revisions
            (observation_id, revision_digest, material_json, first_seen_at, last_seen_at,
             exposure_class, quarantine_reason, tombstone)
            VALUES(?, ?, ?, ?, ?, 'public', NULL, 0)""",
            (
                observation_id,
                digest,
                material,
                "2026-08-27T00:00:01Z",
                "2026-08-27T00:00:01Z",
            ),
        )
        connection.execute(
            "INSERT INTO observation_heads(observation_id, revision_digest) VALUES(?, ?)",
            (observation_id, digest),
        )
    connection.execute("PRAGMA user_version=2")
    connection.commit()
    connection.close()

    state = State(path)
    ids = [row["id"] for row in state.current_observations()]
    assert ids == ["technocore:chat:7"]


def test_v2_migration_canonicalizes_epoch_zero_id_without_legacy_alias(tmp_path) -> None:
    path = tmp_path / "state.db"
    connection = sqlite3.connect(path)
    connection.executescript(LEGACY_SCHEMA)
    for statement in V2_STATEMENTS:
        connection.execute(statement)
    material = canonical_json(
        {
            "source": "technocore",
            "external_id": "chat:8",
            "actor_id": "did:key:z6Mk",
            "actor_username": "did:key:z6Mk",
            "kind": "room_message",
            "title": "same",
            "body": "same",
            "url": None,
            "created_at": "2026-08-27T00:00:00Z",
            "authoritative": 0,
            "raw": {},
        }
    )
    digest = sha256_text(material)
    connection.execute(
        """INSERT INTO observations
        (id, source, external_id, actor_id, actor_username, kind, title, body, url,
         created_at, observed_at, authoritative, raw_json)
        VALUES('technocore:chat:0:8', 'technocore', 'chat:8', 'did:key:z6Mk',
               'did:key:z6Mk', 'room_message', 'same', 'same', NULL, ?, ?, 0, '{}')""",
        ("2026-08-27T00:00:00Z", "2026-08-27T00:00:01Z"),
    )
    connection.execute(
        """INSERT INTO observation_revisions
        (observation_id, revision_digest, material_json, first_seen_at, last_seen_at,
         exposure_class, quarantine_reason, tombstone)
        VALUES('technocore:chat:0:8', ?, ?, ?, ?, 'public', NULL, 0)""",
        (digest, material, "2026-08-27T00:00:01Z", "2026-08-27T00:00:01Z"),
    )
    connection.execute(
        """INSERT INTO observation_heads(observation_id, revision_digest)
        VALUES('technocore:chat:0:8', ?)""",
        (digest,),
    )
    connection.execute("PRAGMA user_version=2")
    connection.commit()
    connection.close()

    state = State(path)
    assert [row["id"] for row in state.current_observations()] == ["technocore:chat:8"]


def test_pre_release_v3_database_repairs_unpaired_epoch_zero_alias(tmp_path) -> None:
    path = tmp_path / "state.db"
    connection = sqlite3.connect(path)
    connection.executescript(LEGACY_SCHEMA)
    for statement in (*V2_STATEMENTS, *V3_STATEMENTS):
        connection.execute(statement)
    material = canonical_json(
        {
            "source": "technocore",
            "external_id": "chat:10",
            "actor_id": "did:key:z6Mk",
            "actor_username": None,
            "kind": "room_message",
            "title": "same",
            "body": "same",
            "url": None,
            "created_at": "2026-08-27T00:00:00Z",
            "authoritative": 0,
            "source_state": None,
        }
    )
    digest = sha256_text(material)
    connection.execute(
        """INSERT INTO observations
        (id, source, external_id, actor_id, kind, title, body, observed_at,
         authoritative, raw_json)
        VALUES('technocore:chat:0:10', 'technocore', 'chat:10', 'did:key:z6Mk',
               'room_message', 'same', 'same', ?, 0, '{}')""",
        ("2026-08-27T00:00:01Z",),
    )
    connection.execute(
        """INSERT INTO observation_revisions
        (observation_id, revision_digest, material_json, first_seen_at, last_seen_at,
         exposure_class, quarantine_reason, tombstone)
        VALUES('technocore:chat:0:10', ?, ?, ?, ?, 'public', NULL, 0)""",
        (digest, material, "2026-08-27T00:00:01Z", "2026-08-27T00:00:01Z"),
    )
    connection.execute(
        """INSERT INTO observation_heads(observation_id, revision_digest)
        VALUES('technocore:chat:0:10', ?)""",
        (digest,),
    )
    connection.execute("PRAGMA user_version=3")
    connection.commit()
    connection.close()

    state = State(path)
    assert [row["id"] for row in state.current_observations()] == ["technocore:chat:10"]


def test_v2_alias_acknowledgment_follows_equivalent_v3_head(tmp_path) -> None:
    path = tmp_path / "state.db"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(LEGACY_SCHEMA)
    for statement in V2_STATEMENTS:
        connection.execute(statement)
    ids = ("technocore:chat:9", "technocore:chat:0:9")
    digests = []
    for index, observation_id in enumerate(ids):
        connection.execute(
            """INSERT INTO observations
            (id, source, external_id, actor_id, actor_username, kind, title, body, url,
             created_at, observed_at, authoritative, raw_json)
            VALUES(?, 'technocore', 'chat:9', 'did:key:z6Mk', 'did:key:z6Mk',
                    'room_message', 'same', 'same', NULL, ?, ?, 0, ?)""",
            (
                observation_id,
                "2026-08-27T00:00:00Z",
                "2026-08-27T00:00:01Z",
                json.dumps({"variant": index}),
            ),
        )
        material = canonical_json(
            {
                "source": "technocore",
                "external_id": "chat:9",
                "actor_id": "did:key:z6Mk",
                "actor_username": "did:key:z6Mk",
                "kind": "room_message",
                "title": "same",
                "body": "same",
                "url": None,
                "created_at": "2026-08-27T00:00:00Z",
                "authoritative": 0,
                "raw": {"variant": index},
            }
        )
        digest = sha256_text(material)
        digests.append(digest)
        connection.execute(
            """INSERT INTO observation_revisions
            (observation_id, revision_digest, material_json, first_seen_at, last_seen_at,
             exposure_class, quarantine_reason, tombstone)
            VALUES(?, ?, ?, ?, ?, 'public', NULL, 0)""",
            (
                observation_id,
                digest,
                material,
                "2026-08-27T00:00:01Z",
                "2026-08-27T00:00:01Z",
            ),
        )
        connection.execute(
            "INSERT INTO observation_heads(observation_id, revision_digest) VALUES(?, ?)",
            (observation_id, digest),
        )
    connection.execute(
        """INSERT INTO acknowledgments
        (consumer_id, observation_id, revision_digest, acknowledged_at)
        VALUES('agent', ?, ?, ?)""",
        (ids[1], digests[1], "2026-08-27T00:01:00Z"),
    )
    connection.execute("PRAGMA user_version=2")
    connection.commit()
    connection.close()

    state = State(path)
    head = state.current_observation(ids[0])
    assert (ids[0], head["revision_digest"]) in state.acknowledgments("agent")
