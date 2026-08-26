from __future__ import annotations

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


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
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


class State:
    def __init__(self, path: Path):
        self.path = path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
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

    def upsert_observation(self, item: dict[str, Any]) -> bool:
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
        with self.connect() as connection:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO observations
                (id, source, external_id, actor_id, actor_username, kind, title, body, url,
                 created_at, observed_at, authoritative, raw_json)
                VALUES (:id, :source, :external_id, :actor_id, :actor_username, :kind, :title,
                        :body, :url, :created_at, :observed_at, :authoritative, :raw_json)""",
                payload,
            )
        return cursor.rowcount == 1

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
        tables = ("observations", "candidates", "bundles", "actions")
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
