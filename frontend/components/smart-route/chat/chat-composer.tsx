"use client";

/* ════════════════════════════════════════════════════════════════════════
   SmartRoute chat — composer

   Vendored prompt-kit PromptInput pill (frontend/components/prompt-kit/
   prompt-input.tsx). Placeholder "Ask SmartRoute"; actions are mic (new
   `use-voice-input` hook, replicating the left rail's proven recognition
   pattern) and a send/stop circle — no attachment/plus icons per spec.
   Textarea auto-grows to ~4 lines then scrolls, and stays enabled while a
   turn streams (only the button becomes Stop).

   Controlled by the parent (ChatPanel) rather than owning its own text
   state, so an empty-state suggestion pill can fill the draft without a
   fake keyboard event.
   ════════════════════════════════════════════════════════════════════════ */

import { Mic, Square } from "lucide-react";
import {
  PromptInput,
  PromptInputActions,
  PromptInputTextarea,
} from "@/components/prompt-kit/prompt-input";
import { useVoiceInput } from "@/lib/use-voice-input";

const MAX_MESSAGE_LENGTH = 500;
// ~4 lines at the composer's 15px/1.55 type + vertical padding.
const TEXTAREA_MAX_HEIGHT = 112;

function SendGlyph() {
  return (
    <svg width={16} height={16} viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M8 13V3M8 3 3.5 7.5M8 3l4.5 4.5"
        stroke="currentColor"
        strokeWidth={1.8}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

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

  const voice = useVoiceInput((transcript) => {
    onValueChange(value ? `${value} ${transcript}` : transcript);
  });

  const canSend = value.trim().length > 0 && !isStreaming;

  return (
    <PromptInput
      value={value}
      onValueChange={onValueChange}
      onSubmit={submit}
      maxHeight={TEXTAREA_MAX_HEIGHT}
      className="sr-chat-composer"
    >
      <PromptInputTextarea
        aria-label="Message SmartRoute"
        placeholder="Ask SmartRoute"
        maxLength={MAX_MESSAGE_LENGTH}
        className="sr-chat-composer__textarea"
      />
      <PromptInputActions className="sr-chat-composer__actions">
        {voice.isSupported && (
          <button
            type="button"
            className="sr-chat-mic-button"
            data-listening={voice.isListening ? "true" : "false"}
            aria-label={voice.isListening ? "Listening" : "Use voice input"}
            aria-pressed={voice.isListening}
            onClick={voice.start}
          >
            <Mic size={16} strokeWidth={1.8} aria-hidden="true" />
          </button>
        )}
        <button
          type="button"
          className="sr-chat-send-button"
          data-mode={isStreaming ? "stop" : "send"}
          aria-label={isStreaming ? "Stop" : "Send message"}
          disabled={!isStreaming && !canSend}
          onClick={isStreaming ? onCancel : submit}
        >
          {isStreaming ? (
            <Square size={13} strokeWidth={2} fill="currentColor" aria-hidden="true" />
          ) : (
            <SendGlyph />
          )}
        </button>
      </PromptInputActions>
    </PromptInput>
  );
}
