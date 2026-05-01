# GitLab personal access token

**What it is.** A token for the GitLab tracker integration that gives Ship read and write access to issues in your specified group.

**Where to get it.** Sign in at `gitlab.com` (or your self-hosted GitLab instance). Click your avatar → Edit profile → Access tokens. Create a token, give it a name like "Ship", set scopes to `api` and `read_repository`, and copy the value.

**Where it goes in Ship.** Onboarding wizard, "Workspace tracker" step → GitLab section. You'll fill in three fields: your GitLab host (e.g., `gitlab.com` or your self-hosted URL), your group name, and the personal access token.

**Safety.** Set a short expiration; GitLab will email you before it expires. Revoke from the same page if the token is compromised.

Back to [Appendix index](/docs/appendix)
