import { LanesView } from "../lanes/lanes-view";

/**
 * ``/automations`` — new IA mount point for the recurring /
 * trigger-driven workflow surface (RFC-0010, P1-01). Same body as
 * the legacy ``/lanes`` page, just relabeled. The legacy route is
 * still wired up while sibling subagent D lands the redirect from
 * ``/lanes → /automations``.
 */

export const dynamic = "force-dynamic";

type SearchParamsBag = Record<string, string | string[] | undefined>;

export default async function AutomationsPage({
  searchParams,
}: {
  searchParams?: Promise<SearchParamsBag>;
}) {
  return (
    <LanesView
      searchParams={searchParams}
      basePath="/automations"
      kicker="ORCHESTRATION"
      title="Automations"
    />
  );
}
