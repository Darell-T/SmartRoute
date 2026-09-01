import assert from "node:assert/strict";
import test from "node:test";

import { NextRequest } from "next/server";

import { POST as postChat } from "./agent/chat/route.ts";
import { POST as postSession } from "./agent/chat/session/route.ts";
import { POST as postSessionReset } from "./agent/chat/session/reset/route.ts";
import { POST as postLiveFeed } from "./live-feed/route.ts";
import { GET as getServiceAlerts } from "./service-alerts/route.ts";
import { GET as getSubwayStops } from "./subway-stops/route.ts";
import { POST as postTrip } from "./trip/route.ts";
import { GET as getEnrichRoute, POST as postEnrichRoute } from "./trip/enrich-route/route.ts";
import { GET as getVehicles } from "./vehicles/route.ts";
import { GET as getWsTicket } from "./ws-ticket/route.ts";

function jsonRequest(url, body, extraHeaders = {}) {
  return new NextRequest(url, {
    method: "POST",
    headers: { "content-type": "application/json", ...extraHeaders },
    body: JSON.stringify(body),
  });
}

function getRequest(url) {
  return new NextRequest(url, { method: "GET" });
}

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

function mockFetchOk(payload = { ok: true }) {
  const original = globalThis.fetch;
  globalThis.fetch = async () =>
    new Response(JSON.stringify(payload), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  return () => {
    globalThis.fetch = original;
  };
}

test("trip planning rejects malformed JSON through the existing 400 path", async () => {
  await withAppKey("test-key", async () => {
    const res = await postTrip(jsonRequest("http://localhost/api/trip", { destination: "JFK" }));
    assert.equal(res.status, 400);
    assert.deepEqual(await res.json(), { error: "Invalid trip request." });
  });
});

test("trip planning forwards a valid request and returns upstream JSON", async () => {
  await withAppKey("test-key", async () => {
    const restore = mockFetchOk({ recommendation: "ok", route: [], alerts: [] });
    try {
      const res = await postTrip(
        jsonRequest("http://localhost/api/trip", {
          origin_lat: 40.75,
          origin_lng: -73.99,
          destination: "JFK",
        }),
      );
      assert.equal(res.status, 200);
      assert.deepEqual(await res.json(), { recommendation: "ok", route: [], alerts: [] });
    } finally {
      restore();
    }
  });
});

test("missing APP_KEY is a redacted 500 on GET proxies", async () => {
  await withAppKey(undefined, async () => {
    const res = await getServiceAlerts(getRequest("http://localhost/api/service-alerts"));
    assert.equal(res.status, 500);
    assert.deepEqual(await res.json(), { error: "Server is not configured (missing APP_KEY)." });
  });
});

test("GET vehicles forwards the route_ids query and disables store cache", async () => {
  await withAppKey("test-key", async () => {
    const calls = [];
    const original = globalThis.fetch;
    globalThis.fetch = async (url, init) => {
      calls.push({ url: String(url), cache: init?.cache });
      return new Response(JSON.stringify({ vehicles: [] }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    };
    try {
      const res = await getVehicles(getRequest("http://localhost/api/vehicles?route_ids=Q"));
      assert.equal(res.status, 200);
      assert.deepEqual(await res.json(), { vehicles: [] });
      assert.equal(calls.length, 1);
      assert.match(calls[0].url, /\/api\/vehicles\?route_ids=Q/);
      assert.equal(calls[0].cache, "no-store");
    } finally {
      globalThis.fetch = original;
    }
  });
});

test("GET subway-stops asks the edge cache to revalidate hourly", async () => {
  await withAppKey("test-key", async () => {
    const calls = [];
    const original = globalThis.fetch;
    globalThis.fetch = async (url, init) => {
      calls.push({ url: String(url), next: init?.next });
      return new Response(JSON.stringify({ stops: [] }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    };
    try {
      const res = await getSubwayStops();
      assert.equal(res.status, 200);
      assert.deepEqual(calls[0].next, { revalidate: 3600 });
    } finally {
      globalThis.fetch = original;
    }
  });
});

test("live-feed and session restore reject unpaired or empty bodies", async () => {
  await withAppKey("test-key", async () => {
    const live = await postLiveFeed(jsonRequest("http://localhost/api/live-feed", { lat: 40.75 }));
    assert.equal(live.status, 400);
    const session = await postSession(jsonRequest("http://localhost/api/agent/chat/session", {}));
    assert.equal(session.status, 400);
    assert.deepEqual(await session.json(), { error: "Invalid session restore request." });
    const reset = await postSessionReset(
      jsonRequest("http://localhost/api/agent/chat/session/reset", {}),
    );
    assert.equal(reset.status, 400);
  });
});

test("enrich-route GET stays a 405 and POST validates steps", async () => {
  await withAppKey("test-key", async () => {
    const get = await getEnrichRoute();
    assert.equal(get.status, 405);
    assert.equal(get.headers.get("Allow"), "POST");
    const bad = await postEnrichRoute(
      jsonRequest("http://localhost/api/trip/enrich-route", { steps: [{ type: "WALK", unexpected: true }] }),
    );
    assert.equal(bad.status, 400);
  });
});

test("agent chat rejects an empty message and streams a valid one", async () => {
  await withAppKey("test-key", async () => {
    const invalid = await postChat(jsonRequest("http://localhost/api/agent/chat", { message: "" }));
    assert.equal(invalid.status, 400);
    const restore = mockFetchOk();
    globalThis.fetch = async () =>
      new Response("event: done\ndata: {}\n\n", {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      });
    try {
      const res = await postChat(
        jsonRequest("http://localhost/api/agent/chat", { message: "Next Q?" }),
      );
      assert.equal(res.status, 200);
      assert.equal(res.headers.get("content-type"), "text/event-stream");
    } finally {
      restore();
    }
  });
});

test("ws-ticket mints a path-bound ticket and rejects an unknown path", async () => {
  await withAppKey("test-key", async () => {
    const bad = await getWsTicket(getRequest("http://localhost/api/ws-ticket?path=/ws/other"));
    assert.equal(bad.status, 400);
    const ok = await getWsTicket(getRequest("http://localhost/api/ws-ticket?path=/ws/live-feed"));
    assert.equal(ok.status, 200);
    const body = await ok.json();
    assert.equal(typeof body.ticket, "string");
    assert.match(body.ws_base_url, /^wss?:\/\//);
  });
});

test("POST proxies reject an oversized body before it reaches the backend", async () => {
  await withAppKey("test-key", async () => {
    const req = new NextRequest("http://localhost/api/trip", {
      method: "POST",
      headers: { "content-type": "application/json", "content-length": "999999" },
      body: "{}",
    });
    const res = await postTrip(req);
    assert.equal(res.status, 413);
    assert.deepEqual(await res.json(), { error: "Request body is too large." });
  });
});

test("agent chat reports a connect failure when upstream fetch throws", async () => {
  await withAppKey("test-key", async () => {
    const original = globalThis.fetch;
    globalThis.fetch = async () => {
      throw new Error("offline");
    };
    try {
      const res = await postChat(jsonRequest("http://localhost/api/agent/chat", { message: "Next Q?" }));
      assert.equal(res.status, 502);
      const body = await res.json();
      assert.equal(typeof body.error, "string");
    } finally {
      globalThis.fetch = original;
    }
  });
});

test("GET proxies surface an upstream timeout as 504", async () => {
  await withAppKey("test-key", async () => {
    const original = globalThis.fetch;
    globalThis.fetch = async () => {
      const error = new Error("aborted");
      error.name = "AbortError";
      throw error;
    };
    try {
      const res = await getServiceAlerts(getRequest("http://localhost/api/service-alerts"));
      assert.equal(res.status, 504);
      assert.deepEqual(await res.json(), { error: "Upstream request timed out." });
    } finally {
      globalThis.fetch = original;
    }
  });
});

test("GET proxies redact a non-JSON upstream body", async () => {
  await withAppKey("test-key", async () => {
    const original = globalThis.fetch;
    globalThis.fetch = async () => new Response("Internal Server Error", { status: 500 });
    try {
      const res = await getServiceAlerts(getRequest("http://localhost/api/service-alerts"));
      assert.equal(res.status, 500);
      assert.deepEqual(await res.json(), { error: "Upstream returned an unexpected response." });
    } finally {
      globalThis.fetch = original;
    }
  });
});

test("Vercel requests without a platform identity stay 503", async () => {
  const previous = process.env.VERCEL;
  process.env.VERCEL = "1";
  try {
    await withAppKey("test-key", async () => {
      const ticket = await getWsTicket(
        new NextRequest("http://localhost/api/ws-ticket?path=/ws/live-feed", {
          headers: { "x-vercel-forwarded-for": "   " },
        }),
      );
      assert.equal(ticket.status, 503);
      const chat = await postChat(
        new NextRequest("http://localhost/api/agent/chat", {
          method: "POST",
          headers: {
            "content-type": "application/json",
            "x-vercel-forwarded-for": "   ",
          },
          body: JSON.stringify({ message: "Next Q?" }),
        }),
      );
      assert.equal(chat.status, 503);
      const trip = await postTrip(
        new NextRequest("http://localhost/api/trip", {
          method: "POST",
          headers: {
            "content-type": "application/json",
            "x-vercel-forwarded-for": "   ",
          },
          body: JSON.stringify({
            origin_lat: 40.75,
            origin_lng: -73.99,
            destination: "JFK",
          }),
        }),
      );
      assert.equal(trip.status, 503);
    });
  } finally {
    if (previous === undefined) delete process.env.VERCEL;
    else process.env.VERCEL = previous;
  }
});

test("deployed ticket minting ignores a stale localhost API_URL", async () => {
  const previousVercel = process.env.VERCEL;
  const previousApi = process.env.API_URL;
  process.env.VERCEL = "1";
  process.env.API_URL = "http://localhost:8000";
  try {
    await withAppKey("test-key", async () => {
      const req = new NextRequest("http://localhost/api/ws-ticket?path=/ws/live-feed", {
        headers: { "x-vercel-forwarded-for": "203.0.113.10" },
      });
      const res = await getWsTicket(req);
      assert.equal(res.status, 200);
      const body = await res.json();
      assert.match(body.ws_base_url, /wss:\/\/jarvis-mta-assistant\.onrender\.com/);
    });
  } finally {
    if (previousVercel === undefined) delete process.env.VERCEL;
    else process.env.VERCEL = previousVercel;
    if (previousApi === undefined) delete process.env.API_URL;
    else process.env.API_URL = previousApi;
  }
});

test("agent chat rejects malformed JSON and accepts a null origin", async () => {
  await withAppKey("test-key", async () => {
    const malformed = await postChat(
      new NextRequest("http://localhost/api/agent/chat", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: "{",
      }),
    );
    assert.equal(malformed.status, 400);
    const restore = mockFetchOk();
    globalThis.fetch = async () =>
      new Response("event: done\ndata: {}\n\n", {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      });
    try {
      const res = await postChat(
        jsonRequest("http://localhost/api/agent/chat", {
          message: "Next Q?",
          session_id: null,
          origin: null,
          selected_card_id: null,
        }),
      );
      assert.equal(res.status, 200);
    } finally {
      restore();
    }
  });
});

test("trip planning rejects unpaired destination coordinates", async () => {
  await withAppKey("test-key", async () => {
    const res = await postTrip(
      jsonRequest("http://localhost/api/trip", {
        origin_lat: 40.75,
        origin_lng: -73.99,
        destination: "JFK",
        destination_lat: 40.64,
      }),
    );
    assert.equal(res.status, 400);
    assert.deepEqual(await res.json(), { error: "Invalid trip request." });
  });
});

test("enrich-route accepts an omitted steps array as empty", async () => {
  await withAppKey("test-key", async () => {
    const restore = mockFetchOk({ steps: [], enriched: false });
    try {
      const res = await postEnrichRoute(jsonRequest("http://localhost/api/trip/enrich-route", {}));
      assert.equal(res.status, 200);
      assert.deepEqual(await res.json(), { steps: [], enriched: false });
    } finally {
      restore();
    }
  });
});

test("live-feed uses the default invalid-request copy", async () => {
  await withAppKey("test-key", async () => {
    const res = await postLiveFeed(jsonRequest("http://localhost/api/live-feed", {}));
    assert.equal(res.status, 400);
    assert.deepEqual(await res.json(), { error: "Invalid request." });
  });
});

test("agent chat reports field-level errors in development", async () => {
  const previous = process.env.NODE_ENV;
  process.env.NODE_ENV = "development";
  try {
    await withAppKey("test-key", async () => {
      const res = await postChat(jsonRequest("http://localhost/api/agent/chat", { message: "" }));
      assert.equal(res.status, 400);
      const body = await res.json();
      assert.match(body.error, /Invalid chat request:/);
    });
  } finally {
    process.env.NODE_ENV = previous;
  }
});

test("GET proxies preserve an empty 204 and map unknown throw to 502", async () => {
  await withAppKey("test-key", async () => {
    const original = globalThis.fetch;
    globalThis.fetch = async () => new Response(null, { status: 204 });
    try {
      const empty = await getVehicles(getRequest("http://localhost/api/vehicles?route_ids=Q"));
      assert.equal(empty.status, 204);
    } finally {
      globalThis.fetch = original;
    }
    globalThis.fetch = async () => {
      throw new Error("socket hang up");
    };
    try {
      const failed = await getVehicles(getRequest("http://localhost/api/vehicles?route_ids=Q"));
      assert.equal(failed.status, 502);
      assert.deepEqual(await failed.json(), { error: "Upstream request failed." });
    } finally {
      globalThis.fetch = original;
    }
  });
});

test("GET proxies redact a 200 body that is not JSON", async () => {
  await withAppKey("test-key", async () => {
    const original = globalThis.fetch;
    globalThis.fetch = async () => new Response("not-json", { status: 200 });
    try {
      const res = await getVehicles(getRequest("http://localhost/api/vehicles?route_ids=Q"));
      assert.equal(res.status, 502);
      assert.deepEqual(await res.json(), { error: "Upstream returned an unexpected response." });
    } finally {
      globalThis.fetch = original;
    }
  });
});

test("agent chat rejects an empty body as an invalid request", async () => {
  await withAppKey("test-key", async () => {
    const res = await postChat(
      new NextRequest("http://localhost/api/agent/chat", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: "",
      }),
    );
    assert.equal(res.status, 400);
  });
});

test("agent chat rejects an oversized body as 413", async () => {
  await withAppKey("test-key", async () => {
    const req = new NextRequest("http://localhost/api/agent/chat", {
      method: "POST",
      headers: { "content-type": "application/json", "content-length": "999999" },
      body: "{}",
    });
    const res = await postChat(req);
    assert.equal(res.status, 413);
    assert.deepEqual(await res.json(), { error: "Request body is too large." });
  });
});

test("agent chat rate-limits a single client after the window is exhausted", async () => {
  await withAppKey("test-key", async () => {
    const restore = mockFetchOk();
    globalThis.fetch = async () =>
      new Response("event: done\ndata: {}\n\n", {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      });
    try {
      let last;
      for (let i = 0; i < 11; i += 1) {
        last = await postChat(
          new NextRequest("http://localhost/api/agent/chat", {
            method: "POST",
            headers: {
              "content-type": "application/json",
              "x-forwarded-for": "203.0.113.88",
            },
            body: JSON.stringify({ message: "Next Q?" }),
          }),
        );
      }
      assert.equal(last.status, 429);
      assert.deepEqual(await last.json(), { error: "Too many requests. Please slow down." });
    } finally {
      restore();
    }
  });
});

test("an already-aborted chat request fails at the stream proxy boundary", async () => {
  await withAppKey("test-key", async () => {
    const controller = new AbortController();
    controller.abort();
    const res = await postChat(
      new NextRequest("http://localhost/api/agent/chat", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ message: "Next Q?" }),
        signal: controller.signal,
      }),
    );
    assert.equal(res.status, 502);
  });
});

test("ws-ticket rate-limits a single client after the window is exhausted", async () => {
  await withAppKey("test-key", async () => {
    let last;
    for (let i = 0; i < 241; i += 1) {
      last = await getWsTicket(
        new NextRequest("http://localhost/api/ws-ticket?path=/ws/live-feed", {
          headers: { "x-forwarded-for": "203.0.113.92" },
        }),
      );
    }
    assert.equal(last.status, 429);
  });
});

test("ws-ticket rejects a missing path", async () => {
  await withAppKey("test-key", async () => {
    const res = await getWsTicket(getRequest("http://localhost/api/ws-ticket"));
    assert.equal(res.status, 400);
  });
});

test("ws-ticket treats an invalid API_URL hostname as non-local", async () => {
  const previousApi = process.env.API_URL;
  const previousVercel = process.env.VERCEL;
  process.env.API_URL = "not a url";
  process.env.VERCEL = "1";
  try {
    await withAppKey("test-key", async () => {
      const req = new NextRequest("http://localhost/api/ws-ticket?path=/ws/live-feed", {
        headers: { "x-vercel-forwarded-for": "203.0.113.10" },
      });
      const res = await getWsTicket(req);
      assert.equal(res.status, 200);
    });
  } finally {
    if (previousApi === undefined) delete process.env.API_URL;
    else process.env.API_URL = previousApi;
    if (previousVercel === undefined) delete process.env.VERCEL;
    else process.env.VERCEL = previousVercel;
  }
});

test("GET service-alerts rate-limits a single client after the window is exhausted", async () => {
  await withAppKey("test-key", async () => {
    const restore = mockFetchOk({ alerts: [] });
    try {
      let last;
      for (let i = 0; i < 121; i += 1) {
        last = await getServiceAlerts(
          new NextRequest("http://localhost/api/service-alerts", {
            headers: { "x-forwarded-for": "203.0.113.90" },
          }),
        );
      }
      assert.equal(last.status, 429);
    } finally {
      restore();
    }
  });
});

test("GET vehicles rate-limits a single client after the window is exhausted", async () => {
  await withAppKey("test-key", async () => {
    const restore = mockFetchOk({ vehicles: [] });
    try {
      let last;
      for (let i = 0; i < 121; i += 1) {
        last = await getVehicles(
          new NextRequest("http://localhost/api/vehicles?route_ids=Q", {
            headers: { "x-forwarded-for": "203.0.113.91" },
          }),
        );
      }
      assert.equal(last.status, 429);
    } finally {
      restore();
    }
  });
});

test("trip planning rate-limits a single client after the window is exhausted", async () => {
  await withAppKey("test-key", async () => {
    const restore = mockFetchOk({ recommendation: "ok", route: [], alerts: [] });
    try {
      let last;
      for (let i = 0; i < 21; i += 1) {
        last = await postTrip(
          new NextRequest("http://localhost/api/trip", {
            method: "POST",
            headers: {
              "content-type": "application/json",
              "x-forwarded-for": "203.0.113.89",
            },
            body: JSON.stringify({
              origin_lat: 40.75,
              origin_lng: -73.99,
              destination: "JFK",
            }),
          }),
        );
      }
      assert.equal(last.status, 429);
    } finally {
      restore();
    }
  });
});

test("trip planning rejects malformed JSON before schema validation", async () => {
  await withAppKey("test-key", async () => {
    const res = await postTrip(
      new NextRequest("http://localhost/api/trip", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: "{",
      }),
    );
    assert.equal(res.status, 400);
    assert.deepEqual(await res.json(), { error: "Malformed JSON request body." });
  });
});

test("agent chat names a root schema failure as body", async () => {
  const previous = process.env.NODE_ENV;
  process.env.NODE_ENV = "development";
  try {
    await withAppKey("test-key", async () => {
      const res = await postChat(
        new NextRequest("http://localhost/api/agent/chat", {
          method: "POST",
          headers: {
            "content-type": "application/json",
            "x-forwarded-for": "198.51.100.77",
          },
          body: "[]",
        }),
      );
      assert.equal(res.status, 400);
      const payload = await res.json();
      assert.match(payload.error, /body/);
    });
  } finally {
    process.env.NODE_ENV = previous;
  }
});

test("live feed treats an empty POST body as an invalid request", async () => {
  await withAppKey("test-key", async () => {
    const res = await postLiveFeed(
      new NextRequest("http://localhost/api/live-feed", { method: "POST" }),
    );
    assert.equal(res.status, 400);
    assert.deepEqual(await res.json(), { error: "Invalid request." });
  });
});

test("ws-ticket missing APP_KEY is a redacted 500", async () => {
  await withAppKey(undefined, async () => {
    const res = await getWsTicket(getRequest("http://localhost/api/ws-ticket?path=/ws/live-feed"));
    assert.equal(res.status, 500);
    assert.deepEqual(await res.json(), { error: "Server is not configured (missing APP_KEY)." });
  });
});
