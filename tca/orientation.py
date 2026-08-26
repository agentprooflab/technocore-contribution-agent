from __future__ import annotations

import re
from typing import Any

from tca.context import ContextError, parse_evidence_ref
from tca.safety import scan_untrusted
from tca.state import State, canonical_json, sha256_text

POLICY_VERSION = "public-orientation-v1"
URL_PATTERN = re.compile(r"\b(?:https?://|file:|data:|javascript:|ssh:)", re.I)


def orientation_cache_key(
    *,
    observation_id: str,
    revision_digest: str,
    coverage_digest: str,
    model_id: str,
    prompt_digest: str,
    policy_version: str = POLICY_VERSION,
) -> str:
    return sha256_text(
        canonical_json(
            {
                "audience": "public",
                "observation_id": observation_id,
                "revision_digest": revision_digest,
                "coverage_digest": coverage_digest,
                "model_id": model_id,
                "prompt_digest": prompt_digest,
                "policy_version": policy_version,
                "schema": "technocore-derived-orientation/v1",
            }
        )
    )


def cache_public_orientation(
    state: State,
    *,
    evidence_id: str,
    text: str,
    coverage_digest: str,
    model_id: str,
    prompt_digest: str,
) -> tuple[str, dict[str, Any]]:
    observation_id, revision_digest = parse_evidence_ref(evidence_id)
    revision = state.observation_revision(observation_id, revision_digest)
    if revision["exposure_class"] != "public":
        raise ContextError("RESTRICTED_ORIENTATION", "restricted evidence cannot be summarized")
    if not text.strip() or len(text) > 500:
        raise ContextError("INVALID_ORIENTATION", "orientation must contain 1 to 500 characters")
    findings = scan_untrusted(text)
    if findings or URL_PATTERN.search(text):
        raise ContextError(
            "UNSAFE_ORIENTATION",
            "orientation contains instruction- or URL-shaped content",
            details={
                "findings": sorted(
                    [finding.code for finding in findings]
                    + (["url_shaped_content"] if URL_PATTERN.search(text) else [])
                )
            },
        )
    key = orientation_cache_key(
        observation_id=observation_id,
        revision_digest=revision_digest,
        coverage_digest=coverage_digest,
        model_id=model_id,
        prompt_digest=prompt_digest,
    )
    payload = {
        "schema": "technocore-derived-orientation/v1",
        "text": text.strip(),
        "authority": "model_derived_untrusted",
        "generated_from_revision": revision_digest,
        "evidence_ids": [evidence_id],
        "coverage_digest": coverage_digest,
        "model_id": model_id,
        "prompt_digest": prompt_digest,
        "policy_version": POLICY_VERSION,
    }
    state.put_orientation(
        cache_key=key,
        observation_id=observation_id,
        revision_digest=revision_digest,
        coverage_digest=coverage_digest,
        exposure_class="public",
        model_id=model_id,
        prompt_digest=prompt_digest,
        policy_version=POLICY_VERSION,
        payload=payload,
    )
    return key, payload
