"use client";

/* ════════════════════════════════════════════════════════════════════════
   SmartRoute chat — panel (composition root)

   Top bar (brand + Near You row) + vendored prompt-kit ChatContainer thread
   + composer. Wires `useAgentChat`'s returned state to the thread, renders
   the empty-state intro with the three demo-query suggestion pills, and
   turns a Near You bullet tap into a local (no-model-call) arrivals turn.
   ════════════════════════════════════════════════════════════════════════ */

import { useRef, useState, useSyncExternalStore } from "react";
import type { ArrivalsTurnPayload, useAgentChat } from "@/lib/use-agent-chat";
import type { RouteCard } from "@/lib/agent-chat-stream";
import type { ChatTheme } from "@/lib/use-chat-theme";
import { responsePresentationModeStore } from "@/lib/response-presentation";
import {
  ChatContainerContent,
  ChatContainerRoot,
  ChatContainerScrollAnchor,
} from "@/components/prompt-kit/chat-container";
import { ScrollButton } from "@/components/prompt-kit/scroll-button";
import { ChatMessage } from "./chat-message";
import { ChatComposer } from "./chat-composer";
import { ChatWelcome } from "./chat-welcome";

const EXAMPLE_QUERIES = [
  "Get me to JFK by 6:30 PM with the fewest transfers",
  "Best route from Brooklyn to Midtown while avoiding current delays",
  "Plan a trip to Coney Island with less walking",
];

export function ChatPanel({
  chat,
  theme,
  onOpenLiveMap,
  onSelectRouteCard,
  onOpenNearbyStation,
}: {
  chat: ReturnType<typeof useAgentChat>;
  theme: ChatTheme;
  onOpenLiveMap: () => void;
  onSelectRouteCard?: (card: RouteCard) => void;
  onOpenNearbyStation?: (arrivals: ArrivalsTurnPayload) => void;
}) {
  const [draft, setDraft] = useState("");
  const presentationMode = useSyncExternalStore(
    responsePresentationModeStore.subscribe,
    responsePresentationModeStore.getClientSnapshot,
    responsePresentationModeStore.getServerSnapshot,
  );
  const composerRef = useRef<HTMLDivElement | null>(null);

  function handleSelectRouteCard(card: RouteCard) {
    chat.selectCard(card.card_id);
    onSelectRouteCard?.(card);
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
      <ChatContainerRoot className="sr-chat-thread">
        <ChatContainerContent className="sr-chat-thread__content" data-empty={isEmpty ? "true" : "false"}>
          {isEmpty ? (
            <ChatWelcome suggestions={EXAMPLE_QUERIES} onSelectSuggestion={fillDraftAndFocus} />
          ) : (
            chat.messages.map((turn, index) => (
              <ChatMessage
                key={index}
                turn={turn}
                theme={theme}
                showCaret={chat.isStreaming && index === chat.messages.length - 1 && turn.role === "assistant"}
                selectedCardId={chat.selectedCardId}
                onSelectRouteCard={handleSelectRouteCard}
                onSeeArrivalsOnMap={onOpenNearbyStation ?? (() => onOpenLiveMap())}
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
          presentationMode={presentationMode}
          onPresentationModeChange={responsePresentationModeStore.setMode}
          theme={theme}
          onSend={(text) => chat.send(text, presentationMode)}
          onCancel={chat.cancel}
          isStreaming={chat.isStreaming}
        />
      </div>
    </div>
  );
}
