# The model: process, states, routines

A process in Ship is the per-repo workflow Ship runs against — and "process" is the word we use across product copy, the console, and the rest of this manual. Inside that process, three named pieces do the actual work: states (the columns work passes through), routines (the named jobs that fire along the way), and specialists (the agent profile a routine takes on). Naming the pieces separately is what stops the conversation from collapsing into "the bot did something." Get the model right and the rest of Part V is reading; get it wrong and routines feel like magic.

## Process

A process is the per-repo workflow definition. One connected repo, one process. It declares the **states** a piece of work passes through, the **transitions** between those states, the **capacity** for each state, and the **routines** that fire along the way. 

In practice, a process looks like this: intake → analysis → in progress → review → done. Each state is a column in your workflow. Intake is where new work lands; it might have a capacity limit (five items max, to keep the filter clear). Analysis is where a technical architect or product manager reads the requirements and flags dependencies. In progress is where the team pulls work and ships code. Review is where finished work waits for sign-off. Done is the archive.

The process editor lives at `/process/<processId>` in the console. When you connect a repo, Ship creates a process for it and pre-fills it with sensible defaults—but the process is yours to shape. You can rename states, add states, remove states, change what can transition to what. The legacy term for process was `lanes`; that still appears in older documentation. Process is the current name.

## States and transitions

States are the columns. Transitions are the arrows: which state can move to which, and what happens when work moves.

Both are typed. Every state has an id (a string used in automation rules), a label (what you read in the console), and optionally a specialist that owns work in that state. If you assign the technical architect specialist to the analysis state, then when a routine runs inside that state, it runs as a technical architect—using the role context to decide what to look for, what to flag, what to ignore.

Transitions declare the allowed moves. You might say that work can move from intake to analysis, or back to intake if it needs more information, but not directly to done without passing through review. That rule lives in the transition definition. Transitions can also carry automation: "when work moves from analysis to in progress, notify the Slack channel" or "fire the kickoff routine that assigns the technical architect to a review."

## Routines

A routine is a named job. It has a prompt (the instructions for what to do), a default schedule (how often to run), and optionally a specialist (the role it assumes when it runs). The shipped catalog includes routines like `daily_architecture_tests_review`, `daily_technical_architecture_review`, `daily_security_review`, and `daily_digest`. Most run on a cron—`0 6 * * 1-5` means every weekday at 6 AM. But a routine can also be event-driven: fire when a tracker item moves to a certain state, or when a pull request opens, or run manually from the console. Routines can live inside a process—firing when work enters or leaves a state—or run **standalone** at the workspace level, reading across all repos and all processes.

The routine catalog is not fixed. You write new routines in the console or import them from Ship's library. Each routine declares the specialist it prefers (if any), the knowledge buckets it wants to read, the tracker contexts it cares about. When a routine runs, it gets those inputs—the diff, the tracker item, the relevant knowledge—and produces an output: a comment on the tracker item, an Inbox entry for a person to review, a state transition, a pull request.

## Specialists

A specialist is an agent profile: a named persona with a role description. When a routine runs, it takes on a specialist for the duration of that run. The specialist provides the role-specific context the model uses to act.

The shipped catalog includes intake, business_analyst, product_manager, designer, technical_architect, and developer. The intake specialist reads raw requests and decides if they are actionable or need clarification. The business analyst reads requirements and flags scope creep. The product manager reads the same requirements and flags priority conflicts. The technical architect reads code and design and flags dependencies and debt. The developer reads a task and breaks it into pull requests.

Specialists are not unique people. They are role definitions, like "tier 1 oncall" rather than "Maria." When you assign the developer specialist to a routine, that routine runs as "a developer"—using the developer's context and decision-making—but the actual person running the routine might be the CI/CD system, or a scheduled agent, or even a person clicking "Run" in the console. The specialist is the hat the routine wears.

## How they fit together

A typical day: a tracker item describing a new API endpoint lands in your workspace. The intake process for your backend repo automatically fires the intake routine, which reads the request and posts clarifying questions as comments. A person answers. The next morning, the `daily_technical_architecture_review` routine runs at 6 AM. It assumes the technical architect specialist, reads the tracker item (now with answers), reads the relevant knowledge buckets (your API design guidelines, your database schema), reads the code diff if one exists, and posts findings as comments or Inbox items: "this endpoint duplicates the existing GET /v2/accounts endpoint" or "the pagination strategy doesn't match the rest of the API."

The tracker item moves into the analysis state. The process records the transition. The audit log records the routine run, what it read, what it output. The Inbox holds items that need a person. The model is built of three named pieces—process, routine, specialist—so the trail stays readable. When someone asks "why did the bot comment that?", you can trace it: which routine ran, which specialist it assumed, what inputs it read, which rule in the process fired it.

The next four chapters walk you through the process editor where you configure states and transitions; the routine catalogue where you name jobs and prompts; the specialist definitions where you write role context; and how to spot the difference between a process that is earning its keep and one that is producing vanity throughput. Start with the [process editor](/docs/process/editor).

This is what you are actually buying: [clarity about who did what and why](/book#what-you-are-actually-buying).
