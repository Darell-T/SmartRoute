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

import { ArrowUp, Mic, Square } from "lucide-react";
import {
  PromptInput,
  PromptInputAction,
  PromptInputActions,
  PromptInputTextarea,
} from "@/components/prompt-kit/prompt-input";
import { Button } from "@/components/ui/button";
import { useVoiceInput } from "@/lib/use-voice-input";
import type { ResponsePresentationMode } from "@/lib/response-presentation";
import type { ChatTheme } from "@/lib/use-chat-theme";
import { ResponseModeMenu } from "./response-mode-menu";

const MAX_MESSAGE_LENGTH = 500;
// ~4 lines at the composer's 15px/1.55 type + vertical padding.
const TEXTAREA_MAX_HEIGHT = 112;

export function ChatComposer({
  value,
  onValueChange,
  presentationMode,
  onPresentationModeChange,
  theme,
  onSend,
  onCancel,
  isStreaming,
}: {
  value: string;
  onValueChange: (value: string) => void;
  presentationMode: ResponsePresentationMode;
  onPresentationModeChange: (mode: ResponsePresentationMode) => void;
  theme: ChatTheme;
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
      <PromptInputActions className="sr-chat-composer__actions">
        <ResponseModeMenu
          value={presentationMode}
          theme={theme}
          onValueChange={onPresentationModeChange}
        />
        <PromptInputTextarea
          aria-label="Message SmartRoute"
          placeholder="Ask SmartRoute"
          maxLength={MAX_MESSAGE_LENGTH}
          className="sr-chat-composer__textarea"
        />
        {voice.isSupported && (
          <PromptInputAction
            tooltip={voice.isListening ? "Listening" : "Use voice input"}
          >
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
              <Mic size={18} strokeWidth={1.8} aria-hidden="true" />
            </Button>
          </PromptInputAction>
        )}
        <PromptInputAction tooltip={isStreaming ? "Stop response" : "Send message"}>
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
              <Square size={13} strokeWidth={2} fill="currentColor" aria-hidden="true" />
            ) : (
              <ArrowUp size={18} strokeWidth={2} aria-hidden="true" />
            )}
          </Button>
        </PromptInputAction>
      </PromptInputActions>
    </PromptInput>
  );
}
