# Jira API token

**What it is.** An Atlassian API token that authenticates Ship's read and write access to Jira issues. Atlassian uses email + API token for API access instead of email + password.

**Where to get it.** Sign in at `id.atlassian.com`. Go to "Security" → "Create and manage API tokens" (or navigate directly to `id.atlassian.com/manage-profile/security/api-tokens`). Click "Create API token", name it "Ship", and copy the value.

**Where it goes in Ship.** Onboarding wizard, "Workspace tracker" step → Jira section. You'll fill in four fields: your Jira site URL (e.g., `yourorg.atlassian.net`), your email address, the API token, and your default Jira project key.

**Safety.** Atlassian API tokens do not expire by default but can be revoked from the same security page. The token is tied to your personal Atlassian account; if you leave the company, revoke it.

Back to [Appendix index](/docs/appendix)
