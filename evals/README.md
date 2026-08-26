# Context evaluation

The committed corpus contains 30 official-source positives, 30 hard negatives, and 240 noisy room
observations. Results are claims about this pinned corpus only. The request model declares its room
and consumer counts and includes broker collection plus consumer requests.

Run `uv run python -m evals.run_context_eval --write` to regenerate the reports and `--verify` to
require byte-equivalent JSON data.
