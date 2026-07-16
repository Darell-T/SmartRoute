"use client";

/* ════════════════════════════════════════════════════════════════════════
   SmartRoute chat — panel (composition root)

   Wires `useAgentChat`'s returned state to the thread + composer, and
   renders the empty-state intro with example query chips (the three demo
   queries from the plan) when there's no conversation yet.
   ════════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import type { useAgentChat } from "@/lib/use-agent-chat";
import type { RouteCard } from "@/lib/agent-chat-stream";
import { ChatThread } from "./chat-thread";
import { ChatComposer } from "./chat-composer";

const EXAMPLE_QUERIES = [
  "Heading to Costco, no bus — I've got a cart",
  "Best way home after the Knicks game tomorrow, avoiding the crowd",
  "Heading to the FIFA game today, want pizza first",
];

export function ChatPanel({
  chat,
  onSelectRouteCard,
}: {
  chat: ReturnType<typeof useAgentChat>;
  onSelectRouteCard?: (card: RouteCard) => void;
}) {
  const [draft, setDraft] = useState("");

  function handleSelectRouteCard(card: RouteCard) {
    chat.selectCard(card.card_id);
    onSelectRouteCard?.(card);
  }

  const emptyState = (
    <div className="sr-chat-empty">
      <p className="sr-chat-empty__title">Where can SmartRoute take you?</p>
      <p className="sr-chat-empty__subtitle">
        Ask in plain language — constraints, timing, and stops along the way all count.
      </p>
      <div className="sr-chat-empty__suggestions">
        {EXAMPLE_QUERIES.map((query) => (
          <button
            key={query}
            type="button"
            className="sr-chat-suggestion-chip"
            onClick={() => setDraft(query)}
          >
            {query}
          </button>
        ))}
      </div>
    </div>
  );

  return (
    <div className="sr-chat-panel">
      <ChatThread
        messages={chat.messages}
        isStreaming={chat.isStreaming}
        onSelectRouteCard={handleSelectRouteCard}
        emptyState={emptyState}
      />
      {chat.error && <p className="sr-chat-error-banner">{chat.error}</p>}
      <ChatComposer
        value={draft}
        onValueChange={setDraft}
        onSend={chat.send}
        onCancel={chat.cancel}
        isStreaming={chat.isStreaming}
      />
    </div>
  );
}
