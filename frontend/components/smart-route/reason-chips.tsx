"use client";

import { AlertTriangle, Check, ChevronDown, Dot } from "lucide-react";
import type { ReasonChip } from "@/lib/smart-route";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";

interface Props {
  chips: ReasonChip[];
  reasonLong: string;
  accent: string;
  expanded: boolean;
  onToggle: () => void;
}

export function ReasonChips({
  chips,
  reasonLong,
  accent: _accent,
  expanded,
  onToggle,
}: Props) {
  if (chips.length === 0) return null;

  return (
    <Card
      variant="elevated"
      className="bg-[#10141d] animate-[srCardIn_280ms_ease-out]"
    >
      <Collapsible open={expanded} onOpenChange={onToggle}>
        <CardHeader className="flex-row items-center justify-between px-4 py-3 space-y-0">
          <span
            className="text-white/90 font-semibold"
            style={{ fontFamily: "var(--font-geist), sans-serif", fontSize: 11 }}
          >
            Why this route won
          </span>
          <CollapsibleTrigger asChild>
            <button
              type="button"
              className={cn(
                "flex items-center gap-1 text-white/55 cursor-pointer transition-colors duration-150 hover:text-white/80",
                "outline-none focus-visible:ring-2 focus-visible:ring-[#d4a7ff]/60 rounded-sm",
              )}
              style={{ fontFamily: "var(--font-geist), sans-serif", fontSize: 11 }}
            >
              {expanded ? "Less" : "Full reasoning"}
              <ChevronDown
                size={12}
                className={cn(
                  "transition-transform duration-200",
                  expanded && "rotate-180",
                )}
              />
            </button>
          </CollapsibleTrigger>
        </CardHeader>

        <CardContent className="px-4 pb-4 pt-0">
          <div className="flex flex-col gap-1.5">
            {chips.map((chip, index) => (
              <ChipRow key={index} chip={chip} />
            ))}
          </div>

          <CollapsibleContent>
            <div
              className="mt-3 rounded-lg border border-white/[0.05] bg-black/25 p-3 leading-[1.55] text-white/78"
              style={{ fontFamily: "var(--font-geist), sans-serif", fontSize: 12.5 }}
            >
              {reasonLong}
            </div>
          </CollapsibleContent>
        </CardContent>
      </Collapsible>
    </Card>
  );
}

function ChipRow({ chip }: { chip: ReasonChip }) {
  const color =
    chip.kind === "pro"
      ? "#9ccfbf"
      : chip.kind === "note"
        ? "#f0b04a"
        : "#ff8a8a";
  const Icon =
    chip.kind === "pro" ? Check : chip.kind === "note" ? Dot : AlertTriangle;

  return (
    <div className="flex items-center gap-2.5 rounded-lg border border-white/[0.05] bg-white/[0.02] px-2.5 py-1.5">
      <span
        className="flex shrink-0 items-center justify-center rounded-full font-bold"
        style={{
          width: 18,
          height: 18,
          borderRadius: 9,
          background: `${color}1c`,
          border: `1px solid ${color}55`,
          color,
        }}
      >
        <Icon size={chip.kind === "note" ? 14 : 10} strokeWidth={2.2} />
      </span>
      <span
        className="flex-1 text-white/88 leading-[1.35]"
        style={{ fontFamily: "var(--font-geist), sans-serif", fontSize: 12.5 }}
      >
        {chip.text}
      </span>
      <span
        className="text-white/40 whitespace-nowrap tabular-nums"
        style={{
          fontFamily: "var(--font-jetbrains-mono), monospace",
          fontSize: 9.5,
          letterSpacing: "0.02em",
        }}
      >
        {chip.source}
      </span>
    </div>
  );
}
