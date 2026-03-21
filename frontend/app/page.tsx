"use client";

import { useState, useCallback, useRef } from "react";
import { JarvisMap, TransitRouteData } from "@/components/jarvis-map";
import {
  Settings,
  User,
  Zap,
  ArrowRight,
  AudioLines,
  ChevronUp,
  ChevronDown,
} from "lucide-react";
import { planTrip, getThinkingAudio } from "@/lib/api";

// Test route: Brooklyn (Church Av Q) → Manhattan (34 St-Herald Sq)
const TEST_ROUTE_DATA: TransitRouteData = {
  walkIn: [
    [-73.9443, 40.6499],
    [-73.9449, 40.6510],
  ],
  transit: [
    [-73.9449, 40.6510],
    [-73.9502, 40.6776],
    [-73.9595, 40.7090],
    [-73.9857, 40.7484],
  ],
  walkOut: [
    [-73.9857, 40.7484],
    [-73.9913, 40.7506],
  ],
  trainLine: "Q",
  originStationName: "Church Av",
  destStationName: "34 St-Herald Sq",
};

function parseEtaMinutes(text: string): number | null {
  const m =
    text.match(/(\d+)[- ]?min(?:ute)?s?\s+(?:ride|trip|journey)/i) ||
    text.match(/(?:ride|trip|journey)\s+(?:is\s+)?(?:about\s+)?(\d+)\s+min/i) ||
    text.match(/arrive[sd]?\s+in\s+(?:about\s+)?(\d+)\s+min/i) ||
    text.match(/(\d+)\s+min(?:ute)?s?\s+(?:away|from now)/i);
  return m ? parseInt(m[1]) : null;
}

function parseNextTrain(text: string): string | null {
  const m = text.match(/\b([ACEJLNQRZ]|[1-7]|SI)\s+(?:train|line)\b/i);
  return m ? `${m[1].toUpperCase()} Train` : null;
}

function parseDirection(text: string): string | null {
  const m = text.match(/\b(northbound|southbound|eastbound|westbound)\b/i);
  if (!m) return null;
  const d = m[1].toLowerCase();
  return d.charAt(0).toUpperCase() + d.slice(1);
}

function parseDeparture(text: string): string | null {
  const m =
    text.match(/(?:departs?|leaves?|board(?:ing)?)\s+in\s+(?:about\s+)?(\d+)\s+min/i) ||
    text.match(/\bin\s+(?:about\s+)?(\d+)\s+min(?:ute)?s?\b/i);
  return m ? `in ${m[1]} min` : null;
}

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
  const [nextTrain, setNextTrain] = useState<string | null>(null);
  const [nextDeparture, setNextDeparture] = useState<string | null>(null);
  const [direction, setDirection] = useState<string | null>(null);
  const [etaMinutes, setEtaMinutes] = useState<number | null>(null);
  const [latencyMs, setLatencyMs] = useState<number | null>(null);

  const wordRevealIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const revealStartedRef = useRef(false);

  const handleLocationUpdate = useCallback(
    (coords: { lng: number; lat: number }) => {
      setUserLocation(coords);
    },
    [],
  );

  async function handleSubmit() {
    if (!userLocation || !inputValue.trim()) return;

    // Reset for new request
    if (wordRevealIntervalRef.current) clearInterval(wordRevealIntervalRef.current);
    revealStartedRef.current = false;
    setDisplayedText("");
    setIsSpeaking(false);
    setNextTrain(null);
    setNextDeparture(null);
    setDirection(null);
    setEtaMinutes(null);
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
      setNextTrain(parseNextTrain(text));
      setNextDeparture(parseDeparture(text));
      setDirection(parseDirection(text));
      setEtaMinutes(parseEtaMinutes(text));

      // Build trip audio element
      const bytes = Uint8Array.from(atob(trip_data.audio), (c) =>
        c.charCodeAt(0),
      );
      const tripAudio = new Audio(
        URL.createObjectURL(new Blob([bytes], { type: "audio/mpeg" })),
      );

      // Word-by-word reveal synced to audio duration
      function startWordReveal(audioDuration: number) {
        if (revealStartedRef.current) return;
        revealStartedRef.current = true;
        const words = text.split(/\s+/).filter((w) => w.length > 0);
        const intervalMs = (audioDuration * 1000) / words.length;
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
      tripAudio.onended = () => setIsSpeaking(false);

      setInputValue("");
    } catch (error) {
      console.error("Error:", error);
      setDisplayedText("Unable to plan your trip. Please try again.");
    } finally {
      setIsLoading(false);
    }
  }

  const panelText = isLoading
    ? "Scanning MTA feeds, sir..."
    : displayedText || jarvisText || "Awaiting your destination, sir.";

  const hasData = !!jarvisText;

  const transitLine = nextTrain
    ? `${nextTrain}${direction ? ` • ${direction.toUpperCase()}` : ""}`
    : "Awaiting route";

  const etaBarWidth =
    etaMinutes != null
      ? `${Math.min((etaMinutes / 60) * 100, 100)}%`
      : "0%";

  return (
    <div className="relative h-screen w-full overflow-hidden bg-[#0a0a0f]">
      {/* Full-screen Mapbox 3D Map */}
      <JarvisMap
        onLocationUpdate={handleLocationUpdate}
        routeData={TEST_ROUTE_DATA}
        isSpeaking={isSpeaking}
      />

      {/* JARVIS Logo - Top Left (Hidden on mobile) */}
      <div className="hidden md:block absolute top-6 left-6 z-10">
        <h1 className="text-xl font-bold tracking-wider text-[#4da6ff]">
          JARVIS
        </h1>
      </div>

      {/* Desktop Right Panel - Floating over map */}
      <div className="hidden md:flex absolute top-4 right-4 bottom-24 w-[280px] z-10 flex-col">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <h2 className="text-sm font-semibold tracking-wider text-[#4da6ff]">
              JARVIS
            </h2>
            <p className="text-xs text-gray-500 tracking-widest">
              SYSTEM ACTIVE
            </p>
          </div>
          <div className="flex items-center gap-3">
            <button className="text-gray-400 hover:text-white transition-colors">
              <User size={20} />
            </button>
            <button className="text-gray-400 hover:text-white transition-colors">
              <Settings size={20} />
            </button>
          </div>
        </div>

        {/* Main Content Area */}
        <div className="flex-1 flex flex-col gap-4 min-h-0">
          {/* JARVIS Response Panel */}
          <div className="relative rounded-xl p-5 bg-[#0a1628]/85 backdrop-blur-xl border border-[#4da6ff]/20 shadow-[0_0_20px_rgba(77,166,255,0.15)]">
            <div
              className="max-h-36 overflow-y-auto pr-1"
              style={{
                scrollbarWidth: "thin",
                scrollbarColor: "rgba(77,166,255,0.2) transparent",
              }}
            >
              <p className="text-sm text-white leading-relaxed">{panelText}</p>
            </div>
          </div>

          {/* NEXT TRANSIT Card */}
          <div className="bg-[#12121a]/80 backdrop-blur-sm rounded-lg p-4 border border-gray-800/50">
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs text-gray-500 tracking-wider">
                NEXT TRANSIT
              </span>
              <div className="flex items-center gap-1.5">
                <span
                  className={`w-1.5 h-1.5 rounded-full ${
                    hasData ? "bg-[#4da6ff] animate-pulse" : "bg-gray-600"
                  }`}
                />
                <span
                  className={`text-xs ${
                    hasData ? "text-[#4da6ff]" : "text-gray-600"
                  }`}
                >
                  LIVE
                </span>
              </div>
            </div>
            <div className="text-3xl font-light text-white mb-1">
              {nextDeparture ?? "--:--"}
            </div>
            <div className="text-xs text-gray-400 tracking-wider">
              {transitLine}
            </div>
          </div>

          {/* ETA Card */}
          <div className="bg-[#12121a]/80 backdrop-blur-sm rounded-lg p-4 border border-gray-800/50">
            <span className="text-xs text-gray-500 tracking-wider">ETA</span>
            <div className="text-3xl font-light text-white mt-1">
              {etaMinutes != null ? `${etaMinutes} min` : "-- min"}
            </div>
            <div className="mt-3 h-1 bg-gray-800 rounded-full overflow-hidden">
              <div
                className="h-full bg-gradient-to-r from-[#4da6ff] to-[#2d7dd2] rounded-full transition-all duration-1000"
                style={{ width: etaBarWidth }}
              />
            </div>
          </div>

          {/* Overview Section */}
          <div className="flex items-center gap-2 mt-2">
            <div className="w-2 h-2 rounded-full bg-[#4da6ff]" />
            <span className="text-sm text-[#4da6ff] tracking-wider">
              OVERVIEW
            </span>
            <div className="ml-auto w-0.5 h-4 bg-[#4da6ff]" />
          </div>
        </div>
      </div>

      {/* Desktop AI Core Alpha - Bottom Right */}
      <div className="hidden md:flex absolute bottom-8 right-6 z-10 items-center gap-3">
        <div className="w-10 h-10 rounded-xl bg-[#12121a]/80 backdrop-blur-sm border border-gray-800/50 flex items-center justify-center">
          <Zap className="text-[#4da6ff]" size={18} />
        </div>
        <div>
          <div className="text-xs font-medium text-white tracking-wider">
            AI CORE ALPHA
          </div>
          <div className="text-xs text-gray-500">
            Latency: {latencyMs != null ? `${latencyMs}ms` : "—"}
          </div>
        </div>
      </div>

      {/* Mobile Bottom Drawer */}
      <div
        className={`md:hidden fixed inset-x-0 bottom-0 z-20 transition-transform duration-300 ease-out ${
          drawerOpen ? "translate-y-0" : "translate-y-[calc(100%-60px)]"
        }`}
      >
        {/* Drawer Handle */}
        <button
          onClick={() => setDrawerOpen(!drawerOpen)}
          className="absolute -top-5 left-1/2 -translate-x-1/2 w-12 h-5 bg-[#0a1628] rounded-t-lg border-t border-x border-[#4da6ff]/20 flex items-center justify-center"
        >
          {drawerOpen ? (
            <ChevronDown className="text-[#4da6ff]" size={16} />
          ) : (
            <ChevronUp className="text-[#4da6ff]" size={16} />
          )}
        </button>

        {/* Drawer Content */}
        <div className="bg-[#0a1628]/95 backdrop-blur-xl border-t border-[#4da6ff]/20 shadow-[0_-10px_40px_rgba(77,166,255,0.1)] rounded-t-2xl max-h-[70vh] overflow-hidden flex flex-col">
          {/* Header */}
          <div className="flex items-center justify-between px-5 py-4 border-b border-gray-800/50">
            <div>
              <h2 className="text-sm font-semibold tracking-wider text-[#4da6ff]">
                JARVIS
              </h2>
              <p className="text-xs text-gray-500 tracking-widest">
                SYSTEM ACTIVE
              </p>
            </div>
            <div className="flex items-center gap-3">
              <button className="text-gray-400 hover:text-white transition-colors">
                <User size={20} />
              </button>
              <button className="text-gray-400 hover:text-white transition-colors">
                <Settings size={20} />
              </button>
            </div>
          </div>

          {/* Scrollable Content */}
          <div className="flex-1 overflow-y-auto p-4 space-y-4">
            {/* JARVIS Response Panel */}
            <div className="relative rounded-xl p-4 bg-[#0d1f38]/90 border border-[#4da6ff]/20 shadow-[0_0_15px_rgba(77,166,255,0.1)]">
              <div
                className="max-h-28 overflow-y-auto pr-1"
                style={{
                  scrollbarWidth: "thin",
                  scrollbarColor: "rgba(77,166,255,0.2) transparent",
                }}
              >
                <p className="text-sm text-white leading-relaxed">{panelText}</p>
              </div>
            </div>

            {/* NEXT TRANSIT Card */}
            <div className="bg-[#12121a]/90 rounded-lg p-4 border border-gray-800/50">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs text-gray-500 tracking-wider">
                  NEXT TRANSIT
                </span>
                <div className="flex items-center gap-1.5">
                  <span
                    className={`w-1.5 h-1.5 rounded-full ${
                      hasData ? "bg-[#4da6ff] animate-pulse" : "bg-gray-600"
                    }`}
                  />
                  <span
                    className={`text-xs ${
                      hasData ? "text-[#4da6ff]" : "text-gray-600"
                    }`}
                  >
                    LIVE
                  </span>
                </div>
              </div>
              <div className="text-3xl font-light text-white mb-1">
                {nextDeparture ?? "--:--"}
              </div>
              <div className="text-xs text-gray-400 tracking-wider">
                {transitLine}
              </div>
            </div>

            {/* ETA Card */}
            <div className="bg-[#12121a]/90 rounded-lg p-4 border border-gray-800/50">
              <span className="text-xs text-gray-500 tracking-wider">ETA</span>
              <div className="text-3xl font-light text-white mt-1">
                {etaMinutes != null ? `${etaMinutes} min` : "-- min"}
              </div>
              <div className="mt-3 h-1 bg-gray-800 rounded-full overflow-hidden">
                <div
                  className="h-full bg-gradient-to-r from-[#4da6ff] to-[#2d7dd2] rounded-full transition-all duration-1000"
                  style={{ width: etaBarWidth }}
                />
              </div>
            </div>

            {/* Overview Section */}
            <div className="flex items-center gap-2">
              <div className="w-2 h-2 rounded-full bg-[#4da6ff]" />
              <span className="text-sm text-[#4da6ff] tracking-wider">
                OVERVIEW
              </span>
              <div className="ml-auto w-0.5 h-4 bg-[#4da6ff]" />
            </div>

            {/* AI Core Alpha - Inside Drawer on Mobile */}
            <div className="flex items-center gap-3 pt-2 border-t border-gray-800/50">
              <div className="w-10 h-10 rounded-xl bg-[#12121a] border border-gray-800/50 flex items-center justify-center">
                <Zap className="text-[#4da6ff]" size={18} />
              </div>
              <div>
                <div className="text-xs font-medium text-white tracking-wider">
                  AI CORE ALPHA
                </div>
                <div className="text-xs text-gray-500">
                  Latency: {latencyMs != null ? `${latencyMs}ms` : "—"}
                </div>
              </div>
            </div>
          </div>

          {/* Mobile Input Bar - Inside Drawer */}
          <div className="p-4 border-t border-gray-800/50">
            <div className="flex items-center gap-3 bg-[#12121a] rounded-full px-4 py-3 border border-gray-800/50">
              <AudioLines className="text-[#4da6ff] shrink-0" size={20} />
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
                className="w-9 h-9 rounded-lg bg-[#4da6ff] flex items-center justify-center hover:bg-[#3d96ef] transition-colors disabled:opacity-50"
              >
                <ArrowRight className="text-white" size={18} />
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* Desktop Input Bar - Bottom Center */}
      <div className="hidden md:block absolute bottom-8 left-1/2 -translate-x-1/2 z-10 w-full max-w-xl px-4">
        <div className="flex items-center gap-3 bg-[#12121a]/90 backdrop-blur-md rounded-full px-5 py-3 border border-gray-800/50">
          <AudioLines className="text-[#4da6ff] shrink-0" size={20} />
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
            className="w-9 h-9 rounded-lg bg-[#4da6ff] flex items-center justify-center hover:bg-[#3d96ef] transition-colors disabled:opacity-50"
          >
            <ArrowRight className="text-white" size={18} />
          </button>
        </div>
      </div>
    </div>
  );
}
