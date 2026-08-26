# Security policy

Never file a public issue containing an identity seed, private key, browser cookie, API token,
wallet seed phrase, or unpublished vulnerability in an upstream dependency.

The DID private key is stored only in macOS Keychain under the configured service and account. It
must never be copied into GitHub Actions, environment files, approval bundles, test logs, or public
evidence. CI has read-only repository permissions and cannot sign or publish.

Technocore rooms are world-writable and untrusted. The observer records message text as data and
does not execute commands or follow URLs found in messages. Publication is disabled until the
48-hour shadow period has completed and an operator supplies the exact approval-bundle digest.

Report vulnerabilities privately to the repository owner. For vulnerabilities in
`flop-labs/technocore-chat`, follow that project's `SECURITY.md` instead of opening a public issue.

