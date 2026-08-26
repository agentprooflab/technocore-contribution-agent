# Technocore Contribution Agent

Contribution-first automation for Technocore with one durable Ed25519 DID, read-only discovery,
deterministic candidate ranking, digest-locked approval bundles, idempotent publication, and signed
public evidence.

The software cannot guarantee a FLOP allocation. It deliberately optimizes for sustained,
difficult-to-fake usefulness rather than identity count or message volume.

## Safety model

- The first 48 hours are read-only. Identity creation and every external write are blocked.
- The private Ed25519 seed lives only in macOS Keychain. GitHub Actions never receives it.
- Official X instructions are trusted only when both handle and numeric account ID match the
  allowlist in `config/targets.toml`.
- Technocore content is stored as untrusted data. URLs and instructions found in rooms are never
  executed.
- Every publication batch requires `--approve` plus the exact SHA-256 displayed in `APPROVAL.md`.
- Wallet activity, token purchases, contract addresses, fund transfers, identity multiplication,
  and private-key language are blocked from outbound text.

## Install and start the shadow period

```bash
uv sync --frozen
uv run tca observe
uv run tca rank
uv run tca status
```

`observe` starts the 48-hour clock on first use. It reads GitHub, allowlisted X timelines, and the
configured Technocore rooms. The state database defaults to `~/.local/share/tca/state.db` and is
never committed.

GitHub Actions runs GitHub-only observation every 30 minutes with read-only repository permission.
For local ten-minute monitoring, review and run `scripts/install-launchd.sh --approve`; the installer
copies a non-editable runtime and public configuration to `~/.local/share/tca/runtime-0.1.0`, then
registers the observer. The runtime copy avoids granting a background process access to Documents.
Re-run the installer after upgrading the project.

## Identity gate

Do not create another public `awesome-technocore` list. During the shadow period:

1. Create the approved pseudonymous GitHub organization and X identity.
2. Put their public names in `config/targets.toml`.
3. Authenticate the pseudonymous GitHub user with an isolated `GH_CONFIG_DIR`; never switch the
   default `gh` session used by your personal account.
4. Log the pseudonymous X account into a separate Chrome profile and put that profile name in
   `x_chrome_profile`; pin the account's numeric ID in `x_user_id`.
5. Keep GitHub organization membership private if the platform permits it.
6. Wait until `tca status` reports `shadow_complete: true`.

Example isolated GitHub authentication after the account exists:

```bash
GH_CONFIG_DIR="$HOME/.config/gh-agentproof" gh auth login --hostname github.com --git-protocol ssh --web
GH_CONFIG_DIR="$HOME/.config/gh-agentproof" gh auth status
```

The publisher always supplies this isolated configuration directory to `gh` and the configured
dedicated Chrome profile to `bird`. Before posting, it requires both the expected handle and numeric
account ID. It will not fall back to the machine's personal sessions.

Then create exactly one DID:

```bash
uv run tca identity init
uv run tca identity status
uv run tca identity binding
```

The command refuses to replace an existing Keychain identity. Publish the sharded DID note only as
an independently approved external action:

```bash
uv run tca identity publish-note --approve
```

The binding is voluntary evidence, not proof of personhood, uniqueness, honesty, reward
eligibility, or wallet ownership.

## Workflow

### Observe and rank

```bash
uv run tca observe
uv run tca rank
uv run tca status
```

Priorities are fixed: official task 100, maintainer request 80, reproducible upstream defect 60,
first-party ecosystem request 50, technical question 20, generic promotion 0. Spoofed accounts and
prompt-injection-shaped messages are quarantined.

### Prepare a publication batch

The agent may build and test without approval. It may not post. A minimal official-task bundle:

```bash
uv run tca prepare cand-EXAMPLE \
  --kind testnet_task \
  --artifact-url https://github.com/PSEUDONYMOUS_ORG/PROJECT \
  --commit-sha FULL_COMMIT_SHA \
  --test-command 'uv run pytest -q' \
  --workdir /absolute/path/to/project \
  --technocore-text 'Completed the documented task; method, tests, and limitations are at ARTIFACT_URL.' \
  --x-text 'Published a reproducible Technocore contribution with tests and signed evidence: ARTIFACT_URL'
```

For a pull request, also pass `--github-repo`, `--github-title`, `--github-body-file`,
`--github-head`, and optionally `--github-base`. The produced `APPROVAL.md` contains the exact diff
metadata, test digest, messages, external actions, and bundle digest.

### Publish once

```bash
uv run tca publish bundle-EXAMPLE \
  --bundle-sha EXACT_SHA256_FROM_APPROVAL_MD \
  --approve
```

The publisher submits GitHub first, then a signed Technocore message, then X. Successful actions are
recorded and skipped on retries. A timed-out signed write is searched by DID and nonce; an unknown
outcome stops for review instead of retrying blindly.

### Verify evidence and history

```bash
uv run tca verify evidence/bundle-EXAMPLE.json
uv run tca site
uv run tca site --check
```

Evidence uses `technocore-contribution-evidence/v1`. The static history keeps invalid records
visible instead of silently deleting them and can be served by GitHub Pages from `docs/`.

## Contribution policy

- At most one open upstream PR and two ordinary publication batches per UTC week.
- Official tasks may bypass the weekly batch count, never approval or safety gates.
- Never take an issue with an active PR or an author who already offered a patch.
- Prefer a failing regression test and a bounded fix. Do not invent work when no defect exists.
- If Flop Labs selects an official ecosystem list, contribute there. Otherwise target
  `zunmax/awesome-technocore` with automated validation rather than another competing list.
- Post only completed technical work, original findings, or useful answers. No heartbeats,
  self-conversations, copied templates, or disposable DIDs.

## Development

```bash
uv run ruff check .
uv run pytest
uv run tca site --check
```

The observer and tests are safe to run during shadow mode. `publish`, `identity init`, and
`identity publish-note` are deliberately unavailable until their gates pass.
