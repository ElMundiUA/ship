# What's the difference between OAuth and a GitHub App install?

They look and feel similar: a button in the wizard → you're redirected to GitHub → you log in and approve permissions → you come back to Ship. The key difference is who Ship acts as. OAuth gives Ship a token tied to your individual account—so when Ship creates a comment on a GitHub issue, GitHub records that *you* created it. A GitHub App install gives Ship a standalone identity on GitHub's side—so when Ship creates a comment, GitHub records that the "Ship" app created it, not you personally. The App approach is what GitHub specifically recommends for integrations like Ship; it doesn't depend on any single person's account, it's easier for your team to manage (no one has to give up their personal GitHub credentials), and the audit trail is cleaner because Ship's actions are attributed to Ship, not to a rotating cast of humans.

Back to [Appendix index](/docs/appendix)
