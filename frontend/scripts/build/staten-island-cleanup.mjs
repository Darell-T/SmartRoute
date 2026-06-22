// Staten Island Railway cleanup.
//
// NYC OpenData shatters the SIR into ~40 fragments: the mainline chain plus
// second-track slivers, yard twigs, and overlapping weave pieces around
// St George / Tompkinsville. Rendered raw it reads as a mess of parallel
// dashes with visible seams (and the trim pass earlier mistook two of the
// micro-fragments for spurs). This authored pass reduces the SIR to one
// clean line:
//
//   1. Build an endpoint graph over the SI fragments and find the mainline
//      chain from Tottenville to St George (Dijkstra by length).
//   2. Keep the chain; bridge its small endpoint seams with straight
//      "si-stitch" connectors so the line reads continuous.
//   3. Drop everything else that is a parallel shadow of the kept chain or a
//      short dangling twig. Long genuinely-offset geometry survives.

const DEG_LAT_M = 111320;
const JOIN_M = 90; // endpoint cluster radius (largest observed seam: 82m)
const STITCH_MAX_M = 100; // bridge seams up to this length
const SHADOW_M = 45; // fragment entirely within this of the chain = shadow
const TWIG_MAX_M = 250; // non-chain fragments shorter than this = twig

const TOTTENVILLE = [-74.251, 40.5134];
const ST_GEORGE = [-74.0734, 40.6437];

function metersXY(coord, lat0) {
  return [coord[0] * DEG_LAT_M * Math.cos((lat0 * Math.PI) / 180), coord[1] * DEG_LAT_M];
}

function distM(a, b, lat0) {
  const [ax, ay] = metersXY(a, lat0);
  const [bx, by] = metersXY(b, lat0);
  return Math.hypot(ax - bx, ay - by);
}

function lineLength(coords, lat0) {
  let total = 0;
  for (let i = 1; i < coords.length; i += 1) total += distM(coords[i - 1], coords[i], lat0);
  return total;
}

function minDistToLine(point, coords, lat0) {
  const p = metersXY(point, lat0);
  let best = Infinity;
  for (let i = 1; i < coords.length; i += 1) {
    const a = metersXY(coords[i - 1], lat0);
    const b = metersXY(coords[i], lat0);
    const seg = Math.hypot(b[0] - a[0], b[1] - a[1]);
    let u = seg === 0 ? 0 :
      ((p[0] - a[0]) * (b[0] - a[0]) + (p[1] - a[1]) * (b[1] - a[1])) / (seg * seg);
    u = Math.min(Math.max(u, 0), 1);
    const d = Math.hypot(p[0] - (a[0] + u * (b[0] - a[0])), p[1] - (a[1] + u * (b[1] - a[1])));
    if (d < best) best = d;
  }
  return best;
}

export function cleanStatenIslandLine(features, options = {}) {
  const fromCoord = options.fromCoord ?? TOTTENVILLE;
  const toCoord = options.toCoord ?? ST_GEORGE;

  const siIdx = [];
  features.forEach((f, i) => {
    if (
      f?.geometry?.type === "LineString" &&
      (f.properties?.route_ids ?? []).includes("SI") &&
      f.geometry.coordinates.length >= 2
    ) {
      siIdx.push(i);
    }
  });
  if (siIdx.length < 2) return { kept: siIdx.length, dropped: 0, stitches: 0 };
  const lat0 = features[siIdx[0]].geometry.coordinates[0][1];

  // --- Endpoint nodes (clustered within JOIN_M) ---
  const nodes = [];
  const nodeOf = (coord) => {
    for (let n = 0; n < nodes.length; n += 1) {
      if (distM(nodes[n], coord, lat0) <= JOIN_M) return n;
    }
    nodes.push(coord);
    return nodes.length - 1;
  };
  const edges = siIdx.map((idx) => {
    const cs = features[idx].geometry.coordinates;
    return {
      idx,
      a: nodeOf(cs[0]),
      b: nodeOf(cs[cs.length - 1]),
      len: lineLength(cs, lat0),
    };
  });

  // --- Dijkstra from the node nearest fromCoord to the node nearest toCoord ---
  const nearestNode = (coord) => {
    let best = 0;
    let bestD = Infinity;
    nodes.forEach((n, i) => {
      const d = distM(n, coord, lat0);
      if (d < bestD) { bestD = d; best = i; }
    });
    return best;
  };
  const src = nearestNode(fromCoord);
  const dst = nearestNode(toCoord);
  const distArr = new Array(nodes.length).fill(Infinity);
  const prevEdge = new Array(nodes.length).fill(null);
  distArr[src] = 0;
  const visited = new Set();
  while (visited.size < nodes.length) {
    let u = -1;
    let uD = Infinity;
    for (let i = 0; i < nodes.length; i += 1) {
      if (!visited.has(i) && distArr[i] < uD) { uD = distArr[i]; u = i; }
    }
    if (u === -1) break;
    visited.add(u);
    for (const e of edges) {
      for (const [x, y] of [[e.a, e.b], [e.b, e.a]]) {
        if (x !== u) continue;
        if (distArr[u] + e.len < distArr[y]) {
          distArr[y] = distArr[u] + e.len;
          prevEdge[y] = { edge: e, from: u };
        }
      }
    }
  }

  if (!Number.isFinite(distArr[dst])) {
    // No connected chain between the terminals: do nothing (safety).
    return { kept: siIdx.length, dropped: 0, stitches: 0, connected: false };
  }

  const chain = new Set();
  const chainOrder = [];
  for (let n = dst; n !== src;) {
    const step = prevEdge[n];
    if (!step) break;
    chain.add(step.edge.idx);
    chainOrder.push(step.edge);
    n = step.from;
  }

  // --- Classify non-chain SI fragments ---
  const chainCoords = [...chain].map((idx) => features[idx].geometry.coordinates);
  const toDrop = new Set();
  for (const e of edges) {
    if (chain.has(e.idx)) continue;
    const cs = features[e.idx].geometry.coordinates;
    const isShadow = cs.every((c) =>
      chainCoords.some((cc) => minDistToLine(c, cc, lat0) <= SHADOW_M),
    );
    if (isShadow || e.len < TWIG_MAX_M) toDrop.add(e.idx);
  }

  // --- Stitch seams between consecutive chain fragments ---
  const stitchFeatures = [];
  chainOrder.forEach((edge, i) => {
    const next = chainOrder[i + 1];
    if (!next) return;
    // shared node between consecutive chain edges; find the actual endpoint
    // coords on each fragment closest to that node and bridge them if they
    // don't touch.
    const sharedNode = [edge.a, edge.b].find((n) => n === next.a || n === next.b);
    if (sharedNode == null) return;
    const endNear = (e) => {
      const cs = features[e.idx].geometry.coordinates;
      const candidates = [cs[0], cs[cs.length - 1]];
      return candidates.reduce((best, c) =>
        distM(c, nodes[sharedNode], lat0) < distM(best, nodes[sharedNode], lat0) ? c : best,
      );
    };
    const p1 = endNear(edge);
    const p2 = endNear(next);
    const gap = distM(p1, p2, lat0);
    if (gap > 5 && gap <= STITCH_MAX_M) {
      stitchFeatures.push({
        type: "Feature",
        properties: {
          corridor_id: `si-stitch-${stitchFeatures.length}`,
          route_ids: ["SI"],
          color_route_ids: ["SI"],
          color: "#0078C6",
          visual_feature_type: "bundle_lane",
          lane_slot: 0,
          lane_offset_baked: true,
          si_stitch: true,
          length_m: gap,
        },
        geometry: { type: "LineString", coordinates: [p1, p2] },
      });
    }
  });

  // Apply drops + stitches.
  for (let i = features.length - 1; i >= 0; i -= 1) {
    if (toDrop.has(i)) features.splice(i, 1);
  }
  features.push(...stitchFeatures);

  return {
    kept: siIdx.length - toDrop.size,
    dropped: toDrop.size,
    stitches: stitchFeatures.length,
    connected: true,
  };
}
