/* Vendored from prompt-kit (ibelick/prompt-kit), source:
   https://github.com/ibelick/prompt-kit/blob/main/components/prompt-kit/loader.tsx
   License: MIT (https://github.com/ibelick/prompt-kit/blob/main/LICENSE.md)

   Local tweaks:
   - Trimmed to the single "circular" variant. Upstream ships ~11 variants
     built on arbitrary Tailwind keyframes (`spinner-fade`, `thin-pulse`,
     `pulse-dot`, `wave-bars`, `shimmer`, ...) that are not defined anywhere
     in this project's `globals.css` `@theme`, so importing them wholesale
     would have shipped dead, non-animating CSS classes. `animate-spin` is a
     built-in Tailwind utility, so this variant works with no extra config.
   - Not wired into the v1 chat surface: the vendored AI Elements
     `Reasoning`/`Shimmer` (components/ai-elements/) already cover the
     "agent working" affordance end to end, and tool rows use lucide status
     glyphs directly per the design spec. Kept available as vendored infra
     per the build architecture's component list rather than deleted. */

import { cn } from "@/lib/utils";

export interface LoaderProps {
  size?: "sm" | "md" | "lg";
  className?: string;
}

const SIZE_CLASSES = {
  sm: "size-4",
  md: "size-5",
  lg: "size-6",
} as const;

export function Loader({ size = "md", className }: LoaderProps) {
  return (
    <div
      className={cn(
        "border-primary animate-spin rounded-full border-2 border-t-transparent",
        SIZE_CLASSES[size],
        className,
      )}
    >
      <span className="sr-only">Loading</span>
    </div>
  );
}
