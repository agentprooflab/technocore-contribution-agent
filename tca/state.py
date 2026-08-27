from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_now() -> str:
    return utc_now().isoformat()


SCHEMA_VERSION = 3

LEGACY_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS observations (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    actor_id TEXT,
    actor_username TEXT,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    url TEXT,
    created_at TEXT,
    observed_at TEXT NOT NULL,
    authoritative INTEGER NOT NULL DEFAULT 0,
    raw_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS candidates (
    id TEXT PRIMARY KEY,
    observation_id TEXT NOT NULL REFERENCES observations(id),
    category TEXT NOT NULL,
    priority INTEGER NOT NULL,
    status TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bundles (
    id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES candidates(id),
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    approved_at TEXT,
    published_at TEXT
);
CREATE TABLE IF NOT EXISTS actions (
    bundle_id TEXT NOT NULL REFERENCES bundles(id),
    action_type TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL,
    external_url TEXT,
    last_error TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(bundle_id, action_type, idempotency_key)
);
CREATE TABLE IF NOT EXISTS nonces (
    scope TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);
"""

V2_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS observation_revisions (
        observation_id TEXT NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
        revision_digest TEXT NOT NULL,
        material_json TEXT NOT NULL,
        first_seen_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        exposure_class TEXT NOT NULL DEFAULT 'public',
        quarantine_reason TEXT,
        tombstone INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(observation_id, revision_digest)
    )""",
    """CREATE TABLE IF NOT EXISTS observation_heads (
        observation_id TEXT PRIMARY KEY REFERENCES observations(id) ON DELETE CASCADE,
        revision_digest TEXT NOT NULL,
        FOREIGN KEY(observation_id, revision_digest)
            REFERENCES observation_revisions(observation_id, revision_digest)
    )""",
    """CREATE TABLE IF NOT EXISTS source_cursors (
        source TEXT NOT NULL,
        scope TEXT NOT NULL,
        epoch INTEGER NOT NULL DEFAULT 0,
        cursor TEXT,
        state TEXT NOT NULL DEFAULT 'active',
        not_before TEXT,
        updated_at TEXT NOT NULL,
        PRIMARY KEY(source, scope)
    )""",
    """CREATE TABLE IF NOT EXISTS coverage_ranges (
        source TEXT NOT NULL,
        scope TEXT NOT NULL,
        epoch INTEGER NOT NULL,
        start_value INTEGER NOT NULL,
        end_value INTEGER NOT NULL,
        state TEXT NOT NULL CHECK(state IN
            ('observed', 'pending_fetch', 'unknown_gap', 'confirmed_lost')),
        recorded_at TEXT NOT NULL,
        CHECK(start_value <= end_value),
        PRIMARY KEY(source, scope, epoch, start_value, end_value, state)
    )""",
    """CREATE TABLE IF NOT EXISTS acknowledgments (
        consumer_id TEXT NOT NULL,
        observation_id TEXT NOT NULL,
        revision_digest TEXT NOT NULL,
        acknowledged_at TEXT NOT NULL,
        PRIMARY KEY(consumer_id, observation_id, revision_digest),
        FOREIGN KEY(observation_id, revision_digest)
            REFERENCES observation_revisions(observation_id, revision_digest)
    )""",
    """CREATE TABLE IF NOT EXISTS orientation_cache (
        cache_key TEXT PRIMARY KEY,
        observation_id TEXT NOT NULL,
        revision_digest TEXT NOT NULL,
        coverage_digest TEXT NOT NULL,
        exposure_class TEXT NOT NULL,
        model_id TEXT NOT NULL,
        prompt_digest TEXT NOT NULL,
        policy_version TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('ready', 'failed')),
        created_at TEXT NOT NULL,
        last_accessed_at TEXT NOT NULL,
        hit_count INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY(observation_id, revision_digest)
            REFERENCES observation_revisions(observation_id, revision_digest)
    )""",
    "CREATE INDEX IF NOT EXISTS revisions_last_seen ON observation_revisions(last_seen_at)",
    "CREATE INDEX IF NOT EXISTS coverage_lookup ON coverage_ranges(source, scope, epoch)",
    "CREATE INDEX IF NOT EXISTS acknowledgments_consumer ON acknowledgments(consumer_id)",
)

V3_STATEMENTS = (
    "ALTER TABLE source_cursors ADD COLUMN exposure_class TEXT NOT NULL DEFAULT 'restricted'",
    "ALTER TABLE coverage_ranges ADD COLUMN exposure_class TEXT NOT NULL DEFAULT 'restricted'",
)


class StaleCursorError(RuntimeError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _material_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": payload["source"],
        "external_id": payload["external_id"],
        "actor_id": payload.get("actor_id"),
        "actor_username": payload.get("actor_username"),
        "kind": payload["kind"],
        "title": payload.get("title", ""),
        "body": payload.get("body", ""),
        "url": payload.get("url"),
        "created_at": payload.get("created_at"),
        "authoritative": int(bool(payload.get("authoritative"))),
        "source_state": payload.get("source_state"),
    }


class State:
    def __init__(self, path: Path):
        self.path = path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            self._migrate(connection)

    def _migrate(self, connection: sqlite3.Connection) -> None:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            raise RuntimeError(
                f"state database schema {version} is newer than supported {SCHEMA_VERSION}"
            )
        if version == 0:
            connection.executescript(LEGACY_SCHEMA)
            connection.execute("PRAGMA user_version=1")
            version = 1
        if version == 1:
            connection.execute("BEGIN IMMEDIATE")
            try:
                for statement in V2_STATEMENTS:
                    connection.execute(statement)
                connection.execute("PRAGMA user_version=2")
                connection.commit()
                version = 2
            except Exception:
                connection.rollback()
                raise
        if version == 2:
            self._migrate_v3(connection)
        self._repair_epoch_zero_aliases(connection)
        self._backfill_revisions(connection)

    def _migrate_v3(self, connection: sqlite3.Connection) -> None:
        connection.execute("BEGIN IMMEDIATE")
        try:
            for statement in V3_STATEMENTS:
                connection.execute(statement)
            # V2 predates exposure labels. Restore only structurally known-public built-in
            # adapter scopes; private Technocore prefixes and unknown adapters remain closed.
            public_scope = """source IN ('x', 'github') OR (
                source = 'technocore'
                AND scope NOT GLOB 'p-*'
                AND scope NOT GLOB 'mb-*'
                AND scope NOT GLOB 'e-p-*'
                AND scope NOT GLOB 'mb-p-*'
            )"""
            connection.execute(
                f"UPDATE source_cursors SET exposure_class = 'public' WHERE {public_scope}"
            )
            connection.execute(
                f"UPDATE coverage_ranges SET exposure_class = 'public' WHERE {public_scope}"
            )
            epoch_zero_rows = connection.execute(
                """SELECT id, external_id FROM observations
                WHERE source = 'technocore' AND id GLOB 'technocore:*:0:*'"""
            ).fetchall()
            for epoch_zero in epoch_zero_rows:
                canonical_id = f"technocore:{epoch_zero['external_id']}"
                canonical_exists = connection.execute(
                    "SELECT 1 FROM observations WHERE id = ?", (canonical_id,)
                ).fetchone()
                if not canonical_exists:
                    connection.execute(
                        """INSERT INTO observations
                        (id, source, external_id, actor_id, actor_username, kind, title, body,
                         url, created_at, observed_at, authoritative, raw_json)
                        SELECT ?, source, external_id, actor_id, actor_username, kind, title, body,
                               url, created_at, observed_at, authoritative, raw_json
                        FROM observations WHERE id = ?""",
                        (canonical_id, epoch_zero["id"]),
                    )
                connection.execute(
                    """INSERT INTO observation_revisions
                    (observation_id, revision_digest, material_json, first_seen_at, last_seen_at,
                     exposure_class, quarantine_reason, tombstone)
                    SELECT ?, revision_digest, material_json, first_seen_at, last_seen_at,
                    exposure_class, quarantine_reason, tombstone
                    FROM observation_revisions WHERE observation_id = ?
                    ON CONFLICT(observation_id, revision_digest) DO UPDATE SET
                    first_seen_at=MIN(observation_revisions.first_seen_at, excluded.first_seen_at),
                    last_seen_at=MAX(observation_revisions.last_seen_at, excluded.last_seen_at),
                    exposure_class=CASE
                        WHEN observation_revisions.exposure_class='restricted'
                          OR excluded.exposure_class='restricted'
                        THEN 'restricted' ELSE 'public' END,
                    quarantine_reason=COALESCE(
                        observation_revisions.quarantine_reason, excluded.quarantine_reason),
                    tombstone=MAX(observation_revisions.tombstone, excluded.tombstone)""",
                    (canonical_id, epoch_zero["id"]),
                )
                connection.execute(
                    """INSERT OR IGNORE INTO observation_heads(observation_id, revision_digest)
                    SELECT ?, revision_digest FROM observation_heads
                    WHERE observation_id = ?""",
                    (canonical_id, epoch_zero["id"]),
                )
                connection.execute(
                    "UPDATE candidates SET observation_id = ? WHERE observation_id = ?",
                    (canonical_id, epoch_zero["id"]),
                )
                connection.execute(
                    """INSERT OR IGNORE INTO acknowledgments
                    (consumer_id, observation_id, revision_digest, acknowledged_at)
                    SELECT consumer_id, ?, revision_digest, acknowledged_at
                    FROM acknowledgments WHERE observation_id = ?""",
                    (canonical_id, epoch_zero["id"]),
                )
                connection.execute(
                    "DELETE FROM acknowledgments WHERE observation_id = ?", (epoch_zero["id"],)
                )
                connection.execute(
                    "DELETE FROM orientation_cache WHERE observation_id = ?", (epoch_zero["id"],)
                )
                connection.execute("DELETE FROM observations WHERE id = ?", (epoch_zero["id"],))

            revision_rows = connection.execute(
                """SELECT r.*, CASE WHEN h.revision_digest = r.revision_digest
                    THEN 1 ELSE 0 END AS is_head
                FROM observation_revisions r
                LEFT JOIN observation_heads h ON h.observation_id = r.observation_id"""
            ).fetchall()
            for row in revision_rows:
                old_material = json.loads(row["material_json"])
                raw = old_material.get("raw")
                source_state = old_material.get("source_state")
                if source_state is None and isinstance(raw, dict):
                    source_state = raw.get("state")
                payload = {**old_material, "source_state": source_state}
                material_json = canonical_json(_material_from_payload(payload))
                new_digest = sha256_text(material_json)
                if new_digest == row["revision_digest"]:
                    continue
                connection.execute(
                    """INSERT INTO observation_revisions
                    (observation_id, revision_digest, material_json, first_seen_at, last_seen_at,
                     exposure_class, quarantine_reason, tombstone)
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(observation_id, revision_digest) DO UPDATE SET
                    first_seen_at=MIN(observation_revisions.first_seen_at, excluded.first_seen_at),
                    last_seen_at=MAX(observation_revisions.last_seen_at, excluded.last_seen_at),
                    exposure_class=CASE
                        WHEN observation_revisions.exposure_class='restricted'
                          OR excluded.exposure_class='restricted'
                        THEN 'restricted' ELSE 'public' END,
                    quarantine_reason=COALESCE(
                        observation_revisions.quarantine_reason, excluded.quarantine_reason),
                    tombstone=MAX(observation_revisions.tombstone, excluded.tombstone)""",
                    (
                        row["observation_id"],
                        new_digest,
                        material_json,
                        row["first_seen_at"],
                        row["last_seen_at"],
                        row["exposure_class"],
                        row["quarantine_reason"],
                        row["tombstone"],
                    ),
                )
                connection.execute(
                    """INSERT OR IGNORE INTO acknowledgments
                    (consumer_id, observation_id, revision_digest, acknowledged_at)
                    SELECT consumer_id, observation_id, ?, acknowledged_at
                    FROM acknowledgments
                    WHERE observation_id = ? AND revision_digest = ?""",
                    (new_digest, row["observation_id"], row["revision_digest"]),
                )
                if row["is_head"]:
                    connection.execute(
                        """UPDATE observation_heads SET revision_digest = ?
                        WHERE observation_id = ? AND revision_digest = ?""",
                        (new_digest, row["observation_id"], row["revision_digest"]),
                    )
            connection.execute("PRAGMA user_version=3")
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def _repair_epoch_zero_aliases(self, connection: sqlite3.Connection) -> None:
        """Repair databases opened by pre-release v3 code that skipped unpaired aliases."""
        rows = connection.execute(
            """SELECT id, external_id FROM observations
            WHERE source = 'technocore' AND id GLOB 'technocore:*:0:*'"""
        ).fetchall()
        if not rows:
            return
        connection.execute("BEGIN IMMEDIATE")
        try:
            for row in rows:
                old_id = str(row["id"])
                canonical_id = f"technocore:{row['external_id']}"
                canonical_exists = connection.execute(
                    "SELECT 1 FROM observations WHERE id = ?", (canonical_id,)
                ).fetchone()
                if not canonical_exists:
                    connection.execute(
                        """INSERT INTO observations
                        (id, source, external_id, actor_id, actor_username, kind, title, body,
                         url, created_at, observed_at, authoritative, raw_json)
                        SELECT ?, source, external_id, actor_id, actor_username, kind, title, body,
                               url, created_at, observed_at, authoritative, raw_json
                        FROM observations WHERE id = ?""",
                        (canonical_id, old_id),
                    )
                connection.execute(
                    """INSERT INTO observation_revisions
                    (observation_id, revision_digest, material_json, first_seen_at, last_seen_at,
                     exposure_class, quarantine_reason, tombstone)
                    SELECT ?, revision_digest, material_json, first_seen_at, last_seen_at,
                           exposure_class, quarantine_reason, tombstone
                    FROM observation_revisions WHERE observation_id = ?
                    ON CONFLICT(observation_id, revision_digest) DO UPDATE SET
                    first_seen_at=MIN(observation_revisions.first_seen_at, excluded.first_seen_at),
                    last_seen_at=MAX(observation_revisions.last_seen_at, excluded.last_seen_at),
                    exposure_class=CASE
                        WHEN observation_revisions.exposure_class='restricted'
                          OR excluded.exposure_class='restricted'
                        THEN 'restricted' ELSE 'public' END,
                    quarantine_reason=COALESCE(
                        observation_revisions.quarantine_reason, excluded.quarantine_reason),
                    tombstone=MAX(observation_revisions.tombstone, excluded.tombstone)""",
                    (canonical_id, old_id),
                )
                connection.execute(
                    """INSERT OR IGNORE INTO observation_heads(observation_id, revision_digest)
                    SELECT ?, revision_digest FROM observation_heads
                    WHERE observation_id = ?""",
                    (canonical_id, old_id),
                )
                connection.execute(
                    "UPDATE candidates SET observation_id = ? WHERE observation_id = ?",
                    (canonical_id, old_id),
                )
                connection.execute(
                    """INSERT OR IGNORE INTO acknowledgments
                    (consumer_id, observation_id, revision_digest, acknowledged_at)
                    SELECT consumer_id, ?, revision_digest, acknowledged_at
                    FROM acknowledgments WHERE observation_id = ?""",
                    (canonical_id, old_id),
                )
                connection.execute(
                    "DELETE FROM acknowledgments WHERE observation_id = ?", (old_id,)
                )
                connection.execute(
                    "DELETE FROM orientation_cache WHERE observation_id = ?", (old_id,)
                )
                connection.execute("DELETE FROM observations WHERE id = ?", (old_id,))
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def _backfill_revisions(self, connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            """SELECT o.* FROM observations o
            LEFT JOIN observation_heads h ON h.observation_id = o.id
            WHERE h.observation_id IS NULL"""
        ).fetchall()
        for row in rows:
            raw = json.loads(row["raw_json"])
            payload = {**dict(row), "raw": raw, "authoritative": bool(row["authoritative"])}
            material_json = canonical_json(_material_from_payload(payload))
            digest = sha256_text(material_json)
            connection.execute(
                """INSERT OR IGNORE INTO observation_revisions
                (observation_id, revision_digest, material_json, first_seen_at, last_seen_at,
                 exposure_class, quarantine_reason, tombstone)
                VALUES(?, ?, ?, ?, ?, ?, ?, 0)""",
                (
                    row["id"],
                    digest,
                    material_json,
                    row["observed_at"],
                    row["observed_at"],
                    self._exposure_class(payload),
                    self._quarantine_reason(payload),
                ),
            )
            connection.execute(
                """INSERT OR REPLACE INTO observation_heads
                (observation_id, revision_digest) VALUES(?, ?)""",
                (row["id"], digest),
            )

    @staticmethod
    def _exposure_class(payload: dict[str, Any]) -> str:
        external = str(payload.get("external_id", ""))
        source = payload.get("source")
        explicit = payload.get("exposure_class")
        if source == "technocore":
            room = external.split(":", 1)[0]
            if room.startswith(("p-", "mb-", "e-p-", "mb-p-")):
                return "restricted"
            return "restricted" if explicit == "restricted" else "public"
        if explicit == "restricted":
            return "restricted"
        if source in {"x", "github"}:
            return "public"
        return "restricted"

    @staticmethod
    def _quarantine_reason(payload: dict[str, Any]) -> str | None:
        explicit = payload.get("quarantine_reason")
        if explicit:
            return str(explicit)
        raw = payload.get("raw") or {}
        findings = raw.get("safety_findings", []) if isinstance(raw, dict) else []
        return ",".join(sorted(str(item) for item in findings)) or None

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def get_meta(self, key: str) -> str | None:
        with self.connect() as connection:
            row = connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def ensure_shadow_started(self, now: datetime | None = None) -> datetime:
        existing = self.get_meta("shadow_started_at")
        if existing:
            return datetime.fromisoformat(existing)
        started = now or utc_now()
        self.set_meta("shadow_started_at", started.isoformat())
        return started

    def shadow_remaining(self, hours: int, now: datetime | None = None) -> timedelta:
        started = self.ensure_shadow_started(now)
        deadline = started + timedelta(hours=hours)
        return max(deadline - (now or utc_now()), timedelta())

    def shadow_complete(self, hours: int, now: datetime | None = None) -> bool:
        return self.shadow_remaining(hours, now) == timedelta()

    def _upsert_observation(
        self, connection: sqlite3.Connection, item: dict[str, Any]
    ) -> tuple[bool, str]:
        payload = {
            "id": item["id"],
            "source": item["source"],
            "external_id": item["external_id"],
            "actor_id": item.get("actor_id"),
            "actor_username": item.get("actor_username"),
            "kind": item["kind"],
            "title": item.get("title", ""),
            "body": item.get("body", ""),
            "url": item.get("url"),
            "created_at": item.get("created_at"),
            "observed_at": item.get("observed_at", iso_now()),
            "authoritative": int(bool(item.get("authoritative"))),
            "raw_json": json.dumps(item.get("raw", item), sort_keys=True),
        }
        material_json = canonical_json(_material_from_payload(item))
        revision_digest = sha256_text(material_json)
        existing_head = connection.execute(
            """SELECT h.revision_digest, r.exposure_class, r.quarantine_reason
            FROM observation_heads h
            JOIN observation_revisions r ON r.observation_id = h.observation_id
                AND r.revision_digest = h.revision_digest
            WHERE h.observation_id = ?""",
            (payload["id"],),
        ).fetchone()
        requested_exposure = self._exposure_class(item)
        effective_exposure = (
            "restricted"
            if requested_exposure == "restricted"
            or (existing_head and existing_head["exposure_class"] == "restricted")
            else "public"
        )
        if existing_head and existing_head["revision_digest"] == revision_digest:
            quarantine = self._quarantine_reason(item)
            connection.execute(
                """UPDATE observation_revisions SET last_seen_at = ?,
                exposure_class = CASE WHEN exposure_class = 'restricted' OR ? = 'restricted'
                    THEN 'restricted' ELSE 'public' END,
                quarantine_reason = COALESCE(?, quarantine_reason)
                WHERE observation_id = ? AND revision_digest = ?""",
                (
                    payload["observed_at"],
                    effective_exposure,
                    quarantine,
                    payload["id"],
                    revision_digest,
                ),
            )
            return False, revision_digest

        logical_inserted = existing_head is None
        if logical_inserted:
            connection.execute(
                """INSERT OR IGNORE INTO observations
                (id, source, external_id, actor_id, actor_username, kind, title, body, url,
                 created_at, observed_at, authoritative, raw_json)
                VALUES (:id, :source, :external_id, :actor_id, :actor_username, :kind, :title,
                        :body, :url, :created_at, :observed_at, :authoritative, :raw_json)""",
                payload,
            )
        connection.execute(
            """INSERT OR IGNORE INTO observation_revisions
            (observation_id, revision_digest, material_json, first_seen_at, last_seen_at,
             exposure_class, quarantine_reason, tombstone)
            VALUES(?, ?, ?, ?, ?, ?, ?, 0)""",
            (
                payload["id"],
                revision_digest,
                material_json,
                payload["observed_at"],
                payload["observed_at"],
                effective_exposure,
                self._quarantine_reason(item),
            ),
        )

        immutable_conflict = bool(existing_head and payload["source"] == "technocore")
        if immutable_conflict:
            connection.execute(
                """UPDATE observation_revisions SET quarantine_reason = 'immutable_conflict'
                WHERE observation_id = ? AND revision_digest = ?""",
                (payload["id"], revision_digest),
            )
            connection.execute(
                """UPDATE observation_revisions SET
                exposure_class = CASE WHEN exposure_class = 'restricted' OR ? = 'restricted'
                    THEN 'restricted' ELSE 'public' END,
                quarantine_reason = COALESCE(quarantine_reason, 'immutable_conflict')
                WHERE observation_id = ? AND revision_digest = ?""",
                (effective_exposure, payload["id"], existing_head["revision_digest"]),
            )
            return True, existing_head["revision_digest"]

        if existing_head:
            connection.execute(
                """UPDATE observations SET actor_id=:actor_id, actor_username=:actor_username,
                kind=:kind, title=:title, body=:body, url=:url, created_at=:created_at,
                observed_at=:observed_at, authoritative=:authoritative, raw_json=:raw_json
                WHERE id=:id""",
                payload,
            )
        connection.execute(
            """INSERT INTO observation_heads(observation_id, revision_digest) VALUES(?, ?)
            ON CONFLICT(observation_id) DO UPDATE SET revision_digest=excluded.revision_digest""",
            (payload["id"], revision_digest),
        )
        return True, revision_digest

    def upsert_observation(self, item: dict[str, Any]) -> bool:
        with self.connect() as connection:
            changed, _ = self._upsert_observation(connection, item)
        return changed

    def current_observations(self, public_only: bool = False) -> list[sqlite3.Row]:
        where = "WHERE r.exposure_class = 'public'" if public_only else ""
        with self.connect() as connection:
            return list(
                connection.execute(
                    f"""SELECT o.*, h.revision_digest, r.exposure_class,
                r.quarantine_reason, r.tombstone
                FROM observations o
                JOIN observation_heads h ON h.observation_id = o.id
                JOIN observation_revisions r ON r.observation_id = h.observation_id
                    AND r.revision_digest = h.revision_digest
                {where}
                ORDER BY o.observed_at, o.source, o.external_id"""
                )
            )

    def current_observation(self, observation_id: str) -> sqlite3.Row:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT o.*, h.revision_digest, r.exposure_class,
                r.quarantine_reason, r.tombstone
                FROM observations o
                JOIN observation_heads h ON h.observation_id = o.id
                JOIN observation_revisions r ON r.observation_id = h.observation_id
                    AND r.revision_digest = h.revision_digest
                WHERE o.id = ?""",
                (observation_id,),
            ).fetchone()
        if not row:
            raise KeyError(f"unknown observation: {observation_id}")
        return row

    def observation_revision(self, observation_id: str, revision_digest: str) -> sqlite3.Row:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT * FROM observation_revisions
                WHERE observation_id = ? AND revision_digest = ?""",
                (observation_id, revision_digest),
            ).fetchone()
        if not row:
            raise KeyError(f"unknown observation revision: {observation_id}@{revision_digest}")
        return row

    def source_cursor(self, source: str, scope: str) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM source_cursors WHERE source = ? AND scope = ?",
                (source, scope),
            ).fetchone()

    @staticmethod
    def _normalize_ranges(ranges: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
        if not ranges:
            return []
        priority = {"pending_fetch": 1, "unknown_gap": 2, "confirmed_lost": 3, "observed": 4}
        boundaries = sorted({value for start, end, _ in ranges for value in (start, end + 1)})
        pieces: list[tuple[int, int, str]] = []
        for left, right_exclusive in zip(boundaries, boundaries[1:], strict=False):
            states = [
                state
                for start, end, state in ranges
                if start <= left and end >= right_exclusive - 1
            ]
            if not states:
                continue
            state = max(states, key=priority.__getitem__)
            right = right_exclusive - 1
            if pieces and pieces[-1][2] == state and pieces[-1][1] + 1 == left:
                pieces[-1] = (pieces[-1][0], right, state)
            else:
                pieces.append((left, right, state))
        return pieces

    def _replace_coverage_ranges(
        self,
        connection: sqlite3.Connection,
        source: str,
        scope: str,
        epoch: int,
        additions: list[tuple[int, int, str]],
        recorded_at: str,
        exposure_class: str,
    ) -> None:
        existing_rows = list(
            connection.execute(
                """SELECT start_value, end_value, state, exposure_class FROM coverage_ranges
                WHERE source = ? AND scope = ? AND epoch = ?""",
                (source, scope, epoch),
            )
        )
        existing = [
            (int(row["start_value"]), int(row["end_value"]), str(row["state"]))
            for row in existing_rows
        ]
        effective_exposure = (
            "restricted"
            if exposure_class == "restricted"
            or any(row["exposure_class"] == "restricted" for row in existing_rows)
            else "public"
        )
        normalized = self._normalize_ranges(existing + additions)
        connection.execute(
            "DELETE FROM coverage_ranges WHERE source = ? AND scope = ? AND epoch = ?",
            (source, scope, epoch),
        )
        connection.executemany(
            """INSERT INTO coverage_ranges
            (source, scope, epoch, start_value, end_value, state, recorded_at, exposure_class)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (source, scope, epoch, start, end, state, recorded_at, effective_exposure)
                for start, end, state in normalized
            ],
        )

    def commit_observation_page(
        self,
        *,
        source: str,
        scope: str,
        epoch: int,
        expected_cursor: str | None,
        observations: list[dict[str, Any]],
        coverage_ranges: list[tuple[int, int, str]],
        next_cursor: str | None,
        cursor_state: str = "active",
        not_before: str | None = None,
        exposure_class: str = "public",
    ) -> int:
        recorded_at = iso_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT cursor, epoch, exposure_class FROM source_cursors
                WHERE source = ? AND scope = ?""",
                (source, scope),
            ).fetchone()
            current = row["cursor"] if row else None
            current_epoch = int(row["epoch"]) if row else epoch
            if current != expected_cursor or current_epoch != epoch:
                raise StaleCursorError(
                    f"stale cursor for {source}:{scope}: expected {expected_cursor!r}, "
                    f"found {current!r} at epoch {current_epoch}"
                )
            effective_exposure = (
                "restricted"
                if exposure_class == "restricted" or (row and row["exposure_class"] == "restricted")
                else "public"
            )
            changed = 0
            for item in observations:
                item_changed, _ = self._upsert_observation(connection, item)
                changed += int(item_changed)
            self._replace_coverage_ranges(
                connection,
                source,
                scope,
                epoch,
                coverage_ranges,
                recorded_at,
                effective_exposure,
            )
            connection.execute(
                """INSERT INTO source_cursors
                (source, scope, epoch, cursor, state, not_before, updated_at, exposure_class)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, scope) DO UPDATE SET epoch=excluded.epoch,
                cursor=excluded.cursor, state=excluded.state, not_before=excluded.not_before,
                updated_at=excluded.updated_at,
                exposure_class=CASE
                    WHEN source_cursors.exposure_class='restricted'
                      OR excluded.exposure_class='restricted'
                    THEN 'restricted' ELSE 'public' END""",
                (
                    source,
                    scope,
                    epoch,
                    next_cursor,
                    cursor_state,
                    not_before,
                    recorded_at,
                    effective_exposure,
                ),
            )
        return changed

    def recover_ambiguous_epoch(
        self,
        *,
        source: str,
        scope: str,
        expected_epoch: int,
        expected_cursor: str,
        exposure_class: str = "public",
    ) -> int:
        """Advance a confirmed rewind to a fresh local epoch without joining coverage."""
        recovered_at = iso_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT epoch, cursor, state, exposure_class FROM source_cursors
                WHERE source = ? AND scope = ?""",
                (source, scope),
            ).fetchone()
            if (
                not row
                or int(row["epoch"]) != expected_epoch
                or row["cursor"] != expected_cursor
                or row["state"] != "epoch_ambiguous"
            ):
                found = (
                    "missing"
                    if not row
                    else f"epoch={row['epoch']} cursor={row['cursor']!r} state={row['state']}"
                )
                raise StaleCursorError(
                    f"ambiguous epoch changed for {source}:{scope}; found {found}"
                )
            next_epoch = expected_epoch + 1
            effective_exposure = (
                "restricted"
                if exposure_class == "restricted" or row["exposure_class"] == "restricted"
                else "public"
            )
            updated = connection.execute(
                """UPDATE source_cursors SET epoch = ?, cursor = NULL, state = 'active',
                not_before = NULL, updated_at = ?, exposure_class = ?
                WHERE source = ? AND scope = ? AND epoch = ? AND cursor = ?
                    AND state = 'epoch_ambiguous'""",
                (
                    next_epoch,
                    recovered_at,
                    effective_exposure,
                    source,
                    scope,
                    expected_epoch,
                    expected_cursor,
                ),
            )
            if updated.rowcount != 1:
                raise StaleCursorError(f"failed to recover ambiguous epoch for {source}:{scope}")
        return next_epoch

    def set_source_health(
        self,
        source: str,
        scope: str,
        health: str,
        *,
        exposure_class: str = "public",
        not_before: str | None = None,
    ) -> None:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT epoch, cursor, exposure_class FROM source_cursors
                WHERE source = ? AND scope = ?""",
                (source, scope),
            ).fetchone()
            effective_exposure = (
                "restricted"
                if exposure_class == "restricted" or (row and row["exposure_class"] == "restricted")
                else "public"
            )
            connection.execute(
                """INSERT INTO source_cursors
                (source, scope, epoch, cursor, state, not_before, updated_at, exposure_class)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, scope) DO UPDATE SET state=excluded.state,
                not_before=excluded.not_before, updated_at=excluded.updated_at,
                exposure_class=CASE
                    WHEN source_cursors.exposure_class='restricted'
                      OR excluded.exposure_class='restricted'
                    THEN 'restricted' ELSE 'public' END""",
                (
                    source,
                    scope,
                    int(row["epoch"]) if row else 0,
                    row["cursor"] if row else None,
                    health,
                    not_before,
                    iso_now(),
                    effective_exposure,
                ),
            )

    def coverage_rows(self) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    "SELECT * FROM coverage_ranges ORDER BY source, scope, epoch, start_value"
                )
            )

    def cursors(self) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(connection.execute("SELECT * FROM source_cursors ORDER BY source, scope"))

    def acknowledge(self, consumer_id: str, items: list[tuple[str, str]]) -> int:
        acknowledged_at = iso_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            count = 0
            for observation_id, digest in items:
                exists = connection.execute(
                    """SELECT 1 FROM observation_revisions
                    WHERE observation_id = ? AND revision_digest = ?""",
                    (observation_id, digest),
                ).fetchone()
                if not exists:
                    raise KeyError(f"unknown observation revision: {observation_id}@{digest}")
                cursor = connection.execute(
                    """INSERT OR IGNORE INTO acknowledgments
                    (consumer_id, observation_id, revision_digest, acknowledged_at)
                    VALUES(?, ?, ?, ?)""",
                    (consumer_id, observation_id, digest, acknowledged_at),
                )
                count += cursor.rowcount
        return count

    def acknowledgments(self, consumer_id: str) -> set[tuple[str, str]]:
        with self.connect() as connection:
            return {
                (str(row["observation_id"]), str(row["revision_digest"]))
                for row in connection.execute(
                    """SELECT observation_id, revision_digest FROM acknowledgments
                    WHERE consumer_id = ?""",
                    (consumer_id,),
                )
            }

    def put_orientation(
        self,
        *,
        cache_key: str,
        observation_id: str,
        revision_digest: str,
        coverage_digest: str,
        exposure_class: str,
        model_id: str,
        prompt_digest: str,
        policy_version: str,
        payload: dict[str, Any],
    ) -> None:
        now = iso_now()
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO orientation_cache
                (cache_key, observation_id, revision_digest, coverage_digest, exposure_class,
                 model_id, prompt_digest, policy_version, payload_json, status, created_at,
                 last_accessed_at, hit_count)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 'ready', ?, ?, 0)
                ON CONFLICT(cache_key) DO UPDATE SET payload_json=excluded.payload_json,
                status='ready', last_accessed_at=excluded.last_accessed_at""",
                (
                    cache_key,
                    observation_id,
                    revision_digest,
                    coverage_digest,
                    exposure_class,
                    model_id,
                    prompt_digest,
                    policy_version,
                    canonical_json(payload),
                    now,
                    now,
                ),
            )

    def orientation(self, cache_key: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT payload_json FROM orientation_cache
                WHERE cache_key = ? AND status = 'ready'""",
                (cache_key,),
            ).fetchone()
            if not row:
                return None
            connection.execute(
                """UPDATE orientation_cache SET hit_count = hit_count + 1,
                last_accessed_at = ? WHERE cache_key = ?""",
                (iso_now(), cache_key),
            )
        return json.loads(row["payload_json"])

    def observations_without_candidates(self) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    """SELECT o.* FROM observations o
                    LEFT JOIN candidates c ON c.observation_id = o.id
                    WHERE c.id IS NULL ORDER BY o.observed_at"""
                )
            )

    def observations(self) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(connection.execute("SELECT * FROM observations ORDER BY observed_at"))

    def add_candidate(self, item: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO candidates
                (id, observation_id, category, priority, status, reason, created_at)
                VALUES (:id, :observation_id, :category, :priority, :status, :reason,
                        :created_at)
                ON CONFLICT(id) DO UPDATE SET category=excluded.category,
                priority=excluded.priority, status=excluded.status, reason=excluded.reason""",
                item,
            )

    def list_candidates(self, status: str | None = None) -> list[sqlite3.Row]:
        query = "SELECT * FROM candidates"
        params: tuple[str, ...] = ()
        if status:
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY priority DESC, created_at ASC"
        with self.connect() as connection:
            return list(connection.execute(query, params))

    def set_candidate_status(self, candidate_id: str, status: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE candidates SET status = ? WHERE id = ?", (status, candidate_id)
            )

    def candidate(self, candidate_id: str) -> sqlite3.Row:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT c.*, o.source, o.external_id, o.actor_id, o.actor_username,
                o.kind AS observation_kind, o.title, o.body, o.url, o.authoritative, o.raw_json
                FROM candidates c JOIN observations o ON o.id = c.observation_id
                WHERE c.id = ?""",
                (candidate_id,),
            ).fetchone()
        if not row:
            raise KeyError(f"unknown candidate: {candidate_id}")
        return row

    def add_bundle(self, item: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO bundles(id, candidate_id, path, sha256, status, created_at)
                VALUES (:id, :candidate_id, :path, :sha256, :status, :created_at)""",
                item,
            )

    def bundle(self, bundle_id: str) -> sqlite3.Row:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM bundles WHERE id = ?", (bundle_id,)).fetchone()
        if not row:
            raise KeyError(f"unknown bundle: {bundle_id}")
        return row

    def counts(self) -> dict[str, int]:
        tables = (
            "observations",
            "observation_revisions",
            "candidates",
            "bundles",
            "actions",
            "acknowledgments",
            "coverage_ranges",
        )
        with self.connect() as connection:
            return {
                table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in tables
            }

    def ordinary_batches_since(self, since: datetime) -> int:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT COUNT(*) FROM bundles b JOIN candidates c ON c.id = b.candidate_id
                WHERE b.published_at >= ? AND c.category != 'official_task'""",
                (since.isoformat(),),
            ).fetchone()
        return int(row[0])

    def set_bundle_status(self, bundle_id: str, status: str) -> None:
        now = iso_now()
        approved = now if status == "approved" else None
        published = now if status == "published" else None
        with self.connect() as connection:
            connection.execute(
                """UPDATE bundles SET status = ?,
                approved_at = COALESCE(approved_at, ?),
                published_at = COALESCE(published_at, ?)
                WHERE id = ?""",
                (status, approved, published, bundle_id),
            )

    def action(self, bundle_id: str, action_type: str, key: str) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                """SELECT * FROM actions
                WHERE bundle_id = ? AND action_type = ? AND idempotency_key = ?""",
                (bundle_id, action_type, key),
            ).fetchone()

    def set_action(
        self,
        bundle_id: str,
        action_type: str,
        key: str,
        status: str,
        external_url: str | None = None,
        error: str | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO actions(bundle_id, action_type, idempotency_key, status,
                external_url, last_error, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(bundle_id, action_type, idempotency_key) DO UPDATE SET
                status=excluded.status, external_url=excluded.external_url,
                last_error=excluded.last_error, updated_at=excluded.updated_at""",
                (bundle_id, action_type, key, status, external_url, error, iso_now()),
            )

    def next_nonce(self, scope: str, floor: int | None = None) -> int:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT value FROM nonces WHERE scope = ?", (scope,)
            ).fetchone()
            current = row["value"] if row else 0
            candidate = max(current + 1, floor or 0)
            connection.execute(
                "INSERT INTO nonces(scope, value) VALUES(?, ?) "
                "ON CONFLICT(scope) DO UPDATE SET value = excluded.value",
                (scope, candidate),
            )
        return candidate
