import type { Metadata } from "next";
import Link from "next/link";
import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";
import { WaitlistSection } from "@/components/waitlist-section";

export const metadata: Metadata = {
  title: "Request closed-beta access",
  description:
    "Tell us about your team and the problems you're solving. We review applications weekly and send invites within seven days. Closed beta seats are limited per cohort.",
};

export default async function BetaPage() {
  return (
    <>
      <SiteHeader />
      <main>
        {/* Hero section */}
        <section className="relative overflow-hidden border-b border-white/10 pb-16 pt-28 sm:pb-20 sm:pt-32">
          <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_80%_50%_at_50%_-10%,rgba(46,230,214,0.18),transparent_55%)]" />
          <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_60%_40%_at_85%_30%,rgba(209,167,255,0.12),transparent_60%)]" />
          <div className="relative mx-auto max-w-4xl px-4 text-center sm:px-6">
            <p className="text-xs font-bold uppercase tracking-[0.2em] text-aqua/90">Closed beta</p>
            <h1 className="font-display mt-4 text-4xl font-bold text-white sm:text-5xl lg:text-6xl">
              Request access to the Ship closed beta
            </h1>
            <p className="mx-auto mt-6 max-w-2xl text-lg text-white/70">
              Tell us about your team and the problems you&apos;re solving. Each application gets read by a human, weekly.
              If we can help, we send an invite within seven days.
            </p>
          </div>
        </section>

        {/* Form section */}
        <section className="border-b border-white/10 py-16 sm:py-20">
          <div className="mx-auto max-w-xl px-4 sm:px-6">
            <WaitlistSection />
          </div>
        </section>

        {/* What happens after you apply */}
        <section className="border-b border-white/10 py-16 sm:py-20">
          <div className="mx-auto max-w-4xl px-4 sm:px-6">
            <p className="text-xs font-bold uppercase tracking-[0.2em] text-aqua/85">Process</p>
            <h2 className="font-display mt-2 text-3xl font-bold text-white sm:text-4xl">
              What happens after you apply
            </h2>

            <div className="mt-10 grid gap-8 md:grid-cols-3">
              <div className="rounded-2xl border border-white/10 bg-white/[0.02] p-6">
                <p className="text-sm font-bold uppercase tracking-[0.18em] text-aqua/85">01 — We read it</p>
                <p className="mt-3 text-base leading-relaxed text-white/70">
                  Applications are reviewed weekly by a human, not a bot. Most teams hear back within seven days.
                </p>
              </div>

              <div className="rounded-2xl border border-white/10 bg-white/[0.02] p-6">
                <p className="text-sm font-bold uppercase tracking-[0.18em] text-lilac/85">02 — We send an invite</p>
                <p className="mt-3 text-base leading-relaxed text-white/70">
                  If we can help your team this cohort, you get an invite link to set up the workspace. If we cannot yet,
                  we say why and add you to the next cohort.
                </p>
              </div>

              <div className="rounded-2xl border border-white/10 bg-white/[0.02] p-6">
                <p className="text-sm font-bold uppercase tracking-[0.18em] text-sun/85">03 — You set up in 20 minutes</p>
                <p className="mt-3 text-base leading-relaxed text-white/70">
                  The wizard walks GitHub install, repo activation, tracker binding, members, and policies. Then you open
                  the Inbox.
                </p>
              </div>
            </div>
          </div>
        </section>

        {/* Closer */}
        <section className="py-16 sm:py-20">
          <div className="mx-auto max-w-2xl px-4 text-center sm:px-6">
            <p className="text-sm leading-relaxed text-white/55">
              Ship is in closed beta because we are still tuning the process model with real teams. Each cohort is small on
              purpose. Reading the <Link href="/book" className="text-sun hover:text-white transition">book</Link> and the{" "}
              <Link href="/roadmap" className="text-sun hover:text-white transition">roadmap</Link> is the right way to
              decide whether you want in.
            </p>
          </div>
        </section>
      </main>
      <SiteFooter />
    </>
  );
}
