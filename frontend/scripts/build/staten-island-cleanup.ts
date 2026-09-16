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

import type { LineStringGeometry, Position } from "./types.ts";

type StatenIslandProperties = {
  corridor_id?: string | null;
  route_ids?: string[];
  color_route_ids?: string[];
  color?: string;
  visual_feature_type?: string;
  lane_slot?: number;
  lane_offset_baked?: boolean;
  si_stitch?: boolean;
  length_m?: number;
};

type StatenIslandFeature = {
  type: "Feature";
  geometry: LineStringGeometry;
  properties: StatenIslandProperties;
};

type StatenIslandOptions = {
  fromCoord?: Position;
  toCoord?: Position;
};

type StatenIslandSummary = {
  kept: number;
  dropped: number;
  stitches: number;
  connected?: boolean;
};

type StatenEdge = {
  idx: number;
  a: number;
  b: number;
  len: number;
};

type PreviousStep = {
  edge: StatenEdge;
  from: number;
};

const DEG_LAT_M = 111320;
const JOIN_M = 90; // endpoint cluster radius (largest observed seam: 82m)
const STITCH_MAX_M = 100; // bridge seams up to this length
const SHADOW_M = 45; // fragment entirely within this of the chain = shadow
const TWIG_MAX_M = 250; // non-chain fragments shorter than this = twig

const TOTTENVILLE: Position = [-74.251, 40.5134];
const ST_GEORGE: Position = [-74.0734, 40.6437];

function metersXY(coord: Position, lat0: number): Position {
  return [coord[0] * DEG_LAT_M * Math.cos((lat0 * Math.PI) / 180), coord[1] * DEG_LAT_M];
}

function distM(a: Position, b: Position, lat0: number): number {
  const [ax, ay] = metersXY(a, lat0);
  const [bx, by] = metersXY(b, lat0);
  return Math.hypot(ax - bx, ay - by);
}

function lineLength(coords: Position[], lat0: number): number {
  let total = 0;
  for (let i = 1; i < coords.length; i += 1) total += distM(coords[i - 1], coords[i], lat0);
  return total;
}

function minDistToLine(point: Position, coords: Position[], lat0: number): number {
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

type SiGraph = {
  nodes: Position[];
  edges: StatenEdge[];
};

type SiPathSearch = {
  distArr: number[];
  prevEdge: Array<PreviousStep | null>;
};

type SiChain = {
  chain: Set<number>;
  chainOrder: StatenEdge[];
};

function collectSiIndexes(features: StatenIslandFeature[]): number[] {
  const siIdx: number[] = [];
  features.forEach((feature, i) => {
    if (feature.geometry?.type !== "LineString") return;
    if (!(feature.properties?.route_ids ?? []).includes("SI")) return;
    if (feature.geometry.coordinates.length < 2) return;
    siIdx.push(i);
  });
  return siIdx;
}

function clusterEndpointNode(nodes: Position[], coord: Position, lat0: number): number {
  for (let n = 0; n < nodes.length; n += 1) {
    if (distM(nodes[n], coord, lat0) <= JOIN_M) return n;
  }
  nodes.push(coord);
  return nodes.length - 1;
}

function buildSiEdges(features: StatenIslandFeature[], siIdx: number[], lat0: number): SiGraph {
  const nodes: Position[] = [];
  const edges = siIdx.map((idx) => {
    const cs = features[idx].geometry.coordinates;
    return {
      idx,
      a: clusterEndpointNode(nodes, cs[0], lat0),
      b: clusterEndpointNode(nodes, cs[cs.length - 1], lat0),
      len: lineLength(cs, lat0),
    };
  });
  return { nodes, edges };
}

function nearestNodeIndex(nodes: Position[], coord: Position, lat0: number): number {
  let best = 0;
  let bestD = Infinity;
  nodes.forEach((node, i) => {
    const d = distM(node, coord, lat0);
    if (d < bestD) {
      bestD = d;
      best = i;
    }
  });
  return best;
}

function shortestSiPath(
  nodes: Position[],
  edges: StatenEdge[],
  src: number,
  _dst: number,
): SiPathSearch {
  const distArr = Array.from({ length: nodes.length }, () => Infinity);
  const prevEdge: Array<PreviousStep | null> = Array.from({ length: nodes.length }, () => null);
  distArr[src] = 0;
  const visited = new Set<number>();
  while (visited.size < nodes.length) {
    let u = -1;
    let uD = Infinity;
    for (let i = 0; i < nodes.length; i += 1) {
      if (!visited.has(i) && distArr[i] < uD) {
        uD = distArr[i];
        u = i;
      }
    }
    if (u === -1) break;
    visited.add(u);
    for (const edge of edges) {
      for (const [x, y] of [[edge.a, edge.b], [edge.b, edge.a]]) {
        if (x !== u) continue;
        if (distArr[u] + edge.len >= distArr[y]) continue;
        distArr[y] = distArr[u] + edge.len;
        prevEdge[y] = { edge, from: u };
      }
    }
  }
  return { distArr, prevEdge };
}

function reconstructChain(
  prevEdge: Array<PreviousStep | null>,
  src: number,
  dst: number,
): SiChain {
  const chain = new Set<number>();
  const chainOrder: StatenEdge[] = [];
  for (let n = dst; n !== src;) {
    const step = prevEdge[n];
    if (!step) break;
    chain.add(step.edge.idx);
    chainOrder.push(step.edge);
    n = step.from;
  }
  return { chain, chainOrder };
}

function shadowAndTwigIndexes(
  features: StatenIslandFeature[],
  edges: StatenEdge[],
  chain: Set<number>,
  lat0: number,
): Set<number> {
  const chainCoords = [...chain].map((idx) => features[idx].geometry.coordinates);
  const toDrop = new Set<number>();
  for (const edge of edges) {
    if (chain.has(edge.idx)) continue;
    const cs = features[edge.idx].geometry.coordinates;
    const isShadow = cs.every((coord) =>
      chainCoords.some((line) => minDistToLine(coord, line, lat0) <= SHADOW_M),
    );
    if (isShadow || edge.len < TWIG_MAX_M) toDrop.add(edge.idx);
  }
  return toDrop;
}

function endNearSharedNode(
  features: StatenIslandFeature[],
  edge: StatenEdge,
  nodes: Position[],
  sharedNode: number,
  lat0: number,
): Position {
  const cs = features[edge.idx].geometry.coordinates;
  const candidates: Position[] = [cs[0], cs[cs.length - 1]];
  return candidates.reduce((best, coord) =>
    distM(coord, nodes[sharedNode], lat0) < distM(best, nodes[sharedNode], lat0) ? coord : best,
  );
}

function stitchChainSeams(
  features: StatenIslandFeature[],
  chainOrder: StatenEdge[],
  nodes: Position[],
  lat0: number,
): StatenIslandFeature[] {
  const stitchFeatures: StatenIslandFeature[] = [];
  chainOrder.forEach((edge, i) => {
    const next = chainOrder[i + 1];
    if (!next) return;
    const sharedNode = [edge.a, edge.b].find((n) => n === next.a || n === next.b);
    if (sharedNode == null) return;
    const p1 = endNearSharedNode(features, edge, nodes, sharedNode, lat0);
    const p2 = endNearSharedNode(features, next, nodes, sharedNode, lat0);
    const gap = distM(p1, p2, lat0);
    if (gap <= 5 || gap > STITCH_MAX_M) return;
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
  });
  return stitchFeatures;
}

export function cleanStatenIslandLine(
  features: StatenIslandFeature[],
  options: StatenIslandOptions = {},
): StatenIslandSummary {
  const fromCoord = options.fromCoord ?? TOTTENVILLE;
  const toCoord = options.toCoord ?? ST_GEORGE;
  const siIdx = collectSiIndexes(features);
  if (siIdx.length < 2) return { kept: siIdx.length, dropped: 0, stitches: 0 };
  const lat0 = features[siIdx[0]].geometry.coordinates[0][1];
  const { nodes, edges } = buildSiEdges(features, siIdx, lat0);
  const src = nearestNodeIndex(nodes, fromCoord, lat0);
  const dst = nearestNodeIndex(nodes, toCoord, lat0);
  const { distArr, prevEdge } = shortestSiPath(nodes, edges, src, dst);
  if (!Number.isFinite(distArr[dst])) {
    return { kept: siIdx.length, dropped: 0, stitches: 0, connected: false };
  }
  const { chain, chainOrder } = reconstructChain(prevEdge, src, dst);
  const toDrop = shadowAndTwigIndexes(features, edges, chain, lat0);
  const stitchFeatures = stitchChainSeams(features, chainOrder, nodes, lat0);
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
