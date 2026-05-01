# What is the difference between a code host, a tracker, and CI?

Three separate tools, three separate jobs. A **code host** is where your actual code files live — GitHub, GitLab, or Bitbucket. A **tracker** is where you describe the work to be done — Linear, Jira, GitHub Issues, etc. A **CI** system (short for "continuous integration") runs tests and handles deployment when code changes — GitHub Actions, GitLab CI, Azure Pipelines, or CircleCI.

Sometimes one product does more than one job. GitHub, for example, does code hosting, has a built-in tracker (Issues), and includes a CI system (Actions). But when the wizard asks you to set up each of these separately, it's because Ship reads from all three to get the full picture: what was decided (tracker), what code was shipped (code host), and whether tests passed (CI). You don't need to use different companies for each — GitHub alone covers all three — but Ship needs you to tell it where to look.

Back to [Appendix index](/docs/appendix)
