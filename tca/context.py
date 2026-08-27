from __future__ import annotations

import base64
import hashlib
import json
import math
import re
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from tca.ranking import pull_request_blocks_issue
from tca.state import State, canonical_json, iso_now, sha256_text

BRIEF_SCHEMA = "technocore-context-brief/v1"
EXPANSION_SCHEMA = "technocore-context-expansion/v1"
ERROR_SCHEMA = "technocore-context-error/v1"
BUDGET_METHOD = "canonical-utf8-div3-v1"
MAX_BUDGET_UNITS = 100_000
MAX_EVIDENCE_IDS = 50
MAX_EVIDENCE_ID_LENGTH = 512
MAX_CURSOR_LENGTH = 2048
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


def _required_budget(payload: dict[str, Any]) -> int:
    """Return the fixed-point budget that can carry this complete payload."""
    required = 1
    while True:
        trial = json.loads(canonical_json(payload))
        trial["budget"]["requested"] = required
        measured = _with_budget_used(trial)["budget"]["estimated_used"]
        if measured == required:
            return required
        required = measured


def _validate_consumer(consumer_id: str) -> str:
    normalized = consumer_id.strip().lower()
    if not CONSUMER_RE.fullmatch(normalized):
        raise ContextError(
            "INVALID_CONSUMER_ID",
            "consumer id must match [a-z0-9][a-z0-9_.-]{0,63}",
        )
    return normalized


def _validate_budget(requested_budget: int) -> int:
    if isinstance(requested_budget, bool) or not isinstance(requested_budget, int):
        raise ContextError("INVALID_BUDGET", "budget must be an integer")
    if not 1 <= requested_budget <= MAX_BUDGET_UNITS:
        raise ContextError(
            "INVALID_BUDGET",
            f"budget must be between 1 and {MAX_BUDGET_UNITS}",
        )
    return requested_budget


def _normalized_interests(
    interests: list[str] | tuple[str, ...] | None,
    *,
    max_items: int = 32,
    max_length: int = 80,
    label: str = "interests",
) -> tuple[str, ...]:
    if interests is not None and (
        not isinstance(interests, (list, tuple))
        or not all(isinstance(value, str) for value in interests)
    ):
        raise ContextError("INVALID_INTERESTS", f"{label} must be a string array")
    values = {value.strip().lower() for value in interests or () if value.strip()}
    if any(len(value) > max_length for value in values) or len(values) > max_items:
        raise ContextError(
            "INVALID_INTERESTS",
            f"at most {max_items} {label} of {max_length} characters are allowed",
        )
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
        (str(row["source"]), str(row["scope"]), int(row["epoch"])): row
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
        cursor = cursors.get((source, scope, epoch))
        if cursor and cursor["state"] != "active":
            status = str(cursor["state"])
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
    for (source, scope, epoch), cursor in sorted(cursors.items()):
        if any(
            report["source"] == source and report["scope"] == scope and report["epoch"] == epoch
            for report in reports
        ):
            continue
        reports.append(
            {
                "source": source,
                "scope": scope,
                "epoch": epoch,
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
    withheld = not bool(row["authoritative"])
    excerpt = (
        "[untrusted source content withheld; expand explicitly]" if withheld else _excerpt(body)
    )
    evidence_id = f"{row['id']}@{row['revision_digest']}"
    return {
        "evidence_id": evidence_id,
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
        "content_trust": "untrusted",
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
    if not isinstance(value, str):
        raise ContextError("INVALID_CURSOR", "continuation cursor must be a string")
    if len(value) > MAX_CURSOR_LENGTH:
        raise ContextError("INVALID_CURSOR", "continuation cursor is too long")
    if not value.startswith("continue:v1:"):
        raise ContextError("CURSOR_VERSION_UNSUPPORTED", "unsupported continuation cursor")
    token = value.removeprefix("continue:v1:")
    try:
        padding = "=" * (-len(token) % 4)
        payload = json.loads(base64.urlsafe_b64decode(token + padding))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ContextError("INVALID_CURSOR", "invalid continuation cursor") from exc
    if not isinstance(payload, dict):
        raise ContextError("INVALID_CURSOR", "invalid continuation cursor")
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


def _canonical_observed_at(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def _observation_watermark(row: Any) -> tuple[str, str, str]:
    return (
        _canonical_observed_at(row["observed_at"]),
        str(row["id"]),
        str(row["revision_digest"]),
    )


def _brief_cursor(watermark: tuple[str, str, str], profile_digest: str) -> str:
    encoded = base64.urlsafe_b64encode(
        canonical_json(
            {
                "profile_digest": profile_digest,
                "version": 2,
                "watermark": list(watermark),
            }
        ).encode("utf-8")
    ).decode("ascii")
    return "brief:v2:" + encoded.rstrip("=")


def _parse_brief_cursor(value: str | None, *, profile_digest: str) -> tuple[str, str, str]:
    if not value:
        return ("", "", "")
    if not isinstance(value, str):
        raise ContextError("INVALID_CURSOR", "brief cursor must be a string")
    if len(value) > MAX_CURSOR_LENGTH:
        raise ContextError("INVALID_CURSOR", "brief cursor is too long")
    if not value.startswith("brief:v2:"):
        raise ContextError("CURSOR_VERSION_UNSUPPORTED", "unsupported brief cursor")
    token = value.removeprefix("brief:v2:")
    try:
        padding = "=" * (-len(token) % 4)
        payload = json.loads(base64.urlsafe_b64decode(token + padding))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ContextError("INVALID_CURSOR", "invalid brief cursor") from exc
    if not isinstance(payload, dict):
        raise ContextError("INVALID_CURSOR", "invalid brief cursor")
    if payload.get("version") != 2:
        raise ContextError("CURSOR_VERSION_UNSUPPORTED", "unsupported brief cursor")
    if payload.get("profile_digest") != profile_digest:
        raise ContextError("CURSOR_PROFILE_MISMATCH", "cursor belongs to a different profile")
    watermark = payload.get("watermark")
    if (
        not isinstance(watermark, list)
        or len(watermark) != 3
        or not all(isinstance(item, str) for item in watermark)
    ):
        raise ContextError("INVALID_CURSOR", "brief cursor watermark is invalid")
    return watermark[0], watermark[1], watermark[2]


def build_brief(
    state: State,
    *,
    consumer_id: str,
    interests: list[str] | tuple[str, ...] | None = None,
    mention_markers: list[str] | tuple[str, ...] | None = None,
    requested_budget: int = 800,
    as_of: str | None = None,
    continuation: str | None = None,
    since: str | None = None,
    public_only: bool = True,
) -> dict[str, Any]:
    requested_budget = _validate_budget(requested_budget)
    consumer = _validate_consumer(consumer_id)
    profile = _normalized_interests(interests)
    markers = _normalized_interests(
        mention_markers,
        max_items=16,
        max_length=160,
        label="mention markers",
    )
    profile_digest = sha256_text(
        canonical_json({"consumer": consumer, "interests": profile, "mention_markers": markers})
    )
    since_watermark = _parse_brief_cursor(since, profile_digest=profile_digest)
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
    target_watermark = since_watermark
    for row in observations:
        row_watermark = _observation_watermark(row)
        target_watermark = max(target_watermark, row_watermark)
        if row_watermark <= since_watermark:
            continue
        if row["quarantine_reason"]:
            suppressed["quarantined"] += 1
            continue
        priority, reasons = _priority_and_reasons(row, markers, profile)
        if priority == 0 or (profile and priority < 40):
            suppressed["low_relevance"] += 1
            continue
        key = (str(row["id"]), str(row["revision_digest"]))
        if key in acknowledgments:
            suppressed["acknowledged"] += 1
            continue
        content_digest = sha256_text(str(row["body"]).strip().lower())
        grouped[content_digest].append((row, priority, reasons))

    candidates: list[tuple[dict[str, Any], str]] = []
    for group in grouped.values():
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
        candidates.append((item, str(representative["observed_at"])))
    candidates.sort(
        key=lambda candidate: (
            -int(candidate[0]["priority"]),
            candidate[0]["created_at"] or "",
            candidate[0]["source"],
            candidate[0]["evidence_id"],
        )
    )
    if as_of is not None:
        if not isinstance(as_of, str):
            raise ContextError("INVALID_AS_OF", "as_of must be an RFC 3339 timestamp")
        try:
            datetime.fromisoformat(as_of.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ContextError("INVALID_AS_OF", "as_of must be an RFC 3339 timestamp") from exc
    now = as_of or iso_now()
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
        "brief_cursor": _brief_cursor(target_watermark, profile_digest),
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

    def page(
        selected_count: int,
        *,
        reported_budget: int = requested_budget,
    ) -> dict[str, Any]:
        remaining = candidates[selected_count:]
        page_suppressed = {**suppressed, "over_budget": len(remaining)}
        return _with_budget_used(
            {
                **base,
                "budget": {**base["budget"], "requested": reported_budget},
                "items": [item for item, _ in candidates[:selected_count]],
                "critical_items_remaining": sum(
                    1 for item, _ in remaining if int(item["priority"]) == 100
                ),
                "continuation_cursor": (
                    _continuation_cursor(
                        offset + selected_count,
                        snapshot_digest,
                        profile_digest,
                    )
                    if remaining
                    else None
                ),
                "suppressed": page_suppressed,
            }
        )

    selected_count = 0
    result = page(0)
    for count in range(1, len(candidates) + 1):
        trial = page(count)
        if trial["budget"]["estimated_used"] > requested_budget:
            break
        selected_count = count
        result = trial
    if candidates and selected_count == 0:
        required = page(1, reported_budget=1)["budget"]["estimated_used"]
        while True:
            exact = page(1, reported_budget=required)["budget"]["estimated_used"]
            if exact == required:
                break
            required = exact
        raise ContextError(
            "BUDGET_TOO_SMALL",
            "budget cannot make progress on the next attention item",
            details={
                "required_units": required,
                "evidence_id": candidates[0][0]["evidence_id"],
            },
        )
    return result


def parse_evidence_ref(value: str) -> tuple[str, str]:
    if not isinstance(value, str):
        raise ContextError("INVALID_EVIDENCE_ID", "evidence id must be a string")
    if len(value) > MAX_EVIDENCE_ID_LENGTH:
        raise ContextError("INVALID_EVIDENCE_ID", "evidence id is too long")
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
    if (
        not isinstance(evidence_ids, (list, tuple))
        or not evidence_ids
        or len(evidence_ids) > MAX_EVIDENCE_IDS
        or not all(isinstance(value, str) for value in evidence_ids)
    ):
        raise ContextError(
            "INVALID_EVIDENCE_IDS",
            f"provide between 1 and {MAX_EVIDENCE_IDS} evidence ids",
        )
    if as_of is not None:
        if not isinstance(as_of, str):
            raise ContextError("INVALID_AS_OF", "as_of must be an RFC 3339 timestamp")
        try:
            datetime.fromisoformat(as_of.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ContextError("INVALID_AS_OF", "as_of must be an RFC 3339 timestamp") from exc
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
    requested_budget = _validate_budget(requested_budget)
    base["budget"]["requested"] = requested_budget
    resolved: list[dict[str, Any] | None] = []
    stubs: list[dict[str, Any]] = []
    for reference in unique:
        observation_id, digest = parse_evidence_ref(reference)
        try:
            revision = state.observation_revision(observation_id, digest)
        except KeyError:
            resolved.append(None)
            stubs.append({"evidence_id": reference, "status": "unknown"})
            continue
        if public_only and revision["exposure_class"] != "public":
            resolved.append(None)
            stubs.append({"evidence_id": reference, "status": "restricted"})
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
        resolved.append(full)
        stubs.append(
            {
                "evidence_id": reference,
                "status": "omitted_budget",
                "content_sha256": sha256_text(content),
                "required_units": 0,
            }
        )

    # Per-item retry hints are complete-response budgets, not isolated item sizes. Iterate because
    # the digit width of one hint can change the complete payload size used to calculate another.
    while True:
        changed = False
        for index, full in enumerate(resolved):
            if full is None:
                continue
            trial_items = list(stubs)
            trial_items[index] = full
            required = _required_budget({**base, "items": trial_items})
            if stubs[index]["required_units"] != required:
                stubs[index]["required_units"] = required
                changed = True
        if not changed:
            break

    base["items"] = stubs
    minimum = _with_budget_used(base)
    if minimum["budget"]["estimated_used"] > requested_budget:
        required = _required_budget(base)
        raise ContextError(
            "BUDGET_TOO_SMALL",
            "budget cannot hold expansion metadata",
            details={"required_units": required},
        )

    items = list(stubs)
    for index, full in enumerate(resolved):
        if full is None:
            continue
        trial_items = list(items)
        trial_items[index] = full
        trial = _with_budget_used({**base, "items": trial_items})
        if trial["budget"]["estimated_used"] <= requested_budget:
            items = trial_items
    return _with_budget_used({**base, "items": items})


def acknowledge_observations(
    state: State, consumer_id: str, evidence_ids: list[str]
) -> dict[str, Any]:
    consumer = _validate_consumer(consumer_id)
    if (
        not isinstance(evidence_ids, (list, tuple))
        or not evidence_ids
        or len(evidence_ids) > MAX_EVIDENCE_IDS
        or not all(isinstance(value, str) for value in evidence_ids)
    ):
        raise ContextError(
            "INVALID_EVIDENCE_IDS",
            f"provide between 1 and {MAX_EVIDENCE_IDS} evidence ids",
        )
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
            if candidate["id"] == row["id"] or not pull_request_blocks_issue(candidate):
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
