---
description: Analyse public and private GitHub repositories with OntoForge - commit pinning, fine-grained tokens and what is never fetched or executed.
---

# GitHub sources

OntoForge can analyse public and private GitHub repositories without cloning them or running any of their code.

Use an `https://github.com/owner/repo` URL, then specify a branch, tag or commit and optional relative subdirectory separately. Enterprise/custom GitHub hosts and SSH URLs are not supported.

For private repositories create a fine-grained token restricted to selected repositories with **Contents: read** permission. Paste it in the GitHub connection form or Settings. The value is sent over the local same-origin API and held in backend memory only; it is never persisted in browser storage, source records, logs, exports, URLs, or subprocess arguments. The password input is cleared after connection. Restarting loses session tokens. Disconnect removes the selected source's session token.

An optional server `GITHUB_TOKEN` environment fallback supports unattended local acquisition. A session disconnect does not revoke that environment variable; remove it and restart to disconnect the fallback. Token revocation on GitHub is separate. A GitHub App flow is not implemented.

Acquisition calls `GET /repos/{owner}/{repo}/commits/{ref}`, then `GET /repos/{owner}/{repo}/zipball/{sha}`. Only the approved `codeload.github.com` HTTPS redirect is followed; the Authorization header is **not forwarded** to the redirected host. Commit SHA and relative subdirectory are persisted; source evidence URLs use `/blob/{sha}/...#L...`.

The archive route avoids directory-list pagination and recursive tree truncation entirely. The application does not present a branch-list UI requiring pagination. References are entered directly. 401/403/404/429 errors explain credentials, references, permissions, or rate limits and show remaining/reset headers where present. Retry is user-controlled; the tool does not wait automatically for quota resets. Transfer failure leaves a failed persisted job.

No Git client, hooks, submodule fetch, repository scripts or LFS downloads are invoked. LFS pointer files are excluded and reported. `.gitmodules` is inventoried; acquiring submodules is unsupported and requires separate sources.

Verification: mocked commit/redirect, credential separation and failure cases are covered by tests. `scripts/verify_public_github.py` runs a real acquisition of a public repository. Private-repository access has not been verified end to end yet. Source acquisition permission never implies permission to send excerpts to AI.

Primary contract: https://docs.github.com/en/rest/repos/contents#download-a-repository-archive-zip
