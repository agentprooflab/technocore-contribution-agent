from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from tca.safety import scan_untrusted
from tca.state import State, iso_now

REPRO_WORDS = ("reproduce", "reproduction", "observed", "expected", "failing test")
GENERIC_PROMO = ("follow me", "airdrop farming", "check in daily", "gm technocore")
TECHNICAL_WORDS = (
    "api",
    "did:key",
    "error",
    "message",
    "mcp",
    "nonce",
    "receipt",
    "room",
    "signature",
    "signed",
    "technocore",
)


def pull_request_blocks_issue(row: Any) -> bool:
    if row["kind"] != "pull_request":
        return False
    try:
        raw = json.loads(row["raw_json"])
    except (KeyError, TypeError, json.JSONDecodeError):
        return True
    if not isinstance(raw, dict):
        return True
    source_state = raw.get("state") or raw.get("source_state")
    if source_state != "closed":
        return True
    pull_request = raw.get("pull_request") or {}
    merged_at = pull_request.get("merged_at") if isinstance(pull_request, dict) else None
    return bool(raw.get("merged_at") or merged_at)


def _collision_for_issue(state: State, issue_number: str) -> bool:
    pattern = re.compile(
        rf"(?:fix(?:es)?|close(?:s)?|resolve(?:s)?)?\s*#{re.escape(issue_number)}\b",
        re.I,
    )
    with state.connect() as connection:
        rows = connection.execute(
            "SELECT kind, title, body, raw_json FROM observations WHERE kind = 'pull_request'"
        ).fetchall()
    return any(
        pull_request_blocks_issue(row) and pattern.search(f"{row['title']}\n{row['body']}")
        for row in rows
    )


def _duplicate_body_count(state: State, body: str) -> int:
    with state.connect() as connection:
        row = connection.execute(
            "SELECT COUNT(*) FROM observations WHERE body = ?", (body,)
        ).fetchone()
    return int(row[0])


def classify(row: Any, state: State) -> dict[str, Any]:
    body = str(row["body"])
    lowered = body.lower()
    authoritative = bool(row["authoritative"])
    category = "generic"
    priority = 0
    status = "rejected"
    reason = "generic promotion and undifferentiated activity are not contribution candidates"

    if row["source"] == "x" and row["kind"] == "official_task":
        if authoritative:
            category, priority, status = "official_task", 100, "ready"
            reason = "allowlisted first-party X account published task or reward guidance"
        else:
            category, priority, status = "official_task", 0, "quarantined"
            reason = "X account identity did not match the allowlisted numeric account ID"
    elif row["source"] == "github" and row["kind"] == "issue":
        collision = _collision_for_issue(state, str(row["external_id"]))
        if collision:
            category, priority, status = "upstream_defect", 0, "rejected"
            reason = "an open pull request already references this issue"
        elif authoritative:
            category, priority, status = "maintainer_request", 80, "ready"
            reason = "allowlisted maintainer or repository member opened the issue"
        elif any(word in lowered for word in REPRO_WORDS):
            category, priority, status = "upstream_defect", 60, "ready"
            reason = "issue contains reproducible defect evidence and no observed PR collision"
    elif row["source"] == "x" and authoritative and "awesome-technocore" in lowered:
        category, priority, status = "ecosystem_request", 50, "ready"
        reason = "first-party request for ecosystem curation"
    elif row["source"] == "technocore" and row["kind"] == "technical_question":
        findings = scan_untrusted(body)
        signed_actor = str(row["actor_id"]).startswith("did:key:")
        technical = any(word in lowered for word in TECHNICAL_WORDS)
        original = _duplicate_body_count(state, body) == 1
        if findings:
            category, priority, status = "technical_question", 0, "quarantined"
            reason = "untrusted room content resembles executable instructions"
        elif signed_actor and technical and original and 20 <= len(body) <= 1000:
            category, priority, status = "technical_question", 20, "ready"
            reason = "signed, original technical question; response remains approval-gated"
        else:
            category, priority, status = "generic", 0, "rejected"
            reason = "room question lacked signed, original, bounded technical evidence"

    if any(marker in lowered for marker in GENERIC_PROMO):
        category, priority, status = "generic", 0, "rejected"
        reason = "matched generic promotion or heartbeat pattern"

    candidate_id = "cand-" + hashlib.sha256(str(row["id"]).encode()).hexdigest()[:16]
    return {
        "id": candidate_id,
        "observation_id": row["id"],
        "category": category,
        "priority": priority,
        "status": status,
        "reason": reason,
        "created_at": iso_now(),
    }


def rank(state: State, rescore: bool = False) -> dict[str, Any]:
    counts: dict[str, int] = {"ready": 0, "quarantined": 0, "rejected": 0}
    observations = state.observations() if rescore else state.observations_without_candidates()
    for observation in observations:
        candidate = classify(observation, state)
        state.add_candidate(candidate)
        counts[candidate["status"]] += 1
    ranked = [dict(row) for row in state.list_candidates()]
    actionable = [item for item in ranked if item["status"] in {"ready", "quarantined"}]
    state.set_meta("last_rank_report", json.dumps(counts, sort_keys=True))
    return {
        "new": counts,
        "rescored": rescore,
        "actionable_total": len(actionable),
        "candidates": actionable[:25],
        "omitted": max(len(actionable) - 25, 0),
    }
