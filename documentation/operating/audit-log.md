# The audit log

An audit log is the system telling the future what the present did. Without one, every privileged action becomes a story; with one, every story has a row to point at. Ship's audit log lives at `/audit` and surfaces privileged mutations across the workspace. A future reviewer should understand what ran, why, and where the proof lives.

## What it records

Every privileged action lands in the audit log. A member added or removed. A role changed. An integration installed. A secret rotated. A policy edited. An Inbox item resolved. A routine triggered manually. Each row carries five pieces of information. The **actor** is who did it—a user by email, a token by name, or a system process by identifier. The **action** is the type of change: `member_added`, `role_changed`, `integration_installed`, and so on. The **target** is what was acted on, stored with a **target_kind** to say what class of thing it is—`inbox_item`, `member`, `integration`, `routine`, `policy`. The **timestamp** is when it happened, always in UTC. The **payload** is a JSON structure holding the details—old values and new values, IDs, configuration changes, whatever context the action needs to be understood later.

The payload is usually compact and human-readable. Expand any row inline to see it. Most payloads are a dozen fields; none are enormous.

## Filtering and reading

The audit log view supports four filters. Filter by **action** to see only member changes, or only integration events. Filter by **actor** using an email address or token name to see what one person or one system did. Filter by **target_kind** to see all events touching integrations, or all events touching Inbox items. Filter by **date range** to narrow to a week, a day, or a specific hour. Combine any of these.

The list is cursor-paginated, not numbered pages—the index grows backward in time. Sort is always reverse chronological: most recent first. Click through pages as needed. For a typical workspace, the log is manageable; for a workspace with many members or frequent routines, the log can grow large, and filters make it navigable.

## Who can see it

The audit log is admin-visible only. Owners and admins see everything; members do not see the log at all, and cannot. This is intentional. Audit data is sensitive—it can leak target IDs, configuration structure, and the timing of changes—and most members have no daily reason to look. If a member needs to know whether an action happened, ask them to contact an admin who can check the log and report back.

## When you open it (and when you don't)

You do not open the audit log every morning. You open it when an instinct says something is off. An Inbox item that should have routed somewhere else routed nowhere. A member claims they didn't change a setting, but you know it changed. A routine ran when the schedule says it shouldn't have. The Inbox and the workspace home are the first two places to look; if both say everything is fine and your gut says otherwise, the audit log is where the truth settles.

Most usefully, the audit log is a tool you almost never open. The point is not to prove nothing happened. The point is to know what happened when you need to. Ship's architecture is built to route events clearly and log them crisply so that when you need proof, the proof is there. Until then, you work on the happy path. See [the morning loop](/docs/operating/morning-loop) for the daily routine that keeps the happy path clean.
