import type { ApiRepoConfig } from "@/lib/api/client";

export function ProcessConfigProposalFields({
  workspaceId,
  repoId,
  config,
  processConfig,
  stateId,
  layoutJson,
}: {
  workspaceId: string;
  repoId?: string;
  config: ApiRepoConfig | null;
  processConfig: Record<string, unknown>;
  stateId?: string;
  layoutJson?: string;
}) {
  return (
    <>
      <input type="hidden" name="workspaceId" value={workspaceId} />
      <input type="hidden" name="repoId" value={repoId ?? ""} />
      {stateId ? <input type="hidden" name="stateId" value={stateId} /> : null}
      <input type="hidden" name="baseSha" value={config?.sha ?? ""} />
      <input
        type="hidden"
        name="lanesJson"
        value={JSON.stringify(config?.parsed?.lanes ?? {})}
      />
      <input
        type="hidden"
        name="processJson"
        value={JSON.stringify(processConfig)}
      />
      {layoutJson ? (
        <input type="hidden" name="layoutJson" value={layoutJson} />
      ) : null}
    </>
  );
}
