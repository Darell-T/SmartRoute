/* Vendored from prompt-kit (ibelick/prompt-kit), source:
   https://github.com/ibelick/prompt-kit/blob/main/components/prompt-kit/message.tsx
   License: MIT (https://github.com/ibelick/prompt-kit/blob/main/LICENSE.md)

   Local tweaks:
   - Dropped `MessageAvatar` (Avatar primitive) and the `markdown` rendering
     path (`Markdown`/Streamdown): the chat design spec renders bare prose
     with no avatars and no markdown formatting in v1 ("MTA bullet tokens in
     prose are NOT rendered as icons in v1; plain text"), so neither
     dependency was needed and neither is vendored.
   - `MessageContent` no longer hardcodes Tailwind's `bg-secondary` — the
     caller supplies the SmartRoute bubble/prose classes instead (see
     chat-message.tsx), keeping this file themeable via plain classNames. */

import * as React from "react";

import { cn } from "@/lib/utils";

export type MessageProps = {
  children: React.ReactNode;
  className?: string;
} & React.HTMLAttributes<HTMLDivElement>;

const Message = ({ children, className, ...props }: MessageProps) => (
  <div className={cn("flex gap-3", className)} {...props}>
    {children}
  </div>
);

export type MessageContentProps = {
  children: React.ReactNode;
  className?: string;
} & React.HTMLAttributes<HTMLDivElement>;

const MessageContent = ({ children, className, ...props }: MessageContentProps) => (
  <div className={cn("break-words whitespace-normal", className)} {...props}>
    {children}
  </div>
);

export { Message, MessageContent };
