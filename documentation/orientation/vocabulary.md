# The vocabulary you'll meet on every screen

This manual leans on seven words. They appear in every corner of the console, in settings, in logs, in team conversations about processes. If they're fuzzy—if Workspace means one thing Monday and another by Friday—your team will stop trusting Ship's decisions. The point of naming things carefully is not philosophy; it's practical. Labels that drift are how organizations lose grip on their own processes. This chapter defines each word plainly, explains why it earned its place, and shows you where to find it on screen.

## Workspace

A workspace is the team or product area Ship operates inside. It holds your members, your connected repositories, your integrations with trackers and CI systems, your process policies, your shared knowledge, and the dashboard you see when you log in. Think of it as the boundary around a coherent unit of work—a product team, a platform group, a service line. The word "workspace" matters because it's not a person and not a repository; it's the container that holds both. You'll see Workspace across the top of the console, in dropdown menus when you switch teams.

## Connected repo

A connected repo is a GitHub repository you've activated in your workspace. The connection is a boundary: it names where Ship is allowed to watch for changes and where process rules are allowed to act. You might have ten repositories in your GitHub organization, but only three of them are connected to Ship; only those three will receive PR comments, only those three will trigger routines. The phrase "connected repo" matters because the alternative—just saying "repository"—would blur the line between code you have and code Ship can touch. You'll see connected repos in the Repos panel, and again during onboarding when you choose which repositories to activate.

## Tracker

A tracker is your system of record for product intent. It might be Linear, Jira, GitHub Issues, GitLab, or Azure DevOps; Ship adapts to each one. The key is that it's *your* single tracker—one per workspace—where the team decides what matters and in what order. The reason we use the word "tracker" instead of "issue system" or "backlog" is that Ship needs to speak about the *function* (the source of truth for priorities), not the vendor. A process can never invent priority; it can only read what the tracker says. You'll set up your tracker connection in Settings under Integrations, and during onboarding you'll point Ship to the URL and credentials.

## Inbox

The inbox is your attention surface in Ship. It collects five types of items—clarifications (Ship needs a decision before acting), improvements (Ship spotted something worth doing), approvals (a human needs to sign off), failures (something ran and broke), and exceptions (something should have happened but didn't). It's not a notification feed or a todo list; it's decision work. The word "inbox" matters because it respects the fact that humans move things *in and out*—you read something, you act on it, it disappears. You'll find the Inbox as a main navigation item in the console, updated in real time as Ship's routines run.

## Knowledge

Knowledge is product and repo context that agents can use without guessing. It includes code style guidelines, runbooks for common failures, brand rules, integration notes, deploy procedures, and any other institutional memory Ship should lean on. Knowledge is stored in buckets—one bucket for Python style, another for API design, another for incident response—and each bucket is promoted through review before Ship starts using it. We say "knowledge" instead of "documentation" because it's specifically the context that processes need to make good decisions and stay synchronized with your team's intent. You'll manage knowledge in the Knowledge section of the console, and you'll see it cited in PR comments and decisions.

## Process

A process is the per-repo workflow Ship runs against: the states a piece of work passes through (intake → analysis → in progress → review → done, in some shape), the transitions between those states, the capacity for each state, and the **routines** that fire along the way. One connected repo, one process. The word "process" is what you'll see in the console (`/process`) and what the team talks about in standups; the legacy term `lanes` still appears in some configuration. Inside a process, two more named pieces do the actual work. A **routine** is a named job — a security review, an architecture sweep, a daily digest — with a prompt and a default schedule; some routines live inside a process, some run standalone at the workspace level. A **specialist** is the agent profile a routine takes on for the duration of a run: intake, business analyst, product manager, developer, technical architect, designer, and so on. The team talks about "the daily security review"; the engine sees "the `daily_security_review` routine, run by the developer specialist, inside the process attached to the backend repo." Both descriptions are true.

## Evidence

Evidence is the trail—the proof that something happened and what it means. It includes tracker links (the issue this PR addresses), pull requests (code that ran), CI runs (automated tests that passed or failed), comments (human judgment recorded), knowledge articles (the rules that applied), and audit events (who did what and when). The reason we use "evidence" instead of "history" or "logs" is that it acknowledges the point: if a claim has no evidence, it's opinion. Processes live by evidence. You'll see evidence everywhere in the console—in PR comments that link back to the tracker, in the audit log that shows every action Ship took, in the knowledge articles Ship cites when making a decision.

---

These seven words live in product copy throughout the console. Underneath, engineers who touch the codebase or the repository configuration will see technical names: `lanes` (the legacy term for what we now call a process), `pipeline_runs` (the historical record of a routine's execution), `buckets` (the storage shape behind a knowledge article), `.ship/config.yml` (the repo-level config), and the RFCs that define the protocol. Both languages exist and both are correct. This manual leads with the product words so the team can talk about processes in the same terms the console uses, and points to technical reference only when you need to configure a repo or wire a new system. The Inbox is where humans decide; the process editor is where processes are configured; the audit log is where evidence settles after the fact. Keeping the layers named separately is what stops "the bot did something" from becoming an organisational shrug.

Next, we'll walk through a day in Ship and see how these seven words come to life in real work.
