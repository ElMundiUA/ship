import { redirect } from "next/navigation";

// The workspace artifact catalog retired in favour of the Plays
// hub (RFC-0010 P1-02 — formerly ``/lanes?tab=library``). Kept as
// a redirect so any bookmarks, external links, or in-product CTAs
// that still point at ``/catalog`` land on the Plays grid rather
// than a 404. The catch-all ``[[...rest]]/page.tsx`` picks up
// ``/catalog/<id>`` and ``/catalog/pull-requests`` for the same
// reason.
export default function CatalogRedirect() {
  redirect("/plays");
}
