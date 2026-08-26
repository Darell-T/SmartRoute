/* Adapted from PromptKit's Source component:
   https://github.com/ibelick/prompt-kit/blob/main/components/prompt-kit/source.tsx
   License: MIT (https://github.com/ibelick/prompt-kit/blob/main/LICENSE.md)

   SmartRoute keeps this version intentionally small. The backend supplies
   already validated source records, so the component only owns accessible
   disclosure and link rendering. */

import type { AgentSource } from "@/lib/agent-chat-stream";

export interface SourcesProps {
  sources: AgentSource[];
}

export function Sources({ sources }: SourcesProps) {
  if (sources.length === 0) return null;

  return (
    <details className="sr-chat-sources">
      <summary className="sr-chat-sources__summary">
        {sources.length === 1 ? `Source: ${sources[0].title}` : "Sources"}
      </summary>
      <ul className="sr-chat-sources__list">
        {sources.map((source) => (
          <li key={source.url}>
            <a
              className="sr-chat-sources__link"
              href={source.url}
              target="_blank"
              rel="noopener noreferrer"
            >
              {source.title}
            </a>
          </li>
        ))}
      </ul>
    </details>
  );
}
