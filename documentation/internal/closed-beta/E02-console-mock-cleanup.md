# E02 — Console mock fallback removal

**Priority:** P0
**Effort:** M (~3–5 days)
**Owner:** TBD

## Goal

When the backend is reachable but returns nothing, the console shows real empty states. When the backend is unreachable, it shows a real error with retry. **No `MockBanner` ever appears in production.**

## Why

Today, several pages silently fall back to `mockWorkspaces[0]` and the canned `lib/mock/cloud.ts` data. A first-time user who hits a flaky deploy sees an attractive but fake workspace and never realizes anything is wrong. This destroys trust in a way that ordinary errors do not.

The fix is mechanical, but it cuts across several pages and needs coordinated empty/error UX.

## Affected files (audit before editing)

| File | Mock usage |
|---|---|
| `console/src/app/page.tsx` | `mockWorkspaces[0]`, `mockOpsDashboard`, `<MockBanner />` |
| `console/src/app/inbox/page.tsx` | `mockWorkspaces[0]`, `<MockBanner />` |
| `console/src/app/inbox/[id]/page.tsx` | locally defined `mockMembers` |
| `console/src/app/audit/page.tsx` | `mockWorkspaces[0]`, `<MockBanner />` |
| `console/src/app/settings/page.tsx` | `mockWorkspaces[0]` |
| `console/src/app/process/page.tsx` | `mockProcess`, `mockProcessList` |
| `console/src/app/r/[owner]/[repo]/page.tsx` | imports `MockBanner` (verify usage) |
| `console/src/components/ui.tsx` | exports `MockBanner` |

## Tasks

### T01 — Define the standard "API unavailable" component **[S]**

- New file: `console/src/components/api-unavailable.tsx`.
- Props: `{ scope: string; retryHref?: string; details?: string }`.
- Renders: friendly message, retry button, "What this means" disclosure with technical detail (`scope` = which endpoint, `details` = error message).
- Replace bare `MockBanner` everywhere with this.

**Acceptance:** component covered by snapshot test; designed to match the dark `ink` theme.

### T02 — Workspace home (`page.tsx`) — drop mock dashboard **[M]**

- Remove `mockOpsDashboard`, `mockWorkspaces` imports.
- Server logic: if no session → redirect login; if no workspace → redirect onboarding; if API errors → render `<ApiUnavailable scope="workspace" />`.
- Empty workspace: render "Welcome" panel with the 5-step setup CTA from `getting-started`.

**Acceptance:** every code path returns either a real dashboard, a real empty state, or a real error — never mock.

### T03 — Inbox (`inbox/page.tsx`) **[M]**

- Same treatment.
- Empty Inbox: render the "**Healthy quiet**" message with a tooltip explaining "zero items can mean working as intended". Reference the blog post.
- Error: `<ApiUnavailable scope="inbox" />`.

**Acceptance:** removing `mockWorkspaces` reference compiles without TS errors; inbox empty state matches blog post tone.

### T04 — Inbox detail (`inbox/[id]/page.tsx`) **[S]**

- Remove the locally defined `mockMembers`. Pull members via `getWorkspaceMembers(workspaceId)`.
- Item not found → `notFound()` (Next.js).

**Acceptance:** opening a real inbox item shows real assignees; opening a fake id 404s.

### T05 — Audit (`audit/page.tsx`) **[S]**

- Same treatment.
- Audit log empty state: "No audit events yet. Audit lights up after the first action in this workspace."

### T06 — Settings (`settings/page.tsx`) **[S]**

- Same treatment.
- Settings page should not hard-pick `mockWorkspaces[0]` ever; use the resolved workspace from cookie/URL.

### T07 — Process (`process/page.tsx`) **[M]**

- Bigger because of the graph view.
- Remove `mockProcess` / `mockProcessList`.
- Empty: "No processes yet. Create one or seed from preset."
- Loading skeleton for the graph (the canvas takes time on first paint).

**Acceptance:** brand-new workspace with no processes does not crash.

### T08 — Move all `lib/mock/cloud.ts` references behind a flag **[S]**

- Wrap the entire file with `if (process.env.NEXT_PUBLIC_USE_MOCK !== "1") throw new Error("mock data should not be imported in production")`.
- Or move it to `__fixtures__/` and import only in Storybook / tests.
- Keep the data for component-library development; just block production import.

**Acceptance:** `npm run build` with `NEXT_PUBLIC_USE_MOCK` unset succeeds; setting it to `1` re-enables for local UX work.

### T09 — Remove `<MockBanner>` from production code paths **[S]**

- After T02–T07 done, `rg "<MockBanner" console/src/app/` should return nothing under `app/` (the component itself can stay in `components/ui.tsx` for fixture tests).

**Acceptance:** clean grep result.

### T10 — Add E2E covering "API down" UX **[S]**

- New test: `e2e/tests/api-unavailable.public.spec.ts` (or as part of console-flows).
- Stub network → expect `<ApiUnavailable>` render, retry button works on recovery.

**Acceptance:** test passes locally and in CI.

## Definition of done

- [ ] Zero `MockBanner` in any rendered production page.
- [ ] `lib/mock/cloud.ts` not importable in production builds.
- [ ] Every page has an explicit empty state and error state.
- [ ] E2E "API down" test green.

## Risks / unknowns

- Some empty states will reveal that the backend is missing default data seeding (e.g. no default policies). Track those as separate tasks rather than re-hide with mocks.
- Loading skeletons may need refactoring `dynamic = "force-dynamic"` patterns; budget time for this.

## Out of scope

- Redesigning the dashboard layout (covered in E12).
- Writing a Storybook (only blocked-imports here).
- Adding optimistic UI for actions.
