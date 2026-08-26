#!/bin/zsh
set -eu

if [[ "${1:-}" != "--approve" ]]; then
  echo "Refusing to modify launchd without --approve" >&2
  exit 2
fi

project_root="$(cd "$(dirname "$0")/.." && pwd)"
template="$project_root/launchd/com.technocore.tca.observer.plist.template"
destination="$HOME/Library/LaunchAgents/com.technocore.tca.observer.plist"
uv_bin="$(command -v uv)"
runtime_root="$HOME/.local/share/tca/runtime-0.1.0"
runtime_script_template="$project_root/launchd/runtime-observe.sh.template"
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/.local/share/tca/logs" \
  "$runtime_root/config"

"$uv_bin" venv "$runtime_root/.venv" --python 3.12 --allow-existing
"$uv_bin" pip install --python "$runtime_root/.venv/bin/python" --reinstall "$project_root"
cp "$project_root/config/targets.toml" "$runtime_root/config/targets.toml"
sed "s|__RUNTIME_ROOT__|$runtime_root|g" "$runtime_script_template" \
  >"$runtime_root/observe.sh"
chmod 700 "$runtime_root/observe.sh"

sed -e "s|__RUNTIME_ROOT__|$runtime_root|g" -e "s|__HOME__|$HOME|g" \
  "$template" >"$destination"
launchctl bootout "gui/$(id -u)/com.technocore.tca.observer" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$destination"
echo "Installed read-only observer: $destination"
