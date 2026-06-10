import Link from "next/link";
import { HeroProductDemo } from "@/components/hero-product-demo";

export function HeroSection() {
  return (
    <section className="relative overflow-hidden pt-28 pb-20 sm:pt-32 sm:pb-28">
      <div
        className="pointer-events-none absolute inset-0 opacity-[0.35]"
        style={{
          backgroundImage:
            "url(\"data:image/svg+xml,%3Csvg width='60' height='60' viewBox='0 0 60 60' xmlns='http://www.w3.org/2000/svg'%3E%3Cg fill='none' fill-rule='evenodd'%3E%3Cg fill='%23ffffff' fill-opacity='0.06'%3E%3Cpath d='M36 34v-4h-2v4h-4v2h4v4h2v-4h4v-2h-4zm0-30V0h-2v4h-4v2h4v4h2V6h4V4h-4zM6 34v-4H4v4H0v2h4v4h2v-4h4v-2H6zM6 4V0H4v4H0v2h4v4h2V6h4V4H6z'/%3E%3C/g%3E%3C/g%3E%3C/svg%3E\")",
        }}
      />
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_70%_45%_at_50%_-10%,rgba(207,169,107,0.10),transparent_60%)]" />
      <div className="relative mx-auto max-w-[88rem] px-4 sm:px-6">
        <Link
          href="#waitlist"
          className="mb-3 inline-flex items-center gap-2 rounded-full border border-aqua/30 bg-aqua/[0.08] px-4 py-1.5 text-xs font-semibold uppercase tracking-widest text-aqua transition hover:border-aqua/55 hover:bg-aqua/[0.14]"
        >
          <span className="inline-block h-1.5 w-1.5 rounded-full bg-aqua" aria-hidden />
          <span>Founders welcome</span>
          <span aria-hidden className="text-white/30">·</span>
          <span className="text-aqua/85">Join the waitlist →</span>
        </Link>
        <h1 className="font-display max-w-5xl text-[2.125rem] font-bold leading-[1.08] tracking-normal text-white sm:text-5xl sm:leading-[1.06] md:text-6xl md:leading-[1.05] lg:text-[3.45rem] lg:leading-[1.03]">
          If you can{" "}
          <span className="bg-gradient-to-r from-coral via-sun to-aqua bg-clip-text text-transparent">
            describe it
          </span>
          , you can ship it.
        </h1>
        <p className="mt-6 max-w-2xl text-lg leading-relaxed text-white/70 sm:text-xl md:text-[1.35rem] md:leading-relaxed">
          Ship turns your idea into a live app — describe what you want, press go, and share a
          real preview. Built for non-technical founders who want results, not repo archaeology.
        </p>

        <HeroProductDemo />

        <div className="mt-10 flex flex-col flex-wrap gap-4 sm:flex-row sm:items-center">
          <Link className="btn-primary text-center sm:text-left" href="#waitlist">
            Join the waitlist
          </Link>
          <Link className="btn-ghost text-center" href="/beta">
            Request closed-beta access
          </Link>
        </div>
      </div>
    </section>
  );
}
