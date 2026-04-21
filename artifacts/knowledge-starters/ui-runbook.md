# UI runbook

Starter UI runbook seeded by Ship on first repo activation. Lives at
`.ship/knowledge/ui-runbook.md`. Ship surfaces this to any agent that
touches the UI — customise the sections below so the agent makes the
same choices a human on your team would.

## Design system

- **Component library** — (replace with your library, e.g. shadcn/ui,
  Chakra, MUI, internal). New screens always compose existing primitives;
  ship a new primitive only when it will be used in ≥ 3 places.
- **Tokens** — colours, spacing, radii, motion timings live in a single
  tokens file. Never hardcode a hex value or a pixel value in a
  component; reference the token.
- **Typography** — the type scale is closed. Adding a new font-size
  requires a design review.
- **Icons** — one icon set for the whole app (Lucide, Heroicons, or
  internal). Do not mix sets.

## Layout & responsive

- Mobile-first. Test every new screen at 360px, 768px, 1280px.
- Max content width: (replace with your breakpoint). Full-bleed sections
  must call out the exception in the PR body.
- Sticky headers / footers: declare the z-index in the tokens file
  instead of inventing a new z-index inline.

## Accessibility

- Every interactive element has a visible focus ring (never
  `outline: none` without a replacement).
- Colour contrast AA minimum (4.5:1 for body text). CI runs `axe-core`
  against the preview URL on every PR; failures block merge.
- Keyboard: the whole app must be usable with keyboard only. Tab order
  follows visual order; modals trap focus; `Escape` dismisses overlays.
- Images: `alt` text mandatory. Decorative images use `alt=""`.
- Form fields: `<label>` associated via `htmlFor` (or wrap). Error
  messages live in an `aria-describedby` region.

## Adding a new page

1. Create the route under (your app's router convention, e.g.
   `app/<segment>/page.tsx`). Keep route boundaries shallow.
2. Compose primitives from the design system — no new styling files
   unless you are adding a new primitive (see below).
3. Wire data via the existing data layer (React Query / SWR / server
   component fetch — one convention per app, pick one).
4. Add an E2E smoke (`tests/<segment>.spec.ts`) that loads the page,
   confirms the top-level heading, and hits at least one interactive
   element.
5. Update the sitemap / nav; do not leave orphan routes.

## Adding a new component

1. Check Storybook (if you have one) — the component may already exist.
2. Place under `components/<domain>/<name>.tsx`. Co-locate the
   `<name>.test.tsx` next to it.
3. Props are fully typed; `…rest` props only when forwarding to a
   DOM element. No `any`.
4. Stories: a default, a loading / empty state, and every variant the
   component supports.
5. If the component owns state longer than render, prefer an external
   store (context / Zustand / Redux) over `useReducer` in the component
   — this keeps SSR snapshots stable.

## Loading, error, empty

Every async surface ships **all four** states:

- Loading (skeleton, not a spinner unless the load is < 500ms).
- Error (retry affordance + a Sentry report behind the scenes).
- Empty (copy explaining *why* and how to populate).
- Populated.

If you do not know the empty state copy, ask design. Do not ship with
an empty `<div />`.

## Performance budget

- Initial JS bundle (first-party) ≤ (replace with your budget, e.g.
  150kb gzipped) on the landing route.
- Largest Contentful Paint ≤ 2.5s at p75 on 4G Moto G4.
- Any PR that regresses LCP > 200ms at p75 is blocked; the Lighthouse
  step in CI enforces this.

## Feature flags

- New user-visible features launch dark (flag off) and are promoted
  via the flags dashboard, not a redeploy.
- Flag names: `ui.<area>.<feature>`. Owner and removal date declared in
  the flag metadata; flags older than the declared date are auto-removed.

## Copy & microcopy

- Sentence case for headings and buttons. No trailing punctuation on
  buttons.
- First-person plural for product voice ("We", "Let's"), never "I".
- Errors describe the *remedy*, not only the *problem*: "Retry in a
  minute" beats "Something went wrong".

## Replace this file

Delete what does not apply. Add your team's specifics (component paths,
real tokens, real component library, real breakpoints). Commit the
edited version — Ship reads the latest committed content every time.
