import { AppShell } from "@/components/app-shell";
import {
  Badge,
  ButtonGhost,
  ButtonPrimary,
  Card,
  CardHeader,
  MockBanner,
} from "@/components/ui";
import { workspaces } from "@/lib/mock/cloud";

const ws = workspaces[0];

type MemberRow = {
  initials: string;
  name: string;
  email: string;
  role: "owner" | "admin" | "maintainer" | "member" | "viewer";
  active: string;
  source: "github" | "local" | "oidc";
};

const members: MemberRow[] = [
  { initials: "DK", name: "Denis K.",     email: "denis@helio.dev",  role: "owner",      active: "now",         source: "github" },
  { initials: "MT", name: "Mira Tan",     email: "mira@helio.dev",   role: "admin",      active: "12m ago",     source: "github" },
  { initials: "JL", name: "Jordan Lee",   email: "jordan@helio.dev", role: "maintainer", active: "1h ago",      source: "github" },
  { initials: "SC", name: "Sam Chen",     email: "sam@helio.dev",    role: "maintainer", active: "3h ago",      source: "github" },
  { initials: "RP", name: "Riley Park",   email: "riley@helio.dev",  role: "member",     active: "yesterday",   source: "github" },
  { initials: "AV", name: "Asha Verma",   email: "asha@helio.dev",   role: "member",     active: "2d ago",      source: "oidc"   },
  { initials: "EB", name: "Eli Becker",   email: "eli@helio.dev",    role: "viewer",     active: "1w ago",      source: "oidc"   },
];

export default function MembersPage() {
  return (
    <AppShell
      kicker={`${ws.name} · access`}
      title="Members"
      actions={
        <>
          <ButtonGhost>Open invite link</ButtonGhost>
          <ButtonPrimary>+ Invite</ButtonPrimary>
        </>
      }
    >
      <MockBanner />

      <div className="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Total" value={members.length.toString()} />
        <Stat label="Owners + admins" value={members.filter((m) => m.role === "owner" || m.role === "admin").length.toString()} />
        <Stat label="Active this week" value="6" />
        <Stat label="Pending invites" value="2" />
      </div>

      <Card padded={false} className="overflow-hidden">
        <CardHeader
          className="px-5 pt-5"
          title="Workspace members"
          subtitle="Roles map to RBAC tiers used by /v1: owner | admin | maintainer | member | viewer"
        />
        <table className="min-w-full text-sm">
          <thead className="bg-white/[0.04] text-[10px] uppercase tracking-widest text-white/45">
            <tr>
              <th className="px-4 py-2 text-left font-semibold">Member</th>
              <th className="px-4 py-2 text-left font-semibold">Role</th>
              <th className="px-4 py-2 text-left font-semibold">Identity</th>
              <th className="px-4 py-2 text-left font-semibold">Last active</th>
              <th className="px-4 py-2 text-right font-semibold"></th>
            </tr>
          </thead>
          <tbody>
            {members.map((m) => (
              <tr key={m.email} className="border-t border-white/5 hover:bg-white/[0.02]">
                <td className="px-4 py-3 align-top">
                  <div className="flex items-center gap-3">
                    <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-gradient-to-br from-aqua via-lilac to-coral text-[11px] font-bold text-ink">
                      {m.initials}
                    </span>
                    <div className="min-w-0">
                      <div className="font-semibold text-white">{m.name}</div>
                      <div className="text-[11px] text-white/50">{m.email}</div>
                    </div>
                  </div>
                </td>
                <td className="px-4 py-3 align-top">
                  <select
                    defaultValue={m.role}
                    className="rounded-full border border-white/15 bg-white/[0.05] px-3 py-1 text-xs font-semibold text-white/85 outline-none focus:border-aqua/40"
                  >
                    <option value="owner">owner</option>
                    <option value="admin">admin</option>
                    <option value="maintainer">maintainer</option>
                    <option value="member">member</option>
                    <option value="viewer">viewer</option>
                  </select>
                </td>
                <td className="px-4 py-3 align-top">
                  <Badge tone={m.source === "github" ? "info" : m.source === "oidc" ? "workspace" : "neutral"}>
                    {m.source}
                  </Badge>
                </td>
                <td className="px-4 py-3 align-top text-xs text-white/55">{m.active}</td>
                <td className="px-4 py-3 text-right align-top">
                  <button className="text-[11px] font-semibold text-coral/80 hover:text-coral">
                    Remove
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </AppShell>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <Card>
      <div className="text-[10px] font-bold uppercase tracking-widest text-white/45">{label}</div>
      <div className="mt-1 font-display text-2xl font-bold text-white">{value}</div>
    </Card>
  );
}
