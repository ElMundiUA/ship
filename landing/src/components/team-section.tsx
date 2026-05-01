import Image from "next/image";

type TeamMember = {
  name: string;
  handle?: string;
  role: string;
  href?: string;
  avatar?: string;
  featured?: boolean;
};

const leadership: TeamMember[] = [
  {
    name: "Denys Kuzin",
    handle: "denyskuzin",
    role: "Founder",
    href: "https://github.com/denyskuzin",
    avatar: "https://avatars.githubusercontent.com/u/761763?v=4",
    featured: true,
  },
  {
    name: "askslayer",
    handle: "askslayer",
    role: "Co-founder",
    href: "https://github.com/askslayer",
    avatar: "/team/askslayer.png",
    featured: true,
  },
  {
    name: "Nikolai Chesalin",
    role: "Board Advisor · Client Strategy",
    href: "https://sessionize.com/nikolai-chesalin",
    avatar: "/team/nikolai-chesalin.png",
    featured: true,
  },
];

const team: TeamMember[] = [
  {
    name: "Katsiaryna Laurynovich",
    handle: "KatsiarynaLaurynovich",
    role: "Developer",
    href: "https://github.com/KatsiarynaLaurynovich",
    avatar: "https://avatars.githubusercontent.com/u/7498477?v=4",
  },
  {
    name: "Danylo Mochuliak",
    handle: "omolynad",
    role: "QA",
    href: "https://github.com/omolynad",
    avatar: "https://avatars.githubusercontent.com/u/278433395?v=4",
  },
  {
    name: "svetlanamitar",
    handle: "svetlanamitar",
    role: "Developer",
    href: "https://github.com/svetlanamitar",
    avatar: "/team/svetlana-mitar.png",
  },
  {
    name: "vvladyslav-dev",
    handle: "vvladyslav-dev",
    role: "Developer",
    href: "https://github.com/vvladyslav-dev",
    avatar: "https://avatars.githubusercontent.com/u/236387037?v=4",
  },
];

function initials(name: string) {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");
}

function Avatar({ member, size = "lg" }: { member: TeamMember; size?: "md" | "lg" }) {
  const px = size === "lg" ? 80 : 56;
  const className =
    size === "lg"
      ? "h-20 w-20 rounded-2xl"
      : "h-14 w-14 rounded-xl";

  if (member.avatar) {
    return (
      <Image
        src={member.avatar}
        alt={`${member.name} profile photo`}
        width={px}
        height={px}
        className={`${className} border border-white/15 object-cover`}
      />
    );
  }

  return (
    <div
      className={`${className} flex items-center justify-center border border-aqua/25 bg-gradient-to-br from-aqua/20 via-white/[0.06] to-lilac/20 font-display text-lg font-bold text-white`}
      aria-label={`${member.name} initials`}
    >
      {initials(member.name)}
    </div>
  );
}

function MemberCard({ member }: { member: TeamMember }) {
  const content = (
    <>
      <Avatar member={member} />
      <div className="min-w-0">
        <p className="font-display text-lg font-bold text-white">{member.name}</p>
        {member.handle ? (
          <p className="mt-1 font-mono text-xs text-aqua/80">@{member.handle}</p>
        ) : null}
        <p className="mt-3 text-sm font-semibold uppercase tracking-[0.16em] text-white/45">
          {member.role}
        </p>
      </div>
    </>
  );

  const className =
    "group flex h-full flex-col gap-4 rounded-3xl border border-white/10 bg-gradient-to-br from-white/[0.07] via-white/[0.03] to-transparent p-5 shadow-card transition hover:border-aqua/35 hover:bg-white/[0.06] sm:flex-row";

  if (member.href) {
    return (
      <a href={member.href} target="_blank" rel="noreferrer" className={className}>
        {content}
      </a>
    );
  }

  return <div className={className}>{content}</div>;
}

function CompactMember({ member }: { member: TeamMember }) {
  return (
    <a
      href={member.href}
      target="_blank"
      rel="noreferrer"
      className="group flex items-center gap-3 rounded-2xl border border-white/10 bg-white/[0.03] p-3 transition hover:border-aqua/35 hover:bg-white/[0.06]"
    >
      <Avatar member={member} size="md" />
      <div className="min-w-0">
        <p className="truncate font-display text-sm font-bold text-white group-hover:text-aqua">
          {member.name}
        </p>
        <p className="mt-1 truncate font-mono text-[11px] text-white/45">@{member.handle}</p>
        <p className="mt-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-white/40">
          {member.role}
        </p>
      </div>
    </a>
  );
}

const COMPANY_FACTS: { kicker: string; value: string; note: string }[] = [
  { kicker: "Headquarters", value: "Kyiv & remote", note: "Distributed across CET / EET timezones." },
  { kicker: "Reference deployment", value: "ElMundi", note: "Ship was built and pressure-tested running ElMundi's own delivery." },
  { kicker: "Stage", value: "Closed beta", note: "Onboarding founder workspaces by invite, cohort by cohort." },
];

export function TeamSection() {
  return (
    <section id="team" className="border-y border-white/10 bg-gradient-to-br from-aqua/[0.05] via-black/30 to-sun/[0.04] py-20 sm:py-24">
      <div className="mx-auto max-w-[88rem] px-4 sm:px-6">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="text-xs font-bold uppercase tracking-[0.2em] text-aqua/85">Team &amp; company</p>
            <h2 className="font-display mt-3 text-3xl font-bold text-white sm:text-4xl">
              The people building Ship
            </h2>
            <p className="mt-4 max-w-2xl text-base leading-relaxed text-white/70 sm:text-lg">
              Ship is shipped by a small leadership and engineering team operating from Europe. The product was built and
              pressure-tested running our own delivery — every routine, specialist, and process you see has been used in
              anger before it left the workspace.
            </p>
          </div>
          <a
            href="https://github.com/ElMundiUA/ship"
            target="_blank"
            rel="noreferrer"
            className="btn-secondary inline-flex shrink-0"
          >
            View the repo
          </a>
        </div>

        <div className="mt-10 grid gap-3 items-stretch sm:grid-cols-3">
          {COMPANY_FACTS.map((fact) => (
            <div
              key={fact.kicker}
              className="h-full flex flex-col rounded-2xl border border-white/10 bg-white/[0.025] p-5"
            >
              <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-white/45">{fact.kicker}</p>
              <p className="font-display mt-2 text-lg font-bold text-white">{fact.value}</p>
              <p className="mt-2 text-xs leading-relaxed text-white/55">{fact.note}</p>
            </div>
          ))}
        </div>

        <div className="mt-12">
          <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-aqua/85">Leadership &amp; advisors</p>
          <div className="mt-4 grid gap-4 lg:grid-cols-3">
            {leadership.map((member) => (
              <MemberCard key={member.name} member={member} />
            ))}
          </div>
        </div>

        <div className="mt-10">
          <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-white/45">Engineering team</p>
          <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {team.map((member) => (
              <CompactMember key={member.handle} member={member} />
            ))}
          </div>
        </div>

      </div>
    </section>
  );
}
