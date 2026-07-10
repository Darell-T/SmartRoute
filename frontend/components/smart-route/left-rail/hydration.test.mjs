import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const ROOT = path.resolve(import.meta.dirname, "../../..");

test("left rail route view does not render an inline client clock during SSR", () => {
  const source = fs.readFileSync(
    path.join(ROOT, "components/smart-route/left-rail/route-view.tsx"),
    "utf8",
  );

  assert.doesNotMatch(
    source,
    /new Date\(\)\.toLocaleTimeString/,
    "route-view.tsx should avoid client-only time strings in the server-rendered tree",
  );
});

test("left rail uses restrained transit product surfaces", () => {
  const routeView = fs.readFileSync(
    path.join(ROOT, "components/smart-route/left-rail/route-view.tsx"),
    "utf8",
  );
  const alertsView = fs.readFileSync(
    path.join(ROOT, "components/smart-route/left-rail/alerts-view.tsx"),
    "utf8",
  );
  const destinationSuggestions = fs.readFileSync(
    path.join(
      ROOT,
      "components/smart-route/left-rail/destination-suggestions.tsx",
    ),
    "utf8",
  );
  const leftRail = fs.readFileSync(
    path.join(ROOT, "components/smart-route/left-rail/left-rail.tsx"),
    "utf8",
  );
  const alertFeed = fs.readFileSync(
    path.join(ROOT, "components/smart-route/left-rail/alert-feed.ts"),
    "utf8",
  );
  const atoms = fs.readFileSync(
    path.join(ROOT, "components/smart-route/left-rail/atoms.tsx"),
    "utf8",
  );
  const railCss = fs.readFileSync(
    path.join(ROOT, "app/styles/smart-route-left-rail.css"),
    "utf8",
  );
  const spiralLoader = fs.readFileSync(
    path.join(ROOT, "components/smart-route/ui/spiral-fill-loader.tsx"),
    "utf8",
  );
  const mobileSheet = fs.readFileSync(
    path.join(ROOT, "components/smart-route/page/use-mobile-rail-sheet.ts"),
    "utf8",
  );
  const liveWorkspace = fs.readFileSync(
    path.join(ROOT, "components/smart-route/page/live-workspace.tsx"),
    "utf8",
  );
  const globalCss = fs.readFileSync(
    path.join(ROOT, "app/globals.css"),
    "utf8",
  );

  assert.match(
    routeView,
    /function RoutePlanningReasoning/,
    "route planning should use a compact public reasoning status",
  );
  assert.match(
    routeView,
    /buildRouteReasoningInsights/,
    "planning lines must be derived from real route-evaluation facts, not a hardcoded script",
  );
  assert.match(
    routeView,
    /role="combobox"[\s\S]*aria-autocomplete="list"[\s\S]*aria-activedescendant/,
    "destination search exposes a complete combobox relationship to its suggestions",
  );
  assert.match(
    destinationSuggestions,
    /role="listbox"[\s\S]*role="option"[\s\S]*aria-selected/,
    "destination predictions render as an accessible listbox with selected options",
  );
  assert.doesNotMatch(
    routeView,
    /function PlanningRoutePlaceholders|showRoutePlaceholders|Route options loading|sr-route-option-placeholder/,
    "route planning should not render fake route-option placeholders while loading",
  );
  assert.match(
    routeView,
    /AnimatePresence/,
    "route candidates should use motion layout entry and reorder primitives",
  );
  assert.match(
    routeView,
    /LayoutGroup/,
    "recommended and alternate route cards should share a Motion layout group",
  );
  assert.match(
    routeView,
    /ArrivalCountdown/,
    "nearby arrival countdown numbers should render through the countdown component",
  );
  assert.match(
    fs.readFileSync(
      path.join(ROOT, "components/smart-route/left-rail/arrival-countdown.tsx"),
      "utf8",
    ),
    /NumberFlow[\s\S]*trend=\{-1\}/,
    "arrival countdown values should use NumberFlow with countdown direction",
  );
  assert.match(
    routeView,
    /import \{ SpiralFillLoader \} from "@\/components\/smart-route\/ui\/spiral-fill-loader"/,
    "route planning should use the custom SmartRoute spiral loader",
  );
  assert.match(
    routeView,
    /<SpiralFillLoader className="shrink-0" \/>/,
    "the Finding routes status should render the inline spiral loader",
  );
  assert.doesNotMatch(
    routeView,
    /Mosaic|react-loading-indicators|sr-route-planning-loader/,
    "route planning should not render Mosaic or keep the old loader selector",
  );
  assert.match(
    spiralLoader,
    /size-\[3\.5px\]/,
    "the route-planning spiral loader should stay at Grok-scale dot sizing",
  );
  assert.match(
    spiralLoader,
    /gap-\[3px\]/,
    "the route-planning spiral loader should use a compact dot gap",
  );
  assert.doesNotMatch(
    routeView,
    /Where to\?|<Search|Loader2|sr-input-spinner/,
    "the destination command input should not render the old heading, search icon, or green spinner",
  );
  assert.match(
    routeView,
    /SpeechRecognition|webkitSpeechRecognition/,
    "destination input should feature-detect browser-native dictation",
  );
  assert.match(
    routeView,
    /setSpeechRecognitionCtor\(\(\) => recognitionCtor\)/,
    "SpeechRecognition constructor values must be wrapped when stored in React state",
  );
  assert.doesNotMatch(
    routeView,
    /setSpeechRecognitionCtor\(getSpeechRecognitionConstructor\(\)\)/,
    "React must not receive the SpeechRecognition constructor as a direct state setter value",
  );
  assert.match(
    routeView,
    /data-action-state=\{actionState\}/,
    "destination input should expose one action slot for submit, stop, finalizing, and clear states",
  );
  assert.match(
    leftRail,
    /planningPhase: RailPlanningPhase/,
    "left rail search props should expose the route-planning action phase",
  );
  assert.match(
    mobileSheet,
    /useState<MobileRailSheetState>\("small"\)/,
    "mobile route sheet should default to the closed/peek state",
  );
  assert.match(
    mobileSheet,
    /if \(state === "idle"\) return;/,
    "idle sync should preserve the current sheet height instead of collapsing user interactions",
  );
  assert.match(
    liveWorkspace,
    /onRailInteraction=\{mobileRail\.expandMobileRailSheet\}/,
    "rail interactions should be able to expand the compact mobile sheet",
  );
  assert.match(
    routeView,
    /onRequestRailExpand\?\.\(\);\s+onWayChange\(value as ArrivalFilter\);/,
    "nearby transit direction changes should expand the mobile sheet before filtering rows",
  );
  assert.doesNotMatch(
    liveWorkspace,
    /"--sr-mobile-sheet-px"/,
    "viewport-derived mobile sheet pixels should not be in SSR inline styles",
  );
  assert.match(
    mobileSheet,
    /"--sr-mobile-sheet-px"/,
    "mobile sheet pixels should be written after hydration for map camera padding",
  );
  assert.match(
    railCss,
    /\.sr-input-voice\s*\{[\s\S]*background: transparent;/,
    "the microphone action should render as a small bare icon, not a filled circular button",
  );
  assert.match(
    globalCss,
    /data-mobile-sheet-state="small"[\s\S]*background: transparent !important;/,
    "the compact mobile sheet should not draw a full panel behind the search shelf",
  );
  assert.match(
    globalCss,
    /sr-live-console\[data-mobile-sheet-state="small"\][\s\S]*\.sr-map-mini-controls[\s\S]*bottom: calc\(var\(--sr-mobile-sheet-px, 124px\) \+ 1rem\)/,
    "the recenter control should move above the compact mobile search shelf",
  );
  assert.match(
    routeView,
    /function PredictionSignalIcon/,
    "arrival rows should use a meaningful signal-bars prediction icon",
  );
  assert.match(
    routeView,
    /function NearbyStationGroupList/,
    "nearby subway arrivals should render as station groups",
  );
  assert.match(
    routeView,
    /function StationGroupHeader/,
    "station grouped arrivals should expose a compact station header",
  );
  assert.match(
    routeView,
    /sr-station-header__title/,
    "station headers should separate parent station title from route bullets",
  );
  assert.match(
    routeView,
    /Nearby buses/,
    "bus arrivals should stay in their own nearby bus section",
  );
  assert.match(
    routeView,
    /const busRows = nearbyBusArrivals;/,
    "nearby bus rows should remain independent of the subway direction toggle",
  );
  assert.doesNotMatch(
    routeView,
    /Choose on map|Plan your trip|View all nearby lines|All directions/,
    "route idle state should be search plus dense nearby arrivals, not filler rows",
  );
  assert.doesNotMatch(
    routeView,
    /sr-live-signal/,
    "arrival prediction status should not be the old decorative dot",
  );
  assert.doesNotMatch(
    routeView,
    /sr-direction-pill|layoutId=\{`sr-direction-pill/,
    "direction toggle should not render a shared-layout pipe marker",
  );
  assert.match(
    atoms,
    /data-step-icon="walk"[\s\S]*data-step-icon="train"[\s\S]*data-step-icon="bus"[\s\S]*data-step-icon="transfer"[\s\S]*data-step-icon="exit"/,
    "compact strip pictograms should expose stable mode icon roles",
  );
  assert.match(
    atoms,
    /marker:\s*"#ef3b5d"/,
    "start and arrive pins should use the route-marker pink",
  );
  assert.doesNotMatch(
    atoms,
    /Footprints|TrainFront|BusFront|from "lucide-react"/,
    "compact strip pictograms should not regress to generic Lucide icons",
  );
  assert.match(
    atoms,
    /cleanTransitParagraphText/,
    "alert paragraph copy should have a plain-text sanitizer",
  );
  assert.doesNotMatch(
    atoms,
    /BusChip route="BUS" title="Shuttle bus"/,
    "alert paragraph copy should not insert a generic BUS pill for shuttle-bus prose",
  );
  assert.match(
    alertsView,
    /mode="paragraph"/,
    "alert bodies and detail rows should render as readable prose, not inline badge soup",
  );
  assert.doesNotMatch(
    alertsView,
    /Affected lines|Search lines, stations, or alerts|AlertFilterPills|AffectedLineGrid/,
    "alerts tab drops the search bar, filter pills, and affected-lines grid",
  );
  assert.match(
    alertsView,
    /Near you/,
    "alerts are personalized: a Near you section of featured cards leads the feed",
  );
  assert.match(
    alertsView,
    /item\.routeIds\.some\(\(route\) => near\.has\(route\)\)/,
    "Near you should only feature alerts that match nearby route ids",
  );
  assert.doesNotMatch(
    alertsView,
    /Based on your location/,
    "the Near you header is a clean label — no location helper text or dot",
  );
  assert.match(
    alertsView,
    /Other alerts/,
    "non-nearby alerts stack below as a compact Other alerts section",
  );
  assert.match(
    alertsView,
    /groupAlertItemsByLine/,
    "other alerts should be grouped by subway trunk line before rendering",
  );
  assert.match(
    alertsView,
    /sr-alert-line-group sr-station-group/,
    "other alert line groups reuse the nearby subway station-card surface",
  );
  for (const label of ["Lexington Avenue", "Broadway", "8 Avenue"]) {
    assert.match(
      alertFeed,
      new RegExp(label),
      `${label} should be available as a passenger-facing line group label`,
    );
  }
  assert.doesNotMatch(
    alertsView,
    /See all|View all/,
    "the alerts rail ends cleanly — no view-all buttons",
  );
  assert.match(
    alertsView,
    /sr-alerts-scroll/,
    "alerts use a contained scroll (fixed title, bounded list region), not whole-rail scroll",
  );
  assert.doesNotMatch(
    alertsView,
    /alertDotTone|AlertSeverityDot/,
    "alert priority should not depend on decorative severity dots",
  );
  assert.match(
    alertsView,
    /Systemwide/,
    "systemwide notices are labelled rather than overflowing a bullet group",
  );
  assert.match(
    alertsView,
    /normalizeAlertFeedItems/,
    "alerts should render from the merged, grouped alert-feed adapter",
  );
  assert.match(
    alertsView,
    /Impact[\s\S]*Affected service[\s\S]*Travel alternatives[\s\S]*Current status/,
    "expanded detail shows the approved icon-keyed rows in reference order",
  );
  assert.match(
    alertsView,
    /className="sr-alert-card"/,
    "featured alert cards use their own flat passenger-notice surface",
  );
  assert.doesNotMatch(
    alertsView,
    /sr-alert-card smart-route-liquid-card/,
    "featured alert cards must not reuse the route-result liquid-glass material",
  );
  assert.match(
    alertsView,
    /className="sr-alert-detail"/,
    "detail expands inline inside the same card (divider + rows), never a detached panel",
  );
  assert.doesNotMatch(
    alertsView,
    /sr-alert-detail[^"]*smart-route-liquid-card|sr-alert-timeline/,
    "expanded alert detail stays inline without a detached glass panel or duplicate timeline component",
  );
  assert.match(
    alertsView,
    /buildAlertDetailView/,
    "expandable detail only renders when real extra alert data exists",
  );
  assert.match(
    alertsView,
    /sr-alert-status\b/,
    "alert status renders as quiet textual metadata",
  );
  assert.doesNotMatch(
    alertsView,
    /sr-status-pill|AlertStatusPill/,
    "status pills are gone in favor of quiet text metadata",
  );
  assert.doesNotMatch(
    alertsView,
    /All updates|line updates|sr-alert-feed-shell|LineFilterBar/,
    "alerts should not keep the post-merge feed-shell presentation",
  );
  assert.match(
    alertFeed,
    /function groupAlertThreads|export function groupAlertThreads/,
    "alerts should group same-issue items into one row with an update thread",
  );
  assert.match(
    alertFeed,
    /const byText/,
    "identical-text alerts tagged to many lines merge into one row with unioned badges",
  );
  assert.match(
    alertFeed,
    /export function leadSentences/,
    "raw MTA paragraphs truncate to lead sentences — never rendered as text walls",
  );
  assert.doesNotMatch(
    leftRail,
    /HubView|label:\s*"Hub"|tab === "hub"|HubTabIcon/,
    "left rail should not keep a Hub tab or Hub-only render path",
  );
  assert.match(
    leftRail,
    /label:\s*"Route"[\s\S]*label:\s*"Alerts"/,
    "left rail nav should render only Route and Alerts",
  );
  assert.equal(
    fs.existsSync(path.join(ROOT, "components/smart-route/left-rail/hub-view.tsx")),
    false,
    "Hub view component should be removed after merging its useful feed into Alerts",
  );
  assert.match(
    alertFeed,
    /normalizeRecentUpdates/,
    "Hub recent updates should be normalized into the alert feed adapter",
  );

  for (const [label, source] of [
    ["route-view", routeView],
    ["alerts-view", alertsView],
    ["left-rail", leftRail],
  ]) {
    assert.doesNotMatch(
      source,
      /ATLAS|JarvisBlock|RailOrb|Network Pulse|scan is paused|scanning/i,
      `${label} should not show sci-fi assistant or scan language`,
    );
  }

  assert.match(
    railCss,
    /--rail-accent:\s*#34d399/,
    "rail accent is the SmartRoute transit green (selected/system state)",
  );
  assert.match(
    railCss,
    /\.sr-station-group/,
    "station groups should use the restrained left-rail CSS surface",
  );
  assert.match(
    railCss,
    /background:\s*rgba\(16,\s*185,\s*129,\s*0\.22\)/,
    "active direction pill uses the transit-green fill",
  );
  assert.match(
    railCss,
    /border:\s*1px solid rgba\(52,\s*211,\s*153,\s*0\.45\)/,
    "active direction pill uses the transit-green border",
  );
  assert.match(
    railCss,
    /\.sr-reasoning-lines/,
    "planning state should render appended status lines",
  );
  assert.doesNotMatch(
    railCss,
    /\.sr-route-option-placeholders|\.sr-route-option-placeholder__/,
    "route planning should not keep skeleton candidate placeholder CSS",
  );
  assert.match(
    railCss,
    /\.sr-detail-step__vehicle/,
    "board rows should pair route bullets or bus pills with neutral vehicle glyphs",
  );
  assert.doesNotMatch(
    railCss,
    /#d89b2b|#f0b94b|gold|amber glass/i,
    "left rail CSS should not keep the old command-center palette",
  );
  assert.doesNotMatch(
    railCss,
    /--sr-glass|font-geist|Geist/,
    "rail styling should not keep the old command-center glass token or Geist typography",
  );
  // The "Cupertino" redesign (see design brief) intentionally introduces a
  // translucent backdrop-filter material and the system SF font stack
  // (which ends in ui-sans-serif/system-ui) — the reverse of the prior
  // command-center aesthetic this file used to guard against.
  assert.match(
    railCss,
    /backdrop-filter:\s*blur\(50px\)\s*saturate\(1\.8\)/,
    "the rail should use the Cupertino translucent material recipe",
  );
  assert.match(
    railCss,
    /--sr-font:[\s\S]*system-ui/,
    "the rail should use the system SF font stack",
  );
  assert.match(
    railCss,
    /\.smart-route-liquid-card/,
    "route-result content should use the restrained liquid-glass inner card",
  );
  assert.doesNotMatch(
    railCss,
    /\.sr-status-pill/,
    "alerts should not keep the retired lifecycle status-pill styling",
  );
  assert.doesNotMatch(
    railCss,
    /\.sr-alert-card__stripe|\.sr-alert-severity-dot/,
    "featured alerts should not restore colored stripes or severity dots",
  );
  assert.doesNotMatch(
    railCss,
    /\.sr-filter-pills/,
    "alerts should not keep stale filter-pill styling",
  );
  assert.doesNotMatch(
    railCss,
    /\.sr-line-grid/,
    "alerts should not keep stale affected-line grid styling",
  );
  assert.match(
    railCss,
    /\.sr-alert-row__severity/,
    "alert rows should expose the old severity micro-label",
  );
  assert.doesNotMatch(
    railCss,
    /\.sr-alert-search-clear/,
    "alerts should not keep stale search clear control styling",
  );
  assert.doesNotMatch(
    railCss,
    /\.sr-line-grid__button\[aria-pressed="true"\]/,
    "alerts should not keep selected affected-line filter styling",
  );
});
