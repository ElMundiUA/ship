# Workspace vs project vs organization: what's what?

In Ship, a **workspace** is the top-level container — a team or product area. One workspace, one tracker, one set of policies, one set of connected repos. Everything Ship tracks belongs to exactly one workspace.

In a tracker like Linear or Jira, a **project** is a smaller container *inside* the workspace — usually one product, one initiative, one rough scope. A Ship workspace can read tickets across many tracker projects. This is where the confusion starts: "the project" in a Linear dashboard means something different than "the workspace" in Ship, even though they're in the same integrated system. When the wizard says "workspace", it always means the Ship workspace. When you read about "the project" in tracker context, it's the tracker's own project concept — they are not the same word.

In GitHub, an **organization** is the company-level account that owns repos. A Ship workspace usually maps to one GitHub organization, but it doesn't have to. You could run Ship across multiple organizations if your team splits repos that way. The key point: workspace is Ship's term, project is your tracker's term, organization is GitHub's term. They don't map one-to-one.

Back to [Appendix index](/docs/appendix)
