"use client";

/* ════════════════════════════════════════════════════════════════════════
   SmartRoute chat — panel (composition root)

   Top bar (brand + Near You row) + vendored prompt-kit ChatContainer thread
   + composer. Wires `useAgentChat`'s returned state to the thread, renders
   the empty-state intro with the three demo-query suggestion pills, and
   turns a Near You bullet tap into a local (no-model-call) arrivals turn.
   ════════════════════════════════════════════════════════════════════════ */

import { useMemo, useRef, useState } from "react";
import type { useAgentChat } from "@/lib/use-agent-chat";
import type { ChatTheme } from "@/lib/use-chat-theme";
import type { RouteCard } from "@/lib/agent-chat-stream";
import type { Arrival, NearbyTransitGroup } from "@/components/smart-route/left-rail/types";
import { SUBWAY_BULLET_ROUTES } from "@/components/smart-route/train-bullet";
import {
  ChatContainerContent,
  ChatContainerRoot,
  ChatContainerScrollAnchor,
} from "@/components/prompt-kit/chat-container";
import { ScrollButton } from "@/components/prompt-kit/scroll-button";
import { ChatTopBar } from "./chat-top-bar";
import { ChatMessage } from "./chat-message";
import { ChatComposer } from "./chat-composer";
import { deriveNearbyRouteIds, stationNameForRoute, buildArrivalsPayloadForRoute } from "./near-you";

const EXAMPLE_QUERIES = [
  "Heading to Costco, no bus, I've got a cart",
  "Best way home after the Knicks game tomorrow, avoiding the crowd",
  "Heading to the FIFA game today, want pizza first",
];

export function ChatPanel({
  chat,
  theme,
  onToggleTheme,
  nearbyTransitGroups,
  nearbyArrivals,
  nearbyBusArrivals,
  nearestStopName,
  onOpenLiveMap,
  onSelectRouteCard,
}: {
  chat: ReturnType<typeof useAgentChat>;
  theme: ChatTheme;
  onToggleTheme: () => void;
  nearbyTransitGroups: NearbyTransitGroup[];
  nearbyArrivals: Arrival[];
  nearbyBusArrivals: Arrival[];
  nearestStopName: string;
  onOpenLiveMap: () => void;
  onSelectRouteCard?: (card: RouteCard) => void;
}) {
  const [draft, setDraft] = useState("");
  const composerRef = useRef<HTMLDivElement | null>(null);

  const nearbyRouteIds = useMemo(
    () => deriveNearbyRouteIds({ nearbyTransitGroups, arrivals: nearbyArrivals, nearbyBusArrivals }),
    [nearbyTransitGroups, nearbyArrivals, nearbyBusArrivals],
  );

  function handleSelectRouteCard(card: RouteCard) {
    chat.selectCard(card.card_id);
    onSelectRouteCard?.(card);
  }

  function handleSelectNearbyRoute(routeId: string) {
    const stationName = stationNameForRoute(routeId, nearbyTransitGroups, nearestStopName);
    const payload = buildArrivalsPayloadForRoute(routeId, nearbyArrivals, stationName);
    const vehicleWord = SUBWAY_BULLET_ROUTES.has(routeId.toUpperCase()) ? "trains" : "buses";
    chat.appendLocalTurn({ text: `Next ${routeId} ${vehicleWord} near you:`, arrivals: payload });
  }

  function fillDraftAndFocus(query: string) {
    setDraft(query);
    requestAnimationFrame(() => {
      composerRef.current?.querySelector("textarea")?.focus();
    });
  }

  const isEmpty = chat.messages.length === 0;

  return (
    <div className="sr-chat-tab-inner">
      <ChatTopBar
        nearbyRouteIds={nearbyRouteIds}
        onSelectNearbyRoute={handleSelectNearbyRoute}
        onOpenLiveMap={onOpenLiveMap}
        theme={theme}
        onToggleTheme={onToggleTheme}
      />

      <ChatContainerRoot className="sr-chat-thread">
        <ChatContainerContent className="sr-chat-thread__content" data-empty={isEmpty ? "true" : "false"}>
          {isEmpty ? (
            <div className="sr-chat-empty">
              <p className="sr-chat-empty__title">Where to?</p>
              <p className="sr-chat-empty__subtitle">Ask about any trip in New York.</p>
              <div className="sr-chat-empty__suggestions">
                {EXAMPLE_QUERIES.map((query) => (
                  <button
                    key={query}
                    type="button"
                    className="sr-chat-suggestion-pill"
                    onClick={() => fillDraftAndFocus(query)}
                  >
                    {query}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            chat.messages.map((turn, index) => (
              <ChatMessage
                key={index}
                turn={turn}
                showCaret={chat.isStreaming && index === chat.messages.length - 1 && turn.role === "assistant"}
                selectedCardId={chat.selectedCardId}
                onSelectRouteCard={handleSelectRouteCard}
                onSeeArrivalsOnMap={onOpenLiveMap}
              />
            ))
          )}
          <ChatContainerScrollAnchor />
        </ChatContainerContent>
        <ScrollButton className="sr-chat-scroll-button" />
      </ChatContainerRoot>

      {chat.error && (
        <p className="sr-chat-error-banner" role="alert">
          {chat.error}
        </p>
      )}

      <div ref={composerRef}>
        <ChatComposer
          value={draft}
          onValueChange={setDraft}
          onSend={chat.send}
          onCancel={chat.cancel}
          isStreaming={chat.isStreaming}
        />
      </div>
    </div>
  );
}
