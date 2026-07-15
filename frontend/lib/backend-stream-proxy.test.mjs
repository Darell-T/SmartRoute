import assert from "node:assert/strict";
import test from "node:test";

import { streamProxyToBackend } from "./backend-stream-proxy.ts";

function withAppKey(value, fn) {
  const original = process.env.APP_KEY;
  if (value === undefined) delete process.env.APP_KEY;
  else process.env.APP_KEY = value;
  return Promise.resolve()
    .then(fn)
    .finally(() => {
      if (original === undefined) delete process.env.APP_KEY;
      else process.env.APP_KEY = original;
    });
}

test("streamProxyToBackend returns a redacted 500 when APP_KEY is missing", async () => {
  await withAppKey(undefined, async () => {
    const res = await streamProxyToBackend("/api/agent/chat", { message: "hi" });
    assert.equal(res.status, 500);
    const payload = await res.json();
    assert.deepEqual(payload, { error: "Server is not configured (missing APP_KEY)." });
  });
});

test("streamProxyToBackend injects X-App-Key and requests text/event-stream", async () => {
  await withAppKey("test-key", async () => {
    const originalFetch = globalThis.fetch;
    let capturedUrl;
    let capturedInit;
    globalThis.fetch = async (url, init) => {
      capturedUrl = url;
      capturedInit = init;
      return new Response(new ReadableStream({ start(c) { c.close(); } }), { status: 200 });
    };
    try {
      await streamProxyToBackend("/api/agent/chat", { message: "hi" });
      assert.match(String(capturedUrl), /\/api\/agent\/chat$/);
      assert.equal(capturedInit.headers["X-App-Key"], "test-key");
      assert.equal(capturedInit.headers["Accept"], "text/event-stream");
      assert.equal(capturedInit.headers["Content-Type"], "application/json");
      assert.equal(capturedInit.method, "POST");
      assert.equal(capturedInit.cache, "no-store");
      assert.equal(capturedInit.body, JSON.stringify({ message: "hi" }));
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});

test("streamProxyToBackend pipes the upstream stream through with SSE headers", async () => {
  await withAppKey("test-key", async () => {
    const originalFetch = globalThis.fetch;
    const chunks = ["event: meta\ndata: {}\n\n", "event: done\ndata: {}\n\n"];
    globalThis.fetch = async () =>
      new Response(
        new ReadableStream({
          start(controller) {
            for (const chunk of chunks) controller.enqueue(new TextEncoder().encode(chunk));
            controller.close();
          },
        }),
        { status: 200 },
      );
    try {
      const res = await streamProxyToBackend("/api/agent/chat", { message: "hi" });
      assert.equal(res.status, 200);
      assert.equal(res.headers.get("Content-Type"), "text/event-stream");
      assert.equal(res.headers.get("Cache-Control"), "no-cache, no-transform");
      assert.equal(res.headers.get("X-Accel-Buffering"), "no");

      const text = await res.text();
      assert.equal(text, chunks.join(""));
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});

test("streamProxyToBackend returns a redacted 502 on upstream connection failure", async () => {
  await withAppKey("test-key", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async () => {
      throw new TypeError("fetch failed");
    };
    try {
      const res = await streamProxyToBackend("/api/agent/chat", { message: "hi" });
      assert.equal(res.status, 502);
      const payload = await res.json();
      assert.deepEqual(payload, { error: "Upstream request failed." });
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});

test("streamProxyToBackend redacts an upstream non-2xx body instead of forwarding it", async () => {
  await withAppKey("test-key", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async () =>
      new Response(JSON.stringify({ detail: "internal secret trace" }), {
        status: 429,
        headers: { "content-type": "application/json" },
      });
    try {
      const res = await streamProxyToBackend("/api/agent/chat", { message: "hi" });
      assert.equal(res.status, 429);
      const payload = await res.json();
      assert.deepEqual(payload, { error: "Upstream request failed." });
      assert.ok(!JSON.stringify(payload).includes("internal secret trace"));
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});

test("streamProxyToBackend aborts the upstream fetch when the incoming signal aborts", async () => {
  await withAppKey("test-key", async () => {
    const originalFetch = globalThis.fetch;
    let sawSignal;
    let upstreamAborted = false;
    globalThis.fetch = (url, init) => {
      sawSignal = init.signal;
      return new Promise((resolve, reject) => {
        init.signal.addEventListener("abort", () => {
          upstreamAborted = true;
          reject(new DOMException("Aborted", "AbortError"));
        });
      });
    };
    try {
      const incoming = new AbortController();
      const pending = streamProxyToBackend("/api/agent/chat", { message: "hi" }, incoming.signal);
      incoming.abort();
      const res = await pending;
      assert.ok(sawSignal, "fetch should have received a signal");
      assert.equal(upstreamAborted, true);
      assert.equal(res.status, 502);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
