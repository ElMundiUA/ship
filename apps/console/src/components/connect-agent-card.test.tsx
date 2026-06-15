import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ConnectAgentCard } from "@/components/connect-agent-card";

describe("ConnectAgentCard (shadcn pilot)", () => {
  it("renders expanded card with shadcn controls and test ids", () => {
    render(
      <ConnectAgentCard
        workspaceId="ws-test"
        mcpEndpoint="https://mcp.example.com/v1"
      />,
    );

    expect(screen.getByTestId("connect-agent-card")).toBeInTheDocument();
    expect(screen.getByTestId("connect-agent-dismiss")).toBeInTheDocument();
    expect(screen.getByTestId("connect-agent-mint")).toBeInTheDocument();
    expect(screen.getByText("Connect your agent")).toBeInTheDocument();
    expect(screen.getByText("MCP endpoint")).toBeInTheDocument();
  });
});
