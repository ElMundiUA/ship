import { redirect } from "next/navigation";

// Catch-all for deep links under the retired ``/catalog`` prefix
// (``/catalog/<artifact-id>``, ``/catalog/pull-requests``, etc.).
// Everything folds back to the Lanes Library tab — the only place
// that still surfaces lane recipes. We intentionally do NOT try to
// preserve the trailing path in the redirect target: the Library tab
// has no per-id detail view today, and /lanes/<laneRowId> is an
// active-lane projection, not a recipe.
export default function CatalogDeepLinkRedirect() {
  redirect("/lanes?tab=library");
}
