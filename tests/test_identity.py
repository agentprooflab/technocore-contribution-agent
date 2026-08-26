import hashlib

import pytest

from tca.identity import (
    Identity,
    MemorySecretStore,
    clean_text,
    did_note_location,
    public_key_from_did,
    verify_signature,
)


def fixed_identity() -> Identity:
    return Identity(MemorySecretStore(bytes.fromhex("00" * 31 + "01")))


def test_did_matches_official_signer_vector() -> None:
    identity = fixed_identity()
    assert identity.did() == "did:key:z6MkjchhfUsD6mmvni8mCdXHw216Xrm9bQe2mBH1P5RDjVJG"
    assert len(public_key_from_did(identity.did())) == 32


def test_message_signature_uses_swept_text() -> None:
    identity = fixed_identity()
    did, signature, swept = identity.sign_message("technocore", 7, "  hello\nworld  ")
    assert swept == "hello world"
    assert verify_signature(did, b"technocore|7|hello world", signature)
    assert not verify_signature(did, b"technocore|7|hello\nworld", signature)


def test_identity_refuses_replacement() -> None:
    identity = fixed_identity()
    with pytest.raises(RuntimeError, match="already exists"):
        identity.create()


def test_note_location_matches_manifest_convention() -> None:
    did = fixed_identity().did()
    digest = hashlib.sha256(did.encode()).hexdigest()[:16]
    assert did_note_location(did) == (f"did-{digest[:2]}", digest[2:])


def test_clean_text_rejects_empty_and_over_limit() -> None:
    with pytest.raises(ValueError):
        clean_text("\n\t", 10)
    with pytest.raises(ValueError):
        clean_text("x" * 11, 10)
