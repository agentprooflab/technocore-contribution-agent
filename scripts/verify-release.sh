#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"

./scripts/verify.sh

rm -rf build dist
uv build

verify_dir=$(mktemp -d "${TMPDIR:-/tmp}/tca-cleanroom.XXXXXX")
trap 'rm -rf "$verify_dir"' EXIT HUP INT TERM
python3 -m venv "$verify_dir/venv"
wheel=$(find "$project_dir/dist" -maxdepth 1 -name '*.whl' -print | sort | head -1)
"$verify_dir/venv/bin/python" -m pip install --no-deps "$wheel" >/dev/null

mkdir -p "$verify_dir/home"
cd "$verify_dir"
HOME="$verify_dir/home" TCA_STATE="$verify_dir/state.db" \
  "$verify_dir/venv/bin/tca-mcp" --consumer cleanroom <<'EOF' >"$verify_dir/mcp.jsonl"
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"cleanroom","version":"1"}}}
{"jsonrpc":"2.0","method":"notifications/cancelled","params":{"requestId":99,"reason":"fixture"}}
{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"coverage_report","arguments":{}}}
EOF
cd "$project_dir"

"$verify_dir/venv/bin/python" - "$verify_dir/mcp.jsonl" <<'PY'
import json
import sys

rows = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8")]
assert [row["id"] for row in rows] == [1, 2, 3]
names = [tool["name"] for tool in rows[1]["result"]["tools"]]
assert names == ["get_relevant_updates", "expand_observations", "coverage_report"]
assert rows[2]["result"]["isError"] is False
PY

find dist -maxdepth 1 -type f -print0 | sort -z | xargs -0 shasum -a 256 >dist/SHA256SUMS
uv run python scripts/write_verification_manifest.py

echo "release verification passed"
