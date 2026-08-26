#!/bin/zsh
set -eu

if [[ "${1:-}" != "--approve" ]]; then
  echo "Refusing to modify launchd without --approve" >&2
  exit 2
fi

project_root="$(cd "$(dirname "$0")/.." && pwd)"
template="$project_root/launchd/com.technocore.tca.observer.plist.template"
destination="$HOME/Library/LaunchAgents/com.technocore.tca.observer.plist"
state_root="$HOME/.local/share/tca"
runtimes_root="$state_root/runtimes"
current_link="$state_root/current"
previous_link="$state_root/previous"
runtime_id="0.2.0-$(git -C "$project_root" rev-parse --short=12 HEAD)"
runtime_root="$runtimes_root/$runtime_id"
runtime_script_template="$project_root/launchd/runtime-observe.sh.template"
uv_bin="$(command -v uv)"
label="gui/$(id -u)/com.technocore.tca.observer"

mkdir -p "$HOME/Library/LaunchAgents" "$state_root/logs" "$runtimes_root"

if [[ ! -d "$runtime_root" ]]; then
  staging="$(mktemp -d "$runtimes_root/.stage.XXXXXX")"
  cleanup_stage() { rm -rf "$staging" }
  trap cleanup_stage EXIT HUP INT TERM
  mkdir -p "$staging/config"
  "$uv_bin" venv "$staging/.venv" --python 3.12
  "$uv_bin" pip install --python "$staging/.venv/bin/python" "$project_root"
  cp "$project_root/config/targets.toml" "$staging/config/targets.toml"
  sed "s|__RUNTIME_ROOT__|$runtime_root|g" "$runtime_script_template" >"$staging/observe.sh"
  chmod 700 "$staging/observe.sh"
  TCA_CONFIG="$staging/config/targets.toml" TCA_STATE="$staging/smoke.db" \
    "$staging/.venv/bin/tca" coverage >/dev/null
  rm -f "$staging/smoke.db" "$staging/smoke.db-shm" "$staging/smoke.db-wal"
  mv "$staging" "$runtime_root"
  trap - EXIT HUP INT TERM
fi

candidate_plist="$(mktemp "$state_root/observer.plist.XXXXXX")"
sed -e "s|__RUNTIME_ROOT__|$current_link|g" -e "s|__HOME__|$HOME|g" \
  "$template" >"$candidate_plist"
plutil -lint "$candidate_plist" >/dev/null

old_target="$(readlink "$current_link" 2>/dev/null || true)"
plist_backup=""
if [[ -f "$destination" ]]; then
  plist_backup="$(mktemp "$state_root/observer.previous.XXXXXX")"
  cp "$destination" "$plist_backup"
fi

rollback() {
  launchctl bootout "$label" 2>/dev/null || true
  if [[ -n "$old_target" ]]; then
    ln -sfn "$old_target" "$current_link.rollback"
    mv -h "$current_link.rollback" "$current_link"
  else
    rm -f "$current_link"
  fi
  if [[ -n "$plist_backup" ]]; then
    cp "$plist_backup" "$destination"
    launchctl bootstrap "gui/$(id -u)" "$destination" 2>/dev/null || true
  fi
}

launchctl bootout "$label" 2>/dev/null || true
ln -sfn "$runtime_root" "$current_link.next"
mv -h "$current_link.next" "$current_link"
mv "$candidate_plist" "$destination"

if ! launchctl bootstrap "gui/$(id -u)" "$destination"; then
  rollback
  echo "Observer upgrade failed; previous launchd configuration restored" >&2
  exit 1
fi

if ! launchctl print "$label" >/dev/null 2>&1; then
  rollback
  echo "Observer upgrade did not register; previous configuration restored" >&2
  exit 1
fi

if [[ -n "$old_target" && "$old_target" != "$runtime_root" ]]; then
  ln -sfn "$old_target" "$previous_link.next"
  mv -h "$previous_link.next" "$previous_link"
fi
rm -f "$plist_backup"
echo "Installed read-only observer: $destination -> $runtime_root"
