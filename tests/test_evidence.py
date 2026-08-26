import json

from tca.evidence import (
    canonical_payload,
    sign_account_binding,
    sign_record,
    verify_account_binding,
    verify_record,
)
from tca.identity import Identity, MemorySecretStore


def record() -> dict:
    return {
        "schema": "technocore-contribution-evidence/v1",
        "did": "placeholder",
        "github_account": "pseudonymous-org",
        "x_account": "pseudonymous-x",
        "kind": "upstream_pr",
        "artifact_url": "https://github.com/example/project/pull/1",
        "published_at": "2026-08-26T00:00:00+00:00",
        "tests": {"result": "pass", "log_sha256": "a" * 64},
        "technocore": {
            "room": "technocore",
            "seq": 42,
            "nonce": 7,
            "message_sha256": "b" * 64,
        },
    }


def test_sign_and_verify_then_reject_tamper() -> None:
    identity = Identity(MemorySecretStore(bytes.fromhex("01" * 32)))
    signed = sign_record(record(), identity)
    valid, errors = verify_record(signed)
    assert valid, errors
    signed["artifact_url"] = "https://example.invalid/tampered"
    valid, errors = verify_record(signed)
    assert not valid
    assert "invalid Ed25519 signature" in errors


def test_canonical_payload_ignores_signature_and_is_stable() -> None:
    value = record()
    first = canonical_payload(value)
    value["signature"] = "ignored"
    assert canonical_payload(value) == first
    assert json.loads(first)["kind"] == "upstream_pr"


def test_account_binding_is_privacy_scoped_and_verifiable() -> None:
    identity = Identity(MemorySecretStore(bytes.fromhex("02" * 32)))
    binding = sign_account_binding(
        identity, "pseudonymous-org", "pseudonymous-x", "2026-08-26T00:00:00+00:00"
    )
    valid, errors = verify_account_binding(binding)
    assert valid, errors
    assert "personhood" in binding["statement"]
    binding["github_account"] = "attacker"
    assert not verify_account_binding(binding)[0]
