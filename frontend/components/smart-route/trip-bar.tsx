"use client";

import { AudioLines, ArrowRight, Loader2 } from "lucide-react";

interface Props {
  originLabel: string;
  originSub?: string | null;
  inputValue: string;
  onInputChange: (v: string) => void;
  onSubmit: () => void;
  isLoading: boolean;
  onVoiceInput: () => void;
  isListening: boolean;
  accent: string;
}

export function TripBar({
  originLabel,
  originSub,
  inputValue,
  onInputChange,
  onSubmit,
  isLoading,
  onVoiceInput,
  isListening,
  accent,
}: Props) {
  return (
    <div
      className="flex items-stretch"
      style={{
        background: "rgba(14,18,26,0.85)",
        backdropFilter: "blur(10px)",
        WebkitBackdropFilter: "blur(10px)",
        border: "1px solid rgba(255,255,255,0.07)",
        borderRadius: 12,
        overflow: "hidden",
      }}
    >
      <div
        className="flex items-center gap-2.5"
        style={{ flex: 1, padding: "10px 14px" }}
      >
        <div
          style={{
            width: 8,
            height: 8,
            borderRadius: 4,
            border: "1.5px solid rgba(255,255,255,0.5)",
            flexShrink: 0,
          }}
        />
        <div className="min-w-0">
          <div
            style={{
              fontSize: 9,
              letterSpacing: "0.14em",
              color: "rgba(255,255,255,0.45)",
              fontFamily: "var(--font-geist), sans-serif",
            }}
          >
            FROM
          </div>
          <div
            className="truncate"
            style={{
              fontFamily: "var(--font-geist), sans-serif",
              fontSize: 13,
              color: "#fff",
            }}
          >
            {originLabel}
          </div>
          {originSub && (
            <div
              className="truncate"
              style={{
                fontFamily: "var(--font-geist), sans-serif",
                fontSize: 10,
                color: "rgba(255,255,255,0.45)",
              }}
            >
              {originSub}
            </div>
          )}
        </div>
      </div>

      <div style={{ width: 1, background: "rgba(255,255,255,0.07)" }} />

      <div
        className="flex items-center gap-2.5"
        style={{ flex: 2, padding: "10px 14px" }}
      >
        <div
          style={{
            width: 8,
            height: 8,
            borderRadius: 4,
            background: accent,
            flexShrink: 0,
            boxShadow: `0 0 8px ${accent}`,
          }}
        />
        <div className="flex-1 min-w-0">
          <div
            style={{
              fontSize: 9,
              letterSpacing: "0.14em",
              color: "rgba(255,255,255,0.45)",
              fontFamily: "var(--font-geist), sans-serif",
            }}
          >
            TO
          </div>
          <input
            type="text"
            value={inputValue}
            onChange={(e) => onInputChange(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && onSubmit()}
            placeholder="Where are you headed, sir?"
            disabled={isLoading}
            className="w-full bg-transparent outline-none"
            style={{
              fontFamily: "var(--font-geist), sans-serif",
              fontSize: 13,
              color: "#fff",
            }}
          />
        </div>
        <AudioLines
          size={16}
          className="cursor-pointer flex-shrink-0"
          style={{
            color: isListening ? accent : "rgba(255,255,255,0.45)",
            filter: isListening ? `drop-shadow(0 0 6px ${accent})` : undefined,
          }}
          onClick={onVoiceInput}
        />
      </div>

      <div style={{ width: 1, background: "rgba(255,255,255,0.07)" }} />

      <button
        onClick={onSubmit}
        disabled={isLoading}
        className="flex items-center gap-2 cursor-pointer disabled:opacity-40"
        style={{
          padding: "0 16px",
          background: `${accent}18`,
          border: "none",
          color: accent,
          fontFamily: "var(--font-geist), sans-serif",
          fontSize: 12,
          fontWeight: 600,
          letterSpacing: "0.05em",
        }}
      >
        {isLoading ? (
          <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} />
        ) : (
          <ArrowRight size={14} />
        )}
        PLAN
      </button>
    </div>
  );
}
