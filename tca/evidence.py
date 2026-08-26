from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from tca.identity import Identity, verify_signature

SCHEMA = "technocore-contribution-evidence/v1"
BINDING_SCHEMA = "technocore-account-binding/v1"
REQUIRED_FIELDS = {
    "schema",
    "did",
    "github_account",
    "x_account",
    "kind",
    "artifact_url",
    "published_at",
    "tests",
    "technocore",
}


def canonical_payload(record: dict[str, Any]) -> bytes:
    unsigned = {key: value for key, value in record.items() if key != "signature"}
    return json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest(record: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_payload(record)).hexdigest()


def validate_shape(record: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    missing = REQUIRED_FIELDS - record.keys()
    if missing:
        errors.append(f"missing fields: {', '.join(sorted(missing))}")
    if record.get("schema") != SCHEMA:
        errors.append(f"unsupported schema: {record.get('schema')!r}")
    tests = record.get("tests")
    if not isinstance(tests, dict) or "result" not in tests or "log_sha256" not in tests:
        errors.append("tests must contain result and log_sha256")
    technocore = record.get("technocore")
    if not isinstance(technocore, dict):
        errors.append("technocore must be an object")
    else:
        for field in ("room", "seq", "nonce", "message_sha256"):
            if field not in technocore:
                errors.append(f"technocore missing {field}")
    return errors


def sign_record(record: dict[str, Any], identity: Identity) -> dict[str, Any]:
    prepared = dict(record)
    prepared["schema"] = SCHEMA
    prepared["did"] = identity.did()
    errors = validate_shape(prepared)
    if errors:
        raise ValueError("; ".join(errors))
    prepared["signature"] = identity.sign_bytes(canonical_payload(prepared))
    return prepared


def verify_record(record: dict[str, Any]) -> tuple[bool, list[str]]:
    errors = validate_shape(record)
    signature = record.get("signature")
    did = record.get("did")
    if not isinstance(signature, str):
        errors.append("missing signature")
    if not isinstance(did, str):
        errors.append("missing did")
    if not errors and not verify_signature(did, canonical_payload(record), signature):
        errors.append("invalid Ed25519 signature")
    return not errors, errors


def sign_account_binding(
    identity: Identity, github_account: str, x_account: str, published_at: str
) -> dict[str, Any]:
    if not github_account or not x_account:
        raise ValueError("account binding requires GitHub and X account names")
    record = {
        "schema": BINDING_SCHEMA,
        "did": identity.did(),
        "github_account": github_account,
        "x_account": x_account,
        "published_at": published_at,
        "statement": "voluntary key-control evidence; not personhood or reward eligibility",
    }
    record["signature"] = identity.sign_bytes(canonical_payload(record))
    return record


def verify_account_binding(record: dict[str, Any]) -> tuple[bool, list[str]]:
    required = {
        "schema",
        "did",
        "github_account",
        "x_account",
        "published_at",
        "statement",
        "signature",
    }
    errors: list[str] = []
    missing = required - record.keys()
    if missing:
        errors.append(f"missing fields: {', '.join(sorted(missing))}")
    if record.get("schema") != BINDING_SCHEMA:
        errors.append(f"unsupported binding schema: {record.get('schema')!r}")
    if not errors and not verify_signature(
        str(record["did"]), canonical_payload(record), str(record["signature"])
    ):
        errors.append("invalid Ed25519 signature")
    return not errors, errors


def load_record(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def write_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
