from __future__ import annotations

import base64
import hashlib
import re
import secrets
import shutil
import subprocess
import unicodedata
from dataclasses import dataclass
from typing import Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
MULTICODEC_ED25519 = b"\xed\x01"
INVISIBLE_CATEGORIES = ("Cc", "Cf", "Cs", "Co", "Zl", "Zp")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
NONCE_RE = re.compile(r"^[0-9]{1,19}$")


class SecretStore(Protocol):
    def get(self) -> bytes | None: ...

    def put(self, secret: bytes) -> None: ...


@dataclass
class MacOSKeychain:
    service: str
    account: str

    def get(self) -> bytes | None:
        if not shutil.which("security"):
            return None
        result = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-s",
                self.service,
                "-a",
                self.account,
                "-w",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return None
        return base64.b64decode(result.stdout.strip(), validate=True)

    def put(self, secret: bytes) -> None:
        if not shutil.which("security"):
            raise RuntimeError("macOS Keychain is unavailable on this host")
        encoded = base64.b64encode(secret).decode()
        subprocess.run(
            [
                "security",
                "add-generic-password",
                "-U",
                "-s",
                self.service,
                "-a",
                self.account,
                "-w",
                encoded,
            ],
            check=True,
            capture_output=True,
            text=True,
        )


@dataclass
class MemorySecretStore:
    value: bytes | None = None

    def get(self) -> bytes | None:
        return self.value

    def put(self, secret: bytes) -> None:
        self.value = secret


def b58encode(raw: bytes) -> str:
    zeroes = len(raw) - len(raw.lstrip(b"\x00"))
    value = int.from_bytes(raw, "big")
    encoded = ""
    while value:
        value, remainder = divmod(value, 58)
        encoded = B58[remainder] + encoded
    return "1" * zeroes + encoded


def b58decode(value: str) -> bytes:
    total = 0
    for character in value:
        try:
            digit = B58.index(character)
        except ValueError as exc:
            raise ValueError("invalid base58btc character") from exc
        total = total * 58 + digit
    raw = total.to_bytes((total.bit_length() + 7) // 8, "big") if total else b""
    return b"\x00" * (len(value) - len(value.lstrip("1"))) + raw


def did_from_public_key(public_key: bytes) -> str:
    if len(public_key) != 32:
        raise ValueError("Ed25519 public key must be 32 bytes")
    return "did:key:z" + b58encode(MULTICODEC_ED25519 + public_key)


def public_key_from_did(did: str) -> bytes:
    prefix = "did:key:z"
    if not did.startswith(prefix):
        raise ValueError("not a base58btc did:key")
    raw = b58decode(did[len(prefix) :])
    if not raw.startswith(MULTICODEC_ED25519) or len(raw) != 34:
        raise ValueError("not an Ed25519 did:key")
    return raw[2:]


def clean_text(text: str, limit: int) -> str:
    cleaned = "".join(
        " " if unicodedata.category(character) in INVISIBLE_CATEGORIES else character
        for character in text
    ).strip()
    if not cleaned:
        raise ValueError("nothing visible remains after the Technocore sweep")
    if len(cleaned) > limit:
        raise ValueError(f"cleaned text exceeds {limit} characters")
    return cleaned


class Identity:
    def __init__(self, store: SecretStore):
        self.store = store

    def exists(self) -> bool:
        return self.store.get() is not None

    def create(self) -> str:
        if self.exists():
            raise RuntimeError("identity already exists; refusing to replace it")
        seed = secrets.token_bytes(32)
        self.store.put(seed)
        return self.did()

    def private_key(self) -> Ed25519PrivateKey:
        seed = self.store.get()
        if seed is None:
            raise RuntimeError("identity has not been initialized")
        if len(seed) != 32:
            raise RuntimeError("stored identity seed is not 32 bytes")
        return Ed25519PrivateKey.from_private_bytes(seed)

    def did(self) -> str:
        public = self.private_key().public_key().public_bytes_raw()
        return did_from_public_key(public)

    def sign_bytes(self, payload: bytes) -> str:
        signature = self.private_key().sign(payload)
        return base64.urlsafe_b64encode(signature).decode().rstrip("=")

    def sign_message(self, room: str, nonce: int, text: str) -> tuple[str, str, str]:
        if not NAME_RE.fullmatch(room):
            raise ValueError("invalid Technocore room name")
        nonce_text = str(nonce)
        if not NONCE_RE.fullmatch(nonce_text):
            raise ValueError("nonce must be 1-19 ASCII digits")
        swept = clean_text(text, 4096)
        payload = f"{room}|{nonce_text}|{swept}".encode()
        return self.did(), self.sign_bytes(payload), swept

    def sign_note(self, namespace: str, key: str, nonce: int, value: str) -> tuple[str, str, str]:
        if not NAME_RE.fullmatch(namespace) or not NAME_RE.fullmatch(key):
            raise ValueError("invalid Technocore note namespace or key")
        nonce_text = str(nonce)
        if not NONCE_RE.fullmatch(nonce_text):
            raise ValueError("nonce must be 1-19 ASCII digits")
        swept = clean_text(value, 8192)
        payload = f"{namespace}|{key}|{nonce_text}|{swept}".encode()
        return self.did(), self.sign_bytes(payload), swept


def verify_signature(did: str, payload: bytes, signature: str) -> bool:
    try:
        raw_signature = base64.urlsafe_b64decode(signature + "==")
        Ed25519PublicKey.from_public_bytes(public_key_from_did(did)).verify(raw_signature, payload)
    except (InvalidSignature, ValueError):
        return False
    return True


def did_note_location(did: str) -> tuple[str, str]:
    digest = hashlib.sha256(did.encode()).hexdigest()[:16]
    return f"did-{digest[:2]}", digest[2:]
