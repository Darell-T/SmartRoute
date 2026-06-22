import { test } from "node:test";
import assert from "node:assert/strict";
import { simplifyTightCurves, maxTurnDensityDegPerM } from "./simplify-tight-curves.mjs";

const DEG_LAT = 1 / 110574;
const DEG_LON = 1 / (111320 * Math.cos((40.69 * Math.PI) / 180));
function P(lon0, lat0, dxM, dyM) {
  return [lon0 + dxM * DEG_LON, lat0 + dyM * DEG_LAT];
}
const R = 6371000;
function hav([a, b], [c, d]) {
  const r = Math.PI / 180, dy = (d - b) * r, dx = (c - a) * r;
  return 2 * R * Math.asin(Math.sqrt(Math.sin(dy / 2) ** 2 + Math.cos(b * r) * Math.cos(d * r) * Math.sin(dx / 2) ** 2));
}
function maxPerp(coords) {
  // max perpendicular distance of any vertex from the straight chord (endpoint to endpoint)
  const a = coords[0], b = coords[coords.length - 1];
  const k = 111320 * Math.cos((a[1] * Math.PI) / 180);
  const ax = a[0] * k, ay = a[1] * 110574, bx = b[0] * k, by = b[1] * 110574;
  const dx = bx - ax, dy = by - ay, l = Math.hypot(dx, dy) || 1e-9;
  let m = 0;
  for (const p of coords) {
    const px = p[0] * k, py = p[1] * 110574;
    const d = Math.abs((px - ax) * dy - (py - ay) * dx) / l;
    if (d > m) m = d;
  }
  return m;
}

// Build a tight ~180deg hairpin (radius ~18m) flanked by straight approaches.
function tightHairpin() {
  const o = [-73.928, 40.818];
  const pts = [P(...o, 0, -120), P(...o, 0, -60), P(...o, 0, -10)];
  const Rm = 18;
  for (let deg = 180; deg >= 0; deg -= 20) {
    const a = (deg * Math.PI) / 180;
    pts.push(P(...o, Rm - Rm * Math.cos(a), Rm * Math.sin(a)));
  }
  pts.push(P(...o, 2 * Rm, -10), P(...o, 2 * Rm, -60), P(...o, 2 * Rm, -120));
  return pts;
}

test("simplifyTightCurves pulls a tight hairpin toward a gentler arc and preserves endpoints", () => {
  const coords = tightHairpin();
  const oLat = 40.818;
  // the hairpin "bulge" is how far the tip pushes north beyond the approach tops (dy=-10)
  const approachTopLat = coords[2][1];
  const bulge = (cs) => Math.max(...cs.map((p) => p[1])) - approachTopLat;
  const before = bulge(coords);
  const out = simplifyTightCurves(coords, { tightTurnDeg: 70, windowM: 50, iterations: 16, lambda: 0.5 });
  // endpoints byte-identical
  assert.deepEqual(out[0], coords[0]);
  assert.deepEqual(out[out.length - 1], coords[coords.length - 1]);
  // tip is pulled in (bulge reduced), but the curve is rounded, not deleted
  const after = bulge(out);
  assert.ok(after < before * 0.7, `expected tip to pull in: before=${(before / DEG_LAT).toFixed(1)}m after=${(after / DEG_LAT).toFixed(1)}m`);
  assert.ok(after > 0, "curve should round, not collapse past the approach line");
  // local turn density drops (less hairpin)
  assert.ok(maxTurnDensityDegPerM(out, 40) < maxTurnDensityDegPerM(coords, 40));
  void oLat; void maxPerp;
});

test("simplifyTightCurves leaves a straight line byte-identical (same ref)", () => {
  const o = [-73.98, 40.69];
  const coords = [P(...o, 0, 0), P(...o, 0, 100), P(...o, 0, 200), P(...o, 0, 300), P(...o, 0, 400)];
  assert.equal(simplifyTightCurves(coords, { tightTurnDeg: 70 }), coords);
});

test("simplifyTightCurves leaves a gentle large-radius curve essentially unchanged", () => {
  const o = [-73.98, 40.69];
  // gentle ~90deg curve over ~1km (radius ~600m): low turn density
  const coords = [];
  const Rm = 600;
  for (let deg = 180; deg >= 90; deg -= 6) {
    const a = (deg * Math.PI) / 180;
    coords.push(P(...o, Rm + Rm * Math.cos(a), Rm * Math.sin(a)));
  }
  const out = simplifyTightCurves(coords, { tightTurnDeg: 70, windowM: 50, iterations: 16, lambda: 0.5 });
  let maxMove = 0;
  for (let i = 0; i < coords.length; i += 1) maxMove = Math.max(maxMove, hav(coords[i], out[i]));
  assert.ok(maxMove < 2, `gentle curve should barely move, got ${maxMove.toFixed(2)}m`);
});

test("simplifyTightCurves returns inputs < 5 points unchanged", () => {
  const a = [[-73.98, 40.69], [-73.97, 40.70], [-73.96, 40.70]];
  assert.equal(simplifyTightCurves(a, {}), a);
});
