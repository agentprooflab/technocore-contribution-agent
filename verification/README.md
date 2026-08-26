# Verification

`slices.yaml` is JSON-compatible YAML and is parsed with the Python standard library. Every
blocking gate is conjunctive: its command must exit successfully and its measured value must satisfy
the registered comparator and threshold. `--verify` recomputes and compares the complete report.
The report pins a deterministic source manifest, fixture digest, commands, measurements, and result.

The contract gate compares a fixed golden brief digest. The side-effect gate executes hostile content
while instrumenting network, HTTP, subprocess, browser, shell, and Keychain sinks; it records the
number and names of attempted calls. These are measurements, not labels inferred from test exit codes.

Release verification uses exact file-byte SHA-256 digests. Directory manifests sort relative paths
lexicographically before hashing. Signing the final manifest is a local, separately approved action;
CI never receives the AgentProof private key.
