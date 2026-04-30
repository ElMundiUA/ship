# E12 — Landing finalize + console UX polish + mobile + demo

**Priority:** P3
**Effort:** M (~5–7 days)
**Owner:** TBD

## Goal

The landing prominently links to the working app. The console works on mobile for at least Inbox triage and dashboard read. Empty states across the console are inviting, not blank. A new ~30-second demo video plays in the landing hero. First-impression for a beta invitee is "this is a real product" within 10 seconds.

## Why

Three reasons:

1. **Currently the landing has no link to `app.ship.elmundi.com`** because the app didn't fully work. After P0 epics close, that gate lifts and we expose the door.
2. **Mobile is broken.** The maintainer chose to fix it (not declare desktop-only). PO-style users will check Inbox on a phone in coffee queues; they need to.
3. **The demo recording is poor.** A new ~30-second cut can lift conversion 5x relative to no video.

## Tasks

### T01 — Landing: app link in header **[S]**

- File: `landing/src/components/site-header.tsx`.
- Add "Sign in" button (or "Open console" if user has a session — not detectable cross-domain easily, just always show "Sign in").
- Behavior: simple `<Link href="https://app.ship.elmundi.com/login">Sign in</Link>`.
- Add small "Closed beta" badge next to it.

**Acceptance:** every page in landing has a visible Sign-in CTA in the header.

### T02 — Landing: "Request access" CTA **[S]**

- File: `landing/src/components/hero-section.tsx` and `landing/src/app/getting-started/page.tsx`.
- Primary CTA on hero: "Request closed-beta access" → scrolls to / navigates to the waitlist form built in E08.
- Secondary CTA: "See how it works" → scrolls to the operator loop section.

**Acceptance:** A/B obvious primary action, no dead-end on the hero.

### T03 — Landing: hero demo video **[M]**

- Record new 25–35 second demo using the deployed dev console (E03's recording is the rough cut for internal use only).
- Script:
  1. Land on workspace home (3s).
  2. Scroll to Inbox (3s).
  3. Open a clarification, type answer, resolve (8s).
  4. Show evidence on tracker (5s).
  5. Final shot: dashboard with green run (4s).
- Bunny Stream-hosted MP4 + WebM. Autoplay muted, looping.
- Replace the static hero illustration on `hero-section.tsx`.

**Acceptance:** video plays on Chrome / Safari / Firefox / iOS Safari; <5s to first frame on a 4G connection.

### T04 — Landing: dead links sweep **[S]**

- After E11 (docs alignment) closes, run `rg "/cli\b|/tools\b|/collections\b" landing/src/`.
- Remove any remaining references.
- Update `sitemap.ts` if any of those paths are listed.

**Acceptance:** zero references; sitemap clean.

### T05 — Console: empty states everywhere **[M]**

- For each surface, define the empty state and the next-step CTA:
  - **Workspace home (no repos)** — "Connect a repo" → onboarding step 1.
  - **Inbox (no items)** — "Healthy quiet" message with the blog-post tooltip.
  - **Knowledge (no buckets)** — "Create your first bucket" → wizard.
  - **Repos list (no activated repos)** — "Activate a repo from your installation".
  - **Audit (no events)** — "Audit lights up after the first action".
  - **Members (only you)** — "Invite a teammate" CTA.
  - **Policies (none defined)** — "Apply the default policy bundle" + 4 starter policies link.
- Files: `console/src/app/{page,inbox,knowledge,audit,members,settings/policy,repos}/page.tsx`.

**Acceptance:** every page has an explicit, on-brand empty state — no blank tables.

### T06 — Console: loading skeletons **[S]**

- Server components currently render with `dynamic = "force-dynamic"` and load the entire page before painting. UX feels broken on slow links.
- Add a `loading.tsx` adjacent to each route (Next.js convention). Renders a skeleton with the same layout shape.

**Acceptance:** every primary route has a `loading.tsx`.

### T07 — Console: mobile layout pass **[L]**

- Audit pages on iPhone-sized viewport (390x844).
- App shell: collapse left nav into a top bar with hamburger.
- Inbox list: stack item rows; show shape + title + age, hide secondary fields.
- Inbox detail: single column, large tap targets.
- Dashboard: vertical stack of cards; sparklines hidden, totals shown.
- Knowledge / Repos / Audit / Settings / Onboarding: usable, not pretty.
- Files: `console/src/components/app-shell.tsx`, `tailwind.config.ts` breakpoints, page-specific styling.

**Acceptance:** Inbox and dashboard usable on iPhone Safari; e2e Playwright run with `viewport: { width: 390, height: 844 }` passes for those flows.

### T08 — Console: error toasts **[S]**

- After E02 (mock cleanup), API errors surface as `<ApiUnavailable>` cards on full-page loads. Inline mutations (creating an article, accepting an invite) need toast notifications.
- File: `console/src/components/ui.tsx` — add `Toast` + `ToastProvider`.
- All server actions wrap their throw paths in a redirect-with-toast pattern, or use Next's error boundaries.

**Acceptance:** failed mutation produces a visible toast within 1s; toast dismisses cleanly.

### T09 — Console: keyboard shortcuts **[S]**

- For Inbox-heavy users:
  - `j` / `k` next / prev item.
  - `e` resolve, `s` snooze, `r` reassign, `?` help.
- File: a small `useKeyboardShortcuts` hook on the inbox list page.

**Acceptance:** keyboard shortcut help modal exists; the 5 keys above work on the inbox list.

### T10 — Console: dark theme audit **[S]**

- The login page has a heavy gradient backdrop; the inside surfaces may be too saturated for an Inbox-by-the-hour reader.
- Review the inside pages: lower the saturation in non-hero contexts. Keep coral/lilac/aqua as accents only.

**Acceptance:** color audit document; one PR with the saturation pass.

### T11 — Trust badges + status link **[S]**

- File: `landing/src/components/site-footer.tsx`.
- Add: "Status" → `https://status.ship.elmundi.com` (E10 dependency).
- Add: small note "Auth0 SSO · Bunny EU · pgvector".
- Maybe a logo strip when first 3 dogfooders have logos to display (after E05).

**Acceptance:** footer has the status link; no fake logos.

### T12 — Open Graph / preview cards **[S]**

- File: `landing/src/app/layout.tsx` and per-page `metadata`.
- Verify OG image generation; add Twitter card metadata.
- Test on Slack / Telegram / iMessage previews — they should render the hero image and the headline.

**Acceptance:** sharing `https://ship.elmundi.com` on Slack shows a clean card.

## Definition of done

- [ ] Sign-in button visible on every landing page.
- [ ] Mobile Inbox + dashboard usable on iPhone Safari (e2e green).
- [ ] Every console page has a real empty state and a `loading.tsx`.
- [ ] New demo video on the landing hero, autoplaying.
- [ ] Toasts on failed mutations.
- [ ] Status link in footer.
- [ ] OG preview cards render correctly.

## Risks / unknowns

- Mobile rewrite may take longer than estimated; if so, scope down to "Inbox + dashboard mobile-usable" and defer everything else to post-beta.
- Recording the demo against the deployed app means E01–E07 must already be solid — schedule this task last in the epic.
- Bunny Stream pricing on the demo video; consider self-hosting a small MP4 instead.

## Out of scope

- Native mobile app.
- Progressive Web App install prompt.
- Marketing landing per language.
- Pricing page (no Stripe, no plans).
- Customer logo wall / case-studies wall (until there are real customers).
