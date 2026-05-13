import Image from "next/image";

export function FounderQuoteSection() {
  return (
    <section className="border-y border-white/10 bg-gradient-to-br from-aqua/[0.05] via-black/30 to-sun/[0.04] py-16 sm:py-24">
      <div className="mx-auto max-w-4xl px-4 sm:px-6">
        <p className="text-xs font-bold uppercase tracking-[0.2em] text-aqua/85">From the founder</p>
        <figure className="mt-6">
          <blockquote className="font-display text-2xl leading-snug text-white sm:text-3xl sm:leading-snug">
            Five years ago I built startups by hiring people. Today I build them by hiring
            processes. Same outcomes, lower coordination tax — but only if every state, routine,
            and specialist is something you can review like code.
            <span className="mt-4 block bg-gradient-to-r from-aqua via-aqua to-sun bg-clip-text text-transparent">
              Vibes don&apos;t ship; contracts do.
            </span>
          </blockquote>
          <figcaption className="mt-8 flex items-center gap-3">
            <Image
              src="https://avatars.githubusercontent.com/u/761763?v=4"
              alt="Denys Kuzin"
              width={56}
              height={56}
              className="h-14 w-14 rounded-2xl border border-white/15 object-cover"
            />
            <div>
              <p className="font-display text-base font-bold text-white">Denys Kuzin</p>
              <p className="text-xs uppercase tracking-[0.18em] text-white/55">
                founder, Ship &amp; ElMundi
              </p>
            </div>
          </figcaption>
        </figure>
      </div>
    </section>
  );
}
