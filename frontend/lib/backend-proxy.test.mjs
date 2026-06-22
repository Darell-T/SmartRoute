import assert from "node:assert/strict";
import test from "node:test";

import {
  appendRequestSearch,
  fetchBackendText,
  readJsonBody,
} from "./backend-proxy-core.ts";

test("readJsonBody distinguishes malformed JSON from an empty body", async () => {
  const malformed = await readJsonBody(
    new Request("http://localhost/api/test", {
      method: "POST",
      body: "{",
      headers: { "content-type": "application/json" },
    }),
  );

  assert.deepEqual(malformed, { ok: false, empty: false, value: undefined });
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
