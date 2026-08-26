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
from tca.state import State, iso_now

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
            f"repos/{repo}/issues?state=open&per_page=100&sort=updated&direction=desc",
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
        items.append(
            {
                "id": f"github:{repo}:{'pr' if is_pr else 'issue'}:{number}",
                "source": "github",
                "external_id": number,
                "actor_id": str(author.get("id", "")),
                "actor_username": author.get("login"),
                "kind": "pull_request" if is_pr else "issue",
                "title": issue.get("title", ""),
                "body": issue.get("body") or "",
                "url": issue.get("html_url"),
                "created_at": issue.get("created_at"),
                "observed_at": iso_now(),
                "authoritative": authoritative,
                "raw": issue,
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
        for post in posts:
            author = post.get("author") or {}
            author_id = str(post.get("authorId", ""))
            username = str(author.get("username", ""))
            authoritative = (
                author_id == account.user_id and username.lower() == account.username.lower()
            )
            text = post.get("text") or ""
            kind = (
                "official_task"
                if any(word in text.lower() for word in TASK_WORDS)
                else "announcement"
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
                    "observed_at": iso_now(),
                    "authoritative": authoritative,
                    "raw": post,
                }
            )
    return items


def _get_json(url: str) -> Any:
    request = Request(url, headers={"User-Agent": "tca/0.1 (+read-only observer)"})
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def _technocore_observations(config: Config) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for room in config.observer.rooms:
        query = urlencode({"format": "json", "limit": 200})
        payload = _get_json(f"{config.observer.technocore_base_url}/r/{room}?{query}")
        for message in payload.get("messages", []):
            text = str(message.get("text", ""))
            sequence = str(message.get("seq", ""))
            findings = scan_untrusted(text)
            kind = "technical_question" if "?" in text else "room_message"
            external = f"{room}:{sequence}"
            items.append(
                {
                    "id": f"technocore:{external}",
                    "source": "technocore",
                    "external_id": external,
                    "actor_id": str(message.get("from", "")),
                    "actor_username": str(message.get("from", "")),
                    "kind": kind,
                    "title": text[:180],
                    "body": text,
                    "url": f"{config.observer.technocore_base_url}/humans#r/{room}/{sequence}",
                    "created_at": message.get("ts"),
                    "observed_at": iso_now(),
                    "authoritative": False,
                    "raw": {**message, "safety_findings": [finding.code for finding in findings]},
                }
            )
    return items


def observe(config: Config, state: State, github_only: bool = False) -> dict[str, Any]:
    state.ensure_shadow_started()
    sources: list[tuple[str, Any]] = [("github", _github_observations)]
    if not github_only:
        sources.extend((("x", _x_observations), ("technocore", _technocore_observations)))
    inserted: dict[str, int] = {}
    errors: dict[str, str] = {}
    for name, collector in sources:
        try:
            count = 0
            for item in collector(config):
                count += int(state.upsert_observation(item))
            inserted[name] = count
        except (
            RuntimeError,
            subprocess.CalledProcessError,
            HTTPError,
            URLError,
            TimeoutError,
        ) as exc:
            errors[name] = str(exc)
    report = {"inserted": inserted, "errors": errors, "observed_at": iso_now()}
    state.set_meta("last_observe_report", json.dumps(report, sort_keys=True))
    state.set_meta("last_observe_at", report["observed_at"])
    return report


def body_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
