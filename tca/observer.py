from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from tca.config import Config
from tca.safety import scan_untrusted
from tca.state import StaleCursorError, State, iso_now

TASK_WORDS = (
    "specific task",
    "task",
    "testnet",
    "faucet",
    "airdrop",
    "rewarded",
    "contribute",
)


def _run_json(command: list[str]) -> Any:
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def _github_observations(config: Config) -> list[dict[str, Any]]:
    if not shutil.which("gh"):
        raise RuntimeError("gh CLI is unavailable")
    repo = config.github.upstream_repo
    issues = _run_json(
        [
            "gh",
            "api",
            f"repos/{repo}/issues?state=all&per_page=100&sort=updated&direction=desc",
        ]
    )
    items: list[dict[str, Any]] = []
    maintainers = {login.lower() for login in config.github.maintainer_logins}
    for issue in issues:
        is_pr = "pull_request" in issue
        author = issue.get("user") or {}
        association = str(issue.get("author_association", "")).upper()
        authoritative = (
            association in {"OWNER", "MEMBER", "COLLABORATOR"}
            or str(author.get("login", "")).lower() in maintainers
        )
        number = str(issue["number"])
        issue_state = str(issue.get("state", "open"))
        items.append(
            {
                "id": f"github:{repo}:{'pr' if is_pr else 'issue'}:{number}",
                "source": "github",
                "external_id": number,
                "actor_id": str(author.get("id", "")),
                "actor_username": author.get("login"),
                "kind": (
                    "pull_request"
                    if is_pr
                    else ("issue" if issue_state == "open" else "closed_issue")
                ),
                "title": issue.get("title", ""),
                "body": issue.get("body") or "",
                "url": issue.get("html_url"),
                "created_at": issue.get("created_at"),
                "observed_at": iso_now(),
                "authoritative": authoritative,
                "source_state": issue_state,
                "raw": issue,
            }
        )
    return items


def _x_posts_to_observations(
    account: Any, posts: list[dict[str, Any]], *, observed_at: str | None = None
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    observed = observed_at or iso_now()
    for post in posts:
        author = post.get("author") or {}
        author_id = str(post.get("authorId", ""))
        username = str(author.get("username", ""))
        authoritative = (
            author_id == account.user_id and username.lower() == account.username.lower()
        )
        text = post.get("text") or ""
        kind = (
            "official_task" if any(word in text.lower() for word in TASK_WORDS) else "announcement"
        )
        items.append(
            {
                "id": f"x:{post['id']}",
                "source": "x",
                "external_id": str(post["id"]),
                "actor_id": author_id,
                "actor_username": username,
                "kind": kind,
                "title": text.splitlines()[0][:180],
                "body": text,
                "url": f"https://x.com/{username}/status/{post['id']}",
                "created_at": post.get("createdAt"),
                "observed_at": observed,
                "authoritative": authoritative,
                "source_state": "visible",
                "raw": post,
            }
        )
    return items


def _x_observations(config: Config) -> list[dict[str, Any]]:
    if not shutil.which("bird"):
        raise RuntimeError("bird CLI is unavailable")
    since = (datetime.now(UTC) - timedelta(days=3)).date().isoformat()
    items: list[dict[str, Any]] = []
    for account in config.official_x:
        bird_command = ["bird", "--cookie-source", "chrome"]
        if config.identity.x_chrome_profile:
            bird_command.extend(["--chrome-profile", config.identity.x_chrome_profile])
        bird_command.extend(
            [
                "search",
                f"from:{account.username} since:{since}",
                "-n",
                "50",
                "--json",
            ]
        )
        posts = _run_json(bird_command)
        items.extend(_x_posts_to_observations(account, posts))
    return items


def _get_json(url: str) -> Any:
    request = Request(url, headers={"User-Agent": "tca/0.1 (+read-only observer)"})
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def _sequence_ranges(sequences: list[int], state: str) -> list[tuple[int, int, str]]:
    if not sequences:
        return []
    values = sorted(set(sequences))
    ranges: list[tuple[int, int, str]] = []
    start = previous = values[0]
    for value in values[1:]:
        if value != previous + 1:
            ranges.append((start, previous, state))
            start = value
        previous = value
    ranges.append((start, previous, state))
    return ranges


def _technocore_item(
    config: Config, room: str, epoch: int, message: dict[str, Any]
) -> dict[str, Any]:
    text = str(message.get("text", ""))
    sequence = int(message["seq"])
    nonce = message.get("nonce")
    findings = scan_untrusted(text)
    external = f"{room}:{sequence}"
    anchor = f"{room}:{sequence}" if epoch == 0 else f"{room}:{epoch}:{sequence}"
    suffix = f"?nonce={nonce}" if nonce is not None else ""
    return {
        "id": f"technocore:{anchor}",
        "source": "technocore",
        "external_id": external,
        "actor_id": str(message.get("from", "")),
        "actor_username": str(message.get("from", "")),
        "kind": "technical_question" if "?" in text else "room_message",
        "title": text[:180],
        "body": text,
        "url": (f"{config.observer.technocore_base_url}/humans#r/{room}/{sequence}{suffix}"),
        "created_at": message.get("ts"),
        "observed_at": iso_now(),
        "authoritative": False,
        "exposure_class": (
            "restricted" if room.startswith(("p-", "mb-", "e-p-", "mb-p-")) else "public"
        ),
        "raw": {
            **message,
            "room": room,
            "local_epoch": epoch,
            "safety_findings": [finding.code for finding in findings],
        },
    }


def _observe_technocore(config: Config, state: State) -> int:
    inserted = 0
    for room in config.observer.rooms:
        cursor_row = state.source_cursor("technocore", room)
        cursor = str(cursor_row["cursor"]) if cursor_row and cursor_row["cursor"] else None
        epoch = int(cursor_row["epoch"]) if cursor_row else 0
        if cursor_row and cursor_row["state"] == "epoch_ambiguous":
            continue
        params: dict[str, Any] = {"format": "json", "limit": 200}
        if cursor is not None:
            tail = _get_json(
                f"{config.observer.technocore_base_url}/r/{room}?"
                f"{urlencode({'format': 'json', 'limit': 1})}"
            )
            actual_tail = int(tail.get("last_seq") or 0)
            if actual_tail < int(cursor):
                state.commit_observation_page(
                    source="technocore",
                    scope=room,
                    epoch=epoch,
                    expected_cursor=cursor,
                    observations=[],
                    coverage_ranges=[],
                    next_cursor=cursor,
                    cursor_state="epoch_ambiguous",
                    exposure_class=(
                        "restricted"
                        if room.startswith(("p-", "mb-", "e-p-", "mb-p-"))
                        else "public"
                    ),
                )
                continue
            params["since"] = cursor
        payload = _get_json(f"{config.observer.technocore_base_url}/r/{room}?{urlencode(params)}")
        messages = sorted(payload.get("messages", []), key=lambda item: int(item["seq"]))
        sequences = [int(message["seq"]) for message in messages]
        reported_last = int(payload.get("last_seq") or 0)

        if cursor is not None and reported_last < int(cursor):
            state.commit_observation_page(
                source="technocore",
                scope=room,
                epoch=epoch,
                expected_cursor=cursor,
                observations=[],
                coverage_ranges=[],
                next_cursor=cursor,
                cursor_state="epoch_ambiguous",
            )
            continue

        coverage = _sequence_ranges(sequences, "observed")
        if cursor is not None and sequences and sequences[0] > int(cursor) + 1:
            coverage.append((int(cursor) + 1, sequences[0] - 1, "confirmed_lost"))
        for left, right in zip(sequences, sequences[1:], strict=False):
            if right > left + 1:
                coverage.append((left + 1, right - 1, "unknown_gap"))
        if sequences and reported_last > sequences[-1]:
            coverage.append((sequences[-1] + 1, reported_last, "pending_fetch"))
        elif cursor is not None and not sequences and reported_last > int(cursor):
            coverage.append((int(cursor) + 1, reported_last, "pending_fetch"))

        items = [_technocore_item(config, room, epoch, message) for message in messages]
        next_cursor = str(max(sequences)) if sequences else cursor
        inserted += state.commit_observation_page(
            source="technocore",
            scope=room,
            epoch=epoch,
            expected_cursor=cursor,
            observations=items,
            coverage_ranges=coverage,
            next_cursor=next_cursor,
            exposure_class=(
                "restricted" if room.startswith(("p-", "mb-", "e-p-", "mb-p-")) else "public"
            ),
        )
    return inserted


def observe(config: Config, state: State, github_only: bool = False) -> dict[str, Any]:
    state.ensure_shadow_started()
    sources: list[tuple[str, Any]] = [("github", _github_observations)]
    if not github_only:
        sources.append(("x", _x_observations))
    inserted: dict[str, int] = {}
    errors: dict[str, str] = {}
    for name, collector in sources:
        try:
            count = 0
            for item in collector(config):
                count += int(state.upsert_observation(item))
            inserted[name] = count
            if name == "github":
                state.set_source_health("github", config.github.upstream_repo, "sampled")
            elif name == "x":
                state.set_source_health("x", "official_accounts", "sampled")
        except (
            RuntimeError,
            subprocess.CalledProcessError,
            HTTPError,
            URLError,
            TimeoutError,
        ) as exc:
            errors[name] = str(exc)
            scope = config.github.upstream_repo if name == "github" else "official_accounts"
            state.set_source_health(name, scope, "unavailable")
    if not github_only:
        try:
            inserted["technocore"] = _observe_technocore(config, state)
        except (
            RuntimeError,
            StaleCursorError,
            HTTPError,
            URLError,
            TimeoutError,
        ) as exc:
            errors["technocore"] = str(exc)
            for room in config.observer.rooms:
                state.set_source_health(
                    "technocore",
                    room,
                    "unavailable",
                    exposure_class=(
                        "restricted"
                        if room.startswith(("p-", "mb-", "e-p-", "mb-p-"))
                        else "public"
                    ),
                )
    report = {"inserted": inserted, "errors": errors, "observed_at": iso_now()}
    state.set_meta("last_observe_report", json.dumps(report, sort_keys=True))
    state.set_meta("last_observe_at", report["observed_at"])
    return report


def body_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
