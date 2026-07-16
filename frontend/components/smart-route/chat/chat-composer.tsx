"use client";

/* ════════════════════════════════════════════════════════════════════════
   SmartRoute chat — composer

   Bottom input, styled like the existing destination search pill
   (`.sr-input-group` in route-view.tsx / smart-route-left-rail.css) so the
   chat feature reads as part of the same product, not a bolted-on widget.
   The send button becomes a stop button while streaming.

   Controlled by the parent (`ChatPanel`) rather than owning its own text
   state, so an empty-state suggestion chip can fill the draft without a
   fake keyboard event.
   ════════════════════════════════════════════════════════════════════════ */

import type { FormEvent, KeyboardEvent } from "react";
import { ArrowUp } from "lucide-react";

const MAX_MESSAGE_LENGTH = 500;

export function ChatComposer({
  value,
  onValueChange,
  onSend,
  onCancel,
  isStreaming,
}: {
  value: string;
  onValueChange: (value: string) => void;
  onSend: (text: string) => void;
  onCancel: () => void;
  isStreaming: boolean;
}) {
  function submit() {
    const trimmed = value.trim();
    if (!trimmed || isStreaming) return;
    onSend(trimmed);
    onValueChange("");
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    submit();
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  }

  const canSend = value.trim().length > 0 && !isStreaming;

  return (
    <form className="sr-chat-composer sr-input-group" onSubmit={handleSubmit}>
      <textarea
        aria-label="Message SmartRoute"
        value={value}
        maxLength={MAX_MESSAGE_LENGTH}
        placeholder="Ask about a route…"
        rows={1}
        disabled={isStreaming}
        onChange={(event) => onValueChange(event.target.value)}
        onKeyDown={handleKeyDown}
      />
      <button
        type={isStreaming ? "button" : "submit"}
        className="sr-input-submit"
        aria-label={isStreaming ? "Stop" : "Send message"}
        data-filled={isStreaming || canSend ? "true" : "false"}
        disabled={!isStreaming && !canSend}
        onClick={isStreaming ? onCancel : undefined}
      >
        {isStreaming ? (
          <span className="sr-input-stop-icon" aria-hidden="true" />
        ) : (
          <ArrowUp size={21} strokeWidth={2.25} aria-hidden="true" />
        )}
      </button>
    </form>
  );
}
