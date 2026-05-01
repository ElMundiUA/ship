# GitHub personal access token

**What it is.** A token for GitHub API access. Most teams don't need this because the Ship GitHub App handles repository permissions automatically. A personal access token (PAT) is only needed for legacy workflows or integrations that don't support GitHub Apps.

**Where to get it.** Sign in at `github.com`. Go to Settings → Developer settings → Personal access tokens → "Fine-grained tokens" (or "Tokens (classic)" for the older format). Create a token with the scopes the integration asks for, and copy the value — it starts with `ghp_`.

**Where it goes in Ship.** Settings → Integrations → GitHub (only if the wizard explicitly asks; most teams won't need it).

**Safety.** Set a short expiration (30 or 90 days) when you create it. The token is tied to your GitHub account; rotate it when you leave.

Back to [Appendix index](/docs/appendix)
