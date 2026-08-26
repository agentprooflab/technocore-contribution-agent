from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class IdentityConfig:
    keychain_service: str
    keychain_account: str
    github_account: str
    x_account: str
    github_cli_config_dir: str
    x_chrome_profile: str


@dataclass(frozen=True)
class ObserverConfig:
    state_dir: Path
    technocore_base_url: str
    shadow_hours: int
    rooms: tuple[str, ...]
    github_interval_minutes: int
    local_interval_minutes: int


@dataclass(frozen=True)
class GitHubConfig:
    upstream_repo: str
    maintainer_logins: tuple[str, ...]
    awesome_fallback: str
    max_open_upstream_prs: int
    max_ordinary_batches_per_week: int


@dataclass(frozen=True)
class OfficialXAccount:
    username: str
    user_id: str


@dataclass(frozen=True)
class PublishingConfig:
    technocore_room: str
    evidence_dir: Path
    site_dir: Path


@dataclass(frozen=True)
class Config:
    project_root: Path
    identity: IdentityConfig
    observer: ObserverConfig
    github: GitHubConfig
    official_x: tuple[OfficialXAccount, ...]
    publishing: PublishingConfig

    @property
    def state_path(self) -> Path:
        override = os.environ.get("TCA_STATE")
        return Path(override).expanduser() if override else self.observer.state_dir / "state.db"


def default_config_path() -> Path:
    override = os.environ.get("TCA_CONFIG")
    if override:
        return Path(override).expanduser().resolve()
    return Path.cwd() / "config" / "targets.toml"


def load_config(path: Path | None = None) -> Config:
    config_path = (path or default_config_path()).resolve()
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)
    project_root = config_path.parent.parent
    observer = raw["observer"]
    publishing = raw["publishing"]
    state_dir = Path(observer["state_dir"]).expanduser()
    evidence_dir = (project_root / publishing["evidence_dir"]).resolve()
    site_dir = (project_root / publishing["site_dir"]).resolve()
    return Config(
        project_root=project_root,
        identity=IdentityConfig(**raw["identity"]),
        observer=ObserverConfig(
            state_dir=state_dir,
            technocore_base_url=observer["technocore_base_url"].rstrip("/"),
            shadow_hours=int(observer["shadow_hours"]),
            rooms=tuple(observer["rooms"]),
            github_interval_minutes=int(observer["github_interval_minutes"]),
            local_interval_minutes=int(observer["local_interval_minutes"]),
        ),
        github=GitHubConfig(
            upstream_repo=raw["github"]["upstream_repo"],
            maintainer_logins=tuple(raw["github"]["maintainer_logins"]),
            awesome_fallback=raw["github"]["awesome_fallback"],
            max_open_upstream_prs=int(raw["github"]["max_open_upstream_prs"]),
            max_ordinary_batches_per_week=int(raw["github"]["max_ordinary_batches_per_week"]),
        ),
        official_x=tuple(OfficialXAccount(**item) for item in raw["official_x"]),
        publishing=PublishingConfig(
            technocore_room=publishing["technocore_room"],
            evidence_dir=evidence_dir,
            site_dir=site_dir,
        ),
    )
