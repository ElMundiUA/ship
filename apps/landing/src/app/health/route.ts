import { NextResponse } from "next/server";

/** Bunny Magic Containers HTTP probes (see scripts/bunny-ship-docs.mjs). */
export async function GET() {
  return NextResponse.json({ ok: true }, { status: 200 });
}
