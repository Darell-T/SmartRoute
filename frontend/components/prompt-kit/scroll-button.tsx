/* Vendored from prompt-kit (ibelick/prompt-kit), source:
   https://github.com/ibelick/prompt-kit/blob/main/components/prompt-kit/scroll-button.tsx
   License: MIT (https://github.com/ibelick/prompt-kit/blob/main/LICENSE.md)

   Local tweaks:
   - `variant`/`size` are typed against this repo's trimmed
     `components/ui/button.tsx` (`ButtonVariant`/`ButtonSize`) instead of
     upstream's `class-variance-authority` `VariantProps`, since cva was
     dropped from the vendored Button (see that file's header). */

"use client";

import { Button, type ButtonSize, type ButtonVariant } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { ChevronDown } from "lucide-react";
import { useStickToBottomContext } from "use-stick-to-bottom";

export type ScrollButtonProps = {
  className?: string;
  variant?: ButtonVariant;
  size?: ButtonSize;
} & React.ButtonHTMLAttributes<HTMLButtonElement>;

function ScrollButton({ className, variant = "outline", size = "sm", ...props }: ScrollButtonProps) {
  const { isAtBottom, scrollToBottom } = useStickToBottomContext();

  return (
    <Button
      variant={variant}
      size={size}
      className={cn(
        "h-10 w-10 rounded-full transition-all duration-150 ease-out",
        !isAtBottom
          ? "translate-y-0 scale-100 opacity-100"
          : "pointer-events-none translate-y-4 scale-95 opacity-0",
        className,
      )}
      onClick={() => scrollToBottom()}
      aria-label="Scroll to latest messages"
      {...props}
    >
      <ChevronDown className="h-5 w-5" aria-hidden="true" />
    </Button>
  );
}

export { ScrollButton };
