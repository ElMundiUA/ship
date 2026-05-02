import { SettingsShell } from "../_shell/settings-shell";

export const dynamic = "force-dynamic";

export const metadata = { title: "Integrations — Workspace settings" };

export default function IntegrationsSettingsPage({
  searchParams,
}: {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}) {
  return <SettingsShell activeTab="integrations" searchParams={searchParams} />;
}
