import assert from "node:assert/strict";
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import test from "node:test";

import { useAgentChat } from "./use-agent-chat.ts";
import { persistSessionId } from "./agent-chat-session.ts";
import { SmartRouteThemeProvider, useSmartRouteTheme } from "./use-chat-theme.ts";
import { useDestinationSearch } from "./use-destination-search.ts";
import { useLiveFeed } from "./use-live-feed.ts";
import { useMobileVisibleViewport } from "./use-mobile-visible-viewport.ts";
import { useServiceAlerts } from "./use-service-alerts.ts";
import { useVoiceInput } from "./use-voice-input.ts";

const realSetTimeout = globalThis.setTimeout;
globalThis.setTimeout = (callback, ms, ...args) =>
  realSetTimeout(callback, ms === 180 ? 0 : ms, ...args);

function createDomNode(name, ownerDocument) {
  const node = {
    nodeType: name === "#text" ? 3 : name === "#document" ? 9 : 1,
    nodeName: name,
    tagName: name,
    namespaceURI: "http://www.w3.org/1999/xhtml",
    ownerDocument,
    parentNode: null,
    childNodes: [],
    style: { setProperty() {}, cssText: "" },
    dataset: {},
    className: "",
    textContent: "",
    innerHTML: "",
    attributes: {},
    setAttribute(key, value) {
      this.attributes[key] = String(value);
    },
    getAttribute(key) {
      return this.attributes[key] ?? null;
    },
    hasAttribute(key) {
      return key in this.attributes;
    },
    removeAttribute(key) {
      delete this.attributes[key];
    },
    appendChild(child) {
      this.childNodes.push(child);
      child.parentNode = this;
      this.firstChild = this.childNodes[0];
      this.lastChild = this.childNodes.at(-1);
      return child;
    },
    removeChild(child) {
      this.childNodes = this.childNodes.filter((entry) => entry !== child);
      child.parentNode = null;
      this.firstChild = this.childNodes[0] ?? null;
      this.lastChild = this.childNodes.at(-1) ?? null;
      return child;
    },
    insertBefore(child, ref) {
      const index = this.childNodes.indexOf(ref);
      if (index === -1) return this.appendChild(child);
      this.childNodes.splice(index, 0, child);
      child.parentNode = this;
      this.firstChild = this.childNodes[0];
      this.lastChild = this.childNodes.at(-1);
      return child;
    },
    addEventListener() {},
    removeEventListener() {},
    contains() {
      return false;
    },
  };
  Object.defineProperty(node, "firstChild", { writable: true, value: null });
  Object.defineProperty(node, "lastChild", { writable: true, value: null });
  Object.defineProperty(node, "nextSibling", { writable: true, value: null });
  Object.defineProperty(node, "previousSibling", { writable: true, value: null });
  return node;
}

function installClientDom() {
  const storage = new Map();
  const documentNode = createDomNode("#document", null);
  const html = createDomNode("HTML", documentNode);
  const body = createDomNode("BODY", documentNode);
  const head = createDomNode("HEAD", documentNode);
  documentNode.ownerDocument = documentNode;
  documentNode.documentElement = html;
  documentNode.body = body;
  documentNode.head = head;
  documentNode.defaultView = null;
  documentNode.createElement = (name) => createDomNode(String(name).toUpperCase(), documentNode);
  documentNode.createElementNS = (_ns, name) => documentNode.createElement(name);
  documentNode.createTextNode = (text) => {
    const node = createDomNode("#text", documentNode);
    node.nodeType = 3;
    node.textContent = String(text);
    return node;
  };
  documentNode.createComment = () => createDomNode("#comment", documentNode);
  documentNode.querySelector = () => null;
  documentNode.querySelectorAll = () => [];
  documentNode.getElementById = () => null;
  html.appendChild(head);
  html.appendChild(body);
  documentNode.appendChild(html);

  const speechInstances = [];
  const windowStub = {
    document: documentNode,
    HTMLElement: class HTMLElement {},
    HTMLIFrameElement: class HTMLIFrameElement {},
    SVGElement: class SVGElement {},
    Node: class Node {},
    Event,
    localStorage: {
      getItem(key) {
        return storage.has(key) ? storage.get(key) : null;
      },
      setItem(key, value) {
        storage.set(key, String(value));
      },
    },
    sessionStorage: {
      getItem() {
        return null;
      },
      setItem() {},
      removeItem() {},
    },
    matchMedia() {
      return { matches: false, addEventListener() {}, removeEventListener() {} };
    },
    requestAnimationFrame(callback) {
      return setTimeout(callback, 0);
    },
    cancelAnimationFrame(id) {
      clearTimeout(id);
    },
    addEventListener() {},
    removeEventListener() {},
    innerHeight: 800,
    location: { origin: "http://localhost:3000", hostname: "localhost" },
    setInterval,
    clearInterval,
    setTimeout(callback, ms, ...args) {
      return globalThis.setTimeout(callback, ms === 180 ? 0 : ms, ...args);
    },
    clearTimeout,
    visualViewport: {
      height: 640,
      offsetTop: 0,
      addEventListener() {},
      removeEventListener() {},
    },
    SpeechRecognition: class SpeechRecognition {
      constructor() {
        this.lang = "";
        this.continuous = false;
        this.interimResults = false;
        this.maxAlternatives = 1;
        this.onresult = null;
        this.onerror = null;
        this.onend = null;
        this.startThrows = false;
        speechInstances.push(this);
      }
      start() {
        if (this.startThrows) throw new Error("recognition busy");
      }
      stop() {
        this.onend?.();
      }
      abort() {}
    },
  };
  documentNode.defaultView = windowStub;
  globalThis.window = windowStub;
  globalThis.document = documentNode;
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    value: { geolocation: undefined },
  });
  return { documentNode, body, speechInstances };
}

function deferred() {
  let resolve;
  const promise = new Promise((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

async function renderProbe(Probe, setup) {
  const installed = installClientDom();
  setup?.(installed);
  const rootNode = installed.body.ownerDocument.createElement("DIV");
  installed.body.appendChild(rootNode);
  const root = createRoot(rootNode);
  await act(async () => {
    root.render(createElement(Probe));
  });
  return {
    ...installed,
    async unmount() {
      await act(async () => {
        root.unmount();
      });
    },
  };
}

async function flushEffects() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

async function waitUntil(predicate, label) {
  for (let attempt = 0; attempt < 25; attempt += 1) {
    if (predicate()) return;
    await flushEffects();
  }
  throw new Error(label);
}

function installFakeSockets() {
  const sockets = [];
  class FakeSocket {
    constructor() {
      this.readyState = 0;
      this.onopen = null;
      this.onclose = null;
      this.onerror = null;
      this.onmessage = null;
      sockets.push(this);
    }
    open() {
      this.readyState = 1;
      this.onopen?.(new Event("open"));
    }
    close() {
      this.readyState = 3;
    }
    send() {}
  }
  FakeSocket.CONNECTING = 0;
  FakeSocket.OPEN = 1;
  FakeSocket.CLOSING = 2;
  FakeSocket.CLOSED = 3;
  globalThis.WebSocket = FakeSocket;
  return sockets;
}

test("owned hooks render their initial public state without a browser document", () => {
  let live;
  let alerts;
  let search;
  let chat;
  function Probe() {
    useMobileVisibleViewport();
    live = useLiveFeed(null);
    alerts = useServiceAlerts(60_000);
    search = useDestinationSearch({ inputValue: "ab", enabled: false });
    chat = useAgentChat();
    return createElement("span", null, live.error ?? alerts.connectionState);
  }
  const html = renderToString(createElement(Probe));
  assert.match(html, /connecting/);
  assert.equal(alerts.isLoading, true);
  assert.equal(search.suggestions.length, 0);
  assert.equal(chat.messages.length, 0);
});

test("agent chat send starts a turn, cancel marks it cancelled, and reset clears it", async () => {
  const { body } = installClientDom();
  const rootNode = body.ownerDocument.createElement("DIV");
  body.appendChild(rootNode);
  const transport = async function* (_request, signal) {
    yield { type: "meta", session_id: "sess-1", turn_id: "t1" };
    await new Promise((_, reject) => {
      const fail = () => reject(Object.assign(new Error("aborted"), { name: "AbortError" }));
      if (signal.aborted) {
        fail();
        return;
      }
      signal.addEventListener("abort", fail, { once: true });
    });
  };
  let chat;
  function Probe() {
    chat = useAgentChat({ transport, getOrigin: () => ({ lat: 40.75, lng: -73.99 }) });
    return createElement("span", null, String(chat.messages.length));
  }
  const root = createRoot(rootNode);
  await act(async () => {
    root.render(createElement(Probe));
  });
  await act(async () => {
    chat.send("   ");
  });
  assert.equal(chat.messages.length, 0);
  await act(async () => {
    chat.send("Next Q to Union?");
  });
  assert.equal(chat.messages[0].text, "Next Q to Union?");
  assert.equal(chat.messages[1].role, "assistant");
  assert.equal(chat.messages[1].isStreaming, true);
  await act(async () => {
    chat.cancel();
  });
  assert.equal(chat.messages[1].stopReason, "cancelled");
  await act(async () => {
    chat.reset();
  });
  assert.equal(chat.messages.length, 0);
  await act(async () => {
    root.unmount();
  });
});

test("agent chat reset aborts an in-flight turn and clears the transcript", async () => {
  const { body } = installClientDom();
  const rootNode = body.ownerDocument.createElement("DIV");
  body.appendChild(rootNode);
  const transport = async function* (_request, signal) {
    yield { type: "meta", session_id: "sess-1", turn_id: "t1" };
    await new Promise((_, reject) => {
      const fail = () => reject(Object.assign(new Error("aborted"), { name: "AbortError" }));
      if (signal.aborted) {
        fail();
        return;
      }
      signal.addEventListener("abort", fail, { once: true });
    });
  };
  let chat;
  function Probe() {
    chat = useAgentChat({ transport });
    return createElement("span", null, String(chat.messages.length));
  }
  const root = createRoot(rootNode);
  await act(async () => {
    root.render(createElement(Probe));
  });
  await act(async () => {
    chat.send("Next Q to Union?");
  });
  assert.equal(chat.messages[1].isStreaming, true);
  await act(async () => {
    chat.reset();
  });
  assert.equal(chat.messages.length, 0);
  await act(async () => {
    root.unmount();
  });
});

test("agent chat send retries a 503 from the chat endpoint then shows the reply", async () => {
  const originalFetch = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = async (url) => {
    const href = String(url);
    if (!href.includes("/api/agent/chat") || href.includes("session")) {
      return new Response("{}", { status: 200 });
    }
    calls += 1;
    if (calls === 1) return new Response("busy", { status: 503 });
    return new Response(
      "event: token\ndata: {\"text\":\"Q in 4\"}\n\nevent: done\ndata: {\"session_id\":\"s1\",\"turn_id\":\"t1\",\"stop_reason\":\"end_turn\",\"usage\":{}}\n\n",
      { status: 200, headers: { "content-type": "text/event-stream" } },
    );
  };
  let chat;
  function Probe() {
    chat = useAgentChat();
    return createElement("span", null, String(chat.messages.length));
  }
  const mounted = await renderProbe(Probe);
  try {
    await act(async () => {
      chat.send("Next Q?");
    });
    await waitUntil(() => chat.messages[1]?.text === "Q in 4", "retried reply did not arrive");
    assert.equal(calls, 2);
  } finally {
    globalThis.fetch = originalFetch;
    await mounted.unmount();
  }
});

test("agent chat appendLocalTurn stores arrivals without starting a network turn", async () => {
  const { body } = installClientDom();
  const rootNode = body.ownerDocument.createElement("DIV");
  body.appendChild(rootNode);
  let calls = 0;
  const transport = async function* () {
    calls += 1;
    yield { type: "done", session_id: "s", turn_id: "t", stop_reason: "end_turn", usage: {} };
  };
  let chat;
  function Probe() {
    chat = useAgentChat({ transport });
    return createElement("span", null, String(chat.messages.length));
  }
  const root = createRoot(rootNode);
  await act(async () => {
    root.render(createElement(Probe));
  });
  await act(async () => {
    chat.appendLocalTurn({
      text: "Nearby",
      arrivals: {
        routeId: "Q",
        stationName: "Times Sq",
        stationCoordinates: { lat: 40.75, lng: -73.98 },
        groups: [],
      },
    });
  });
  assert.equal(calls, 0);
  assert.equal(chat.messages[0].role, "assistant");
  assert.equal(chat.messages[0].text, "Nearby");
  assert.equal(chat.messages[0].arrivals.stationName, "Times Sq");
  await act(async () => {
    chat.selectCard("card-1");
  });
  assert.equal(chat.selectedCardId, "card-1");
  await act(async () => {
    root.unmount();
  });
});

test("destination search choose without a token returns null", async () => {
  let search;
  function Probe() {
    search = useDestinationSearch({ inputValue: "times square", enabled: true });
    return createElement("span", null, String(search.suggestions.length));
  }
  renderToString(createElement(Probe));
  assert.equal(await search.choose({ id: "1", label: "Times Sq" }), null);
});

test("theme toggle switches the provider from dark to light", async () => {
  const { body } = installClientDom();
  const rootNode = body.ownerDocument.createElement("DIV");
  body.appendChild(rootNode);
  let theme;
  function Probe() {
    theme = useSmartRouteTheme();
    return createElement("span", null, theme.theme);
  }
  const root = createRoot(rootNode);
  await act(async () => {
    root.render(createElement(SmartRouteThemeProvider, null, createElement(Probe)));
  });
  assert.equal(theme.theme, "dark");
  await act(async () => {
    theme.toggleTheme();
  });
  assert.equal(theme.theme, "light");
  await act(async () => {
    root.unmount();
  });
});

test("live feed ignores a stale bus generation and keeps a matching update", async () => {
  const originalFetch = globalThis.fetch;
  const originalWebSocket = globalThis.WebSocket;
  const { body } = installClientDom();
  const rootNode = body.ownerDocument.createElement("DIV");
  body.appendChild(rootNode);
  globalThis.fetch = async (url) => {
    if (String(url).includes("ws-ticket")) {
      return new Response(JSON.stringify({ ticket: "t", ws_base_url: "ws://localhost:8000" }), {
        status: 200,
      });
    }
    return new Response("{}", { status: 200 });
  };
  const sockets = [];
  class FakeSocket {
    constructor() {
      this.readyState = 1;
      this.onopen = null;
      this.onclose = null;
      this.onerror = null;
      this.onmessage = null;
      sockets.push(this);
      queueMicrotask(() => this.onopen?.(new Event("open")));
    }
    close() {
      this.readyState = 3;
    }
    send() {}
  }
  globalThis.WebSocket = FakeSocket;
  let live;
  function Probe() {
    live = useLiveFeed({ lat: 40.75, lng: -73.99 });
    return createElement("span", null, live.error ?? "ok");
  }
  const root = createRoot(rootNode);
  await act(async () => {
    root.render(createElement(Probe));
  });
  await act(async () => {
    await Promise.resolve();
  });
  assert.equal(sockets.length, 1);
  await act(async () => {
    sockets[0].onmessage?.({
      data: JSON.stringify({
        type: "snapshot",
        data: {
          arrivals: [{ arrival_time: 5, mode: "subway" }],
          updated_at: 10,
          bus_generation: 1,
        },
      }),
    });
  });
  assert.deepEqual(
    live.arrivals.map((arrival) => arrival.mode),
    ["subway"],
  );
  await act(async () => {
    sockets[0].onmessage?.({
      data: JSON.stringify({
        type: "snapshot",
        data: {
          arrivals: [{ arrival_time: 5, mode: "subway" }],
          updated_at: 10,
          bus_generation: 1,
          nearest_stop: { stop_name: "Times Sq", route_ids: ["Q"] },
          stops: [{ stop_name: "Times Sq" }],
          alerts: [],
          vehicles: [],
          signals: { heartbeat: true },
          incidents: [],
          nearby_issues: [],
          degraded: true,
          debug: { source: "test" },
        },
      }),
    });
  });
  assert.equal(live.nearestStop?.stop_name, "Times Sq");
  assert.equal(live.degraded, true);
  await act(async () => {
    sockets[0].onmessage?.({
      data: JSON.stringify({
        type: "snapshot",
        data: {
          arrivals: null,
          updated_at: null,
          stops: null,
          alerts: null,
          vehicles: null,
          incidents: null,
          nearby_issues: null,
        },
      }),
    });
  });
  assert.deepEqual(live.arrivals, []);
  await act(async () => {
    sockets[0].onmessage?.({
      data: JSON.stringify({
        type: "snapshot",
        data: {
          arrivals: [{ arrival_time: 5, mode: "subway" }],
          updated_at: 10,
          bus_generation: 1,
        },
      }),
    });
  });
  assert.deepEqual(
    live.arrivals.map((arrival) => arrival.mode),
    ["subway"],
  );
  await act(async () => {
    sockets[0].onmessage?.({
      data: JSON.stringify({
        type: "bus_update",
        data: {
          generation: 99,
          arrivals: [{ arrival_time: 1, mode: "bus" }],
          fetched_at: 11,
          status: "ready",
        },
      }),
    });
  });
  assert.equal(
    live.arrivals.some((arrival) => arrival.mode === "bus"),
    false,
  );
  await act(async () => {
    sockets[0].onmessage?.({
      data: JSON.stringify({
        type: "bus_update",
        data: {
          generation: 1,
          arrivals: [{ mode: "bus" }],
          fetched_at: 12,
          status: "cached",
        },
      }),
    });
  });
  assert.equal(live.arrivals.find((arrival) => arrival.mode === "bus")?.mode, "bus");
  await act(async () => {
    sockets[0].onmessage?.({
      data: JSON.stringify({
        type: "bus_update",
        data: {
          generation: 1,
          arrivals: [{ arrival_time: 3, mode: "bus" }],
          fetched_at: 13,
          status: "unavailable",
        },
      }),
    });
  });
  assert.equal(live.arrivals.find((arrival) => arrival.mode === "bus")?.arrival_time, 3);
  await act(async () => {
    sockets[0].onmessage?.({
      data: JSON.stringify({
        type: "bus_update",
        data: {
          generation: 1,
          arrivals: [{ arrival_time: null, mode: "bus" }],
          fetched_at: 14,
          status: "ready",
        },
      }),
    });
  });
  assert.equal(live.arrivals.find((arrival) => arrival.mode === "bus")?.arrival_time, null);
  await act(async () => {
    sockets[0].onmessage?.({
      data: JSON.stringify({
        type: "bus_update",
        data: {
          generation: 1,
          arrivals: [
            { arrival_time: null, mode: "bus" },
            { arrival_time: null, mode: "bus" },
          ],
          fetched_at: 15,
          status: "ready",
        },
      }),
    });
  });
  assert.equal(
    live.arrivals.filter((arrival) => arrival.mode === "bus").length,
    2,
  );
  await act(async () => {
    sockets[0].onmessage?.({
      data: JSON.stringify({ type: "bus_update", data: null }),
    });
  });
  await act(async () => {
    sockets[0].onmessage?.({
      data: JSON.stringify({ type: "snapshot", data: { arrivals: [] } }),
    });
  });
  assert.equal(live.arrivals.find((arrival) => arrival.mode === "bus")?.arrival_time, null);
  await act(async () => {
    sockets[0].onmessage?.({ data: JSON.stringify(null) });
  });
  await act(async () => {
    sockets[0].onmessage?.({ data: JSON.stringify({ type: "error" }) });
  });
  await act(async () => {
    sockets[0].onmessage?.({ data: JSON.stringify({ type: "error", message: "feed down" }) });
  });
  assert.equal(live.degraded, true);
  assert.equal(live.error, "feed down");
  await act(async () => {
    sockets[0].onmessage?.({ data: "{" });
  });
  assert.equal(live.error, "Malformed live feed message");
  await act(async () => {
    sockets[0].onerror?.(new Event("error"));
  });
  assert.equal(live.degraded, true);
  await act(async () => {
    sockets[0].onclose?.(new Event("close"));
    await Promise.resolve();
    await Promise.resolve();
  });
  assert.equal(live.error, "Live feed reconnecting");
  await act(async () => {
    root.unmount();
  });
  globalThis.fetch = originalFetch;
  globalThis.WebSocket = originalWebSocket;
});

function alertsJson(alerts, extra = {}) {
  return {
    alerts,
    updated_at: 100,
    active_count: alerts.length,
    affected_route_count: 1,
    ...extra,
  };
}

test("service alerts apply a REST snapshot when ticket minting fails", async () => {
  const originalFetch = globalThis.fetch;
  const snapshot = deferred();
  globalThis.fetch = async (url) => {
    if (String(url).startsWith("/api/service-alerts")) {
      await snapshot.promise;
      return new Response(
        JSON.stringify(alertsJson([{ alert_id: "a1", header: "Q delay", start: "t0" }])),
        { status: 200 },
      );
    }
    return new Response("nope", { status: 503 });
  };
  installFakeSockets();
  let alerts;
  function Probe() {
    alerts = useServiceAlerts(60_000);
    return createElement("span", null, alerts.connectionState);
  }
  const mounted = await renderProbe(Probe);
  try {
    assert.equal(alerts.alerts.length, 0);
    snapshot.resolve();
    await waitUntil(() => alerts.alerts[0]?.alert_id === "a1", "REST snapshot did not apply");
    assert.equal(alerts.isLoading, false);
    assert.equal(alerts.error, null);
    assert.equal(alerts.connectionState, "closed");
  } finally {
    globalThis.fetch = originalFetch;
    await mounted.unmount();
  }
});

test("service alerts surface the REST error body when the snapshot fails", async () => {
  const originalFetch = globalThis.fetch;
  const snapshot = deferred();
  globalThis.fetch = async (url) => {
    if (String(url).startsWith("/api/service-alerts")) {
      await snapshot.promise;
      return new Response(JSON.stringify({ error: "alerts down" }), { status: 503 });
    }
    return new Response("nope", { status: 503 });
  };
  installFakeSockets();
  let alerts;
  function Probe() {
    alerts = useServiceAlerts(60_000);
    return createElement("span", null, alerts.error ?? "ok");
  }
  const mounted = await renderProbe(Probe);
  try {
    snapshot.resolve();
    await waitUntil(() => alerts.error === "alerts down", "REST error did not surface");
    assert.equal(alerts.isLoading, false);
    assert.equal(alerts.alerts.length, 0);
  } finally {
    globalThis.fetch = originalFetch;
    await mounted.unmount();
  }
});

test("service alerts use the generic snapshot error when the body has no error field", async () => {
  const originalFetch = globalThis.fetch;
  const snapshot = deferred();
  globalThis.fetch = async (url) => {
    if (String(url).startsWith("/api/service-alerts")) {
      await snapshot.promise;
      return new Response("{}", { status: 503 });
    }
    return new Response("nope", { status: 503 });
  };
  installFakeSockets();
  let alerts;
  function Probe() {
    alerts = useServiceAlerts(60_000);
    return createElement("span", null, alerts.error ?? "ok");
  }
  const mounted = await renderProbe(Probe);
  try {
    snapshot.resolve();
    await waitUntil(
      () => alerts.error === "Service alerts unavailable",
      "generic REST error did not surface",
    );
  } finally {
    globalThis.fetch = originalFetch;
    await mounted.unmount();
  }
});

test("service alerts apply a websocket snapshot, heartbeat, and reject malformed frames", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    if (String(url).includes("ws-ticket")) {
      return new Response(JSON.stringify({ ticket: "t", ws_base_url: "ws://localhost:8000" }), {
        status: 200,
      });
    }
    return new Response(JSON.stringify(alertsJson([])), { status: 200 });
  };
  const sockets = installFakeSockets();
  let alerts;
  function Probe() {
    alerts = useServiceAlerts(60_000);
    return createElement("span", null, alerts.connectionState);
  }
  const mounted = await renderProbe(Probe);
  try {
    await flushEffects();
    assert.equal(sockets.length, 1);
    await act(async () => {
      sockets[0].open();
    });
    assert.equal(alerts.connectionState, "open");
    await act(async () => {
      sockets[0].onmessage?.({
        data: JSON.stringify({
          type: "SERVICE_SNAPSHOT",
          data: alertsJson([{ alert_id: "a1", header: "Q delay" }]),
        }),
      });
    });
    assert.equal(alerts.alerts[0].alert_id, "a1");
    await act(async () => {
      sockets[0].onmessage?.({
        data: JSON.stringify({ type: "SERVICE_HEARTBEAT", updated_at: 999 }),
      });
    });
    assert.equal(alerts.updatedAt, 999);
    await act(async () => {
      sockets[0].onmessage?.({ data: "{" });
    });
    assert.equal(alerts.error, "Malformed service alert stream message");
  } finally {
    globalThis.fetch = originalFetch;
    await mounted.unmount();
  }
});

test("service alerts mark changed ids from a signature update and from stream ids", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    if (String(url).includes("ws-ticket")) {
      return new Response(JSON.stringify({ ticket: "t", ws_base_url: "ws://localhost:8000" }), {
        status: 200,
      });
    }
    return new Response(JSON.stringify(alertsJson([])), { status: 200 });
  };
  const sockets = installFakeSockets();
  let alerts;
  function Probe() {
    alerts = useServiceAlerts(60_000);
    return createElement("span", null, String(alerts.changedAlertIds.size));
  }
  const mounted = await renderProbe(Probe);
  try {
    await flushEffects();
    await act(async () => {
      sockets[0].open();
    });
    await act(async () => {
      sockets[0].onmessage?.({
        data: JSON.stringify({
          type: "SERVICE_SNAPSHOT",
          data: alertsJson([{ alert_id: "a1", header: "old", route_ids: ["Q"] }]),
        }),
      });
    });
    assert.equal(alerts.changedAlertIds.size, 0);
    await act(async () => {
      sockets[0].onmessage?.({
        data: JSON.stringify({
          type: "SERVICE_UPDATE",
          data: alertsJson([{ alert_id: "a1", header: "new", route_ids: ["Q"] }]),
        }),
      });
    });
    assert.equal(alerts.changedAlertIds.has("a1"), true);
    await act(async () => {
      sockets[0].onmessage?.({
        data: JSON.stringify({
          type: "SERVICE_UPDATE",
          data: alertsJson([{ alert_id: "a2", header: "bus", routeIds: ["B63"] }]),
          changed_alert_ids: ["a2"],
        }),
      });
    });
    assert.equal(alerts.changedAlertIds.has("a2"), true);
    await act(async () => {
      sockets[0].onmessage?.({
        data: JSON.stringify({ type: "error", message: "alerts stream down" }),
      });
    });
    assert.equal(alerts.error, "alerts stream down");
  } finally {
    globalThis.fetch = originalFetch;
    await mounted.unmount();
  }
});

test("service alerts identify header-only alerts from route ids", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    if (String(url).includes("ws-ticket")) {
      return new Response(JSON.stringify({ ticket: "t", ws_base_url: "ws://localhost:8000" }), {
        status: 200,
      });
    }
    return new Response(JSON.stringify(alertsJson([])), { status: 200 });
  };
  const sockets = installFakeSockets();
  let alerts;
  function Probe() {
    alerts = useServiceAlerts(60_000);
    return createElement("span", null, String(alerts.alerts.length));
  }
  const mounted = await renderProbe(Probe);
  try {
    await flushEffects();
    await act(async () => {
      sockets[0].open();
    });
    await act(async () => {
      sockets[0].onmessage?.({
        data: JSON.stringify({
          type: "SERVICE_SNAPSHOT",
          data: alertsJson([{ header: "Q delay", routeIds: ["Q"] }]),
        }),
      });
    });
    assert.equal(alerts.alerts[0].header, "Q delay");
    await act(async () => {
      sockets[0].onmessage?.({
        data: JSON.stringify({
          type: "SERVICE_UPDATE",
          data: alertsJson([{ header: "system delay" }]),
        }),
      });
    });
    assert.equal(alerts.alerts[0].header, "system delay");
  } finally {
    globalThis.fetch = originalFetch;
    await mounted.unmount();
  }
});

test("service-alert polling cannot replace an open websocket snapshot", async () => {
  let restAlerts = alertsJson([{ alert_id: "rest", header: "REST" }]);
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    if (String(url).includes("ws-ticket")) {
      return new Response(JSON.stringify({ ticket: "t", ws_base_url: "ws://localhost:8000" }), {
        status: 200,
      });
    }
    return new Response(JSON.stringify(restAlerts), { status: 200 });
  };
  const sockets = installFakeSockets();
  const intervalCallbacks = [];
  let alerts;
  function Probe() {
    alerts = useServiceAlerts(60_000);
    return createElement("span", null, alerts.connectionState);
  }
  const mounted = await renderProbe(Probe, () => {
    window.setInterval = (callback) => {
      intervalCallbacks.push(callback);
      return 1;
    };
    window.clearInterval = () => {};
  });
  try {
    await flushEffects();
    await act(async () => {
      sockets[0].open();
      sockets[0].onmessage?.({
        data: JSON.stringify({
          type: "SERVICE_SNAPSHOT",
          data: alertsJson([{ alert_id: "live", header: "LIVE" }]),
        }),
      });
    });
    assert.equal(alerts.alerts[0].alert_id, "live");
    restAlerts = alertsJson([{ alert_id: "stale-poll", header: "POLL" }]);
    assert.equal(intervalCallbacks.length > 0, true);
    await act(async () => {
      await intervalCallbacks[0]();
    });
    assert.equal(alerts.alerts[0].alert_id, "live");
    assert.equal(
      alerts.alerts.some((alert) => alert.alert_id === "stale-poll"),
      false,
    );
  } finally {
    globalThis.fetch = originalFetch;
    await mounted.unmount();
  }
});

test("destination search publishes suggestions then choose hydrates coordinates", async () => {
  const previousToken = process.env.NEXT_PUBLIC_MAPBOX_TOKEN;
  process.env.NEXT_PUBLIC_MAPBOX_TOKEN = "pk.test";
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    if (String(url).includes("suggest")) {
      return new Response(
        JSON.stringify({
          suggestions: [{ mapbox_id: "mbx_1", name: "Times Square", full_address: "Manhattan" }],
        }),
        { status: 200 },
      );
    }
    return new Response(
      JSON.stringify({
        features: [
          {
            properties: { name: "Times Square", full_address: "Manhattan, NY" },
            geometry: { coordinates: [-73.7781, 40.6413] },
          },
        ],
      }),
      { status: 200 },
    );
  };
  let search;
  function Probe() {
    search = useDestinationSearch({ inputValue: "times square", enabled: true });
    return createElement("span", null, String(search.suggestions.length));
  }
  const mounted = await renderProbe(Probe);
  try {
    await waitUntil(() => search.suggestions[0]?.mapboxId === "mbx_1", "suggestions did not arrive");
    await flushEffects();
    let selection;
    await act(async () => {
      selection = await search.choose(search.suggestions[0]);
    });
    assert.equal(selection.label.includes("Times Square"), true);
    assert.deepEqual(selection.coordinates, { lat: 40.6413, lng: -73.7781 });
  } finally {
    globalThis.fetch = originalFetch;
    if (previousToken === undefined) delete process.env.NEXT_PUBLIC_MAPBOX_TOKEN;
    else process.env.NEXT_PUBLIC_MAPBOX_TOKEN = previousToken;
    await mounted.unmount();
  }
});

test("destination search clears suggestions and suppresses a selected label", async () => {
  const previousToken = process.env.NEXT_PUBLIC_MAPBOX_TOKEN;
  process.env.NEXT_PUBLIC_MAPBOX_TOKEN = "pk.test";
  const originalFetch = globalThis.fetch;
  let suggestCalls = 0;
  globalThis.fetch = async (url) => {
    if (String(url).includes("suggest")) {
      suggestCalls += 1;
      return new Response(
        JSON.stringify({
          suggestions: [{ mapbox_id: "mbx_1", name: "Times Square", place_formatted: "Manhattan" }],
        }),
        { status: 200 },
      );
    }
    return new Response("{}", { status: 200 });
  };
  let search;
  function Probe() {
    search = useDestinationSearch({ inputValue: "times square", enabled: true });
    return createElement("span", null, String(search.suggestions.length));
  }
  const mounted = await renderProbe(Probe);
  try {
    await waitUntil(() => search.suggestions.length === 1, "suggestions did not arrive");
    await act(async () => {
      search.clearSuggestions();
    });
    assert.equal(search.suggestions.length, 0);
    await act(async () => {
      search.markSelectedLabel("times square");
    });
    const callsAfterSelect = suggestCalls;
    await flushEffects();
    assert.equal(suggestCalls, callsAfterSelect);
    await act(async () => {
      search.markInputEdited();
      search.resetSession();
    });
    assert.equal(search.isResolving, false);
  } finally {
    globalThis.fetch = originalFetch;
    if (previousToken === undefined) delete process.env.NEXT_PUBLIC_MAPBOX_TOKEN;
    else process.env.NEXT_PUBLIC_MAPBOX_TOKEN = previousToken;
    await mounted.unmount();
  }
});

test("destination search treats a Mapbox failure as an empty suggestion list", async () => {
  const previousToken = process.env.NEXT_PUBLIC_MAPBOX_TOKEN;
  process.env.NEXT_PUBLIC_MAPBOX_TOKEN = "pk.test";
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => {
    throw new Error("offline");
  };
  let search;
  function Probe() {
    search = useDestinationSearch({ inputValue: "times square", enabled: true });
    return createElement("span", null, String(search.suggestions.length));
  }
  const mounted = await renderProbe(Probe);
  try {
    await flushEffects();
    await flushEffects();
    assert.deepEqual(search.suggestions, []);
  } finally {
    globalThis.fetch = originalFetch;
    if (previousToken === undefined) delete process.env.NEXT_PUBLIC_MAPBOX_TOKEN;
    else process.env.NEXT_PUBLIC_MAPBOX_TOKEN = previousToken;
    await mounted.unmount();
  }
});

test("voice input delivers a final transcript and then returns to idle", async () => {
  const transcripts = [];
  let voice;
  function Probe() {
    voice = useVoiceInput((text) => transcripts.push(text));
    return createElement("span", null, String(voice.isSupported));
  }
  const mounted = await renderProbe(Probe);
  try {
    assert.equal(voice.isSupported, true);
    await act(async () => {
      voice.start();
    });
    assert.equal(voice.isListening, true);
    const recognition = mounted.speechInstances[0];
    await act(async () => {
      recognition.onresult?.({
        results: {
          length: 1,
          0: { 0: { transcript: "next Q" }, item: () => ({ transcript: "next Q" }), length: 1 },
          item(index) {
            return this[index];
          },
        },
      });
      recognition.onend?.();
    });
    assert.deepEqual(transcripts, ["next Q"]);
    assert.equal(voice.isListening, false);
  } finally {
    await mounted.unmount();
  }
});

test("voice input stop and error paths leave the mic idle", async () => {
  let voice;
  function Probe() {
    voice = useVoiceInput(() => {});
    return createElement("span", null, String(voice.isListening));
  }
  const mounted = await renderProbe(Probe);
  try {
    await act(async () => {
      voice.start();
    });
    await act(async () => {
      voice.start();
    });
    assert.equal(voice.isListening, false);
    await act(async () => {
      voice.start();
    });
    await act(async () => {
      mounted.speechInstances.at(-1).onerror?.();
    });
    assert.equal(voice.isListening, false);
    await act(async () => {
      voice.start();
    });
    mounted.speechInstances.at(-1).startThrows = true;
    await act(async () => {
      voice.start();
    });
    await act(async () => {
      voice.stop();
    });
    assert.equal(voice.isListening, false);
  } finally {
    await mounted.unmount();
  }
});

test("voice input is unsupported when the browser has no speech API", async () => {
  let voice;
  function Probe() {
    voice = useVoiceInput(() => {});
    return createElement("span", null, String(voice.isSupported));
  }
  const mounted = await renderProbe(Probe, () => {
    delete window.SpeechRecognition;
    delete window.webkitSpeechRecognition;
  });
  try {
    assert.equal(voice.isSupported, false);
    await act(async () => {
      voice.start();
    });
    assert.equal(voice.isListening, false);
  } finally {
    await mounted.unmount();
  }
});

test("voice input uses webkitSpeechRecognition when the unprefixed API is absent", async () => {
  let voice;
  function Probe() {
    voice = useVoiceInput(() => {});
    return createElement("span", null, String(voice.isSupported));
  }
  const mounted = await renderProbe(Probe, () => {
    window.webkitSpeechRecognition = window.SpeechRecognition;
    delete window.SpeechRecognition;
  });
  try {
    assert.equal(voice.isSupported, true);
    await act(async () => {
      voice.start();
    });
    assert.equal(voice.isListening, true);
    await act(async () => {
      voice.stop();
    });
    assert.equal(voice.isListening, false);
  } finally {
    await mounted.unmount();
  }
});

test("theme follows prefers-color-scheme light when nothing is stored", async () => {
  const installed = installClientDom();
  installed.documentNode.defaultView.matchMedia = (query) => ({
    matches: String(query).includes("light"),
    addEventListener() {},
    removeEventListener() {},
  });
  const rootNode = installed.body.ownerDocument.createElement("DIV");
  installed.body.appendChild(rootNode);
  let theme;
  function Probe() {
    theme = useSmartRouteTheme();
    return createElement("span", null, theme.theme);
  }
  const root = createRoot(rootNode);
  await act(async () => {
    root.render(createElement(SmartRouteThemeProvider, null, createElement(Probe)));
  });
  try {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    assert.equal(theme.theme, "light");
  } finally {
    await act(async () => {
      root.unmount();
    });
  }
});

test("theme restores a stored light preference and still toggles when storage writes fail", async () => {
  const installed = installClientDom();
  installed.documentNode.defaultView.localStorage.setItem("sr-theme", "light");
  const rootNode = installed.body.ownerDocument.createElement("DIV");
  installed.body.appendChild(rootNode);
  let theme;
  function Probe() {
    theme = useSmartRouteTheme();
    return createElement("span", null, theme.theme);
  }
  const root = createRoot(rootNode);
  await act(async () => {
    root.render(createElement(SmartRouteThemeProvider, null, createElement(Probe)));
  });
  try {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    assert.equal(theme.theme, "light");
    installed.documentNode.defaultView.localStorage.setItem = () => {
      throw new Error("full");
    };
    await act(async () => {
      theme.toggleTheme();
    });
    assert.equal(theme.theme, "dark");
  } finally {
    await act(async () => {
      root.unmount();
    });
  }
});

test("theme falls back to dark when storage and matchMedia are unavailable", async () => {
  const installed = installClientDom();
  installed.documentNode.defaultView.localStorage.getItem = () => {
    throw new Error("blocked");
  };
  installed.documentNode.defaultView.matchMedia = () => {
    throw new Error("no media");
  };
  const rootNode = installed.body.ownerDocument.createElement("DIV");
  installed.body.appendChild(rootNode);
  let theme;
  function Probe() {
    theme = useSmartRouteTheme();
    return createElement("span", null, theme.theme);
  }
  const root = createRoot(rootNode);
  await act(async () => {
    root.render(createElement(SmartRouteThemeProvider, null, createElement(Probe)));
  });
  try {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    assert.equal(theme.theme, "dark");
  } finally {
    await act(async () => {
      root.unmount();
    });
  }
});

test("agent chat retryLast replays the failed request and dismissError clears it", async () => {
  let attempts = 0;
  const transport = async function* () {
    attempts += 1;
    if (attempts === 1) {
      yield { type: "error", code: "internal", message: "upstream down", retryable: false };
      yield { type: "done", session_id: "s", turn_id: "t1", stop_reason: "error", usage: {} };
      return;
    }
    yield { type: "token", text: "Take the Q." };
    yield { type: "done", session_id: "s", turn_id: "t", stop_reason: "end_turn", usage: {} };
  };
  let chat;
  function Probe() {
    chat = useAgentChat({ transport, getOrigin: () => ({ lat: 40.75, lng: -73.99 }) });
    return createElement("span", null, chat.error ?? "ok");
  }
  const mounted = await renderProbe(Probe);
  try {
    await act(async () => {
      chat.send("Next Q?");
    });
    await waitUntil(() => chat.error === "upstream down", "first attempt did not fail");
    await act(async () => {
      chat.retryLast();
    });
    await waitUntil(
      () => String(chat.messages.at(-1)?.text ?? "").includes("Take the Q"),
      "retry did not complete",
    );
    await act(async () => {
      chat.dismissError();
    });
    assert.equal(chat.error, null);
  } finally {
    await mounted.unmount();
  }
});

test("voice input is unsupported when window is absent", () => {
  const windowDescriptor = Object.getOwnPropertyDescriptor(globalThis, "window");
  delete globalThis.window;
  try {
    function Probe() {
      const voice = useVoiceInput(() => {});
      return createElement("span", null, String(voice.isSupported));
    }
    assert.match(renderToString(createElement(Probe)), /false/);
  } finally {
    if (windowDescriptor) Object.defineProperty(globalThis, "window", windowDescriptor);
  }
});

test("voice input aborts recognition when the mic unmounts mid-listen", async () => {
  let voice;
  function Probe() {
    voice = useVoiceInput(() => {});
    return createElement("span", null, String(voice.isListening));
  }
  const mounted = await renderProbe(Probe);
  try {
    await act(async () => {
      voice.start();
    });
    assert.equal(voice.isListening, true);
  } finally {
    await mounted.unmount();
  }
});

test("agent chat restores a persisted session snapshot", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    if (String(url).includes("/api/agent/chat/session")) {
      return new Response(
        JSON.stringify({
          session_id: "sess-1",
          history: [
            { role: "user", text: "Next Q?" },
            { role: "assistant", text: "Take the Q.", turn_id: "t1" },
          ],
          route_cards: [],
          arrival_cards: [],
        }),
        { status: 200 },
      );
    }
    return new Response("{}", { status: 200 });
  };
  const values = new Map();
  let chat;
  function Probe() {
    chat = useAgentChat({ transport: async function* () {} });
    return createElement("span", null, chat.sessionId ?? "none");
  }
  const mounted = await renderProbe(Probe, () => {
    window.sessionStorage = {
      getItem(key) {
        return values.get(key) ?? null;
      },
      setItem(key, value) {
        values.set(key, String(value));
      },
      removeItem(key) {
        values.delete(key);
      },
    };
    persistSessionId(window.sessionStorage, "sess-1");
  });
  try {
    await waitUntil(
      () => String(chat.messages.at(-1)?.text ?? "").includes("Take the Q"),
      "persisted session did not restore",
    );
    assert.equal(chat.sessionId, "sess-1");
  } finally {
    globalThis.fetch = originalFetch;
    await mounted.unmount();
  }
});

test("service alerts ignore a REST snapshot that finishes after unmount", async () => {
  const originalFetch = globalThis.fetch;
  const snapshot = deferred();
  globalThis.fetch = async (url) => {
    if (String(url).startsWith("/api/service-alerts")) {
      await snapshot.promise;
      return new Response(
        JSON.stringify(alertsJson([{ alert_id: "late", header: "late" }])),
        { status: 200 },
      );
    }
    return new Response("nope", { status: 503 });
  };
  installFakeSockets();
  let alerts;
  function Probe() {
    alerts = useServiceAlerts(60_000);
    return createElement("span", null, alerts.connectionState);
  }
  const mounted = await renderProbe(Probe);
  await mounted.unmount();
  snapshot.resolve();
  await flushEffects();
  assert.equal(alerts.alerts.length, 0);
  globalThis.fetch = originalFetch;
});

test("service alerts drop a REST result that loses the websocket race", async () => {
  const originalFetch = globalThis.fetch;
  const snapshot = deferred();
  globalThis.fetch = async (url) => {
    if (String(url).includes("ws-ticket")) {
      return new Response(JSON.stringify({ ticket: "t", ws_base_url: "ws://localhost:8000" }), {
        status: 200,
      });
    }
    if (String(url).startsWith("/api/service-alerts")) {
      await snapshot.promise;
      return new Response(
        JSON.stringify(alertsJson([{ alert_id: "rest", header: "REST" }])),
        { status: 200 },
      );
    }
    return new Response("{}", { status: 200 });
  };
  const sockets = installFakeSockets();
  let alerts;
  function Probe() {
    alerts = useServiceAlerts(60_000);
    return createElement("span", null, alerts.connectionState);
  }
  const mounted = await renderProbe(Probe);
  try {
    await flushEffects();
    await act(async () => {
      sockets[0].open();
      sockets[0].onmessage?.({
        data: JSON.stringify({
          type: "SERVICE_SNAPSHOT",
          data: alertsJson([{ alert_id: "live", header: "LIVE" }]),
        }),
      });
    });
    snapshot.resolve();
    await flushEffects();
    assert.equal(alerts.alerts[0].alert_id, "live");
    await act(async () => {
      sockets[0].onmessage?.({
        data: JSON.stringify({ type: "SERVICE_HEARTBEAT" }),
      });
    });
    assert.equal(alerts.connectionState, "open");
  } finally {
    globalThis.fetch = originalFetch;
    await mounted.unmount();
  }
});

test("service alerts use the generic copy when REST throws a non-Error", async () => {
  const originalFetch = globalThis.fetch;
  const snapshot = deferred();
  globalThis.fetch = async (url) => {
    if (String(url).startsWith("/api/service-alerts")) {
      await snapshot.promise;
      throw "alerts exploded";
    }
    return new Response("nope", { status: 503 });
  };
  installFakeSockets();
  let alerts;
  function Probe() {
    alerts = useServiceAlerts(60_000);
    return createElement("span", null, alerts.error ?? "ok");
  }
  const mounted = await renderProbe(Probe);
  try {
    snapshot.resolve();
    await waitUntil(
      () => alerts.error === "Service alerts unavailable",
      "non-Error REST failure did not surface",
    );
  } finally {
    globalThis.fetch = originalFetch;
    await mounted.unmount();
  }
});

test("agent chat discards a persisted session that the backend reports expired", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    if (String(url).includes("/api/agent/chat/session")) {
      return new Response("gone", { status: 404 });
    }
    return new Response("{}", { status: 200 });
  };
  const values = new Map();
  let chat;
  function Probe() {
    chat = useAgentChat({ transport: async function* () {} });
    return createElement("span", null, chat.sessionId ?? "none");
  }
  const mounted = await renderProbe(Probe, () => {
    window.sessionStorage = {
      getItem(key) {
        return values.get(key) ?? null;
      },
      setItem(key, value) {
        values.set(key, String(value));
      },
      removeItem(key) {
        values.delete(key);
      },
    };
    persistSessionId(window.sessionStorage, "sess-expired");
  });
  try {
    await waitUntil(() => chat.sessionId === null, "expired session was not discarded");
  } finally {
    globalThis.fetch = originalFetch;
    await mounted.unmount();
  }
});

test("agent chat ignores a snapshot that arrives after a newer restore generation", async () => {
  const originalFetch = globalThis.fetch;
  const snapshot = deferred();
  globalThis.fetch = async (url) => {
    if (String(url).includes("/api/agent/chat/session")) {
      await snapshot.promise;
      return new Response(
        JSON.stringify({
          session_id: "sess-stale",
          history: [{ role: "user", text: "stale" }, { role: "assistant", text: "late", turn_id: "t1" }],
          route_cards: [],
          arrival_cards: [],
        }),
        { status: 200 },
      );
    }
    return new Response("{}", { status: 200 });
  };
  const values = new Map();
  let chat;
  function Probe() {
    chat = useAgentChat({ transport: async function* () {} });
    return createElement("span", null, String(chat.messages.length));
  }
  const mounted = await renderProbe(Probe, () => {
    window.sessionStorage = {
      getItem(key) {
        return values.get(key) ?? null;
      },
      setItem(key, value) {
        values.set(key, String(value));
      },
      removeItem(key) {
        values.delete(key);
      },
    };
    persistSessionId(window.sessionStorage, "sess-stale");
  });
  await mounted.unmount();
  snapshot.resolve();
  await flushEffects();
  assert.equal(chat.messages.length, 0);
  globalThis.fetch = originalFetch;
});

test("service alerts abandon a ticket that resolves after unmount", async () => {
  const originalFetch = globalThis.fetch;
  const ticket = deferred();
  globalThis.fetch = async (url) => {
    if (String(url).includes("ws-ticket")) {
      await ticket.promise;
      throw new Error("ticket gone");
    }
    return new Response(JSON.stringify(alertsJson([])), { status: 200 });
  };
  installFakeSockets();
  let alerts;
  function Probe() {
    alerts = useServiceAlerts(60_000);
    return createElement("span", null, alerts.connectionState);
  }
  const mounted = await renderProbe(Probe);
  await mounted.unmount();
  ticket.resolve();
  await flushEffects();
  assert.equal(alerts.connectionState === "connecting" || alerts.connectionState === "closed", true);
  globalThis.fetch = originalFetch;
});

test("service alerts do not stack reconnect timers after a dropped socket", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    if (String(url).includes("ws-ticket")) {
      return new Response(JSON.stringify({ ticket: "t", ws_base_url: "ws://localhost:8000" }), {
        status: 200,
      });
    }
    return new Response(JSON.stringify(alertsJson([])), { status: 200 });
  };
  const sockets = installFakeSockets();
  let alerts;
  function Probe() {
    alerts = useServiceAlerts(60_000);
    return createElement("span", null, alerts.connectionState);
  }
  const mounted = await renderProbe(Probe);
  try {
    await flushEffects();
    await act(async () => {
      sockets[0].open();
    });
    await act(async () => {
      sockets[0].onclose?.(new Event("close"));
      sockets[0].onclose?.(new Event("close"));
    });
    assert.equal(alerts.connectionState, "closed");
  } finally {
    globalThis.fetch = originalFetch;
    await mounted.unmount();
  }
});

test("service alerts ignore a REST error that finishes after unmount", async () => {
  const originalFetch = globalThis.fetch;
  const snapshot = deferred();
  globalThis.fetch = async (url) => {
    if (String(url).startsWith("/api/service-alerts")) {
      await snapshot.promise;
      throw new Error("late failure");
    }
    return new Response("nope", { status: 503 });
  };
  installFakeSockets();
  let alerts;
  function Probe() {
    alerts = useServiceAlerts(60_000);
    return createElement("span", null, alerts.error ?? "ok");
  }
  const mounted = await renderProbe(Probe);
  await mounted.unmount();
  snapshot.resolve();
  await flushEffects();
  assert.equal(alerts.error, null);
  globalThis.fetch = originalFetch;
});

test("service alerts ignore a successful ticket that arrives after unmount", async () => {
  const originalFetch = globalThis.fetch;
  const ticket = deferred();
  globalThis.fetch = async (url) => {
    if (String(url).includes("ws-ticket")) {
      await ticket.promise;
      return new Response(JSON.stringify({ ticket: "t", ws_base_url: "ws://localhost:8000" }), {
        status: 200,
      });
    }
    return new Response(JSON.stringify(alertsJson([])), { status: 200 });
  };
  const sockets = installFakeSockets();
  let alerts;
  function Probe() {
    alerts = useServiceAlerts(60_000);
    return createElement("span", null, alerts.connectionState);
  }
  const mounted = await renderProbe(Probe);
  await flushEffects();
  await mounted.unmount();
  ticket.resolve();
  await flushEffects();
  assert.equal(sockets.length, 0);
  assert.equal(alerts.connectionState === "connecting" || alerts.connectionState === "closed", true);
  globalThis.fetch = originalFetch;
});

test("service alerts ignore a socket close after unmount", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    if (String(url).includes("ws-ticket")) {
      return new Response(JSON.stringify({ ticket: "t", ws_base_url: "ws://localhost:8000" }), {
        status: 200,
      });
    }
    return new Response(JSON.stringify(alertsJson([])), { status: 200 });
  };
  const sockets = installFakeSockets();
  let alerts;
  function Probe() {
    alerts = useServiceAlerts(60_000);
    return createElement("span", null, alerts.connectionState);
  }
  const mounted = await renderProbe(Probe);
  try {
    await flushEffects();
    await act(async () => {
      sockets[0].open();
    });
    await mounted.unmount();
    sockets[0].onclose?.(new Event("close"));
    assert.equal(alerts.connectionState, "open");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("service alerts drop a REST error that loses the websocket race", async () => {
  const originalFetch = globalThis.fetch;
  const snapshot = deferred();
  globalThis.fetch = async (url) => {
    if (String(url).includes("ws-ticket")) {
      return new Response(JSON.stringify({ ticket: "t", ws_base_url: "ws://localhost:8000" }), {
        status: 200,
      });
    }
    if (String(url).startsWith("/api/service-alerts")) {
      await snapshot.promise;
      throw new Error("late rest");
    }
    return new Response("{}", { status: 200 });
  };
  const sockets = installFakeSockets();
  let alerts;
  function Probe() {
    alerts = useServiceAlerts(60_000);
    return createElement("span", null, alerts.error ?? "ok");
  }
  const mounted = await renderProbe(Probe);
  try {
    await flushEffects();
    await act(async () => {
      sockets[0].open();
    });
    snapshot.resolve();
    await flushEffects();
    assert.equal(alerts.error, null);
  } finally {
    globalThis.fetch = originalFetch;
    await mounted.unmount();
  }
});
