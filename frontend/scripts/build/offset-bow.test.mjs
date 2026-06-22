import { test } from "node:test";
import assert from "node:assert/strict";
import { offsetBow } from "./offset-bow.mjs";

const DEG_LAT = 1 / 110574;
const DEG_LON = 1 / (111320 * Math.cos((40.815 * Math.PI) / 180));
const O = [-73.928, 40.818];
const P = (dxM, dyM) => [O[0] + dxM * DEG_LON, O[1] + dyM * DEG_LAT];
const R = 6371000;
function hav([a, b], [c, d]) {
  const r = Math.PI / 180, dy = (d - b) * r, dx = (c - a) * r;
  return 2 * R * Math.asin(Math.sqrt(Math.sin(dy / 2) ** 2 + Math.cos(b * r) * Math.cos(d * r) * Math.sin(dx / 2) ** 2));
}

test("bow coincides with the spine at both ends", () => {
  const spine = Array.from({ length: 9 }, (_, i) => P(i * 50, 0)); // 400m east
  const bow = offsetBow(spine, { maxOffsetM: 80, side: "left" });
  assert.ok(hav(bow[0], spine[0]) < 1, "start coincides");
  assert.ok(hav(bow[bow.length - 1], spine[spine.length - 1]) < 1, "end coincides");
});

test("bow reaches ~maxOffset perpendicular at the middle", () => {
  const spine = Array.from({ length: 9 }, (_, i) => P(i * 50, 0)); // straight east
  const bow = offsetBow(spine, { maxOffsetM: 80, side: "left" });
  const mid = bow[4]; // t=0.5
  const offset = hav(mid, spine[4]);
  assert.ok(Math.abs(offset - 80) < 6, `mid offset ~80m, got ${offset.toFixed(1)}`);
  // left of due-east travel is north (+lat)
  assert.ok(mid[1] > spine[4][1], "bowed to the left (north) of an eastward spine");
});

test("right side bows the opposite way", () => {
  const spine = Array.from({ length: 9 }, (_, i) => P(i * 50, 0));
  const left = offsetBow(spine, { maxOffsetM: 80, side: "left" });
  const right = offsetBow(spine, { maxOffsetM: 80, side: "right" });
  assert.ok(left[4][1] > spine[4][1] && right[4][1] < spine[4][1], "opposite sides");
});

test("teardrop profile: single smooth convex bump with a steep (non-tangential) rejoin", () => {
  const spine = Array.from({ length: 41 }, (_, i) => P(i * 10, 0)); // 400m east, dense
  const bow = offsetBow(spine, { maxOffsetM: 80, side: "left", teardropK: 1.6 });
  const d = bow.map((p, i) => hav(p, spine[i]));
  // exactly one interior peak (single convex loop, not two bumps)
  let peaks = 0;
  for (let i = 1; i < d.length - 1; i += 1) if (d[i] > d[i - 1] && d[i] >= d[i + 1]) peaks += 1;
  assert.equal(peaks, 1, "single apex");
  assert.ok(d[0] < 1 && d[d.length - 1] < 1, "meets spine at both ends");
  // rejoin steeper than the peel (teardrop Y): last-segment drop > first-segment rise
  const peelRise = d[1] - d[0];
  const rejoinDrop = d[d.length - 2] - d[d.length - 1];
  assert.ok(rejoinDrop > peelRise, `Y rejoin steeper than peel (${rejoinDrop.toFixed(1)} > ${peelRise.toFixed(1)})`);
});

test("bow has no kink (monotone smooth offset profile near the middle)", () => {
  const spine = Array.from({ length: 21 }, (_, i) => P(i * 20, 0));
  const bow = offsetBow(spine, { maxOffsetM: 80, side: "left" });
  // perpendicular distance should rise to the middle then fall, smoothly
  const d = bow.map((p, i) => hav(p, spine[i]));
  let peakIdx = 0;
  for (let i = 1; i < d.length; i += 1) if (d[i] > d[peakIdx]) peakIdx = i;
  assert.ok(peakIdx > 6 && peakIdx < 14, "peak near the middle");
  for (let i = 1; i <= peakIdx; i += 1) assert.ok(d[i] >= d[i - 1] - 0.5, "rising to peak");
  for (let i = peakIdx + 1; i < d.length; i += 1) assert.ok(d[i] <= d[i - 1] + 0.5, "falling after peak");
});
