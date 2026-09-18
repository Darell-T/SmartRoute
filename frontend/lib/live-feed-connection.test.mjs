import assert from "node:assert/strict";
import test from "node:test";
import { LiveFeedConnection, normalizeRouteScope } from "./live-feed-connection.ts";

function deferred() { let resolve; return { promise: new Promise((done) => { resolve = done; }), resolve }; }

test("connection sends only canonical location fields and normalized scope", async () => {
  const ticket = deferred();
  const socket = { readyState: 1, send: (payload) => socket.sent.push(JSON.parse(payload)), close() { socket.closed += 1; }, onopen: null, onclose: null, onerror: null, onmessage: null, sent: [], closed: 0 };
  const connection = new LiveFeedConnection({ fetchTicket: () => ticket.promise, createSocket: () => socket, onStatus: () => {}, onMessage: () => {} });
  connection.updateLocation({ lat: 40.7, lng: -73.9 });
  connection.updateRouteIds(["q", "Q", " "]);
  connection.start();
  connection.updateLocation({ lat: 40.71, lng: -73.91, accuracyMeters: 12 });
  ticket.resolve("ticket");
  await Promise.resolve();
  socket.onopen();
  assert.deepEqual(socket.sent[0], { type: "location", lat: 40.71, lng: -73.91, selected_route_ids: ["Q"] });
  connection.updateRouteIds(["a", "Q"]);
  assert.deepEqual(socket.sent[1], { type: "vehicle_scope", selected_route_ids: ["A", "Q"] });
  assert.equal(socket.sent.length, 2, "opening sends the scoped location once");
  connection.dispose();
  assert.equal(socket.closed, 1);
});

test("connection cancels reconnect timers on dispose and never reconnects after cleanup", async () => {
  const timers = [];
  const socket = { readyState: 1, send() {}, close() {}, onopen: null, onclose: null, onerror: null, onmessage: null };
  let created = 0;
  const connection = new LiveFeedConnection({ fetchTicket: async () => "ticket", createSocket: () => { created += 1; return socket; }, onStatus: () => {}, onMessage: () => {}, setTimer: (callback) => { const timer = { callback }; timers.push(timer); return timer; }, clearTimer: (timer) => { timer.cleared = true; } });
  connection.updateLocation({ lat: 40.7, lng: -73.9 });
  connection.start();
  await Promise.resolve();
  socket.onclose();
  connection.dispose();
  timers[0].callback();
  await Promise.resolve();
  assert.equal(created, 1);
  assert.equal(timers[0].cleared, true);
  assert.deepEqual(normalizeRouteScope([" q", "Q", "A", ""]), ["Q", "A"]);
});

test("dispose detaches every handler and ignores late socket events", async () => {
  const socket = { readyState: 1, send() {}, close() {}, onopen: null, onclose: null, onerror: null, onmessage: null };
  const statuses = [];
  const messages = [];
  const connection = new LiveFeedConnection({ fetchTicket: async () => "ticket", createSocket: () => socket, onStatus: (status) => statuses.push(status), onMessage: (message) => messages.push(message) });
  connection.updateLocation({ lat: 40.7, lng: -73.9 });
  connection.start();
  await Promise.resolve();
  const handlers = { open: socket.onopen, close: socket.onclose, error: socket.onerror, message: socket.onmessage };
  connection.dispose();
  assert.equal(socket.onopen, null);
  assert.equal(socket.onclose, null);
  assert.equal(socket.onerror, null);
  assert.equal(socket.onmessage, null);
  handlers.open?.();
  handlers.error?.();
  handlers.message?.({ data: "late" });
  handlers.close?.();
  assert.deepEqual(messages, []);
  assert.deepEqual(statuses, ["connecting"]);
});

test("a Strict Mode cleanup followed by a new controller does not leak sockets or timers", async () => {
  const sockets = [];
  const timers = [];
  const makeSocket = () => ({ readyState: 1, send() {}, close() {}, onopen: null, onclose: null, onerror: null, onmessage: null });
  const options = {
    fetchTicket: async () => "ticket",
    createSocket: () => { const socket = makeSocket(); sockets.push(socket); return socket; },
    onStatus() {}, onMessage() {},
    setTimer: (callback) => { const timer = { callback }; timers.push(timer); return timer; },
    clearTimer: (timer) => { timer.cleared = true; },
  };
  const first = new LiveFeedConnection(options);
  first.updateLocation({ lat: 40.7, lng: -73.9 });
  first.start();
  await Promise.resolve();
  first.dispose();
  const second = new LiveFeedConnection(options);
  second.updateLocation({ lat: 40.71, lng: -73.91 });
  second.start();
  await Promise.resolve();
  assert.equal(sockets.length, 2);
  assert.equal(timers.length, 0);
  assert.equal(sockets[0].onopen, null);
  second.dispose();
});

test("ticket failure schedules a reconnect and a later close reports reconnecting", async () => {
  const statuses = [];
  const timers = [];
  const connection = new LiveFeedConnection({
    fetchTicket: async () => {
      throw new Error("no ticket");
    },
    createSocket: () => {
      throw new Error("should not open");
    },
    onStatus: (status) => statuses.push(status),
    onMessage: () => {},
    setTimer: (callback) => {
      timers.push(callback);
      return callback;
    },
    clearTimer: () => {},
  });
  connection.updateLocation({ lat: 40.7, lng: -73.9 });
  connection.start();
  await Promise.resolve();
  await Promise.resolve();
  assert.deepEqual(statuses, ["connecting", "reconnecting"]);
  connection.updateRouteIds(["Q"]);
  connection.updateRouteIds(["Q"]);
  connection.dispose();
  assert.equal(timers.length, 1);
});

test("open sockets report errors and ignore tiny location jitter", async () => {
  const statuses = [];
  const socket = {
    readyState: 1,
    send: (payload) => socket.sent.push(JSON.parse(payload)),
    close() {},
    onopen: null,
    onclose: null,
    onerror: null,
    onmessage: null,
    sent: [],
  };
  const connection = new LiveFeedConnection({
    fetchTicket: async () => "ticket",
    createSocket: () => socket,
    onStatus: (status) => statuses.push(status),
    onMessage: () => {},
  });
  connection.updateLocation({ lat: 40.7, lng: -73.9 });
  connection.start();
  await Promise.resolve();
  socket.onopen();
  socket.onerror();
  connection.updateLocation({ lat: 40.7001, lng: -73.9001 });
  assert.equal(statuses.includes("error"), true);
  assert.equal(socket.sent.length, 1);
  socket.onmessage?.({ data: "ping" });
  connection.dispose();
});

test("start is a no-op without a location and scope is skipped while the socket is closed", async () => {
  const socket = {
    readyState: 0,
    send: (payload) => socket.sent.push(JSON.parse(payload)),
    close() {},
    onopen: null,
    onclose: null,
    onerror: null,
    onmessage: null,
    sent: [],
  };
  const statuses = [];
  const connection = new LiveFeedConnection({
    fetchTicket: async () => "ticket",
    createSocket: () => socket,
    onStatus: (status) => statuses.push(status),
    onMessage: () => {},
  });
  connection.start();
  connection.updateRouteIds(["Q"]);
  await Promise.resolve();
  assert.deepEqual(statuses, []);
  assert.equal(socket.sent.length, 0);
  connection.updateLocation({ lat: 40.7, lng: -73.9 });
  connection.start();
  await Promise.resolve();
  connection.updateRouteIds(["B"]);
  assert.equal(socket.sent.length, 0);
  socket.readyState = 1;
  socket.onopen();
  assert.equal(socket.sent[0].type, "location");
  connection.updateRouteIds(["A"]);
  assert.equal(socket.sent[1].type, "vehicle_scope");
  assert.deepEqual(socket.sent[1].selected_route_ids, ["A"]);
  connection.dispose();
});

test("a second start while connecting is ignored and dispose cancels an in-flight ticket", async () => {
  const ticket = deferred();
  const statuses = [];
  const connection = new LiveFeedConnection({
    fetchTicket: () => ticket.promise,
    createSocket: () => {
      throw new Error("should not open");
    },
    onStatus: (status) => statuses.push(status),
    onMessage: () => {},
  });
  connection.updateLocation({ lat: 40.7, lng: -73.9 });
  connection.start();
  connection.start();
  connection.dispose();
  ticket.resolve("ticket");
  await Promise.resolve();
  await Promise.resolve();
  assert.deepEqual(statuses, ["connecting"]);
});

test("close while a reconnect timer is pending does not stack timers", async () => {
  const timers = [];
  const socket = {
    readyState: 1,
    send() {},
    close() {},
    onopen: null,
    onclose: null,
    onerror: null,
    onmessage: null,
  };
  const connection = new LiveFeedConnection({
    fetchTicket: async () => "ticket",
    createSocket: () => socket,
    onStatus: () => {},
    onMessage: () => {},
    setTimer: (callback) => {
      timers.push(callback);
      return callback;
    },
    clearTimer: () => {},
  });
  connection.updateLocation({ lat: 40.7, lng: -73.9 });
  connection.start();
  await Promise.resolve();
  socket.onclose();
  socket.onclose();
  assert.equal(timers.length, 1);
  connection.dispose();
});

test("a second start after ticket failure does not stack reconnect timers", async () => {
  const timers = [];
  const connection = new LiveFeedConnection({
    fetchTicket: async () => {
      throw new Error("no ticket");
    },
    createSocket: () => {
      throw new Error("should not open");
    },
    onStatus: () => {},
    onMessage: () => {},
    setTimer: (callback) => {
      timers.push(callback);
      return callback;
    },
    clearTimer: () => {},
  });
  connection.updateLocation({ lat: 40.7, lng: -73.9 });
  connection.start();
  await Promise.resolve();
  await Promise.resolve();
  connection.start();
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(timers.length, 1);
  connection.dispose();
});

test("reconnect uses the platform timer when no test timer is supplied", async () => {
  const scheduled = [];
  const originalSetTimeout = globalThis.setTimeout;
  globalThis.setTimeout = (callback) => {
    scheduled.push(callback);
    return 1;
  };
  const connection = new LiveFeedConnection({
    fetchTicket: async () => {
      throw new Error("no ticket");
    },
    createSocket: () => {
      throw new Error("should not open");
    },
    onStatus: () => {},
    onMessage: () => {},
  });
  try {
    connection.updateLocation({ lat: 40.7, lng: -73.9 });
    connection.start();
    await Promise.resolve();
    await Promise.resolve();
    assert.equal(scheduled.length, 1);
  } finally {
    globalThis.setTimeout = originalSetTimeout;
    connection.dispose();
  }
});

test("a later reconnect after ticket failure reports reconnecting", async () => {
  const statuses = [];
  const timers = [];
  const connection = new LiveFeedConnection({
    fetchTicket: async () => {
      throw new Error("no ticket");
    },
    createSocket: () => {
      throw new Error("should not open");
    },
    onStatus: (status) => statuses.push(status),
    onMessage: () => {},
    setTimer: (callback) => {
      timers.push(callback);
      return callback;
    },
    clearTimer: () => {},
  });
  connection.updateLocation({ lat: 40.7, lng: -73.9 });
  connection.start();
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(timers.length, 1);
  timers[0]();
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(statuses.filter((status) => status === "reconnecting").length >= 2, true);
  connection.dispose();
});
