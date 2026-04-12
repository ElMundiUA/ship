import Link from "next/link";
import { repoUrl } from "@/lib/config";

/**
 * Nav order (left → right): evaluation → proof → lanes → how it runs →
 * integrations → deep reference → then a vivid **The book** CTA and GitHub.
 */
const NAV: { href: string; label: string; className: string }[] = [
  { href: "/docs/getting-started", label: "Getting started", className: "" },
  { href: "/use-cases", label: "Use cases", className: "" },
  { href: "/patterns", label: "Patterns", className: "hidden sm:inline" },
  { href: "/workflows", label: "Workflows", className: "hidden md:inline" },
  { href: "/collections", label: "Collections", className: "hidden md:inline" },
  { href: "/tools", label: "Tools", className: "hidden lg:inline" },
  { href: "/docs", label: "Manual", className: "hidden sm:inline" },
];

export function SiteHeader() {
  return (
    <header className="fixed inset-x-0 top-0 z-50 border-b border-white/10 bg-ink/70 backdrop-blur-xl">
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between gap-3 px-4 sm:px-6">
        <Link href="/" className="shrink-0 font-display text-lg font-bold tracking-normal text-white">
          Ship<span className="text-aqua">.</span>
        </Link>
        <nav className="flex min-w-0 flex-1 items-center justify-end gap-0.5 text-sm sm:gap-1 md:gap-2">
          {NAV.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={["shrink-0 rounded-full px-2.5 py-1.5 text-white/75 transition hover:bg-white/10 hover:text-white sm:px-3", item.className].filter(Boolean).join(" ")}
            >
              {item.label}
            </Link>
          ))}
          <Link
            href="/book"
            className="ml-1 inline-flex shrink-0 items-center rounded-full bg-gradient-to-r from-amber-400 via-orange-500 to-fuchsia-600 px-3 py-2 text-xs font-bold uppercase tracking-wide text-zinc-950 shadow-[0_0_24px_rgba(249,115,22,0.45)] ring-2 ring-white/25 transition hover:brightness-110 hover:ring-white/40 sm:ml-2 sm:px-3.5 sm:text-[0.8rem]"
          >
            The book
          </Link>
          <a
            href={repoUrl}
            className="btn-secondary ml-1 shrink-0 !py-2 !text-xs sm:ml-2 sm:!text-sm"
            target="_blank"
            rel="noreferrer"
          >
            GitHub
          </a>
        </nav>
      </div>
    </header>
  );
}
