# Context evaluation

The committed repetition-stress corpus contains 30 official-source positives, 30 hard negatives,
and 240 noisy room observations. Results are claims about this pinned corpus only. Official matching
is derived through the X adapter, and recall is evaluated across paged 800-unit briefs. No request
reduction or population-wide task-success claim is made.

Run `uv run python -m evals.run_context_eval --write` to regenerate the reports and `--verify` to
require byte-equivalent JSON data.
