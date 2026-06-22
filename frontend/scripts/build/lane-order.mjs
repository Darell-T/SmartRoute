// frontend/scripts/build/lane-order.mjs
// Deterministic color-lane ordering for subway bundles, with optional
// hand-curated overrides keyed by overrideKey (e.g. "<from>::<to>" anchor pair
// or a stable bundle identifier).
//
// Pure module: no fs, no globals.

// Canonical ranked color order for SmartRoute bundle lanes.
// Single source of truth -- imported by build-subway-visual-network.mjs.
// SI hex (#0078C6) matches ROUTE_COLORS.SI in the build script; do not
// substitute #00A9CE here or SI lines will be treated as unknown colors
// and sorted to the end of any multicolor bundle they appear in.
export const BUNDLE_COLOR_ORDER = [
  "#EE352E", // red          1/2/3
  "#FF6319", // orange       B/D/F/M
  "#FCCC0A", // yellow       N/Q/R/W
  "#00933C", // green        4/5/6
  "#0A84FF", // blue         A/C/E
  "#A7A9AC", // L gray       L
  "#6CBE45", // light green  G
  "#B933AD", // purple       7
  "#996633", // brown        J/Z
  "#0078C6", // SI dark blue SI
  "#808183", // shuttle gray S/FS/GS/H
];

const RANK = new Map(BUNDLE_COLOR_ORDER.map((c, i) => [c.toUpperCase(), i]));

function rank(color) {
  const r = RANK.get(String(color).toUpperCase());
  return r === undefined ? Number.POSITIVE_INFINITY : r;
}

/**
 * Order a bundle's color list. If overrides[overrideKey] is provided AND its
 * set of colors matches the input exactly, use that order verbatim. Otherwise,
 * fall back to the global BUNDLE_COLOR_ORDER rank.
 *
 * Returns BOTH the ordered colors AND whether an override was applied, so the
 * build can record provenance in its lane-order debug artifact without a
 * second lookup.
 *
 * Precondition: `colors` should be deduped case-insensitively by the caller.
 * Two entries that differ only in case (e.g. `#EE352E` and `#ee352e`) are
 * treated as the same color; only one survives the override mapping.
 *
 * @param {Array<string>} colors  Hex color strings present in the bundle.
 * @param {object} [options]
 * @param {string|null} [options.overrideKey]  Lookup key for the overrides table.
 *   Pass `null` to skip override lookup entirely (e.g. when anchor pair is incomplete).
 * @param {Record<string, Array<string>>} [options.overrides]
 * @returns {{ colors: Array<string>, overrideApplied: boolean }}
 */
export function orderColorsForBundle(colors, { overrideKey, overrides = {} } = {}) {
  if (overrideKey && overrides[overrideKey]) {
    const want = overrides[overrideKey].map((c) => c.toUpperCase());
    const haveUpper = colors.map((c) => String(c).toUpperCase());
    const have = new Set(haveUpper);
    const matched = want.filter((c) => have.has(c));
    // Both checks needed: matched.length === colors.length ensures every input
    // color is mentioned in the override; matched.length === want.length
    // ensures the override doesn't list extra colors not in the bundle.
    if (matched.length === colors.length && matched.length === want.length) {
      // Full-match override: preserve original casing from input by mapping back.
      const upperToOriginal = new Map();
      for (const c of colors) upperToOriginal.set(String(c).toUpperCase(), c);
      return {
        colors: matched.map((c) => upperToOriginal.get(c)),
        overrideApplied: true,
      };
    }
    // Override does not fully match -- fall through to heuristic.
  }
  // Heuristic: sort by global rank. Unknown colors come after known ones and
  // preserve their relative input order via stable sort tie-breaking.
  const sorted = [...colors]
    .map((c, idx) => ({ c, idx }))
    .sort((a, b) => {
      const ra = rank(a.c);
      const rb = rank(b.c);
      if (ra !== rb) return ra - rb;
      return a.idx - b.idx;
    })
    .map((x) => x.c);
  return { colors: sorted, overrideApplied: false };
}
