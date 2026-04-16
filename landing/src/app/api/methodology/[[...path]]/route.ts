import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

/** Internal FastAPI root (no trailing slash), e.g. http://127.0.0.1:8100 */
function upstreamBase(): string {
  return (process.env.SHIP_METHODOLOGY_UPSTREAM_URL ?? "").replace(/\/$/, "");
}

async function proxy(req: NextRequest, pathSegments: string[], method: "GET" | "POST") {
  const upstream = upstreamBase();
  if (!upstream) {
    return NextResponse.json(
      {
        error:
          "Methodology proxy is not configured. Set SHIP_METHODOLOGY_UPSTREAM_URL to the internal FastAPI base URL (same routes as uvicorn, e.g. http://127.0.0.1:8100).",
      },
      { status: 503 },
    );
  }

  const tail = pathSegments.length ? `/${pathSegments.join("/")}` : "";
  const dest = new URL(req.url);
  const target = `${upstream}${tail}${dest.search}`;

  const headers = new Headers();
  const ct = req.headers.get("content-type");
  if (ct) headers.set("content-type", ct);
  const accept = req.headers.get("accept");
  if (accept) headers.set("accept", accept);

  const init: RequestInit = {
    method,
    headers,
    redirect: "manual",
  };
  if (method === "POST") {
    init.body = await req.arrayBuffer();
  }

  const res = await fetch(target, init);
  const out = new Headers();
  const ctOut = res.headers.get("content-type");
  if (ctOut) out.set("content-type", ctOut);
  return new NextResponse(await res.arrayBuffer(), { status: res.status, headers: out });
}

export async function GET(req: NextRequest, ctx: { params: Promise<{ path?: string[] }> }) {
  const { path } = await ctx.params;
  return proxy(req, path ?? [], "GET");
}

export async function POST(req: NextRequest, ctx: { params: Promise<{ path?: string[] }> }) {
  const { path } = await ctx.params;
  return proxy(req, path ?? [], "POST");
}
