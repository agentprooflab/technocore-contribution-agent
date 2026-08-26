from __future__ import annotations

import base64
import hashlib
import json
import math
import re
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from tca.state import State, canonical_json, iso_now, sha256_text

BRIEF_SCHEMA = "technocore-context-brief/v1"
EXPANSION_SCHEMA = "technocore-context-expansion/v1"
ERROR_SCHEMA = "technocore-context-error/v1"
BUDGET_METHOD = "canonical-utf8-div3-v1"
CONSUMER_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
REF_RE = re.compile(r"^(?P<observation>.+)@(?P<digest>[0-9a-f]{64})$")


class ContextError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.details = details or {}

    def payload(self) -> dict[str, Any]:
        return {
            "schema": ERROR_SCHEMA,
            "error": {
                "code": self.code,
                "message": str(self),
                "retryable": self.retryable,
                "details": self.details,
            },
        }


def budget_units(value: Any) -> int:
    return math.ceil(len(canonical_json(value).encode("utf-8")) / 3)


def _with_budget_used(payload: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(canonical_json(payload))
    previous = -1
    used = 0
    while previous != used:
        previous = used
        result["budget"]["estimated_used"] = used
        used = budget_units(result)
    result["budget"]["estimated_used"] = used
    return result


def _validate_consumer(consumer_id: str) -> str:
    normalized = consumer_id.strip().lower()
    if not CONSUMER_RE.fullmatch(normalized):
        raise ContextError(
            "INVALID_CONSUMER_ID",
            "consumer id must match [a-z0-9][a-z0-9_.-]{0,63}",
        )
    return normalized


def _normalized_interests(interests: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    values = {value.strip().lower() for value in interests or () if value.strip()}
    if any(len(value) > 80 for value in values) or len(values) > 32:
        raise ContextError("INVALID_INTERESTS", "at most 32 interests of 80 characters are allowed")
    return tuple(sorted(values))


def _priority_and_reasons(
    row: Any, mention_markers: tuple[str, ...], interests: tuple[str, ...]
) -> tuple[int, list[str]]:
    text = f"{row['title']}\n{row['body']}".lower()
    priority = 0
    reasons: list[str] = []
    if row["source"] == "x" and row["kind"] == "official_task" and row["authoritative"]:
        priority = 100
        reasons.append("official_task")
    elif row["source"] == "github" and row["authoritative"] and row["kind"] == "issue":
        priority = 75
        reasons.append("maintainer_request")
    elif row["source"] == "x" and row["authoritative"]:
        priority = 70
        reasons.append("official_announcement")

    if any(
        re.search(rf"(?<![\w]){re.escape(marker.lower())}(?![\w])", text)
        for marker in mention_markers
    ):
        priority = max(priority, 90)
        reasons.append("mentions_consumer")
    if row["kind"] == "technical_question":
        priority = max(priority, 50)
        reasons.append("question_observed")
    matched = [interest for interest in interests if interest in text]
    if matched:
        priority = max(priority, 40)
        reasons.extend(f"matches_interest:{interest}" for interest in matched[:4])
    if not reasons and row["source"] == "technocore":
        priority = 10
        reasons.append("new_room_delta")
    return priority, reasons


def _excerpt(text: str, limit: int = 280) -> str:
    return text if len(text) <= limit else text[:limit]


def coverage_report(
    state: State, *, include_ranges: bool = True, public_only: bool = True
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, int], list[Any]] = defaultdict(list)
    for row in state.coverage_rows():
        if public_only and row["exposure_class"] != "public":
            continue
        grouped[(str(row["source"]), str(row["scope"]), int(row["epoch"]))].append(row)
    cursors = {
        (str(row["source"]), str(row["scope"])): row
        for row in state.cursors()
        if not public_only or row["exposure_class"] == "public"
    }
    reports: list[dict[str, Any]] = []
    for (source, scope, epoch), rows in sorted(grouped.items()):
        counts = {
            name: 0 for name in ("observed", "pending_fetch", "unknown_gap", "confirmed_lost")
        }
        ranges: list[dict[str, Any]] = []
        for row in rows:
            count = int(row["end_value"]) - int(row["start_value"]) + 1
            counts[str(row["state"])] += count
            ranges.append(
                {
                    "start": int(row["start_value"]),
                    "end": int(row["end_value"]),
                    "state": str(row["state"]),
                }
            )
        cursor = cursors.get((source, scope))
        cursor_state = str(cursor["state"]) if cursor else "unknown"
        if cursor_state != "active":
            status = cursor_state
        elif counts["unknown_gap"] or counts["confirmed_lost"]:
            status = "partial"
        elif counts["pending_fetch"]:
            status = "pending"
        elif counts["observed"]:
            status = "complete_for_observed_window"
        else:
            status = "unknown"
        reports.append(
            {
                "source": source,
                "scope": scope,
                "epoch": epoch,
                "status": status,
                "cursor": str(cursor["cursor"]) if cursor and cursor["cursor"] else None,
                "known_missing": counts["confirmed_lost"],
                "unknown_gap": counts["unknown_gap"],
                "pending_fetch": counts["pending_fetch"],
                "observed": counts["observed"],
                **({"ranges": ranges} if include_ranges else {}),
            }
        )
    for (source, scope), cursor in sorted(cursors.items()):
        if any(report["source"] == source and report["scope"] == scope for report in reports):
            continue
        reports.append(
            {
                "source": source,
                "scope": scope,
                "epoch": int(cursor["epoch"]),
                "status": str(cursor["state"]),
                "cursor": str(cursor["cursor"]) if cursor["cursor"] else None,
                "known_missing": 0,
                "unknown_gap": 0,
                "pending_fetch": 0,
                "observed": 0,
                **({"ranges": []} if include_ranges else {}),
            }
        )
    return sorted(reports, key=lambda item: (item["source"], item["scope"], item["epoch"]))


def _attention_item(row: Any, priority: int, reasons: list[str]) -> dict[str, Any]:
    body = str(row["body"])
    withheld = row["source"] == "technocore"
    excerpt = (
        "[untrusted Technocore content withheld; expand explicitly]" if withheld else _excerpt(body)
    )
    evidence_id = f"{row['id']}@{row['revision_digest']}"
    return {
        "event_id": str(row["id"]),
        "evidence_id": evidence_id,
        "revision_digest": str(row["revision_digest"]),
        "source": str(row["source"]),
        "kind": str(row["kind"]),
        "priority": priority,
        "match_reasons": sorted(set(reasons)),
        "excerpt": excerpt,
        "excerpt_status": (
            "withheld_untrusted"
            if withheld
            else ("truncated" if len(excerpt) < len(body) else "complete")
        ),
        "content_length": len(body),
        "excerpt_sha256": sha256_text(excerpt),
        "content_sha256": sha256_text(body),
        "content_trust": "untrusted",
        "expand_ref": evidence_id,
        "actor": str(row["actor_id"]) if row["actor_id"] else None,
        "created_at": str(row["created_at"]) if row["created_at"] else None,
        "source_url": str(row["url"]) if row["url"] else None,
    }


def _continuation_cursor(offset: int, snapshot_digest: str, profile_digest: str) -> str:
    encoded = base64.urlsafe_b64encode(
        canonical_json(
            {
                "offset": offset,
                "profile_digest": profile_digest,
                "snapshot_digest": snapshot_digest,
                "version": 1,
            }
        ).encode("utf-8")
    ).decode("ascii")
    return "continue:v1:" + encoded.rstrip("=")


def _parse_continuation(value: str | None, *, snapshot_digest: str, profile_digest: str) -> int:
    if not value:
        return 0
    if not value.startswith("continue:v1:"):
        raise ContextError("CURSOR_VERSION_UNSUPPORTED", "unsupported continuation cursor")
    token = value.removeprefix("continue:v1:")
    try:
        padding = "=" * (-len(token) % 4)
        payload = json.loads(base64.urlsafe_b64decode(token + padding))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ContextError("INVALID_CURSOR", "invalid continuation cursor") from exc
    if payload.get("version") != 1:
        raise ContextError("CURSOR_VERSION_UNSUPPORTED", "unsupported continuation cursor")
    if payload.get("profile_digest") != profile_digest:
        raise ContextError("CURSOR_PROFILE_MISMATCH", "cursor belongs to a different profile")
    if payload.get("snapshot_digest") != snapshot_digest:
        raise ContextError("CURSOR_STALE", "observations changed after the cursor was issued")
    offset = payload.get("offset")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ContextError("INVALID_CURSOR", "cursor offset is invalid")
    return offset


def build_brief(
    state: State,
    *,
    consumer_id: str,
    interests: list[str] | tuple[str, ...] | None = None,
    mention_markers: list[str] | tuple[str, ...] | None = None,
    requested_budget: int = 800,
    as_of: str | None = None,
    continuation: str | None = None,
    public_only: bool = True,
) -> dict[str, Any]:
    if requested_budget < 1:
        raise ContextError("INVALID_BUDGET", "budget must be a positive integer")
    consumer = _validate_consumer(consumer_id)
    profile = _normalized_interests(interests)
    markers = _normalized_interests(mention_markers)
    profile_digest = sha256_text(
        canonical_json({"consumer": consumer, "interests": profile, "mention_markers": markers})
    )
    acknowledgments = state.acknowledgments(consumer)
    suppressed = {
        "acknowledged": 0,
        "duplicates": 0,
        "low_relevance": 0,
        "over_budget": 0,
        "quarantined": 0,
    }
    grouped: dict[str, list[tuple[Any, int, list[str]]]] = defaultdict(list)
    observations = state.current_observations(public_only=public_only)
    for row in observations:
        if row["quarantine_reason"]:
            suppressed["quarantined"] += 1
            continue
        priority, reasons = _priority_and_reasons(row, markers, profile)
        if priority == 0 or (profile and priority < 40):
            suppressed["low_relevance"] += 1
            continue
        content_digest = sha256_text(str(row["body"]).strip().lower())
        grouped[content_digest].append((row, priority, reasons))

    candidates: list[tuple[dict[str, Any], str, list[tuple[str, str]]]] = []
    for group in grouped.values():
        keys = [(str(row["id"]), str(row["revision_digest"])) for row, _, _ in group]
        if any(key in acknowledgments for key in keys):
            suppressed["acknowledged"] += len(group)
            continue
        representative, priority, reasons = sorted(
            group,
            key=lambda item: (
                -item[1],
                -int(bool(item[0]["authoritative"])),
                str(item[0]["created_at"] or ""),
                str(item[0]["source"]),
                str(item[0]["id"]),
            ),
        )[0]
        suppressed["duplicates"] += len(group) - 1
        item = _attention_item(representative, priority, reasons)
        item["related_evidence_count"] = len(group)
        candidates.append((item, str(representative["observed_at"]), keys))
    candidates.sort(
        key=lambda candidate: (
            -int(candidate[0]["priority"]),
            candidate[0]["created_at"] or "",
            candidate[0]["source"],
            candidate[0]["evidence_id"],
        )
    )
    if as_of is not None:
        try:
            datetime.fromisoformat(as_of.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ContextError("INVALID_AS_OF", "as_of must be an RFC 3339 timestamp") from exc
    now = as_of or iso_now()
    watermark = max((observed_at for _, observed_at, _ in candidates), default=now)
    snapshot_digest = sha256_text(
        canonical_json([candidate[0]["evidence_id"] for candidate in candidates])
    )
    offset = _parse_continuation(
        continuation,
        snapshot_digest=snapshot_digest,
        profile_digest=profile_digest,
    )
    if offset > len(candidates):
        raise ContextError("INVALID_CURSOR", "cursor offset exceeds the candidate set")
    candidates = candidates[offset:]
    base = {
        "schema": BRIEF_SCHEMA,
        "as_of": now,
        "consumer_id": consumer,
        "profile_digest": profile_digest,
        "brief_cursor": f"brief:v1:{watermark}",
        "budget": {
            "requested": requested_budget,
            "estimated_used": 0,
            "method": BUDGET_METHOD,
            "scope": "domain_payload_only",
        },
        "coverage": coverage_report(state, include_ranges=False, public_only=public_only),
        "items": [],
        "critical_items_remaining": 0,
        "continuation_cursor": None,
        "suppressed": suppressed,
    }
    minimum = _with_budget_used(base)
    if minimum["budget"]["estimated_used"] > requested_budget:
        raise ContextError(
            "BUDGET_TOO_SMALL",
            "budget cannot hold the minimum schema-valid brief",
            details={"required_units": minimum["budget"]["estimated_used"]},
        )
    selected: list[dict[str, Any]] = []
    consumed = 0
    for item, _, _ in candidates:
        trial = {**base, "items": [*selected, item]}
        if _with_budget_used(trial)["budget"]["estimated_used"] <= requested_budget:
            selected.append(item)
            consumed += 1
        else:
            break
    remaining = candidates[consumed:]
    suppressed["over_budget"] += len(remaining)
    omitted_critical = sum(1 for item, _, _ in remaining if int(item["priority"]) == 100)
    next_cursor = (
        _continuation_cursor(offset + consumed, snapshot_digest, profile_digest)
        if remaining and consumed
        else None
    )
    result = {
        **base,
        "items": selected,
        "critical_items_remaining": omitted_critical,
        "continuation_cursor": next_cursor,
    }
    result["suppressed"] = suppressed
    result = _with_budget_used(result)
    while result["budget"]["estimated_used"] > requested_budget and result["items"]:
        removed = result["items"].pop()
        result["suppressed"]["over_budget"] += 1
        if removed["priority"] == 100:
            result["critical_items_remaining"] += 1
        result["continuation_cursor"] = _continuation_cursor(
            offset + len(result["items"]), snapshot_digest, profile_digest
        )
        result = _with_budget_used(result)
    return result


def parse_evidence_ref(value: str) -> tuple[str, str]:
    match = REF_RE.fullmatch(value)
    if not match:
        raise ContextError("INVALID_EVIDENCE_ID", "evidence id must be observation@sha256")
    return match.group("observation"), match.group("digest")


def expand_observations(
    state: State,
    evidence_ids: list[str],
    *,
    requested_budget: int = 800,
    public_only: bool = True,
    as_of: str | None = None,
) -> dict[str, Any]:
    if not evidence_ids or len(evidence_ids) > 50:
        raise ContextError("INVALID_EVIDENCE_IDS", "provide between 1 and 50 evidence ids")
    unique = list(dict.fromkeys(evidence_ids))
    base = {
        "schema": EXPANSION_SCHEMA,
        "as_of": as_of or datetime.now(UTC).isoformat(),
        "budget": {
            "requested": requested_budget,
            "estimated_used": 0,
            "method": BUDGET_METHOD,
            "scope": "domain_payload_only",
        },
        "items": [],
    }
    if requested_budget < 1:
        raise ContextError("INVALID_BUDGET", "budget must be a positive integer")
    for reference in unique:
        observation_id, digest = parse_evidence_ref(reference)
        try:
            revision = state.observation_revision(observation_id, digest)
        except KeyError:
            base["items"].append({"evidence_id": reference, "status": "unknown"})
            continue
        if public_only and revision["exposure_class"] != "public":
            base["items"].append({"evidence_id": reference, "status": "restricted"})
            continue
        material = json.loads(revision["material_json"])
        content = str(material["body"])
        full = {
            "evidence_id": reference,
            "status": "included",
            "content": content,
            "content_sha256": sha256_text(content),
            "content_trust": "untrusted",
            "source": material["source"],
            "external_id": material["external_id"],
            "actor": material.get("actor_id"),
            "created_at": material.get("created_at"),
            "source_url": material.get("url"),
        }
        trial = {**base, "items": [*base["items"], full]}
        if _with_budget_used(trial)["budget"]["estimated_used"] <= requested_budget:
            base["items"].append(full)
        else:
            complete_units = _with_budget_used(trial)["budget"]["estimated_used"]
            metadata = {
                "evidence_id": reference,
                "status": "omitted_budget",
                "content_sha256": sha256_text(content),
                "required_units": complete_units,
            }
            base["items"].append(metadata)
    result = _with_budget_used(base)
    if result["budget"]["estimated_used"] > requested_budget:
        raise ContextError(
            "BUDGET_TOO_SMALL",
            "budget cannot hold expansion metadata",
            details={"required_units": result["budget"]["estimated_used"]},
        )
    return result


def acknowledge_observations(
    state: State, consumer_id: str, evidence_ids: list[str]
) -> dict[str, Any]:
    consumer = _validate_consumer(consumer_id)
    parsed = [parse_evidence_ref(value) for value in list(dict.fromkeys(evidence_ids))]
    count = state.acknowledge(consumer, parsed)
    return {
        "schema": "technocore-context-acknowledgment/v1",
        "consumer_id": consumer,
        "acknowledged": count,
        "requested": len(parsed),
    }


def check_collisions(state: State, target: str) -> dict[str, Any]:
    observation_id = target
    if "@" in target:
        observation_id, _ = parse_evidence_ref(target)
    row = state.current_observation(observation_id)
    matches: list[dict[str, Any]] = []
    observations = state.current_observations(public_only=True)
    if row["source"] == "github" and row["kind"] == "issue":
        number = re.escape(str(row["external_id"]))
        pattern = re.compile(
            rf"(?:fix(?:es)?|close(?:s)?|resolve(?:s)?)?\s*#{number}\b",
            re.I,
        )
        for candidate in observations:
            if candidate["kind"] != "pull_request" or candidate["id"] == row["id"]:
                continue
            if pattern.search(f"{candidate['title']}\n{candidate['body']}"):
                matches.append(
                    {
                        "rule": "github_exact_issue_reference",
                        "evidence_id": (f"{candidate['id']}@{candidate['revision_digest']}"),
                    }
                )
    if row["source"] == "technocore":
        room, _, sequence = str(row["external_id"]).partition(":")
        patterns = (
            re.compile(rf"\b{re.escape(room)}:{re.escape(sequence)}\b", re.I),
            re.compile(
                rf"\b(?:re\s+)?{re.escape(room)}\s+seq(?:uence)?\s+{re.escape(sequence)}\b",
                re.I,
            ),
        )
        created = str(row["created_at"] or "")
        for candidate in observations:
            if candidate["source"] != "technocore" or candidate["id"] == row["id"]:
                continue
            if created and str(candidate["created_at"] or "") <= created:
                continue
            if any(pattern.search(str(candidate["body"])) for pattern in patterns):
                matches.append(
                    {
                        "rule": "technocore_exact_source_reference",
                        "evidence_id": (f"{candidate['id']}@{candidate['revision_digest']}"),
                    }
                )
    with state.connect() as connection:
        local = connection.execute(
            """SELECT a.bundle_id, a.action_type, a.external_url, a.status
            FROM candidates c
            JOIN bundles b ON b.candidate_id = c.id
            JOIN actions a ON a.bundle_id = b.id
            WHERE c.observation_id = ? AND a.status IN ('submitted', 'published', 'merged')""",
            (observation_id,),
        ).fetchall()
    for action in local:
        matches.append(
            {
                "rule": "local_published_action",
                "bundle_id": str(action["bundle_id"]),
                "action_type": str(action["action_type"]),
                "status": str(action["status"]),
                "external_url": str(action["external_url"]) if action["external_url"] else None,
            }
        )
    coverage = coverage_report(state)
    relevant_coverage = [
        item
        for item in coverage
        if row["source"] != "technocore"
        or item["scope"] == str(row["external_id"]).split(":", 1)[0]
    ]
    incomplete = any(
        item["status"] not in {"complete_for_observed_window"} for item in relevant_coverage
    )
    state_label = (
        "confirmed" if matches else ("unknown_due_to_coverage" if incomplete else "none_observed")
    )
    return {
        "schema": "technocore-context-collision/v1",
        "target": f"{row['id']}@{row['revision_digest']}",
        "collision_state": state_label,
        "matches": matches,
        "coverage": relevant_coverage,
    }


def payload_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
