# Agent vs routine vs specialist

An **agent** is the model — the AI that actually generates text or code. Cursor, Claude Code, GitHub Copilot, OpenAI's model, Anthropic's model. The agent is what engineers run on their machines or what cloud services run on theirs. Ship is agent-agnostic — it works with whichever agent your team uses, or even multiple agents for different tasks.

A **routine** is a specific, named job Ship runs on a schedule or trigger. "Daily security review" is a routine. "Daily digest" is a routine. "Clarify ambiguous acceptance criteria when a ticket sits in Review" is a routine. Each routine has a prompt template, a schedule (or trigger condition), and a target — like a Slack channel or your Inbox.

A **specialist** is the *role* a routine plays when it runs. The same agent (say, Claude) can take on the developer specialist for one routine and the technical architect specialist for another. Specialists are role definitions — they tell the agent what persona, context, and constraints apply. You might have a "security specialist" routine that reviews code and a "writer specialist" routine that drafts release notes, both running in the same workspace with the same agent.

In one sentence: **routines** run *as* **specialists** *using* **agents**.

Back to [Appendix index](/docs/appendix)
