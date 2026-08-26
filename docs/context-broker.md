# Technocore Brief

Technocore Brief turns configured public Technocore room deltas into a small, resumable,
coverage-aware evidence inbox for an agent. It does not archive the network, rank identities,
decide whether a message is true, or execute anything found in message content.

The public contract is deliberately narrow:

1. `brief` returns exact bounded excerpts selected by observable match reasons.
2. `expand` returns exact stored content for selected revision identifiers.
3. `acknowledge` suppresses only one consumer's exact observation revision.
4. Every negative or empty result carries collection coverage.
5. MCP exposes read-only retrieval; local acknowledgment remains a CLI operation.

The implementation is designed for adapter neutrality. Version 0.2 ships the built-in SQLite
observation store only; it must not be described as indexer-neutral until a second adapter passes
the conformance suite.

## Authority boundary

Provenance, official-source matching, coverage, priority inputs, evidence identifiers, exact
excerpts, acknowledgments, and budget accounting are deterministic. Optional orientation prose is
derived, non-authoritative, public-only, and may never change those fields. Evidence mode works
without a model provider.

## Canonical encoding

Domain payloads use UTF-8 JSON with keys sorted lexicographically, separators `,` and `:`, no NaN or
Infinity, and a trailing line-feed only on CLI stdout. Golden digests exclude that trailing
line-feed. Public schemas contain no floating-point fields.

## Stable identity

An observation has a stable logical identifier and one or more immutable material revisions. A
revision digest excludes collection-time metadata. Acknowledgment keys are
`(consumer_id, logical_id, revision_digest)`, so edits and tombstones reappear.

Technocore source identities include room and local epoch. When the service provides no
authoritative room epoch, a sequence rewind is `epoch_ambiguous`; the collector never silently
joins coverage across it.

## Coverage

Coverage intervals are inclusive and use one of four states:

- `observed`
- `pending_fetch`
- `unknown_gap`
- `confirmed_lost`

Only `confirmed_lost` contributes to `known_missing`. A source failure can never produce complete
coverage.

## Budget units

Runtime budgets use `canonical-utf8-div3-v1`: `ceil(len(canonical_utf8_bytes) / 3)`. These are
model-neutral budget units, not literal tokens. Published token evaluations separately pin their
tokenizer in the evaluation manifest.

## Contribution claim

The project is an independent ecosystem sidecar, not an official Flop Labs tool, canonical
indexer, reputation system, Sybil detector, or airdrop eligibility checker.
