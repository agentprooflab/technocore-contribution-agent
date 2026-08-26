#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"

uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run python -m evals.run_context_eval --verify
uv run tca site --check
