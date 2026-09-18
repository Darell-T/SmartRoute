import assert from "node:assert/strict";
import Module from "node:module";
import { register } from "node:module";
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import test from "node:test";

import { SmartRouteThemeProvider, useSmartRouteTheme } from "@/lib/hooks/use-chat-theme";
import { useVoiceInput } from "@/lib/hooks/use-voice-input";

const cssStubLoader = `
export function resolve(specifier, context, nextResolve) {
  if (typeof specifier === "string" && specifier.includes(".css")) {
    return { shortCircuit: true, url: "data:text/javascript,export default {}" };
  }
  if (specifier === "next/font/local") {
    return {
      shortCircuit: true,
      url: "data:text/javascript,export default function localFont(o={}){return {className:'test-font',variable:o.variable||'--test-font',style:{fontFamily:'test-font'}}}",
    };
  }
  if (specifier === "next/image") {
    return {
      shortCircuit: true,
      url: "data:text/javascript,import{createElement as h}from 'react';export default function Image(p){return h('img',{src:String(p.src||''),alt:String(p.alt||'')})}",
    };
  }
  if (specifier === "next/dynamic") {
    return {
      shortCircuit: true,
      url: "data:text/javascript,export default function dynamic(){return function DynamicComponent(){return null}}",
    };
  }
  if (specifier === "@vercel/analytics/next") {
    return {
      shortCircuit: true,
      url: "data:text/javascript,export function Analytics(){return null}",
    };
  }
  return nextResolve(specifier, context);
}
export function load(url, context, nextLoad) {
  if (typeof url === "string" && url.includes(".css")) {
    return { format: "module", shortCircuit: true, source: "export default {}" };
  }
  return nextLoad(url, context);
}
`;

register(`data:text/javascript,${encodeURIComponent(cssStubLoader)}`, import.meta.url);

const originalLoad = Module._load;
const NEXT_STUB_NEEDLES = [
  [".css", "css"],
  ["next/font/local", "font"],
  ["image-component", "image"],
  ["next/image", "image"],
  ["app-dynamic", "dynamic"],
  ["next/dynamic", "dynamic"],
  ["deployment-id", "dpl"],
  ["@vercel/analytics/next", "analytics"],
];

function nextStubKind(request) {
  for (const [needle, kind] of NEXT_STUB_NEEDLES) {
    if (request.includes(needle)) return kind;
  }
  return null;
}

function nextStubModule(kind) {
  if (kind === "css") return {};
  if (kind === "font") {
    return {
      __esModule: true,
      default(options = {}) {
        return {
          className: "test-font",
          variable: options.variable ?? "--test-font",
          style: { fontFamily: "test-font" },
        };
      },
    };
  }
  if (kind === "image") {
    return {
      __esModule: true,
      default(props) {
        return createElement("img", { src: String(props.src ?? ""), alt: String(props.alt ?? "") });
      },
    };
  }
  if (kind === "dynamic") {
    return {
      __esModule: true,
      default() {
        return function DynamicComponent() {
          return null;
        };
      },
    };
  }
  if (kind === "dpl") {
    return {
      __esModule: true,
      getDeploymentId() {},
      getDeploymentIdQuery() {
        return "";
      },
      getAssetToken() {},
      getAssetTokenQuery() {
        return "";
      },
    };
  }
  return {
    __esModule: true,
    Analytics() {
      return null;
    },
  };
}

Module._load = function loadWithoutCss(request, parent, isMain) {
  if (typeof request !== "string") return originalLoad.call(this, request, parent, isMain);
  const kind = nextStubKind(request);
  if (!kind) return originalLoad.call(this, request, parent, isMain);
  return nextStubModule(kind);
};
Module._extensions[".css"] = function cssStub(module) {
  module.exports = {};
};

function installBrowserStubs() {
  const storage = new Map();
  const windowStub = {
    localStorage: {
      getItem(key) {
        return storage.has(key) ? storage.get(key) : null;
      },
      setItem(key, value) {
        storage.set(key, String(value));
      },
    },
    matchMedia() {
      return {
        matches: false,
        addEventListener() {},
        removeEventListener() {},
      };
    },
    requestAnimationFrame() {
      return 1;
    },
    cancelAnimationFrame() {},
    addEventListener() {},
    removeEventListener() {},
    innerHeight: 800,
    visualViewport: {
      height: 640,
      offsetTop: 0,
      addEventListener() {},
      removeEventListener() {},
    },
    location: { origin: "http://localhost:3000", hostname: "localhost" },
    setInterval() {
      return 1;
    },
    clearInterval() {},
    setTimeout() {
      return 1;
    },
    clearTimeout() {},
  };
  globalThis.window = windowStub;
  globalThis.document = {
    documentElement: { style: { setProperty() {} }, dataset: {} },
    querySelector() {
      return null;
    },
    fullscreenElement: null,
    addEventListener() {},
    removeEventListener() {},
  };
  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    value: { geolocation: undefined },
  });
  globalThis.WebSocket = class {
    constructor() {
      this.readyState = 3;
    }
    close() {}
    addEventListener() {}
    removeEventListener() {}
  };
  globalThis.fetch = async () =>
    new Response(JSON.stringify({ error: "unavailable" }), { status: 503 });
}

test("layout metadata names SmartRoute and RootLayout renders children", async () => {
  const layout = await import("./layout.tsx");
  assert.equal(layout.metadata.applicationName, "SmartRoute");
  assert.equal(layout.viewport.themeColor, "#0d1117");
  const html = renderToString(
    createElement(layout.default, null, createElement("main", null, "ok")),
  );
  assert.match(html, /lang="en"/);
  assert.match(html, />ok</);
});

test("root layout mounts analytics on a Vercel deployment", async () => {
  const previous = process.env.VERCEL;
  process.env.VERCEL = "1";
  try {
    const layout = await import(`./layout.tsx?vercel=1`);
    const html = renderToString(
      createElement(layout.default, null, createElement("main", null, "ok")),
    );
    assert.match(html, />ok</);
  } finally {
    if (previous === undefined) delete process.env.VERCEL;
    else process.env.VERCEL = previous;
  }
});

test("the workspace page renders the chat shell without a destination", async () => {
  installBrowserStubs();
  const { default: Page } = await import("./page.tsx");
  const html = renderToString(createElement(Page));
  assert.match(html, /data-tab="chat"/);
  assert.match(html, /sr-tab-shell__panel--hidden/);
  assert.match(html, /SmartRoute/);
});

test("theme provider is required and starts dark on the first render", () => {
  installBrowserStubs();
  function Probe() {
    const { theme } = useSmartRouteTheme();
    return createElement("span", null, theme);
  }
  assert.throws(
    () => renderToString(createElement(Probe)),
    /must be used within SmartRouteThemeProvider/,
  );
  const html = renderToString(
    createElement(SmartRouteThemeProvider, null, createElement(Probe)),
  );
  assert.match(html, /dark/);
});

test("voice input is unsupported during SSR and start is a no-op", () => {
  const windowDescriptor = Object.getOwnPropertyDescriptor(globalThis, "window");
  delete globalThis.window;
  let api;
  function Probe() {
    api = useVoiceInput(() => {});
    return createElement("span", null, String(api.isSupported));
  }
  try {
    const html = renderToString(createElement(Probe));
    assert.match(html, /false/);
    assert.equal(api.isListening, false);
    api.start();
    assert.equal(api.isListening, false);
    api.stop();
    assert.equal(api.isListening, false);
  } finally {
    if (windowDescriptor) Object.defineProperty(globalThis, "window", windowDescriptor);
  }
});

function createPageNode(name, ownerDocument) {
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
    value: "",
    width: 300,
    height: 150,
    innerHTML: "",
    attributes: {},
    _listeners: {},
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
    addEventListener(type, handler) {
      (this._listeners[type] ||= []).push(handler);
    },
    removeEventListener(type, handler) {
      this._listeners[type] = (this._listeners[type] || []).filter((entry) => entry !== handler);
    },
    contains() {
      return false;
    },
    focus() {},
    blur() {},
    click() {
      dispatchClick(this);
    },
    querySelector(selector) {
      return queryAll(this, selector)[0] ?? null;
    },
    querySelectorAll(selector) {
      return queryAll(this, selector);
    },
    getBoundingClientRect() {
      return { x: 0, y: 0, top: 0, left: 0, bottom: 0, right: 0, width: 0, height: 0 };
    },
    getContext() {
      const canvas = this;
      return new Proxy(
        { canvas },
        {
          get(target, prop) {
            if (prop in target) return target[prop];
            return () => undefined;
          },
        },
      );
    },
  };
  Object.defineProperty(node, "firstChild", { writable: true, value: null });
  Object.defineProperty(node, "lastChild", { writable: true, value: null });
  Object.defineProperty(node, "nextSibling", { writable: true, value: null });
  Object.defineProperty(node, "previousSibling", { writable: true, value: null });
  return node;
}

function matchSelector(node, selector) {
  const labeled = /^\[([^\]]+)="([^"]*)"\]$/.exec(selector);
  if (labeled) return node.getAttribute?.(labeled[1]) === labeled[2];
  const present = /^\[([^\]]+)\]$/.exec(selector);
  if (present) return node.hasAttribute?.(present[1]);
  if (selector.startsWith(".")) {
    return String(node.className || "").split(/\s+/).includes(selector.slice(1));
  }
  return node.tagName === selector.toUpperCase() || node.nodeName === selector.toUpperCase();
}

function queryAll(root, selector) {
  const found = [];
  const walk = (node) => {
    if (matchSelector(node, selector)) found.push(node);
    for (const child of node.childNodes || []) walk(child);
  };
  walk(root);
  return found;
}

function dispatchClick(node) {
  const event = new Event("click", { bubbles: true, cancelable: true });
  Object.defineProperty(event, "target", { configurable: true, value: node });
  let current = node;
  while (current) {
    for (const handler of current._listeners?.click || []) handler.call(current, event);
    current = current.parentNode;
  }
}

function installPageDom() {
  const storage = new Map();
  const documentNode = createPageNode("#document", null);
  const html = createPageNode("HTML", documentNode);
  const body = createPageNode("BODY", documentNode);
  const head = createPageNode("HEAD", documentNode);
  documentNode.ownerDocument = documentNode;
  documentNode.documentElement = html;
  documentNode.body = body;
  documentNode.head = head;
  documentNode.createElement = (name) => createPageNode(String(name).toUpperCase(), documentNode);
  documentNode.createElementNS = (_ns, name) => documentNode.createElement(name);
  documentNode.createTextNode = (text) => {
    const node = createPageNode("#text", documentNode);
    node.nodeType = 3;
    node.textContent = String(text);
    return node;
  };
  documentNode.createComment = () => createPageNode("#comment", documentNode);
  documentNode.querySelector = (selector) => queryAll(documentNode, selector)[0] ?? null;
  documentNode.querySelectorAll = (selector) => queryAll(documentNode, selector);
  documentNode.getElementById = () => null;
  html.appendChild(head);
  html.appendChild(body);
  documentNode.appendChild(html);
  const windowStub = {
    document: documentNode,
    HTMLElement: class HTMLElement {},
    HTMLIFrameElement: class HTMLIFrameElement {},
    SVGElement: class SVGElement {},
    Node: class Node {},
    Event,
    getComputedStyle() {
      return {
        getPropertyValue() {
          return "0px";
        },
        overflowY: "visible",
        overflowX: "visible",
      };
    },
    ResizeObserver: class ResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
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
    matchMedia(query) {
      return {
        matches: String(query).includes("prefers-reduced-motion"),
        addEventListener() {},
        removeEventListener() {},
      };
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
    setInterval() {
      return 1;
    },
    clearInterval() {},
    setTimeout,
    clearTimeout,
    visualViewport: {
      height: 640,
      offsetTop: 0,
      addEventListener() {},
      removeEventListener() {},
    },
  };
  documentNode.defaultView = windowStub;
  globalThis.window = windowStub;
  globalThis.document = documentNode;
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  globalThis.ResizeObserver = windowStub.ResizeObserver;
  globalThis.getComputedStyle = windowStub.getComputedStyle;
  globalThis.requestAnimationFrame = windowStub.requestAnimationFrame;
  globalThis.cancelAnimationFrame = windowStub.cancelAnimationFrame;
  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    value: {
      geolocation: {
        getCurrentPosition(success) {
          success({ coords: { latitude: 40.75, longitude: -73.99 } });
        },
      },
    },
  });
  return { documentNode, body };
}

function routeCardSse(route = [{ type: "WALK", end_point: { latitude: 40.64, longitude: -73.78 } }]) {
  const card = {
    card_id: route.length === 0 ? "rc_empty" : "rc_1",
    turn_id: route.length === 0 ? "t2" : "t1",
    role: "recommended",
    origin: { label: "Origin", lat: 40.75, lng: -73.99 },
    destination: { label: "JFK", lat: 40.64, lng: -73.78 },
    summary: { eta_minutes: 34, transfers: 0, lines: ["A"], reason: "Server reason" },
    alerts: [],
    route,
    itinerary: {
      itinerary_id: "it_1",
      total_duration_seconds: 2040,
      transfer_count: 0,
      arrival_at: "2026-07-16T15:45:00-04:00",
      legs: [{ mode: "WALK", walk_seconds: 2040 }],
    },
  };
  const turnId = card.turn_id;
  const sessionId = turnId === "t2" ? "s2" : "s1";
  return (
    `event: meta\ndata: {"session_id":"${sessionId}","turn_id":"${turnId}"}\n\n` +
    'event: token\ndata: {"text":"Take the A."}\n\n' +
    `event: route_card\ndata: ${JSON.stringify(card)}\n\n` +
    `event: done\ndata: {"session_id":"${sessionId}","turn_id":"${turnId}","stop_reason":"end_turn","usage":{}}\n\n`
  );
}

async function flushPage() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

async function waitForLabel(root, label, message) {
  for (let attempt = 0; attempt < 80; attempt += 1) {
    if (queryAll(root, `[aria-label="${label}"]`)[0]) return;
    await flushPage();
  }
  throw new Error(message);
}

test("the workspace collapses the sidebar and opens the live map from chat", async () => {
  const originalFetch = globalThis.fetch;
  const originalWebSocket = globalThis.WebSocket;
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
    close() {
      this.readyState = 3;
    }
    send() {}
  }
  globalThis.WebSocket = FakeSocket;
  let chatCalls = 0;
  globalThis.fetch = async (url) => {
    const href = String(url);
    if (href.includes("/api/agent/chat") && !href.includes("session")) {
      chatCalls += 1;
      return new Response(routeCardSse(chatCalls === 1 ? undefined : []), {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      });
    }
    if (href.includes("ws-ticket") && href.includes("service-alerts")) {
      return new Response("nope", { status: 503 });
    }
    if (href.includes("ws-ticket")) {
      return new Response(JSON.stringify({ ticket: "t", ws_base_url: "ws://localhost:8000" }), {
        status: 200,
      });
    }
    if (href.includes("/api/service-alerts")) {
      throw new Error("alerts down");
    }
    return new Response("{}", { status: 200 });
  };
  const installed = installPageDom();
  const rootNode = installed.body.ownerDocument.createElement("DIV");
  installed.body.appendChild(rootNode);
  const { default: Page } = await import("./page.tsx");
  const root = createRoot(rootNode);
  try {
    await act(async () => {
      root.render(createElement(Page));
    });
    await flushPage();
    await waitForLabel(installed.documentNode, "Collapse sidebar", "sidebar did not render");
    await waitForLabel(installed.documentNode, "Transit Map", "live map control did not render");
    for (let attempt = 0; attempt < 80; attempt += 1) {
      if (queryAll(installed.documentNode, '[data-state="unavailable"]')[0]) break;
      await flushPage();
    }
    const collapse = queryAll(installed.documentNode, '[aria-label="Collapse sidebar"]')[0];
    const liveMap = queryAll(installed.documentNode, '[aria-label="Transit Map"]')[0];
    assert.equal(Boolean(collapse && liveMap), true);
    await act(async () => {
      dispatchClick(collapse);
    });
    assert.equal(
      queryAll(installed.documentNode, '[data-sidebar-collapsed="true"]').length > 0,
      true,
    );
    await act(async () => {
      dispatchClick(liveMap);
    });
    assert.equal(queryAll(installed.documentNode, '[data-tab="livemap"]').length > 0, true);
    const suggestion = queryAll(
      installed.documentNode,
      '[aria-label="Get me to JFK with fewer transfers."]',
    )[0];
    await act(async () => {
      dispatchClick(suggestion);
    });
    await flushPage();
    const send = queryAll(installed.documentNode, '[aria-label="Send message"]')[0];
    await act(async () => {
      dispatchClick(send);
    });
    await waitForLabel(installed.documentNode, "Open on map", "route card did not render");
    await act(async () => {
      dispatchClick(queryAll(installed.documentNode, '[aria-label="Open on map"]')[0]);
    });
    await flushPage();
    await act(async () => {
      for (const socket of sockets) {
        socket.readyState = 1;
        socket.onopen?.(new Event("open"));
        socket.onmessage?.({
          data: JSON.stringify({
            type: "snapshot",
            data: {
              arrivals: [{ arrival_time: 5, mode: "subway", route_id: "A" }],
              updated_at: 10,
              nearest_stop: { stop_name: "Times Sq", route_ids: ["A"] },
            },
          }),
        });
      }
    });
    await act(async () => {
      dispatchClick(queryAll(installed.documentNode, '[aria-label="Start a new SmartRoute trip"]')[0]);
    });
    await flushPage();
    assert.equal(queryAll(installed.documentNode, '[data-tab="livemap"]').length, 0);
    const nextSuggestion = queryAll(
      installed.documentNode,
      '[aria-label="Get me to JFK with fewer transfers."]',
    )[0];
    await act(async () => {
      dispatchClick(nextSuggestion);
    });
    await flushPage();
    await act(async () => {
      dispatchClick(queryAll(installed.documentNode, '[aria-label="Send message"]')[0]);
    });
    await waitForLabel(installed.documentNode, "Open on map", "empty route card did not render");
    await act(async () => {
      dispatchClick(queryAll(installed.documentNode, '[aria-label="Open on map"]')[0]);
    });
    await flushPage();
    assert.equal(queryAll(installed.documentNode, '[data-tab="livemap"]').length, 0);
  } finally {
    await act(async () => {
      root.unmount();
    });
    globalThis.fetch = originalFetch;
    if (originalWebSocket) globalThis.WebSocket = originalWebSocket;
    else delete globalThis.WebSocket;
  }
});
