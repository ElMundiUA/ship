import type { APIRequestContext } from "@playwright/test";

/** То же, что `SHIP_API_URL` у консоли: origin FastAPI без `/` в конце. */
export function shipApiBase(): string | null {
  const b = process.env.E2E_SHIP_API_BASE?.trim().replace(/\/+$/, "");
  return b && b.length > 0 ? b : null;
}

/** Bearer (CLI / mint в Console). Нужен **admin** для POST clarification/improvement. */
export function shipApiToken(): string | null {
  const t = process.env.E2E_SHIP_API_TOKEN?.trim();
  return t && t.length > 0 ? t : null;
}

export function hasShipApiCredentials(): boolean {
  return Boolean(shipApiBase() && shipApiToken());
}

export async function shipResolveWorkspaceId(
  request: APIRequestContext,
): Promise<string> {
  const env = process.env.E2E_WORKSPACE_ID?.trim();
  if (env) return env;
  const res = await shipApiGet(request, "/v1/workspaces");
  if (!res.ok()) {
    throw new Error(`GET /v1/workspaces → ${res.status()}`);
  }
  const list = (await res.json()) as { id: string }[];
  const id = list[0]?.id;
  if (!id) throw new Error("no workspace in /v1/workspaces");
  return id;
}

export async function shipApiGet(
  request: APIRequestContext,
  path: string,
): Promise<Awaited<ReturnType<APIRequestContext["get"]>>> {
  const base = shipApiBase()!;
  const token = shipApiToken()!;
  return request.get(`${base}${path}`, {
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: "application/json",
    },
  });
}

export async function shipApiPost(
  request: APIRequestContext,
  path: string,
  body: unknown,
): Promise<Awaited<ReturnType<APIRequestContext["post"]>>> {
  const base = shipApiBase()!;
  const token = shipApiToken()!;
  return request.post(`${base}${path}`, {
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    data: JSON.stringify(body),
  });
}

export async function shipApiPatch(
  request: APIRequestContext,
  path: string,
  body: unknown,
): Promise<Awaited<ReturnType<APIRequestContext["patch"]>>> {
  const base = shipApiBase()!;
  const token = shipApiToken()!;
  return request.patch(`${base}${path}`, {
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    data: JSON.stringify(body),
  });
}
