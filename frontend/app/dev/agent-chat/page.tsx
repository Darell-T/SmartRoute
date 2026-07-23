"use client";

/* ════════════════════════════════════════════════════════════════════════
   Dev story page — SmartRoute Agent Chat

   A standalone preview of the real chat layout (`ChatPanel`, the same
   component page.tsx mounts) with zero backend dependency: the
   `useAgentChat` transport is swapped for a scripted async generator that
   replays a realistic turn (streamed prose, tool rows, three
   geometry-complete route cards — real `@mapbox/polyline`-encoded
   polylines + coords, recommended enriched) followed by a second turn that
   demonstrates the error path. Near You bullets/arrivals are the left
   rail's own demo fixtures (`DEMO_RAIL_DATA`), so the bullet-tap ->
   ArrivalsCard flow works with no backend either. A theme switch control
   sits in the corner for deterministic screenshots (also readable from a
   `?theme=light` query param on load).

   Visit `/dev/agent-chat`. Dev-only route — same notFound() guard as
   `/dev/left-rail`.
   ════════════════════════════════════════════════════════════════════════ */

import { notFound, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import polyline from "@mapbox/polyline";
import {
  useAgentChat,
  type AgentChatRequestBody,
  type ArrivalsTurnPayload,
} from "@/lib/use-agent-chat";
import type { AgentEvent, RouteCardEndpoint } from "@/lib/agent-chat-stream";
import type { ChatTheme } from "@/lib/use-chat-theme";
import { ChatPanel } from "@/components/smart-route/chat/chat-panel";
import { ChatSidebar } from "@/components/smart-route/chat/chat-sidebar";

const FIRST_DEMO_QUERY = "Heading to Costco, no bus, I've got a cart";

const ORIGIN: RouteCardEndpoint = { label: "Your location", lat: 40.7484, lng: -73.9857 };
const DESTINATION: RouteCardEndpoint = { label: "Costco Sunset Park", lat: 40.6559, lng: -74.0089 };
const MOCK_NEARBY_LINES = ["A", "C", "E", "N", "Q", "R", "1", "2"];

function encodedLine(points: [number, number][]): { encodedPolyline: string } {
  return { encodedPolyline: polyline.encode(points) };
}

function wait(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException("Aborted", "AbortError"));
      return;
    }
    const timer = setTimeout(resolve, ms);
    signal.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        reject(new DOMException("Aborted", "AbortError"));
      },
      { once: true },
    );
  });
}

/** Splits prose into small word-groups so the mock stream reads like real
 *  token deltas rather than one giant chunk landing at once. */
function tokenChunks(text: string): string[] {
  const words = text.split(" ");
  const chunks: string[] = [];
  for (let i = 0; i < words.length; i += 2) {
    const group = words.slice(i, i + 2).join(" ");
    chunks.push(i === 0 ? group : ` ${group}`);
  }
  return chunks;
}

async function* successTurn(turnId: string, signal: AbortSignal): AsyncGenerator<AgentEvent> {
  yield { type: "meta", session_id: "dev-session", turn_id: turnId };

  await wait(150, signal);
  yield {
    type: "tool_start",
    tool_call_id: "c1",
    tool: "plan_trip",
    label: "Finding routes to Costco (no bus)…",
  };
  await wait(1200, signal);
  yield {
    type: "tool_end",
    tool_call_id: "c1",
    tool: "plan_trip",
    ok: true,
    duration_ms: 1200,
    summary: "3 candidates found",
  };

  await wait(150, signal);
  yield {
    type: "tool_start",
    tool_call_id: "c2",
    tool: "transit_snapshot",
    label: "Checking live conditions near Costco…",
  };
  await wait(900, signal);
  yield {
    type: "tool_end",
    tool_call_id: "c2",
    tool: "transit_snapshot",
    ok: true,
    duration_ms: 900,
    summary: "No major alerts",
  };

  await wait(150, signal);
  yield {
    type: "tool_start",
    tool_call_id: "c3",
    tool: "walk_directions",
    label: "Checking the walk from the platform…",
  };
  await wait(700, signal);
  yield {
    type: "tool_end",
    tool_call_id: "c3",
    tool: "walk_directions",
    ok: true,
    duration_ms: 700,
    summary: "Elevator access confirmed",
  };

  const prose =
    "The A train keeps the cart-friendly path intact with no bus and no transfer.";
  for (const chunk of tokenChunks(prose)) {
    await wait(55, signal);
    yield { type: "token", text: chunk };
  }

  await wait(250, signal);
  yield {
    type: "route_card",
    card_id: "rc_demo1",
    turn_id: turnId,
    role: "recommended",
    origin: ORIGIN,
    destination: DESTINATION,
    summary: {
      eta_minutes: 34,
      transfers: 0,
      lines: ["A"],
      reason: "No bus · Elevator access for the cart",
    },
    route: [
      {
        type: "WALK",
        departure_stop: "Your location",
        arrival_stop: "34 St-Penn Station",
        minutes_until_arrival: 4,
        start_point: { latitude: ORIGIN.lat, longitude: ORIGIN.lng },
        end_point: { latitude: 40.7527, longitude: -73.9862 },
        polyline: encodedLine([
          [ORIGIN.lat, ORIGIN.lng],
          [40.7527, -73.9862],
        ]),
      },
      {
        type: "SUBWAY",
        train_line: "A",
        departure_stop: "34 St-Penn Station",
        arrival_stop: "Jay St-MetroTech",
        minutes_until_arrival: 26,
        departure_coords: { latitude: 40.7527, longitude: -73.9862 },
        arrival_coords: { latitude: 40.6627, longitude: -73.9958 },
        polyline: encodedLine([
          [40.7527, -73.9862],
          [40.72, -73.995],
          [40.6627, -73.9958],
        ]),
        departure_time_iso: "2026-07-18T14:05:00-04:00",
        arrival_time_iso: "2026-07-18T14:31:00-04:00",
      },
      {
        type: "WALK",
        departure_stop: "Jay St-MetroTech",
        arrival_stop: "Costco Sunset Park",
        minutes_until_arrival: 4,
        start_point: { latitude: 40.6627, longitude: -73.9958 },
        end_point: { latitude: DESTINATION.lat, longitude: DESTINATION.lng },
        polyline: encodedLine([
          [40.6627, -73.9958],
          [DESTINATION.lat, DESTINATION.lng],
        ]),
        arrival_time_iso: "2026-07-18T14:39:00-04:00",
      },
    ],
    alerts: [],
    depart_iso: "2026-07-18T14:05:00-04:00",
  };

  await wait(220, signal);
  yield {
    type: "route_card",
    card_id: "rc_demo2",
    turn_id: turnId,
    role: "alternative",
    origin: ORIGIN,
    destination: DESTINATION,
    summary: {
      eta_minutes: 29,
      transfers: 1,
      lines: ["N", "R"],
      reason: "Faster, but one transfer and a longer walk with a cart.",
    },
    route: [
      {
        type: "SUBWAY",
        train_line: "N",
        departure_stop: "34 St-Herald Sq",
        arrival_stop: "Atlantic Av-Barclays Ctr",
        minutes_until_arrival: 18,
        departure_coords: { latitude: 40.7484, longitude: -73.9857 },
        arrival_coords: { latitude: 40.6892, longitude: -73.9906 },
        polyline: encodedLine([
          [40.7484, -73.9857],
          [40.71, -73.99],
          [40.6892, -73.9906],
        ]),
      },
      {
        type: "SUBWAY",
        train_line: "R",
        departure_stop: "Atlantic Av-Barclays Ctr",
        arrival_stop: "36 St",
        minutes_until_arrival: 8,
        departure_coords: { latitude: 40.6892, longitude: -73.9906 },
        arrival_coords: { latitude: 40.6459, longitude: -74.0089 },
        polyline: encodedLine([
          [40.6892, -73.9906],
          [40.6459, -74.0089],
        ]),
      },
    ],
    alerts: [],
  };

  await wait(220, signal);
  yield {
    type: "route_card",
    card_id: "rc_demo3",
    turn_id: turnId,
    role: "alternative",
    origin: ORIGIN,
    destination: DESTINATION,
    summary: {
      eta_minutes: 41,
      transfers: 0,
      lines: ["D"],
      reason: "Slower, but the least walking overall.",
    },
    route: [
      {
        type: "SUBWAY",
        train_line: "D",
        departure_coords: { latitude: 40.7484, longitude: -73.9857 },
        arrival_coords: { latitude: 40.6459, longitude: -74.0067 },
        polyline: encodedLine([
          [40.7484, -73.9857],
          [40.7, -73.998],
          [40.6459, -74.0067],
        ]),
      },
    ],
    alerts: [],
  };

  await wait(150, signal);
  yield {
    type: "done",
    session_id: "dev-session",
    turn_id: turnId,
    stop_reason: "end_turn",
    usage: { input_tokens: 812, output_tokens: 96 },
  };
}

async function* errorTurn(turnId: string, signal: AbortSignal): AsyncGenerator<AgentEvent> {
  yield { type: "meta", session_id: "dev-session", turn_id: turnId };

  await wait(150, signal);
  yield {
    type: "tool_start",
    tool_call_id: "c4",
    tool: "plan_trip",
    label: "Checking real-time conditions…",
  };
  await wait(900, signal);

  yield {
    type: "error",
    code: "upstream_error",
    message: "Couldn't reach live schedules. The route shown uses normal service times.",
    retryable: true,
  };
  await wait(100, signal);
  yield {
    type: "done",
    session_id: "dev-session",
    turn_id: turnId,
    stop_reason: "error",
    usage: { input_tokens: 140, output_tokens: 12 },
  };
}

function ThemeSwitchControl({ theme, onChange }: { theme: ChatTheme; onChange: (theme: ChatTheme) => void }) {
  return (
    <div
      style={{
        // A genuine document-flow strip above the real chat surface, not an
        // overlay — this is dev-harness chrome only, so it must never sit on
        // top of (and risk intercepting clicks on, or visually colliding
        // with) the real chat panel under review.
        flex: "0 0 auto",
        display: "flex",
        alignItems: "center",
        gap: 4,
        padding: "4px 8px",
        background: "#000",
        fontFamily: "ui-sans-serif, system-ui, sans-serif",
      }}
      data-dev-theme-switch
    >
      {(["dark", "light"] as const).map((option) => (
        <button
          key={option}
          type="button"
          data-dev-theme-option={option}
          data-active={theme === option}
          onClick={() => onChange(option)}
          style={{
            padding: "4px 10px",
            borderRadius: 6,
            border: "none",
            cursor: "pointer",
            fontSize: 11,
            fontWeight: 600,
            background: theme === option ? "#3ed134" : "transparent",
            color: theme === option ? "#06210a" : "#fff",
          }}
        >
          {option}
        </button>
      ))}
    </div>
  );
}

function AgentChatStoryInner() {
  const searchParams = useSearchParams();
  const initialTheme = searchParams.get("theme") === "light" ? "light" : "dark";
  // `?empty=1` skips the auto-sent first turn, so the story can also show
  // the true empty state (title/subtitle/suggestion pills, zero messages) —
  // used by the W-A screenshot gate's "empty state" requirement.
  const skipAutoSend = searchParams.get("empty") === "1";
  const showSidebar = searchParams.get("sidebar") === "1";
  const [theme, setTheme] = useState<ChatTheme>(initialTheme);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const turnCountRef = useRef(0);

  const chat = useAgentChat({
    transport: async function* mockTransport(_request: AgentChatRequestBody, signal: AbortSignal) {
      turnCountRef.current += 1;
      const turnId = `turn-${turnCountRef.current}`;
      if (turnCountRef.current === 1) {
        yield* successTurn(turnId, signal);
      } else {
        yield* errorTurn(turnId, signal);
      }
    },
  });

  useEffect(() => {
    if (skipAutoSend) return;
    // Kicks off the scripted success turn as soon as the page mounts so the
    // story shows a populated thread without any click. Safe against
    // React StrictMode's dev-only double effect invocation: useAgentChat's
    // own in-flight guard (not this component) makes a second concurrent
    // send() a no-op.
    chat.send(FIRST_DEMO_QUERY);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function showMockArrivals(routeId: string) {
    const arrivals: ArrivalsTurnPayload = {
      routeId,
      stationName: "34 St–Penn Station",
      stationGuidance: "4 min walk · 0.2 mi away",
      stationCoordinates: { lat: 40.7506, lng: -73.9935 },
      groups: [
        { direction: "uptown", label: "Uptown · Inwood–207 St", minutes: [2, 8, 14] },
        { direction: "downtown", label: "Downtown · Far Rockaway", minutes: [4, 11, 18] },
      ],
    };
    chat.appendLocalTurn({
      text: `Here are the next ${routeId} trains at ${arrivals.stationName}.`,
      arrivals,
    });
  }

  if (showSidebar) {
    return (
      <div
        className="sr-tab-shell"
        data-tab="chat"
        data-sidebar-collapsed={sidebarCollapsed ? "true" : "false"}
      >
        <ChatSidebar
          activeTab="chat"
          collapsed={sidebarCollapsed}
          theme={theme}
          nearbyRouteIds={MOCK_NEARBY_LINES}
          onOpenChat={() => undefined}
          onOpenLiveMap={() => undefined}
          onNewTrip={chat.reset}
          onSelectNearbyLine={showMockArrivals}
          onToggleCollapsed={() => setSidebarCollapsed((value) => !value)}
          onToggleTheme={() => setTheme((value) => (value === "dark" ? "light" : "dark"))}
        />
        <div className="sr-chat-tab sr-tab-shell__panel sr-tab-shell__panel--chat" data-sr-theme={theme}>
          <ChatPanel
            chat={chat}
            theme={theme}
            onOpenLiveMap={() => undefined}
            onOpenNearbyStation={(arrivals) => {
              // eslint-disable-next-line no-console
              console.log("[dev/agent-chat] station directions", arrivals.stationName);
            }}
          />
        </div>
      </div>
    );
  }

  return (
    <div
      style={{
        height: "100vh",
        width: "100%",
        background: "#07090F",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <ThemeSwitchControl theme={theme} onChange={setTheme} />
      <div className="sr-chat-tab" data-sr-theme={theme} style={{ flex: "1 1 auto", minHeight: 0 }}>
        <ChatPanel
          chat={chat}
          theme={theme}
          onOpenLiveMap={() => {
            // eslint-disable-next-line no-console
            console.log("[dev/agent-chat] open live map (stub — no map on this story page)");
          }}
          onSelectRouteCard={(card) => {
            // eslint-disable-next-line no-console
            console.log("[dev/agent-chat] route card selected", card.card_id);
          }}
        />
      </div>
    </div>
  );
}

export default function AgentChatStoryPage() {
  // Dev-only route: hide it from production builds, same guard as
  // /dev/left-rail.
  if (process.env.NODE_ENV === "production") notFound();

  return (
    <Suspense fallback={null}>
      <AgentChatStoryInner />
    </Suspense>
  );
}
