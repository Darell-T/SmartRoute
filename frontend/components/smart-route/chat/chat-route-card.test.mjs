import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { ChatMessage } from "./chat-message.tsx";
import { Sources } from "../../prompt-kit/source.tsx";
import { ChatRouteCardList } from "./chat-route-card.tsx";
import { RecommendedItineraryCard, ItineraryCardSkeleton } from "./recommended-itinerary-card.tsx";

const CHAT_CSS_SOURCE = fs.readFileSync(
  fileURLToPath(
    new URL("../../../app/styles/smart-route-chat.css", import.meta.url),
  ),
  "utf8",
);

const itineraryCard = {
  card_id: "recommended",
  turn_id: "t1",
  role: "recommended",
  origin: { label: "Your location", lat: 40.7, lng: -73.9 },
  destination: { label: "Costco", lat: 40.6, lng: -74 },
  summary: { eta_minutes: 34, transfers: 0, lines: ["A"], reason: "Server reason" },
  route: [],
  alerts: [],
  itinerary: {
    itinerary_id: "it_1",
    total_duration_seconds: 2040,
    transfer_count: 0,
    arrival_at: "2026-07-16T15:45:00-04:00",
    total_dwell_seconds: 0,
    legs: [
      {
        mode: "WALK",
        walk_seconds: 240,
        board: { label: "Your location" },
        alight: { label: "A station" },
      },
      {
        mode: "SUBWAY",
        ride_seconds: 1560,
        service_id: "A",
        board: { label: "A station" },
        alight: { label: "Costco" },
        stop_count: 8,
      },
    ],
  },
};

function renderCard(card = itineraryCard, extra = {}) {
  return renderToStaticMarkup(
    createElement(RecommendedItineraryCard, { card, ...extra }),
  );
}

test("invalid canonical itinerary renders an unavailable card", () => {
  const html = renderCard({
    ...itineraryCard,
    itinerary: { total_duration_seconds: "bad" },
  });
  assert.match(html, /sr-itinerary-card--invalid/);
  assert.match(html, /unavailable|not available|could not/i);
});

test("itinerary skeleton keeps a quiet loading shell", () => {
  const html = renderToStaticMarkup(createElement(ItineraryCardSkeleton));
  assert.match(html, /sr-itinerary-card--skeleton/);
});

test("recommendation card keeps transit details collapsed by default", () => {
  const html = renderCard();
  assert.match(html, /data-expanded="false"/);
  assert.match(html, /class="sr-itinerary-card/);
  assert.doesNotMatch(html, /id="[^"]+-stops"/);
});

test("recommendation card keeps the route hierarchy compact", () => {
  const html = renderCard();
  assert.match(html, /sr-itinerary-card__summary/);
  assert.match(html, /34 min/);
  assert.doesNotMatch(html, /faArrowRightArrowLeft/);
  assert.match(html, /sr-itinerary-card__walk-duration|Walk/);
  assert.match(
    CHAT_CSS_SOURCE,
    /\.sr-chat-tab \.sr-itinerary-card__duration-value\s*\{[\s\S]*?font-size:\s*20px;[\s\S]*?font-weight:\s*600;[\s\S]*?line-height:\s*24px;/,
  );
  assert.match(
    CHAT_CSS_SOURCE,
    /\.sr-itinerary-card__leg,[\s\S]*?grid-template-columns:\s*24px minmax\(0, 1fr\) auto;[\s\S]*?border-top:\s*0;/,
  );
});

test("recommendation card uses the same quiet shell as arrivals", () => {
  const routeShell = CHAT_CSS_SOURCE.match(
    /\.sr-chat-tab \.sr-itinerary-card\s*\{([^}]*)\}/,
  )?.[1];
  const arrivalShell = CHAT_CSS_SOURCE.match(
    /\.sr-chat-arrivals-card\s*\{([^}]*)\}/,
  )?.[1];

  assert.ok(routeShell);
  assert.ok(arrivalShell);
  assert.match(routeShell, /gap:\s*10px/);
  assert.match(routeShell, /border-radius:\s*16px/);
  assert.match(routeShell, /border:\s*1px solid var\(--sr-chat-hairline\)/);
  assert.match(routeShell, /background:\s*var\(--sr-chat-surface\)/);
  assert.match(routeShell, /box-shadow:\s*var\(--sr-chat-raised-shadow\)/);
  assert.match(arrivalShell, /gap:\s*10px/);
  assert.match(arrivalShell, /border-radius:\s*16px/);
  assert.match(arrivalShell, /border:\s*1px solid var\(--sr-chat-hairline\)/);
  assert.match(arrivalShell, /background:\s*var\(--sr-chat-surface\)/);
  assert.match(arrivalShell, /box-shadow:\s*var\(--sr-chat-raised-shadow\)/);
  const html = renderCard();
  assert.doesNotMatch(html, /BorderBeam|border-beam/);
  assert.doesNotMatch(routeShell, /18px 44px|inset/);
});

test("recommendation card uses restrained three-level typography", () => {
  assert.match(
    CHAT_CSS_SOURCE,
    /--sr-itinerary-primary:[\s\S]*?--sr-itinerary-secondary:[\s\S]*?--sr-itinerary-tertiary:/,
  );
  assert.match(
    CHAT_CSS_SOURCE,
    /\.sr-chat-tab \.sr-itinerary-card__arrive\s*\{[\s\S]*?font-size:\s*12px;[\s\S]*?font-weight:\s*400;[\s\S]*?line-height:\s*16px;/,
  );
  assert.match(
    CHAT_CSS_SOURCE,
    /\.sr-itinerary-card__leg-heading,[\s\S]*?font-size:\s*14px;[\s\S]*?font-weight:\s*550;[\s\S]*?line-height:\s*19px;/,
  );
  assert.match(
    CHAT_CSS_SOURCE,
    /\.sr-itinerary-card__station\s*\{[\s\S]*?font-size:\s*13px;[\s\S]*?font-weight:\s*500;[\s\S]*?line-height:\s*18px;/,
  );
  assert.match(
    CHAT_CSS_SOURCE,
    /\.sr-itinerary-card__disclosure,[\s\S]*?font-size:\s*12px;[\s\S]*?font-weight:\s*400;[\s\S]*?line-height:\s*17px;/,
  );
});

test("recommendation card preserves total duration and route-colored chains", () => {
  const html = renderCard();
  assert.match(html, />34 min</);
  assert.match(html, /Arrive around 3:45 PM/);
  assert.match(
    CHAT_CSS_SOURCE,
    /\.sr-itinerary-card__chain-marker--start,[\s\S]*?background: var\(--sr-route-color\)/,
  );
  assert.doesNotMatch(
    CHAT_CSS_SOURCE,
    /\.sr-itinerary-card__chain-marker--(?:start|end)::after/,
  );
});

test("chat card omits the redundant recommendation badge without changing recommendation data", () => {
  const html = renderCard(itineraryCard, { isSelected: true });
  assert.doesNotMatch(html, /sr-itinerary-card__badge/);
  assert.match(html, /data-selected="true"/);
});

test("bus legs use a compact bus glyph, plain route text, and the shared chain", () => {
  const html = renderCard({
    ...itineraryCard,
    itinerary: {
      ...itineraryCard.itinerary,
      legs: [
        {
          mode: "BUS",
          ride_seconds: 600,
          service_id: "B44",
          board: { label: "Atlantic Av" },
          alight: { label: "Costco" },
        },
      ],
    },
  });
  assert.match(html, /sr-itinerary-card__bus-glyph/);
  assert.match(html, /sr-itinerary-card__bus-route/);
  assert.match(html, /B44/);
  assert.doesNotMatch(html, /TrainBullet/);
});

test("Open on map remains a direct keyboard-accessible action", () => {
  const html = renderCard(itineraryCard, { onSelect: () => {} });
  assert.match(html, /aria-label="Open on map"/);
  assert.match(html, /type="button"/);
  assert.doesNotMatch(html, /disabled=""/);
});

test("source attribution uses PromptKit-style favicon triggers", () => {
  const markup = renderToStaticMarkup(Sources({
    sources: [
      { title: "Google Maps", url: "https://www.google.com/maps" },
      { title: "Damn Lines", url: "https://damnlines.com/camera/l-industrie" },
    ],
  }));

  assert.match(markup, /<div class="sr-chat-sources" aria-label="Sources">/);
  assert.match(markup, />Source<\/span>/);
  assert.match(markup, /href="https:\/\/damnlines\.com\/camera\/l-industrie"/);
  assert.match(markup, /title="Damn Lines"/);
  assert.match(markup, /www\.google\.com\/s2\/favicons\?sz=64&amp;domain_url=https%3A%2F%2Fdamnlines\.com%2Fcamera%2Fl-industrie/);
  assert.match(markup, /class="sr-chat-sources__favicon"/);
  assert.match(markup, /class="sr-chat-sources__google-attribution"/);
  assert.match(markup, /Place data by/);
  assert.match(markup, /href="https:\/\/www\.google\.com\/maps"/);
  assert.equal(markup.match(/class="sr-chat-sources__favicon"/g)?.length, 1);
  assert.match(markup, /target="_blank"/);
  assert.match(markup, /rel="noopener noreferrer"/);
  assert.match(CHAT_CSS_SOURCE, /\.sr-chat-transit-action\s*\{/);
});

test("settled assistant route cards render without prose", () => {
  const html = renderToStaticMarkup(createElement(ChatMessage, {
    theme: "dark",
    turn: { role: "assistant", turnId: "t1", text: "", reasoning: "", toolChips: [], routeCards: [itineraryCard], isStreaming: false },
  }));
  assert.match(html, /34 min/);
  assert.match(html, /Open on map/);
});


test("chat renders only the recommendation without mutating map alternatives", () => {
  const cards = [{
    ...itineraryCard,
    card_id: "alternative",
    role: "alternative",
    itinerary: { ...itineraryCard.itinerary, total_duration_seconds: 3600 },
  }, itineraryCard];
  const before = structuredClone(cards);
  const html = renderToStaticMarkup(createElement(ChatRouteCardList, { cards }));
  assert.match(html, /34 min/);
  assert.doesNotMatch(html, /60 min/);
  assert.deepEqual(cards, before);
});

test("chat does not promote an alternative when no recommendation exists", () => {
  const cards = [{ ...itineraryCard, card_id: "alternative", role: "alternative" }];
  assert.equal(renderToStaticMarkup(createElement(ChatRouteCardList, { cards })), "");
});
