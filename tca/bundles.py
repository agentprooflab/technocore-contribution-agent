from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tca.config import Config
from tca.safety import require_safe_outbound
from tca.state import State, iso_now


@dataclass(frozen=True)
class PrepareOptions:
    kind: str
    artifact_url: str
    commit_sha: str | None = None
    test_command: str | None = None
    workdir: Path | None = None
    technocore_room: str | None = None
    technocore_text: str | None = None
    x_text: str | None = None
    github_repo: str | None = None
    github_title: str | None = None
    github_body_file: Path | None = None
    github_head: str | None = None
    github_base: str = "main"


def _run_test(command: str | None, workdir: Path) -> tuple[dict[str, Any], str]:
    if not command:
        content = "No test command supplied; result is not_applicable.\n"
        return {
            "command": None,
            "result": "not_applicable",
            "log_sha256": hashlib.sha256(content.encode()).hexdigest(),
        }, content
    arguments = shlex.split(command)
    if not arguments:
        raise ValueError("test command is empty")
    result = subprocess.run(
        arguments,
        cwd=workdir,
        capture_output=True,
        text=True,
        timeout=1800,
        check=False,
    )
    content = f"$ {shlex.join(arguments)}\n{result.stdout}\n{result.stderr}"
    outcome = "pass" if result.returncode == 0 else "fail"
    return {
        "command": shlex.join(arguments),
        "result": outcome,
        "log_sha256": hashlib.sha256(content.encode()).hexdigest(),
        "exit_code": result.returncode,
    }, content


def _bundle_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(
    config: Config, state: State, candidate_id: str, options: PrepareOptions
) -> dict[str, Any]:
    candidate = state.candidate(candidate_id)
    if candidate["status"] != "ready":
        raise RuntimeError(f"candidate is {candidate['status']}, not ready")
    outbound = [text for text in (options.technocore_text, options.x_text) if text]
    require_safe_outbound(*outbound)
    if options.github_title:
        require_safe_outbound(options.github_title)
    workdir = (options.workdir or config.project_root).resolve()
    tests, test_log = _run_test(options.test_command, workdir)
    if tests["result"] == "fail":
        raise RuntimeError("test command failed; no approval bundle was created")

    bundle_id = "bundle-" + hashlib.sha256(f"{candidate_id}:{iso_now()}".encode()).hexdigest()[:16]
    bundle_dir = config.observer.state_dir / "outbox" / bundle_id
    bundle_dir.mkdir(parents=True, exist_ok=False)
    (bundle_dir / "test.log").write_text(test_log)

    actions: list[dict[str, Any]] = []
    if options.github_repo:
        required = (
            options.github_title,
            options.github_body_file,
            options.github_head,
        )
        if not all(required):
            raise ValueError("GitHub PR action requires title, body file, and head")
        body_copy = bundle_dir / "pr-body.md"
        body_copy.write_text(options.github_body_file.resolve().read_text())
        actions.append(
            {
                "type": "github_pr",
                "repo": options.github_repo,
                "title": options.github_title,
                "body_file": str(body_copy),
                "head": options.github_head,
                "base": options.github_base,
            }
        )
    if options.technocore_text:
        actions.append(
            {
                "type": "technocore",
                "room": options.technocore_room or config.publishing.technocore_room,
                "text": options.technocore_text,
            }
        )
    if options.x_text:
        actions.append({"type": "x", "text": options.x_text})

    bundle = {
        "schema": "technocore-approval-bundle/v1",
        "id": bundle_id,
        "candidate": {
            "id": candidate_id,
            "category": candidate["category"],
            "priority": candidate["priority"],
            "source": candidate["source"],
            "source_url": candidate["url"],
            "title": candidate["title"],
            "body": candidate["body"],
            "authoritative": bool(candidate["authoritative"]),
        },
        "kind": options.kind,
        "artifact_url": options.artifact_url,
        "commit_sha": options.commit_sha,
        "tests": tests,
        "actions": actions,
        "security": {
            "private_key_in_bundle": False,
            "external_writes": [action["type"] for action in actions],
            "requires_batch_approval": True,
        },
        "created_at": iso_now(),
    }
    bundle_path = bundle_dir / "bundle.json"
    bundle_path.write_text(json.dumps(bundle, indent=2, sort_keys=True) + "\n")
    digest = _bundle_digest(bundle_path)
    approval = [
        f"# Approval bundle {bundle_id}",
        "",
        f"- SHA-256: `{digest}`",
        (
            f"- Candidate: `{candidate_id}` "
            f"({candidate['category']}, priority {candidate['priority']})"
        ),
        f"- Artifact: {options.artifact_url}",
        f"- Tests: {tests['result']}",
        f"- External writes: {', '.join(action['type'] for action in actions) or 'none'}",
        "",
        "## Source observation",
        "",
        candidate["body"],
        "",
        "## Actions",
        "",
        "```json",
        json.dumps(actions, indent=2, sort_keys=True),
        "```",
        "",
        "Publishing requires the exact bundle digest and `--approve`.",
    ]
    (bundle_dir / "APPROVAL.md").write_text("\n".join(approval) + "\n")
    state.add_bundle(
        {
            "id": bundle_id,
            "candidate_id": candidate_id,
            "path": str(bundle_path),
            "sha256": digest,
            "status": "prepared",
            "created_at": bundle["created_at"],
        }
    )
    state.set_candidate_status(candidate_id, "prepared")
    return {"id": bundle_id, "path": str(bundle_path), "sha256": digest}
