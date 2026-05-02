import { SettingsShell } from "../_shell/settings-shell";

export const dynamic = "force-dynamic";

export const metadata = { title: "API keys — Workspace settings" };

export default function ApiKeysSettingsPage({
  searchParams,
}: {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}) {
  return <SettingsShell activeTab="api-keys" searchParams={searchParams} />;
}
