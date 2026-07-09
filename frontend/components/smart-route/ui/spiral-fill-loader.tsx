"use client";

import { cn } from "@/lib/utils";

type SpiralFillLoaderProps = {
  className?: string;
};

const DOT_BASE =
  "size-[3.5px] rounded-[1.5px] bg-white/[0.22] motion-reduce:animate-none";

const DOT_ANIMATIONS = [
  "animate-spiral-dot-pos-1",
  "animate-spiral-dot-pos-2",
  "animate-spiral-dot-pos-3",
  "animate-spiral-dot-pos-4",
  "animate-spiral-dot-pos-5",
  "animate-spiral-dot-pos-6",
  "animate-spiral-dot-pos-7",
  "animate-spiral-dot-pos-8",
  "animate-spiral-dot-pos-9",
] as const;

export function SpiralFillLoader({ className }: SpiralFillLoaderProps) {
  return (
    <span
      aria-hidden="true"
      className={cn("grid grid-cols-3 grid-rows-3 gap-[3px]", className)}
    >
      {DOT_ANIMATIONS.map((animation) => (
        <span key={animation} className={cn(DOT_BASE, animation)} />
      ))}
    </span>
  );
}
