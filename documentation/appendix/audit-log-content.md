# What does "the audit log" actually contain?

The **audit log** records privileged actions — anything that changes the workspace's shared state. Members added or removed, roles changed, integrations installed, secrets rotated, policies edited, Inbox items resolved, routines triggered. Each row has *who* did *what* on *what target* at *what time*, plus the structured details. The audit log is admin-only and you probably don't open it daily; it's there for the morning you need to answer "wait, who changed that policy?" or "when did we integrate with that tool?".

Back to [Appendix index](/docs/appendix)
