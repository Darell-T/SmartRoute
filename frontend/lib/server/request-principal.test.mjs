import assert from "node:assert/strict";
import test from "node:test";
import { NextRequest } from "next/server";
import { requestPrincipal } from "./request-principal.ts";

function request(identity) {
  return new NextRequest("https://smartroute.fyi/api/trip", {
    headers: identity ? { "x-vercel-forwarded-for": identity } : {},
  });
}

function restore(name, value) {
  if (value === undefined) delete process.env[name];
  else process.env[name] = value;
}

test("principal is stable, keyed, and separates platform identities", () => {
  const previousVercel = process.env.VERCEL;
  const previousKey = process.env.APP_KEY;
  process.env.VERCEL = "1";
  process.env.APP_KEY = "principal-test-key";
  const first = requestPrincipal(request("203.0.113.1"));
  assert.equal(first, requestPrincipal(request("203.0.113.1")));
  assert.notEqual(first, requestPrincipal(request("203.0.113.2")));
  assert.ok(first.startsWith("v1."));
  assert.equal(first.includes("203.0.113.1"), false);
  restore("VERCEL", previousVercel);
  restore("APP_KEY", previousKey);
});

test("principal fails closed for missing, oversized, and missing-key production input", () => {
  const previousVercel = process.env.VERCEL;
  const previousKey = process.env.APP_KEY;
  process.env.VERCEL = "1";
  process.env.APP_KEY = "principal-test-key";
  assert.equal(requestPrincipal(request()), null);
  assert.equal(requestPrincipal(request("   ")), null);
  assert.equal(requestPrincipal(request("x".repeat(257))), null);
  delete process.env.APP_KEY;
  assert.equal(requestPrincipal(request("203.0.113.1")), null);
  restore("VERCEL", previousVercel);
  restore("APP_KEY", previousKey);
});
