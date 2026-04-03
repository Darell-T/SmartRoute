import { NextResponse } from "next/server";

const backendBase =
  process.env.API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** Browsers default to GET; opening `/api/thinking` in a tab hits this (not POST). */
export async function GET() {
  return NextResponse.json(
    { error: "Use POST. This route proxies to the FastAPI POST /api/thinking endpoint." },
    { status: 405, headers: { Allow: "POST" } },
  );
}

/** Backend exposes POST /api/thinking (matches `getThinking()` in lib/api.ts). */
export async function POST() {
  const appKey = process.env.APP_KEY;
  if (!appKey) {
    return NextResponse.json(
      {
        error:
          "APP_KEY is not set for the Next.js server. Add APP_KEY in Vercel (or local .env) — it must match FastAPI APP_KEY.",
      },
      { status: 500 },
    );
  }

  const response = await fetch(`${backendBase}/api/thinking`, {
    method: "POST",
    headers: {
      "X-App-Key": appKey,
    },
  });

  const data = await response.json();
  return NextResponse.json(data, { status: response.status });
}
