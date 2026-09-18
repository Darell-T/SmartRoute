"use client";

/* ════════════════════════════════════════════════════════════════════════
   SmartRoute chat — one turn

   User turns render as a filled bubble on the right (vendored prompt-kit
   Message/MessageContent, radius 18/18/6/18). The assistant never gets
   bubble chrome — bare prose on the canvas, full column width — with the
   working panel (tool progress) above it and route cards below. A local
   turn (Near You bullet tap) skips the working panel and prose entirely
   and renders an ArrivalsCard instead; it is never streamed and never sent
   to the backend.
   ════════════════════════════════════════════════════════════════════════ */

import type { ArrivalsTurnPayload, AssistantTurn, ChatTurn } from "@/lib/agent-chat/use-agent-chat";
import type { RouteCard } from "@/lib/agent-chat/stream";
import type { ChatTheme } from "@/lib/hooks/use-chat-theme";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { ThinkingOrb } from "thinking-orbs";
import { Message, MessageContent } from "@/components/prompt-kit/message";
import { Sources } from "@/components/prompt-kit/source";
import { isSearchActivityTool } from "@/lib/agent-chat/route-tools";
import { ChatWorkingPanel } from "./chat-working-panel";
import { ChatRouteCardList } from "./chat-route-card";
import { ChatArrivalsCard } from "./chat-arrivals-card";
import { useProgressiveText } from "./use-progressive-text";

function LocalArrivalsMessage({
  turn,
  onSeeOnMap,
}: {
  turn: AssistantTurn;
  onSeeOnMap?: (arrivals: ArrivalsTurnPayload) => void;
}) {
  const arrivals = turn.arrivals;
  if (!arrivals) return null;
  return (
    <div className="sr-chat-message sr-chat-message--assistant">
      {turn.text && <p className="sr-chat-message__prose">{turn.text}</p>}
      <ChatArrivalsCard
        arrivals={arrivals}
        onSeeOnMap={() => onSeeOnMap?.(arrivals)}
      />
    </div>
  );
}

function assistantSurfaces(
  turn: AssistantTurn,
  isCaughtUp: boolean,
) {
  const settled = !turn.isStreaming && isCaughtUp;
  return {
    showCards: settled && turn.routeCards.length > 0,
    showArrivals: settled && Boolean(turn.arrivals),
    showAlertsAction: settled && turn.transitStatusAction === "view_alerts",
    showSources: settled && Boolean(turn.sources?.length),
  };
}

function AssistantErrorMessage({
  message,
  retryable,
  onRetry,
  onDismissError,
}: {
  message: string;
  retryable: boolean;
  onRetry?: () => void;
  onDismissError?: () => void;
}) {
  return (
    <div className="sr-chat-message sr-chat-message--assistant">
      <div className="sr-chat-turn-error" role="alert">
        <p>{message}</p>
        <div className="sr-chat-turn-error__actions">
          {retryable && onRetry ? (
            <button type="button" onClick={onRetry}>
              Try again
            </button>
          ) : null}
          {onDismissError ? (
            <button type="button" onClick={onDismissError}>
              Dismiss
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function AssistantStreamingOrb({
  isStreaming,
  reduceMotion,
  orbState,
  isSearching,
  theme,
}: {
  isStreaming: boolean;
  reduceMotion: boolean;
  orbState: "searching" | "composing";
  isSearching: boolean;
  theme: ChatTheme;
}) {
  return (
    <span
      className="sr-chat-assistant-response__orb"
      data-visible={isStreaming ? "true" : "false"}
    >
      <AnimatePresence initial={false} mode="wait">
        {isStreaming ? (
          <motion.span
            key={orbState}
            className="sr-chat-assistant-response__orb-stage"
            initial={reduceMotion ? false : { opacity: 0, scale: 0.82, filter: "blur(4px)" }}
            animate={{ opacity: 1, scale: 1, filter: "blur(0px)" }}
            exit={reduceMotion ? undefined : { opacity: 0, scale: 0.86, filter: "blur(3px)" }}
            transition={{ duration: 0.18, ease: [0.22, 1, 0.36, 1] }}
          >
            <ThinkingOrb
              state={orbState}
              size={64}
              theme={theme}
              speed={0.9}
              aria-label={isSearching ? "Searching current sources" : "Deliberating"}
              style={{ width: 34, height: 34 }}
            />
          </motion.span>
        ) : null}
      </AnimatePresence>
    </span>
  );
}
function AssistantSettledExtras({
  turn,
  surfaces,
  onViewAlerts,
  onSeeArrivalsOnMap,
}: {
  turn: AssistantTurn;
  surfaces: ReturnType<typeof assistantSurfaces>;
  onViewAlerts?: () => void;
  onSeeArrivalsOnMap?: (arrivals: ArrivalsTurnPayload) => void;
}) {
  const arrivals = turn.arrivals;
  return (
    <>
      {surfaces.showSources && turn.sources ? <Sources sources={turn.sources} /> : null}
      {surfaces.showAlertsAction && onViewAlerts ? (
        <button type="button" className="sr-chat-transit-action" onClick={onViewAlerts}>
          View alerts
        </button>
      ) : null}
      {surfaces.showArrivals && arrivals ? (
        <ChatArrivalsCard
          arrivals={arrivals}
          onSeeOnMap={() => onSeeArrivalsOnMap?.(arrivals)}
        />
      ) : null}
    </>
  );
}

function AssistantRouteResults({
  show,
  reduceMotion,
  cards,
  selectedCardId,
  onSelect,
}: {
  show: boolean;
  reduceMotion: boolean;
  cards: RouteCard[];
  selectedCardId?: string | null;
  onSelect?: (card: RouteCard) => void;
}) {
  return (
    <AnimatePresence initial={false}>
      {show ? (
        <motion.div
          className="sr-chat-route-results"
          initial={reduceMotion ? false : { opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          exit={reduceMotion ? undefined : { opacity: 0, y: 4 }}
          transition={{ duration: 0.24, ease: [0.22, 1, 0.36, 1] }}
        >
          <ChatRouteCardList cards={cards} selectedCardId={selectedCardId} onSelect={onSelect} />
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}

function AssistantMessage({
  turn,
  theme,
  selectedCardId,
  onSelectRouteCard,
  onSeeArrivalsOnMap,
  onViewAlerts,
  onRetry,
  onDismissError,
}: {
  turn: AssistantTurn;
  theme: ChatTheme;
  selectedCardId?: string | null;
  onSelectRouteCard?: (card: RouteCard) => void;
  onSeeArrivalsOnMap?: (arrivals: ArrivalsTurnPayload) => void;
  onViewAlerts?: () => void;
  onRetry?: () => void;
  onDismissError?: () => void;
}) {
  const hasText = turn.text.length > 0;
  const reduceMotion = useReducedMotion() ?? false;
  const { displayedText, isCaughtUp } = useProgressiveText(turn.text, reduceMotion);
  const isSearching = turn.toolChips.some(
    (chip) => isSearchActivityTool(chip.tool) && chip.status === "running",
  );
  const surfaces = assistantSurfaces(turn, isCaughtUp);
  if (turn.error) {
    return (
      <AssistantErrorMessage
        message={turn.error.message}
        retryable={turn.error.retryable}
        onRetry={onRetry}
        onDismissError={onDismissError}
      />
    );
  }

  return (
    <div className="sr-chat-message sr-chat-message--assistant">
      <div
        className="sr-chat-assistant-response"
        data-orb-visible={turn.isStreaming ? "true" : "false"}
      >
        <AssistantStreamingOrb
          isStreaming={turn.isStreaming}
          reduceMotion={reduceMotion}
          orbState={isSearching ? "searching" : "composing"}
          isSearching={isSearching}
          theme={theme}
        />
        <div className="sr-chat-assistant-response__content">
          {turn.notice ? (
            <output className="sr-chat-session-notice">{turn.notice}</output>
          ) : null}
          <ChatWorkingPanel
            toolChips={turn.toolChips}
            progress={turn.progress}
            reasoning={turn.reasoning}
            isStreaming={turn.isStreaming}
          />
          {hasText ? (
            <p className="sr-chat-message__prose" aria-live="polite">
              {displayedText}
            </p>
          ) : null}
          <AssistantSettledExtras
            turn={turn}
            surfaces={surfaces}
            onViewAlerts={onViewAlerts}
            onSeeArrivalsOnMap={onSeeArrivalsOnMap}
          />
        </div>
      </div>
      <AssistantRouteResults
        show={surfaces.showCards}
        reduceMotion={reduceMotion}
        cards={turn.routeCards}
        selectedCardId={selectedCardId}
        onSelect={onSelectRouteCard}
      />
    </div>
  );
}

export function ChatMessage({
  turn,
  theme,
  selectedCardId,
  onSelectRouteCard,
  onSeeArrivalsOnMap,
  onViewAlerts,
  onRetry,
  onDismissError,
}: {
  turn: ChatTurn;
  theme: ChatTheme;
  selectedCardId?: string | null;
  onSelectRouteCard?: (card: RouteCard) => void;
  onSeeArrivalsOnMap?: (arrivals: ArrivalsTurnPayload) => void;
  onViewAlerts?: () => void;
  onRetry?: () => void;
  onDismissError?: () => void;
}) {
  if (turn.role === "user") {
    return (
      <Message className="sr-chat-message sr-chat-message--user">
        <MessageContent className="sr-chat-bubble">{turn.text}</MessageContent>
      </Message>
    );
  }
  if (turn.local) {
    return <LocalArrivalsMessage turn={turn} onSeeOnMap={onSeeArrivalsOnMap} />;
  }
  return (
    <AssistantMessage
      turn={turn}
      theme={theme}
      selectedCardId={selectedCardId}
      onSelectRouteCard={onSelectRouteCard}
      onSeeArrivalsOnMap={onSeeArrivalsOnMap}
      onViewAlerts={onViewAlerts}
      onRetry={onRetry}
      onDismissError={onDismissError}
    />
  );
}
