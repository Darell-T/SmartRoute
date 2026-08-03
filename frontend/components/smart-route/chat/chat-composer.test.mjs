import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { fileURLToPath } from "node:url";

const COMPOSER_SOURCE = fs.readFileSync(
  fileURLToPath(new URL("./chat-composer.tsx", import.meta.url)),
  "utf8",
);
const PANEL_SOURCE = fs.readFileSync(
  fileURLToPath(new URL("./chat-panel.tsx", import.meta.url)),
  "utf8",
);
const MODE_MENU_SOURCE = fs.readFileSync(
  fileURLToPath(new URL("./response-mode-menu.tsx", import.meta.url)),
  "utf8",
);
const WELCOME_SOURCE = fs.readFileSync(
  fileURLToPath(new URL("./chat-welcome.tsx", import.meta.url)),
  "utf8",
);
const HOME_NEARBY_SOURCE = fs.readFileSync(
  fileURLToPath(new URL("./home-near-you.tsx", import.meta.url)),
  "utf8",
);
const SUGGESTION_SOURCE = fs.readFileSync(
  fileURLToPath(
    new URL("../../prompt-kit/prompt-suggestion.tsx", import.meta.url),
  ),
  "utf8",
);
const CHAT_STYLE_SOURCE = fs.readFileSync(
  fileURLToPath(
    new URL("../../../app/styles/smart-route-chat.css", import.meta.url),
  ),
  "utf8",
);
const MESSAGE_SOURCE = fs.readFileSync(
  fileURLToPath(new URL("./chat-message.tsx", import.meta.url)),
  "utf8",
);
const WORKING_PANEL_SOURCE = fs.readFileSync(
  fileURLToPath(new URL("./chat-working-panel.tsx", import.meta.url)),
  "utf8",
);

test("composer exposes Auto and Quick through an accessible custom menu", () => {
  assert.match(COMPOSER_SOURCE, /<ResponseModeMenu/);
  assert.doesNotMatch(COMPOSER_SOURCE, /<select/);
  assert.doesNotMatch(COMPOSER_SOURCE, /<option/);
  assert.match(MODE_MENU_SOURCE, /value: "auto"/);
  assert.match(MODE_MENU_SOURCE, /value: "quick"/);
  assert.match(MODE_MENU_SOURCE, /role="menuitemradio"/);
  assert.match(MODE_MENU_SOURCE, /aria-checked=\{selected\}/);
  assert.match(MODE_MENU_SOURCE, /aria-haspopup="menu"/);
  assert.match(MODE_MENU_SOURCE, /aria-expanded=\{open\}/);
  assert.match(
    MODE_MENU_SOURCE,
    /Mode affects response depth, not trip time\./,
  );
  assert.match(
    MODE_MENU_SOURCE,
    /Chooses the right amount of analysis/,
  );
  assert.match(
    MODE_MENU_SOURCE,
    /Faster response with fewer comparisons/,
  );
  assert.doesNotMatch(MODE_MENU_SOURCE, /sr-response-mode-menu__eyebrow/);
  assert.doesNotMatch(MODE_MENU_SOURCE, /components\/ui\/brain/);
  assert.doesNotMatch(MODE_MENU_SOURCE, /components\/ui\/zap/);
  assert.match(MODE_MENU_SOURCE, /ChevronDown/);
});

test("response menu opens upward and supports keyboard navigation", () => {
  assert.match(MODE_MENU_SOURCE, /bottom: window\.innerHeight - rect\.top \+ 8/);
  assert.match(MODE_MENU_SOURCE, /const MENU_WIDTH = 240/);
  assert.match(MODE_MENU_SOURCE, /rect\.right - MENU_WIDTH/);
  assert.match(MODE_MENU_SOURCE, /event\.key === "ArrowDown"/);
  assert.match(MODE_MENU_SOURCE, /event\.key === "ArrowUp"/);
  assert.match(MODE_MENU_SOURCE, /event\.key === "Home"/);
  assert.match(MODE_MENU_SOURCE, /event\.key === "End"/);
  assert.match(MODE_MENU_SOURCE, /event\.key === "Escape"/);
});

test("focused response mode options activate with Enter and Space", () => {
  const activationBranch = /event\.key === "Enter" \|\| event\.key === " "\)[\s\S]*?event\.preventDefault\(\);[\s\S]*?onValueChange\(RESPONSE_MODES\[index\]\.value\);[\s\S]*?closeMenu\(\);/;
  assert.match(MODE_MENU_SOURCE, activationBranch);
});

test("composer actions use the shared Prompt Kit action primitive", () => {
  assert.match(COMPOSER_SOURCE, /<PromptInputActions className="sr-chat-composer__actions">/);
  assert.match(COMPOSER_SOURCE, /<PromptInputAction[\s\S]*Use voice input/);
  assert.match(
    COMPOSER_SOURCE,
    /<PromptInputAction\s+tooltip=\{isStreaming \? "Stop response" : "Send message"\}/,
  );
  assert.ok(
    COMPOSER_SOURCE.indexOf("<PromptInputTextarea") <
      COMPOSER_SOURCE.indexOf("<PromptInputActions"),
    "the text area should precede Auto, microphone, and send controls",
  );
  assert.doesNotMatch(COMPOSER_SOURCE, /import\s*\{[^}]*\bPlus\b[^}]*\}/s);
  assert.doesNotMatch(COMPOSER_SOURCE, /aria-label=["'][^"']*attach/i);
});

test("changing presentation does not regenerate a completed response", () => {
  assert.match(PANEL_SOURCE, /useSyncExternalStore\([\s\S]*?responsePresentationModeStore\.getServerSnapshot/);
  assert.match(
    PANEL_SOURCE,
    /onPresentationModeChange=\{responsePresentationModeStore\.setMode\}/,
  );
  assert.match(PANEL_SOURCE, /onSend=\{\(text\) => chat\.send\(text, presentationMode\)\}/);
  assert.doesNotMatch(PANEL_SOURCE, /useEffect\(\(\) => \{\s*chat\.send/);
});

test("empty chat leads with nearby transit proof and compact Prompt Kit suggestions", () => {
  assert.match(WELCOME_SOURCE, /Where to\?/);
  assert.match(WELCOME_SOURCE, /sr-chat-welcome-line--title/);
  assert.match(WELCOME_SOURCE, /<HomeNearYou/);
  assert.match(HOME_NEARBY_SOURCE, /<ArrivalCountdown/);
  assert.match(HOME_NEARBY_SOURCE, /MapPin/);
  assert.doesNotMatch(HOME_NEARBY_SOURCE, /Brain|Sparkle|Diamond/);
  assert.doesNotMatch(WELCOME_SOURCE, /Let’s get moving/);
  assert.match(WELCOME_SOURCE, /<PromptSuggestion/);
  assert.match(WELCOME_SOURCE, /variant="outline"/);
  assert.doesNotMatch(WELCOME_SOURCE, /NavArrowRight/);
  assert.doesNotMatch(WELCOME_SOURCE, /Chevron/);
  assert.match(PANEL_SOURCE, /JFK by 6:30 PM/);
  assert.match(PANEL_SOURCE, /Brooklyn to Midtown/);
  assert.match(PANEL_SOURCE, /Coney Island, less walking/);
  assert.match(SUGGESTION_SOURCE, /variant = "outline"/);

  assert.match(
    PANEL_SOURCE,
    /Get me to JFK by 6:30 PM with the fewest transfers/,
  );
  assert.match(
    PANEL_SOURCE,
    /Best route from Brooklyn to Midtown while avoiding current delays/,
  );
  assert.match(
    PANEL_SOURCE,
    /Plan a trip to Coney Island with less walking/,
  );
  assert.match(PANEL_SOURCE, /fillDraftAndFocus/);
  assert.match(
    PANEL_SOURCE,
    /querySelector\("textarea"\)\?\.focus\(\)/,
  );
});

test("mobile suggestions use animated transit glyphs and a focus-aware snap rail", () => {
  assert.match(WELCOME_SOURCE, /components\/ui\/airplane/);
  assert.match(WELCOME_SOURCE, /components\/ui\/waypoints/);
  assert.match(WELCOME_SOURCE, /components\/ui\/accessibility/);
  assert.match(PANEL_SOURCE, /icon: "airplane"/);
  assert.match(PANEL_SOURCE, /icon: "waypoints"/);
  assert.match(PANEL_SOURCE, /icon: "accessibility"/);
  assert.match(PANEL_SOURCE, /className="sr-chat-composer-dock"/);
  assert.match(CHAT_STYLE_SOURCE, /scroll-snap-type: x mandatory/);
  assert.match(CHAT_STYLE_SOURCE, /scrollbar-width: none/);
  assert.match(CHAT_STYLE_SOURCE, /env\(safe-area-inset-bottom\)/);
  assert.match(
    CHAT_STYLE_SOURCE,
    /\.sr-chat-empty__suggestions\[data-hidden="true"\]/,
  );
  assert.match(PANEL_SOURCE, /hidden=\{composerFocused\}/);
  assert.match(
    CHAT_STYLE_SOURCE,
    /\.sr-chat-suggestion-motion \{[\s\S]*?scroll-snap-align: start/,
  );
});

test("generic working copy deliberates while semantic transit stages remain factual", () => {
  assert.match(WORKING_PANEL_SOURCE, /Finding viable routes/);
  assert.match(WORKING_PANEL_SOURCE, /Checking live service and current incidents/);
  assert.match(WORKING_PANEL_SOURCE, /Deliberating between the best options/);
  assert.match(WORKING_PANEL_SOURCE, /<Shimmer[^>]*>[\s\S]*?Deliberating?/);
  assert.doesNotMatch(WORKING_PANEL_SOURCE, /Thinking?/);
  assert.match(
    MESSAGE_SOURCE,
    /aria-label=\{isFindingRoutes \? "Searching for the best route" : "Deliberating"\}/,
  );
  assert.doesNotMatch(WORKING_PANEL_SOURCE, /CheckCircle|CircleCheck|CheckIcon/);
});

test("failed chat turns have one compact manual recovery surface", () => {
  const messageSource = fs.readFileSync(
    fileURLToPath(new URL("./chat-message.tsx", import.meta.url)),
    "utf8",
  );
  assert.doesNotMatch(PANEL_SOURCE, /sr-chat-error-banner/);
  assert.match(messageSource, /className="sr-chat-turn-error" role="alert"/);
  assert.match(messageSource, />\s*Try again\s*</);
  assert.match(messageSource, />\s*Dismiss\s*</);
  assert.match(PANEL_SOURCE, /chat\.retryLast/);
  assert.match(PANEL_SOURCE, /chat\.dismissError/);
  assert.doesNotMatch(messageSource, /Upstream request failed\./);
});
