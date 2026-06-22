import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

import {
  fetchWsTicket,
  wsUrlWithTicket,
} from "./ws-ticket.ts";

const ROOT = path.resolve(import.meta.dirname, "..");

test("websocket URL uses server-provided backend base instead of stale public localhost config", async () => {
  const originalFetch = globalThis.fetch;
  const originalNextPublicApiUrl = process.env.NEXT_PUBLIC_API_URL;
  const originalWindowDescriptor = Object.getOwnPropertyDescriptor(globalThis, "window");

  process.env.NEXT_PUBLIC_API_URL = "http://localhost:8000";
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: {
      location: {
        hostname: "smart-route.example",
      },
    },
  });

  globalThis.fetch = async () =>
    new Response(
      JSON.stringify({
        ticket: "ticket-123",
        ws_base_url: "wss://api.smart-route.example",
      }),
      {
        status: 200,
        headers: { "content-type": "application/json" },
      },
    );

  try {
    const ticket = await fetchWsTicket("/ws/live-feed");
    assert.equal(ticket, "ticket-123");
    assert.equal(
      wsUrlWithTicket("/ws/live-feed", ticket),
      "wss://api.smart-route.example/ws/live-feed?ticket=ticket-123",
    );
  } finally {
    globalThis.fetch = originalFetch;
    if (originalNextPublicApiUrl === undefined) {
      delete process.env.NEXT_PUBLIC_API_URL;
    } else {
      process.env.NEXT_PUBLIC_API_URL = originalNextPublicApiUrl;
    }

    if (originalWindowDescriptor) {
      Object.defineProperty(globalThis, "window", originalWindowDescriptor);
    } else {
      delete globalThis.window;
    }
  }
});

test("server-side websocket ticket route exposes ws_base_url from API_URL", () => {
  const source = fs.readFileSync(path.join(ROOT, "app/api/ws-ticket/route.ts"), "utf8");

  assert.match(
    source,
    /process\.env\.API_URL/,
    "the websocket ticket route must read server-only API_URL, not require a NEXT_PUBLIC value",
  );
  assert.match(
    source,
    /ws_base_url/,
    "the websocket ticket response should include the backend websocket base URL",
  );
  assert.match(
    source,
    /isLocalBackendBase/,
    "the websocket ticket route should not emit a localhost backend URL from a deployed build",
  );
});
