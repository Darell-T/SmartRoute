import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { ChatComposer } from "./chat-composer.tsx";
import { ChatMessage } from "./chat-message.tsx";
import { ChatPanel } from "./chat-panel.tsx";
import { ChatSuggestions, ChatWelcome } from "./chat-welcome.tsx";
import { ChatWorkingPanel } from "./chat-working-panel.tsx";
import { HomeNearYou } from "./home-near-you.tsx";
import { ResponseModeMenu } from "./response-mode-menu.tsx";
import { ChatRouteCardList, recommendedCardsForChat } from "./chat-route-card.tsx";
import { WalkingIcon } from "./walking-icon.tsx";

const CHAT_STYLE_SOURCE = fs.readFileSync(
  new URL("../../../app/styles/smart-route-chat.css", import.meta.url),
  "utf8",
);

const nearby = {
  locationState: "precise_nyc",
  locationLabel: "Near you",
  locationNotice: null,
  stationName: "Jay St-MetroTech",
  arrivals: [
    {
      id: "q-coney",
      routeId: "Q",
      destination: "Coney Island",
      minutes: [4],
    },
  ],
  arrivalsState: "ready",
  condition: { state: "clear", label: "No active service changes nearby" },
  issue: null,
};

const idleChat = {
  messages: [],
  selectedCardId: null,
  isStreaming: false,
  send() {},
  cancel() {},
  selectCard() {},
  retryLast() {},
  dismissError() {},
};

test("composer exposes Auto, Quick, send, and voice without an attachment action", () => {
  const html = renderToStaticMarkup(
    createElement(ChatComposer, {
      value: "Plan a trip to JFK",
      onValueChange() {},
      presentationMode: "auto",
      onPresentationModeChange() {},
      theme: "dark",
      onSend() {},
      onCancel() {},
      isStreaming: false,
    }),
  );
  assert.match(html, /aria-label="Message SmartRoute"/);
  assert.match(html, /Ask SmartRoute/);
  assert.match(html, /Response style: Auto/);
  assert.match(html, /aria-label="Send message"/);
  assert.doesNotMatch(html, /attach/i);
  const streaming = renderToStaticMarkup(
    createElement(ChatComposer, {
      value: "",
      onValueChange() {},
      presentationMode: "quick",
      onPresentationModeChange() {},
      theme: "dark",
      onSend() {},
      onCancel() {},
      isStreaming: true,
    }),
  );
  assert.match(streaming, /Response style: Quick/);
  assert.match(streaming, /aria-label="Stop"/);
});

test("response mode menu lists Auto and Quick as radio items", () => {
  const html = renderToStaticMarkup(
    createElement(ResponseModeMenu, {
      value: "auto",
      theme: "dark",
      onValueChange() {},
    }),
  );
  assert.match(html, /aria-haspopup="menu"/);
  assert.match(html, /aria-expanded="false"/);
  assert.match(html, /Response style: Auto/);
  assert.match(html, />Auto</);
});

test("empty chat leads with nearby proof and compact suggestions", () => {
  const welcome = renderToStaticMarkup(
    createElement(ChatWelcome, { nearby, onOpenLiveMap() {} }),
  );
  assert.match(welcome, /Where to\?/);
  assert.match(welcome, />Near you</);
  assert.match(welcome, /Jay St-MetroTech/);
  assert.match(welcome, /Coney Island/);
  const suggestions = renderToStaticMarkup(
    createElement(ChatSuggestions, {
      suggestions: [
        { label: "JFK · fewer transfers", query: "Get me to JFK with fewer transfers." },
        { label: "MSG · avoid crowds", query: "Get me to Madison Square Garden and avoid crowds." },
        { label: "Ramen · best route now", query: "Find a good ramen spot and route me there by subway." },
      ],
      onSelectSuggestion() {},
    }),
  );
  assert.match(suggestions, /aria-label="Trip suggestions"/);
  assert.match(suggestions, /JFK · fewer transfers/);
  assert.match(suggestions, /MSG · avoid crowds/);
  assert.match(suggestions, /Ramen · best route now/);
  const panel = renderToStaticMarkup(
    createElement(ChatPanel, {
      chat: idleChat,
      theme: "dark",
      nearby,
      onOpenLiveMap() {},
    }),
  );
  assert.match(panel, /Where to\?/);
  assert.match(panel, /JFK · fewer transfers/);
  assert.match(panel, /data-empty="true"/);
});

test("home nearby unavailable and outside-area states keep passenger copy", () => {
  const unavailable = renderToStaticMarkup(
    createElement(HomeNearYou, {
      model: {
        ...nearby,
        arrivals: [],
        arrivalsState: "unavailable",
        condition: { state: "unavailable", label: "Service status unavailable" },
      },
      onOpenLiveMap() {},
    }),
  );
  assert.match(unavailable, /Nearby arrivals unavailable/);
  const outside = renderToStaticMarkup(
    createElement(HomeNearYou, {
      model: {
        ...nearby,
        locationState: "outside_service_area",
        locationLabel: "NYC transit only",
        locationNotice: "SmartRoute currently covers NYC transit.",
        stationName: null,
        arrivals: [],
        arrivalsState: "outside_service_area",
      },
      onOpenLiveMap() {},
    }),
  );
  assert.match(outside, /NYC transit only/);
  assert.match(outside, /covers NYC transit/);
});

test("failed chat turns render one compact recovery surface", () => {
  const html = renderToStaticMarkup(
    createElement(ChatMessage, {
      turn: {
        role: "assistant",
        turnId: "t1",
        text: "",
        reasoning: "",
        toolChips: [],
        routeCards: [],
        isStreaming: false,
        error: {
          code: "upstream_error",
          message: "SmartRoute couldn’t complete this request.",
          retryable: true,
        },
      },
      theme: "dark",
      onRetry() {},
      onDismissError() {},
    }),
  );
  assert.match(html, /class="sr-chat-turn-error" role="alert"/);
  assert.match(html, /Try again/);
  assert.match(html, /Dismiss/);
  assert.doesNotMatch(html, /Upstream request failed/);
  const user = renderToStaticMarkup(
    createElement(ChatMessage, {
      turn: { role: "user", text: "hello" },
      theme: "dark",
    }),
  );
  assert.match(user, /hello/);
});

test("working panel uses route-progress copy instead of a thinking label", () => {
  const html = renderToStaticMarkup(
    createElement(ChatWorkingPanel, {
      toolChips: [],
      progress: { stage: "finding_routes", status: "active" },
      reasoning: "Comparing live routes",
      isStreaming: true,
    }),
  );
  assert.match(html, /Finding viable routes/);
  assert.match(html, /sr-chat-working-panel__reasoning/);
  assert.doesNotMatch(html, /Thinking…/);
});

test("walking icon stays decorative", () => {
  const html = renderToStaticMarkup(createElement(WalkingIcon));
  assert.match(html, /aria-hidden="true"/);
});

test("suggestion rail CSS stays a text-only snap strip", () => {
  assert.match(
    CHAT_STYLE_SOURCE,
    /\.sr-chat-suggestion-pill\s*\{[\s\S]*?border:\s*0;[\s\S]*?background:\s*transparent;/,
  );
  assert.match(CHAT_STYLE_SOURCE, /scroll-snap-type: x mandatory/);
  assert.match(CHAT_STYLE_SOURCE, /scrollbar-width: none/);
  assert.match(CHAT_STYLE_SOURCE, /env\(safe-area-inset-bottom\)/);
  assert.doesNotMatch(CHAT_STYLE_SOURCE, /sr-chat-caret/);
});

test("local arrivals turns skip the working panel and expose Live Feed", () => {
  const html = renderToStaticMarkup(
    createElement(ChatMessage, {
      turn: {
        id: "local-1",
        role: "assistant",
        local: true,
        text: "Next Q trains",
        arrivals: {
          routeId: "Q",
          stationName: "Jay St",
          stationGuidance: "3 min walk",
          sourceStatus: "live",
          updatedAt: "2026-07-16T12:00:00-04:00",
          groups: [{ direction: "uptown", label: "Uptown", minutes: [4] }],
        },
        routeCards: [],
        isStreaming: false,
      },
      theme: "dark",
      onSeeArrivalsOnMap() {},
    }),
  );
  assert.match(html, /Jay St|Uptown|Open in Live Feed/);
  assert.deepEqual(recommendedCardsForChat([{ card_id: "recommended", role: "recommended" }]).map((card) => card.card_id), ["recommended"]);
  const empty = renderToStaticMarkup(createElement(ChatRouteCardList, { cards: [] }));
  assert.equal(empty, "");
});
