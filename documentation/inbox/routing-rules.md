# Routing rules

A routing rule maps a symbolic handle—like `secops` or `repo_maintainer`—to a concrete owner: a person, a group, or a built-in assignment strategy. Routines in your shipped profiles don't name people directly. They name handles. When an item needs to route to `secops`, the system looks up the current binding for that handle and sends the work there. If Alex leaves the security team, one rule change reroutes all future items; routines stay untouched. The indirection is the fence. It separates the pattern—"security issues go to the secops handle"—from the people—who that handle currently points to. Handles are workspace-scoped; exactly one rule binds each handle. The database enforces it; the admin UI surfaces a clear error if you try to add a duplicate.

## How a rule is shaped

A rule has three fields. The **handle key** is the symbolic name—`secops`, `on_call`, `release_manager`, anything you choose. The **target type** is one of `user`, `group`, or `strategy`. The **target value** is the concrete destination: a user ID if the type is `user`; a group key if the type is `group`; a strategy name like `round_robin` if the type is `strategy`. You manage rules at Settings → Inbox routing. The admin panel lists all bindings in your workspace, shows which handles are used by shipped profiles, and flags configuration problems.

## Targets: user, group, strategy

A **user** target sends the item to one named person. It's simple: straightforward ownership. But it breaks when that person is on vacation, sick leave, or rotates to a different team. The item still lands on their desk in the Inbox; they can snooze or delegate, but the routing doesn't auto-correct. Use user targets when the person is stable and the handle is truly personal—a single author responsible for a review gate, for example.

A **group** target sends to a member group. The group's dispatch policy then decides who actually gets the item. One policy might fan out to all members; another might route to the group's designated lead. Group targets are right when you want to say "any of our security on-call team" without naming individuals. If people rotate through the group—Alice is on-call this week, Bob next week—the group membership changes and routing follows automatically.

A **strategy** target invokes a built-in resolver. `round_robin` is the common example: it cycles through a member group, keeping rotation state in the workspace. Each time an item is routed, the strategy picks the next person in the cycle. Strategies are the right answer when the group is large and the workload should spread evenly across people. They are over-engineering for a two-person team.

## Configuration health

Before you save a rule, and weekly thereafter, check the configuration health panel in the Inbox routing UI. It classifies your handles into four categories. **Bound** handles have a rule—the binding exists and is active. **Used** handles appear in at least one shipped profile routine. **Orphaned** handles are bound but never used; their rule will never fire and should be cleaned up. **Unbound** handles are used by routines but have no rule; intake falls back to the built-in fallback chain, typically `workspace_admin → workspace_owner`, which may or may not be what you intended.

The health panel is diagnostic, not policing. It flags patterns that often hide bugs. An orphaned handle is usually clutter—someone added a rule months ago, the routine moved to a different handle, and the old rule was forgotten. Deleting it is free. An unbound handle is a quiet bug. A routine references a handle that has no binding. Items routed to that handle silently fall back. If you didn't intend the fallback, configuration has drifted from intent. Read the health panel weekly, especially after a bulk profile update or when your team structure changes.

## Previewing a rule before saving

Before a rule lands in your workspace, the routing UI offers a preview. "If I save this, what would happen to a sample item?" The preview is side-effect-free. Even resolvers like `round_robin` that normally update rotation state run inside a database savepoint and are rolled back. You see what would happen without actually changing anything. Use it. Testing a rule before it lives saves the surprise of finding out a resolver picked the wrong person after the rule is already live and items are landing in the wrong place.

---

Routing is the part of the Inbox that pays back over months. A workspace with five handles, all bound, all used, no orphans, no unbound—that's an Inbox that won't surprise you. You can see the configuration at a glance and trust it. A workspace with sixteen orphaned rules and four unbound handles will, eventually, route an item somewhere unexpected and leave someone hunting through Settings to understand why.
