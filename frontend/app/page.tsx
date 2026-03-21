"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import { JarvisMap, TransitRouteData } from "@/components/jarvis-map";
import { ArrowRight, AudioLines, ChevronUp, ChevronDown, Zap, TriangleAlert } from "lucide-react";
import { planTrip, getThinkingAudio, ServiceAlert } from "@/lib/api";

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

export default function JarvisPage() {
  const [inputValue, setInputValue] = useState("");
  const [drawerOpen, setDrawerOpen] = useState(true);
  const [userLocation, setUserLocation] = useState<{
    lng: number;
    lat: number;
  } | null>(null);
  const [jarvisText, setJarvisText] = useState("");
  const [displayedText, setDisplayedText] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [latencyMs, setLatencyMs] = useState<number | null>(null);

  // Structured route data from API (Task 6)
  const [trainLine, setTrainLine] = useState<string | null>(null);
  const [departureTimestamp, setDepartureTimestamp] = useState<number | null>(null);
  const [departureMinutes, setDepartureMinutes] = useState<number | null>(null);
  const [direction, setDirection] = useState<string | null>(null);
  const [rideDurationMinutes, setRideDurationMinutes] = useState<number | null>(null);
  const [routeData, setRouteData] = useState<TransitRouteData | null>(null);
  const [destCoords, setDestCoords] = useState<{ lat: number; lng: number } | null>(null);
  const [serviceAlerts, setServiceAlerts] = useState<ServiceAlert[]>([]);

  const wordRevealIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const revealStartedRef = useRef(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const departureIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

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

    tick(); // immediate first computation
    departureIntervalRef.current = setInterval(tick, 15000);

    return () => {
      if (departureIntervalRef.current) {
        clearInterval(departureIntervalRef.current);
        departureIntervalRef.current = null;
      }
    };
  }, [departureTimestamp]);

  const handleLocationUpdate = useCallback(
    (coords: { lng: number; lat: number }) => {
      setUserLocation(coords);
    },
    [],
  );

  async function handleSubmit() {
    if (!userLocation || !inputValue.trim()) return;

    // Stop any playing audio
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current = null;
    }

    // Reset for new request
    if (wordRevealIntervalRef.current) clearInterval(wordRevealIntervalRef.current);
    revealStartedRef.current = false;
    setDisplayedText("");
    setIsSpeaking(false);
    setTrainLine(null);
    setDepartureTimestamp(null);
    setDirection(null);
    setRideDurationMinutes(null);
    setRouteData(null);
    setDestCoords(null);
    setServiceAlerts([]);
    setIsLoading(true);
    const t0 = performance.now();

    try {
      const origin = `${userLocation.lat}, ${userLocation.lng}`;

      // Fire thinking audio immediately — fire and forget
      getThinkingAudio()
        .then((buf) => {
          new Audio(
            URL.createObjectURL(new Blob([buf], { type: "audio/mpeg" })),
          ).play();
        })
        .catch(console.error);

      const trip_data = await planTrip(origin, inputValue);
      setLatencyMs(Math.round(performance.now() - t0));

      const text = trip_data.text;
      setJarvisText(text);

      // Use structured data from API (Task 6)
      setTrainLine(trip_data.trainLine || null);
      setDepartureTimestamp(trip_data.departureTimestamp ?? null);
      setDirection(trip_data.direction || null);
      setRideDurationMinutes(trip_data.rideDurationMinutes ?? null);
      setDestCoords(trip_data.destCoords || null);
      setServiceAlerts(trip_data.serviceAlerts || []);

      // Build route data for the map (Task 4)
      if (trip_data.originStation && trip_data.destStation) {
        const originLngLat: [number, number] = [trip_data.originStation.lng, trip_data.originStation.lat];
        const destLngLat: [number, number] = [trip_data.destStation.lng, trip_data.destStation.lat];
        const userLngLat: [number, number] = [userLocation.lng, userLocation.lat];
        const finalDestLngLat: [number, number] = trip_data.destCoords
          ? [trip_data.destCoords.lng, trip_data.destCoords.lat]
          : destLngLat;

        setRouteData({
          walkIn: [userLngLat, originLngLat],
          // TODO: replace with GTFS shapes polyline when available
          transit: [originLngLat, destLngLat],
          walkOut: [destLngLat, finalDestLngLat],
          trainLine: trip_data.trainLine || "?",
          originStationName: trip_data.originStation.name,
          destStationName: trip_data.destStation.name,
        });
      }

      // Build trip audio element
      const bytes = Uint8Array.from(atob(trip_data.audio), (c) =>
        c.charCodeAt(0),
      );
      const tripAudio = new Audio(
        URL.createObjectURL(new Blob([bytes], { type: "audio/mpeg" })),
      );
      audioRef.current = tripAudio;

      // Word-by-word reveal synced to audio duration (Task 3)
      function startWordReveal(audioDuration: number) {
        if (revealStartedRef.current) return;
        revealStartedRef.current = true;
        const words = text.split(/\s+/).filter((w) => w.length > 0);
        if (words.length === 0) return;
        const intervalMs = Math.max(
          (audioDuration * 1000) / words.length,
          80, // minimum 80ms per word fallback
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

      const fallbackDuration = text.split(" ").length * 0.45; // ~130 wpm

      tripAudio.addEventListener("loadedmetadata", () => {
        const dur =
          isFinite(tripAudio.duration) && tripAudio.duration > 0
            ? tripAudio.duration
            : fallbackDuration;
        startWordReveal(dur);
      });

      // Fallback if loadedmetadata doesn't fire within 300ms
      setTimeout(() => startWordReveal(fallbackDuration), 300);

      setIsSpeaking(true);
      tripAudio.play();
      tripAudio.onended = () => {
        setIsSpeaking(false);
        audioRef.current = null;
      };

      setInputValue("");
    } catch (error) {
      console.error("Error:", error);
      setJarvisText("Unable to plan your trip. Please try again.");
      setDisplayedText("Unable to plan your trip. Please try again.");
    } finally {
      setIsLoading(false);
    }
  }

  // Bubble visibility: show when loading or when we have a response
  const showBubble = isLoading || !!jarvisText;
  const bubbleText = isLoading
    ? "Scanning MTA feeds, sir..."
    : displayedText || jarvisText;

  // Pill visibility: show when we have structured route data
  const hasRouteData = !!trainLine;
  const trainLineColor = trainLine ? (MTA_COLORS[trainLine] || "#FFD700") : "#FFD700";

  // Format pill content (Task 6)
  const transitPillText = trainLine
    ? departureMinutes != null
      ? `${trainLine} \u2014 in ${departureMinutes} min${direction ? ` \u00B7 ${direction}` : ""}`
      : `${trainLine} \u2014 checking...`
    : "";

  const etaPillText = rideDurationMinutes != null
    ? `~${rideDurationMinutes} min ride`
    : "ETA pending";

  return (
    <div className="relative h-screen w-full overflow-hidden bg-[#0a0a0f]">
      {/* Full-screen Mapbox 3D Map */}
      <JarvisMap
        onLocationUpdate={handleLocationUpdate}
        routeData={routeData}
        isSpeaking={isSpeaking}
        destCoords={destCoords}
      />

      {/* JARVIS Logo — Top Left (hidden on mobile) */}
      <div className="hidden md:block absolute top-6 left-6 z-10">
        <h1
          className="text-sm font-medium tracking-widest"
          style={{ color: "rgba(255, 255, 255, 0.4)" }}
        >
          JARVIS
        </h1>
      </div>

      {/* AI CORE ALPHA — Top Right (hidden on mobile) */}
      <div className="hidden md:flex absolute top-6 right-6 z-10 items-center gap-2.5">
        <Zap size={14} style={{ color: "rgba(0, 255, 255, 0.6)" }} />
        <span
          className="text-xs tracking-wider"
          style={{ color: "rgba(255, 255, 255, 0.4)" }}
        >
          AI CORE ALPHA{latencyMs != null ? ` \u2014 ${latencyMs}ms` : ""}
        </span>
      </div>

      {/* HUD Overlay Pills — Top Center (Task 2c, 6) */}
      {hasRouteData && (
        <div className="absolute top-6 left-1/2 -translate-x-1/2 z-10 flex items-center gap-2">
          {/* NEXT TRANSIT pill */}
          <div
            style={{
              backdropFilter: "blur(16px)",
              WebkitBackdropFilter: "blur(16px)",
              background: "rgba(8, 10, 18, 0.65)",
              border: "1px solid rgba(255, 255, 255, 0.06)",
              padding: "6px 14px",
              borderRadius: "20px",
              fontSize: "13px",
              letterSpacing: "0.01em",
              color: "rgba(255, 255, 255, 0.88)",
              display: "flex",
              alignItems: "center",
              gap: "8px",
              animation: "hudPillIn 300ms cubic-bezier(0.16, 1, 0.3, 1) forwards",
              whiteSpace: "nowrap" as const,
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

          {/* ETA pill */}
          <div
            style={{
              backdropFilter: "blur(16px)",
              WebkitBackdropFilter: "blur(16px)",
              background: "rgba(8, 10, 18, 0.65)",
              border: "1px solid rgba(255, 255, 255, 0.06)",
              padding: "6px 14px",
              borderRadius: "20px",
              fontSize: "13px",
              letterSpacing: "0.01em",
              color: "rgba(255, 255, 255, 0.88)",
              animation: "hudPillIn 300ms cubic-bezier(0.16, 1, 0.3, 1) 100ms forwards",
              opacity: 0,
              whiteSpace: "nowrap" as const,
            }}
          >
            {etaPillText}
          </div>
        </div>
      )}

      {/* Service Alert Banner — Desktop, below pills (Step 7) */}
      {serviceAlerts.length > 0 && (
        <div
          className="hidden md:flex absolute z-10 left-1/2 -translate-x-1/2 flex-col items-center gap-1.5"
          style={{ top: hasRouteData ? "58px" : "24px" }}
        >
          {serviceAlerts.slice(0, 2).map((alert, i) => (
            <div
              key={i}
              style={{
                backdropFilter: "blur(16px)",
                WebkitBackdropFilter: "blur(16px)",
                background: "rgba(8, 10, 18, 0.7)",
                border: "1px solid rgba(245, 166, 35, 0.15)",
                padding: "7px 14px",
                borderRadius: "20px",
                fontSize: "12px",
                letterSpacing: "0.01em",
                color: "rgba(255, 255, 255, 0.85)",
                display: "flex",
                alignItems: "center",
                gap: "8px",
                maxWidth: "460px",
                animation: `hudAlertIn 350ms cubic-bezier(0.16, 1, 0.3, 1) ${200 + i * 120}ms forwards, alertBorderPulse 3s ease-in-out ${200 + i * 120}ms infinite`,
                opacity: 0,
              }}
            >
              <TriangleAlert
                size={13}
                style={{ color: "#F5A623", flexShrink: 0 }}
              />
              <span
                style={{
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {alert.routeIds.length > 0 && (
                  <span style={{ color: "#F5A623", fontWeight: 600, marginRight: 6 }}>
                    {alert.routeIds.join(", ")}
                  </span>
                )}
                {alert.header}
              </span>
            </div>
          ))}
          {serviceAlerts.length > 2 && (
            <span
              style={{
                fontSize: "11px",
                color: "rgba(245, 166, 35, 0.6)",
                letterSpacing: "0.02em",
                animation: "hudAlertIn 350ms cubic-bezier(0.16, 1, 0.3, 1) 450ms forwards",
                opacity: 0,
              }}
            >
              +{serviceAlerts.length - 2} more alert{serviceAlerts.length - 2 > 1 ? "s" : ""}
            </span>
          )}
        </div>
      )}

      {/* JARVIS Response Bubble — Bottom Center, above input (Task 2b) */}
      {showBubble && (
        <div className="hidden md:block absolute bottom-24 left-1/2 -translate-x-1/2 z-10 w-full max-w-[600px] px-4">
          <div
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
            {bubbleText}
          </div>
        </div>
      )}

      {/* Desktop Input Bar — Bottom Center */}
      <div className="hidden md:block absolute bottom-8 left-1/2 -translate-x-1/2 z-10 w-full max-w-xl px-4">
        <div
          className="flex items-center gap-3 rounded-full px-5 py-3"
          style={{
            background: "rgba(8, 10, 18, 0.65)",
            backdropFilter: "blur(16px)",
            WebkitBackdropFilter: "blur(16px)",
            border: "1px solid rgba(255, 255, 255, 0.06)",
          }}
        >
          <AudioLines
            className="shrink-0"
            size={20}
            style={{ color: "rgba(0, 255, 255, 0.6)" }}
          />
          <input
            type="text"
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSubmit()}
            placeholder="Where are you headed, sir?"
            className="flex-1 bg-transparent text-white placeholder-gray-500 outline-none text-sm"
            disabled={isLoading}
          />
          <button
            onClick={handleSubmit}
            disabled={isLoading}
            className="w-9 h-9 rounded-full flex items-center justify-center transition-opacity disabled:opacity-40"
            style={{ background: "rgba(0, 255, 255, 0.15)" }}
          >
            <ArrowRight size={18} style={{ color: "rgba(0, 255, 255, 0.8)" }} />
          </button>
        </div>
      </div>

      {/* ─── Mobile Bottom Drawer ─── */}
      <div
        className={`md:hidden fixed inset-x-0 bottom-0 z-20 transition-transform duration-300 ease-out ${
          drawerOpen ? "translate-y-0" : "translate-y-[calc(100%-56px)]"
        }`}
      >
        {/* Drawer Handle */}
        <button
          onClick={() => setDrawerOpen(!drawerOpen)}
          className="absolute -top-5 left-1/2 -translate-x-1/2 w-12 h-5 rounded-t-lg flex items-center justify-center"
          style={{
            background: "rgba(8, 10, 18, 0.8)",
            borderTop: "1px solid rgba(0, 255, 255, 0.12)",
            borderLeft: "1px solid rgba(0, 255, 255, 0.12)",
            borderRight: "1px solid rgba(0, 255, 255, 0.12)",
          }}
        >
          {drawerOpen ? (
            <ChevronDown size={16} style={{ color: "rgba(0, 255, 255, 0.6)" }} />
          ) : (
            <ChevronUp size={16} style={{ color: "rgba(0, 255, 255, 0.6)" }} />
          )}
        </button>

        {/* Drawer Content — frosted glass (Task 2f) */}
        <div
          className="rounded-t-2xl max-h-[70vh] overflow-hidden flex flex-col"
          style={{
            backdropFilter: "blur(20px) saturate(1.4)",
            WebkitBackdropFilter: "blur(20px) saturate(1.4)",
            background: "rgba(8, 10, 18, 0.75)",
            borderTop: "1px solid rgba(0, 255, 255, 0.12)",
            boxShadow: "0 -10px 40px rgba(0, 0, 0, 0.3)",
          }}
        >
          {/* Scrollable Content */}
          <div className="flex-1 overflow-y-auto p-4 space-y-3">
            {/* JARVIS Response */}
            {showBubble && (
              <div
                style={{
                  background: "rgba(8, 10, 18, 0.5)",
                  border: "1px solid rgba(0, 255, 255, 0.08)",
                  borderRadius: "12px",
                  padding: "16px",
                  color: "rgba(255, 255, 255, 0.92)",
                  fontSize: "14px",
                  lineHeight: 1.6,
                  maxHeight: "120px",
                  overflowY: "auto" as const,
                  animation: isLoading ? "hudBorderPulse 2s ease-in-out infinite" : undefined,
                }}
              >
                {bubbleText}
              </div>
            )}

            {/* HUD data pills (mobile) */}
            {hasRouteData && (
              <div className="flex flex-wrap gap-2">
                <div
                  className="flex items-center gap-2"
                  style={{
                    background: "rgba(8, 10, 18, 0.5)",
                    border: "1px solid rgba(255, 255, 255, 0.06)",
                    padding: "6px 12px",
                    borderRadius: "20px",
                    fontSize: "12px",
                    color: "rgba(255, 255, 255, 0.88)",
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
                <div
                  style={{
                    background: "rgba(8, 10, 18, 0.5)",
                    border: "1px solid rgba(255, 255, 255, 0.06)",
                    padding: "6px 12px",
                    borderRadius: "20px",
                    fontSize: "12px",
                    color: "rgba(255, 255, 255, 0.88)",
                  }}
                >
                  {etaPillText}
                </div>
              </div>
            )}

            {/* Service alerts (mobile) */}
            {serviceAlerts.length > 0 && (
              <div className="flex flex-col gap-1.5">
                {serviceAlerts.slice(0, 2).map((alert, i) => (
                  <div
                    key={i}
                    className="flex items-center gap-2"
                    style={{
                      background: "rgba(8, 10, 18, 0.5)",
                      border: "1px solid rgba(245, 166, 35, 0.15)",
                      padding: "7px 12px",
                      borderRadius: "16px",
                      fontSize: "11px",
                      color: "rgba(255, 255, 255, 0.85)",
                      animation: `alertBorderPulse 3s ease-in-out ${i * 120}ms infinite`,
                    }}
                  >
                    <TriangleAlert
                      size={12}
                      style={{ color: "#F5A623", flexShrink: 0 }}
                    />
                    <span
                      style={{
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {alert.routeIds.length > 0 && (
                        <span style={{ color: "#F5A623", fontWeight: 600, marginRight: 5 }}>
                          {alert.routeIds.join(", ")}
                        </span>
                      )}
                      {alert.header}
                    </span>
                  </div>
                ))}
                {serviceAlerts.length > 2 && (
                  <span
                    style={{
                      fontSize: "10px",
                      color: "rgba(245, 166, 35, 0.5)",
                      paddingLeft: "4px",
                    }}
                  >
                    +{serviceAlerts.length - 2} more alert{serviceAlerts.length - 2 > 1 ? "s" : ""}
                  </span>
                )}
              </div>
            )}

            {/* AI Core Alpha — inside drawer on mobile */}
            <div className="flex items-center gap-2.5 pt-2">
              <Zap size={13} style={{ color: "rgba(0, 255, 255, 0.5)" }} />
              <span
                className="text-xs tracking-wider"
                style={{ color: "rgba(255, 255, 255, 0.35)" }}
              >
                AI CORE ALPHA{latencyMs != null ? ` \u2014 ${latencyMs}ms` : ""}
              </span>
            </div>
          </div>

          {/* Mobile Input Bar — inside drawer */}
          <div
            className="p-4"
            style={{ borderTop: "1px solid rgba(255, 255, 255, 0.04)" }}
          >
            <div
              className="flex items-center gap-3 rounded-full px-4 py-3"
              style={{
                background: "rgba(8, 10, 18, 0.5)",
                border: "1px solid rgba(255, 255, 255, 0.06)",
              }}
            >
              <AudioLines
                className="shrink-0"
                size={20}
                style={{ color: "rgba(0, 255, 255, 0.5)" }}
              />
              <input
                type="text"
                value={inputValue}
                onChange={(e) => setInputValue(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleSubmit()}
                placeholder="Where are you headed, sir?"
                className="flex-1 bg-transparent text-white placeholder-gray-500 outline-none text-sm"
                disabled={isLoading}
              />
              <button
                onClick={handleSubmit}
                disabled={isLoading}
                className="w-9 h-9 rounded-full flex items-center justify-center disabled:opacity-40"
                style={{ background: "rgba(0, 255, 255, 0.15)" }}
              >
                <ArrowRight size={18} style={{ color: "rgba(0, 255, 255, 0.8)" }} />
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
