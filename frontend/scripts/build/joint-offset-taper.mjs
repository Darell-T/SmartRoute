// Pure helper -- no fs, no globals. Removes lane-slot kinks at corridor
// joints.
//
// Lanes bake their lane_slot offset into geometry (some in the physical-
// bundle materialization, some in the Fix-3 bake loop). Where the same route
// continues into an adjacent corridor with a DIFFERENT slot (e.g. G: bundled
// with F at slot +0.5 south of Terrace Pl, solo at slot 0 north of it), the
// baked endpoints land a few meters apart laterally and the route-gap bridge
// joins them with a sharp sideways step -- a visible zigzag at high zoom.
//
// This pass runs AFTER all offset baking: it finds same-route endpoint pairs
// whose gap is a small lateral step (snapMinM..snapMaxM) and whose semantic
// slots differ, then warps the tail of the MORE-offset lane so its endpoint
// lands exactly on the neighbor's, easing the correction over blendM of arc
// length (smoothstep, so no corner where the warp meets the lane body).
// Already-flush joints (gap < snapMinM -- e.g. fanout ramps that taper to
// slot 0 themselves) are left untouched.

const M_PER_DEG_LAT = 110574;

function metersPerDegLng(lat) {
  return 111320 * Math.cos((lat * Math.PI) / 180);
}

function distM(a, b) {
  const k = metersPerDegLng((a[1] + b[1]) / 2);
  return Math.hypot((a[0] - b[0]) * k, (a[1] - b[1]) * M_PER_DEG_LAT);
}

function sharedRoute(aRouteIds, bRouteIds) {
  const set = new Set(aRouteIds ?? []);
  return (bRouteIds ?? []).some((r) => set.has(r));
}

function semanticSlot(feature) {
  const props = feature.properties ?? {};
  const value = Number(props.lane_slot_semantic ?? props.lane_slot ?? 0);
  return Number.isFinite(value) ? value : 0;
}

// Smoothstep: continuous first derivative at both ends of the blend zone.
function ease(t) {
  const clamped = Math.max(0, Math.min(1, t));
  return clamped * clamped * (3 - 2 * clamped);
}

/**
 * Warp one end of a polyline so the endpoint lands on `target`, easing the
 * correction over the last `blendM` of arc length. Mutates nothing; returns
 * new coordinates.
 */
function warpTailToTarget(coords, endpointIndex, target, blendM) {
  const last = coords.length - 1;
  const endpoint = endpointIndex === 0 ? coords[0] : coords[last];
  const delta = [target[0] - endpoint[0], target[1] - endpoint[1]];

  // Arc distance of each vertex from the warped endpoint.
  const fromEnd = new Array(coords.length).fill(0);
  if (endpointIndex === 0) {
    for (let i = 1; i < coords.length; i += 1) {
      fromEnd[i] = fromEnd[i - 1] + distM(coords[i - 1], coords[i]);
    }
  } else {
    for (let i = last - 1; i >= 0; i -= 1) {
      fromEnd[i] = fromEnd[i + 1] + distM(coords[i], coords[i + 1]);
    }
  }
  const total = endpointIndex === 0 ? fromEnd[last] : fromEnd[0];
  const blend = Math.min(blendM, total);
  if (blend <= 0) return coords;

  return coords.map((c, i) => {
    const w = ease(1 - fromEnd[i] / blend);
    if (w <= 0) return c;
    return [c[0] + delta[0] * w, c[1] + delta[1] * w];
  });
}

/**
 * Detect and repair small lateral steps between baked same-route lane
 * endpoints whose semantic slots differ. Mutates matching features'
 * geometry in place and flags them with `joint_offset_taper_baked`.
 *
 * @param {Array<GeoJSON.Feature>} lanes  POST-bake bundle_lane features.
 * @param {object} [options]
 * @param {number} [options.snapMinM=1.5]  gaps below this are already flush.
 * @param {number} [options.snapMaxM=10]   gaps above this are real gaps for
 *   the bridge pass, not slot steps.
 * @param {number} [options.blendM=100]    warp length along the mover's tail.
 * @returns {{count: number, joints: Array<{routes: string, gapM: number, at: [number, number]}>}}
 */
export function taperBakedJointSteps(lanes, options = {}) {
  const { snapMinM = 1.5, snapMaxM = 10, blendM = 100 } = options;

  const entries = lanes
    .filter(
      (f) =>
        f.geometry?.type === "LineString" &&
        Array.isArray(f.geometry.coordinates) &&
        f.geometry.coordinates.length >= 2,
    )
    .map((f) => ({ feature: f, slot: semanticSlot(f), routeIds: f.properties?.route_ids ?? [] }));

  const joints = [];

  // One warp per mover endpoint, onto the NEAREST eligible target. Warping
  // greedily per pair re-stepped an already-flush endpoint whenever a joint
  // had more than one same-route neighbor in range.
  for (let i = 0; i < entries.length; i += 1) {
    const mover = entries[i];
    for (const endpointEnd of ["start", "end"]) {
      const mc = mover.feature.geometry.coordinates;
      const endpoint = endpointEnd === "start" ? mc[0] : mc[mc.length - 1];
      let best = null;
      for (let j = 0; j < entries.length; j += 1) {
        if (i === j) continue;
        const still = entries[j];
        // Only the more-offset lane moves; a slot-0 (or lower-offset)
        // neighbor stays put. Equal magnitudes have no defined mover.
        if (Math.abs(mover.slot) <= Math.abs(still.slot)) continue;
        if (!sharedRoute(mover.routeIds, still.routeIds)) continue;
        const sc = still.feature.geometry.coordinates;
        for (const target of [sc[0], sc[sc.length - 1]]) {
          const gap = distM(endpoint, target);
          if (gap < snapMinM || gap > snapMaxM) continue;
          if (!best || gap < best.gap) best = { gap, target };
        }
      }
      if (!best) continue;
      // An earlier pass may have stitched the step with a tiny 2-point
      // connector between exactly these endpoints; the warp makes it a
      // dangling whisker. Flag it for removal by the caller.
      for (const other of entries) {
        if (other === mover) continue;
        const oc = other.feature.geometry.coordinates;
        if (oc.length > 2) continue;
        if (!sharedRoute(mover.routeIds, other.routeIds)) continue;
        const matches =
          (distM(oc[0], endpoint) <= 1 && distM(oc[oc.length - 1], best.target) <= 1) ||
          (distM(oc[oc.length - 1], endpoint) <= 1 && distM(oc[0], best.target) <= 1);
        if (matches) {
          other.feature.properties = {
            ...other.feature.properties,
            joint_offset_taper_drop: true,
          };
        }
      }
      mover.feature.geometry = {
        type: "LineString",
        coordinates: warpTailToTarget(
          mover.feature.geometry.coordinates,
          endpointEnd === "start" ? 0 : mover.feature.geometry.coordinates.length - 1,
          best.target,
          blendM,
        ),
      };
      mover.feature.properties = {
        ...mover.feature.properties,
        joint_offset_taper_baked: true,
      };
      joints.push({
        routes: mover.routeIds.join(","),
        gapM: Number(best.gap.toFixed(2)),
        at: best.target,
      });
    }
  }

  return { count: joints.length, joints };
}
