/* Adapted from PromptKit's Source component:
   https://www.prompt-kit.com/docs/source
   License: MIT (https://github.com/ibelick/prompt-kit/blob/main/LICENSE.md)

   SmartRoute keeps this version intentionally small. The backend supplies
   already validated source records, so the component only owns attribution
   links and their favicons. */

"use client";

import Image from "next/image";
import { createContext, useContext, type ReactNode } from "react";

import type { AgentSource } from "@/lib/agent-chat-stream";

const GOOGLE_MAPS_URL = "https://www.google.com/maps";

interface SourceContextValue {
  domain: string;
  href: string;
}

const SourceContext = createContext<SourceContextValue | null>(null);

function useSourceContext(): SourceContextValue {
  const source = useContext(SourceContext);
  if (!source) throw new Error("SourceTrigger must be used inside Source");
  return source;
}

interface SourceProps {
  children: ReactNode;
  href: string;
}

export function Source({ children, href }: SourceProps) {
  let domain = href;
  try {
    domain = new URL(href).hostname.replace(/^www\./, "");
  } catch {
    // The event boundary rejects malformed URLs. Keep a readable fallback for
    // direct component use in tests and development.
  }

  return (
    <SourceContext.Provider value={{ domain, href }}>
      {children}
    </SourceContext.Provider>
  );
}

interface SourceTriggerProps {
  label?: string;
  showFavicon?: boolean;
}

export function SourceTrigger({ label, showFavicon = false }: SourceTriggerProps) {
  const { domain, href } = useSourceContext();
  const faviconUrl = `https://www.google.com/s2/favicons?sz=64&domain_url=${encodeURIComponent(href)}`;

  return (
    <a
      className="sr-chat-sources__trigger"
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      title={label}
    >
      {showFavicon ? (
        <Image
          className="sr-chat-sources__favicon"
          src={faviconUrl}
          alt=""
          width={14}
          height={14}
          unoptimized
        />
      ) : null}
      <span>{label ?? domain}</span>
    </a>
  );
}

export interface SourcesProps {
  sources: AgentSource[];
}

export function Sources({ sources }: SourcesProps) {
  if (sources.length === 0) return null;

  const googleMapsSource = sources.find(
    (source) =>
      source.title === "Google Maps" && source.url === GOOGLE_MAPS_URL,
  );
  const linkedSources = sources.filter((source) => source !== googleMapsSource);

  return (
    <div className="sr-chat-sources" aria-label="Sources">
      {linkedSources.length > 0 ? (
        <>
          <span className="sr-chat-sources__label">
            {linkedSources.length === 1 ? "Source" : "Sources"}
          </span>
          <ul className="sr-chat-sources__list">
            {linkedSources.map((source) => (
              <li key={source.url}>
                <Source href={source.url}>
                  <SourceTrigger label={source.title} showFavicon />
                </Source>
              </li>
            ))}
          </ul>
        </>
      ) : null}
      {googleMapsSource ? (
        <span className="sr-chat-sources__google-attribution">
          Place data by{" "}
          <a
            href={GOOGLE_MAPS_URL}
            target="_blank"
            rel="noopener noreferrer"
          >
            Google Maps
          </a>
        </span>
      ) : null}
    </div>
  );
}
