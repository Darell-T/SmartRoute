"use client";

/* ════════════════════════════════════════════════════════════════════════
   SmartRoute chat — panel (composition root)

   Top bar (brand + Near You row) + vendored prompt-kit ChatContainer thread
   + composer. Wires `useAgentChat`'s returned state to the thread, renders
   the empty-state intro with the three demo-query suggestion pills, and
   turns a Near You bullet tap into a local (no-model-call) arrivals turn.
   ════════════════════════════════════════════════════════════════════════ */

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
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
import {
  ChatSuggestions,
  ChatWelcome,
  type ChatSuggestion,
} from "./chat-welcome";
import type { HomeNearbyModel } from "./near-you";

const EXAMPLE_QUERIES: readonly ChatSuggestion[] = [
  {
    label: "JFK · fewer transfers",
    query: "Get me to JFK with fewer transfers.",
  },
  {
    label: "MSG · avoid crowds",
    query: "Get me to Madison Square Garden and avoid crowds.",
  },
  {
    label: "Ramen · best route now",
    query: "Find a good ramen spot and route me there by subway.",
  },
];

export function ChatPanel({
  chat,
  theme,
  nearby,
  onOpenLiveMap,
  onViewAlerts,
  onSelectRouteCard,
  onOpenNearbyStation,
}: {
  chat: ReturnType<typeof useAgentChat>;
  theme: ChatTheme;
  nearby: HomeNearbyModel;
  onOpenLiveMap: () => void;
  onViewAlerts?: () => void;
  onSelectRouteCard?: (card: RouteCard) => void;
  onOpenNearbyStation?: (arrivals: ArrivalsTurnPayload) => void;
}) {
  const [draft, setDraft] = useState("");
  const [composerFocused, setComposerFocused] = useState(false);
  const presentationMode = useSyncExternalStore(
    responsePresentationModeStore.subscribe,
    responsePresentationModeStore.getClientSnapshot,
    responsePresentationModeStore.getServerSnapshot,
  );
  const composerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const composerDock = composerRef.current;
    if (!composerDock) return;

    const handleFocusIn = () => setComposerFocused(true);
    const handleFocusOut = () => {
      requestAnimationFrame(() => {
        setComposerFocused(
          composerDock.contains(document.activeElement),
        );
      });
    };

    composerDock.addEventListener("focusin", handleFocusIn);
    composerDock.addEventListener("focusout", handleFocusOut);
    return () => {
      composerDock.removeEventListener("focusin", handleFocusIn);
      composerDock.removeEventListener("focusout", handleFocusOut);
    };
  }, []);

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
    <div className="sr-chat-tab-inner" data-empty={isEmpty ? "true" : "false"}>
      <ChatContainerRoot className="sr-chat-thread">
        <ChatContainerContent className="sr-chat-thread__content" data-empty={isEmpty ? "true" : "false"}>
          {isEmpty ? (
            <ChatWelcome
              nearby={nearby}
              onOpenLiveMap={onOpenLiveMap}
            />
          ) : (
            chat.messages.map((turn, index) => (
              <ChatMessage
                key={index}
                turn={turn}
                theme={theme}
                selectedCardId={chat.selectedCardId}
                onSelectRouteCard={handleSelectRouteCard}
                onSeeArrivalsOnMap={onOpenNearbyStation ?? (() => onOpenLiveMap())}
                onViewAlerts={onViewAlerts}
                onRetry={index === chat.messages.length - 1 ? chat.retryLast : undefined}
                onDismissError={
                  index === chat.messages.length - 1 ? chat.dismissError : undefined
                }
              />
            ))
          )}
          <ChatContainerScrollAnchor />
        </ChatContainerContent>
        <ScrollButton className="sr-chat-scroll-button" />
      </ChatContainerRoot>

      <div className="sr-chat-interaction-dock">
        {isEmpty ? (
          <ChatSuggestions
            suggestions={EXAMPLE_QUERIES}
            hidden={composerFocused}
            onSelectSuggestion={fillDraftAndFocus}
          />
        ) : null}

        <div
          ref={composerRef}
          className="sr-chat-composer-dock"
        >
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
    </div>
  );
}
