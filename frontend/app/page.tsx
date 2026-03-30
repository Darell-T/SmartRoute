"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import { JarvisMap, TransitRouteData, RouteStep } from "@/components/jarvis-map";
import { ArrowRight, AudioLines, ChevronUp, ChevronDown, X, Loader2, Crosshair, RotateCcw } from "lucide-react";
import { planTrip, getThinking, DEFAULT_LOCATION } from "@/lib/api";

const MTA_COLORS: Record<string, string> = {
  A: "#0039A6", C: "#0039A6", E: "#0039A6",
  B: "#FF6319", D: "#FF6319", F: "#FF6319", M: "#FF6319",
  G: "#6CBE45",
  J: "#996633", Z: "#996633",
  L: "#A7A9AC",
  N: "#FCCC0A", Q: "#FCCC0A", R: "#FCCC0A", W: "#FCCC0A",
  "1": "#EE352E", "2": "#EE352E", "3": "#EE352E",
  "4": "#00933C", "5": "#00933C", "6": "#00933C",
  "7": "#B933AD",
  S: "#808183",
  SI: "#00A9CE",
};

/* ── Audio waveform bar component ── */
function WaveformBars({ active }: { active: boolean }) {
  const barCount = 24;
  return (
    <div
      className="hud-waveform"
      style={{
        display: "flex",
        alignItems: "center",
        gap: "2px",
        height: "16px",
        marginBottom: "10px",
        overflow: "hidden",
        opacity: active ? 1 : 0,
        transition: "opacity 0.4s ease",
      }}
    >
      {Array.from({ length: barCount }, (_, i) => (
        <div
          key={i}
          style={{
            width: "2px",
            borderRadius: "1px",
            background: "rgba(0, 212, 255, 0.35)",
            height: active ? undefined : "2px",
            animation: active
              ? `waveBar 0.8s ease-in-out ${i * 0.04}s infinite alternate`
              : "none",
          }}
        />
      ))}
    </div>
  );
}

export default function JarvisPage() {
  const [inputValue, setInputValue] = useState("");
  const [userLocation, setUserLocation] = useState<{
    lng: number;
    lat: number;
  } | null>(null);
  const [jarvisText, setJarvisText] = useState("");
  const [displayedText, setDisplayedText] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [thinkingText, setThinkingText] = useState("");
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [errorText, setErrorText] = useState<string | null>(null);
  const [isListening, setIsListening] = useState(false);
  const [mobileBubbleExpanded, setMobileBubbleExpanded] = useState(false);

  // Structured route data from API
  const [trainLine, setTrainLine] = useState<string | null>(null);
  const [departureTimestamp, setDepartureTimestamp] = useState<number | null>(null);
  const [departureMinutes, setDepartureMinutes] = useState<number | null>(null);
  const [direction, setDirection] = useState<string | null>(null);
  const [rideDurationMinutes, setRideDurationMinutes] = useState<number | null>(null);
  const [routeData, setRouteData] = useState<TransitRouteData | null>(null);
  const [destCoords, setDestCoords] = useState<{ lat: number; lng: number } | null>(null);

  const wordRevealIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const thinkingRevealRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const revealStartedRef = useRef(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const speakingTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const departureIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const mapActionsRef = useRef<{ recenter: () => void } | null>(null);
  // Live departure countdown from raw timestamp
  useEffect(() => {
    if (departureIntervalRef.current) {
      clearInterval(departureIntervalRef.current);
      departureIntervalRef.current = null;
    }

    if (departureTimestamp == null) {
      setDepartureMinutes(null);
      return;
    }

    function tick() {
      setDepartureMinutes(Math.max(0, Math.round((departureTimestamp! - Date.now() / 1000) / 60)));
    }

    tick();
    departureIntervalRef.current = setInterval(tick, 15000);

    return () => {
      if (departureIntervalRef.current) {
        clearInterval(departureIntervalRef.current);
        departureIntervalRef.current = null;
      }
    };
  }, [departureTimestamp]);

  // GPS fallback — use demo location if GPS unavailable or times out
  useEffect(() => {
    if (!navigator.geolocation) {
      setUserLocation(DEFAULT_LOCATION);
      return;
    }
    const timeout = setTimeout(() => {
      setUserLocation((prev) => prev ?? DEFAULT_LOCATION);
    }, 8000);
    return () => clearTimeout(timeout);
  }, []);


  const handleLocationUpdate = useCallback(
    (coords: { lng: number; lat: number }) => {
      setUserLocation(coords);
    },
    [],
  );

  const handleMapReady = useCallback(
    (actions: { recenter: () => void }) => {
      mapActionsRef.current = actions;
    },
    [],
  );

  function handleRecenter() {
    mapActionsRef.current?.recenter();
  }

  function handleVoiceInput() {
    const SpeechRecognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SpeechRecognition) return;
    const recognition = new SpeechRecognition();
    recognition.lang = "en-US";
    recognition.onstart = () => setIsListening(true);
    recognition.onresult = (e: any) => setInputValue(e.results[0][0].transcript);
    recognition.onend = () => setIsListening(false);
    recognition.onerror = () => setIsListening(false);
    recognition.start();
  }

  function handleClearRoute() {
    // Stop any playing audio
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current = null;
    }
    if (speakingTimeoutRef.current) {
      clearTimeout(speakingTimeoutRef.current);
      speakingTimeoutRef.current = null;
    }
    if (wordRevealIntervalRef.current) clearInterval(wordRevealIntervalRef.current);
    if (thinkingRevealRef.current) clearInterval(thinkingRevealRef.current);
    revealStartedRef.current = false;
    setJarvisText("");
    setDisplayedText("");
    setThinkingText("");
    setIsSpeaking(false);
    setTrainLine(null);
    setDepartureTimestamp(null);
    setDirection(null);
    setRideDurationMinutes(null);
    setRouteData(null);
    setDestCoords(null);
    setErrorText(null);
  }

  async function handleSubmit() {
    if (!inputValue.trim()) return;
    if (!userLocation) {
      setErrorText("Waiting for GPS location...");
      setTimeout(() => setErrorText(null), 3000);
      return;
    }

    // Stop any playing audio
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current = null;
    }
    if (speakingTimeoutRef.current) {
      clearTimeout(speakingTimeoutRef.current);
      speakingTimeoutRef.current = null;
    }

    // Unlock audio on user gesture (mobile browsers require this)
    const unlockedAudio = new Audio();
    unlockedAudio.src = "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YQAAAAA=";
    unlockedAudio.play().catch(() => {});
    audioRef.current = unlockedAudio;

    // Reset for new request
    if (wordRevealIntervalRef.current) clearInterval(wordRevealIntervalRef.current);
    if (thinkingRevealRef.current) clearInterval(thinkingRevealRef.current);
    revealStartedRef.current = false;
    setDisplayedText("");
    setThinkingText("");
    setIsSpeaking(false);
    setTrainLine(null);
    setDepartureTimestamp(null);
    setDirection(null);
    setRideDurationMinutes(null);
    setRouteData(null);
    setDestCoords(null);
    setErrorText(null);
    setMobileBubbleExpanded(false);
    setIsLoading(true);

    try {
      // Fire thinking audio immediately with word-by-word reveal
      getThinking()
        .then(({ text, audio }) => {
          const bytes = Uint8Array.from(atob(audio), (c) => c.charCodeAt(0));
          const thinkAudio = new Audio(
            URL.createObjectURL(new Blob([bytes], { type: "audio/mpeg" })),
          );

          // Word-by-word reveal synced to audio
          const words = text.split(/\s+/).filter((w) => w.length > 0);
          let wordIdx = 0;

          function startThinkingReveal(duration: number) {
            const intervalMs = Math.max((duration * 1000) / words.length, 80);
            if (thinkingRevealRef.current) clearInterval(thinkingRevealRef.current);
            thinkingRevealRef.current = setInterval(() => {
              wordIdx++;
              setThinkingText(words.slice(0, wordIdx).join(" "));
              if (wordIdx >= words.length) {
                clearInterval(thinkingRevealRef.current!);
              }
            }, intervalMs);
          }

          const fallback = words.length * 0.35;
          thinkAudio.addEventListener("loadedmetadata", () => {
            const dur = isFinite(thinkAudio.duration) && thinkAudio.duration > 0
              ? thinkAudio.duration : fallback;
            startThinkingReveal(dur);
          });
          setTimeout(() => {
            if (!thinkingRevealRef.current) startThinkingReveal(fallback);
          }, 200);

          thinkAudio.play().catch(() => {});
        })
        .catch(() => {});

      const trip_data = await planTrip(userLocation.lat, userLocation.lng, inputValue);

      const text = trip_data.recommendation;
      setJarvisText(text);

      // Extract HUD pill data from the chosen route
      const chosenRoute = trip_data.route || [];
      const firstTransit = chosenRoute.find(
        (s: RouteStep) => s.type === "SUBWAY" || s.type === "BUS"
      );

      setTrainLine(firstTransit?.train_line || null);
      setDepartureTimestamp(
        firstTransit?.minutes_until_train_arrives != null
          ? Date.now() / 1000 + firstTransit.minutes_until_train_arrives * 60
          : null
      );
      setDirection(firstTransit?.direction || null);
      setRideDurationMinutes(
        firstTransit?.minutes_until_arrival != null
          ? Math.round(firstTransit.minutes_until_arrival)
          : null
      );

      // Destination coords from last step's end point
      const lastStep = chosenRoute[chosenRoute.length - 1];
      const rawDest = lastStep?.type === "WALK"
        ? lastStep.end_point
        : lastStep?.arrival_coords;
      const destCoordsComputed = rawDest
        ? { lat: rawDest.latitude, lng: rawDest.longitude }
        : null;
      setDestCoords(destCoordsComputed);

      // Pass chosen route to map
      setRouteData({ steps: chosenRoute });

      // Reuse the unlocked audio element for trip audio
      const bytes = Uint8Array.from(atob(trip_data.audio), (c) =>
        c.charCodeAt(0),
      );
      const tripAudioUrl = URL.createObjectURL(new Blob([bytes], { type: "audio/mpeg" }));
      const tripAudio = unlockedAudio;
      tripAudio.src = tripAudioUrl;
      audioRef.current = tripAudio;

      // Word-by-word reveal synced to audio duration
      function startWordReveal(audioDuration: number) {
        if (revealStartedRef.current) return;
        revealStartedRef.current = true;
        const words = text.split(/\s+/).filter((w) => w.length > 0);
        if (words.length === 0) return;
        const intervalMs = Math.max(
          (audioDuration * 1000) / words.length,
          80,
        );
        let wordIndex = 0;
        if (wordRevealIntervalRef.current)
          clearInterval(wordRevealIntervalRef.current);
        wordRevealIntervalRef.current = setInterval(() => {
          wordIndex++;
          setDisplayedText(words.slice(0, wordIndex).join(" "));
          if (wordIndex >= words.length) {
            clearInterval(wordRevealIntervalRef.current!);
          }
        }, intervalMs);
      }

      const fallbackDuration = text.split(" ").length * 0.45;

      tripAudio.addEventListener("loadedmetadata", () => {
        const dur =
          isFinite(tripAudio.duration) && tripAudio.duration > 0
            ? tripAudio.duration
            : fallbackDuration;
        startWordReveal(dur);
      });

      setTimeout(() => startWordReveal(fallbackDuration), 300);

      setIsSpeaking(true);

      // Safety timeout — stop spinning after 60s no matter what
      speakingTimeoutRef.current = setTimeout(() => {
        setIsSpeaking(false);
        speakingTimeoutRef.current = null;
      }, 60_000);

      tripAudio.onended = () => {
        setIsSpeaking(false);
        audioRef.current = null;
        if (speakingTimeoutRef.current) {
          clearTimeout(speakingTimeoutRef.current);
          speakingTimeoutRef.current = null;
        }
      };

      tripAudio.onerror = () => {
        setIsSpeaking(false);
        audioRef.current = null;
        if (speakingTimeoutRef.current) {
          clearTimeout(speakingTimeoutRef.current);
          speakingTimeoutRef.current = null;
        }
      };

      tripAudio.play().catch(() => {
        setIsSpeaking(false);
        if (speakingTimeoutRef.current) {
          clearTimeout(speakingTimeoutRef.current);
          speakingTimeoutRef.current = null;
        }
      });

      setInputValue("");
    } catch (error) {
      const msg = error instanceof Error ? error.message : "Unknown error";
      if (msg.includes("Failed to plan trip")) {
        setErrorText("No route found, sir. Try a more specific address.");
      } else {
        setErrorText("Connection error. Check your network and try again.");
      }
      setJarvisText("");
      setDisplayedText("");
    } finally {
      setIsLoading(false);
    }
  }

  // Bubble visibility — also show bubble for errors so retry button is accessible
  const showBubble = isLoading || !!jarvisText || !!errorText;
  const bubbleText = isLoading
    ? (thinkingText || "Processing, sir...")
    : displayedText || jarvisText;

  // First sentence for collapsed mobile bubble
  const firstSentence = (() => {
    if (!bubbleText) return "";
    const end = bubbleText.indexOf(". ");
    return end > 0 ? bubbleText.slice(0, end + 1) : bubbleText;
  })();

  // Pill visibility
  const hasRouteData = !!trainLine;
  const trainLineColor = trainLine ? (MTA_COLORS[trainLine] || "#FFD700") : "#FFD700";
  const showActions = !!routeData && !isLoading;

  const transitPillText = trainLine
    ? departureMinutes != null
      ? `${trainLine} \u2014 in ${departureMinutes} min${direction ? ` \u00B7 ${direction}` : ""}`
      : `${trainLine} \u2014 checking...`
    : "";

  const etaPillText = rideDurationMinutes != null
    ? `~${rideDurationMinutes} min ride`
    : "ETA pending";

  /* ── Shared HUD pill style ── */
  const hudPill: React.CSSProperties = {
    fontFamily: "var(--font-geist-mono), monospace",
    backdropFilter: "blur(16px)",
    WebkitBackdropFilter: "blur(16px)",
    background: "rgba(8, 10, 18, 0.5)",
    border: "1px solid rgba(0, 212, 255, 0.08)",
    padding: "6px 14px",
    borderRadius: "20px",
    fontSize: "12px",
    color: "rgba(255, 255, 255, 0.88)",
    whiteSpace: "nowrap" as const,
  };

  return (
    <div className="relative h-screen w-full overflow-hidden bg-[#0a0a0f]">
      {/* Full-screen Mapbox 3D Map */}
      <JarvisMap
        onLocationUpdate={handleLocationUpdate}
        routeData={routeData}
        isSpeaking={isSpeaking}
        destCoords={destCoords}
        onMapReady={handleMapReady}
      />

      {/* ── Viewport Effects ── */}
      <div
        className="fixed inset-0 pointer-events-none z-[1] hidden md:block"
        style={{
          background: "radial-gradient(ellipse at center, transparent 50%, rgba(0,0,0,0.3) 80%, rgba(0,0,0,0.55) 100%)",
        }}
      />
      <div
        className="fixed inset-0 pointer-events-none z-[1] md:hidden"
        style={{
          background: "radial-gradient(ellipse at center, transparent 60%, rgba(0,0,0,0.15) 85%, rgba(0,0,0,0.3) 100%)",
        }}
      />
      <div
        className="fixed inset-0 pointer-events-none z-[1] hidden md:block"
        style={{
          background: "repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(0,0,0,0.03) 2px, rgba(0,0,0,0.03) 4px)",
        }}
      />
      {/* HUD Corner Brackets */}
      <div className="fixed pointer-events-none z-[2]" style={{ top: 16, left: 16, width: 30, height: 30, borderTop: "1px solid rgba(0,212,255,0.15)", borderLeft: "1px solid rgba(0,212,255,0.15)" }} />
      <div className="fixed pointer-events-none z-[2]" style={{ top: 16, right: 16, width: 30, height: 30, borderTop: "1px solid rgba(0,212,255,0.15)", borderRight: "1px solid rgba(0,212,255,0.15)" }} />
      <div className="fixed pointer-events-none z-[2]" style={{ bottom: 16, left: 16, width: 30, height: 30, borderBottom: "1px solid rgba(0,212,255,0.15)", borderLeft: "1px solid rgba(0,212,255,0.15)" }} />
      <div className="fixed pointer-events-none z-[2]" style={{ bottom: 16, right: 16, width: 30, height: 30, borderBottom: "1px solid rgba(0,212,255,0.15)", borderRight: "1px solid rgba(0,212,255,0.15)" }} />

      {/* JARVIS Logo — Top Left (hidden on mobile) */}
      <div className="hidden md:block absolute top-6 left-6 z-10">
        <h1
          style={{
            fontFamily: "var(--font-geist-mono), monospace",
            fontSize: "11px",
            letterSpacing: "0.15em",
            color: "rgba(0, 212, 255, 0.3)",
          }}
        >
          JARVIS
        </h1>
      </div>

      {/* HUD Overlay Pills — Top Center */}
      {hasRouteData && (
        <div className="hidden md:flex absolute top-6 left-1/2 -translate-x-1/2 z-10 items-center gap-2">
          <div
            style={{
              ...hudPill,
              display: "flex",
              alignItems: "center",
              gap: "8px",
              animation: "hudPillIn 300ms cubic-bezier(0.16, 1, 0.3, 1) forwards",
            }}
          >
            <span
              style={{
                width: 8,
                height: 8,
                borderRadius: "50%",
                background: trainLineColor,
                display: "inline-block",
                flexShrink: 0,
              }}
            />
            <span>{transitPillText}</span>
          </div>
          <div
            style={{
              ...hudPill,
              animation: "hudPillIn 300ms cubic-bezier(0.16, 1, 0.3, 1) 100ms forwards",
              opacity: 0,
            }}
          >
            {etaPillText}
          </div>
        </div>
      )}

      {/* JARVIS Response Bubble — Desktop */}
      {showBubble && (
        <div className="hidden md:block absolute bottom-24 left-1/2 -translate-x-1/2 z-10 w-full max-w-[600px] px-4">
          <div
            className="hud-bubble"
            style={{
              backdropFilter: "blur(20px) saturate(1.4)",
              WebkitBackdropFilter: "blur(20px) saturate(1.4)",
              background: "rgba(8, 10, 18, 0.7)",
              border: "1px solid rgba(0, 255, 255, 0.12)",
              boxShadow:
                "0 0 30px rgba(0, 255, 255, 0.06), inset 0 1px 0 rgba(255, 255, 255, 0.04)",
              borderRadius: "16px",
              padding: "20px 24px",
              color: "rgba(255, 255, 255, 0.92)",
              fontSize: "15px",
              lineHeight: 1.6,
              animation: isLoading
                ? "hudSlideUp 400ms cubic-bezier(0.16, 1, 0.3, 1) forwards, hudBorderPulse 2s ease-in-out infinite"
                : "hudSlideUp 400ms cubic-bezier(0.16, 1, 0.3, 1) forwards",
              maxHeight: "200px",
              overflowY: "auto" as const,
              scrollbarWidth: "thin" as const,
              scrollbarColor: "rgba(0, 255, 255, 0.15) transparent",
            }}
          >
            {/* Audio waveform visualizer */}
            <WaveformBars active={isSpeaking} />
            <div style={{
              fontFamily: "var(--font-geist-mono), monospace",
              fontSize: "10px",
              letterSpacing: "0.15em",
              color: "rgba(0, 212, 255, 0.4)",
              marginBottom: "8px",
              display: "flex",
              alignItems: "center",
              gap: "6px",
            }}>
              JARVIS
              {isLoading && (
                <Loader2
                  size={10}
                  style={{ color: "rgba(0, 212, 255, 0.4)", animation: "spin 1s linear infinite" }}
                />
              )}
            </div>
            {errorText ? (
              <>
                <div style={{ color: "rgba(255, 200, 180, 0.9)" }}>{errorText}</div>
                <button
                  onClick={handleSubmit}
                  className="hud-action-btn-pill"
                  style={{ marginTop: "10px" }}
                >
                  <RotateCcw size={13} />
                  <span>Retry</span>
                </button>
              </>
            ) : (
              bubbleText
            )}
          </div>
        </div>
      )}

      {/* Floating Action Buttons — Bottom Left, stacked vertically */}
      {showActions && (
        <div
          className="hidden md:flex absolute z-10 flex-col gap-2"
          style={{
            bottom: "40px",
            left: "16px",
            animation: "hudFadeIn 300ms ease forwards",
          }}
        >
          <button
            onClick={handleRecenter}
            className="hud-action-btn-pill"
            title="Re-center"
          >
            <Crosshair size={13} />
            <span>Re-center</span>
          </button>
          <button
            onClick={handleClearRoute}
            className="hud-action-btn-pill"
            title="Clear route"
          >
            <X size={13} />
            <span>Clear</span>
          </button>
        </div>
      )}

      {/* Desktop Input Bar */}
      <div className="hidden md:block absolute bottom-8 left-1/2 -translate-x-1/2 z-10 w-full max-w-xl px-4">
        <div
          className="flex items-center gap-3 rounded-full px-5 py-3 hud-input-bar"
          style={{
            background: "rgba(8, 10, 18, 0.65)",
            backdropFilter: "blur(16px)",
            WebkitBackdropFilter: "blur(16px)",
            border: "1px solid rgba(0, 212, 255, 0.1)",
            transition: "border-color 0.3s ease, box-shadow 0.3s ease",
          }}
        >
          <AudioLines
            className="shrink-0 cursor-pointer transition-colors duration-200"
            size={20}
            style={{ color: isListening ? "rgba(0, 212, 255, 1)" : "rgba(0, 212, 255, 0.6)",
              animation: isListening ? "hudBorderPulse 1.5s ease-in-out infinite" : undefined,
              filter: isListening ? "drop-shadow(0 0 6px rgba(0, 212, 255, 0.6))" : undefined,
            }}
            onClick={handleVoiceInput}
          />
          <input
            type="text"
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSubmit()}
            placeholder="Where are you headed, sir?"
            className="flex-1 bg-transparent text-white outline-none text-sm"
            style={{
              fontFamily: "var(--font-geist-mono), monospace",
              color: "rgba(255, 255, 255, 0.9)",
            }}
            disabled={isLoading}
          />
          <button
            onClick={handleSubmit}
            disabled={isLoading}
            className="w-9 h-9 rounded-full flex items-center justify-center transition-all duration-200 disabled:opacity-40 active:scale-95"
            style={{ background: "rgba(0, 212, 255, 0.15)" }}
          >
            {isLoading ? (
              <Loader2 size={18} style={{ color: "rgba(0, 212, 255, 0.8)", animation: "spin 1s linear infinite" }} />
            ) : (
              <ArrowRight size={18} style={{ color: "rgba(0, 212, 255, 0.8)" }} />
            )}
          </button>
        </div>
      </div>

      {/* ─── Mobile Bottom ─── */}
      <div
        className="md:hidden fixed inset-x-0 bottom-0 z-20 flex flex-col"
        style={{ paddingBottom: "env(safe-area-inset-bottom)" }}
      >
        {/* Collapsible JARVIS Bubble */}
        {showBubble && (
          <div
            style={{
              backdropFilter: "blur(20px) saturate(1.4)",
              WebkitBackdropFilter: "blur(20px) saturate(1.4)",
              background: "rgba(8, 10, 18, 0.85)",
              borderTop: "1px solid rgba(0, 255, 255, 0.12)",
              boxShadow: "0 -10px 40px rgba(0, 0, 0, 0.3)",
              maxHeight: mobileBubbleExpanded ? "50vh" : "120px",
              overflow: "hidden",
              transition: "max-height 0.3s ease-out",
              animation: isLoading ? "hudBorderPulse 2s ease-in-out infinite" : undefined,
            }}
          >
            <div style={{
              padding: "12px 16px",
              overflowY: mobileBubbleExpanded ? "auto" as const : "hidden" as const,
              maxHeight: mobileBubbleExpanded ? "calc(50vh - 1px)" : "119px",
            }}>
              {/* JARVIS label + chevron toggle */}
              <div style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                marginBottom: "6px",
              }}>
                <div style={{
                  fontFamily: "var(--font-geist-mono), monospace",
                  fontSize: "10px",
                  letterSpacing: "0.15em",
                  color: "rgba(0, 212, 255, 0.4)",
                  display: "flex",
                  alignItems: "center",
                  gap: "6px",
                }}>
                  JARVIS
                  {isLoading && (
                    <Loader2
                      size={10}
                      style={{ color: "rgba(0, 212, 255, 0.4)", animation: "spin 1s linear infinite" }}
                    />
                  )}
                </div>
                {!isLoading && (
                  <button
                    onClick={() => setMobileBubbleExpanded(!mobileBubbleExpanded)}
                    style={{
                      background: "none",
                      border: "none",
                      padding: "4px",
                      cursor: "pointer",
                      display: "flex",
                      alignItems: "center",
                    }}
                  >
                    {mobileBubbleExpanded ? (
                      <ChevronDown size={16} style={{ color: "rgba(0, 255, 255, 0.6)" }} />
                    ) : (
                      <ChevronUp size={16} style={{ color: "rgba(0, 255, 255, 0.6)" }} />
                    )}
                  </button>
                )}
              </div>

              {/* Waveform — only when expanded */}
              {mobileBubbleExpanded && <WaveformBars active={isSpeaking} />}

              {/* Response text or error */}
              {errorText ? (
                <>
                  <div style={{
                    color: "rgba(255, 200, 180, 0.9)",
                    fontSize: "14px",
                    lineHeight: 1.6,
                  }}>
                    {errorText}
                  </div>
                  <button
                    onClick={handleSubmit}
                    className="hud-action-btn-pill"
                    style={{ marginTop: "8px" }}
                  >
                    <RotateCcw size={13} />
                    <span>Retry</span>
                  </button>
                </>
              ) : (
                <div style={{
                  color: "rgba(255, 255, 255, 0.92)",
                  fontSize: "14px",
                  lineHeight: 1.6,
                }}>
                  {mobileBubbleExpanded ? bubbleText : firstSentence}
                </div>
              )}

              {/* HUD pills — always visible (collapsed + expanded) */}
              {hasRouteData && (
                <div style={{
                  display: "flex",
                  flexWrap: "wrap" as const,
                  gap: "8px",
                  marginTop: "10px",
                }}>
                  <div
                    style={{
                      ...hudPill,
                      padding: "6px 12px",
                      display: "flex",
                      alignItems: "center",
                      gap: "8px",
                    }}
                  >
                    <span
                      style={{
                        width: 7,
                        height: 7,
                        borderRadius: "50%",
                        background: trainLineColor,
                        display: "inline-block",
                      }}
                    />
                    <span>{transitPillText}</span>
                  </div>
                  <div style={{ ...hudPill, padding: "6px 12px" }}>
                    {etaPillText}
                  </div>
                </div>
              )}

              {/* Action buttons — only when expanded */}
              {mobileBubbleExpanded && showActions && (
                <div style={{ display: "flex", gap: "8px", marginTop: "10px" }}>
                  <button onClick={handleRecenter} className="hud-action-btn-pill" title="Re-center">
                    <Crosshair size={13} /><span>Re-center</span>
                  </button>
                  <button onClick={handleClearRoute} className="hud-action-btn-pill" title="Clear">
                    <X size={13} /><span>Clear</span>
                  </button>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Mobile Input Bar — always visible */}
        <div
          style={{
            padding: "12px 16px",
            background: "rgba(8, 10, 18, 0.9)",
            borderTop: showBubble ? "1px solid rgba(0, 212, 255, 0.06)" : "1px solid rgba(0, 255, 255, 0.12)",
          }}
        >
          <div
            className="flex items-center gap-3 rounded-full px-4 py-3 hud-input-bar"
            style={{
              background: "rgba(8, 10, 18, 0.5)",
              border: "1px solid rgba(0, 212, 255, 0.1)",
            }}
          >
            <AudioLines
              className="shrink-0 cursor-pointer transition-colors duration-200"
              size={20}
              style={{
                color: isListening ? "rgba(0, 212, 255, 1)" : "rgba(0, 212, 255, 0.5)",
                filter: isListening ? "drop-shadow(0 0 6px rgba(0, 212, 255, 0.6))" : undefined,
              }}
              onClick={handleVoiceInput}
            />
            <input
              type="text"
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleSubmit()}
              placeholder="Where are you headed, sir?"
              className="flex-1 bg-transparent text-white outline-none text-sm"
              style={{
                fontFamily: "var(--font-geist-mono), monospace",
                color: "rgba(255, 255, 255, 0.9)",
                fontSize: "16px",
              }}
              disabled={isLoading}
            />
            <button
              onClick={handleSubmit}
              disabled={isLoading}
              className="w-11 h-11 rounded-full flex items-center justify-center transition-all duration-200 disabled:opacity-40 active:scale-95"
              style={{ background: "rgba(0, 212, 255, 0.15)", minWidth: "44px", minHeight: "44px" }}
            >
              {isLoading ? (
                <Loader2 size={18} style={{ color: "rgba(0, 212, 255, 0.8)", animation: "spin 1s linear infinite" }} />
              ) : (
                <ArrowRight size={18} style={{ color: "rgba(0, 212, 255, 0.8)" }} />
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
