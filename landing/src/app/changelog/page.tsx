import type { Metadata } from "next";
import Link from "next/link";
import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";
import {
  type ChangelogWeek,
  listChangelogWeeks,
} from "@/lib/changelog";
import { repoUrl } from "@/lib/config";

export const metadata: Metadata = {
  title: "Changelog — Ship",
  description:
    "Public log of merged PRs to ship/main. Grouped by week, by area. No marketing copy — what shipped, when, with a link to the PR.",
  alternates: { canonical: "/changelog" },
};

function PRLink({ number }: { number: number }) {
  return (
    <a
      href={`${repoUrl}/pull/${number}`}
      target="_blank"
      rel="noreferrer"
      className="ml-2 inline-flex shrink-0 rounded-md border border-white/10 bg-white/[0.04] px-1.5 py-0.5 text-[11px] font-mono text-white/55 transition hover:border-aqua/40 hover:bg-aqua/10 hover:text-aqua"
    >
      #{number}
    </a>
  );
}

function ShaTag({ short }: { short: string }) {
  return (
    <a
      href={`${repoUrl}/commit/${short}`}
      target="_blank"
      rel="noreferrer"
      className="ml-2 hidden font-mono text-[10px] text-white/30 hover:text-white/60 sm:inline"
    >
      {short}
    </a>
  );
}

function WeekCard({ week }: { week: ChangelogWeek }) {
  return (
    <article className="rounded-3xl border border-white/10 bg-gradient-to-b from-white/[0.04] to-transparent p-6 sm:p-10">
      <header className="flex items-baseline justify-between gap-4 border-b border-white/10 pb-5">
        <h2 className="font-display text-xl font-bold tracking-tight text-white sm:text-2xl">
          {week.rangeLabel}
        </h2>
        <span className="font-mono text-[11px] uppercase tracking-[0.2em] text-white/35">
          {week.weekStart}
        </span>
      </header>

      <div className="mt-6 space-y-7">
        {week.groups.map((group) => (
          <section key={group.label}>
            <h3 className="text-[11px] font-bold uppercase tracking-[0.22em] text-aqua/85">
              {group.label}
            </h3>
            <ul className="mt-3 space-y-2.5">
              {group.entries.map((entry) => (
                <li
                  key={entry.shortSha}
                  className="flex items-baseline gap-2 text-[15px] leading-relaxed text-white/80"
                >
                  <span className="mt-2 inline-block h-1 w-1 shrink-0 rounded-full bg-white/30" aria-hidden />
                  <span className="min-w-0 flex-1">
                    {entry.title}
                    <PRLink number={entry.prNumber} />
                    <ShaTag short={entry.shortSha} />
                  </span>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </article>
  );
}

export default function ChangelogPage() {
  const weeks = listChangelogWeeks();

  return (
    <>
      <SiteHeader />
      <main className="pb-32 pt-28 sm:pt-32">
        <div className="mx-auto max-w-3xl px-4 sm:px-6">
          <header className="mb-14">
            <p className="text-[11px] font-bold uppercase tracking-[0.3em] text-sun">Changelog</p>
            <h1 className="font-display mt-3 text-4xl font-bold leading-[1.05] tracking-tight text-white sm:text-5xl">
              What shipped, week by week.
            </h1>
            <p className="mt-5 max-w-xl text-base leading-relaxed text-white/60 sm:text-lg">
              A dry log of merged PRs into{" "}
              <a
                href={`${repoUrl}/commits/main`}
                target="_blank"
                rel="noreferrer"
                className="text-aqua/85 underline-offset-4 hover:underline"
              >
                ship/main
              </a>
              . Grouped weekly, sub-grouped by area. For the long-form thinking
              behind major changes, read{" "}
              <Link href="/blog" className="text-aqua/85 underline-offset-4 hover:underline">
                the Ship Log
              </Link>
              .
            </p>
          </header>

          {weeks.length === 0 ? (
            <p className="rounded-2xl border border-white/10 bg-white/[0.02] p-6 text-sm text-white/55">
              Build environment didn&apos;t expose git history (shallow clone or
              missing <code className="font-mono">.git</code>). The changelog
              regenerates on the next deploy with a full clone.
            </p>
          ) : (
            <div className="space-y-10">
              {weeks.map((week) => (
                <WeekCard key={week.weekStart} week={week} />
              ))}
            </div>
          )}
        </div>
      </main>
      <SiteFooter />
    </>
  );
}
