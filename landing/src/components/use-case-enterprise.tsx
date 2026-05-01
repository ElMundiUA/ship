import Image from "next/image";
import Link from "next/link";
import type { ReactNode } from "react";

export type UseCaseEvidence = { src: string; alt: string; caption: string };

export function UseCaseHero({
  eyebrow,
  title,
  subtitle,
  executive,
}: {
  eyebrow: string;
  title: string;
  subtitle: string;
  executive: string;
}) {
  return (
    <section className="relative overflow-hidden border-b border-white/10 pb-16 pt-28 sm:pb-20 sm:pt-32">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_90%_50%_at_50%_-15%,rgba(46,230,214,0.12),transparent_50%)]" />
      <div className="relative mx-auto max-w-4xl px-4 sm:px-6">
        <p className="text-xs font-bold uppercase tracking-[0.2em] text-aqua/90">{eyebrow}</p>
        <h1 className="font-display mt-4 text-4xl font-bold leading-tight text-white sm:text-5xl">{title}</h1>
        <p className="mt-5 text-lg text-white/70 sm:text-xl">{subtitle}</p>
        <div className="glass-panel mt-10 p-6 sm:p-8">
          <p className="text-xs font-bold uppercase tracking-widest text-white/40">Executive summary</p>
          <p className="mt-3 text-base leading-relaxed text-white/85 sm:text-lg">{executive}</p>
        </div>
      </div>
    </section>
  );
}

export type UseCaseSnapshotItem = { label: string; value: string };

/** "At a glance" row — sector / stack / size / status — designed to live directly under the hero. */
export function UseCaseSnapshot({ items }: { items: UseCaseSnapshotItem[] }) {
  if (!items.length) return null;
  return (
    <section className="border-b border-white/10 bg-black/20 py-10">
      <div className="mx-auto grid max-w-5xl grid-cols-2 gap-y-6 px-4 text-left sm:grid-cols-4 sm:px-6">
        {items.map((it) => (
          <div key={it.label}>
            <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-white/40">{it.label}</p>
            <p className="mt-2 text-sm font-semibold text-white sm:text-base">{it.value}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

export type UseCaseResult = {
  /** Big number / phrase (e.g. "5x", "Days, not weeks", "100%"). */
  headline: string;
  /** One-line label under the headline. */
  label: string;
  /** Optional supporting sentence. */
  detail?: string;
};

/** Three-up "results" / quantified outcomes panel. Place after the implementation section. */
export function UseCaseResults({
  kicker = "Results",
  title,
  caveat,
  results,
}: {
  kicker?: string;
  title: string;
  /** Print-friendly note about how the numbers were measured (keeps the section honest). */
  caveat?: string;
  results: UseCaseResult[];
}) {
  if (!results.length) return null;
  return (
    <section id="results" className="scroll-mt-24 border-b border-white/10 bg-gradient-to-b from-black/20 to-transparent py-14 sm:py-20">
      <div className="mx-auto max-w-5xl px-4 sm:px-6">
        <p className="text-xs font-bold uppercase tracking-widest text-aqua/90">{kicker}</p>
        <h2 className="font-display mt-2 text-2xl font-bold text-white sm:text-3xl">{title}</h2>
        <div className="mt-10 grid gap-6 md:grid-cols-3">
          {results.map((r) => (
            <div key={r.label} className="glass-panel p-6">
              <p className="font-display text-3xl font-bold text-aqua sm:text-4xl">{r.headline}</p>
              <p className="mt-3 text-sm font-semibold text-white">{r.label}</p>
              {r.detail ? <p className="mt-2 text-sm leading-relaxed text-white/65">{r.detail}</p> : null}
            </div>
          ))}
        </div>
        {caveat ? (
          <p className="mt-6 text-xs leading-relaxed text-white/45">{caveat}</p>
        ) : null}
      </div>
    </section>
  );
}

export type UseCaseStakeholder = {
  role: string;
  perspective: string;
};

/** "What different stakeholders see" — Engineering, Security, Procurement, Leadership. */
export function UseCaseStakeholders({
  title = "What different stakeholders see",
  items,
}: {
  title?: string;
  items: UseCaseStakeholder[];
}) {
  if (!items.length) return null;
  return (
    <section className="border-b border-white/10 py-14 sm:py-16">
      <div className="mx-auto max-w-4xl px-4 sm:px-6">
        <p className="text-xs font-bold uppercase tracking-widest text-white/45">Stakeholder view</p>
        <h2 className="font-display mt-2 text-2xl font-bold text-white sm:text-3xl">{title}</h2>
        <div className="mt-8 grid gap-4 sm:grid-cols-2">
          {items.map((s) => (
            <div key={s.role} className="rounded-2xl border border-white/10 bg-white/[0.03] p-5">
              <p className="text-[11px] font-bold uppercase tracking-[0.16em] text-aqua/85">{s.role}</p>
              <p className="mt-3 text-sm leading-relaxed text-white/80">{s.perspective}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

export function UseCaseSection({
  id,
  kicker,
  title,
  children,
}: {
  id: string;
  kicker: string;
  title: string;
  children: ReactNode;
}) {
  return (
    <section id={id} className="scroll-mt-24 border-b border-white/10 py-14 sm:py-16">
      <div className="mx-auto max-w-3xl px-4 sm:px-6">
        <p className="text-xs font-bold uppercase tracking-widest text-white/45">{kicker}</p>
        <h2 className="font-display mt-2 text-2xl font-bold text-white sm:text-3xl">{title}</h2>
        <div className="prose prose-invert prose-lg mt-8 max-w-none prose-p:text-white/78 prose-p:leading-relaxed prose-strong:text-white prose-li:marker:text-aqua/70">
          {children}
        </div>
      </div>
    </section>
  );
}

export function UseCaseEvidenceGrid({
  items,
  evidenceIntro,
}: {
  items: UseCaseEvidence[];
  /** When screenshots come from more than one host, set this so captions stay honest. */
  evidenceIntro?: string;
}) {
  if (!items.length) return null;
  return (
    <section id="evidence" className="scroll-mt-24 py-14 sm:py-20">
      <div className="mx-auto max-w-[88rem] px-4 sm:px-6">
        <p className="text-center text-xs font-bold uppercase tracking-widest text-sun/90">Evidence</p>
        <h2 className="font-display mt-2 text-center text-2xl font-bold text-white sm:text-3xl">Screens from the live site</h2>
        <p className="mx-auto mt-3 max-w-2xl text-center text-sm text-white/55">
          {evidenceIntro ??
            "Captured from the same Next.js surface readers use — marketing, workspace, and docs routes."}
        </p>
        <div className="mt-12 grid gap-8 md:grid-cols-2">
          {items.map((ev) => (
            <figure key={ev.src} className="glass-panel overflow-hidden p-3 sm:p-4">
              <div className="overflow-hidden rounded-xl border border-white/10 bg-black/40">
                <Image
                  src={ev.src}
                  alt={ev.alt}
                  width={1400}
                  height={900}
                  className="h-auto w-full object-cover object-top"
                  sizes="(max-width: 768px) 100vw, 50vw"
                />
              </div>
              <figcaption className="mt-3 px-1 text-sm text-white/60">{ev.caption}</figcaption>
            </figure>
          ))}
        </div>
      </div>
    </section>
  );
}

export function UseCaseCtaRow({ links }: { links: { href: string; label: string; variant?: "primary" | "secondary" }[] }) {
  return (
    <section className="py-14 sm:py-16">
      <div className="mx-auto flex max-w-3xl flex-wrap justify-center gap-3 px-4 sm:px-6">
        {links.map((l) => (
          <Link
            key={l.href}
            href={l.href}
            className={l.variant === "secondary" ? "btn-secondary inline-flex" : "btn-primary inline-flex"}
          >
            {l.label}
          </Link>
        ))}
      </div>
    </section>
  );
}
