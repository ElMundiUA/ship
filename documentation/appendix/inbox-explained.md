# Inbox: why is it called that, and why isn't it just notifications?

The **Inbox** is for decisions — items that need a human action before something can move forward. It's not a notification feed (which would just tell you "this happened") and not a todo list (which would be open-ended). Each Inbox item has exactly one of five kinds: clarification, improvement, approval, failure, or exception. Each kind has a canonical action that closes it. A clarification item says "this ticket's wording is ambiguous; you should pick a direction before work starts" — you make that choice and dismiss it. An approval item says "this code change touches a security boundary; you need to sign off" — you review and approve, or send it back.

When you "drain" the Inbox, you're making decisions, not reading updates. The Inbox has changed in size because your routines found something that needs a human call, not because something changed in the world and you ought to know about it. This distinction saves noise: routines can generate dozens of daily updates without filling your Inbox, because the Inbox is reserved for breakpoints.

Back to [Appendix index](/docs/appendix)
