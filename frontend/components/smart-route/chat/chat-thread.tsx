"use client";

/* ════════════════════════════════════════════════════════════════════════
   SmartRoute chat — scrollable thread

   Auto-scrolls to the bottom as new content streams in, but only while the
   rider hasn't scrolled up to read something earlier — the same
   "don't yank control from someone who's reading" rule any chat UI needs.
   Tracked via scroll position: within ~24px of the bottom counts as
   "following"; anything further up disengages auto-scroll until the rider
   scrolls back down themselves.
   ════════════════════════════════════════════════════════════════════════ */

import { useEffect, useRef, useState, type ReactNode } from "react";
import type { ChatTurn } from "@/lib/use-agent-chat";
import type { RouteCard } from "@/lib/agent-chat-stream";
import { ChatMessage } from "./chat-message";

const FOLLOW_BOTTOM_THRESHOLD_PX = 24;

export function ChatThread({
  messages,
  isStreaming,
  onSelectRouteCard,
  emptyState,
}: {
  messages: ChatTurn[];
  isStreaming: boolean;
  onSelectRouteCard?: (card: RouteCard) => void;
  emptyState?: ReactNode;
}) {
  const scrollerRef = useRef<HTMLDivElement | null>(null);
  const [followBottom, setFollowBottom] = useState(true);

  function handleScroll() {
    const el = scrollerRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    setFollowBottom(distanceFromBottom <= FOLLOW_BOTTOM_THRESHOLD_PX);
  }

  useEffect(() => {
    if (!followBottom) return;
    const el = scrollerRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
    // Depends on the full message list identity plus streamed text length,
    // since token/tool_end/route_card events mutate the last turn in place
    // without changing `messages` array identity's length.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages, isStreaming, followBottom]);

  if (messages.length === 0) {
    return (
      <div className="sr-chat-thread sr-chat-thread--empty" ref={scrollerRef}>
        {emptyState}
      </div>
    );
  }

  return (
    <div className="sr-chat-thread" ref={scrollerRef} onScroll={handleScroll}>
      {messages.map((turn, index) => (
        <ChatMessage
          key={index}
          turn={turn}
          showCaret={isStreaming && index === messages.length - 1 && turn.role === "assistant"}
          onSelectRouteCard={onSelectRouteCard}
        />
      ))}
    </div>
  );
}
