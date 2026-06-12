import { expect, type APIRequestContext } from "@playwright/test";

import { shipApiGet, shipApiPost } from "./ship-api";

export type InboxListItem = {
  id: string;
  title: string;
  status: string;
  type: string;
  owner?: { email?: string; user_id?: string } | null;
};

export type InboxItemDetail = {
  id: string;
  title: string;
  status: string;
  type: string;
  resolution?: string | null;
  events?: { id: string; action: string; payload?: Record<string, unknown> }[];
  owner?: { email?: string; display_name?: string | null } | null;
};

export type WorkspaceMember = {
  user_id: string;
  email: string;
  display_name?: string | null;
  role?: string;
};

/** Mint a disposable inbox row (WriteOut has no item id — list by title). */
export async function mintInboxItem(
  request: APIRequestContext,
  workspaceId: string,
  body: {
    type: string;
    title: string;
    summary?: string;
    /** Merged into the row's payload bag — e.g. `{stakes: "destructive"}`
     * to exercise the typed-slug confirm on /approve/{id}. */
    payload?: Record<string, unknown>;
  },
): Promise<void> {
  const res = await shipApiPost(
    request,
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/inbox/items`,
    body,
  );
  expect(res.ok(), `POST inbox/items → ${res.status()}`).toBeTruthy();
}

/** Resolve inbox item id after mint (polls list API). */
export async function findInboxItemIdByTitle(
  request: APIRequestContext,
  workspaceId: string,
  title: string,
  options?: { status?: string; attempts?: number; match?: "exact" | "contains" },
): Promise<string> {
  const status = options?.status ?? "new";
  const attempts = options?.attempts ?? 8;
  const match = options?.match ?? "exact";
  const path = `/v1/workspaces/${encodeURIComponent(workspaceId)}/inbox?ownership=all&status=${encodeURIComponent(status)}&limit=100`;

  for (let i = 0; i < attempts; i++) {
    const res = await shipApiGet(request, path);
    expect(res.ok(), `GET inbox list → ${res.status()}`).toBeTruthy();
    const data = (await res.json()) as { items: InboxListItem[] };
    const found = data.items.find((row) =>
      match === "exact" ? row.title === title : row.title.includes(title),
    );
    if (found) return found.id;
    await new Promise((r) => setTimeout(r, 400));
  }
  throw new Error(`inbox item not found for title: ${title}`);
}

export async function getInboxItemDetail(
  request: APIRequestContext,
  workspaceId: string,
  itemId: string,
): Promise<InboxItemDetail> {
  const res = await shipApiGet(
    request,
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/inbox/${encodeURIComponent(itemId)}`,
  );
  expect(res.ok(), `GET inbox item → ${res.status()}`).toBeTruthy();
  return (await res.json()) as InboxItemDetail;
}

export async function listWorkspaceMembers(
  request: APIRequestContext,
  workspaceId: string,
): Promise<WorkspaceMember[]> {
  const res = await shipApiGet(
    request,
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/members`,
  );
  expect(res.ok(), `GET members → ${res.status()}`).toBeTruthy();
  return (await res.json()) as WorkspaceMember[];
}
