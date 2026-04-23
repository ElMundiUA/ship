import { redirect } from "next/navigation";

// Catch-all for deep links under the retired ``/catalog`` prefix
// (``/catalog/<artifact-id>``, ``/catalog/pull-requests``, etc.).
// Everything folds back to the Plays grid (RFC-0010 P1-02) — the
// only place that still surfaces play / lane recipes. We
// intentionally do NOT try to preserve the trailing path in the
// redirect target: the Plays grid has no per-id detail view today,
// and ``/automations/<id>`` is an active-lane projection, not a
// recipe.
export default function CatalogDeepLinkRedirect() {
  redirect("/plays");
}
