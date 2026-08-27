# Context evaluation

The committed repetition-stress corpus contains 30 official-source positives, 30 hard negatives,
and 240 noisy room observations. Results are claims about this pinned corpus only. Official matching
is derived through the X adapter with independent actor-ID and username mutations, and recall is
evaluated across paged 800-unit briefs. The consumer stops when the deterministic
`critical_items_remaining` count reaches zero; lower-priority pages are outside this official-task
retrieval measurement. The comparison baseline carries every observation once, with one content
copy and only the source and authority fields needed for the task. The 50% gate is specific to this
repetition-stress fixture. No request reduction, population-wide task-success, or model-token claim
is made.

Run `uv run python -m evals.run_context_eval --write` to regenerate the reports and `--verify` to
require byte-equivalent JSON data.
