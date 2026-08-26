from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SafetyFinding:
    code: str
    message: str


OUTBOUND_BLOCK_PATTERNS = {
    "seed_phrase": re.compile(r"\b(seed phrase|mnemonic|recovery phrase)\b", re.I),
    "private_key": re.compile(r"\b(private key|secret key|SIGN_SEED)\b", re.I),
    "funds": re.compile(
        r"\b(send|transfer|deposit|bridge|swap|buy)\b.{0,40}"
        r"\b(funds?|tokens?|crypto|ETH|SOL|USDC)\b",
        re.I,
    ),
    "contract": re.compile(r"\b0x[a-fA-F0-9]{40}\b"),
    "identity_multiplication": re.compile(
        r"\b(multiple|many|batch|farm)\b.{0,30}"
        r"\b(DID|identit(?:y|ies)|wallets?)\b",
        re.I,
    ),
}

PROMPT_INJECTION_PATTERNS = [
    re.compile(r"ignore (all|any|the) previous instructions", re.I),
    re.compile(r"system prompt", re.I),
    re.compile(r"developer message", re.I),
    re.compile(r"run (this|the following) (command|script)", re.I),
    re.compile(r"curl .*/say", re.I),
]


def scan_outbound(text: str) -> list[SafetyFinding]:
    findings: list[SafetyFinding] = []
    for code, pattern in OUTBOUND_BLOCK_PATTERNS.items():
        if pattern.search(text):
            findings.append(SafetyFinding(code, f"outbound text matched blocked pattern: {code}"))
    return findings


def scan_untrusted(text: str) -> list[SafetyFinding]:
    findings: list[SafetyFinding] = []
    for index, pattern in enumerate(PROMPT_INJECTION_PATTERNS, start=1):
        if pattern.search(text):
            findings.append(
                SafetyFinding(f"prompt_injection_{index}", "untrusted text resembles instructions")
            )
    return findings


def require_safe_outbound(*texts: str) -> None:
    findings = [finding for text in texts for finding in scan_outbound(text)]
    if findings:
        raise RuntimeError("; ".join(finding.message for finding in findings))
