import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const CARD_SOURCE = readFileSync(
  new URL("./chat-arrivals-card.tsx", import.meta.url),
  "utf8",
);
const CHAT_CSS = readFileSync(
  new URL("../../../app/styles/smart-route-chat.css", import.meta.url),
  "utf8",
);
const ITINERARY_SOURCE = readFileSync(
  new URL("./recommended-itinerary-card.tsx", import.meta.url),
  "utf8",
);
const WALKING_ICON_SOURCE = readFileSync(
  new URL("./walking-icon.tsx", import.meta.url),
  "utf8",
);

test("arrival and itinerary cards share the same walking icon primitive", () => {
  assert.match(CARD_SOURCE, /import \{ WalkingIcon \} from "\.\/walking-icon"/);
  assert.match(ITINERARY_SOURCE, /import \{ WalkingIcon \} from "\.\/walking-icon"/);
  assert.match(CARD_SOURCE, /<WalkingIcon className="sr-chat-arrivals-card__walk-icon"/);
  assert.match(ITINERARY_SOURCE, /<WalkingIcon \/>/);
  assert.match(WALKING_ICON_SOURCE, /faPersonWalking/);
  assert.match(WALKING_ICON_SOURCE, /aria-hidden="true"/);
  assert.doesNotMatch(CARD_SOURCE, /import \{[^}]*Walking[^}]*\} from "iconoir-react"/);
});

test("light mode strengthens the existing orb without changing its dimensions", () => {
  assert.match(
    CHAT_CSS,
    /\.sr-chat-assistant-response__orb\[data-visible="true"\]\s*\{[\s\S]*?width:\s*34px;[\s\S]*?height:\s*34px;/,
  );
  assert.match(
    CHAT_CSS,
    /\.sr-chat-tab\[data-sr-theme="light"\][\s\S]*?\.sr-chat-assistant-response__orb\[data-visible="true"\][\s\S]*?filter:\s*brightness\(0\.74\) contrast\(1\.38\)/,
  );
});
