import { createHmac } from "node:crypto";
import type { NextRequest } from "next/server";

/**
 * Derives an opaque, bounded principal at the trusted hosting boundary.
 *
 * Vercel supplies `x-vercel-forwarded-for`; unlike browser-controlled
 * forwarding headers it is set by the platform before the route executes.
 * Raw addresses never leave this module or reach FastAPI. Local development
 * intentionally uses one bounded principal because it is not an abuse boundary.
 */
export function requestPrincipal(request: NextRequest): string | null {
  const source = process.env.VERCEL
    ? request.headers.get("x-vercel-forwarded-for")
    : "local-development";
  if (!source || !source.trim() || source.length > 256) return null;
  const appKey = process.env.APP_KEY;
  if (!appKey) return null;
  const digest = createHmac("sha256", appKey).update(source.trim()).digest("base64url");
  return `v1.${digest.slice(0, 43)}`;
}
