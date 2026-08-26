#!/bin/zsh
set -eu

project_root="$(cd "$(dirname "$0")/.." && pwd)"
state_root="${TCA_STATE_ROOT:-$HOME/.local/share/tca}"
uv_bin="${TCA_UV_BIN:-$(command -v uv)}"
mkdir -p "$state_root/logs"
cd "$project_root"

{
  printf '%s observe start\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  "$uv_bin" run tca observe
  "$uv_bin" run tca rank
  "$uv_bin" run tca status
} >>"$state_root/logs/observer.log" 2>&1
