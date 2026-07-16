"use client";

/* ════════════════════════════════════════════════════════════════════════
   SmartRoute chat — one turn

   User turns render as a filled bubble on the right (iMessage-style). The
   assistant never gets bubble chrome — plan prose directly on the rail
   material, Apple-Maps-sheet style — with tool chips above the text and
   route cards below it. A blinking caret (the left rail's srReasoningCaret
   keyframe) marks the actively streaming assistant turn.
   ════════════════════════════════════════════════════════════════════════ */

import type { AssistantTurn, ChatTurn } from "@/lib/use-agent-chat";
import type { RouteCard } from "@/lib/agent-chat-stream";
import { ToolChipRow } from "./tool-chip";
import { ChatRouteCardList } from "./chat-route-card";

function AssistantMessage({
  turn,
  showCaret,
  onSelectRouteCard,
}: {
  turn: AssistantTurn;
  showCaret: boolean;
  onSelectRouteCard?: (card: RouteCard) => void;
}) {
  const hasText = turn.text.length > 0;
  return (
    <div className="sr-chat-message sr-chat-message--assistant">
      <ToolChipRow chips={turn.toolChips} />
      {(hasText || showCaret) && (
        <p className="sr-chat-message__prose">
          {turn.text}
          {showCaret && <span className="sr-chat-caret" aria-hidden="true" />}
        </p>
      )}
      <ChatRouteCardList cards={turn.routeCards} onSelect={onSelectRouteCard} />
      {turn.error && !hasText && (
        <p className="sr-chat-message__error">{turn.error.message}</p>
      )}
    </div>
  );
}

export function ChatMessage({
  turn,
  showCaret = false,
  onSelectRouteCard,
}: {
  turn: ChatTurn;
  /** True only for the last assistant turn while its stream is open. */
  showCaret?: boolean;
  onSelectRouteCard?: (card: RouteCard) => void;
}) {
  if (turn.role === "user") {
    return (
      <div className="sr-chat-message sr-chat-message--user">
        <p className="sr-chat-bubble">{turn.text}</p>
      </div>
    );
  }
  return <AssistantMessage turn={turn} showCaret={showCaret} onSelectRouteCard={onSelectRouteCard} />;
}
