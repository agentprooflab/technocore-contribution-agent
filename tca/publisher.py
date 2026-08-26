from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from tca.config import Config
from tca.evidence import SCHEMA, sign_record, write_record
from tca.identity import Identity, did_note_location
from tca.safety import require_safe_outbound
from tca.site import build_site
from tca.state import State, iso_now


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(command: list[str], env: dict[str, str] | None = None) -> str:
    process_env = os.environ.copy()
    if env:
        process_env.update(env)
    result = subprocess.run(command, capture_output=True, text=True, check=True, env=process_env)
    return result.stdout.strip()


def _action_key(action: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(action, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _github_env(config: Config) -> dict[str, str]:
    return {
        "GH_CONFIG_DIR": str(Path(config.identity.github_cli_config_dir).expanduser().resolve())
    }


def _github_pr(config: Config, action: dict[str, Any]) -> str:
    if not shutil.which("gh"):
        raise RuntimeError("gh CLI is unavailable")
    existing = json.loads(
        _run(
            [
                "gh",
                "pr",
                "list",
                "-R",
                action["repo"],
                "--head",
                action["head"],
                "--state",
                "all",
                "--json",
                "url,state",
            ],
            env=_github_env(config),
        )
        or "[]"
    )
    if existing:
        return existing[0]["url"]
    output = _run(
        [
            "gh",
            "pr",
            "create",
            "-R",
            action["repo"],
            "--base",
            action["base"],
            "--head",
            action["head"],
            "--title",
            action["title"],
            "--body-file",
            action["body_file"],
        ],
        env=_github_env(config),
    )
    match = re.search(r"https://github\.com/\S+/pull/\d+", output)
    if not match:
        raise RuntimeError(f"could not parse created PR URL: {output}")
    return match.group(0)


def _read_room(base_url: str, room: str) -> list[dict[str, Any]]:
    query = urlencode({"format": "json", "limit": 200})
    request = Request(f"{base_url}/r/{quote(room)}?{query}", headers={"User-Agent": "tca/0.1"})
    with urlopen(request, timeout=20) as response:
        return json.load(response).get("messages", [])


def _find_receipt(base_url: str, room: str, did: str, nonce: int) -> dict[str, Any] | None:
    for message in _read_room(base_url, room):
        if message.get("from") == did and int(message.get("nonce", -1)) == nonce:
            return {**message, "room": room}
    return None


def _receipt_from_url(url: str, text: str, did: str) -> dict[str, Any]:
    match = re.search(r"#r/([^/]+)/([0-9]+)\?nonce=([0-9]+)$", url)
    if not match:
        raise RuntimeError("stored Technocore receipt URL is not parseable")
    return {
        "room": match.group(1),
        "seq": int(match.group(2)),
        "nonce": int(match.group(3)),
        "from": did,
        "text": text,
    }


def _technocore_message(
    config: Config, state: State, identity: Identity, action: dict[str, Any]
) -> dict[str, Any]:
    room = action["room"]
    text = action["text"]
    require_safe_outbound(text)
    nonce = state.next_nonce(f"room:{room}", floor=int(time.time() * 1000))
    did, signature, swept = identity.sign_message(room, nonce, text)
    url = (
        f"{config.observer.technocore_base_url}/r/{quote(room)}/say-signed/"
        f"{quote(did, safe=':')}/{quote(signature)}/{nonce}/{quote(swept, safe='')}"
    )
    request = Request(url, headers={"User-Agent": "tca/0.1"})
    try:
        with urlopen(request, timeout=20) as response:
            body = response.read().decode(errors="replace")
    except HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"Technocore rejected signed write: HTTP {exc.code}: {detail}") from exc
    except (TimeoutError, URLError) as exc:
        receipt = _find_receipt(config.observer.technocore_base_url, room, did, nonce)
        if receipt is None:
            raise RuntimeError(
                "signed write outcome is unknown and no receipt is visible; manual review required"
            ) from exc
        return receipt
    match = re.search(r"\bseq(?:uence)?[=:\s]+(\d+)\b", body, re.I)
    if match:
        return {
            "room": room,
            "seq": int(match.group(1)),
            "nonce": nonce,
            "from": did,
            "text": swept,
        }
    receipt = _find_receipt(config.observer.technocore_base_url, room, did, nonce)
    if receipt is None:
        raise RuntimeError(f"write returned success but no parseable receipt: {body[:300]}")
    return receipt


def _x_post(config: Config, action: dict[str, Any]) -> str:
    if not shutil.which("bird"):
        raise RuntimeError("bird CLI is unavailable")
    require_safe_outbound(action["text"])
    identity_output = _run(
        [
            "bird",
            "--plain",
            "--no-color",
            "--cookie-source",
            "chrome",
            "--chrome-profile",
            config.identity.x_chrome_profile,
            "whoami",
        ]
    )
    expected_handle = f"@{config.identity.x_account}"
    if expected_handle.lower() not in identity_output.lower():
        raise RuntimeError("configured Chrome profile is signed into the wrong X account")
    if config.identity.x_user_id not in identity_output:
        raise RuntimeError("configured Chrome profile returned an unexpected X numeric account ID")
    output = _run(
        [
            "bird",
            "--cookie-source",
            "chrome",
            "--chrome-profile",
            config.identity.x_chrome_profile,
            "tweet",
            action["text"],
        ]
    )
    match = re.search(r"https://x\.com/\S+/status/\d+", output)
    if not match:
        raise RuntimeError(f"could not parse X post URL: {output}")
    return match.group(0)


def _monday_utc(now: datetime) -> datetime:
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight - timedelta(days=midnight.weekday())


def _enforce_publication_budget(config: Config, state: State, bundle: dict[str, Any]) -> None:
    if bundle["candidate"]["category"] == "official_task":
        return
    used = state.ordinary_batches_since(_monday_utc(datetime.now(UTC)))
    if used >= config.github.max_ordinary_batches_per_week:
        raise RuntimeError("ordinary publication batch limit reached for this UTC week")
    upstream_actions = [
        action
        for action in bundle["actions"]
        if action["type"] == "github_pr" and action["repo"] == config.github.upstream_repo
    ]
    if upstream_actions:
        open_prs = json.loads(
            _run(
                [
                    "gh",
                    "pr",
                    "list",
                    "-R",
                    config.github.upstream_repo,
                    "--author",
                    "@me",
                    "--state",
                    "open",
                    "--json",
                    "headRefName,url",
                ],
                env=_github_env(config),
            )
            or "[]"
        )
        intended_heads = {action["head"].split(":")[-1] for action in upstream_actions}
        unrelated_open = [
            item for item in open_prs if item.get("headRefName") not in intended_heads
        ]
        if len(unrelated_open) >= config.github.max_open_upstream_prs:
            raise RuntimeError("open upstream PR limit reached; finish existing work first")


def publish_bundle(
    config: Config,
    state: State,
    identity: Identity,
    bundle_id: str,
    supplied_sha256: str,
    approved: bool,
) -> dict[str, Any]:
    if not approved:
        raise RuntimeError("publication requires --approve")
    if not state.shadow_complete(config.observer.shadow_hours):
        raise RuntimeError("48-hour read-only shadow gate has not completed")
    if not config.identity.github_account or not config.identity.x_account:
        raise RuntimeError(
            "configure the approved pseudonymous GitHub and X account bindings first"
        )
    if (
        not config.identity.github_cli_config_dir
        or not config.identity.x_chrome_profile
        or not config.identity.x_user_id
    ):
        raise RuntimeError("configure isolated GitHub CLI and Chrome profile credentials first")
    bundle_row = state.bundle(bundle_id)
    bundle_path = Path(bundle_row["path"])
    actual_hash = _file_hash(bundle_path)
    if supplied_sha256 != actual_hash or supplied_sha256 != bundle_row["sha256"]:
        raise RuntimeError("bundle digest mismatch; review the changed bundle again")
    bundle = json.loads(bundle_path.read_text())
    if bundle_row["status"] == "published":
        return {"status": "already_published", "bundle": bundle_id}
    if not any(action["type"] == "technocore" for action in bundle["actions"]):
        raise RuntimeError("a contribution batch must include a signed Technocore evidence message")
    _enforce_publication_budget(config, state, bundle)
    state.set_bundle_status(bundle_id, "approved")

    results: dict[str, Any] = {}
    for action in bundle["actions"]:
        action_type = action["type"]
        key = _action_key(action)
        existing = state.action(bundle_id, action_type, key)
        if existing and existing["status"] == "success":
            reused_result: dict[str, Any] = {
                "url": existing["external_url"],
                "reused": True,
            }
            if action_type == "technocore":
                reused_result["receipt"] = _receipt_from_url(
                    str(existing["external_url"]), action["text"], identity.did()
                )
            results[action_type] = reused_result
            continue
        state.set_action(bundle_id, action_type, key, "running")
        try:
            if action_type == "github_pr":
                url = _github_pr(config, action)
                result: dict[str, Any] = {"url": url}
            elif action_type == "technocore":
                receipt = _technocore_message(config, state, identity, action)
                url = (
                    f"{config.observer.technocore_base_url}/humans#r/"
                    f"{action['room']}/{receipt['seq']}?nonce={receipt['nonce']}"
                )
                result = {"url": url, "receipt": receipt}
            elif action_type == "x":
                url = _x_post(config, action)
                result = {"url": url}
            else:
                raise RuntimeError(f"unsupported action type: {action_type}")
        except (RuntimeError, subprocess.CalledProcessError, HTTPError) as exc:
            state.set_action(bundle_id, action_type, key, "needs_review", error=str(exc))
            raise
        state.set_action(bundle_id, action_type, key, "success", external_url=url)
        results[action_type] = result

    technocore_result = results["technocore"]
    receipt = technocore_result.get("receipt")
    if receipt is None:
        raise RuntimeError("Technocore receipt requires manual evidence reconciliation")
    tests = bundle["tests"]
    evidence = {
        "schema": SCHEMA,
        "did": identity.did(),
        "github_account": config.identity.github_account,
        "x_account": config.identity.x_account,
        "kind": bundle["kind"],
        "artifact_url": bundle["artifact_url"],
        "commit_sha": bundle.get("commit_sha"),
        "github_pr": results.get("github_pr", {}).get("url"),
        "x_url": results.get("x", {}).get("url"),
        "published_at": iso_now(),
        "tests": {
            "command": tests.get("command"),
            "result": tests["result"],
            "log_sha256": tests["log_sha256"],
        },
        "technocore": {
            "room": receipt["room"],
            "seq": int(receipt["seq"]),
            "nonce": int(receipt["nonce"]),
            "message_sha256": hashlib.sha256(str(receipt["text"]).encode()).hexdigest(),
        },
    }
    signed = sign_record(evidence, identity)
    evidence_path = config.publishing.evidence_dir / f"{bundle_id}.json"
    write_record(evidence_path, signed)
    build_site(config.publishing.evidence_dir, config.publishing.site_dir)
    state.set_bundle_status(bundle_id, "published")
    return {
        "status": "published",
        "bundle": bundle_id,
        "evidence": str(evidence_path),
        "actions": results,
    }


def publish_identity_note(config: Config, state: State, identity: Identity, approved: bool) -> str:
    if not approved:
        raise RuntimeError("identity publication requires --approve")
    if not state.shadow_complete(config.observer.shadow_hours):
        raise RuntimeError("48-hour read-only shadow gate has not completed")
    if not config.identity.github_account or not config.identity.x_account:
        raise RuntimeError("configure pseudonymous account bindings before identity publication")
    did = identity.did()
    namespace, key = did_note_location(did)
    statement = (
        f"technocore-account-binding-v1 {did} "
        f"github:{config.identity.github_account} x:{config.identity.x_account}"
    )
    binding = identity.sign_bytes(statement.encode())
    value = (
        f"{did} github:{config.identity.github_account} "
        f"x:{config.identity.x_account} binding:{binding}"
    )
    require_safe_outbound(value)
    url = (
        f"{config.observer.technocore_base_url}/kv/{namespace}/{key}/set/"
        f"{quote(value, safe='')}?if_absent=1"
    )
    request = Request(url, headers={"User-Agent": "tca/0.1"})
    with urlopen(request, timeout=20) as response:
        body = response.read().decode(errors="replace")
    state.set_meta(
        "identity_note_url",
        f"{config.observer.technocore_base_url}/kv/{namespace}/{key}",
    )
    return body
