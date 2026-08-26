from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="launchd installer is macOS-only")


def write_executable(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(0o700)


def git(project: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=project, check=True, capture_output=True, text=True
    ).stdout.strip()


def committed_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    for relative in (
        "scripts/install-launchd.sh",
        "launchd/runtime-observe.sh.template",
        "launchd/com.technocore.tca.observer.plist.template",
    ):
        destination = project / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, destination)
    (project / "config").mkdir()
    (project / "config/targets.toml").write_text('[fixture]\nname = "public-only"\n')
    (project / "pyproject.toml").write_text('[project]\nname = "fixture"\nversion = "0.2.0"\n')
    (project / "uv.lock").write_text('version = 1\nrevision = 3\nrequires-python = ">=3.12"\n')
    git(project, "init", "-q")
    git(project, "config", "user.name", "Installer Test")
    git(project, "config", "user.email", "installer@example.invalid")
    git(project, "add", ".")
    git(project, "commit", "-qm", "fixture")
    return project


def fake_tools(tmp_path: Path) -> tuple[Path, Path]:
    tools = tmp_path / "tools"
    state = tmp_path / "fake-launchctl"
    state.mkdir()
    write_executable(
        tools / "uv",
        r"""#!/bin/sh
set -eu
command=$1
shift
printf '%s %s\n' "$command" "$*" >>"${FAKE_UV_CALLS:?}"
case "$command" in
  export)
    output=""
    while [ "$#" -gt 0 ]; do
      case "$1" in
        --output-file) output=$2; shift 2 ;;
        *) shift ;;
      esac
    done
    printf 'fake-dependency==1.0 --hash=sha256:%064d\n' 0 >"$output"
    ;;
  build)
    output=""
    while [ "$#" -gt 0 ]; do
      case "$1" in
        --out-dir) output=$2; shift 2 ;;
        *) shift ;;
      esac
    done
    mkdir -p "$output"
    printf 'reviewed wheel bytes\n' >"$output/fixture-0.2.0-py3-none-any.whl"
    ;;
  venv)
    target=$1
    mkdir -p "$target/bin"
    printf '#!/bin/sh\nexec /usr/bin/python3 "$@"\n' >"$target/bin/python"
    chmod 700 "$target/bin/python"
    ;;
  pip)
    python=""
    project_install=0
    while [ "$#" -gt 0 ]; do
      case "$1" in
        --python) python=$2; shift 2 ;;
        --no-deps) project_install=1; shift ;;
        *) shift ;;
      esac
    done
    if [ "$project_install" -eq 1 ]; then
      bin=$(dirname "$python")
      cat >"$bin/tca" <<'EOF'
#!/bin/sh
set -eu
printf '%s\n' "$*" >>"${FAKE_TCA_CALLS:?}"
case "${1:-}" in
  coverage) exit 0 ;;
  observe)
    now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    case "${FAKE_TCA_OBSERVE_MODE:-noop}" in
      noop)
        printf '%s%s%s\n' \
          '{"inserted":{"github":0,"x":0,"technocore":0},' \
          '"errors":{},"observed_at":"' "$now\"}"
        ;;
      total_failure)
        printf '%s%s%s\n' \
          '{"inserted":{},"errors":{"github":"down","x":"down",' \
          '"technocore":"down"},"observed_at":"' "$now\"}"
        ;;
      stale)
        printf '%s%s\n' \
          '{"inserted":{"github":0,"x":0,"technocore":0},"errors":{},' \
          '"observed_at":"2000-01-01T00:00:00Z"}'
        ;;
      missing_source)
        printf '%s%s\n' \
          '{"inserted":{"github":0},"errors":{},"observed_at":"' "$now\"}"
        ;;
      *) printf 'not-json\n' ;;
    esac
    ;;
  rank) printf 'private fixture must not enter observer.log\n'; exit 0 ;;
  *) exit 2 ;;
esac
EOF
      chmod 700 "$bin/tca"
    fi
    ;;
  *) exit 2 ;;
esac
""",
    )
    write_executable(
        tools / "plutil",
        "#!/bin/sh\nexit 0\n",
    )
    write_executable(
        tools / "stat",
        r"""#!/bin/sh
set -eu
for argument in "$@"; do target=$argument; done
if [ -n "${FAKE_WRONG_OWNER_PATH:-}" ] && [ "$target" = "$FAKE_WRONG_OWNER_PATH" ]; then
  printf '99999\n'
  exit 0
fi
exec /usr/bin/stat "$@"
""",
    )
    write_executable(
        tools / "launchctl",
        r"""#!/bin/sh
set -eu
state=${FAKE_LAUNCHCTL_STATE:?}
command=$1
shift
case "$command" in
  print)
    test -f "$state/loaded"
    ;;
  bootout)
    rm -f "$state/loaded"
    ;;
  bootstrap)
    plist=$2
    count=0
    if [ -f "$state/bootstrap-count" ]; then count=$(cat "$state/bootstrap-count"); fi
    count=$((count + 1))
    printf '%s\n' "$count" >"$state/bootstrap-count"
    if [ -n "${FAKE_LAUNCHCTL_FAIL_BOOTSTRAP_NUMBER:-}" ] \
      && [ "$count" -eq "$FAKE_LAUNCHCTL_FAIL_BOOTSTRAP_NUMBER" ]; then
      rm -f "$state/loaded"
      exit 1
    fi
    touch "$state/loaded"
    if [ "${FAKE_LAUNCHCTL_SKIP_RUN:-0}" = 1 ]; then
      exit 0
    fi
    script=$(sed -n 's|.*<string>\(.*\)/observe\.sh</string>.*|\1/observe.sh|p' "$plist" | head -1)
    /bin/zsh "$script"
    ;;
  *) exit 2 ;;
esac
""",
    )
    return tools, state


def installer_environment(tmp_path: Path, tools: Path, launchctl_state: Path) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir()
    calls = tmp_path / "tca-calls.log"
    calls.touch()
    uv_calls = tmp_path / "uv-calls.log"
    uv_calls.touch()
    return {
        **os.environ,
        "HOME": str(home),
        "PATH": f"{tools}:/usr/bin:/bin:/usr/sbin:/sbin",
        "FAKE_LAUNCHCTL_STATE": str(launchctl_state),
        "FAKE_TCA_CALLS": str(calls),
        "FAKE_UV_CALLS": str(uv_calls),
        "TCA_HEALTH_TIMEOUT_SECONDS": "2",
    }


def run_installer(
    project: Path, env: dict[str, str], **overrides: str
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/zsh", str(project / "scripts/install-launchd.sh"), "--approve"],
        env={**env, **overrides},
        capture_output=True,
        text=True,
        check=False,
    )


def install_legacy_job(home: Path, launchctl_state: Path) -> tuple[Path, Path]:
    legacy = home / ".local/share/tca/runtime-0.1.0"
    write_executable(legacy / "observe.sh", "#!/bin/sh\nexit 0\n")
    destination = home / "Library/LaunchAgents/com.technocore.tca.observer.plist"
    destination.parent.mkdir(parents=True)
    destination.write_text(
        f"<plist><dict><array><string>{legacy}/observe.sh</string></array></dict></plist>\n"
    )
    launchctl_state.joinpath("loaded").touch()
    return legacy, destination


def test_installer_executes_final_runtime_and_rolls_back_after_activation(tmp_path) -> None:
    assert 'requires = ["setuptools==80.9.0"]' in (ROOT / "pyproject.toml").read_text()
    assert 'name = "setuptools"' in (ROOT / "uv.lock").read_text()
    project = committed_project(tmp_path)
    tools, launchctl_state = fake_tools(tmp_path)
    env = installer_environment(tmp_path, tools, launchctl_state)
    home = Path(env["HOME"])
    legacy, destination = install_legacy_job(home, launchctl_state)

    first = run_installer(project, env)
    assert first.returncode == 0, first.stderr
    state_root = home / ".local/share/tca"
    current = state_root / "current"
    previous = state_root / "previous"
    runtime = Path(os.readlink(current))
    assert runtime.parent == state_root / "runtimes"
    assert runtime.name.startswith(f"0.2.0-{git(project, 'rev-parse', 'HEAD')}.")
    assert Path(os.readlink(previous)) == legacy
    health_marker = state_root / "health" / f"{runtime.name}.ready"
    assert health_marker.is_file()
    completion = (runtime / ".complete").read_text().splitlines()
    install_nonce = next(
        line.split("=", 1)[1] for line in completion if line.startswith("install_nonce=")
    )
    assert f"install_nonce={install_nonce}" in health_marker.read_text().splitlines()
    assert (runtime / "install/build.lock").is_file()
    assert any(line.startswith("build_sha256=") for line in completion)
    assert subprocess.run([runtime / ".venv/bin/tca", "coverage"], env=env).returncode == 0
    assert ".stage" not in (runtime / ".venv/bin/tca").read_text().splitlines()[0]
    subprocess.run(
        ["shasum", "-a", "256", "-c", "runtime-files.sha256"],
        cwd=runtime,
        check=True,
        capture_output=True,
    )
    assert stat.S_IMODE(runtime.stat().st_mode) == 0o700
    assert stat.S_IMODE((runtime / "config/targets.toml").stat().st_mode) == 0o600
    calls = Path(env["FAKE_TCA_CALLS"]).read_text().splitlines()
    assert "observe" in calls
    assert "rank" in calls
    assert all("status" not in call for call in calls)
    assert "private fixture" not in (state_root / "logs/observer.log").read_text()
    assert stat.S_IMODE((state_root / "logs/observer.log").stat().st_mode) == 0o600
    assert stat.S_IMODE((state_root / "health" / f"{runtime.name}.ready").stat().st_mode) == 0o600
    uv_calls = Path(env["FAKE_UV_CALLS"]).read_text()
    assert "export --directory" in uv_calls
    assert "--only-group build" in uv_calls
    assert "--require-hashes" in uv_calls
    assert "build --wheel" in uv_calls
    assert "--no-build-isolation --offline" in uv_calls

    old_plist = destination.read_bytes()
    old_runtime = runtime
    old_previous = os.readlink(previous)
    (project / "README.md").write_text("second reviewed revision\n")
    git(project, "add", "README.md")
    git(project, "commit", "-qm", "second fixture")
    second_commit = git(project, "rev-parse", "HEAD")

    failed = run_installer(project, env, TCA_INSTALL_FAILPOINT="after_previous_link")
    assert failed.returncode != 0
    assert Path(os.readlink(current)) == old_runtime
    assert os.readlink(previous) == old_previous
    assert destination.read_bytes() == old_plist
    assert launchctl_state.joinpath("loaded").is_file()
    assert list((state_root / "runtimes").glob(f"0.2.0-{second_commit}.*")) == []
    assert list((state_root / "health").glob(f"0.2.0-{second_commit}.*.ready")) == []


def test_fresh_install_never_executes_or_reuses_tampered_current_runtime(tmp_path) -> None:
    project = committed_project(tmp_path)
    tools, launchctl_state = fake_tools(tmp_path)
    env = installer_environment(tmp_path, tools, launchctl_state)
    installed = run_installer(project, env)
    assert installed.returncode == 0, installed.stderr
    state_root = Path(env["HOME"]) / ".local/share/tca"
    first_runtime = Path(os.readlink(state_root / "current"))
    tamper_canary = tmp_path / "tampered-runtime-executed"
    write_executable(
        first_runtime / ".venv/bin/tca",
        f"#!/bin/sh\ntouch '{tamper_canary}'\nexit 99\n",
    )
    manifest = first_runtime / "runtime-files.sha256"
    replacement = hashlib.sha256((first_runtime / ".venv/bin/tca").read_bytes()).hexdigest()
    manifest.write_text(
        "\n".join(
            (f"{replacement}  ./.venv/bin/tca" if line.endswith("  ./.venv/bin/tca") else line)
            for line in manifest.read_text().splitlines()
        )
        + "\n"
    )
    subprocess.run(
        ["shasum", "-a", "256", "-c", "runtime-files.sha256"],
        cwd=first_runtime,
        check=True,
        capture_output=True,
    )

    reinstalled = run_installer(project, env)

    assert reinstalled.returncode == 0, reinstalled.stderr
    second_runtime = Path(os.readlink(state_root / "current"))
    assert second_runtime != first_runtime
    commit = git(project, "rev-parse", "HEAD")
    assert first_runtime.name.startswith(f"0.2.0-{commit}.")
    assert second_runtime.name.startswith(f"0.2.0-{commit}.")
    assert not tamper_canary.exists()
    assert Path(os.readlink(state_root / "previous")) == first_runtime


def test_installer_refuses_non_symlink_runtime_pointer(tmp_path) -> None:
    project = committed_project(tmp_path)
    tools, launchctl_state = fake_tools(tmp_path)
    env = installer_environment(tmp_path, tools, launchctl_state)
    state_root = Path(env["HOME"]) / ".local/share/tca"
    state_root.mkdir(parents=True)
    (state_root / "current").write_text("not a symlink\n")

    refused_pointer = run_installer(project, env)

    assert refused_pointer.returncode == 2
    assert "non-symlink runtime pointer" in refused_pointer.stderr
    assert list((state_root / "runtimes").iterdir()) == []


@pytest.mark.parametrize(
    "relative",
    [
        "Library/LaunchAgents",
        ".local/share/tca",
        ".local/share/tca/logs",
        ".local/share/tca/runtimes",
        ".local/share/tca/health",
    ],
)
def test_installer_rejects_symlinked_trusted_parent_before_mutation(tmp_path, relative) -> None:
    project = committed_project(tmp_path)
    tools, launchctl_state = fake_tools(tmp_path)
    env = installer_environment(tmp_path, tools, launchctl_state)
    home = Path(env["HOME"])
    attacker = tmp_path / "attacker"
    attacker.mkdir()
    trusted_parent = home / relative
    trusted_parent.parent.mkdir(parents=True, exist_ok=True)
    trusted_parent.symlink_to(attacker, target_is_directory=True)

    symlinked = run_installer(project, env)

    assert symlinked.returncode == 2
    assert "symlink in trusted runtime path" in symlinked.stderr
    assert list(attacker.iterdir()) == []


@pytest.mark.parametrize(
    "relative",
    [
        "Library/LaunchAgents",
        ".local/share/tca",
        ".local/share/tca/logs",
        ".local/share/tca/runtimes",
        ".local/share/tca/health",
    ],
)
def test_installer_rejects_wrong_owner_trusted_parent_before_mutation(tmp_path, relative) -> None:
    project = committed_project(tmp_path)
    tools, launchctl_state = fake_tools(tmp_path)
    env = installer_environment(tmp_path, tools, launchctl_state)
    home = Path(env["HOME"])
    wrong_owner = home / relative
    wrong_owner.mkdir(parents=True)
    owner_rejected = run_installer(
        project,
        env,
        FAKE_WRONG_OWNER_PATH=str(wrong_owner),
    )

    assert owner_rejected.returncode == 2
    assert "owned by another user" in owner_rejected.stderr
    state_root = home / ".local/share/tca"
    if relative != ".local/share/tca/runtimes" and (state_root / "runtimes").exists():
        assert list((state_root / "runtimes").iterdir()) == []


def test_registration_without_successful_observer_run_rolls_back_first_install(tmp_path) -> None:
    project = committed_project(tmp_path)
    tools, launchctl_state = fake_tools(tmp_path)
    env = installer_environment(tmp_path, tools, launchctl_state)

    failed = run_installer(project, env, FAKE_LAUNCHCTL_SKIP_RUN="1")

    assert failed.returncode == 1
    assert "health marker" in failed.stderr
    home = Path(env["HOME"])
    state_root = home / ".local/share/tca"
    assert not (state_root / "current").exists()
    assert not (state_root / "previous").exists()
    assert not (home / "Library/LaunchAgents/com.technocore.tca.observer.plist").exists()
    assert list((state_root / "runtimes").iterdir()) == []
    assert not launchctl_state.joinpath("loaded").exists()


@pytest.mark.parametrize("observe_mode", ["total_failure", "stale", "missing_source"])
def test_failed_or_stale_required_source_report_cannot_create_health(
    tmp_path, observe_mode
) -> None:
    project = committed_project(tmp_path)
    tools, launchctl_state = fake_tools(tmp_path)
    env = installer_environment(tmp_path, tools, launchctl_state)

    failed = run_installer(project, env, FAKE_TCA_OBSERVE_MODE=observe_mode)

    assert failed.returncode == 1
    home = Path(env["HOME"])
    state_root = home / ".local/share/tca"
    assert not (state_root / "current").exists()
    assert list((state_root / "health").iterdir()) == []
    assert list((state_root / "runtimes").iterdir()) == []
    observer_log = (state_root / "logs/observer.log").read_text()
    assert "observe health validation failed" in observer_log


def test_rollback_surfaces_fatal_error_when_previous_job_cannot_be_restarted(tmp_path) -> None:
    project = committed_project(tmp_path)
    tools, launchctl_state = fake_tools(tmp_path)
    env = installer_environment(tmp_path, tools, launchctl_state)
    first = run_installer(project, env)
    assert first.returncode == 0, first.stderr
    state_root = Path(env["HOME"]) / ".local/share/tca"
    old_runtime = Path(os.readlink(state_root / "current"))
    old_plist = (
        Path(env["HOME"]) / "Library/LaunchAgents/com.technocore.tca.observer.plist"
    ).read_bytes()
    (project / "README.md").write_text("recovery failure revision\n")
    git(project, "add", "README.md")
    git(project, "commit", "-qm", "recovery failure fixture")
    new_commit = git(project, "rev-parse", "HEAD")

    failed = run_installer(
        project,
        env,
        TCA_INSTALL_FAILPOINT="after_previous_link",
        FAKE_LAUNCHCTL_FAIL_BOOTSTRAP_NUMBER="3",
    )

    assert failed.returncode == 70
    assert "FATAL: observer rollback could not restore" in failed.stderr
    assert Path(os.readlink(state_root / "current")) == old_runtime
    destination = Path(env["HOME"]) / "Library/LaunchAgents/com.technocore.tca.observer.plist"
    assert destination.read_bytes() == old_plist
    assert not launchctl_state.joinpath("loaded").exists()
    assert len(list((state_root / "runtimes").glob(f"0.2.0-{new_commit}.*"))) == 1


def test_candidate_bootstrap_failure_restores_loaded_previous_job_without_false_fatal(
    tmp_path,
) -> None:
    project = committed_project(tmp_path)
    tools, launchctl_state = fake_tools(tmp_path)
    env = installer_environment(tmp_path, tools, launchctl_state)
    first = run_installer(project, env)
    assert first.returncode == 0, first.stderr
    state_root = Path(env["HOME"]) / ".local/share/tca"
    old_runtime = Path(os.readlink(state_root / "current"))
    (project / "README.md").write_text("candidate bootstrap failure revision\n")
    git(project, "add", "README.md")
    git(project, "commit", "-qm", "candidate bootstrap failure fixture")

    failed = run_installer(
        project,
        env,
        FAKE_LAUNCHCTL_FAIL_BOOTSTRAP_NUMBER="2",
    )

    assert failed.returncode == 1
    assert "FATAL:" not in failed.stderr
    assert Path(os.readlink(state_root / "current")) == old_runtime
    assert launchctl_state.joinpath("loaded").is_file()
