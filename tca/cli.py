from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

from tca.bundles import PrepareOptions, prepare
from tca.config import Config, load_config
from tca.context import (
    ContextError,
    acknowledge_observations,
    build_brief,
    check_collisions,
    coverage_report,
    expand_observations,
)
from tca.evidence import (
    BINDING_SCHEMA,
    load_record,
    sign_account_binding,
    verify_account_binding,
    verify_record,
    write_record,
)
from tca.identity import Identity, MacOSKeychain
from tca.observer import observe
from tca.publisher import publish_bundle, publish_identity_note, reconcile_bundle_receipt
from tca.ranking import rank
from tca.site import build_site
from tca.state import State, iso_now


def _json(value: object) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


def _runtime(config_path: Path | None) -> tuple[Config, State, Identity]:
    config = load_config(config_path)
    state = State(config.state_path)
    keychain = MacOSKeychain(
        service=config.identity.keychain_service,
        account=config.identity.keychain_account,
    )
    return config, state, Identity(keychain)


def _format_duration(value: timedelta) -> str:
    seconds = max(int(value.total_seconds()), 0)
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    return f"{hours}h {minutes}m"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tca")
    parser.add_argument("--config", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)

    observe_parser = sub.add_parser("observe", help="collect allowlisted sources read-only")
    observe_parser.add_argument("--github-only", action="store_true")

    rank_parser = sub.add_parser("rank", help="score observations and reject collisions")
    rank_parser.add_argument("--rescore", action="store_true")

    prepare_parser = sub.add_parser("prepare", help="create a reviewable approval bundle")
    prepare_parser.add_argument("candidate")
    prepare_parser.add_argument("--kind", required=True)
    prepare_parser.add_argument("--artifact-url", required=True)
    prepare_parser.add_argument("--commit-sha")
    prepare_parser.add_argument("--test-command")
    prepare_parser.add_argument("--workdir", type=Path)
    prepare_parser.add_argument("--technocore-room")
    prepare_parser.add_argument("--technocore-text")
    prepare_parser.add_argument("--x-text")
    prepare_parser.add_argument("--github-repo")
    prepare_parser.add_argument("--github-title")
    prepare_parser.add_argument("--github-body-file", type=Path)
    prepare_parser.add_argument("--github-head")
    prepare_parser.add_argument("--github-base", default="main")

    publish_parser = sub.add_parser("publish", help="publish an approved bundle idempotently")
    publish_parser.add_argument("bundle")
    publish_parser.add_argument("--bundle-sha", required=True)
    publish_parser.add_argument("--approve", action="store_true")

    verify_parser = sub.add_parser("verify", help="verify one evidence record")
    verify_parser.add_argument("evidence", type=Path)

    reconcile_parser = sub.add_parser(
        "reconcile", help="repair a stored Technocore receipt without reposting"
    )
    reconcile_parser.add_argument("bundle")

    sub.add_parser("status", help="show shadow gate and workflow state")

    brief_parser = sub.add_parser("brief", help="return a token-budgeted attention brief")
    brief_parser.add_argument("--consumer", default="default")
    brief_parser.add_argument("--interest", action="append", default=[])
    brief_parser.add_argument("--mention", action="append", default=[])
    brief_parser.add_argument("--budget", type=int, default=800)
    brief_parser.add_argument("--as-of")
    brief_parser.add_argument(
        "--since",
        help="return observations newer than a completed brief:v2 watermark",
    )
    brief_parser.add_argument("--continue-from")

    expand_parser = sub.add_parser("expand", help="expand exact evidence revisions")
    expand_parser.add_argument("evidence", nargs="+")
    expand_parser.add_argument("--budget", type=int, default=800)

    acknowledge_parser = sub.add_parser(
        "acknowledge", help="locally acknowledge exact evidence revisions"
    )
    acknowledge_parser.add_argument("evidence", nargs="+")
    acknowledge_parser.add_argument("--consumer", default="default")

    sub.add_parser("coverage", help="show source coverage and known gaps")
    collisions_parser = sub.add_parser(
        "collisions", help="check exact duplicate-work and reply references"
    )
    collisions_parser.add_argument("target")

    site_parser = sub.add_parser("site", help="build or check the static evidence history")
    site_parser.add_argument("--check", action="store_true")

    identity_parser = sub.add_parser("identity", help="manage the Keychain-backed DID")
    identity_sub = identity_parser.add_subparsers(dest="identity_command", required=True)
    identity_sub.add_parser("status")
    identity_sub.add_parser("init")
    binding = identity_sub.add_parser("binding")
    binding.add_argument("--output", type=Path)
    publish_note = identity_sub.add_parser("publish-note")
    publish_note.add_argument("--approve", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        config, state, identity = _runtime(args.config)
        if args.command == "observe":
            _json(observe(config, state, github_only=args.github_only))
        elif args.command == "rank":
            _json(rank(state, rescore=args.rescore))
        elif args.command == "prepare":
            options = PrepareOptions(
                kind=args.kind,
                artifact_url=args.artifact_url,
                commit_sha=args.commit_sha,
                test_command=args.test_command,
                workdir=args.workdir,
                technocore_room=args.technocore_room,
                technocore_text=args.technocore_text,
                x_text=args.x_text,
                github_repo=args.github_repo,
                github_title=args.github_title,
                github_body_file=args.github_body_file,
                github_head=args.github_head,
                github_base=args.github_base,
            )
            _json(prepare(config, state, args.candidate, options))
        elif args.command == "publish":
            _json(
                publish_bundle(
                    config,
                    state,
                    identity,
                    args.bundle,
                    args.bundle_sha,
                    args.approve,
                )
            )
        elif args.command == "verify":
            record = load_record(args.evidence)
            if record.get("schema") == BINDING_SCHEMA:
                valid, errors = verify_account_binding(record)
            else:
                valid, errors = verify_record(record)
            _json({"valid": valid, "errors": errors, "path": str(args.evidence)})
            if not valid:
                raise SystemExit(1)
        elif args.command == "reconcile":
            _json(reconcile_bundle_receipt(config, state, identity, args.bundle))
        elif args.command == "status":
            remaining = state.shadow_remaining(config.observer.shadow_hours)
            _json(
                {
                    "shadow_complete": remaining == timedelta(),
                    "shadow_remaining": _format_duration(remaining),
                    "identity_initialized": identity.exists(),
                    "github_binding_configured": bool(config.identity.github_account),
                    "x_binding_configured": bool(config.identity.x_account),
                    "isolated_github_cli_configured": bool(config.identity.github_cli_config_dir),
                    "isolated_x_profile_configured": bool(config.identity.x_chrome_profile),
                    "x_numeric_identity_configured": bool(config.identity.x_user_id),
                    "last_observe_at": state.get_meta("last_observe_at"),
                    "counts": state.counts(),
                }
            )
        elif args.command == "brief":
            _json(
                build_brief(
                    state,
                    consumer_id=args.consumer,
                    interests=args.interest,
                    mention_markers=args.mention,
                    requested_budget=args.budget,
                    as_of=args.as_of,
                    since=args.since,
                    continuation=args.continue_from,
                )
            )
        elif args.command == "expand":
            _json(expand_observations(state, args.evidence, requested_budget=args.budget))
        elif args.command == "acknowledge":
            _json(acknowledge_observations(state, args.consumer, args.evidence))
        elif args.command == "coverage":
            _json({"schema": "technocore-context-coverage/v1", "sources": coverage_report(state)})
        elif args.command == "collisions":
            _json(check_collisions(state, args.target))
        elif args.command == "site":
            ok = build_site(
                config.publishing.evidence_dir,
                config.publishing.site_dir,
                context_path=config.project_root / "reports" / "dashboard-context.json",
                evaluation_path=config.project_root / "reports" / "context-eval-latest.json",
                check=args.check,
            )
            _json({"ok": ok, "check": args.check, "site": str(config.publishing.site_dir)})
            if not ok:
                raise SystemExit(1)
        elif args.command == "identity":
            if args.identity_command == "status":
                _json(
                    {
                        "initialized": identity.exists(),
                        "did": identity.did() if identity.exists() else None,
                    }
                )
            elif args.identity_command == "init":
                if not state.shadow_complete(config.observer.shadow_hours):
                    raise RuntimeError("48-hour read-only shadow gate has not completed")
                if not config.identity.github_account or not config.identity.x_account:
                    raise RuntimeError("configure approved pseudonymous account bindings first")
                _json({"did": identity.create(), "stored_in": "macOS Keychain"})
            elif args.identity_command == "binding":
                if not identity.exists():
                    raise RuntimeError("identity has not been initialized")
                output = args.output or config.project_root / "bindings" / "identity.json"
                record = sign_account_binding(
                    identity,
                    config.identity.github_account,
                    config.identity.x_account,
                    iso_now(),
                )
                write_record(output, record)
                _json({"binding": str(output), "did": identity.did()})
            elif args.identity_command == "publish-note":
                print(publish_identity_note(config, state, identity, args.approve))
    except ContextError as exc:
        _json(exc.payload())
        raise SystemExit(2) from exc
    except (KeyError, ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
