#!/bin/zsh
set -eu
umask 077

if [[ "${1:-}" != "--approve" ]]; then
  echo "Refusing to modify launchd without --approve" >&2
  exit 2
fi

project_root="$(cd "$(dirname "$0")/.." && pwd)"
template="$project_root/launchd/com.technocore.tca.observer.plist.template"
runtime_script_template="$project_root/launchd/runtime-observe.sh.template"
destination="$HOME/Library/LaunchAgents/com.technocore.tca.observer.plist"
state_root="$HOME/.local/share/tca"
runtimes_root="$state_root/runtimes"
current_link="$state_root/current"
previous_link="$state_root/previous"
health_root="$state_root/health"
label="gui/$(id -u)/com.technocore.tca.observer"
uv_bin="$(command -v uv)"

if [[ -n "$(git -C "$project_root" status --porcelain --untracked-files=all)" ]]; then
  echo "Refusing to install from a dirty worktree; commit the exact reviewed tree first" >&2
  exit 2
fi
commit="$(git -C "$project_root" rev-parse --verify 'HEAD^{commit}')"
version="$(git -C "$project_root" show "$commit:pyproject.toml" | sed -n \
  's/^version = "\([^"]*\)"/\1/p' | head -1)"
if [[ -z "$version" ]]; then
  echo "Unable to read the committed project version" >&2
  exit 2
fi
runtime_id="$version-$commit"
owner_uid="$(id -u)"
trusted_dirs=(
  "$HOME"
  "$HOME/Library"
  "$HOME/Library/LaunchAgents"
  "$HOME/.local"
  "$HOME/.local/share"
  "$state_root"
  "$state_root/logs"
  "$runtimes_root"
  "$health_root"
)
for trusted_dir in "${trusted_dirs[@]}"; do
  if [[ -L "$trusted_dir" ]]; then
    echo "Refusing symlink in trusted runtime path: $trusted_dir" >&2
    exit 2
  fi
  if [[ -e "$trusted_dir" ]]; then
    if [[ ! -d "$trusted_dir" ]]; then
      echo "Refusing non-directory in trusted runtime path: $trusted_dir" >&2
      exit 2
    fi
    if [[ "$(stat -f '%u' "$trusted_dir")" != "$owner_uid" ]]; then
      echo "Refusing runtime path owned by another user: $trusted_dir" >&2
      exit 2
    fi
  fi
done
for trusted_dir in "${trusted_dirs[@]}"; do
  if [[ ! -d "$trusted_dir" ]]; then
    mkdir "$trusted_dir"
  fi
done
chmod 700 "$HOME/Library/LaunchAgents" "$state_root" "$state_root/logs" \
  "$runtimes_root" "$health_root"

for trusted_file in "$destination" "$current_link" "$previous_link"; do
  if [[ -L "$trusted_file" && "$trusted_file" == "$destination" ]]; then
    echo "Refusing symlink at launchd plist destination: $destination" >&2
    exit 2
  fi
done
for link in "$current_link" "$previous_link"; do
  if [[ -e "$link" && ! -L "$link" ]]; then
    echo "Refusing to replace non-symlink runtime pointer: $link" >&2
    exit 2
  fi
done

touch "$state_root/logs/observer.log" "$state_root/logs/launchd.out.log" \
  "$state_root/logs/launchd.err.log"
chmod 600 "$state_root/logs/observer.log" "$state_root/logs/launchd.out.log" \
  "$state_root/logs/launchd.err.log"

runtime_root="$(mktemp -d "$runtimes_root/$version-$commit.XXXXXXXX")"
runtime_id="$(basename "$runtime_root")"
install_nonce="$(printf '%s' "$runtime_id" | shasum -a 256 | awk '{print $1}')"
health_marker="$health_root/$runtime_id.ready"
chmod 700 "$runtime_root"

build_root="$(mktemp -d "$state_root/install.XXXXXX")"
candidate_plist=""
plist_backup=""
created_runtime=1
mutation_started=0
install_committed=0
rollback_running=0
rollback_recovery_failed=0
destination_existed=0
service_was_loaded=0
old_target=""
old_previous_target=""

cleanup_files() {
  [[ -z "$candidate_plist" || ! -e "$candidate_plist" ]] || rm -f "$candidate_plist"
  [[ -z "$plist_backup" || ! -e "$plist_backup" ]] || rm -f "$plist_backup"
  [[ ! -d "$build_root" ]] || rm -rf "$build_root"
}

restore_symlink() {
  local link="$1"
  local target="$2"
  if [[ -n "$target" ]]; then
    ln -sfn "$target" "$link.restore"
    mv -h "$link.restore" "$link"
  else
    rm -f "$link"
  fi
}

rollback() {
  (( rollback_running == 0 )) || return
  rollback_running=1
  set +e
  if launchctl print "$label" >/dev/null 2>&1; then
    if ! launchctl bootout "$label" >/dev/null 2>&1; then
      rollback_recovery_failed=1
    fi
  fi
  restore_symlink "$current_link" "$old_target" || rollback_recovery_failed=1
  restore_symlink "$previous_link" "$old_previous_target" || rollback_recovery_failed=1
  if (( destination_existed )); then
    cp "$plist_backup" "$destination" || rollback_recovery_failed=1
    chmod 600 "$destination" || rollback_recovery_failed=1
  else
    rm -f "$destination" || rollback_recovery_failed=1
  fi
  if (( service_was_loaded && destination_existed )); then
    if ! launchctl bootstrap "gui/$(id -u)" "$destination" >/dev/null 2>&1 \
      || ! launchctl print "$label" >/dev/null 2>&1; then
      rollback_recovery_failed=1
    fi
  fi
  rm -f "$health_marker"
  set -e
  if (( rollback_recovery_failed )); then
    echo "FATAL: observer rollback could not restore the previously loaded launchd job" >&2
    return 1
  fi
  return 0
}

on_exit() {
  local exit_code=$?
  trap - EXIT HUP INT TERM
  if (( exit_code != 0 && mutation_started && ! install_committed )); then
    rollback || exit_code=70
  fi
  if (( exit_code != 0 && ! rollback_recovery_failed && created_runtime && ! install_committed )); then
    case "$runtime_root" in
      "$runtimes_root"/*) rm -rf "$runtime_root" ;;
      *) echo "Refusing unsafe incomplete-runtime cleanup: $runtime_root" >&2 ;;
    esac
  fi
  cleanup_files
  exit "$exit_code"
}

trap on_exit EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

runtime_file_list() {
  (
    cd "$1"
    find . \( -type f -o -type l \) ! -name runtime-files.sha256 -print | LC_ALL=C sort
  )
}

write_runtime_manifest() {
  local root="$1"
  (
    cd "$root"
    runtime_file_list "$root" | while IFS= read -r runtime_file; do
      shasum -a 256 "$runtime_file"
    done
  ) >"$root/runtime-files.sha256"
  chmod 600 "$root/runtime-files.sha256"
}

verify_runtime() {
  local root="$1"
  local expected="$build_root/expected-files"
  local recorded="$build_root/recorded-files"
  [[ -d "$root" && ! -L "$root" && -f "$root/.complete" \
    && -f "$root/runtime-files.sha256" && -x "$root/.venv/bin/tca" ]] || return 1
  grep -Fqx "commit=$commit" "$root/.complete" || return 1
  (cd "$root" && shasum -a 256 -c runtime-files.sha256 >/dev/null) || return 1
  runtime_file_list "$root" >"$expected"
  sed 's/^[0-9a-f][0-9a-f]*  //' "$root/runtime-files.sha256" | LC_ALL=C sort >"$recorded"
  cmp -s "$expected" "$recorded" || return 1
  PYTHONDONTWRITEBYTECODE=1 TCA_CONFIG="$root/config/targets.toml" \
    TCA_STATE="$build_root/verify.db" "$root/.venv/bin/tca" coverage >/dev/null
}

source_root="$build_root/source"
artifacts_root="$build_root/artifacts"
build_venv="$build_root/build-venv"
mkdir -p "$source_root" "$artifacts_root" "$runtime_root/config" "$runtime_root/install"
git -C "$project_root" archive "$commit" | tar -x -C "$source_root"

"$uv_bin" export --directory "$source_root" --frozen --no-default-groups --no-emit-project \
  --no-header --format requirements.txt --output-file "$artifacts_root/dependencies.lock"
"$uv_bin" export --directory "$source_root" --frozen --only-group build --no-emit-project \
  --no-header --format requirements.txt --output-file "$artifacts_root/build.lock"
for lock_file in "$artifacts_root/dependencies.lock" "$artifacts_root/build.lock"; do
  if grep -E '^[[:alnum:]_.-]+==' "$lock_file" >/dev/null \
    && ! grep -q -- '--hash=sha256:' "$lock_file"; then
    echo "Frozen lock did not contain package hashes: $lock_file" >&2
    exit 2
  fi
done

"$uv_bin" venv "$build_venv" --python 3.12
"$uv_bin" pip install --python "$build_venv/bin/python" --require-hashes \
  -r "$artifacts_root/build.lock"
"$uv_bin" build --wheel "$source_root" --out-dir "$artifacts_root" \
  --python "$build_venv/bin/python" --no-build-isolation --offline
wheel="$(find "$artifacts_root" -maxdepth 1 -name '*.whl' -print | LC_ALL=C sort | head -1)"
[[ -n "$wheel" && -f "$wheel" ]] || { echo "Wheel build did not produce an artifact" >&2; exit 2; }

cp "$wheel" "$runtime_root/install/"
cp "$artifacts_root/dependencies.lock" "$runtime_root/install/dependencies.lock"
cp "$artifacts_root/build.lock" "$runtime_root/install/build.lock"
wheel_name="$(basename "$wheel")"
wheel_sha="$(shasum -a 256 "$runtime_root/install/$wheel_name" | awk '{print $1}')"
printf '%s  %s\n' "$wheel_sha" "$wheel_name" >"$runtime_root/install/wheel.sha256"
(cd "$runtime_root/install" && shasum -a 256 -c wheel.sha256 >/dev/null)

"$uv_bin" venv "$runtime_root/.venv" --python 3.12
"$uv_bin" pip install --python "$runtime_root/.venv/bin/python" --require-hashes \
  -r "$runtime_root/install/dependencies.lock"
"$uv_bin" pip install --python "$runtime_root/.venv/bin/python" --no-deps \
  "$runtime_root/install/$wheel_name"
cp "$source_root/config/targets.toml" "$runtime_root/config/targets.toml"
sed -e "s|__RUNTIME_ROOT__|$runtime_root|g" \
  -e "s|__RUNTIME_ID__|$runtime_id|g" \
  -e "s|__INSTALL_NONCE__|$install_nonce|g" \
  "$runtime_script_template" >"$runtime_root/observe.sh"
chmod 700 "$runtime_root/observe.sh"
chmod 600 "$runtime_root/config/targets.toml" "$runtime_root/install/"*

PYTHONDONTWRITEBYTECODE=1 TCA_CONFIG="$runtime_root/config/targets.toml" \
  TCA_STATE="$build_root/smoke.db" "$runtime_root/.venv/bin/tca" coverage >/dev/null
dependencies_sha="$(shasum -a 256 "$runtime_root/install/dependencies.lock" | awk '{print $1}')"
build_sha="$(shasum -a 256 "$runtime_root/install/build.lock" | awk '{print $1}')"
{
  printf 'runtime_id=%s\n' "$runtime_id"
  printf 'install_nonce=%s\n' "$install_nonce"
  printf 'commit=%s\n' "$commit"
  printf 'wheel_sha256=%s\n' "$wheel_sha"
  printf 'dependencies_sha256=%s\n' "$dependencies_sha"
  printf 'build_sha256=%s\n' "$build_sha"
} >"$runtime_root/.complete"
chmod 600 "$runtime_root/.complete"
chmod -R go-rwx "$runtime_root"
write_runtime_manifest "$runtime_root"
verify_runtime "$runtime_root" || { echo "Installed runtime failed manifest verification" >&2; exit 2; }

candidate_plist="$(mktemp "$state_root/observer.plist.XXXXXX")"
sed -e "s|__RUNTIME_ROOT__|$current_link|g" -e "s|__HOME__|$HOME|g" \
  "$template" >"$candidate_plist"
chmod 600 "$candidate_plist"
plutil -lint "$candidate_plist" >/dev/null

if [[ -L "$current_link" ]]; then
  old_target="$(readlink "$current_link")"
  if [[ "$old_target" != "$state_root"/* || ! -d "$old_target" || -L "$old_target" ]]; then
    echo "Refusing unsafe current runtime target: $old_target" >&2
    exit 2
  fi
fi
if [[ -L "$previous_link" ]]; then
  old_previous_target="$(readlink "$previous_link")"
  if [[ "$old_previous_target" != "$state_root"/* || ! -d "$old_previous_target" \
    || -L "$old_previous_target" ]]; then
    echo "Refusing unsafe previous runtime target: $old_previous_target" >&2
    exit 2
  fi
fi
if [[ -f "$destination" ]]; then
  destination_existed=1
  plist_backup="$(mktemp "$state_root/observer.previous.XXXXXX")"
  cp "$destination" "$plist_backup"
  chmod 600 "$plist_backup"
  if [[ -z "$old_target" ]]; then
    legacy_script="$(sed -n 's|.*<string>\(.*\)/observe\.sh</string>.*|\1|p' "$destination" | head -1)"
    if [[ "$legacy_script" == "$state_root"/* && -d "$legacy_script" \
      && ! -L "$legacy_script" && -x "$legacy_script/observe.sh" ]]; then
      old_target="$legacy_script"
    fi
  fi
fi
if launchctl print "$label" >/dev/null 2>&1; then
  service_was_loaded=1
fi

mutation_started=1
launchctl bootout "$label" >/dev/null 2>&1 || true
ln -sfn "$runtime_root" "$current_link.next"
mv -h "$current_link.next" "$current_link"
mv "$candidate_plist" "$destination"
candidate_plist=""
chmod 600 "$destination"
rm -f "$health_marker"

launchctl bootstrap "gui/$(id -u)" "$destination"
health_timeout="${TCA_HEALTH_TIMEOUT_SECONDS:-60}"
elapsed=0
while [[ ! -f "$health_marker" && "$elapsed" -lt "$health_timeout" ]]; do
  sleep 1
  elapsed=$((elapsed + 1))
done
if [[ ! -f "$health_marker" ]] \
  || ! grep -Fqx "runtime_id=$runtime_id" "$health_marker" \
  || ! grep -Fqx "install_nonce=$install_nonce" "$health_marker"; then
  echo "Observer upgrade did not produce a successful-run health marker" >&2
  exit 1
fi

if [[ -n "$old_target" && "$old_target" != "$runtime_root" ]]; then
  ln -sfn "$old_target" "$previous_link.next"
  mv -h "$previous_link.next" "$previous_link"
fi
if [[ "${TCA_INSTALL_FAILPOINT:-}" == "after_previous_link" ]]; then
  echo "Injected installer failure after previous-link update" >&2
  exit 1
fi

install_committed=1
cleanup_files
trap - EXIT HUP INT TERM
echo "Installed verified read-only observer: $destination -> $runtime_root"
