import assert from "node:assert/strict";
import test from "node:test";

import { NextRequest } from "next/server";

import { rateLimit } from "./rate-limit.ts";
import { cn } from "./utils.ts";
import { getRouteColor } from "./mta-colors.ts";
import { deriveTransitRouteIds, isAlertForRouteIds, normalizeTripCandidates, routeCandidateLabel } from "./route-planning.ts";
import { enrichRoute, planTrip } from "./api.ts";
import {
  DestinationRequestGate,
  publishDestinationSearch,
  visibleDestinationSuggestions,
} from "./use-destination-search.ts";
import { installMobileViewportVariables } from "./use-mobile-visible-viewport.ts";
import {
  createResponsePresentationModeStore,
  persistResponsePresentationMode,
  readResponsePresentationMode,
} from "./response-presentation.ts";
import { parseTripResponse, TRIP_PLAN_FAILED } from "./trip-response.ts";
import { apiBaseUrl, fetchWsTicket, wsUrlWithTicket } from "./ws-ticket.ts";
import { buildAgentChatRequest } from "./agent-chat-request.ts";
import { canonicalPlaceLabel, canonicalStopLabel } from "./canonical-itinerary-label.ts";
import { withLiveFeedNow } from "./use-live-feed.ts";
import { fetchSessionSnapshot, clearPersistedSession, resetSession } from "./agent-chat-session.ts";
import { appendRequestSearch, readJsonBody, resolveBackendBaseUrl } from "./backend-proxy-core.ts";
import { parseSseStream } from "./agent-chat-stream.ts";
import "./agent-route-card-contract.ts";

test("rateLimit returns 429 after the window is exhausted for one client", () => {
  const req = new NextRequest("http://localhost/api/trip", {
    headers: { "x-forwarded-for": "198.51.100.10, 10.0.0.1" },
  });
  assert.equal(rateLimit(req, { key: "rate-limit-test", limit: 2, windowMs: 60_000 }), null);
  assert.equal(rateLimit(req, { key: "rate-limit-test", limit: 2, windowMs: 60_000 }), null);
  const limited = rateLimit(req, { key: "rate-limit-test", limit: 2, windowMs: 60_000 });
  assert.equal(limited?.status, 429);
  assert.ok(limited?.headers.get("Retry-After"));
});

test("rateLimit uses x-real-ip and resets after the window elapses", () => {
  const realNow = Date.now;
  let now = realNow();
  Date.now = () => now;
  try {
    const req = new NextRequest("http://localhost/api/trip", {
      headers: { "x-real-ip": "198.51.100.20" },
    });
    assert.equal(rateLimit(req, { key: "rate-limit-real-ip", limit: 1, windowMs: 1_000 }), null);
    assert.equal(rateLimit(req, { key: "rate-limit-real-ip", limit: 1, windowMs: 1_000 })?.status, 429);
    now += 61_000;
    assert.equal(rateLimit(req, { key: "rate-limit-real-ip", limit: 1, windowMs: 1_000 }), null);
  } finally {
    Date.now = realNow;
  }
});

test("unknown MTA services keep the documented gold fallback", () => {
  assert.equal(getRouteColor("not-a-line"), "#FFD700");
  assert.equal(getRouteColor("q"), getRouteColor("Q"));
});

test("route candidate labels and alert scoping use canonical transit ids", () => {
  const steps = [
    { type: "WALK" },
    { type: "SUBWAY", route_id: "q", departure_stop: "Times Sq" },
    { type: "SUBWAY", route_id: "7", departure_stop: "Queensboro Plaza" },
  ];
  assert.equal(routeCandidateLabel(steps), "Q/7 via Times Sq");
  assert.deepEqual(deriveTransitRouteIds(steps), ["Q", "7"]);
  assert.equal(
    isAlertForRouteIds({ header: "Q delay", route_ids: ["q"] }, ["Q"]),
    true,
  );
  assert.equal(isAlertForRouteIds({ header: "A delay", routeIds: ["A"] }, ["Q"]), false);
  assert.equal(isAlertForRouteIds({ header: "none" }, ["Q"]), false);
});

test("cn merges tailwind classes without inventing values", () => {
  assert.equal(cn("px-2", "px-4"), "px-4");
});

test("enrichRoute returns the backend payload and fails on a non-OK response", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () =>
    new Response(JSON.stringify({ steps: [{ type: "WALK" }], enriched: true }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  try {
    const result = await enrichRoute([{ type: "WALK" }]);
    assert.deepEqual(result, { steps: [{ type: "WALK" }], enriched: true });
  } finally {
    globalThis.fetch = original;
  }

  globalThis.fetch = async () => new Response("nope", { status: 503 });
  try {
    await assert.rejects(enrichRoute([{ type: "WALK" }]), /Failed to enrich route/);
  } finally {
    globalThis.fetch = original;
  }
});

test("destination search ignores stale generations and hides inactive suggestions", () => {
  const gate = new DestinationRequestGate();
  const first = gate.begin();
  const second = gate.begin();
  const stale = publishDestinationSearch(
    { query: "", suggestions: [] },
    gate,
    first,
    "times",
    [{ id: "1", label: "Times Sq" }],
  );
  assert.deepEqual(stale, { query: "", suggestions: [] });
  const current = publishDestinationSearch(
    { query: "", suggestions: [] },
    gate,
    second,
    "times",
    [{ id: "1", label: "Times Sq" }],
  );
  assert.equal(current.query, "times");
  assert.equal(visibleDestinationSuggestions(current, "times", true).length, 1);
  assert.deepEqual(visibleDestinationSuggestions(current, "times", false), []);
});

test("mobile viewport variables commit visible height and clean up listeners", () => {
  const properties = {};
  const windowListeners = [];
  const viewportListeners = [];
  let cancelled = 0;
  const targetWindow = {
    innerHeight: 900,
    visualViewport: {
      height: 640,
      offsetTop: 24,
      addEventListener(_type, handler) {
        viewportListeners.push(handler);
      },
      removeEventListener() {},
    },
    requestAnimationFrame(cb) {
      cb();
      return 7;
    },
    cancelAnimationFrame() {
      cancelled += 1;
    },
    addEventListener(_type, handler) {
      windowListeners.push(handler);
    },
    removeEventListener() {},
  };
  const uninstall = installMobileViewportVariables(targetWindow, {
    style: {
      setProperty(name, value) {
        properties[name] = value;
      },
    },
  });
  assert.equal(properties["--visible-viewport-height"], "640px");
  assert.equal(properties["--mobile-viewport-offset-top"], "24px");
  targetWindow.requestAnimationFrame = () => 9;
  windowListeners[0]();
  uninstall();
  assert.equal(cancelled, 1);
  assert.equal(windowListeners.length, 1);
  assert.equal(viewportListeners.length, 2);
});

test("response presentation storage failures stay on auto", () => {
  assert.equal(readResponsePresentationMode(undefined), "auto");
  assert.equal(
    readResponsePresentationMode({
      getItem() {
        throw new Error("blocked");
      },
      setItem() {},
    }),
    "auto",
  );
  persistResponsePresentationMode(
    {
      getItem() {
        return null;
      },
      setItem() {
        throw new Error("full");
      },
    },
    "quick",
  );
  const store = createResponsePresentationModeStore(() => ({
    getItem() {
      return "quick";
    },
    setItem() {},
  }));
  assert.equal(store.getServerSnapshot(), "auto");
  assert.equal(store.getClientSnapshot(), "quick");
  let notified = 0;
  const unsubscribe = store.subscribe(() => {
    notified += 1;
  });
  store.setMode("auto");
  unsubscribe();
  assert.equal(notified, 1);
});

test("planTrip retries a 503 once then returns a validated itinerary", async () => {
  const originalFetch = globalThis.fetch;
  const originalTimeout = globalThis.setTimeout;
  globalThis.setTimeout = (callback, ms, ...args) => {
    if (ms === 2000) return originalTimeout(callback, 0, ...args);
    return originalTimeout(callback, ms, ...args);
  };
  let calls = 0;
  const validTrip = {
    recommendation: "ok",
    route: [{ type: "WALK" }],
    selected_route_index: 0,
    alerts: [],
    route_candidates: [
      {
        id: "c0",
        index: 0,
        steps: [{ type: "WALK" }],
        is_recommended: true,
        total_minutes: 12,
        itinerary: {
          itinerary_id: "it",
          total_duration_seconds: 720,
          transfer_count: 0,
          legs: [{ mode: "WALK" }],
        },
        score_breakdown: { transfers: 0 },
      },
    ],
  };
  globalThis.fetch = async () => {
    calls += 1;
    if (calls === 1) return new Response("nope", { status: 503 });
    return new Response(JSON.stringify(validTrip), { status: 200 });
  };
  try {
    const result = await planTrip(40.75, -73.99, "JFK");
    assert.equal(result.selected_route_index, 0);
    assert.equal(calls, 2);
  } finally {
    globalThis.fetch = originalFetch;
    globalThis.setTimeout = originalTimeout;
  }
});

test("planTrip rejects malformed JSON and preserves caller cancellation", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () =>
    new Response(JSON.stringify({ recommendation: "x", route: [], alerts: [] }), { status: 200 });
  try {
    await assert.rejects(planTrip(40.75, -73.99, "JFK"), new Error(TRIP_PLAN_FAILED));
  } finally {
    globalThis.fetch = original;
  }

  const aborted = new AbortController();
  aborted.abort();
  globalThis.fetch = async (_url, init) => {
    if (init?.signal?.aborted) throw new DOMException("aborted", "AbortError");
    throw new Error("should not fetch");
  };
  try {
    await assert.rejects(planTrip(40.75, -73.99, "JFK", null, { signal: aborted.signal }));
  } finally {
    globalThis.fetch = original;
  }
});

test("fetchWsTicket rejects a non-object payload and apiBaseUrl keeps a configured host", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () =>
    new Response(JSON.stringify(["ticket"]), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  try {
    await assert.rejects(fetchWsTicket("/ws/live-feed"), /missing ticket/);
  } finally {
    globalThis.fetch = original;
  }

  const previous = process.env.NEXT_PUBLIC_API_URL;
  process.env.NEXT_PUBLIC_API_URL = "https://api.example.test/";
  try {
    assert.equal(apiBaseUrl(), "https://api.example.test");
  } finally {
    if (previous === undefined) delete process.env.NEXT_PUBLIC_API_URL;
    else process.env.NEXT_PUBLIC_API_URL = previous;
  }
});

test("live feed clock updates ignore a zero timestamp", () => {
  assert.deepEqual(withLiveFeedNow({ nowMs: 1, label: "keep" }, 0), { nowMs: 1, label: "keep" });
  assert.equal(withLiveFeedNow({ nowMs: 1 }, 9).nowMs, 9);
});

test("parseTripResponse rejects a selected index that matches no candidate", () => {
  assert.throws(
    () =>
      parseTripResponse({
        recommendation: "x",
        route: [{ type: "WALK" }],
        selected_route_index: 3,
        alerts: [{ header: "ok" }],
        route_candidates: [
          {
            id: "c0",
            index: 0,
            steps: [{ type: "WALK" }],
            is_recommended: true,
            total_minutes: 12,
            itinerary: {
              itinerary_id: "it",
              total_duration_seconds: 720,
              transfer_count: 0,
              legs: [{ mode: "WALK" }],
            },
            score_breakdown: { transfers: 0 },
          },
        ],
      }),
    new Error(TRIP_PLAN_FAILED),
  );
});

test("clearPersistedSession survives a blocked session store", () => {
  const windowDescriptor = Object.getOwnPropertyDescriptor(globalThis, "window");
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: {
      location: { origin: "http://localhost:3000", hostname: "localhost" },
      sessionStorage: {
        getItem() {
          return null;
        },
        setItem() {},
        removeItem() {
          throw new Error("blocked");
        },
      },
    },
  });
  try {
    clearPersistedSession();
  } finally {
    if (windowDescriptor) Object.defineProperty(globalThis, "window", windowDescriptor);
    else delete globalThis.window;
  }
});

test("fetchSessionSnapshot treats 404 as expired and other failures as unavailable", async () => {
  assert.deepEqual(
    await fetchSessionSnapshot("sess", async () => new Response("gone", { status: 404 })),
    { status: "expired" },
  );
  assert.deepEqual(
    await fetchSessionSnapshot("sess", async () => {
      throw new Error("offline");
    }),
    { status: "unavailable" },
  );
  assert.deepEqual(
    await fetchSessionSnapshot("sess", async () => new Response("nope", { status: 500 })),
    { status: "unavailable" },
  );
});

test("resetSession swallows a failed server wipe", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => {
    throw new TypeError("offline");
  };
  try {
    await resetSession("sess-1");
  } finally {
    globalThis.fetch = original;
  }
});

test("apiBaseUrl uses the production fallback on a deployed host without a public API URL", () => {
  const previous = process.env.NEXT_PUBLIC_API_URL;
  const windowDescriptor = Object.getOwnPropertyDescriptor(globalThis, "window");
  delete process.env.NEXT_PUBLIC_API_URL;
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: { location: { hostname: "smart-route.example" } },
  });
  try {
    assert.equal(apiBaseUrl(), "https://jarvis-mta-assistant.onrender.com");
  } finally {
    if (previous === undefined) delete process.env.NEXT_PUBLIC_API_URL;
    else process.env.NEXT_PUBLIC_API_URL = previous;
    if (windowDescriptor) Object.defineProperty(globalThis, "window", windowDescriptor);
    else delete globalThis.window;
  }
});

test("walk-only routes label as Walk via direct and train_line ids still count", () => {
  assert.equal(routeCandidateLabel([{ type: "WALK" }]), "Walk via direct");
  assert.deepEqual(deriveTransitRouteIds([{ type: "SUBWAY", train_line: " q " }]), ["Q"]);
  assert.deepEqual(deriveTransitRouteIds([{ type: "SUBWAY" }]), []);
  assert.deepEqual(deriveTransitRouteIds([{ type: "SUBWAY", route_id: "  " }]), []);
  assert.equal(routeCandidateLabel([{ type: "SUBWAY", route_id: "Q", departure_stop: "Canal" }]), "Q via Canal");
  assert.equal(isAlertForRouteIds({ header: "Q delay", route_ids: ["Q"] }, []), false);
  assert.equal(normalizeTripCandidates({
    recommendation: "x",
    route: [],
    selected_route_index: 2,
    alerts: [],
    route_candidates: [{ id: "c0", index: 0, steps: [], is_recommended: true, total_minutes: 1, itinerary: { itinerary_id: "it", total_duration_seconds: 60, transfer_count: 0, legs: [] }, score_breakdown: { transfers: 0 } }],
  }), null);
});

test("canonical place labels prefer display_name and fall back when empty", () => {
  assert.equal(canonicalPlaceLabel({ display_name: " Union Sq " }, "here"), "Union Sq");
  assert.equal(canonicalPlaceLabel({ name: "" }, "here"), "here");
  assert.equal(canonicalPlaceLabel("   ", "here"), "here");
  assert.equal(canonicalStopLabel("  Canal  "), "Canal");
  assert.equal(canonicalStopLabel("   "), null);
  assert.equal(canonicalStopLabel({}), null);
});

test("buildAgentChatRequest drops invalid origin and oversized ids", () => {
  const request = buildAgentChatRequest({
    sessionId: "s".repeat(200),
    message: "  Next Q?  ",
    origin: { lat: Number.NaN, lng: -73.99 },
    selectedCardId: null,
  });
  assert.equal(request.session_id, undefined);
  assert.equal(request.origin, undefined);
  assert.equal(request.selected_card_id, undefined);
  assert.equal(request.message, "Next Q?");
});

test("fetchWsTicket rejects a non-OK mint and wsUrlWithTicket uses https as wss", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => new Response("nope", { status: 503 });
  try {
    await assert.rejects(fetchWsTicket("/ws/live-feed"), /failed \(503\)/);
  } finally {
    globalThis.fetch = original;
  }
  const previous = process.env.NEXT_PUBLIC_API_URL;
  const windowDescriptor = Object.getOwnPropertyDescriptor(globalThis, "window");
  process.env.NEXT_PUBLIC_API_URL = "https://api.example.test";
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: { location: { hostname: "localhost" } },
  });
  try {
    assert.equal(
      wsUrlWithTicket("/ws/live-feed", "t 1"),
      "wss://api.example.test/ws/live-feed?ticket=t%201",
    );
  } finally {
    if (previous === undefined) delete process.env.NEXT_PUBLIC_API_URL;
    else process.env.NEXT_PUBLIC_API_URL = previous;
    if (windowDescriptor) Object.defineProperty(globalThis, "window", windowDescriptor);
    else delete globalThis.window;
  }
});

test("a deployed host ignores a stale localhost public API URL", () => {
  const previous = process.env.NEXT_PUBLIC_API_URL;
  const windowDescriptor = Object.getOwnPropertyDescriptor(globalThis, "window");
  process.env.NEXT_PUBLIC_API_URL = "http://localhost:8000";
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: { location: { hostname: "smart-route.example" } },
  });
  try {
    assert.equal(apiBaseUrl(), "https://jarvis-mta-assistant.onrender.com");
  } finally {
    if (previous === undefined) delete process.env.NEXT_PUBLIC_API_URL;
    else process.env.NEXT_PUBLIC_API_URL = previous;
    if (windowDescriptor) Object.defineProperty(globalThis, "window", windowDescriptor);
    else delete globalThis.window;
  }
});

test("planTrip forwards destination coordinates from a resolved selection", async () => {
  const original = globalThis.fetch;
  let body;
  globalThis.fetch = async (_url, init) => {
    body = JSON.parse(init.body);
    return new Response(
      JSON.stringify({
        recommendation: "ok",
        route: [{ type: "WALK" }],
        selected_route_index: 0,
        alerts: [],
        route_candidates: [
          {
            id: "c0",
            index: 0,
            steps: [{ type: "WALK" }],
            is_recommended: true,
            total_minutes: 12,
            itinerary: {
              itinerary_id: "it",
              total_duration_seconds: 720,
              transfer_count: 0,
              legs: [{ mode: "WALK" }],
            },
            score_breakdown: { transfers: 0 },
          },
        ],
      }),
      { status: 200 },
    );
  };
  try {
    await planTrip(40.75, -73.99, "JFK", {
      label: "JFK",
      coordinates: { lat: 40.64, lng: -73.78 },
    });
    assert.equal(body.destination_lat, 40.64);
    assert.equal(body.destination_lng, -73.78);
  } finally {
    globalThis.fetch = original;
  }
});

test("an invalid public API URL on a deployed host is not treated as local", () => {
  const previous = process.env.NEXT_PUBLIC_API_URL;
  const windowDescriptor = Object.getOwnPropertyDescriptor(globalThis, "window");
  process.env.NEXT_PUBLIC_API_URL = "not-a-url";
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: { location: { hostname: "smart-route.example" } },
  });
  try {
    assert.equal(apiBaseUrl(), "not-a-url");
  } finally {
    if (previous === undefined) delete process.env.NEXT_PUBLIC_API_URL;
    else process.env.NEXT_PUBLIC_API_URL = previous;
    if (windowDescriptor) Object.defineProperty(globalThis, "window", windowDescriptor);
    else delete globalThis.window;
  }
});

test("fetchSessionSnapshot treats a JSON decode failure as unavailable", async () => {
  const result = await fetchSessionSnapshot("sess", async () => ({
    status: 200,
    ok: true,
    json: async () => {
      throw new Error("truncated");
    },
  }));
  assert.deepEqual(result, { status: "unavailable" });
});

test("readJsonBody distinguishes empty bodies from invalid UTF-8", async () => {
  const empty = await readJsonBody(new Request("http://localhost/api/trip", { method: "POST" }));
  assert.deepEqual(empty, { ok: true, empty: true, value: undefined });
  const invalid = await readJsonBody(
    new Request("http://localhost/api/trip", {
      method: "POST",
      body: new Uint8Array([0xff, 0xfe]),
    }),
  );
  assert.equal(invalid.ok, false);
});

test("appendRequestSearch leaves a path unchanged when the request has no query", () => {
  assert.equal(
    appendRequestSearch("/api/vehicles", new Request("http://localhost/api/vehicles")),
    "/api/vehicles",
  );
});

test("resolveBackendBaseUrl rejects a non-http Vercel override", () => {
  assert.equal(
    resolveBackendBaseUrl({ VERCEL: "1", API_URL: "ftp://example.test" }),
    "https://jarvis-mta-assistant.onrender.com",
  );
});

test("SSE parser skips comments, CRLF heartbeats, and frames without an event field", async () => {
  const encoder = new TextEncoder();
  const reader = {
    async read() {
      if (this.sent) return { done: true, value: undefined };
      this.sent = true;
      return {
        done: false,
        value: encoder.encode(": ping\r\ndata: {\"text\":\"hi\"}\n\nevent: token\ndata: not-json\n\nevent: token\ndata: []\n\nevent: token\r\ndata: {\"text\":\"ok\"}\n\n"),
      };
    },
  };
  const events = [];
  for await (const event of parseSseStream(reader)) events.push(event);
  assert.deepEqual(
    events.map((event) => event.text),
    ["ok"],
  );
});

test("planTrip throws a non-Error fetch failure without retrying", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => {
    throw "offline";
  };
  try {
    await assert.rejects(planTrip(40.75, -73.99, "JFK"), (err) => err === "offline");
  } finally {
    globalThis.fetch = original;
  }
});

test("fetchWsTicket rejects a ticket-less object payload", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () =>
    new Response(JSON.stringify({ ws_base_url: "ws://localhost:8000" }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  try {
    await assert.rejects(fetchWsTicket("/ws/live-feed"), /missing ticket/);
  } finally {
    globalThis.fetch = original;
  }
});

test("apiBaseUrl uses localhost when the browser host is local", () => {
  const previous = process.env.NEXT_PUBLIC_API_URL;
  const windowDescriptor = Object.getOwnPropertyDescriptor(globalThis, "window");
  delete process.env.NEXT_PUBLIC_API_URL;
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: { location: { hostname: "localhost" } },
  });
  try {
    assert.equal(apiBaseUrl(), "http://localhost:8000");
  } finally {
    if (previous === undefined) delete process.env.NEXT_PUBLIC_API_URL;
    else process.env.NEXT_PUBLIC_API_URL = previous;
    if (windowDescriptor) Object.defineProperty(globalThis, "window", windowDescriptor);
    else delete globalThis.window;
  }
});

test("planTrip does not retry when the caller already aborted", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => {
    throw new Error("should not fetch");
  };
  const controller = new AbortController();
  controller.abort();
  try {
    await assert.rejects(
      planTrip(40.75, -73.99, "JFK", null, { signal: controller.signal }),
    );
  } finally {
    globalThis.fetch = original;
  }
});

test("planTrip retries a 503 once after attaching an idle abort listener", async () => {
  const originalFetch = globalThis.fetch;
  const originalTimeout = globalThis.setTimeout;
  globalThis.setTimeout = (callback, ms, ...args) =>
    originalTimeout(callback, ms === 2000 ? 0 : ms, ...args);
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    if (calls === 1) return new Response("busy", { status: 503 });
    return new Response(
      JSON.stringify({
        recommendation: "ok",
        route: [{ type: "WALK" }],
        selected_route_index: 0,
        alerts: [],
        route_candidates: [
          {
            id: "c0",
            index: 0,
            steps: [{ type: "WALK" }],
            is_recommended: true,
            total_minutes: 12,
            itinerary: {
              itinerary_id: "it",
              total_duration_seconds: 720,
              transfer_count: 0,
              legs: [{ mode: "WALK" }],
            },
            score_breakdown: { transfers: 0 },
          },
        ],
      }),
      { status: 200 },
    );
  };
  const idle = new AbortController();
  try {
    const trip = await planTrip(40.75, -73.99, "JFK", null, { signal: idle.signal });
    assert.equal(calls, 2);
    assert.equal(trip.route_candidates[0].itinerary.itinerary_id, "it");
  } finally {
    globalThis.fetch = originalFetch;
    globalThis.setTimeout = originalTimeout;
  }
});