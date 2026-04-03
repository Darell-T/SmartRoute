import { NextRequest, NextResponse } from "next/server";

const backendBase =
  process.env.API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export async function POST(req: NextRequest) {
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

  const body = await req.json();

  const response = await fetch(`${backendBase}/api/trip`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-App-Key": appKey,
    },
    body: JSON.stringify(body),
  });

  const data = await response.json();
  return NextResponse.json(data, { status: response.status });
}
