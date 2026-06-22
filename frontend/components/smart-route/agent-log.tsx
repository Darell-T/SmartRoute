"use client";

import { useEffect, useRef } from "react";
import { ChevronDown } from "lucide-react";
import type { AgentLogEntry } from "@/lib/smart-route";
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion";
import { cn } from "@/lib/utils";

interface Props {
  accent: string;
  entries: AgentLogEntry[];
  live: boolean;
}

/**
 * Agent stream log — collapsed by default, accordion-07 pattern.
 * Custom trigger with pulsing status dot + mono timestamp + entry preview.
 */
export function AgentLog({ accent, entries, live }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);

  const lastEntry = entries[entries.length - 1];
  const lastLevelColor =
    lastEntry?.level === "decision"
      ? accent
      : lastEntry?.level === "detect"
        ? "#f0b04a"
        : lastEntry?.level === "reason"
          ? "#9ccfbf"
          : "rgba(255,255,255,0.55)";

  function handleValueChange(val: string) {
    if (val === "stream") {
      // Scroll to bottom after Radix animates open
      requestAnimationFrame(() => {
        if (scrollRef.current) {
          scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
        }
      });
    }
  }

  // Keep scroll at bottom when new entries arrive while open
  useEffect(() => {
    if (scrollRef.current && scrollRef.current.scrollHeight > 0) {
      const el = scrollRef.current;
      const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
      if (atBottom) {
        el.scrollTop = el.scrollHeight;
      }
    }
  }, [entries.length]);

  return (
    <Accordion
      type="single"
      collapsible
      onValueChange={handleValueChange}
      className={cn(
        "rounded-xl overflow-hidden border border-white/[0.06] bg-black/[0.32]",
        "animate-[srCardIn_280ms_ease-out]",
      )}
    >
      <AccordionItem value="stream" className="border-b-0">
        {/* Custom trigger */}
        <AccordionTrigger
          className={cn(
            "flex items-center gap-2 px-3.5 py-2.5 hover:no-underline hover:bg-white/[0.02]",
            "focus-visible:ring-2 focus-visible:ring-[#d4a7ff]/60",
            "[&>svg]:ml-auto [&>svg]:flex-shrink-0",
          )}
        >
          {/* Live pulse dot */}
          <span
            className="size-1.5 rounded-full flex-shrink-0"
            style={{
              background: live ? "#9ccfbf" : "rgba(255,255,255,0.35)",
              boxShadow: live ? "0 0 6px #9ccfbf" : "none",
              animation: live ? "srPulse 1.2s infinite" : undefined,
            }}
          />

          {/* Label */}
          <span
            className="text-white/72 font-medium tracking-[0.18em] uppercase"
            style={{ fontFamily: "var(--font-geist), sans-serif", fontSize: 10.5 }}
          >
            AGENT STREAM
          </span>

          {/* Count */}
          <span
            className="text-white/40 tabular-nums"
            style={{ fontFamily: "var(--font-jetbrains-mono), monospace", fontSize: 10, letterSpacing: "0.08em" }}
          >
            {entries.length} events
          </span>

          {/* Last entry preview (collapsed only) */}
          {lastEntry && (
            <span
              className="truncate max-w-[160px] ml-2 opacity-80"
              style={{
                fontFamily: "var(--font-jetbrains-mono), monospace",
                fontSize: 10.5,
                color: lastLevelColor,
              }}
            >
              {lastEntry.text}
            </span>
          )}

          {/* STREAMING badge */}
          {live && (
            <span
              className="ml-2 flex items-center gap-1.5 text-[#9ccfbf] tracking-[0.12em]"
              style={{ fontFamily: "var(--font-jetbrains-mono), monospace", fontSize: 10 }}
            >
              <span
                className="size-1.5 rounded-full"
                style={{ background: "#9ccfbf", animation: "srPulse 1.2s infinite" }}
              />
              LIVE
            </span>
          )}
        </AccordionTrigger>

        <AccordionContent className="pb-0">
          <div
            ref={scrollRef}
            className="max-h-[180px] overflow-y-auto px-3.5 pb-3 border-t border-white/[0.05]"
            style={{ fontFamily: "var(--font-jetbrains-mono), monospace", fontSize: 11, lineHeight: 1.55 }}
          >
            {entries.map((e, i) => {
              const levelColor =
                e.level === "decision"
                  ? accent
                  : e.level === "detect"
                    ? "#f0b04a"
                    : e.level === "reason"
                      ? "#9ccfbf"
                      : "rgba(255,255,255,0.55)";
              return (
                <div
                  key={i}
                  className="mb-1.5"
                  style={{ opacity: i === entries.length - 1 ? 1 : 0.78 }}
                >
                  <span className="text-white/35 mr-2">[{e.t}]</span>
                  <span className="mr-1.5 tracking-[0.04em]" style={{ color: levelColor }}>
                    {e.level.toUpperCase().padEnd(8, "\u00a0")}
                  </span>
                  <span className="text-white/82">{e.text}</span>
                </div>
              );
            })}

            {/* Typing indicator */}
            {live && (
              <div className="flex items-center gap-1 mt-1" style={{ color: accent }}>
                {[0, 150, 300].map((delay) => (
                  <span
                    key={delay}
                    className="rounded-full"
                    style={{
                      width: 5 - (delay / 150) * 0.75,
                      height: 5 - (delay / 150) * 0.75,
                      background: accent,
                      opacity: 1 - (delay / 300) * 0.6,
                      animation: `srPulse 0.9s infinite ${delay}ms`,
                    }}
                  />
                ))}
              </div>
            )}
          </div>
        </AccordionContent>
      </AccordionItem>
    </Accordion>
  );
}
