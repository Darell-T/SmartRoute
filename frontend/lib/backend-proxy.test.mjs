import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  appendRequestSearch,
  fetchBackendText,
  readJsonBody,
  resolveBackendBaseUrl,
} from "./backend-proxy-core.ts";

const FRONTEND_ROOT = path.dirname(fileURLToPath(new URL("../package.json", import.meta.url)));

test("Next configuration rejects a public-prefixed application secret", () => {
  const publicSecret = "must-not-appear-in-output";
  const environment = { ...process.env, NEXT_PUBLIC_APP_KEY: publicSecret };
  const result = spawnSync(
    process.execPath,
    ["--input-type=module", "-e", "await import('./next.config.mjs')"],
    { cwd: FRONTEND_ROOT, encoding: "utf8", env: environment },
  );
  const output = `${result.stdout}${result.stderr}`;

  assert.notEqual(result.status, 0);
  assert.match(output, /APP_KEY must remain server-only/);
  assert.doesNotMatch(output, new RegExp(publicSecret));
});

test("Next configuration still loads when the application secret is server-only", () => {
  const environment = { ...process.env };
  delete environment.NEXT_PUBLIC_APP_KEY;
  const result = spawnSync(
    process.execPath,
    ["--input-type=module", "-e", "await import('./next.config.mjs')"],
    { cwd: FRONTEND_ROOT, encoding: "utf8", env: environment },
  );

  assert.equal(result.status, 0, result.stderr);
});

test("Vercel falls back from stale local or non-HTTP backend overrides", () => {
  const environments = [
    { NEXT_PUBLIC_API_URL: "http://localhost:8000" },
    { API_URL: "http://127.0.0.1:8000" },
    { API_URL: "http://[::1]:8000" },
    { API_URL: "ftp://backend.example" },
  ];

  for (const environment of environments) {
    assert.equal(
      resolveBackendBaseUrl({ ...environment, VERCEL: "1" }),
      "https://jarvis-mta-assistant.onrender.com",
      JSON.stringify(environment),
    );
  }
});

test("local development preserves the configured local backend", () => {
  assert.equal(
    resolveBackendBaseUrl({ NEXT_PUBLIC_API_URL: "http://localhost:8000" }),
    "http://localhost:8000",
  );
});

test("readJsonBody distinguishes malformed JSON from an empty body", async () => {
  const malformed = await readJsonBody(
    new Request("http://localhost/api/test", {
      method: "POST",
      body: "{",
      headers: { "content-type": "application/json" },
    }),
  );

  assert.deepEqual(malformed, { ok: false, tooLarge: false, empty: false, value: undefined });
});

test("readJsonBody marks missing or whitespace-only bodies as empty", async () => {
  assert.deepEqual(await readJsonBody(new Request("http://localhost/api/test")), {
    ok: true,
    empty: true,
    value: undefined,
  });
  assert.deepEqual(
    await readJsonBody(
      new Request("http://localhost/api/test", { method: "POST", body: "  \n" }),
    ),
    { ok: true, empty: true, value: undefined },
  );
});

test("readJsonBody accepts an ASCII JSON body at its exact byte ceiling", async () => {
  const acceptedBody = JSON.stringify({ value: "x".repeat(3) });
  const rejectedBody = JSON.stringify({ value: "x".repeat(4) });
  const limit = new TextEncoder().encode(acceptedBody).byteLength;
  const atLimit = new Request("https://example.test", { method: "POST", body: acceptedBody });
  const overLimit = new Request("https://example.test", { method: "POST", body: rejectedBody });
  const accepted = await readJsonBody(atLimit, limit);
  const rejected = await readJsonBody(overLimit, limit);
  assert.deepEqual(accepted, { ok: true, empty: false, value: { value: "x".repeat(3) } });
  assert.deepEqual(rejected, { ok: false, tooLarge: true, empty: false, value: undefined });
});

test("readJsonBody rejects a multibyte UTF-8 overflow", async () => {
  const acceptedBody = JSON.stringify({ value: "\u00e9".repeat(3) });
  const rejectedBody = JSON.stringify({ value: "\u00e9".repeat(4) });
  const limit = new TextEncoder().encode(acceptedBody).byteLength;

  const accepted = await readJsonBody(
    new Request("https://example.test", { method: "POST", body: acceptedBody }),
    limit,
  );
  const rejected = await readJsonBody(
    new Request("https://example.test", { method: "POST", body: rejectedBody }),
    limit,
  );

  assert.deepEqual(accepted, { ok: true, empty: false, value: { value: "\u00e9".repeat(3) } });
  assert.deepEqual(rejected, { ok: false, tooLarge: true, empty: false, value: undefined });
});

test("fetchBackendText keeps timeout active while consuming the upstream body", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (_url, init) =>
    new Response(
      new ReadableStream({
        start(controller) {
          let aborted = false;
          let timer;
          init?.signal?.addEventListener("abort", () => {
            aborted = true;
            clearTimeout(timer);
            controller.error(new DOMException("Aborted", "AbortError"));
          });
          timer = setTimeout(() => {
            if (aborted) return;
            controller.enqueue(new TextEncoder().encode(JSON.stringify({ late: true })));
            controller.close();
          }, 50);
        },
      }),
      { status: 200, headers: { "content-type": "application/json" } },
    );

  try {
    const result = await fetchBackendText("http://backend.test/api/slow-body", {
      method: "GET",
    }, 10);

    assert.deepEqual(result, { ok: false, aborted: true });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("appendRequestSearch forwards query parameters to the backend path", () => {
  const req = new Request("http://localhost/api/vehicles?route_ids=A&route_ids=C&limit=5");

  assert.equal(
    appendRequestSearch("/api/vehicles", req),
    "/api/vehicles?route_ids=A&route_ids=C&limit=5",
  );
});
