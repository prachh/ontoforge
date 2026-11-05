# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems. Report them privately through [GitHub private vulnerability reporting](https://github.com/prachh/ontoforge/security/advisories/new). Include the steps to reproduce, the affected version or commit, and the impact you expect.

You should get an acknowledgement within a few days. Fixes are released as soon as practical and credited in the changelog unless you prefer otherwise.

## Supported versions

OntoForge is pre-1.0. Security fixes go into the latest release on `main`.

## Security model

OntoForge is designed to analyse untrusted repositories on your own machine:

- Analysed code is parsed statically and **never imported, executed, built or installed**. Repository hooks, submodules and Git LFS objects are not fetched.
- Archives are checked for path traversal, symlinks and size budgets before extraction.
- Parser and AI output are treated as untrusted and validated before use.
- GitHub tokens and API keys entered in the UI stay in server memory; they are never written to the database, logs, exports or URLs.
- The optional Open Ontologies engine runs only a pinned, SHA-256-verified binary with fixed arguments, an isolated data directory and a credential-free environment.
- Ad-hoc SPARQL is restricted to read-only query forms.

It is **not** a multi-user server: there is no authentication or access control. Keep it bound to `127.0.0.1` (the default, also in Docker Compose). Exposing it to a network gives anyone who can reach it access to your analysed sources and AI configuration.

Reports about ways to escape these boundaries (for example code execution from an analysed repository, or reading files outside allowed roots) are especially welcome.
