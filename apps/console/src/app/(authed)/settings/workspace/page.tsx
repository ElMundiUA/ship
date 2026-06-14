import { SettingsShell } from "../_shell/settings-shell";

export const dynamic = "force-dynamic";

export const metadata = { title: "Workspace — Workspace settings" };

export default function WorkspaceSettingsPage({
  searchParams,
}: {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}) {
  return <SettingsShell activeTab="workspace" searchParams={searchParams} />;
}
