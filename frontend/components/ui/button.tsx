/* Vendored from shadcn/ui (registry: new-york-v4), source:
   https://github.com/shadcn-ui/ui/blob/main/apps/v4/registry/new-york-v4/ui/button.tsx
   License: MIT (https://github.com/shadcn-ui/ui/blob/main/LICENSE.md)

   Local tweaks:
   - Dropped `class-variance-authority` and the Radix `Slot`/`asChild`
     indirection (not in this repo's approved dependency list for this
     feature) in favor of a plain lookup-table `cn()` call. The public
     `variant`/`size` prop API is unchanged from upstream.
   - Only the variants actually used by this feature are kept: `outline`
     (prompt-kit's ScrollButton) and `ghost`/`icon` sizes (chat theme
     toggle). Unused upstream variants (`destructive`, `secondary`, `link`)
     and sizes (`xs`, `icon-xs`, `icon-lg`) were removed rather than carried
     as dead code — add back from upstream if a future consumer needs them. */

import * as React from "react";

import { cn } from "@/lib/utils";

const BUTTON_BASE =
  "inline-flex shrink-0 items-center justify-center gap-2 rounded-md text-sm font-medium whitespace-nowrap transition-all outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4";

const BUTTON_VARIANTS = {
  default: "bg-primary text-primary-foreground hover:bg-primary/90",
  outline:
    "border bg-background shadow-xs hover:bg-accent hover:text-accent-foreground dark:border-input dark:bg-input/30 dark:hover:bg-input/50",
  ghost: "hover:bg-accent hover:text-accent-foreground dark:hover:bg-accent/50",
} as const;

const BUTTON_SIZES = {
  default: "h-9 px-4 py-2 has-[>svg]:px-3",
  sm: "h-8 gap-1.5 rounded-md px-3 has-[>svg]:px-2.5",
  icon: "size-9",
  "icon-sm": "size-8",
} as const;

export type ButtonVariant = keyof typeof BUTTON_VARIANTS;
export type ButtonSize = keyof typeof BUTTON_SIZES;

export interface ButtonProps extends React.ComponentProps<"button"> {
  variant?: ButtonVariant;
  size?: ButtonSize;
}

function Button({
  className,
  variant = "default",
  size = "default",
  ...props
}: ButtonProps) {
  return (
    <button
      data-slot="button"
      data-variant={variant}
      data-size={size}
      className={cn(BUTTON_BASE, BUTTON_VARIANTS[variant], BUTTON_SIZES[size], className)}
      {...props}
    />
  );
}

export { Button };
