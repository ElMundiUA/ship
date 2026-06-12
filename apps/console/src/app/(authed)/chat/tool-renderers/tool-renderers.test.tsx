/**
 * Tool-renderer regression tests.
 *
 * ``renderToolResult`` is the load-bearing dispatch the chat UI
 * runs once per tool call (live SSE + hydrated history). A buggy
 * renderer must never blank the chat — every defence the function
 * relies on is pinned here so future refactors can't quietly
 * regress to "tool card disappears on a missing field":
 *
 * - Known tool name + happy payload → matching renderer wins.
 * - Error-shaped payload (``{error: "..."}``) on any tool name →
 *   ErrorCard.
 * - Unknown tool name → JsonFallback (still renders).
 * - Renderer throws mid-render → caught, JsonFallback rendered.
 * - Empty / partial payload → renderer either degrades gracefully
 *   or hands off to JsonFallback (no React error boundary).
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  TOOL_RENDERERS,
  renderToolResult,
  type ToolRenderer,
} from "./index";


describe("renderToolResult", () => {
  it("U1 — inbox_list happy payload renders the list card", () => {
    render(
      <>
        {renderToolResult("inbox_list", {
          total_estimate: 3,
          items: [
            {
              id: "abc",
              type: "clarification",
              status: "pending",
              title: "Investigate flaky test",
              owner_display: "denys",
              created_at: "2026-05-14T10:00:00Z",
            },
          ],
        })}
      </>,
    );
    expect(screen.getByText("Investigate flaky test")).toBeInTheDocument();
    // "Open Inbox" deeplink chip + per-row "Open" chip — both must
    // be present for the operator to be able to click through.
    expect(screen.getAllByText(/Open/).length).toBeGreaterThanOrEqual(1);
    const rowOpen = screen
      .getAllByRole("link")
      .find((el) => el.getAttribute("href") === "/approve/abc");
    expect(rowOpen).toBeDefined();
  });

  it("U2 — inbox_list empty items renders the cleared-queue state", () => {
    render(
      <>{renderToolResult("inbox_list", { items: [] })}</>,
    );
    expect(
      screen.getByText(/queue is clear/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/Open hub/i)).toBeInTheDocument();
  });

  it("U3 — error-shaped payload short-circuits any renderer to ErrorCard", () => {
    render(
      <>
        {renderToolResult("inbox_list", {
          error: "permission_denied",
          message: "you don't have access to this inbox row",
        })}
      </>,
    );
    // ErrorCard renders the error code + message; the rich list
    // shouldn't render alongside.
    expect(
      screen.getByText(/permission_denied/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/don't have access/i),
    ).toBeInTheDocument();
    // No "queue is clear" copy — the error path took precedence.
    expect(screen.queryByText(/queue is clear/i)).toBeNull();
  });

  it("U4 — unknown tool name falls back to JsonFallback", () => {
    render(
      <>
        {renderToolResult("totally_unknown_tool", {
          some: "payload",
          number: 42,
        })}
      </>,
    );
    // JsonFallback prints the raw JSON body so the user can still
    // eyeball what the tool returned. The header surfaces the
    // tool name run through ``prettyTool`` (underscores → spaces),
    // and the raw payload is visible in the collapsible details
    // block.
    expect(
      screen.getByText(/totally unknown tool/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/payload/)).toBeInTheDocument();
  });

  it("U5 — buggy renderer never blanks the chat", () => {
    // Hot-patch the registry with a renderer that throws. The
    // ``renderToolResult`` try/catch should swallow + fall back.
    const erroring: ToolRenderer = () => {
      throw new Error("synthetic crash");
    };
    const original = TOOL_RENDERERS["inbox_list"];
    (TOOL_RENDERERS as Record<string, ToolRenderer>)["inbox_list"] = erroring;
    try {
      render(<>{renderToolResult("inbox_list", { items: [] })}</>);
      // JsonFallback still renders something — the prettified tool
      // name surfaces in the header (underscores → spaces).
      expect(screen.getByText(/inbox list/i)).toBeInTheDocument();
    } finally {
      (TOOL_RENDERERS as Record<string, ToolRenderer>)["inbox_list"] = original;
    }
  });

  it("U6 — null result on a known tool degrades to fallback", () => {
    // Some servers will hand back ``null`` for "no data". The
    // renderer should not throw — it either renders an empty
    // state or hands off to JsonFallback. Either is fine; what we
    // pin is that the component tree mounts at all.
    expect(() => {
      render(<>{renderToolResult("inbox_list", null)}</>);
    }).not.toThrow();
  });

  it("U7 — error short-circuit ignores tool-renderer lookup for unknown tools too", () => {
    render(
      <>
        {renderToolResult("totally_unknown_tool", {
          error: "tool_not_implemented",
          message: "this tool is on the roadmap but not yet built",
        })}
      </>,
    );
    expect(screen.getByText(/tool_not_implemented/i)).toBeInTheDocument();
    expect(screen.getByText(/roadmap/i)).toBeInTheDocument();
  });
});


describe("TOOL_RENDERERS registry", () => {
  it("contains the documented Phase-6 tool set", () => {
    // If a tool is renamed on the backend the registry must keep
    // up — this assertion is a cheap canary so a backend rename
    // can't silently disable the rich rendering on prod.
    const expected = [
      "inbox_list",
      "inbox_get",
      "inbox_dispose",
      "runs_query",
      "run_detail",
    ];
    for (const name of expected) {
      expect(TOOL_RENDERERS).toHaveProperty(name);
    }
  });

  it("each registered renderer returns a ReactNode without throwing on empty input", () => {
    // The renderers all carry their own defensive ``asObject`` /
    // ``asArray`` shims so the worst case is "JsonFallback rendered"
    // — never an exception bubbling up to the chat client.
    for (const [name, fn] of Object.entries(TOOL_RENDERERS)) {
      let result;
      expect(
        () => {
          result = fn({});
        },
        `renderer ${name} should not throw on {}`,
      ).not.toThrow();
      expect(result).not.toBeNull();
    }
  });
});


// Helper not strictly necessary, but documents intent: silence the
// React 19 hydration warnings that fire when a fragment-wrapped
// node mounts directly under <body> in jsdom. They're noise here
// — not a real bug.
vi.spyOn(console, "error").mockImplementation((msg, ...rest) => {
  if (typeof msg === "string" && msg.includes("Warning:")) return;
  // eslint-disable-next-line no-console
  console.warn("console.error: ", msg, ...rest);
});
