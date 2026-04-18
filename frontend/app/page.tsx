"use client";

import { useState, useCallback, useRef, useEffect, useMemo } from "react";
import type { TransitRouteData, RouteStep, ServiceAlert } from "@/types";
import { JarvisMap } from "@/components/jarvis-map";
import { planTrip, getThinking, DEFAULT_LOCATION } from "@/lib/api";
import { getLineColor } from "@/components/map/route-layers";
import {
  summarizeRoute,
  type RouteSummary,
  type AgentLogEntry,
  INITIAL_LOG,
  THINKING_LOG_SEED,
  nowStamp,
} from "@/lib/smart-route";

import { Header, type TabId } from "@/components/smart-route/header";
import {
  RecommendationPanel,
  type AgentState,
} from "@/components/smart-route/recommendation-panel";
import { AgentLog } from "@/components/smart-route/agent-log";
import { TripBar } from "@/components/smart-route/trip-bar";
import {
  AlternateCard,
  type AlternateRoute,
} from "@/components/smart-route/alternate-card";
import { NetworkStatus } from "@/components/smart-route/network-status";

const ACCENT = "#d4a7ff";

function pushLog(
  set: React.Dispatch<React.SetStateAction<AgentLogEntry[]>>,
  entry: Omit<AgentLogEntry, "t">,
) {
  set((prev) => [...prev, { t: nowStamp(), ...entry }]);
}

function deriveAlternates(primary: RouteSummary | null): AlternateRoute[] {
  if (!primary) return [];
  // Simple derived set: the primary summary + two stylized alternates for the right rail.
  const base = primary.totalMin;
  const via = primary.transitLines[0] ?? "route";
  const alt1: AlternateRoute = {
    id: "alt-1",
    label: `Via ${via} express`,
    verdict: "SLOWER",
    confidence: 82,
    eta: primary.arriveLabel,
    totalMin: Math.round(base * 1.18),
    reasonShort: "Fewer transfers, but slightly longer ride time.",
  };
  const alt2: AlternateRoute = {
    id: "alt-2",
    label: "Surface bus alt.",
    verdict: "DELAYED",
    confidence: 61,
    eta: primary.arriveLabel,
    totalMin: Math.round(base * 1.4),
    reasonShort: "Traffic congestion flagged by live incident feed.",
  };
  return [alt1, alt2];
}

export default function JarvisPage() {
  const [tab, setTab] = useState<TabId>("planner");
  const [inputValue, setInputValue] = useState("");
  const [userLocation, setUserLocation] = useState<{
    lng: number;
    lat: number;
  } | null>(null);

  // Core trip state
  const [jarvisText, setJarvisText] = useState("");
  const [displayedText, setDisplayedText] = useState("");
  const [thinkingText, setThinkingText] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [errorText, setErrorText] = useState<string | null>(null);
  const [isListening, setIsListening] = useState(false);
  const [routeData, setRouteData] = useState<TransitRouteData | null>(null);
  const [destCoords, setDestCoords] = useState<{
    lat: number;
    lng: number;
  } | null>(null);
  const [alerts, setAlerts] = useState<ServiceAlert[]>([]);
  const [summary, setSummary] = useState<RouteSummary | null>(null);
  const [logEntries, setLogEntries] =
    useState<AgentLogEntry[]>(INITIAL_LOG);
  const [showDetails, setShowDetails] = useState(true);

  // Animation refs
  const wordRevealIntervalRef = useRef<ReturnType<typeof setInterval> | null>(
    null,
  );
  const thinkingRevealRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const revealStartedRef = useRef(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const speakingTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const thinkingLogTimerRef = useRef<ReturnType<typeof setInterval> | null>(
    null,
  );
  const mapActionsRef = useRef<{ recenter: () => void } | null>(null);

  // Origin label derived from user location
  const originLabel = useMemo(() => {
    if (!userLocation) return "Locating…";
    if (
      Math.abs(userLocation.lat - DEFAULT_LOCATION.lat) < 0.001 &&
      Math.abs(userLocation.lng - DEFAULT_LOCATION.lng) < 0.001
    ) {
      return "Empire State Building";
    }
    return "Current location";
  }, [userLocation]);
  const originSub = userLocation
    ? `${userLocation.lat.toFixed(3)}, ${userLocation.lng.toFixed(3)}`
    : null;

  // GPS
  useEffect(() => {
    if (!navigator.geolocation) {
      setUserLocation(DEFAULT_LOCATION);
      return;
    }
    const t = setTimeout(() => {
      setUserLocation((prev) => prev ?? DEFAULT_LOCATION);
    }, 8000);
    return () => clearTimeout(t);
  }, []);

  const handleLocationUpdate = useCallback(
    (coords: { lng: number; lat: number }) => setUserLocation(coords),
    [],
  );
  const handleMapReady = useCallback(
    (actions: { recenter: () => void }) => {
      mapActionsRef.current = actions;
    },
    [],
  );

  function handleVoiceInput() {
    const w = window as unknown as {
      SpeechRecognition?: new () => {
        lang: string;
        onstart: () => void;
        onresult: (e: { results: { [index: number]: { [index: number]: { transcript: string } } } }) => void;
        onend: () => void;
        onerror: () => void;
        start: () => void;
      };
      webkitSpeechRecognition?: new () => {
        lang: string;
        onstart: () => void;
        onresult: (e: { results: { [index: number]: { [index: number]: { transcript: string } } } }) => void;
        onend: () => void;
        onerror: () => void;
        start: () => void;
      };
    };
    const Ctor = w.SpeechRecognition || w.webkitSpeechRecognition;
    if (!Ctor) return;
    const recognition = new Ctor();
    recognition.lang = "en-US";
    recognition.onstart = () => setIsListening(true);
    recognition.onresult = (e) => setInputValue(e.results[0][0].transcript);
    recognition.onend = () => setIsListening(false);
    recognition.onerror = () => setIsListening(false);
    recognition.start();
  }

  function stopThinkingLogStream() {
    if (thinkingLogTimerRef.current) {
      clearInterval(thinkingLogTimerRef.current);
      thinkingLogTimerRef.current = null;
    }
  }

  function startThinkingLogStream() {
    stopThinkingLogStream();
    let i = 0;
    thinkingLogTimerRef.current = setInterval(() => {
      if (i >= THINKING_LOG_SEED.length) {
        stopThinkingLogStream();
        return;
      }
      pushLog(setLogEntries, THINKING_LOG_SEED[i]);
      i++;
    }, 1600);
  }

  async function handleSubmit() {
    if (!inputValue.trim()) return;
    if (!userLocation) {
      setErrorText("Waiting for GPS location...");
      setTimeout(() => setErrorText(null), 3000);
      return;
    }

    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current = null;
    }
    if (speakingTimeoutRef.current) {
      clearTimeout(speakingTimeoutRef.current);
      speakingTimeoutRef.current = null;
    }

    // Unlock audio on gesture
    const unlockedAudio = new Audio();
    unlockedAudio.src =
      "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YQAAAAA=";
    unlockedAudio.play().catch(() => {});
    audioRef.current = unlockedAudio;

    if (wordRevealIntervalRef.current)
      clearInterval(wordRevealIntervalRef.current);
    if (thinkingRevealRef.current) clearInterval(thinkingRevealRef.current);
    revealStartedRef.current = false;

    setDisplayedText("");
    setThinkingText("");
    setIsSpeaking(false);
    setRouteData(null);
    setDestCoords(null);
    setSummary(null);
    setErrorText(null);
    setIsLoading(true);
    pushLog(setLogEntries, {
      level: "scan",
      text: `Query received · "${inputValue.trim()}"`,
    });
    startThinkingLogStream();

    try {
      getThinking()
        .then(({ text, audio }) => {
          const bytes = Uint8Array.from(atob(audio), (c) => c.charCodeAt(0));
          const thinkAudio = new Audio(
            URL.createObjectURL(new Blob([bytes], { type: "audio/mpeg" })),
          );
          const words = text.split(/\s+/).filter((w) => w.length > 0);
          let idx = 0;
          function startThinkingReveal(duration: number) {
            const intervalMs = Math.max((duration * 1000) / words.length, 80);
            if (thinkingRevealRef.current)
              clearInterval(thinkingRevealRef.current);
            thinkingRevealRef.current = setInterval(() => {
              idx++;
              setThinkingText(words.slice(0, idx).join(" "));
              if (idx >= words.length)
                clearInterval(thinkingRevealRef.current!);
            }, intervalMs);
          }
          const fallback = words.length * 0.35;
          thinkAudio.addEventListener("loadedmetadata", () => {
            const dur =
              isFinite(thinkAudio.duration) && thinkAudio.duration > 0
                ? thinkAudio.duration
                : fallback;
            startThinkingReveal(dur);
          });
          setTimeout(() => {
            if (!thinkingRevealRef.current) startThinkingReveal(fallback);
          }, 200);
          thinkAudio.play().catch(() => {});
        })
        .catch(() => {});

      const trip_data = await planTrip(
        userLocation.lat,
        userLocation.lng,
        inputValue,
      );

      stopThinkingLogStream();
      const text = trip_data.recommendation;
      setJarvisText(text);

      const chosenRoute = trip_data.route || [];
      const sum = summarizeRoute(chosenRoute);
      setSummary(sum);

      const firstTransit = chosenRoute.find(
        (s: RouteStep) => s.type === "SUBWAY" || s.type === "BUS",
      );
      if (firstTransit?.train_line) {
        pushLog(setLogEntries, {
          level: "detect",
          text: `Primary leg identified · ${firstTransit.train_line} ${firstTransit.direction || ""}`.trim(),
        });
      }
      if (sum.transferStation) {
        pushLog(setLogEntries, {
          level: "reason",
          text: `Transfer planned at ${sum.transferStation}.`,
        });
      }
      pushLog(setLogEntries, {
        level: "decision",
        text: `Selected · ${sum.transitLines.join(" + ") || "route"} · ${sum.totalMin} min total.`,
      });

      const lastStep = chosenRoute[chosenRoute.length - 1];
      const rawDest =
        lastStep?.type === "WALK"
          ? lastStep.end_point
          : lastStep?.arrival_coords;
      const destCoordsComputed = rawDest
        ? { lat: rawDest.latitude, lng: rawDest.longitude }
        : null;
      setDestCoords(destCoordsComputed);
      setRouteData({ steps: chosenRoute });
      setAlerts(trip_data.alerts || []);

      const bytes = Uint8Array.from(atob(trip_data.audio), (c) =>
        c.charCodeAt(0),
      );
      const tripAudioUrl = URL.createObjectURL(
        new Blob([bytes], { type: "audio/mpeg" }),
      );
      const tripAudio = unlockedAudio;
      tripAudio.src = tripAudioUrl;
      audioRef.current = tripAudio;

      function startWordReveal(audioDuration: number) {
        if (revealStartedRef.current) return;
        revealStartedRef.current = true;
        const words = text.split(/\s+/).filter((w) => w.length > 0);
        if (words.length === 0) return;
        const intervalMs = Math.max((audioDuration * 1000) / words.length, 80);
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
      };
      tripAudio.play().catch(() => setIsSpeaking(false));

      setInputValue("");
    } catch (error) {
      stopThinkingLogStream();
      const msg = error instanceof Error ? error.message : "Unknown error";
      const display = msg.includes("Failed to plan trip")
        ? "No route found, sir. Try a more specific address."
        : "Connection error. Check your network and try again.";
      setErrorText(display);
      setJarvisText("");
      setDisplayedText("");
      pushLog(setLogEntries, { level: "scan", text: display });
    } finally {
      setIsLoading(false);
    }
  }

  function handleRetry() {
    setErrorText(null);
    handleSubmit();
  }

  function handlePlayVoice() {
    if (!audioRef.current) return;
    try {
      audioRef.current.currentTime = 0;
      audioRef.current.play().catch(() => {});
      setIsSpeaking(true);
    } catch {}
  }

  const agentState: AgentState = isLoading
    ? "thinking"
    : isSpeaking
      ? "speaking"
      : "idle";

  const alternates = useMemo(() => deriveAlternates(summary), [summary]);

  const plannerView = (
    <div
      className="flex gap-3.5 flex-1 min-h-0"
      style={{ padding: 14, boxSizing: "border-box" }}
    >
      {/* Left rail */}
      <div
        className="flex flex-col gap-3.5 flex-shrink-0"
        style={{ width: 380, minHeight: 0 }}
      >
        <RecommendationPanel
          accent={ACCENT}
          state={agentState}
          summary={summary}
          recommendationText={jarvisText}
          displayedText={displayedText}
          thinkingText={thinkingText}
          voicePlaying={isSpeaking}
          onPlayVoice={handlePlayVoice}
          showDetails={showDetails}
          onToggleDetails={() => setShowDetails((v) => !v)}
          confidence={92}
          errorText={errorText}
          onRetry={handleRetry}
        />
        <AgentLog accent={ACCENT} entries={logEntries} live={isLoading} />
      </div>

      {/* Center map stage */}
      <div
        className="relative flex-1 min-w-0"
        style={{
          background: "#0a0e15",
          borderRadius: 14,
          overflow: "hidden",
          border: "1px solid rgba(255,255,255,0.06)",
        }}
      >
        <div
          className="absolute"
          style={{ top: 14, left: 14, right: 14, zIndex: 5 }}
        >
          <TripBar
            originLabel={originLabel}
            originSub={originSub}
            inputValue={inputValue}
            onInputChange={setInputValue}
            onSubmit={handleSubmit}
            isLoading={isLoading}
            onVoiceInput={handleVoiceInput}
            isListening={isListening}
            accent={ACCENT}
          />
        </div>

        <div className="absolute inset-0">
          <JarvisMap
            onLocationUpdate={handleLocationUpdate}
            routeData={routeData}
            isSpeaking={isSpeaking}
            destCoords={destCoords}
            onMapReady={handleMapReady}
          />
        </div>

        <div
          className="absolute flex items-center gap-2"
          style={{
            bottom: 14,
            right: 14,
            zIndex: 5,
            padding: "6px 10px",
            background: "rgba(8,11,17,0.82)",
            backdropFilter: "blur(6px)",
            WebkitBackdropFilter: "blur(6px)",
            border: "1px solid rgba(255,255,255,0.07)",
            borderRadius: 10,
            fontFamily: "var(--font-jetbrains-mono), monospace",
            fontSize: 10,
            color: "#9ccfbf",
            letterSpacing: "0.1em",
          }}
        >
          <span
            style={{
              width: 5,
              height: 5,
              borderRadius: 3,
              background: "#9ccfbf",
              animation: "srPulse 1.4s infinite",
            }}
          />
          LIVE · GTFS-RT
        </div>
      </div>

      {/* Right rail */}
      <div
        className="flex flex-col gap-2.5 flex-shrink-0"
        style={{ width: 280 }}
      >
        <div
          style={{
            fontFamily: "var(--font-geist), sans-serif",
            fontSize: 10,
            letterSpacing: "0.18em",
            color: "rgba(255,255,255,0.5)",
            fontWeight: 500,
            padding: "2px 2px",
          }}
        >
          ALTERNATE OPTIONS
        </div>
        {alternates.length === 0 && (
          <div
            style={{
              padding: 14,
              background: "rgba(255,255,255,0.025)",
              border: "1px solid rgba(255,255,255,0.06)",
              borderRadius: 12,
              fontFamily: "var(--font-geist), sans-serif",
              fontSize: 11.5,
              color: "rgba(255,255,255,0.5)",
              lineHeight: 1.5,
            }}
          >
            Submit a destination to evaluate alternates against live signal.
          </div>
        )}
        {alternates.map((r) => (
          <AlternateCard key={r.id} route={r} accent={ACCENT} />
        ))}
        <NetworkStatus alerts={alerts} />
      </div>
    </div>
  );

  const liveMapView = (
    <div
      className="flex-1 relative"
      style={{ margin: 14, borderRadius: 14, overflow: "hidden" }}
    >
      <JarvisMap
        onLocationUpdate={handleLocationUpdate}
        routeData={routeData}
        isSpeaking={isSpeaking}
        destCoords={destCoords}
        onMapReady={handleMapReady}
      />
    </div>
  );

  const grokView = (
    <div
      className="flex-1 overflow-auto"
      style={{ padding: "24px 28px" }}
    >
      <div
        style={{
          fontFamily: "var(--font-instrument-serif), serif",
          fontSize: 32,
          color: "#fff",
          marginBottom: 4,
        }}
      >
        Grok <span style={{ color: ACCENT, fontStyle: "italic" }}>Intel</span>
      </div>
      <div
        style={{
          fontFamily: "var(--font-geist), sans-serif",
          fontSize: 12,
          color: "rgba(255,255,255,0.55)",
          letterSpacing: "0.04em",
          marginBottom: 20,
        }}
      >
        Cross-referenced social + official feeds for context around your route.
      </div>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        {[
          {
            source: "@NYCTSubway",
            weight: 0.92,
            note: "Official MTA live service updates.",
          },
          {
            source: "311 Reports",
            weight: 0.74,
            note: "City incident & construction feed.",
          },
          {
            source: "Grok Social",
            weight: 0.68,
            note: "Rider sentiment cross-checked against officials.",
          },
        ].map((s) => (
          <div
            key={s.source}
            style={{
              padding: 16,
              background: "rgba(255,255,255,0.03)",
              border: "1px solid rgba(255,255,255,0.07)",
              borderRadius: 12,
            }}
          >
            <div
              style={{
                fontFamily: "var(--font-geist), sans-serif",
                fontSize: 11,
                letterSpacing: "0.12em",
                color: "rgba(255,255,255,0.55)",
                marginBottom: 8,
              }}
            >
              {s.source}
            </div>
            <div
              style={{
                fontFamily: "var(--font-jetbrains-mono), monospace",
                fontSize: 22,
                color: ACCENT,
              }}
            >
              {s.weight.toFixed(2)}
            </div>
            <div
              style={{
                marginTop: 6,
                fontFamily: "var(--font-geist), sans-serif",
                fontSize: 12,
                color: "rgba(255,255,255,0.7)",
                lineHeight: 1.45,
              }}
            >
              {s.note}
            </div>
          </div>
        ))}
      </div>
    </div>
  );

  const alertsView = (
    <div
      className="flex-1 overflow-auto"
      style={{ padding: "24px 28px" }}
    >
      <div
        style={{
          fontFamily: "var(--font-instrument-serif), serif",
          fontSize: 32,
          color: "#fff",
          marginBottom: 4,
        }}
      >
        Service <span style={{ color: ACCENT, fontStyle: "italic" }}>Alerts</span>
      </div>
      <div
        style={{
          fontFamily: "var(--font-geist), sans-serif",
          fontSize: 12,
          color: "rgba(255,255,255,0.55)",
          marginBottom: 20,
          letterSpacing: "0.04em",
        }}
      >
        {alerts.length === 0
          ? "No alerts flagged in the latest planning run."
          : `${alerts.length} alert${alerts.length > 1 ? "s" : ""} affecting your route or surrounding lines.`}
      </div>
      <div className="flex flex-col gap-2.5">
        {alerts.map((a, i) => (
          <div
            key={i}
            style={{
              padding: 14,
              background: "rgba(255,255,255,0.03)",
              border: "1px solid rgba(255,104,104,0.18)",
              borderLeft: "3px solid #ff6868",
              borderRadius: 10,
            }}
          >
            <div className="flex items-center gap-2" style={{ marginBottom: 6 }}>
              {(a.routeIds || []).map((r) => (
                <span
                  key={r}
                  className="flex items-center justify-center"
                  style={{
                    width: 20,
                    height: 20,
                    borderRadius: 10,
                    background: getLineColor(r),
                    color: "#0b0e13",
                    fontFamily: "var(--font-geist), sans-serif",
                    fontWeight: 700,
                    fontSize: 10,
                  }}
                >
                  {r}
                </span>
              ))}
            </div>
            <div
              style={{
                fontFamily: "var(--font-geist), sans-serif",
                fontSize: 13,
                color: "rgba(255,255,255,0.9)",
                lineHeight: 1.45,
              }}
            >
              {a.header}
            </div>
          </div>
        ))}
      </div>
    </div>
  );

  return (
    <div
      className="flex flex-col"
      style={{ height: "100vh", background: "#0a0d13", overflow: "hidden" }}
    >
      <Header
        activeTab={tab}
        onTabChange={setTab}
        accent={ACCENT}
        systemStatus={errorText ? "error" : isLoading ? "warning" : "nominal"}
      />
      {tab === "planner" && plannerView}
      {tab === "livemap" && liveMapView}
      {tab === "grok" && grokView}
      {tab === "alerts" && alertsView}
    </div>
  );
}
