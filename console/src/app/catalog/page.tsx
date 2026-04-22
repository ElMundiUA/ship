import { redirect } from "next/navigation";

// The workspace artifact catalog retired in favour of the Lanes hub
// (``/lanes``). Kept as a redirect so any bookmarks, external links,
// or in-product CTAs that still point at ``/catalog`` land on the
// Library tab rather than a 404. The catch-all
// ``[[...rest]]/page.tsx`` picks up ``/catalog/<id>`` and
// ``/catalog/pull-requests`` for the same reason.
export default function CatalogRedirect() {
  redirect("/lanes?tab=library");
}
