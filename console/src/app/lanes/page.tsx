import { LanesView } from "./lanes-view";

/**
 * ``/lanes`` — the operator's workflow hub.
 *
 * Two tabs, selected via ``?tab=``:
 *
 * - **active** — weekly calendar showing who runs when, plus an
 *   event-driven strip for PR/push lanes.
 * - **library** — catalog grid of built-in recipes; saving opens a
 *   PR against ``.ship/config.yml``.
 *
 * Inbox-redesign sprint (RFC-0010 / P1-01): the actual page body
 * lives in :func:`LanesView` (sibling module) so the new
 * ``/automations`` route can mount the same screen with relabeled
 * chrome. Sibling subagent D will land the redirect from
 * ``/lanes → /automations``; until then both routes resolve.
 */

export const dynamic = "force-dynamic";

type SearchParamsBag = Record<string, string | string[] | undefined>;

export default async function LanesPage({
  searchParams,
}: {
  searchParams?: Promise<SearchParamsBag>;
}) {
  return (
    <LanesView
      searchParams={searchParams}
      basePath="/lanes"
      title="Lanes"
    />
  );
}
