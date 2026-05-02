import { SettingsShell } from "../_shell/settings-shell";

export const dynamic = "force-dynamic";

export const metadata = { title: "Connected code — Workspace settings" };

export default function RepositoriesSettingsPage({
  searchParams,
}: {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}) {
  return <SettingsShell activeTab="repositories" searchParams={searchParams} />;
}
