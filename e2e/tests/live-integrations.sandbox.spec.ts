import { expect, test, type APIRequestContext } from "@playwright/test";

import {
  hasShipApiCredentials,
  shipApiGet,
  shipApiPost,
  shipApiPut,
  shipResolveWorkspaceId,
} from "../lib/ship-api";

type WorkspaceIntegration = {
  kind: string;
  secret: string | undefined;
  config: Record<string, unknown>;
};

type NativeInstall = {
  provider: string;
  path: string;
  body: Record<string, unknown>;
  configured: boolean;
};

/**
 * Live external integration probes.
 *
 * These specs intentionally exercise provider contracts independently from the
 * full onboarding journey. Missing provider secrets skip only that provider, so
 * the suite can grow as more sandbox accounts become available.
 *
 * @deployed
 */
test.describe("live external integrations (sandbox)", () => {
  test.describe.configure({ mode: "serial", timeout: 180_000 });

  test.beforeEach(() => {
    test.skip(
      process.env.E2E_RUN_EXTERNAL_INTEGRATIONS !== "1",
      "Set E2E_RUN_EXTERNAL_INTEGRATIONS=1 to probe live integrations",
    );
    test.skip(
      !hasShipApiCredentials(),
      "Set E2E_SHIP_API_BASE + E2E_SHIP_API_TOKEN",
    );
  });

  for (const integration of workspaceIntegrations()) {
    test(`workspace integration probe: ${integration.kind}`, async ({
      request,
    }) => {
      test.skip(
        !integration.secret,
        `Set ${envHintForKind(integration.kind)} to probe ${integration.kind}`,
      );
      const workspaceId = await shipResolveWorkspaceId(request);
      const row = await upsertAndProbeWorkspaceIntegration(
        request,
        workspaceId,
        integration,
      );
      expect(row.kind).toBe(integration.kind);
      expect(row.has_secret).toBe(true);
      expect(row.status, row.last_health_error ?? undefined).toBe("ok");
    });
  }

  for (const install of nativeInstalls()) {
    test(`native integration probe: ${install.provider}`, async ({ request }) => {
      test.skip(
        !install.configured,
        `Set sandbox credentials for ${install.provider}`,
      );
      const workspaceId = await shipResolveWorkspaceId(request);
      const create = await shipApiPost(
        request,
        `/v1/workspaces/${encodeURIComponent(workspaceId)}/native-integrations/${install.path}`,
        install.body,
      );
      expect(create.ok(), `${install.provider} install ${create.status()}`).toBeTruthy();
      const created = (await create.json()) as NativeIntegrationRow;
      expect(created.provider).toBe(install.provider);
      expect(created.has_credential).toBe(true);

      const probe = await shipApiPost(
        request,
        `/v1/workspaces/${encodeURIComponent(workspaceId)}/native-integrations/${created.id}/probe`,
        {},
      );
      expect(probe.ok(), `${install.provider} probe ${probe.status()}`).toBeTruthy();
      const probed = (await probe.json()) as NativeIntegrationRow;
      expect(probed.status, probed.last_health_error ?? undefined).toBe("ready");
    });
  }

  test("integration catalogs list configured rows", async ({ request }) => {
    const workspaceId = await shipResolveWorkspaceId(request);
    const ws = encodeURIComponent(workspaceId);

    const integrations = await shipApiGet(request, `/v1/workspaces/${ws}/integrations`);
    expect(integrations.ok(), `integrations ${integrations.status()}`).toBeTruthy();
    expect(Array.isArray(await integrations.json())).toBeTruthy();

    const native = await shipApiGet(
      request,
      `/v1/workspaces/${ws}/native-integrations`,
    );
    expect(native.ok(), `native integrations ${native.status()}`).toBeTruthy();
    expect(Array.isArray(await native.json())).toBeTruthy();
  });
});

async function upsertAndProbeWorkspaceIntegration(
  request: APIRequestContext,
  workspaceId: string,
  integration: WorkspaceIntegration,
): Promise<WorkspaceIntegrationRow> {
  const ws = encodeURIComponent(workspaceId);
  const kind = encodeURIComponent(integration.kind);
  const put = await shipApiPut(request, `/v1/workspaces/${ws}/integrations/${kind}`, {
    kind: integration.kind,
    config: integration.config,
    secret: integration.secret,
  });
  expect(put.ok(), `${integration.kind} PUT ${put.status()}`).toBeTruthy();

  const probe = await shipApiPost(
    request,
    `/v1/workspaces/${ws}/integrations/${kind}/probe`,
    {},
  );
  expect(probe.ok(), `${integration.kind} probe ${probe.status()}`).toBeTruthy();
  return (await probe.json()) as WorkspaceIntegrationRow;
}

function workspaceIntegrations(): WorkspaceIntegration[] {
  return [
    {
      kind: "github",
      secret: process.env.E2E_GITHUB_TOKEN || process.env.GITHUB_TOKEN,
      config: {},
    },
    {
      kind: "linear",
      secret: process.env.E2E_LINEAR_API_KEY,
      config: {},
    },
    {
      kind: "jira",
      secret: process.env.E2E_JIRA_API_TOKEN,
      config: {
        host: process.env.E2E_JIRA_SITE,
        email: process.env.E2E_JIRA_EMAIL,
      },
    },
    {
      kind: "notion",
      secret: process.env.E2E_NOTION_TOKEN,
      config: {},
    },
    {
      kind: "slack",
      secret: process.env.E2E_SLACK_BOT_TOKEN,
      config: {},
    },
    {
      kind: "gitlab",
      secret: process.env.E2E_GITLAB_TOKEN,
      config: {
        host: process.env.E2E_GITLAB_HOST || "gitlab.com",
      },
    },
    {
      kind: "webhook",
      secret: process.env.E2E_WEBHOOK_SECRET,
      config: {
        url: process.env.E2E_WEBHOOK_URL,
      },
    },
    {
      kind: "teams",
      secret: process.env.E2E_TEAMS_WEBHOOK_URL,
      config: {},
    },
    {
      kind: "otel",
      secret: process.env.E2E_OTEL_BEARER_TOKEN,
      config: {
        endpoint: process.env.E2E_OTEL_ENDPOINT,
      },
    },
    {
      kind: "s3-export",
      secret: process.env.E2E_S3_SECRET_ACCESS_KEY,
      config: {
        bucket: process.env.E2E_S3_BUCKET,
        region: process.env.E2E_S3_REGION,
        access_key_id: process.env.E2E_S3_ACCESS_KEY_ID,
      },
    },
  ];
}

function nativeInstalls(): NativeInstall[] {
  return [
    {
      provider: "atlassian",
      path: "atlassian/api-token",
      configured: Boolean(
        process.env.E2E_JIRA_SITE &&
          process.env.E2E_JIRA_EMAIL &&
          process.env.E2E_JIRA_API_TOKEN,
      ),
      body: {
        site: process.env.E2E_JIRA_SITE,
        email: process.env.E2E_JIRA_EMAIL,
        api_token: process.env.E2E_JIRA_API_TOKEN,
        jira_project: process.env.E2E_JIRA_PROJECT,
      },
    },
    {
      provider: "gitlab",
      path: "gitlab/pat",
      configured: Boolean(process.env.E2E_GITLAB_TOKEN),
      body: {
        host: process.env.E2E_GITLAB_HOST || "gitlab.com",
        pat: process.env.E2E_GITLAB_TOKEN,
        group: process.env.E2E_GITLAB_GROUP,
      },
    },
    {
      provider: "azure_devops",
      path: "azure-devops/pat",
      configured: Boolean(
        process.env.E2E_AZURE_DEVOPS_ORG && process.env.E2E_AZURE_DEVOPS_PAT,
      ),
      body: {
        organization: process.env.E2E_AZURE_DEVOPS_ORG,
        pat: process.env.E2E_AZURE_DEVOPS_PAT,
        project: process.env.E2E_AZURE_DEVOPS_PROJECT,
      },
    },
  ];
}

function envHintForKind(kind: string): string {
  const hints: Record<string, string> = {
    github: "E2E_GITHUB_TOKEN or GITHUB_TOKEN",
    linear: "E2E_LINEAR_API_KEY",
    jira: "E2E_JIRA_SITE + E2E_JIRA_EMAIL + E2E_JIRA_API_TOKEN",
    notion: "E2E_NOTION_TOKEN",
    slack: "E2E_SLACK_BOT_TOKEN",
    gitlab: "E2E_GITLAB_TOKEN",
    webhook: "E2E_WEBHOOK_URL + E2E_WEBHOOK_SECRET",
    teams: "E2E_TEAMS_WEBHOOK_URL",
    otel: "E2E_OTEL_ENDPOINT + E2E_OTEL_BEARER_TOKEN",
    "s3-export":
      "E2E_S3_BUCKET + E2E_S3_ACCESS_KEY_ID + E2E_S3_SECRET_ACCESS_KEY",
  };
  return hints[kind] ?? `credentials for ${kind}`;
}

type WorkspaceIntegrationRow = {
  kind: string;
  status: string;
  has_secret: boolean;
  last_health_error?: string | null;
};

type NativeIntegrationRow = {
  id: string;
  provider: string;
  status: string;
  has_credential: boolean;
  last_health_error?: string | null;
};
