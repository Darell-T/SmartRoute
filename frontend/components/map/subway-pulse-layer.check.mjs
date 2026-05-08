import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import vm from "node:vm";
import ts from "typescript";

const require = createRequire(import.meta.url);
const helperPath = new URL("./subway-pulse-layer.ts", import.meta.url);
const helperSource = readFileSync(helperPath, "utf8");
const helperModule = { exports: {} };
const transpiled = ts.transpileModule(helperSource, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2020,
  },
});

vm.runInNewContext(transpiled.outputText, {
  console,
  exports: helperModule.exports,
  module: helperModule,
  require,
}, { filename: helperPath.pathname });

const {
  buildSubwayPulseTrips,
  resolveSubwayPulseVisuals,
  MANHATTAN_HEART,
  SUBWAY_PULSE_LOOP_MS,
} = helperModule.exports;

const network = {
  type: "FeatureCollection",
  features: [
    {
      type: "Feature",
      properties: {
        route_id: "B",
        shape_id: "B.test",
        color: "#FF6319",
      },
      geometry: {
        type: "LineString",
        coordinates: [
          [-73.99, 40.65],
          [-73.98, 40.66],
          [-73.97, 40.67],
        ],
      },
    },
  ],
};

const trips = buildSubwayPulseTrips(network);
assert.equal(trips.length, 3, "each canonical shape should create pulse offsets");

const idle = resolveSubwayPulseVisuals(trips[0]);
const emphasized = resolveSubwayPulseVisuals(trips[0], new Set(["b"]));
const background = resolveSubwayPulseVisuals(trips[0], new Set(["Q"]));

assert.equal(
  emphasized.color[3] > idle.color[3],
  true,
  "emphasized pulse should be more opaque than idle pulse",
);
assert.equal(
  emphasized.width > idle.width,
  true,
  "emphasized pulse should be wider than idle pulse",
);
assert.equal(
  background.color[3] < idle.color[3],
  true,
  "background pulse should be quieter when another route is emphasized",
);
assert.equal(
  background.width < idle.width,
  true,
  "background pulse should be narrower when another route is emphasized",
);

// Loop duration regression: locks in the new 22s loop after the
// converging-pulse rewrite (was 18s).
assert.equal(
  SUBWAY_PULSE_LOOP_MS,
  22000,
  "SUBWAY_PULSE_LOOP_MS must be 22s for the converging-pulse loop",
);

// Manhattan-heart anchor regression: the [-73.985, 40.755] anchor (Times Sq
// area) is the convergence point all pulses flow toward. Component-wise check
// because the array is constructed in a vm context — its prototype identity
// differs from the host, so deepStrictEqual would fail on prototype inequality
// even though the values match.
assert.equal(MANHATTAN_HEART[0], -73.985, "MANHATTAN_HEART lng must be -73.985");
assert.equal(MANHATTAN_HEART[1], 40.755, "MANHATTAN_HEART lat must be 40.755");

// Convergence regression: every emitted trip's path must end at a vertex
// that is closer to MANHATTAN_HEART than its starting vertex. Locks in the
// "all pulses flow toward Manhattan" contract.
function distFromHeartM(coord) {
  const heartLat = MANHATTAN_HEART[1];
  const avgLat = (coord[1] + heartLat) / 2;
  const mPerLat = 111320;
  const mPerLng = mPerLat * Math.cos((avgLat * Math.PI) / 180);
  const dx = (coord[0] - MANHATTAN_HEART[0]) * mPerLng;
  const dy = (coord[1] - heartLat) * mPerLat;
  return Math.hypot(dx, dy);
}
const tripsForConvergenceAssertion = buildSubwayPulseTrips({
  type: "FeatureCollection",
  features: [
    {
      type: "Feature",
      properties: { route_id: "4", color: "#00933C" },
      geometry: {
        // Synthetic 4-train-like through-route: north → Manhattan → Brooklyn.
        type: "LineString",
        coordinates: [
          [-73.85, 40.88], // Bronx terminus
          [-73.92, 40.82],
          [-73.97, 40.78],
          [-73.985, 40.755], // ~Manhattan apex (matches MANHATTAN_HEART)
          [-73.99, 40.71],
          [-73.94, 40.67], // Brooklyn terminus
        ],
      },
    },
  ],
});
assert.equal(
  tripsForConvergenceAssertion.length,
  6,
  "synthetic through-route must emit 2 halves × 3 offsets = 6 trips",
);
for (const trip of tripsForConvergenceAssertion) {
  const startD = distFromHeartM(trip.path[0]);
  const endD = distFromHeartM(trip.path[trip.path.length - 1]);
  assert.ok(
    endD < startD,
    `trip ${trip.id} must flow toward Manhattan (start ${startD.toFixed(0)}m vs end ${endD.toFixed(0)}m from heart)`,
  );
}

console.log("subway pulse layer checks passed");
