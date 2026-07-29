/* Vendored Prompt Kit PromptSuggestion contract:
   https://www.prompt-kit.com/docs/prompt-suggestion
   Normal suggestion mode delegates button behavior to the shared shadcn
   Button primitive. SmartRoute-specific visuals remain in the chat stylesheet. */

import * as React from "react";

import { Button, type ButtonProps } from "@/components/ui/button";
import { cn } from "@/lib/utils";

type PromptSuggestionSize = "default" | "sm" | "lg" | "icon";

export interface PromptSuggestionProps extends Omit<ButtonProps, "size"> {
  size?: PromptSuggestionSize;
  highlight?: string;
}

const SIZE_CLASS: Record<PromptSuggestionSize, string> = {
  default: "h-9 px-4 py-2",
  sm: "h-8 px-3",
  lg: "h-11 px-4",
  icon: "size-9 px-0",
};

export function PromptSuggestion({
  className,
  children,
  highlight,
  size = "lg",
  variant = "outline",
  ...props
}: PromptSuggestionProps) {
  const content =
    highlight && typeof children === "string"
      ? highlightText(children, highlight)
      : children;

  return (
    <Button
      type="button"
      variant={highlight ? "ghost" : variant}
      size="default"
      className={cn(SIZE_CLASS[size], className)}
      {...props}
    >
      {content}
    </Button>
  );
}

function highlightText(value: string, highlight: string): React.ReactNode {
  const query = highlight.trim();
  if (!query) return value;

  const index = value.toLocaleLowerCase().indexOf(query.toLocaleLowerCase());
  if (index < 0) return value;

  return (
    <>
      {value.slice(0, index)}
      <mark>{value.slice(index, index + query.length)}</mark>
      {value.slice(index + query.length)}
    </>
  );
}
