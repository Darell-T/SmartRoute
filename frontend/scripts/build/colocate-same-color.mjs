// Pure helper -- no fs, no globals. Pulls long parallel same-color lanes
// onto one shared track for the Apple one-ribbon-per-color look.
//
// On Queens Blvd the F express track runs ~18m from the F+M local track for
// ~5km. Both are MTA orange; Apple draws ONE orange ribbon there, but two
// distinct geometries 18m apart read as a clear double strand from ~z13.5 up.
// (Pairs ~6m apart -- Lex 4+5/4+6, 6th Av -- already fuse in paint and are
// deliberately left alone via minGapM.)
//
// For each same-color lane pair, the overlay lane is pulled onto its
// sibling wherever they run parallel within maxGapM for at least
// minStretchM, easing in/out over blendM so the departure points stay
// smooth. The carrier lane never moves. Overlay selection: fewer routes
// moves; on a tie (at this build stage the QB local lane is still [M] --
// F joins it later), the LONGER feature moves, keeping the ribbon on the
// short local alignment that owns the intermediate stations.

const M_PER_DEG_LAT = 110574;

function metersPerDegLng(lat) {
  return 111320 * Math.cos((lat * Math.PI) / 180);
}

function distM(a, b) {
  const k = metersPerDegLng((a[1] + b[1]) / 2);
  return Math.hypot((a[0] - b[0]) * k, (a[1] - b[1]) * M_PER_DEG_LAT);
}

// Nearest point on polyline (projected per-segment in local meters).
function nearestOnPolyline(point, coords) {
  const lat = point[1];
  const k = metersPerDegLng(lat);
  const px = point[0] * k;
  const py = point[1] * M_PER_DEG_LAT;
  let best = null;
  for (let i = 0; i < coords.length - 1; i += 1) {
    const ax = coords[i][0] * k;
    const ay = coords[i][1] * M_PER_DEG_LAT;
    const bx = coords[i + 1][0] * k;
    const by = coords[i + 1][1] * M_PER_DEG_LAT;
    const dx = bx - ax;
    const dy = by - ay;
    const len2 = dx * dx + dy * dy || 1e-12;
    const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / len2));
    const candidate = [
      coords[i][0] + (coords[i + 1][0] - coords[i][0]) * t,
      coords[i][1] + (coords[i + 1][1] - coords[i][1]) * t,
    ];
    const d = distM(point, candidate);
    if (!best || d < best.d) best = { d, point: candidate };
  }
  return best;
}

function ease(t) {
  const clamped = Math.max(0, Math.min(1, t));
  return clamped * clamped * (3 - 2 * clamped);
}

function median(values) {
  if (!values.length) return NaN;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.floor(sorted.length / 2)];
}

/**
 * Co-locate long parallel same-color stretches onto one track.
 *
 * Mutates mover geometry in place; flags movers with
 * `same_color_colocated: true`.
 *
 * @param {Array<GeoJSON.Feature>} lanes  bundle_lane features (post-bake).
 * @param {object} [options]
 * @param {number} [options.minGapM=10]   stretches whose median gap is below
 *   this already fuse in paint -- skip.
 * @param {number} [options.maxGapM=30]   beyond this the tracks genuinely
 *   diverge (Apple splits too).
 * @param {number} [options.minStretchM=500]  ignore short flirtations.
 * @param {number} [options.blendM=100]   ease length at stretch boundaries.
 * @returns {{count: number, stretches: Array<{routes: string, lengthM: number}>}}
 */
export function colocateSameColorStretches(lanes, options = {}) {
  const { minGapM = 10, maxGapM = 30, minStretchM = 500, blendM = 100 } = options;

  const entries = lanes
    .filter(
      (f) =>
        f.geometry?.type === "LineString" &&
        Array.isArray(f.geometry.coordinates) &&
        f.geometry.coordinates.length >= 2,
    )
    .map((f) => {
      const coords = f.geometry.coordinates;
      let lengthM = 0;
      for (let i = 1; i < coords.length; i += 1) lengthM += distM(coords[i - 1], coords[i]);
      return {
        feature: f,
        color: String(f.properties?.color ?? "").toUpperCase(),
        routeCount: (f.properties?.route_ids ?? []).length,
        lengthM,
      };
    });

  const stretches = [];

  for (let i = 0; i < entries.length; i += 1) {
    for (let j = 0; j < entries.length; j += 1) {
      if (i === j) continue;
      const mover = entries[i];
      const carrier = entries[j];
      if (!mover.color || mover.color !== carrier.color) continue;
      // The route-poorer lane is the overlay that gets pulled in. On a
      // route-count tie the longer feature moves (see module docstring);
      // exact ties in both are skipped (no defined overlay).
      const moverByRoutes = mover.routeCount < carrier.routeCount;
      const tieByLength =
        mover.routeCount === carrier.routeCount && mover.lengthM > carrier.lengthM;
      if (!moverByRoutes && !tieByLength) continue;

      const mc = mover.feature.geometry.coordinates;
      const cc = carrier.feature.geometry.coordinates;

      // Per-vertex projection onto the carrier.
      const proj = mc.map((v) => nearestOnPolyline(v, cc));

      // Cumulative arc length along the mover.
      const arc = [0];
      for (let v = 1; v < mc.length; v += 1) arc.push(arc[v - 1] + distM(mc[v - 1], mc[v]));

      // Maximal runs of vertices within maxGapM of the carrier.
      let runStart = null;
      const runs = [];
      for (let v = 0; v <= mc.length; v += 1) {
        const inRun = v < mc.length && proj[v] && proj[v].d <= maxGapM;
        if (inRun && runStart === null) runStart = v;
        if (!inRun && runStart !== null) {
          runs.push([runStart, v - 1]);
          runStart = null;
        }
      }

      for (const [from, to] of runs) {
        const lengthM = arc[to] - arc[from];
        if (lengthM < minStretchM) continue;
        const gaps = [];
        for (let v = from; v <= to; v += 1) gaps.push(proj[v].d);
        if (median(gaps) < minGapM) continue;

        // Pull each vertex toward its projection, easing over blendM from
        // both run boundaries (a boundary at a feature end gets no ease --
        // the lane terminates on the carrier).
        const startBoundaryEases = from > 0;
        const endBoundaryEases = to < mc.length - 1;
        for (let v = from; v <= to; v += 1) {
          let w = 1;
          if (startBoundaryEases) w = Math.min(w, ease((arc[v] - arc[from]) / blendM));
          if (endBoundaryEases) w = Math.min(w, ease((arc[to] - arc[v]) / blendM));
          if (w <= 0) continue;
          const target = proj[v].point;
          mc[v] = [
            mc[v][0] + (target[0] - mc[v][0]) * w,
            mc[v][1] + (target[1] - mc[v][1]) * w,
          ];
        }
        mover.feature.properties = {
          ...mover.feature.properties,
          same_color_colocated: true,
        };
        stretches.push({
          routes: (mover.feature.properties.route_ids ?? []).join(","),
          lengthM: Number(lengthM.toFixed(0)),
        });
      }
    }
  }

  return { count: stretches.length, stretches };
}
