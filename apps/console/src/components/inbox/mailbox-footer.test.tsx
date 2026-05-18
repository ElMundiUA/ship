import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MailboxFooter } from "@/components/inbox/mailbox-footer";
import type { InboxItemDetail } from "@/lib/inbox-types";

function detail(overrides: Partial<InboxItemDetail> = {}): InboxItemDetail {
  return {
    id: "item-1",
    workspace_id: "ws-1",
    repo_id: null,
    type: "report",
    status: "new",
    title: "Daily digest",
    summary: null,
    intake_handle: null,
    intake_reason: null,
    owner: null,
    play_key: null,
    run_id: null,
    created_at: "2026-05-18T10:00:00Z",
    due_at: null,
    snoozed_until: null,
    resolved_at: null,
    resolution: null,
    payload: {},
    events: [],
    source_table: null,
    source_id: null,
    ...overrides,
  };
}

describe("MailboxFooter checklist", () => {
  it("renders one row per action_item with six buttons total for three items", () => {
    render(
      <MailboxFooter
        workspaceId="ws-1"
        detail={detail({
          payload: {
            action_items: [
              {
                id: "a",
                prompt: "First?",
                primary: { label: "Go", choice: "go" },
                secondary: { label: "Skip", choice: "skip" },
              },
              {
                id: "b",
                prompt: "Second?",
                primary: { label: "Yes", choice: "yes" },
                secondary: { label: "No", choice: "no" },
              },
              {
                id: "c",
                prompt: "Third?",
                primary: { label: "Ok", choice: "ok" },
                secondary: { label: "Later", choice: "later" },
              },
            ],
          },
        })}
      />,
    );

    expect(screen.getByText("First?")).toBeInTheDocument();
    expect(screen.getByText("Second?")).toBeInTheDocument();
    expect(screen.getByText("Third?")).toBeInTheDocument();
    expect(screen.getAllByRole("button")).toHaveLength(6);
    expect(
      screen.getByRole("button", { name: "Go" }).closest("form"),
    ).toContainHTML('name="action_item_id"');
    expect(
      screen.getByRole("button", { name: "Go" }).closest("form"),
    ).toContainHTML('value="go"');
  });

  it("shows resolved one-liner instead of checklist when closed", () => {
    render(
      <MailboxFooter
        workspaceId="ws-1"
        detail={detail({
          status: "resolved",
          resolution: "acknowledged",
          payload: {
            action_items: [
              {
                id: "a",
                prompt: "First?",
                primary: { label: "Go", choice: "go" },
                secondary: { label: "Skip", choice: "skip" },
              },
            ],
          },
        })}
      />,
    );

    expect(screen.getByText(/Resolved/)).toBeInTheDocument();
    expect(screen.queryByText("First?")).not.toBeInTheDocument();
  });
});
