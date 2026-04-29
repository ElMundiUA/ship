import Image from "next/image";

type TeamMember = {
  name: string;
  handle?: string;
  role: string;
  href?: string;
  avatar?: string;
  note?: string;
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
    note: "AI product architect and enterprise client advisor.",
    featured: true,
  },
];

const team: TeamMember[] = [
  {
    name: "Katsiaryna Laurynovich",
    handle: "KatsiarynaLaurynovich",
    role: "Team",
    href: "https://github.com/KatsiarynaLaurynovich",
    avatar: "https://avatars.githubusercontent.com/u/7498477?v=4",
  },
  {
    name: "Danylo Mochuliak",
    handle: "omolynad",
    role: "Team",
    href: "https://github.com/omolynad",
    avatar: "https://avatars.githubusercontent.com/u/278433395?v=4",
  },
  {
    name: "svetlanamitar",
    handle: "svetlanamitar",
    role: "Team",
    href: "https://github.com/svetlanamitar",
    avatar: "https://avatars.githubusercontent.com/u/6459255?v=4",
  },
  {
    name: "vvladyslav-dev",
    handle: "vvladyslav-dev",
    role: "Team",
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
        {member.note ? <p className="mt-3 text-sm leading-relaxed text-white/60">{member.note}</p> : null}
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
      </div>
    </a>
  );
}

export function TeamSection() {
  return (
    <section id="team" className="border-y border-white/10 bg-gradient-to-br from-aqua/[0.06] via-black/20 to-lilac/[0.06] py-20 sm:py-24">
      <div className="mx-auto max-w-6xl px-4 sm:px-6">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="text-sm font-bold uppercase tracking-widest text-aqua/90">Team</p>
            <h2 className="font-display mt-2 text-3xl font-bold text-white sm:text-4xl">
              The people behind Ship
            </h2>
            <p className="mt-4 max-w-3xl text-lg text-white/65">
              Ship is built in public by a small product and engineering team, with advisory help for enterprise clients
              and go-to-market work.
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

        <div className="mt-10 grid gap-4 lg:grid-cols-3">
          {leadership.map((member) => (
            <MemberCard key={member.name} member={member} />
          ))}
        </div>

        <div className="mt-8">
          <p className="mb-4 text-xs font-bold uppercase tracking-[0.18em] text-white/40">
            Core team
          </p>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {team.map((member) => (
              <CompactMember key={member.handle} member={member} />
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
