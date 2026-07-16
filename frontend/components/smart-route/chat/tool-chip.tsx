"use client";

/* ════════════════════════════════════════════════════════════════════════
   SmartRoute chat — tool progress chip

   Renders one `tool_start`/`tool_end` pair as a small inline pill: a
   spinner + label while running, a check/✕ + faded label once resolved.
   Quiet and monochrome by design — the plan calls tool progress "inline
   status chips," not another surface competing with the route cards for
   attention.
   ════════════════════════════════════════════════════════════════════════ */

import { Check, X } from "lucide-react";
import type { ToolChip as ToolChipData } from "@/lib/use-agent-chat";

export function ToolChip({ chip }: { chip: ToolChipData }) {
  return (
    <span className="sr-chat-tool-chip" data-status={chip.status}>
      {chip.status === "running" ? (
        <span className="sr-chat-tool-chip__spinner" aria-hidden="true" />
      ) : chip.status === "ok" ? (
        <Check size={12} strokeWidth={2.5} aria-hidden="true" />
      ) : (
        <X size={12} strokeWidth={2.5} aria-hidden="true" />
      )}
      <span className="sr-chat-tool-chip__label">{chip.label}</span>
    </span>
  );
}

export function ToolChipRow({ chips }: { chips: ToolChipData[] }) {
  if (chips.length === 0) return null;
  return (
    <div className="sr-chat-tool-chips" aria-label="Tool progress">
      {chips.map((chip) => (
        <ToolChip key={chip.id} chip={chip} />
      ))}
    </div>
  );
}
