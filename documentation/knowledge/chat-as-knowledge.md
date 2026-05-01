# Chat as a knowledge tool

The workspace chat is not a general-purpose assistant. It is a reference desk where members ask questions about the workspace's own knowledge, routines, inboxes, and connected repositories, and where good answers become articles that other people will need.

At `/chat`, you have one active conversation thread per user. The assistant in that thread has access to your workspace's knowledge base, your repositories, your inbox, and whatever context you thread down explicitly in the conversation. When you ask a question—"what is our policy on breaking changes," "what's this routine doing," "who owns the deployment process," "what does this code block do"—the assistant answers using what the workspace actually contains, not from generic background knowledge. This is the purpose: to make your own context legible and actionable without requiring you to hunt across Slack, Google Drive, code comments, and wiki pages.

The chat is not a replacement for ownership and decision-making. It does not decide priority. It does not merge code or move tickets without an explicit step. It does not resolve debates that are still in progress. It is a tool for members to ask questions and for admins to inspect patterns across the workspace. The loudest interface in the product is the easiest to misuse—treat it as a reference desk, not as a place where decisions happen.

When you ask a question in chat, the assistant might detect that your next question shifts character entirely—from "how does billing work" to "should we hire a new engineer." These topic shifts are visible to the system. When a thread drifts this far, the chat offers to start a new conversation, keeping each thread coherent and cohesive. This matters because coherence is what makes a thread saveable. A conversation that meanders through four unrelated topics is hard to distill into knowledge; a focused thread that produces a clear answer is ready to capture.

## Saving a thread as knowledge

When a thread produces a clean, useful answer—a clarification someone else will need, a runbook that actually works, a decision that's been settled and is worth remembering—the chat offers to save it as a draft article. You choose a bucket, give it a title if the system's suggestion doesn't fit, and the thread is transformed into an article that flows through the distiller for review.

The save creates a draft, not a published article. Before it goes live, the distiller and the team who wrote it can polish prose, add context, clarify scope, cite sources, and make sure the captured answer is something you'd trust an agent to act on as the final word. Some threads will need revision; some will need to be split into multiple articles if they covered different topics.

Do not save threads that are mid-debate or mid-debug. Do not save speculation. Save the threads that produced a result—that answered the question, that clarified the rule, that ran through the procedure and it worked. The result is the article; the debate is the working-out.

Saving a thread does not delete it from chat history. You can return to a saved thread, add more questions, and save additional insights as separate articles. Over time, your chat becomes a diary of how the workspace learned and clarified its own practices, and that diary becomes your knowledge base.

Members can ask questions and save threads they own. Admins can additionally open saved threads across the workspace, inspect their contents, and close decisions when a saved thread is connected to an inbox item—marking the decision as made and moving the work forward. This keeps the chat from becoming a parallel decision-making system; the inbox remains the source of truth for what needs deciding, but the chat is where the workspace thinks out loud and preserves the thinking for later.

Chat is the loudest interface in the product, and loud interfaces are the easiest to misuse. The temptation is to use it as a thinking space where rough ideas and conversations accumulate without structure or review. Resist that. Treat it as the workspace's reference desk—a place to ask questions of the workspace itself, not a place where decisions happen or where debates settle. The workspace that saves threads thoughtfully, that distills them into knowledge, and that reviews them before they become policy, finds that its chat stays useful instead of becoming noise.
