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

import type { ArrivalsTurnPayload, AssistantTurn, ChatTurn } from "@/lib/use-agent-chat";
import type { RouteCard } from "@/lib/agent-chat-stream";
import type { ChatTheme } from "@/lib/use-chat-theme";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { ThinkingOrb } from "thinking-orbs";
import { Message, MessageContent } from "@/components/prompt-kit/message";
import { Sources } from "@/components/prompt-kit/source";
import { isSearchActivityTool } from "@/lib/agent-route-tools";
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
  if (!turn.arrivals) return null;
  return (
    <div className="sr-chat-message sr-chat-message--assistant">
      {turn.text && <p className="sr-chat-message__prose">{turn.text}</p>}
      <ChatArrivalsCard
        arrivals={turn.arrivals}
        onSeeOnMap={() => onSeeOnMap?.(turn.arrivals!)}
      />
    </div>
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
  const orbState = isSearching ? "searching" : "composing";
  const showCards = !turn.isStreaming && hasText && isCaughtUp && turn.routeCards.length > 0;
  const showArrivals = !turn.isStreaming && isCaughtUp && Boolean(turn.arrivals);
  const showAlertsAction =
    !turn.isStreaming &&
    isCaughtUp &&
    turn.transitStatusAction === "view_alerts" &&
    Boolean(onViewAlerts);
  const showSources = !turn.isStreaming && isCaughtUp && Boolean(turn.sources?.length);

  if (turn.error) {
    return (
      <div className="sr-chat-message sr-chat-message--assistant">
        <div className="sr-chat-turn-error" role="alert">
          <p>{turn.error.message}</p>
          <div className="sr-chat-turn-error__actions">
            {turn.error.retryable && onRetry ? (
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

  return (
    <div className="sr-chat-message sr-chat-message--assistant">
      <div
        className="sr-chat-assistant-response"
        data-orb-visible={turn.isStreaming ? "true" : "false"}
      >
        <span
          className="sr-chat-assistant-response__orb"
          data-visible={turn.isStreaming ? "true" : "false"}
        >
          <AnimatePresence initial={false} mode="wait">
            {turn.isStreaming ? (
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
        <div className="sr-chat-assistant-response__content">
          {turn.notice ? (
            <output className="sr-chat-session-notice">
              {turn.notice}
            </output>
          ) : null}
          <ChatWorkingPanel
            toolChips={turn.toolChips}
            progress={turn.progress}
            reasoning={turn.reasoning}
            isStreaming={turn.isStreaming}
          />
          {hasText && (
            <p className="sr-chat-message__prose" aria-live="polite">
              {displayedText}
            </p>
          )}
          {showSources && turn.sources ? <Sources sources={turn.sources} /> : null}
          {showAlertsAction ? (
            <button
              type="button"
              className="sr-chat-transit-action"
              onClick={onViewAlerts}
            >
              View alerts
            </button>
          ) : null}
          {showArrivals && turn.arrivals ? (
            <ChatArrivalsCard
              arrivals={turn.arrivals}
              onSeeOnMap={() => onSeeArrivalsOnMap?.(turn.arrivals!)}
            />
          ) : null}
        </div>
      </div>
      <AnimatePresence initial={false}>
        {showCards ? (
          <motion.div
            className="sr-chat-route-results"
            initial={reduceMotion ? false : { opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={reduceMotion ? undefined : { opacity: 0, y: 4 }}
            transition={{ duration: 0.24, ease: [0.22, 1, 0.36, 1] }}
          >
            <ChatRouteCardList
              cards={turn.routeCards}
              selectedCardId={selectedCardId}
              onSelect={onSelectRouteCard}
            />
          </motion.div>
        ) : null}
      </AnimatePresence>
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
