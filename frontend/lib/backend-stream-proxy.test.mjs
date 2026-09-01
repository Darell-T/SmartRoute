import assert from "node:assert/strict";
import test from "node:test";

import {
  classifyProxyAbort,
  streamProxyToBackend,
} from "./backend-stream-proxy.ts";
import { safeChatFailure } from "./chat-failure-copy.ts";

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

async function withCapturedErrors(fn) {
  const original = console.error;
  const entries = [];
  console.error = (...args) => entries.push(args);
  try {
    return await fn(entries);
  } finally {
    console.error = original;
  }
}

test("missing APP_KEY is redacted and correlated without exposing configuration", async () => {
  await withCapturedErrors(async (entries) => {
    await withAppKey(undefined, async () => {
      const res = await streamProxyToBackend("/api/agent/chat", {
        message: "private prompt",
      });
      assert.equal(res.status, 500);
      assert.match(res.headers.get("x-smartroute-request-id"), /^[0-9a-f-]{36}$/);
      assert.deepEqual(await res.json(), {
        error: "SmartRoute couldn’t complete this request.",
        retryable: true,
      });
      const logged = JSON.stringify(entries);
      assert.match(logged, /agent_chat_proxy_failure/);
      assert.doesNotMatch(logged, /private prompt|APP_KEY/);
    });
  });
});

test("success injects server credentials and stays unbuffered", async () => {
  await withAppKey("test-key", async () => {
    const originalFetch = globalThis.fetch;
    let capturedUrl;
    let capturedInit;
    const chunks = ["event: meta\ndata: {}\n\n", "event: done\ndata: {}\n\n"];
    globalThis.fetch = async (url, init) => {
      capturedUrl = url;
      capturedInit = init;
      return new Response(
        new ReadableStream({
          start(controller) {
            for (const chunk of chunks) {
              controller.enqueue(new TextEncoder().encode(chunk));
            }
            controller.close();
          },
        }),
        { status: 200 },
      );
    };
    try {
      const res = await streamProxyToBackend("/api/agent/chat", { message: "hi" });
      assert.match(String(capturedUrl), /\/api\/agent\/chat$/);
      assert.equal(capturedInit.headers["X-App-Key"], "test-key");
      assert.match(capturedInit.headers["X-SmartRoute-Request-Id"], /^[0-9a-f-]{36}$/);
      assert.equal(capturedInit.headers.Accept, "text/event-stream");
      assert.equal(capturedInit.method, "POST");
      assert.equal(capturedInit.cache, "no-store");
      assert.equal(res.headers.get("Content-Type"), "text/event-stream");
      assert.equal(res.headers.get("Cache-Control"), "no-cache, no-transform");
      assert.equal(res.headers.get("X-Accel-Buffering"), "no");
      assert.equal(
        res.headers.get("x-smartroute-request-id"),
        capturedInit.headers["X-SmartRoute-Request-Id"],
      );
      assert.equal(await res.text(), chunks.join(""));
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});

test("connection failures are classified, redacted, correlated, and safely logged", async () => {
  await withCapturedErrors(async (entries) => {
    await withAppKey("test-key", async () => {
      const originalFetch = globalThis.fetch;
      globalThis.fetch = async () => {
        throw new TypeError("fetch failed with secret-token");
      };
      try {
        const res = await streamProxyToBackend("/api/agent/chat", {
          message: "private prompt",
        });
        assert.equal(res.status, 502);
        assert.deepEqual(await res.json(), {
          error: "SmartRoute is temporarily unavailable.",
          retryable: true,
        });
        const logged = JSON.stringify(entries);
        const details = JSON.parse(entries[0][1]);
        assert.equal(details.failurePhase, "connect");
        assert.equal(details.abortSource, "unknown");
        assert.doesNotMatch(logged, /private prompt|secret-token|test-key/);
      } finally {
        globalThis.fetch = originalFetch;
      }
    });
  });
});

test("upstream statuses retain status while raw response bodies stay private", async () => {
  const statuses = [400, 401, 403, 404, 429, 500, 502, 503];
  await withCapturedErrors(async () => {
    await withAppKey("test-key", async () => {
      const originalFetch = globalThis.fetch;
      try {
        for (const status of statuses) {
          globalThis.fetch = async () =>
            new Response(JSON.stringify({ detail: `secret-${status}` }), { status });
          const res = await streamProxyToBackend("/api/agent/chat", { message: "hi" });
          assert.equal(res.status, status);
          assert.equal(res.headers.has("x-smartroute-request-id"), true);
          const payload = await res.json();
          assert.deepEqual(payload, {
            error: safeChatFailure(status).message,
            retryable: safeChatFailure(status).retryable,
          });
          assert.doesNotMatch(JSON.stringify(payload), new RegExp(`secret-${status}`));
        }
        globalThis.fetch = async () => new Response(null, { status: 502 });
        const empty = await streamProxyToBackend(
          "/api/agent/chat",
          { message: "hi" },
          new AbortController().signal,
        );
        assert.equal(empty.status, 502);
      } finally {
        globalThis.fetch = originalFetch;
      }
    });
  });
});

test("a body failure after headers is logged as a stream failure", async () => {
  await withCapturedErrors(async (entries) => {
    await withAppKey("test-key", async () => {
      const originalFetch = globalThis.fetch;
      globalThis.fetch = async () =>
        new Response(
          new ReadableStream({
            start(controller) {
              controller.enqueue(new TextEncoder().encode("event: meta\n"));
              controller.error(new Error("private provider detail"));
            },
          }),
          { status: 200 },
        );
      try {
        const res = await streamProxyToBackend("/api/agent/chat", {
          message: "private prompt",
        });
        await assert.rejects(() => res.text(), /SmartRoute stream ended unexpectedly/);
        const details = JSON.parse(entries[0][1]);
        assert.equal(details.failurePhase, "stream");
        const logged = JSON.stringify(entries);
        assert.doesNotMatch(logged, /private prompt|private provider detail/);
      } finally {
        globalThis.fetch = originalFetch;
      }
    });
  });
});

test("client abort reaches the upstream request and is classified separately", async () => {
  await withCapturedErrors(async (entries) => {
    await withAppKey("test-key", async () => {
      const originalFetch = globalThis.fetch;
      let upstreamAborted = false;
      globalThis.fetch = (_url, init) =>
        new Promise((_resolve, reject) => {
          init.signal.addEventListener("abort", () => {
            upstreamAborted = true;
            reject(new DOMException("Aborted", "AbortError"));
          });
        });
      try {
        const incoming = new AbortController();
        const pending = streamProxyToBackend(
          "/api/agent/chat",
          { message: "hi" },
          incoming.signal,
        );
        incoming.abort();
        const res = await pending;
        assert.equal(upstreamAborted, true);
        assert.equal(res.status, 502);
        assert.equal(JSON.parse(entries[0][1]).abortSource, "client");
      } finally {
        globalThis.fetch = originalFetch;
      }
    });
  });
});

test("connect timeout, client abort, and unknown aborts have distinct labels", () => {
  assert.equal(classifyProxyAbort(true, false), "connect_timeout");
  assert.equal(classifyProxyAbort(false, true), "client");
  assert.equal(classifyProxyAbort(false, false), "unknown");
});

test("an already-aborted client signal fails connect without waiting for upstream", async () => {
  await withCapturedErrors(async (entries) => {
    await withAppKey("test-key", async () => {
      const originalFetch = globalThis.fetch;
      globalThis.fetch = async (_url, init) => {
        if (init.signal?.aborted) {
          throw new DOMException("Aborted", "AbortError");
        }
        throw new Error("should not fetch");
      };
      try {
        const incoming = new AbortController();
        incoming.abort();
        const res = await streamProxyToBackend(
          "/api/agent/chat",
          { message: "hi" },
          incoming.signal,
        );
        assert.equal(res.status, 502);
        assert.equal(JSON.parse(entries[0][1]).abortSource, "client");
      } finally {
        globalThis.fetch = originalFetch;
      }
    });
  });
});

test("a successful upstream with no body still returns an SSE response", async () => {
  await withAppKey("test-key", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async () => new Response(null, { status: 200 });
    try {
      const res = await streamProxyToBackend("/api/agent/chat", { message: "hi" });
      assert.equal(res.status, 200);
      assert.equal(res.headers.get("content-type"), "text/event-stream");
      assert.equal(res.body, null);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});

test("a non-error upstream status is mapped to 502 at the proxy boundary", async () => {
  await withCapturedErrors(async () => {
    await withAppKey("test-key", async () => {
      const originalFetch = globalThis.fetch;
      globalThis.fetch = async () => ({
        ok: false,
        status: 399,
        body: { cancel: async () => undefined },
      });
      try {
        const res = await streamProxyToBackend("/api/agent/chat", { message: "hi" });
        assert.equal(res.status, 502);
      } finally {
        globalThis.fetch = originalFetch;
      }
    });
  });
});

test("a stream read failure after the client aborted is classified as client", async () => {
  await withCapturedErrors(async (entries) => {
    await withAppKey("test-key", async () => {
      const originalFetch = globalThis.fetch;
      const incoming = new AbortController();
      globalThis.fetch = async () =>
        new Response(
          new ReadableStream({
            start(controller) {
              incoming.abort();
              controller.error(new Error("socket reset"));
            },
          }),
          { status: 200 },
        );
      try {
        const res = await streamProxyToBackend(
          "/api/agent/chat",
          { message: "hi" },
          incoming.signal,
        );
        await assert.rejects(() => res.text(), /SmartRoute stream ended unexpectedly/);
        assert.equal(JSON.parse(entries[0][1]).abortSource, "client");
      } finally {
        globalThis.fetch = originalFetch;
      }
    });
  });
});

test("stream proxy treats a non-URL API_URL as an invalid backend host", async () => {
  const previousApi = process.env.API_URL;
  const previousPublic = process.env.NEXT_PUBLIC_API_URL;
  const previousVercel = process.env.VERCEL;
  process.env.API_URL = "not a url";
  delete process.env.NEXT_PUBLIC_API_URL;
  delete process.env.VERCEL;
  try {
    const loaded = await import(`./backend-stream-proxy.ts?invalid-host=1`);
    assert.equal(typeof loaded.streamProxyToBackend, "function");
  } finally {
    if (previousApi === undefined) delete process.env.API_URL;
    else process.env.API_URL = previousApi;
    if (previousPublic === undefined) delete process.env.NEXT_PUBLIC_API_URL;
    else process.env.NEXT_PUBLIC_API_URL = previousPublic;
    if (previousVercel === undefined) delete process.env.VERCEL;
    else process.env.VERCEL = previousVercel;
  }
});

test("stream proxy labels a file URL backend as an invalid host", async () => {
  const previousApi = process.env.API_URL;
  const previousPublic = process.env.NEXT_PUBLIC_API_URL;
  const previousVercel = process.env.VERCEL;
  process.env.API_URL = "file:///tmp";
  delete process.env.NEXT_PUBLIC_API_URL;
  delete process.env.VERCEL;
  try {
    const loaded = await import(`./backend-stream-proxy.ts?file-host=1`);
    assert.equal(typeof loaded.streamProxyToBackend, "function");
  } finally {
    if (previousApi === undefined) delete process.env.API_URL;
    else process.env.API_URL = previousApi;
    if (previousPublic === undefined) delete process.env.NEXT_PUBLIC_API_URL;
    else process.env.NEXT_PUBLIC_API_URL = previousPublic;
    if (previousVercel === undefined) delete process.env.VERCEL;
    else process.env.VERCEL = previousVercel;
  }
});
