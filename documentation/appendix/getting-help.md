# Appendix E — When you're stuck

Every Ship workspace eventually hits a moment when something doesn't behave the way you expected. The cheap habit is a small triage flow that resolves most of those moments without a support ticket. When the flow doesn't resolve it, you ask — but with the right artefacts, so the answer comes in one round-trip instead of three.

## Things you can usually fix yourself

The check-it-yourself flow, in this order:

1. **Is the workspace home telling you something?** A "Ship template update needed" banner, a degraded integration warning, a routine in repeat-failure state — these surface on the home page. If the answer is on the workspace home, you don't need support. See [the morning loop](/docs/operating/morning-loop).

2. **Is there an Inbox item you missed?** Most repeat issues have an Inbox item explaining what's stuck and what kind of decision will unstick it. See [disposition](/docs/inbox/disposition).

3. **Has the bundle drifted?** If the workspace home shows the "Ship template update needed" banner, applying the update fixes a lot of mystery symptoms. See [bundle updates](/docs/developer/bundle-updates).

4. **Did `shipctl doctor` say something?** Run it. The output is honest and points at the real issue most of the time. See [shipctl](/docs/developer/shipctl).

5. **Is your tracker token still valid?** Atlassian and Azure DevOps tokens expire silently; a binding that was working last week may have hit its TTL.

6. **Is there a known symptom in [troubleshooting](/docs/reference/troubleshooting)?** Most common failure modes are listed there with a cause and a fix.

If steps 1–6 don't resolve it, you've done the diligence — go to support.

## When to ask for help

Three triggers:

- The symptom doesn't match anything in troubleshooting and you've tried the obvious fixes.
- An action affected more than one repo and you can't identify the cause.
- A security or compliance concern (a leaked credential, an unexpected privileged action in the audit log).

Don't sit on a security concern; "this seems suspicious" is a valid reason to write immediately, even if you're not sure it's a real issue.

## What to send when you ask

The three things that turn a one-week support thread into a one-day one:

1. **Workspace ID and the affected repo's full name** (e.g., `myorg/backend-api`).
2. **A screenshot of the symptom** — the Inbox item, the banner, the error toast — with the URL bar visible.
3. **What you already tried** — "I ran `shipctl doctor`, applied the bundle update, and the routine is still failing".

Optionally: if the issue involves a routine, the routine id and the timestamp range. If the issue involves a credential, do not paste the credential value; just say which integration is affected.

For non-urgent issues: open a ticket in the support inbox the workspace owner has set up (varies per organisation). For urgent or security concerns: use the dedicated escalation channel — the workspace owner knows where it is. Don't post credentials, full payloads, or customer data into a public channel.

---

Most "I'm stuck" moments resolve in two or three minutes if you check the workspace home, the Inbox, and the troubleshooting page first. Support gets the genuinely-mysterious cases, which is where they can actually help. Treat your future self the same way — leave a note on the resolved issue (in the Inbox item, on the audit row, in a knowledge article) so the next person sees the pattern.
