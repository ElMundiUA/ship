import Link from "next/link";
import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";

/** Same funnel as the landing header: evaluate → product → manual depth. */
const NAV = [
  { href: "/docs/getting-started", label: "Getting started" },
  { href: "/use-cases", label: "Use cases" },
  { href: "/patterns", label: "Org patterns" },
  { href: "/workflows", label: "Workflows" },
  { href: "/collections", label: "Collections" },
  { href: "/tools", label: "Tools" },
  { href: "/docs", label: "Start here" },
  { href: "/docs/prompts-workflows", label: "Prompts & workflows" },
  { href: "/docs/tools", label: "Manual · Tools" },
  { href: "/book", label: "The book" },
  { href: "/docs/adoption", label: "Adoption" },
];

export default function DocsLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-ink pt-16">
      <SiteHeader />
      <div className="border-b border-white/10 bg-black/30">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6">
          <Link href="/docs" className="font-display text-sm font-bold text-white">
            Manual<span className="text-aqua">.</span>
          </Link>
          <nav className="flex flex-wrap gap-1 text-xs sm:gap-2 sm:text-sm">
            {NAV.map((n) => (
              <Link
                key={n.href}
                href={n.href}
                className="rounded-full px-2.5 py-1 text-white/65 transition hover:bg-white/10 hover:text-white sm:px-3"
              >
                {n.label}
              </Link>
            ))}
          </nav>
          <Link href="/" className="text-xs font-semibold text-aqua underline-offset-2 hover:underline sm:text-sm">
            ← Landing
          </Link>
        </div>
      </div>
      {children}
      <SiteFooter />
    </div>
  );
}
