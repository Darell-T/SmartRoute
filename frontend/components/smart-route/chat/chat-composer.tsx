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

import { ArrowUp, Microphone, Square } from "iconoir-react";
import {
  PromptInput,
  PromptInputActions,
  PromptInputTextarea,
} from "@/components/prompt-kit/prompt-input";
import { Button } from "@/components/ui/button";
import { useVoiceInput } from "@/lib/use-voice-input";

const MAX_MESSAGE_LENGTH = 500;
// ~4 lines at the composer's 15px/1.55 type + vertical padding.
const TEXTAREA_MAX_HEIGHT = 112;

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
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="sr-chat-mic-button"
            data-listening={voice.isListening ? "true" : "false"}
            aria-label={voice.isListening ? "Listening" : "Use voice input"}
            aria-pressed={voice.isListening}
            onClick={voice.start}
          >
            <Microphone width={19} height={19} strokeWidth={1.7} aria-hidden="true" />
          </Button>
        )}
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="sr-chat-send-button"
          data-mode={isStreaming ? "stop" : "send"}
          data-ready={canSend ? "true" : "false"}
          aria-label={isStreaming ? "Stop" : "Send message"}
          disabled={!isStreaming && !canSend}
          onClick={isStreaming ? onCancel : submit}
        >
          {isStreaming ? (
            <Square width={14} height={14} strokeWidth={2} fill="currentColor" aria-hidden="true" />
          ) : (
            <ArrowUp width={18} height={18} strokeWidth={1.9} aria-hidden="true" />
          )}
        </Button>
      </PromptInputActions>
    </PromptInput>
  );
}
