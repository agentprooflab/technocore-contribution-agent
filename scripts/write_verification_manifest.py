from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def manifest(paths: list[Path]) -> list[dict[str, str]]:
    return [
        {"path": str(path.relative_to(ROOT)), "sha256": sha256(path)}
        for path in sorted(paths)
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix != ".pyc"
        and path.name != ".gitignore"
        and path.name not in {"verification-manifest.json", "sbom.spdx.json"}
    ]


def main() -> None:
    tracked_groups = [
        ROOT / "schemas",
        ROOT / "evals",
        ROOT / "verification",
        ROOT / "reports",
        ROOT / "docs",
        ROOT / "dist",
    ]
    paths = [path for group in tracked_groups if group.exists() for path in group.rglob("*")]
    artifacts = manifest(paths)
    artifact_digest = hashlib.sha256(
        json.dumps(artifacts, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    dirty = bool(git("status", "--porcelain=v1", "--untracked-files=all"))
    data = {
        "schema": "technocore-brief-verification-manifest/v1",
        "repository": "https://github.com/agentprooflab/technocore-contribution-agent",
        "commit_sha": git("rev-parse", "HEAD"),
        "tree_sha": git("rev-parse", "HEAD^{tree}"),
        "dirty_worktree": dirty,
        "environment": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "uv": subprocess.run(
                ["uv", "--version"], check=True, capture_output=True, text=True
            ).stdout.strip(),
        },
        "packages": {
            "cryptography": importlib.metadata.version("cryptography"),
            "technocore-contribution-agent": "0.2.0",
        },
        "lockfile_sha256": sha256(ROOT / "uv.lock"),
        "artifact_manifest_sha256": artifact_digest,
        "artifacts": artifacts,
        "signature": None,
        "signature_note": (
            "The AgentProof DID signature is added only after local review and explicit approval; "
            "CI never receives the key."
        ),
    }
    output = ROOT / "reports" / "verification-manifest.json"
    output.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    sbom = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": "technocore-contribution-agent-0.2.0",
        "documentNamespace": f"https://agentprooflab.invalid/spdx/{artifact_digest}",
        "packages": [
            {
                "name": "technocore-contribution-agent",
                "SPDXID": "SPDXRef-Package-TCA",
                "versionInfo": "0.2.0",
                "downloadLocation": "NOASSERTION",
                "licenseConcluded": "MIT",
            },
            {
                "name": "cryptography",
                "SPDXID": "SPDXRef-Package-Cryptography",
                "versionInfo": data["packages"]["cryptography"],
                "downloadLocation": "NOASSERTION",
                "licenseConcluded": "NOASSERTION",
            },
        ],
    }
    (ROOT / "reports" / "sbom.spdx.json").write_text(
        json.dumps(sbom, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps({"manifest": str(output), "dirty": dirty}, sort_keys=True))


if __name__ == "__main__":
    main()
