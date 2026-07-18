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

import type { AssistantTurn, ChatTurn } from "@/lib/use-agent-chat";
import type { RouteCard } from "@/lib/agent-chat-stream";
import { Message, MessageContent } from "@/components/prompt-kit/message";
import { ChatWorkingPanel } from "./chat-working-panel";
import { ChatRouteCardList } from "./chat-route-card";
import { ChatArrivalsCard } from "./chat-arrivals-card";

function LocalArrivalsMessage({ turn, onSeeOnMap }: { turn: AssistantTurn; onSeeOnMap?: () => void }) {
  if (!turn.arrivals) return null;
  return (
    <div className="sr-chat-message sr-chat-message--assistant">
      {turn.text && <p className="sr-chat-message__prose">{turn.text}</p>}
      <ChatArrivalsCard arrivals={turn.arrivals} onSeeOnMap={onSeeOnMap} />
    </div>
  );
}

function AssistantMessage({
  turn,
  showCaret,
  selectedCardId,
  onSelectRouteCard,
}: {
  turn: AssistantTurn;
  showCaret: boolean;
  selectedCardId?: string | null;
  onSelectRouteCard?: (card: RouteCard) => void;
}) {
  const hasText = turn.text.length > 0;
  return (
    <div className="sr-chat-message sr-chat-message--assistant">
      <ChatWorkingPanel toolChips={turn.toolChips} isStreaming={turn.isStreaming} />
      {(hasText || showCaret) && (
        <p className="sr-chat-message__prose">
          {turn.text}
          {showCaret && <span className="sr-chat-caret" aria-hidden="true" />}
        </p>
      )}
      <ChatRouteCardList
        cards={turn.routeCards}
        selectedCardId={selectedCardId}
        onSelect={onSelectRouteCard}
      />
      {turn.error && !hasText && <p className="sr-chat-message__error">{turn.error.message}</p>}
    </div>
  );
}

export function ChatMessage({
  turn,
  showCaret = false,
  selectedCardId,
  onSelectRouteCard,
  onSeeArrivalsOnMap,
}: {
  turn: ChatTurn;
  /** True only for the last assistant turn while its stream is open. */
  showCaret?: boolean;
  selectedCardId?: string | null;
  onSelectRouteCard?: (card: RouteCard) => void;
  onSeeArrivalsOnMap?: () => void;
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
      showCaret={showCaret}
      selectedCardId={selectedCardId}
      onSelectRouteCard={onSelectRouteCard}
    />
  );
}
