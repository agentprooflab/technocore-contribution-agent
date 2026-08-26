# Verification

`slices.yaml` is JSON-compatible YAML and is parsed with the Python standard library. Every
blocking gate is conjunctive and returns a non-zero process status on failure. Generated slice
reports pin the commit, tree, fixture manifest, commands, counts, and result.

Release verification uses exact file-byte SHA-256 digests. Directory manifests sort relative paths
lexicographically before hashing. Signing the final manifest is a local, separately approved action;
CI never receives the AgentProof private key.
