// frontend/scripts/build/lane-order.test.ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { orderColorsForBundle, BUNDLE_COLOR_ORDER } from "./lane-order.ts";

test("BUNDLE_COLOR_ORDER lists 11 unique hex colors", () => {
  assert.equal(BUNDLE_COLOR_ORDER.length, 11);
  assert.equal(new Set(BUNDLE_COLOR_ORDER).size, 11);
  for (const c of BUNDLE_COLOR_ORDER) {
    assert.match(c, /^#[0-9A-Fa-f]{6}$/, `${c} should be 7-char hex`);
  }
});

test("BUNDLE_COLOR_ORDER SI hex matches the build script's ROUTE_COLORS.SI", () => {
  // SI must be #0078C6, not #00A9CE. The build script's routeColorFor("SI")
  // returns #0078C6; any divergence here causes SI lines to be ranked as
  // "unknown" (sorted to the end of multicolor bundles).
  assert.ok(BUNDLE_COLOR_ORDER.includes("#0078C6"), "SI hex #0078C6 must be in BUNDLE_COLOR_ORDER");
  assert.ok(!BUNDLE_COLOR_ORDER.includes("#00A9CE"), "stale SI hex #00A9CE must not appear");
});

test("orderColorsForBundle returns { colors, overrideApplied } shape", () => {
  const result = orderColorsForBundle(["#EE352E"]);
  assert.ok(Array.isArray(result.colors));
  assert.equal(typeof result.overrideApplied, "boolean");
});

test("orderColorsForBundle returns colors sorted by global rank (no override)", () => {
  // Pass colors in an arbitrary order; expect them re-sorted by BUNDLE_COLOR_ORDER position.
  const { colors, overrideApplied } = orderColorsForBundle(
    ["#00933C", "#EE352E", "#FCCC0A", "#FF6319"],
  );
  // red, orange, yellow, green (per BUNDLE_COLOR_ORDER)
  assert.deepEqual(colors, ["#EE352E", "#FF6319", "#FCCC0A", "#00933C"]);
  assert.equal(overrideApplied, false);
});

test("orderColorsForBundle is idempotent", () => {
  const first = orderColorsForBundle(["#FCCC0A", "#EE352E"]).colors;
  const second = orderColorsForBundle(first).colors;
  assert.deepEqual(first, second);
});

test("orderColorsForBundle preserves order when given pre-sorted input", () => {
  const sorted = [...BUNDLE_COLOR_ORDER].slice(0, 4);
  const out = orderColorsForBundle(sorted).colors;
  assert.deepEqual(out, sorted);
});

test("orderColorsForBundle applies a full-match override and reports overrideApplied=true", () => {
  const overrides = {
    "atlantic-pacific": ["#EE352E", "#00933C", "#FF6319", "#FCCC0A"],
  };
  const { colors, overrideApplied } = orderColorsForBundle(
    ["#FCCC0A", "#EE352E", "#00933C", "#FF6319"],
    { overrideKey: "atlantic-pacific", overrides },
  );
  assert.deepEqual(colors, ["#EE352E", "#00933C", "#FF6319", "#FCCC0A"]);
  assert.equal(overrideApplied, true);
});

test("orderColorsForBundle falls back to heuristic when override has too few colors", () => {
  // Override mentions 4 colors but the bundle only has 2 of them.
  const overrides = {
    "test-key": ["#EE352E", "#00933C", "#FF6319", "#FCCC0A"],
  };
  // Pass only red + green (2 of the 4 override colors).
  const { colors, overrideApplied } = orderColorsForBundle(
    ["#00933C", "#EE352E"],
    { overrideKey: "test-key", overrides },
  );
  // Should NOT use the override (length mismatch); fall back to heuristic order.
  assert.deepEqual(colors, ["#EE352E", "#00933C"]);
  assert.equal(overrideApplied, false);
});

test("orderColorsForBundle falls back to heuristic when override mentions extra colors not in the bundle", () => {
  // Override lists 3 colors but bundle has 2 of which 1 is not in the override list.
  const overrides = {
    "test-key": ["#EE352E", "#00933C", "#FF6319"],  // 3 colors
  };
  // Bundle has yellow (not in override) + red. Override coverage incomplete from BOTH sides.
  const { colors, overrideApplied } = orderColorsForBundle(
    ["#FCCC0A", "#EE352E"],
    { overrideKey: "test-key", overrides },
  );
  assert.deepEqual(colors, ["#EE352E", "#FCCC0A"]);
  assert.equal(overrideApplied, false);
});

test("orderColorsForBundle ignores override when key not present", () => {
  const overrides = {};
  const { colors, overrideApplied } = orderColorsForBundle(
    ["#FCCC0A", "#EE352E"],
    { overrideKey: "missing", overrides },
  );
  assert.deepEqual(colors, ["#EE352E", "#FCCC0A"]);
  assert.equal(overrideApplied, false);
});

test("orderColorsForBundle treats null overrideKey as no override", () => {
  const overrides = { "atlantic": ["#FCCC0A", "#EE352E"] };
  const { colors, overrideApplied } = orderColorsForBundle(
    ["#FCCC0A", "#EE352E"],
    { overrideKey: null, overrides },
  );
  assert.deepEqual(colors, ["#EE352E", "#FCCC0A"]);
  assert.equal(overrideApplied, false);
});

test("orderColorsForBundle handles empty input", () => {
  const { colors, overrideApplied } = orderColorsForBundle([]);
  assert.deepEqual(colors, []);
  assert.equal(overrideApplied, false);
});

test("orderColorsForBundle preserves input order for unknown colors after known ones", () => {
  // Pin the full ordering: known color (yellow) first, then unknowns in input order.
  const { colors } = orderColorsForBundle(["#123456", "#FCCC0A", "#abcdef"]);
  assert.deepEqual(colors, ["#FCCC0A", "#123456", "#abcdef"]);
});
