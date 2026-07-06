"use client";

/* Vendored from Vercel AI Elements ("reasoning"), adapted for SmartRoute:
   markdown/Streamdown rendering is dropped (rail copy is plain product
   text), visual styling comes from the rail stylesheet via classNames
   rather than Tailwind design tokens, and duration is a plain prop instead
   of an internally-clocked measurement. The API shape — Reasoning /
   ReasoningTrigger / ReasoningContent with isStreaming, duration, and
   controlled/uncontrolled open — matches upstream so a future package
   install is a drop-in swap. */

import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ComponentProps,
  type ReactNode,
} from "react";
import { ChevronDown } from "lucide-react";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";

const AUTO_CLOSE_DELAY_MS = 1000;

type ReasoningContextValue = {
  isStreaming: boolean;
  isOpen: boolean;
  duration?: number;
};

const ReasoningContext = createContext<ReasoningContextValue | null>(null);

function useReasoning() {
  const context = useContext(ReasoningContext);
  if (!context) {
    throw new Error("Reasoning components must be used within <Reasoning>");
  }
  return context;
}

export type ReasoningProps = ComponentProps<typeof Collapsible> & {
  isStreaming?: boolean;
  duration?: number;
};

export function Reasoning({
  className,
  isStreaming = false,
  open,
  defaultOpen = true,
  onOpenChange,
  duration,
  children,
  ...props
}: ReasoningProps) {
  const [uncontrolledOpen, setUncontrolledOpen] = useState(defaultOpen);
  const [hasStreamed, setHasStreamed] = useState(isStreaming);
  const isOpen = open ?? uncontrolledOpen;

  function setIsOpen(next: boolean) {
    if (open === undefined) setUncontrolledOpen(next);
    onOpenChange?.(next);
  }

  // React to isStreaming edges during render (documented derived-state
  // pattern): remember that a stream happened and, while uncontrolled,
  // re-open when a new stream starts.
  const [prevStreaming, setPrevStreaming] = useState(isStreaming);
  if (prevStreaming !== isStreaming) {
    setPrevStreaming(isStreaming);
    if (isStreaming) {
      setHasStreamed(true);
      if (open === undefined) setUncontrolledOpen(true);
    }
  }

  // While uncontrolled, collapse shortly after the stream ends. Instances
  // that never streamed don't auto-close.
  useEffect(() => {
    if (open !== undefined || isStreaming || !hasStreamed) return;
    const timer = window.setTimeout(
      () => setUncontrolledOpen(false),
      AUTO_CLOSE_DELAY_MS,
    );
    return () => window.clearTimeout(timer);
  }, [isStreaming, open, hasStreamed]);

  return (
    <ReasoningContext.Provider value={{ isStreaming, isOpen, duration }}>
      <Collapsible
        className={cn(className)}
        data-streaming={isStreaming ? "true" : "false"}
        open={isOpen}
        onOpenChange={setIsOpen}
        {...props}
      >
        {children}
      </Collapsible>
    </ReasoningContext.Provider>
  );
}

export type ReasoningTriggerProps = ComponentProps<
  typeof CollapsibleTrigger
> & {
  children?: ReactNode;
};

export function ReasoningTrigger({
  className,
  children,
  ...props
}: ReasoningTriggerProps) {
  const { isStreaming, duration } = useReasoning();
  const fallbackLabel = isStreaming
    ? "Thinking..."
    : duration && duration > 0
      ? `Thought for ${duration}s`
      : "Done";

  return (
    <CollapsibleTrigger className={cn(className)} {...props}>
      <span>{children ?? fallbackLabel}</span>
      <ChevronDown size={16} strokeWidth={1.8} aria-hidden="true" />
    </CollapsibleTrigger>
  );
}

export type ReasoningContentProps = ComponentProps<typeof CollapsibleContent>;

export function ReasoningContent({
  className,
  children,
  ...props
}: ReasoningContentProps) {
  return (
    <CollapsibleContent className={cn(className)} {...props}>
      <div aria-live="polite">{children}</div>
    </CollapsibleContent>
  );
}
