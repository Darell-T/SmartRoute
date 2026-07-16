"use client";

/* ════════════════════════════════════════════════════════════════════════
   Dev story page — SmartRoute Agent Chat

   A standalone preview of the chat tab with zero backend dependency: the
   `useAgentChat` transport is swapped for a scripted async generator that
   replays a realistic turn (streamed prose, two tool chips, three route
   cards — one recommended) followed by a second turn that demonstrates the
   error path. Visit `/dev/agent-chat` to interact with it.

   This page is dev-only — not linked from the production site — same
   pattern as `/dev/left-rail`.
   ════════════════════════════════════════════════════════════════════════ */

import { notFound } from "next/navigation";
import { useEffect, useRef } from "react";
import { useAgentChat, type AgentChatRequestBody } from "@/lib/use-agent-chat";
import type { AgentEvent } from "@/lib/agent-chat-stream";
import { ChatPanel } from "@/components/smart-route/chat/chat-panel";

const FIRST_DEMO_QUERY = "Heading to Costco, no bus — I've got a cart";

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
  await wait(1500, signal);
  yield {
    type: "tool_end",
    tool_call_id: "c1",
    tool: "plan_trip",
    ok: true,
    duration_ms: 1500,
    summary: "3 candidates found",
  };

  await wait(150, signal);
  yield {
    type: "tool_start",
    tool_call_id: "c2",
    tool: "transit_snapshot",
    label: "Checking live conditions near Costco…",
  };
  await wait(1200, signal);
  yield {
    type: "tool_end",
    tool_call_id: "c2",
    tool: "transit_snapshot",
    ok: true,
    duration_ms: 1200,
    summary: "No major alerts",
  };

  const prose =
    "Here's the best way to Costco without a bus — the A train gets you closest, " +
    "and it's a short walk from there with your cart.";
  for (const chunk of tokenChunks(prose)) {
    await wait(55, signal);
    yield { type: "token", text: chunk };
  }

  await wait(300, signal);
  yield {
    type: "route_card",
    card_id: "rc_demo1",
    turn_id: turnId,
    role: "recommended",
    origin: { label: "Your location", lat: 40.7484, lng: -73.9857 },
    destination: { label: "Costco Sunset Park", lat: 40.6559, lng: -74.0089 },
    summary: {
      eta_minutes: 34,
      transfers: 0,
      lines: ["A"],
      reason: "No bus, fewest transfers, and the platform has elevator access for the cart.",
    },
    route: [
      {
        type: "SUBWAY",
        train_line: "A",
        departure_time_iso: "2026-07-16T14:05:00-04:00",
        arrival_time_iso: "2026-07-16T14:35:00-04:00",
      },
    ],
    alerts: [],
  };

  await wait(220, signal);
  yield {
    type: "route_card",
    card_id: "rc_demo2",
    turn_id: turnId,
    role: "alternative",
    origin: { label: "Your location", lat: 40.7484, lng: -73.9857 },
    destination: { label: "Costco Sunset Park", lat: 40.6559, lng: -74.0089 },
    summary: {
      eta_minutes: 29,
      transfers: 1,
      lines: ["N", "R"],
      reason: "Faster, but one transfer and a longer walk with a cart.",
    },
    route: [{ type: "SUBWAY", train_line: "N" }, { type: "SUBWAY", train_line: "R" }],
    alerts: [],
  };

  await wait(220, signal);
  yield {
    type: "route_card",
    card_id: "rc_demo3",
    turn_id: turnId,
    role: "alternative",
    origin: { label: "Your location", lat: 40.7484, lng: -73.9857 },
    destination: { label: "Costco Sunset Park", lat: 40.6559, lng: -74.0089 },
    summary: {
      eta_minutes: 41,
      transfers: 0,
      lines: ["D"],
      reason: "Slower, but the least walking overall.",
    },
    route: [{ type: "SUBWAY", train_line: "D" }],
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
    tool_call_id: "c3",
    tool: "plan_trip",
    label: "Checking real-time conditions…",
  };
  await wait(900, signal);

  yield {
    type: "error",
    code: "upstream_error",
    message: "Lost the connection to the routing service. Try again in a moment.",
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

export default function AgentChatStoryPage() {
  // Dev-only route: hide it from production builds, same guard as
  // /dev/left-rail.
  if (process.env.NODE_ENV === "production") notFound();

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
    // Kicks off the scripted success turn as soon as the page mounts so the
    // story shows a populated thread without any click. Safe against
    // React StrictMode's dev-only double effect invocation: useAgentChat's
    // own in-flight guard (not this component) makes a second concurrent
    // send() a no-op.
    chat.send(FIRST_DEMO_QUERY);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div
      style={{
        height: "100vh",
        width: "100%",
        background: "#07090F",
        display: "flex",
        justifyContent: "center",
      }}
    >
      <div
        className="sr-rail"
        style={{
          width: "100%",
          maxWidth: 420,
          height: "100%",
        }}
      >
        <ChatPanel chat={chat} />
      </div>
    </div>
  );
}
