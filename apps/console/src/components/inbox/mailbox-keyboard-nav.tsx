"use client";

/**
 * Optional keyboard triage for the mailbox list: j/k move selection,
 * Enter opens, 1/2 trigger primary/secondary on the focused checklist row.
 */

import { useRouter } from "next/navigation";
import { useEffect } from "react";

export function MailboxKeyboardNav({
  itemIds,
  selectedId,
  buildHref,
}: {
  itemIds: string[];
  selectedId: string | null;
  buildHref: (id: string) => string;
}) {
  const router = useRouter();

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.tagName === "SELECT" ||
          target.isContentEditable)
      ) {
        return;
      }

      const idx = selectedId ? itemIds.indexOf(selectedId) : -1;

      if (event.key === "j" || event.key === "k") {
        if (itemIds.length === 0) return;
        event.preventDefault();
        const next =
          event.key === "j"
            ? Math.min(idx < 0 ? 0 : idx + 1, itemIds.length - 1)
            : Math.max(idx < 0 ? 0 : idx - 1, 0);
        router.push(buildHref(itemIds[next]!));
        return;
      }

      if (event.key === "Enter" && selectedId) {
        event.preventDefault();
        router.push(buildHref(selectedId));
        return;
      }

      if ((event.key === "1" || event.key === "2") && selectedId) {
        const pane = document.querySelector("[data-mailbox-preview]");
        const forms = pane?.querySelectorAll<HTMLFormElement>(
          "form[action*='/disposition']",
        );
        if (!forms || forms.length === 0) return;
        const formIndex = event.key === "1" ? 0 : 1;
        const form = forms[formIndex] as HTMLFormElement | undefined;
        if (form) {
          event.preventDefault();
          form.requestSubmit();
        }
      }
    }

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [buildHref, itemIds, router, selectedId]);

  return null;
}
