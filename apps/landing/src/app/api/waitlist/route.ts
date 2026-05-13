import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";

interface WaitlistSubmissionPayload {
  email: string;
  role?: string;
  tracker?: string;
  agent?: string;
  note?: string;
}

interface BackendResponse {
  ok: boolean;
}

/**
 * POST /api/waitlist
 *
 * Proxy endpoint that receives waitlist form submissions from the landing site
 * and forwards them to the backend's public /v1/public/waitlist endpoint.
 *
 * Returns 200 with { ok: true } on success.
 */
export async function POST(request: NextRequest): Promise<NextResponse> {
  try {
    const body: WaitlistSubmissionPayload = await request.json();

    // Validate required email field
    if (!body.email || typeof body.email !== "string") {
      return NextResponse.json(
        { error: "email is required and must be a string" },
        { status: 400 }
      );
    }

    // Get backend URL from environment
    const backendUrl = process.env.BACKEND_URL || "http://localhost:8000";

    // Forward to backend endpoint
    const response = await fetch(`${backendUrl}/v1/public/waitlist`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        email: body.email,
        role: body.role || null,
        tracker: body.tracker || null,
        agent: body.agent || null,
        note: body.note || null,
      }),
    });

    // Handle backend errors
    if (!response.ok) {
      const errorData = await response.text();
      console.error("Backend waitlist error:", {
        status: response.status,
        body: errorData,
      });

      return NextResponse.json(
        { error: "Failed to submit waitlist form" },
        { status: response.status }
      );
    }

    const result: BackendResponse = await response.json();

    return NextResponse.json(result, { status: 200 });
  } catch (error) {
    console.error("Waitlist API error:", error);
    return NextResponse.json(
      { error: "Internal server error" },
      { status: 500 }
    );
  }
}
