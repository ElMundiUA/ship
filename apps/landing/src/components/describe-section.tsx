export function DescribeSection() {
  return (
    <section
      id="describe"
      aria-labelledby="describe-heading"
      className="border-y border-white/10 bg-black/20 py-20 sm:py-24"
    >
      <div className="mx-auto max-w-[88rem] px-4 sm:px-6">
        <p className="text-sm font-bold uppercase tracking-widest text-aqua/90">Step 1</p>
        <h2
          id="describe-heading"
          className="font-display mt-2 text-3xl font-bold text-white sm:text-4xl"
        >
          Describe
        </h2>
        <p className="mt-4 max-w-2xl text-lg leading-relaxed text-white/65">
          Tell Ship what you want in plain English — who it&apos;s for, what it should do, how it
          should feel. No tickets, no wireframes, no repo setup. Just your idea, the way you&apos;d
          explain it to a co-founder over coffee.
        </p>
        <ul className="mt-10 grid gap-4 sm:grid-cols-3">
          {[
            "Founder-friendly prompts, not operator jargon",
            "Your tone and constraints captured up front",
            "Ship asks only when something is ambiguous",
          ].map((item) => (
            <li
              key={item}
              className="rounded-2xl border border-white/10 bg-white/[0.03] px-5 py-4 text-sm text-white/75"
            >
              {item}
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
