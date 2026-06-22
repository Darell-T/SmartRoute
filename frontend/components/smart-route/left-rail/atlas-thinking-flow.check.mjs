import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const here = import.meta.dirname;
const leftRailPath = resolve(here, "left-rail.tsx");
const pagePath = resolve(here, "../../../app/page.tsx");

const leftRail = readFileSync(leftRailPath, "utf8");
const page = readFileSync(pagePath, "utf8");

assert.match(
  leftRail,
  /thinkingText\?: string/,
  "LeftRail should accept controlled ATLAS thinking text from the route pipeline",
);

assert.match(
  leftRail,
  /thinkingText=\{thinkingText\}/,
  "RouteView should pass controlled thinkingText into JarvisBlock",
);

assert.doesNotMatch(
  leftRail,
  /<ThinkingTicker\s*\/>|function ThinkingTicker/,
  "The mounted ATLAS card should not rotate local canned thinking phrases",
);

assert.match(
  leftRail,
  /WebkitLineClamp/,
  "The ATLAS card copy should be line-clamped so long text does not expand the card",
);

assert.match(
  page,
  /thinkingText=\{thinkingText\}/,
  "app/page.tsx should pass live thinkingText into LeftRail",
);

assert.match(
  page,
  /routePlanningRequestIdRef/,
  "Route planning should guard thinking and route audio against stale requests",
);
