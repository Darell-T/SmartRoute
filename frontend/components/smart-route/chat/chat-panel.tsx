"use client";

/* ════════════════════════════════════════════════════════════════════════
   SmartRoute chat — panel (composition root)

   Top bar (brand + Near You row) + vendored prompt-kit ChatContainer thread
   + composer. Wires `useAgentChat`'s returned state to the thread, renders
   the empty-state intro with the three demo-query suggestion pills, and
   turns a Near You bullet tap into a local (no-model-call) arrivals turn.
   ════════════════════════════════════════════════════════════════════════ */

import { useRef, useState } from "react";
import type { ArrivalsTurnPayload, useAgentChat } from "@/lib/use-agent-chat";
import type { RouteCard } from "@/lib/agent-chat-stream";
import type { ChatTheme } from "@/lib/use-chat-theme";
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
  "Heading to Costco, no bus, I've got a cart",
  "Best way home after the Knicks game tomorrow, avoiding the crowd",
  "Heading to the FIFA game today, want pizza first",
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
      {/* Displacement lens for the route cards' liquid-glass backdrop
          (backdrop-filter: url(#sr-liquid-lens) in smart-route-chat.css).
          Droplet optics, per the reference: the backdrop bends and its
          colors split hardest at the borders while the center stays almost
          clean. Built as: soft turbulence warp -> R/B channel offsets
          (chromatic dispersion) -> composited over the untouched source
          through a radial edge mask, so distortion ramps from ~zero at the
          center to full strength at the rim. primitiveUnits are
          objectBoundingBox so the same filter fits every card size.
          Engines without SVG backdrop filters never reference this and get
          the CSS rim-band fallback instead. */}
      <svg className="sr-chat-lens-defs" aria-hidden="true" focusable="false" width={0} height={0}>
        <filter
          id="sr-liquid-lens"
          x="-10%"
          y="-10%"
          width="120%"
          height="120%"
          primitiveUnits="objectBoundingBox"
          colorInterpolationFilters="sRGB"
        >
          <feTurbulence
            type="fractalNoise"
            baseFrequency="3.2 2.1"
            numOctaves="2"
            seed="11"
            result="ripple"
          />
          <feDisplacementMap
            in="SourceGraphic"
            in2="ripple"
            scale="0.045"
            xChannelSelector="R"
            yChannelSelector="G"
            result="warp"
          />
          <feColorMatrix
            in="warp"
            type="matrix"
            values="1 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1 0"
            result="warp-r"
          />
          <feOffset in="warp-r" dx="0.006" dy="0" result="warp-r-shift" />
          <feColorMatrix
            in="warp"
            type="matrix"
            values="0 0 0 0 0  0 1 0 0 0  0 0 0 0 0  0 0 0 1 0"
            result="warp-g"
          />
          <feColorMatrix
            in="warp"
            type="matrix"
            values="0 0 0 0 0  0 0 0 0 0  0 0 1 0 0  0 0 0 1 0"
            result="warp-b"
          />
          <feOffset in="warp-b" dx="-0.006" dy="0" result="warp-b-shift" />
          <feBlend in="warp-r-shift" in2="warp-g" mode="screen" result="warp-rg" />
          <feBlend in="warp-rg" in2="warp-b-shift" mode="screen" result="chroma" />
          <feImage
            href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='64' height='64'%3E%3Cdefs%3E%3CradialGradient id='m' cx='50%25' cy='50%25' r='71%25'%3E%3Cstop offset='46%25' stop-color='%23000' stop-opacity='0'/%3E%3Cstop offset='78%25' stop-color='%23000' stop-opacity='0.55'/%3E%3Cstop offset='100%25' stop-color='%23000' stop-opacity='1'/%3E%3C/radialGradient%3E%3C/defs%3E%3Crect width='64' height='64' fill='url(%23m)'/%3E%3C/svg%3E"
            x="0"
            y="0"
            width="1"
            height="1"
            preserveAspectRatio="none"
            result="edge-alpha"
          />
          <feComposite in="chroma" in2="edge-alpha" operator="in" result="edge-warp" />
          <feMerge>
            <feMergeNode in="SourceGraphic" />
            <feMergeNode in="edge-warp" />
          </feMerge>
        </filter>
      </svg>
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
          onSend={chat.send}
          onCancel={chat.cancel}
          isStreaming={chat.isStreaming}
        />
      </div>
    </div>
  );
}
