"use client";

import { useMemo, useState } from "react";
import {
  Accordion,
  BlurFade,
  MagicCard,
  NumberTicker,
  PhraseReveal,
} from "./fx";
import {
  Dot,
  BusChip,
  LineBullet,
  Meta,
  StepIcon,
  btnGhost,
  btnPrimary,
} from "./atoms";
import { RailOrb, jarvisStateToOrbTone, type RailOrbTone } from "./rail-orb";
import { Loader2, Mic2, X } from "lucide-react";
import type { LiveFeedIncident } from "@/types/api";
import type { MapboxSearchSuggestion } from "@/lib/mapbox-search";
import { useDestinationSearch } from "@/lib/use-destination-search";
import type {
  Arrival,
  Direction,
  JarvisState,
  NetworkHealth,
  RoutePlan,
  Station,
} from "./types";
import { incidentTone, incidentTimeLabel } from "./incident-format";
import type { RailSearchProps } from "./left-rail";
export function RouteView({
  station,
  health: _health,
  arrivals,
  plan,
  incidents,
  atlasScanOn,
  way,
  onWayChange,
  jarvisState,
  onJarvisStateChange,
  isSpeaking,
  thinkingText,
  onSelectAlternative,
  onSelectIncident,
  search,
}: {
  station: Station;
  health: NetworkHealth;
  arrivals: Arrival[];
  plan: RoutePlan;
  incidents: LiveFeedIncident[];
  atlasScanOn: boolean;
  way: Direction;
  onWayChange: (d: Direction) => void;
  jarvisState: JarvisState;
  onJarvisStateChange: (s: JarvisState) => void;
  isSpeaking?: boolean;
  thinkingText?: string;
  onSelectAlternative?: (candidateId: string) => void;
  onSelectIncident?: (incident: LiveFeedIncident) => void;
  search?: RailSearchProps;
}) {
  return (
    <>
      <SearchBlock
        onSubmit={() => onJarvisStateChange("thinking")}
        search={search}
      />
      <JarvisBlock
        state={jarvisState}
        onStateChange={onJarvisStateChange}
        plan={plan}
        thinkingText={thinkingText}
        onSelectAlternative={onSelectAlternative}
      />
      <ArrivalsSection
        station={station}
        arrivals={arrivals}
        way={way}
        onWayChange={onWayChange}
      />
      <IncidentsSection
        station={station}
        incidents={incidents}
        atlasScanOn={atlasScanOn}
        onSelectIncident={onSelectIncident}
      />
    </>
  );
}

/* ── SearchBlock ──────────────────────────────────────────────── */

function SearchBlock({
  onSubmit,
  search,
}: {
  onSubmit?: (q: string) => void;
  search?: RailSearchProps;
}) {
  // Demo mode (no `search` bundle): self-contained input, no API.
  const [localVal, setLocalVal] = useState("");
  const [focused, setFocused] = useState(false);
  const wired = Boolean(search);
  const value = wired ? search!.inputValue : localVal;

  const destinationSearch = useDestinationSearch({
    inputValue: wired ? search!.inputValue : "",
    enabled: wired && focused,
    isLoading: search?.isLoading ?? false,
  });
  const {
    suggestions,
    highlightedIndex,
    setHighlightedIndex,
    choose,
    isResolving,
    clearSuggestions,
    markInputEdited,
    markSelectedLabel,
    resetSession,
  } = destinationSearch;

  function handleChange(next: string) {
    if (wired) {
      markInputEdited();
      search!.onInputChange(next);
    } else {
      setLocalVal(next);
    }
  }

  async function handleChoose(suggestion: MapboxSearchSuggestion) {
    const selection = await choose(suggestion);
    if (!selection) {
      // Coordinate resolution failed (Mapbox retrieve error). Don't strand
      // the user with an open dropdown: submit the suggestion label as free
      // text and let the trip API geocode it.
      search?.onInputChange(suggestion.label);
      clearSuggestions();
      resetSession();
      search?.onSubmit(suggestion.label, null);
      return;
    }
    search?.onInputChange(selection.label);
    search?.onSubmit(selection.label, selection);
  }

  function handleSubmitForm() {
    const query = value.trim();
    if (!query) return;
    if (wired) {
      // Stop the debounced fetch from reopening the dropdown over the rail
      // once the trip request is already in flight.
      markSelectedLabel(query);
      clearSuggestions();
      resetSession();
      setFocused(false);
      if (document.activeElement instanceof HTMLElement) {
        document.activeElement.blur();
      }
      search!.onSubmit(query, null);
    } else {
      onSubmit?.(query);
    }
  }

  return (
    <div style={{ padding: "18px 24px 6px", position: "relative" }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 8,
        }}
      >
        <Meta tone="ink" style={{ letterSpacing: "0.16em" }}>
          Where to
        </Meta>
        <Meta>Natural language ok</Meta>
      </div>
      <form
        className="sr-liquid-control sr-search-glass"
        onSubmit={(e) => {
          e.preventDefault();
          handleSubmitForm();
        }}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          background: "transparent",
          border: "1px solid transparent",
          padding: "10px 12px",
        }}
      >
        <span
          style={{
            fontFamily: "var(--sr-mono)",
            fontSize: 10,
            letterSpacing: "0.16em",
            color: "var(--sr-cyan)",
          }}
        >
          ›_
        </span>
        <input
          value={value}
          onChange={(e) => handleChange(e.target.value)}
          onFocus={() => setFocused(true)}
          onBlur={() => {
            // Delay so suggestion onMouseDown/click can land first.
            setTimeout(() => setFocused(false), 150);
          }}
          onKeyDown={(e) => {
            if (!wired || suggestions.length === 0) return;
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setHighlightedIndex((highlightedIndex + 1) % suggestions.length);
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setHighlightedIndex(
                highlightedIndex === 0 ? suggestions.length - 1 : highlightedIndex - 1,
              );
            } else if (e.key === "Enter" && suggestions[highlightedIndex]) {
              e.preventDefault();
              void handleChoose(suggestions[highlightedIndex]);
            } else if (e.key === "Escape") {
              clearSuggestions();
            }
          }}
          placeholder="Search destination — Atlantic Term., JFK, 774 Grand St"
          disabled={search?.isLoading ?? false}
          autoComplete="off"
          style={{
            flex: 1,
            background: "transparent",
            border: 0,
            outline: "none",
            fontFamily: "var(--sr-display)",
            fontWeight: 400,
            fontSize: 14,
            color: "var(--sr-fg)",
            letterSpacing: "-0.005em",
          }}
        />
        {wired ? (
          <>
            <button
              type="button"
              onClick={search!.onVoiceInput}
              aria-label="Use voice input"
              title="Voice input"
              style={{
                background: "transparent",
                border: 0,
                cursor: "pointer",
                padding: 0,
                lineHeight: 0,
                color: search!.isListening ? "var(--sr-cyan)" : "var(--sr-muted)",
              }}
            >
              <Mic2 size={14} strokeWidth={1.7} aria-hidden="true" />
            </button>
            {search!.hasActiveRoute && (
              <button
                type="button"
                onClick={search!.onClear}
                aria-label="Clear route"
                title="Clear route"
                style={{
                  background: "transparent",
                  border: 0,
                  cursor: "pointer",
                  padding: 0,
                  lineHeight: 0,
                  color: "var(--sr-muted)",
                }}
              >
                <X size={14} strokeWidth={1.7} aria-hidden="true" />
              </button>
            )}
            {(search!.isLoading || isResolving) && (
              <Loader2
                size={13}
                strokeWidth={1.8}
                aria-hidden="true"
                style={{ color: "var(--sr-cyan)", animation: "spin 1s linear infinite" }}
              />
            )}
          </>
        ) : (
          <Meta>⌘K</Meta>
        )}
      </form>
      {wired && focused && suggestions.length > 0 && (
        <div
          className="sr-suggestion-popover"
          role="listbox"
          style={{
            position: "absolute",
            left: 24,
            right: 24,
            zIndex: 30,
            // Glass chip: the surface token is translucent now, so the
            // dropdown needs its own backdrop blur to stay legible over
            // the rail content beneath it.
            background: "rgba(14, 19, 30, 0.78)",
            backdropFilter: "var(--sr-glass-blur)",
            WebkitBackdropFilter: "var(--sr-glass-blur)",
            border: "1px solid var(--sr-glass-border)",
            borderRadius: 14,
            overflow: "hidden",
            boxShadow: "0 14px 30px rgba(0,0,0,0.45), inset 0 1px 0 var(--sr-glass-specular)",
          }}
        >
          {suggestions.map((suggestion, index) => (
            <button
              key={suggestion.id}
              type="button"
              role="option"
              aria-selected={index === highlightedIndex}
              onMouseDown={(e) => e.preventDefault()}
              onMouseEnter={() => setHighlightedIndex(index)}
              onClick={() => void handleChoose(suggestion)}
              style={{
                display: "block",
                width: "100%",
                textAlign: "left",
                padding: "9px 12px",
                background:
                  index === highlightedIndex ? "var(--sr-surface-2)" : "transparent",
                border: 0,
                borderTop: index === 0 ? "none" : "1px solid var(--sr-rule)",
                cursor: "pointer",
              }}
            >
              <span
                style={{
                  display: "block",
                  fontFamily: "var(--sr-display)",
                  fontSize: 13,
                  color: "var(--sr-fg)",
                }}
              >
                {suggestion.label.split(",")[0]?.trim() || suggestion.label}
              </span>
              <span
                style={{
                  display: "block",
                  marginTop: 2,
                  fontFamily: "var(--sr-mono)",
                  fontSize: 9.5,
                  letterSpacing: "0.04em",
                  color: "var(--sr-muted)",
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
              >
                {suggestion.label}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/* ── JarvisBlock ──────────────────────────────────────────────── */

function JarvisBlock({
  state,
  onStateChange,
  plan,
  isSpeaking,
  thinkingText,
  onSelectAlternative,
}: {
  state: JarvisState;
  onStateChange: (next: JarvisState) => void;
  plan: RoutePlan;
  isSpeaking?: boolean;
  thinkingText?: string;
  onSelectAlternative?: (candidateId: string) => void;
}) {
  const tone: RailOrbTone = jarvisStateToOrbTone(state);
  const orbPhase: "idle" | "thinking" | "speaking" =
    state === "thinking" ? "thinking" : state === "result" ? "speaking" : "idle";

  const COPY: Record<"standby" | "error", { headline: string; sub: string }> = {
    standby: {
      headline: "Where are we going?",
      sub: "Type a destination and ATLAS will pick the best route.",
    },
    error: {
      headline: "ATLAS couldn't build a reliable route.",
      sub: "No route found. Try a more specific address.",
    },
  };

  const stateLabel =
    state === "result" ? "answered" : state === "thinking" ? "thinking" : state;
  const stateDotColor =
    state === "error"
      ? "var(--sr-coral)"
      : state === "thinking"
      ? "var(--sr-amber)"
      : "var(--sr-cyan)";
  const activeThinkingText =
    thinkingText?.trim() || "Scanning live feeds, alerts, and route options...";

  return (
    <section
      className="sr-liquid-card sr-jarvis-panel"
      style={{ padding: "22px 24px 18px", position: "relative" }}
    >
      {state === "thinking" && (
        <span
          aria-hidden="true"
          style={{
            position: "absolute",
            inset: 14,
            pointerEvents: "none",
            background:
              "conic-gradient(from var(--sr-beam-angle), transparent 0%, transparent 70%, var(--sr-cyan) 85%, transparent 100%)",
            WebkitMask:
              "linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0)",
            WebkitMaskComposite: "xor",
            maskComposite: "exclude",
            padding: 1,
            animation: "srBeamRotate 2.6s linear infinite",
          }}
        />
      )}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 14,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span
            style={{
              fontFamily: "var(--sr-mono)",
              fontSize: 11,
              letterSpacing: "0.28em",
              color: "var(--sr-cyan)",
              fontWeight: 600,
            }}
          >
            ATLAS
          </span>
          <Meta>
            <Dot
              color={stateDotColor}
              size={5}
              pulse
              style={{ marginRight: 6, verticalAlign: "middle" }}
            />
            {stateLabel}
          </Meta>
        </div>
        <Meta>
          Today ·{" "}
          {new Date().toLocaleTimeString([], {
            hour: "numeric",
            minute: "2-digit",
          })}
        </Meta>
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "auto 1fr",
          gap: 16,
          alignItems: "flex-start",
        }}
      >
        <RailOrb size={64} tone={tone} phase={orbPhase} />
        <div style={{ minWidth: 0 }}>
          {state === "thinking" ? (
            <div
              title={activeThinkingText}
              style={{
                fontFamily: "var(--sr-display)",
                fontWeight: 400,
                fontStyle: "italic",
                fontSize: 16,
                lineHeight: 1.4,
                color: "var(--sr-fg-2)",
                letterSpacing: "-0.01em",
                minHeight: "3em",
                maxHeight: "4.2em",
                display: "-webkit-box",
                WebkitLineClamp: 3,
                WebkitBoxOrient: "vertical",
                overflow: "hidden",
              }}
            >
              <PhraseReveal text={activeThinkingText} />
            </div>
          ) : state === "result" ? (
            <ResultHead plan={plan} />
          ) : (
            <>
              <h2
                style={{
                  fontFamily: "var(--sr-display)",
                  fontWeight: 500,
                  fontSize: 19,
                  lineHeight: 1.25,
                  letterSpacing: "-0.015em",
                  color: "var(--sr-fg)",
                }}
              >
                {COPY[state].headline}
              </h2>
              <p
                style={{
                  marginTop: 8,
                  fontFamily: "var(--sr-display)",
                  fontWeight: 400,
                  fontSize: 13,
                  lineHeight: 1.5,
                  color: "var(--sr-fg-3)",
                }}
              >
                {COPY[state].sub}
              </p>
            </>
          )}
        </div>
      </div>

      {state === "error" && (
        <div style={{ marginTop: 14, display: "flex", gap: 8 }}>
          <button onClick={() => onStateChange("thinking")} style={btnPrimary()}>
            Try again
          </button>
          <button onClick={() => onStateChange("standby")} style={btnGhost()}>
            Clear
          </button>
        </div>
      )}
      {state === "standby" && (
        <div style={{ marginTop: 12 }}>
          <Meta>Try · Atlantic Term. · JFK · 774 Grand St · LGA</Meta>
        </div>
      )}
      {state === "thinking" && (
        <div
          style={{
            marginTop: 14,
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          <div style={{ display: "flex", gap: 3 }}>
            {[0, 1, 2].map((i) => (
              <span
                key={i}
                style={{
                  width: 4,
                  height: 4,
                  borderRadius: "50%",
                  background: "var(--sr-cyan)",
                  animation: `srOrbBreath 1.4s ease-in-out ${i * 0.18}s infinite alternate`,
                }}
              />
            ))}
          </div>
          <Meta>Narration warming up…</Meta>
        </div>
      )}

      {state === "result" && (
        <ResultBody
          plan={plan}
          isSpeaking={isSpeaking}
          onSelectAlternative={onSelectAlternative}
        />
      )}
    </section>
  );
}

function ResultHead({ plan }: { plan: RoutePlan }) {
  return (
    <>
      <h2
        style={{
          fontFamily: "var(--sr-display)",
          fontWeight: 500,
          fontSize: 19,
          lineHeight: 1.25,
          letterSpacing: "-0.015em",
          color: "var(--sr-fg)",
          display: "-webkit-box",
          WebkitLineClamp: 2,
          WebkitBoxOrient: "vertical",
          overflow: "hidden",
        }}
        title={plan.headline}
      >
        <PhraseReveal text={plan.headline} />
      </h2>
      <p
        style={{
          marginTop: 8,
          fontFamily: "var(--sr-display)",
          fontWeight: 400,
          fontSize: 13,
          lineHeight: 1.5,
          color: "var(--sr-fg-2)",
          // Full narration text, no line clamp -- ATLAS's spoken recommendation
          // is shown in its entirety. Cap height defensively on tiny viewports.
          maxHeight: 240,
          overflowY: "auto",
        }}
        title={plan.rationale}
      >
        <PhraseReveal text={plan.rationale} />
      </p>
      <div
        style={{
          marginTop: 10,
          display: "flex",
          alignItems: "center",
          gap: 14,
          flexWrap: "wrap",
        }}
      >
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            padding: "3px 8px",
            border: "1px solid var(--sr-rule-bright)",
          }}
        >
          <LineBullet line={plan.pickedLine} size={16} />
          <Meta tone="ink" style={{ fontSize: 9.5, letterSpacing: "0.16em" }}>
            Picked
          </Meta>
        </span>
        <Meta tone="cyan">
          <Dot
            color="var(--sr-cyan)"
            size={4}
            pulse
            style={{ marginRight: 6, verticalAlign: "middle" }}
          />
          ETA {plan.eta} · {plan.totalTime}
        </Meta>
      </div>
    </>
  );
}

function ResultBody({
  plan,
  isSpeaking,
  onSelectAlternative,
}: {
  plan: RoutePlan;
  isSpeaking?: boolean;
  onSelectAlternative?: (candidateId: string) => void;
}) {
  type Pane = "plan" | "alts";
  const [pane, setPane] = useState<Pane>("plan");

  const TABS: { k: Pane; label: string; badge: number }[] = [
    { k: "plan", label: "Plan", badge: plan.steps.length },
    { k: "alts", label: "Alternatives", badge: plan.alternatives.length },
  ];

  return (
    <div
      style={{
        marginTop: 18,
        paddingTop: 14,
        borderTop: "1px solid var(--sr-rule)",
      }}
    >
      {/* Live narration indicator — the waveform animates ONLY while the
          narration audio is actually playing (driven by isSpeaking), then
          settles to a quiet "Spoken" state when ATLAS finishes. */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          marginBottom: 14,
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 2,
            height: 14,
          }}
        >
          {[3, 7, 4, 9, 5, 8, 4, 6, 3, 7, 5, 4].map((h, i) => (
            <span
              key={i}
              style={{
                display: "inline-block",
                width: 2,
                height: h,
                background: isSpeaking ? "var(--sr-cyan)" : "var(--sr-muted)",
                animation: `srWave 0.9s ease-in-out ${i * 0.05}s infinite alternate`,
                animationPlayState: isSpeaking ? "running" : "paused",
                opacity: isSpeaking ? 1 : 0.5,
                transformOrigin: "center",
              }}
            />
          ))}
        </div>
        <Meta tone={isSpeaking ? "cyan" : "muted"} style={{ letterSpacing: "0.16em" }}>
          {isSpeaking ? "ATLAS speaking" : "Spoken"}
        </Meta>
        <span style={{ flex: 1, height: 1, background: "var(--sr-rule)" }} />
      </div>

      {/* Sub-tabs */}
      <div
        style={{
          display: "flex",
          gap: 0,
          borderBottom: "1px solid var(--sr-rule)",
        }}
      >
        {TABS.map((t) => {
          const active = pane === t.k;
          return (
            <button
              key={t.k}
              onClick={() => setPane(t.k)}
              style={{
                background: "transparent",
                border: 0,
                cursor: "pointer",
                padding: "8px 0 10px",
                marginRight: 18,
                fontFamily: "var(--sr-mono)",
                fontSize: 10,
                letterSpacing: "0.16em",
                textTransform: "uppercase",
                color: active ? "var(--sr-fg)" : "var(--sr-muted)",
                borderBottom: active
                  ? "1.5px solid var(--sr-cyan)"
                  : "1.5px solid transparent",
                marginBottom: -1,
                display: "inline-flex",
                alignItems: "center",
                gap: 8,
              }}
            >
              {t.label}
              <span
                style={{
                  fontFamily: "var(--sr-mono)",
                  fontSize: 9.5,
                  padding: "1px 5px",
                  background: active
                    ? "rgba(216,155,43,0.2)"
                    : "rgba(255,255,255,0.06)",
                  color: active ? "var(--sr-cyan)" : "var(--sr-muted)",
                }}
              >
                {t.badge}
              </span>
            </button>
          );
        })}
      </div>

      <div key={pane} className="sr-fade-in" style={{ paddingTop: 14 }}>
        {pane === "plan" && <RouteSteps plan={plan} />}
        {pane === "alts" && (
          <Alternatives plan={plan} onSelectAlternative={onSelectAlternative} />
        )}
      </div>
    </div>
  );
}

function RouteSteps({ plan }: { plan: RoutePlan }) {
  return (
    <ol style={{ listStyle: "none", position: "relative", margin: 0, padding: 0 }}>
      <span
        aria-hidden="true"
        style={{
          position: "absolute",
          left: 13,
          top: 18,
          bottom: 18,
          width: 1,
          background: "var(--sr-rule-bright)",
        }}
      />
      {plan.steps.map((s, i) => {
        const isLast = i === plan.steps.length - 1;
        const isBoard = s.type === "board";
        const isFirst = i === 0;
        // Depart node reads cyan; the destination/arrive node reads warm amber
        // so the start and finish of the trip are visually distinct.
        const nodeBg = isFirst
          ? "var(--sr-cyan)"
          : isLast
          ? "var(--sr-amber)"
          : "var(--sr-surface-2)";
        const nodeRing = isFirst
          ? "var(--sr-cyan)"
          : isLast
          ? "var(--sr-amber)"
          : "var(--sr-rule-bright)";
        const iconColor =
          isFirst || isLast
            ? "#241704"
            : isBoard
            ? "var(--sr-cyan)"
            : "var(--sr-fg-2)";
        return (
          <li
            key={i}
            className="sr-slide-in"
            style={{
              animationDelay: `${i * 65}ms`,
              display: "grid",
              gridTemplateColumns: "28px 1fr auto",
              gap: 12,
              padding: "10px 0",
              position: "relative",
              alignItems: "start",
            }}
          >
            <span
              style={{
                width: 28,
                height: 28,
                borderRadius: "50%",
                background: nodeBg,
                border: `1px solid ${nodeRing}`,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                zIndex: 1,
              }}
            >
              <StepIcon type={s.type} color={iconColor} />
            </span>
            <div style={{ minWidth: 0 }}>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  flexWrap: "wrap",
                }}
              >
                <span
                  style={{
                    fontFamily: "var(--sr-mono)",
                    fontSize: 9.5,
                    letterSpacing: "0.16em",
                    color: isBoard ? "var(--sr-cyan)" : "var(--sr-fg-3)",
                    fontWeight: 600,
                    textTransform: "uppercase",
                  }}
                >
                  {s.action}
                </span>
                {s.line && <LineBullet line={s.line} size={18} />}
                <span
                  style={{
                    fontFamily: "var(--sr-display)",
                    fontSize: 14,
                    fontWeight: 500,
                    color: "var(--sr-fg)",
                    letterSpacing: "-0.005em",
                  }}
                >
                  {s.title}
                </span>
              </div>
              <Meta style={{ display: "block", marginTop: 4, lineHeight: 1.5 }}>
                {s.detail}
              </Meta>
            </div>
            <span
              style={{
                fontFamily: "var(--sr-mono)",
                fontSize: 11,
                color: "var(--sr-fg-2)",
                letterSpacing: "0.04em",
                whiteSpace: "nowrap",
              }}
            >
              {s.duration}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

function Alternatives({
  plan,
  onSelectAlternative,
}: {
  plan: RoutePlan;
  onSelectAlternative?: (candidateId: string) => void;
}) {
  return (
    <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
      {plan.alternatives.map((alt, i) => {
        const recommended = alt.status === "recommended";
        const clickable = Boolean(onSelectAlternative && alt.id);
        const row = (
          <>
            <div style={{ position: "relative" }}>
              <LineBullet line={alt.line} size={22} />
              {!recommended && (
                <span
                  aria-hidden="true"
                  style={{
                    position: "absolute",
                    left: -2,
                    right: -2,
                    top: "50%",
                    height: 1,
                    background: "var(--sr-coral)",
                    opacity: 0.6,
                    transform: "rotate(-18deg)",
                  }}
                />
              )}
            </div>
            <div>
              <div
                style={{
                  display: "flex",
                  alignItems: "baseline",
                  gap: 8,
                  flexWrap: "wrap",
                }}
              >
                <span
                  style={{
                    fontFamily: "var(--sr-display)",
                    fontSize: 14,
                    color: "var(--sr-fg-2)",
                    textAlign: "left",
                    ...(recommended
                      ? {}
                      : {
                          textDecoration: "line-through",
                          textDecorationColor: "rgba(248,113,113,0.5)",
                          textDecorationThickness: "1px",
                        }),
                  }}
                >
                  {alt.dest}
                </span>
                <Meta tone={recommended ? "cyan" : "coral"} style={{ fontSize: 9.5 }}>
                  {alt.delta}
                </Meta>
              </div>
              <div
                style={{
                  marginTop: 5,
                  fontFamily: "var(--sr-display)",
                  fontStyle: "italic",
                  fontSize: 12.5,
                  color: "var(--sr-fg-3)",
                  lineHeight: 1.45,
                  borderLeft: "1px solid var(--sr-rule-bright)",
                  paddingLeft: 10,
                  textAlign: "left",
                }}
              >
                {alt.reason}
              </div>
              {clickable && (
                <Meta
                  tone="muted"
                  style={{ fontSize: 8.5, marginTop: 6, display: "block" }}
                >
                  Tap to switch
                </Meta>
              )}
            </div>
            <span
              style={{
                fontFamily: "var(--sr-mono)",
                fontSize: 9,
                letterSpacing: "0.16em",
                color: recommended ? "var(--sr-cyan)" : "var(--sr-coral)",
                textTransform: "uppercase",
                fontWeight: 600,
                whiteSpace: "nowrap",
              }}
            >
              {recommended ? "Recommended" : "Rejected"}
            </span>
          </>
        );

        // Grid layout lives on the inner button/div (the actual row content);
        // the <li> only carries the divider, stagger fade, and dim.
        const rowStyle = {
          padding: "12px 0",
          display: "grid",
          gridTemplateColumns: "auto 1fr auto",
          gap: 12,
          alignItems: "start" as const,
        };

        return (
          <li
            key={alt.id ?? `${alt.line}-${i}`}
            className="sr-slide-in"
            style={{
              animationDelay: `${i * 70}ms`,
              borderTop: i === 0 ? "none" : "1px solid var(--sr-rule)",
              opacity: recommended ? 1 : 0.85,
            }}
          >
            {clickable ? (
              <button
                type="button"
                onClick={() => onSelectAlternative?.(alt.id!)}
                style={{
                  ...rowStyle,
                  width: "100%",
                  background: "transparent",
                  border: 0,
                  cursor: "pointer",
                  font: "inherit",
                  color: "inherit",
                }}
              >
                {row}
              </button>
            ) : (
              <div style={rowStyle}>{row}</div>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function RouteNotes({ plan }: { plan: RoutePlan }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 0 }}>
      {plan.notes.map((n, i) => (
        <div
          key={`${n.t}-${i}`}
          className="sr-slide-in"
          style={{
            animationDelay: `${i * 60}ms`,
            display: "grid",
            gridTemplateColumns: "auto 1fr auto",
            gap: 12,
            padding: "10px 0",
            borderTop: i === 0 ? "none" : "1px solid var(--sr-rule)",
            alignItems: "center",
          }}
        >
          <Dot color={`var(--sr-${n.tone})`} size={5} pulse={n.tone === "cyan"} />
          <span
            style={{
              fontFamily: "var(--sr-mono)",
              fontSize: 10,
              letterSpacing: "0.16em",
              color: "var(--sr-fg-2)",
              textTransform: "uppercase",
            }}
          >
            {n.t}
          </span>
          <span
            style={{
              fontFamily: "var(--sr-display)",
              fontSize: 13,
              color: "var(--sr-fg-2)",
              textAlign: "right",
              letterSpacing: "-0.005em",
            }}
          >
            {n.v}
          </span>
        </div>
      ))}
    </div>
  );
}

/* ── ArrivalsSection ─────────────────────────────────────────── */

function ArrivalsSection({
  station,
  arrivals,
  way,
  onWayChange,
}: {
  station: Station;
  arrivals: Arrival[];
  way: Direction;
  onWayChange: (d: Direction) => void;
}) {
  const list = useMemo(
    () =>
      // Only the selected direction (plus "both", e.g. buses whose grid-tilted
      // compass reads as either way). Previously this only SORTED by direction,
      // so uptown arrivals leaked into the downtown tab and vice versa.
      arrivals
        .filter((arrival) => arrival.way === way || arrival.way === "both")
        .sort((a, b) => {
          const modeRank = (arrival: Arrival) => (arrival.mode === "bus" ? 1 : 0);
          const byMode = modeRank(a) - modeRank(b);
          if (byMode !== 0) return byMode;
          return a.mins - b.mins;
        }),
    [arrivals, way],
  );
  return (
    <section>
      <ArrivalsHead station={station} />
      <DirectionToggle way={way} onWayChange={onWayChange} />
      <ul
        key={way}
        className="sr-arrivals-list"
        style={{ listStyle: "none", marginTop: 8, padding: 0 }}
      >
        {list.map((a, i) => (
          <BlurFade
            key={`${a.mode ?? "subway"}-${a.line}-${a.way}-${a.dest}-${a.stationName ?? ""}`}
            delay={i * 45}
            duration={320}
          >
            <ArrivalRow arrival={a} station={station} />
          </BlurFade>
        ))}
      </ul>
    </section>
  );
}

function ArrivalsHead({ station }: { station: Station }) {
  return (
    <div style={{ padding: "22px 24px 12px" }}>
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
        }}
      >
        <Meta tone="ink" style={{ letterSpacing: "0.16em" }}>
          Next Arrivals
        </Meta>
        <Meta>
          {station.walk} · {station.dist}
        </Meta>
      </div>
      <div
        style={{
          marginTop: 6,
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
        }}
      >
        <div
          style={{
            fontFamily: "var(--sr-display)",
            fontSize: 22,
            fontWeight: 500,
            letterSpacing: "-0.015em",
            color: "var(--sr-fg)",
          }}
        >
          {station.name}
        </div>
        <Meta suppressHydrationWarning>
          <Dot
            color="var(--sr-cyan)"
            size={5}
            pulse
            style={{ marginRight: 6, verticalAlign: "middle" }}
          />
          Updated {station.updatedSec}s ago
        </Meta>
      </div>
    </div>
  );
}

function DirectionToggle({
  way,
  onWayChange,
}: {
  way: Direction;
  onWayChange: (d: Direction) => void;
}) {
  const renderOpt = (
    k: Direction,
    arrow: string,
    label: string,
    sub: string,
  ) => {
    const active = way === k;
    return (
      <button
        onClick={() => onWayChange(k)}
        aria-pressed={active}
        style={{
          flex: 1,
          padding: "12px 14px",
          cursor: "pointer",
          border: 0,
          // Active fill: warm gold to match the rail's amber accent (was a
          // leftover cyan gradient from the pre-glass palette).
          background: active
            ? "linear-gradient(135deg, rgba(244,200,108,0.97), rgba(216,155,43,0.92))"
            : "transparent",
          color: active ? "#241704" : "var(--sr-fg-3)",
          textAlign: "left",
          display: "flex",
          alignItems: "center",
          gap: 10,
          transition:
            "background var(--sr-dur-1), color var(--sr-dur-1), box-shadow var(--sr-dur-1)",
        }}
        className="sr-direction-option"
      >
        <span
          style={{
            fontFamily: "var(--sr-display)",
            fontSize: 18,
            fontWeight: 600,
            letterSpacing: "-0.01em",
            opacity: active ? 1 : 0.5,
          }}
        >
          {arrow}
        </span>
        <span
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "flex-start",
            gap: 2,
          }}
        >
          <span
            style={{
              fontFamily: "var(--sr-mono)",
              fontSize: 10.5,
              letterSpacing: "0.16em",
              textTransform: "uppercase",
              fontWeight: 500,
            }}
          >
            {label}
          </span>
          <span
            style={{
              fontFamily: "var(--sr-mono)",
              fontSize: 9,
              letterSpacing: "0.16em",
              textTransform: "uppercase",
              color: active ? "rgba(36,23,4,0.7)" : "var(--sr-muted)",
            }}
          >
            {sub}
          </span>
        </span>
      </button>
    );
  };

  return (
    <div style={{ padding: "0 24px" }}>
      <div
        className="sr-liquid-control sr-direction-frame"
        style={{
          display: "flex",
          border: "1px solid transparent",
          background: "transparent",
        }}
      >
        {renderOpt("uptown", "↑", "Uptown", "Manhattan · Bronx")}
        <span style={{ width: 1, background: "var(--sr-rule-bright)" }} />
        {renderOpt("downtown", "↓", "Downtown", "Brooklyn · Queens")}
      </div>
    </div>
  );
}

function ArrivalRow({
  arrival,
  station,
}: {
  arrival: Arrival;
  station: Station;
}) {
  const [open, setOpen] = useState(false);
  const isNow = arrival.label === "Now";
  const statusTone: "coral" | "amber" | "cyan" =
    arrival.status === "Delayed" ? "coral" : arrival.stale ? "amber" : "cyan";
  const statusColor = `var(--sr-${statusTone})`;
  const next5 = useMemo(() => arrival.nextArrivals ?? [], [arrival.nextArrivals]);
  const hasLiveSequence = next5.length > 0;
  const departingStation = arrival.stationName ?? station.name;

  return (
    <li className="sr-arrival-shell" style={{ borderTop: "1px solid var(--sr-rule)" }}>
      <MagicCard className="sr-arrival-card" intensity={0.11} size={260}>
        <div
          role="button"
          tabIndex={0}
          onClick={() => setOpen((v) => !v)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              setOpen((v) => !v);
            }
          }}
          style={{
            padding: "14px 24px",
            display: "grid",
            gridTemplateColumns: "minmax(32px, auto) 1fr auto 16px",
            columnGap: 14,
            alignItems: "center",
            cursor: "pointer",
            background: open ? "rgba(216,155,43,0.075)" : "transparent",
            transition: "background var(--sr-dur-2)",
          }}
        >
          {arrival.mode === "bus" ? (
            <BusChip route={arrival.line} />
          ) : (
            <LineBullet line={arrival.line} size={28} />
          )}
          <div style={{ minWidth: 0 }}>
            <div
              style={{
                fontFamily: "var(--sr-display)",
                fontSize: 15,
                fontWeight: 500,
                letterSpacing: "-0.005em",
                lineHeight: 1.2,
                color: "var(--sr-fg)",
              }}
            >
              {arrival.mode === "bus"
                ? `To ${arrival.dest}`
                : `${arrival.way === "uptown" ? "Uptown" : "Downtown"} · ${arrival.dest}`}
            </div>
            <Meta style={{ marginTop: 4, display: "block" }}>
              Departing {departingStation}
            </Meta>
          </div>
          <div style={{ textAlign: "right" }}>
            <div
              style={{
                fontFamily: "var(--sr-display)",
                fontSize: isNow ? 14 : 18,
                fontWeight: 600,
                letterSpacing: isNow ? "0.04em" : "-0.02em",
                textTransform: isNow ? "uppercase" : "none",
                color: isNow ? "var(--sr-cyan)" : "var(--sr-fg)",
                lineHeight: 1,
              }}
            >
              {isNow ? (
                "Now"
              ) : (
                <>
                  <NumberTicker value={arrival.mins} />
                  <span
                    style={{
                      fontSize: 12,
                      color: "var(--sr-muted)",
                      marginLeft: 2,
                      fontWeight: 500,
                    }}
                  >
                    m
                  </span>
                </>
              )}
            </div>
            <Meta
              tone={statusTone}
              style={{
                marginTop: 5,
                display: "inline-flex",
                alignItems: "center",
                gap: 5,
              }}
            >
              <Dot color={statusColor} size={5} />
              {arrival.stale ? "Stale" : arrival.status}
            </Meta>
          </div>
          <span
            style={{
              color: "var(--sr-muted)",
              fontFamily: "var(--sr-mono)",
              fontSize: 12,
              transform: open ? "rotate(180deg)" : "none",
              transition: "transform var(--sr-dur-2)",
              justifySelf: "end",
            }}
          >
            ▾
          </span>
        </div>

        <Accordion open={open}>
          <div
            style={{
              padding: "6px 24px 16px",
              background: "rgba(216,155,43,0.035)",
            }}
          >
            <div
              style={{
                padding: "10px 0 8px",
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                borderTop: "1px dashed var(--sr-rule-bright)",
              }}
            >
              <Meta tone="cyan" style={{ letterSpacing: "0.16em" }}>
                Next 5 on the {arrival.line}
              </Meta>
              <Meta>From {departingStation}</Meta>
            </div>
            {hasLiveSequence ? (
            <ul
              style={{
                listStyle: "none",
                display: "flex",
                flexDirection: "column",
                gap: 0,
                margin: 0,
                padding: 0,
              }}
            >
              {next5.map((t, i) => (
                <li
                  key={i}
                  className={open ? "sr-slide-in" : undefined}
                  style={{
                    animationDelay: open ? `${i * 55}ms` : "0ms",
                    // No third "Track" column — design intent is to keep
                    // this accordion read-only and time-focused. Times stay
                    // the headline; metadata (track/cars/crowd) is the
                    // secondary read. No action affordances inside the row.
                    display: "grid",
                    gridTemplateColumns: "24px 1fr auto",
                    columnGap: 12,
                    alignItems: "center",
                    padding: "9px 0",
                    borderTop: i === 0 ? "none" : "1px solid var(--sr-rule)",
                  }}
                >
                  <span
                    style={{
                      fontFamily: "var(--sr-mono)",
                      fontSize: 10,
                      color: i === 0 ? "var(--sr-cyan)" : "var(--sr-muted)",
                      letterSpacing: "0.16em",
                    }}
                  >
                    {String(i + 1).padStart(2, "0")}
                  </span>
                  <div
                    style={{
                      display: "flex",
                      flexDirection: "column",
                      gap: 2,
                      minWidth: 0,
                    }}
                  >
                    <div
                      style={{
                        fontFamily: "var(--sr-display)",
                        fontSize: 13.5,
                        color: t.stale ? "var(--sr-amber)" : "var(--sr-fg)",
                        fontWeight: i === 0 ? 600 : 500,
                        letterSpacing: "-0.005em",
                      }}
                    >
                      {t.label}
                      {t.stale && (
                        <span
                          style={{
                            marginLeft: 8,
                            fontFamily: "var(--sr-mono)",
                            fontSize: 9.5,
                            letterSpacing: "0.16em",
                            color: "var(--sr-amber)",
                          }}
                        >
                          · STALE
                        </span>
                      )}
                    </div>
                    <div
                      style={{
                        display: "flex",
                        gap: 8,
                        alignItems: "center",
                      }}
                    >
                      <Meta>{t.track ?? "Live"}</Meta>
                      <Meta>{typeof t.cars === "number" ? `· ${t.cars} cars` : "· GTFS-RT"}</Meta>
                      <Meta
                        tone={
                          t.crowd === "heavy"
                            ? "coral"
                            : t.crowd === "moderate"
                            ? "amber"
                            : "cyan"
                        }
                      >
                        ·{" "}
                        <Dot
                          color={
                            t.crowd === "heavy"
                              ? "var(--sr-coral)"
                              : t.crowd === "moderate"
                              ? "var(--sr-amber)"
                              : "var(--sr-cyan)"
                          }
                          size={4}
                          style={{ marginRight: 5, verticalAlign: "middle" }}
                        />
                        {t.crowd ?? "live"}
                      </Meta>
                    </div>
                  </div>
                  {/* Right column shows the bare arrival time again as a
                      mono label so the eye lands on it cleanly. No action,
                      no button — read-only by design. */}
                  <span
                    style={{
                      fontFamily: "var(--sr-mono)",
                      fontSize: 11,
                      letterSpacing: "0.04em",
                      color: i === 0 ? "var(--sr-cyan)" : "var(--sr-fg-2)",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {t.label === "Now" ? "NOW" : `${t.mins}m`}
                  </span>
                </li>
              ))}
            </ul>
            ) : (
              <div
                style={{
                  padding: "12px 0 10px",
                  display: "flex",
                  gap: 10,
                  alignItems: "flex-start",
                  borderTop: "1px solid var(--sr-rule)",
                }}
              >
                <Dot color="var(--sr-amber)" size={6} style={{ marginTop: 5 }} />
                <p
                  style={{
                    margin: 0,
                    fontFamily: "var(--sr-display)",
                    fontSize: 13,
                    lineHeight: 1.45,
                    color: "var(--sr-fg-3)",
                  }}
                >
                  Live sequence unavailable for this trip. Showing the latest single arrival only.
                </p>
              </div>
            )}
            <div
              style={{
                marginTop: 10,
                paddingTop: 10,
                borderTop: "1px dashed var(--sr-rule-bright)",
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
              }}
            >
              <Meta>
                {next5.length > 1
                  ? `Avg headway ${Math.max(1, next5[1].mins - next5[0].mins)}m`
                  : hasLiveSequence ? "Only one live arrival" : "Single live arrival"}
              </Meta>
              <Meta tone={arrival.stale ? "amber" : "cyan"}>
                <Dot
                  color={arrival.stale ? "var(--sr-amber)" : "var(--sr-cyan)"}
                  size={4}
                  pulse={!arrival.stale}
                  style={{ marginRight: 5, verticalAlign: "middle" }}
                />
                GTFS-RT {arrival.stale ? "5m 12s ago" : "12s ago"}
              </Meta>
            </div>
          </div>
        </Accordion>
      </MagicCard>
    </li>
  );
}

/* ── IncidentsSection (empty-state shown by default) ───────────── */

// incidentTone + incidentTimeLabel are imported from ./incident-format.

function IncidentsSection({
  station,
  incidents,
  atlasScanOn,
  onSelectIncident,
}: {
  station: Station;
  incidents: LiveFeedIncident[];
  atlasScanOn: boolean;
  onSelectIncident?: (incident: LiveFeedIncident) => void;
}) {
  const visibleIncidents = atlasScanOn ? incidents : [];
  // Production: feed real incidents from the agent pipeline. Empty state
  // matches the screenshot exactly — cyan dot + paragraph + footer caption.
  return (
    <section
      style={{
        paddingBottom: 90,
        borderTop: "1px solid var(--sr-rule)",
        marginTop: 14,
      }}
    >
      <div
        style={{
          padding: "22px 24px 14px",
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
        }}
      >
        <Meta tone="ink" style={{ letterSpacing: "0.16em" }}>
          Live Incidents
        </Meta>
        <Meta tone="cyan">
          <Dot
            color="var(--sr-cyan)"
            size={5}
            pulse
            style={{ marginRight: 6, verticalAlign: "middle" }}
          />
          {visibleIncidents.length} Active
        </Meta>
      </div>
      <div
        style={{
          padding: "4px 24px 20px",
          borderTop: "1px solid var(--sr-rule)",
        }}
      >
        {visibleIncidents.length > 0 && (
          <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
            {visibleIncidents.slice(0, 6).map((incident) => {
              const tone = incidentTone(incident);
              const color = `var(--sr-${tone})`;
              return (
                <li key={incident.id} style={{ borderTop: "1px solid var(--sr-rule)" }}>
                  <button
                    type="button"
                    onClick={() => onSelectIncident?.(incident)}
                    style={{
                      width: "100%",
                      padding: "14px 0",
                      display: "grid",
                      gridTemplateColumns: "12px minmax(0, 1fr) auto",
                      gap: 12,
                      alignItems: "start",
                      textAlign: "left",
                      background: "transparent",
                      border: 0,
                      color: "inherit",
                      cursor: "pointer",
                    }}
                  >
                    <Dot
                      color={color}
                      size={8}
                      pulse={tone === "coral"}
                      style={{ marginTop: 5 }}
                    />
                    <span style={{ minWidth: 0 }}>
                      <span
                        style={{
                          display: "block",
                          fontFamily: "var(--sr-display)",
                          fontSize: 13.5,
                          fontWeight: 600,
                          lineHeight: 1.25,
                          color: "var(--sr-fg)",
                        }}
                      >
                        {incident.title}
                      </span>
                      <span
                        style={{
                          display: "block",
                          marginTop: 4,
                          fontFamily: "var(--sr-display)",
                          fontSize: 12.2,
                          lineHeight: 1.45,
                          color: "var(--sr-fg-3)",
                        }}
                      >
                        {incident.detail ?? "ATLAS is monitoring this incident near your route."}
                      </span>
                      <Meta style={{ display: "block", marginTop: 7 }}>
                        {incident.routeIds?.length ? `${incident.routeIds.join("/")} · ` : ""}
                        ATLAS signal
                      </Meta>
                    </span>
                    <Meta tone={tone}>{incidentTimeLabel(incident.updated_at)}</Meta>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
        <div
          style={{
            marginTop: 14,
            display: visibleIncidents.length > 0 ? "none" : "flex",
            gap: 12,
            alignItems: "flex-start",
          }}
        >
          <span
            style={{
              width: 8,
              height: 8,
              borderRadius: "50%",
              background: "var(--sr-cyan)",
              marginTop: 6,
              flexShrink: 0,
            }}
          />
          <p
            style={{
              fontFamily: "var(--sr-display)",
              fontWeight: 400,
              fontSize: 13,
              lineHeight: 1.5,
              color: "var(--sr-fg-3)",
            }}
          >
            {atlasScanOn
              ? `All clear near ${station.name}. No incidents flagged by MTA or ATLAS Intel in the last 30 min.`
              : "ATLAS scan is paused. Turn it on from Hub to show nearby incident markers and details."}
          </p>
        </div>
        <div
          style={{
            marginTop: 14,
            display: "flex",
            justifyContent: "space-between",
          }}
        >
          <Meta>Scanning · @NYCrimeNow · @CitizenAppNYC</Meta>
          <Meta>{atlasScanOn ? "Next sweep 4m" : "Off by default"}</Meta>
        </div>
      </div>
    </section>
  );
}

/* ──────────────────────────────────────────────────────────────
   HUB VIEW
   ────────────────────────────────────────────────────────────── */
