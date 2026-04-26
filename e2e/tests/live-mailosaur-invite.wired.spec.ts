import { expect, test } from "@playwright/test";

import {
  extractInviteUrl,
  mailosaurConfigured,
  uniqueMailosaurEmail,
  waitForMailosaurMessage,
} from "../lib/mailosaur";
import {
  hasShipApiCredentials,
  shipApiBase,
  shipApiDelete,
  shipApiGet,
  shipApiPost,
  shipResolveWorkspaceId,
} from "../lib/ship-api";
import { hasPlaywrightStorageState } from "../lib/storage";

/**
 * Live invite-email smoke via Mailosaur.
 *
 * This proves the staging API queues the transactional invite email, the
 * configured email provider delivers it to the Mailosaur inbox, and the public
 * invite page/API can read the token. Actual acceptance is opt-in because it
 * needs a second authenticated browser state whose email matches the invite.
 *
 * @deployed
 */
test.describe("live Mailosaur invite flow (wired)", () => {
  test.describe.configure({ mode: "serial", timeout: 180_000 });

  test("@deployed invite is emailed and opens the accept page", async ({
    browser,
    page,
    request,
  }) => {
    test.skip(
      process.env.E2E_RUN_MAILOSAUR !== "1",
      "Set E2E_RUN_MAILOSAUR=1 to send real invite email",
    );
    test.skip(
      !hasPlaywrightStorageState(),
      "Set E2E_STORAGE_STATE for the admin console session",
    );
    test.skip(
      !hasShipApiCredentials(),
      "Set E2E_SHIP_API_BASE + E2E_SHIP_API_TOKEN",
    );
    test.skip(
      !mailosaurConfigured(),
      "Set MAILOSAUR_API_KEY + MAILOSAUR_SERVER_ID",
    );

    const workspaceId = await shipResolveWorkspaceId(request);
    const email = uniqueMailosaurEmail("invite");
    let inviteId: string | null = null;
    let acceptedMemberEmail: string | null = null;

    try {
      const create = await shipApiPost(
        request,
        `/v1/workspaces/${encodeURIComponent(workspaceId)}/invites`,
        {
          invites: [{ email, role: "viewer" }],
          ttl_days: 1,
        },
      );
      expect(create.status(), "POST /invites").toBe(201);
      const invites = (await create.json()) as {
        id: string;
        email: string;
        token?: string | null;
        accept_url?: string | null;
        email_status?: string | null;
      }[];
      const invite = invites[0];
      expect(invite?.email).toBe(email.toLowerCase());
      expect(invite?.email_status, "invite email should be queued").toBe("queued");
      inviteId = invite.id;

      const message = await waitForMailosaurMessage({
        sentTo: email,
        subject: /ship|invite/i,
      });
      test.info().annotations.push({
        type: "mailosaur-message",
        description: message.id,
      });

      const emailedUrl = extractInviteUrl(message) ?? invite.accept_url;
      expect(emailedUrl, "email contains /invite?token=...").toBeTruthy();
      const token = new URL(emailedUrl!).searchParams.get("token") ?? invite.token;
      expect(token, "invite token").toBeTruthy();

      const peek = await request.get(
        `${shipApiBase()}/v1/invites/${encodeURIComponent(token!)}`,
        { headers: { Accept: "application/json" } },
      );
      expect(peek.ok(), `GET /v1/invites/{token} ${peek.status()}`).toBeTruthy();
      const peekBody = (await peek.json()) as {
        email: string;
        role: string;
        workspace_id: string;
      };
      expect(peekBody.email).toBe(email.toLowerCase());
      expect(peekBody.role).toBe("viewer");
      expect(peekBody.workspace_id).toBe(workspaceId);

      await page.goto(emailedUrl!);
      await expect(
        page.getByRole("heading", { name: /you're invited to ship/i }),
      ).toBeVisible({ timeout: 30_000 });
      await expect(page.getByText(email.toLowerCase())).toBeVisible();
      await expect(page.getByRole("button", { name: /accept invite/i })).toBeVisible();

      const inviteeStorage = process.env.E2E_INVITEE_STORAGE_STATE?.trim();
      if (!inviteeStorage) {
        test.info().annotations.push({
          type: "invite-acceptance",
          description:
            "Skipped acceptance: set E2E_INVITEE_STORAGE_STATE for an account matching the Mailosaur email.",
        });
        return;
      }

      const invitee = await browser.newContext({ storageState: inviteeStorage });
      const inviteePage = await invitee.newPage();
      try {
        await inviteePage.goto(emailedUrl!);
        await inviteePage.getByRole("button", { name: /accept invite/i }).click();
        await expect(inviteePage).toHaveURL(/joined=1|reason=invite_accepted/, {
          timeout: 30_000,
        });
        acceptedMemberEmail = email.toLowerCase();
      } finally {
        await invitee.close();
      }
    } finally {
      if (acceptedMemberEmail) {
        const members = await shipApiGet(
          request,
          `/v1/workspaces/${encodeURIComponent(workspaceId)}/members`,
        );
        if (members.ok()) {
          const rows = (await members.json()) as { id: string; email: string }[];
          const member = rows.find((row) => row.email === acceptedMemberEmail);
          if (member) {
            const del = await shipApiDelete(
              request,
              `/v1/workspaces/${encodeURIComponent(workspaceId)}/members/${member.id}`,
            );
            expect([204, 404, 409]).toContain(del.status());
          }
        }
      }
      if (inviteId) {
        const del = await shipApiDelete(
          request,
          `/v1/workspaces/${encodeURIComponent(workspaceId)}/invites/${inviteId}`,
        );
        expect([204, 404, 409]).toContain(del.status());
      }
    }
  });
});
