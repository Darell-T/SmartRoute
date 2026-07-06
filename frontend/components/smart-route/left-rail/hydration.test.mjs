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
    /sr-direction-pill|layoutId=\{`sr-direction-pill|useId/,
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
    /SHUTTLE_BUS_TOKEN/,
    "alert text should recognize plain shuttle-bus phrases, not just bracketed route tokens",
  );
  assert.match(
    atoms,
    /BusChip route="BUS" title="Shuttle bus"/,
    "plain shuttle-bus phrases should render with the bus pill used elsewhere in the rail",
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
  assert.match(
    alertsView,
    /function alertDotTone/,
    "alerts use the three-tier signal severity model (red/amber/green)",
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
    /sr-alert-card smart-route-liquid-card/,
    "featured alert cards share the RecommendedRouteCard liquid-glass surface",
  );
  assert.match(
    alertsView,
    /className="sr-alert-detail"/,
    "detail expands inline inside the same card (divider + rows), never a detached panel",
  );
  assert.doesNotMatch(
    alertsView,
    /sr-alert-detail[^"]*smart-route-liquid-card|sr-alert-timeline/,
    "the glass lives on the card itself — no separate glass detail panel, no update-timeline block",
  );
  assert.match(
    alertsView,
    /buildAlertDetailView/,
    "expandable detail only renders when real extra alert data exists",
  );
  assert.match(
    alertsView,
    /sr-alert-status\b/,
    "alert status renders as quiet dot + word metadata",
  );
  assert.doesNotMatch(
    alertsView,
    /sr-status-pill|AlertStatusPill/,
    "status pills are gone — severity dot + one word only",
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
    /backdrop-filter|--sr-glass|font-geist|Geist|system-ui/,
    "rail styling should avoid backdrop blur and generic AI-dashboard typography (a restrained box-shadow is allowed on the route-result liquid card)",
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
  assert.match(
    railCss,
    /\.sr-alert-card__stripe/,
    "featured alert cards carry a route/severity accent stripe",
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
