# What is a secret, and why does it never go in a prompt?

A secret is anything that gives a system access to a tool, an account, or a tenant. API keys, passwords, authentication tokens, personal access tokens—these are all secrets. The defining feature is that whoever has the value can act as you. If someone else gets your GitHub PAT, they can push code to your repositories, merge PRs, and delete branches in your name. If someone gets your OpenAI API key, they can use your API quota and run up a bill.

The rule is simple: never paste a secret into a chat with an AI assistant (including ChatGPT or Claude), never put it in a config file you commit to Git, never share it in Slack, never type it into a browser address bar. Once a secret is in those places, you don't know who else has seen it—it might be in chat logs, cached by a third party, or visible in plaintext to anyone with repository access. Ship has a secret store specifically so the value is held in one place, encrypted at rest, with audit logs of who accessed it and when. The wizard asks you to paste the secret into a form field; the form sends it directly to Ship's secret store, it's encrypted immediately, and the value is never logged or displayed again. That's the safe path.

Back to [Appendix index](/docs/appendix)
