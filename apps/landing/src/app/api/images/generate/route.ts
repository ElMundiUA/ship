import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";

const bucket = new Map<string, number[]>();

function rateLimit(ip: string, limit = 10, windowMs = 60_000): boolean {
  const now = Date.now();
  const stamps = bucket.get(ip) ?? [];
  const fresh = stamps.filter((t) => now - t < windowMs);
  if (fresh.length >= limit) return false;
  fresh.push(now);
  bucket.set(ip, fresh);
  return true;
}

function clientIp(req: NextRequest): string {
  return (
    req.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ??
    req.headers.get("x-real-ip") ??
    "local"
  );
}

type TogetherImageResponse = {
  data?: Array<{ url?: string; b64_json?: string }>;
  error?: { message?: string };
};

export async function POST(req: NextRequest) {
  const ip = clientIp(req);
  if (!rateLimit(ip)) {
    return NextResponse.json({ error: "Too many requests. Try again in a minute." }, { status: 429 });
  }

  const apiKey = process.env.TOGETHER_API_KEY;
  if (!apiKey) {
    return NextResponse.json(
      { error: "TOGETHER_API_KEY is not configured on the server." },
      { status: 503 },
    );
  }

  let body: { prompt?: string; width?: number; height?: number };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body." }, { status: 400 });
  }

  const prompt = (body.prompt ?? "").trim();
  if (prompt.length < 8 || prompt.length > 2000) {
    return NextResponse.json({ error: "Prompt must be between 8 and 2000 characters." }, { status: 400 });
  }

  const width = Math.min(Math.max(body.width ?? 1024, 512), 1344);
  const height = Math.min(Math.max(body.height ?? 640, 512), 1344);
  const model =
    process.env.TOGETHER_IMAGE_MODEL ?? "black-forest-labs/FLUX.1-schnell";

  const res = await fetch("https://api.together.xyz/v1/images/generations", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model,
      prompt,
      width,
      height,
      steps: model.includes("schnell") ? 4 : 16,
      n: 1,
      response_format: "url",
    }),
  });

  const raw = await res.text();
  let json: TogetherImageResponse;
  try {
    json = JSON.parse(raw) as TogetherImageResponse;
  } catch {
    return NextResponse.json({ error: "Upstream returned non-JSON." }, { status: 502 });
  }

  if (!res.ok) {
    const msg = json.error?.message ?? raw.slice(0, 200);
    return NextResponse.json({ error: msg || "Together image request failed." }, { status: 502 });
  }

  const url = json.data?.[0]?.url;
  if (!url) {
    return NextResponse.json({ error: "No image URL in Together response." }, { status: 502 });
  }

  return NextResponse.json({ url });
}
