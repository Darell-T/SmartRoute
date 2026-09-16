// Pure helper -- no fs, no globals. Closes the small seams left between
// consecutive pieces of the SAME route after the split-and-reassemble pipeline
// (corridors -> bundles -> shared-spine + fanouts + tails -> DeKalb clips).
//
// The materialization emits the shared spine from the BASE corridor's geometry
// but each member's fanout/tail from the MEMBER's geometry, and those differ by
// up to the overlap tolerance (~15 m). That leaves a small visible gap at every
// fanout/junction. Rather than re-architect the offset model, we close the gap
// at the right layer: ADD a short connector LineString from a dangling endpoint
// to the nearest vertex of another piece of the same route. This is purely
// additive -- it never moves or deletes existing geometry, so it cannot break a
// line. Connectors inherit the route's color and carry lane_slot 0 /
// lane_offset_baked so the runtime draws them with no extra offset.

import type { Feature, LineStringGeometry, Position } from "./types.ts";

type Vector = [number, number];
type RouteId = string;

type ColorRouteTable = Record<string, RouteId[]>;

type BridgeProperties = {
  color?: string;
  route_ids?: RouteId[];
  color_route_ids?: RouteId[] | ColorRouteTable;
  qa_orphan_severity?: string;
  route_gap_bridge?: boolean;
  route_gap_bridge_subset_connector?: boolean;
  route_gap_integrated?: boolean;
  route_gap_integrated_count?: number;
  route_gap_bridge_curved?: boolean;
  bridge_gap_m?: number;
  length_m?: number;
  visual_feature_type?: string;
  route_id?: RouteId;
  representative_route_id?: RouteId;
  bundle_materialization_role?: string | null;
  lane_slot?: number;
  render_lane_slot?: number;
  lane_offset_baked?: boolean;
};

type BridgeFeature = Feature<LineStringGeometry, BridgeProperties>;

type BridgeOptions = {
  minGapM?: number;
  maxGapM?: number;
  sampleM?: number;
  maxJoinTurnDeg?: number;
  curveSampleM?: number;
  allowSubsetRouteConnectors?: boolean;
  subsetConnectorMaxGapM?: number;
  subsetConnectorEndpointSnapM?: number;
};

type ProximityCandidate = {
  d: number;
  point: Position;
  index: number;
};

type ProjectedCandidate = ProximityCandidate & {
  t: number;
  coords: Position[];
};

type RepairTarget = ProximityCandidate & {
  t?: number;
  coords?: Position[];
  fj: BridgeFeature;
  samples: Position[];
  sharedRoutes: RouteId[];
  routeSubsetConnector: boolean;
};

type RepairCandidate = {
  sourceIndex: number;
  endpointIndex: number;
  endpoint: Position;
  targetPoint: Position;
  routeKey: string;
  midpoint: Position;
  distanceM: number;
  bridgeCoordinates: Position[];
  bridgeLengthM: number;
  shouldCurve: boolean;
  sharedRoutes: RouteId[];
  integrationMode: "append" | "integrate";
  sourceIsOrphan: boolean;
  debugFeature: BridgeFeature;
};

const EARTH_RADIUS_M = 6371000;
const M_PER_DEG_LAT = 110574;

function metersPerDegLng(lat: number): number {
  return 111320 * Math.cos((lat * Math.PI) / 180);
}

function haversineM([lon1, lat1]: Position, [lon2, lat2]: Position): number {
  const toRad = (d: number): number => (d * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(a));
}

function sharedRouteIds(aRouteIds: RouteId[], bRouteIds: RouteId[]): RouteId[] {
  const bSet = new Set(bRouteIds);
  return [...new Set(aRouteIds.filter((routeId) => bSet.has(routeId)))];
}

function routeSetKey(routeIds: RouteId[]): string {
  return [...new Set(routeIds)].sort().join("|");
}

function sameRouteSet(left: RouteId[], right: RouteId[]): boolean {
  return routeSetKey(left) === routeSetKey(right);
}

function sameColor(left: BridgeFeature, right: BridgeFeature): boolean {
  const leftColor = left.properties?.color;
  const rightColor = right.properties?.color;
  return Boolean(leftColor && rightColor && String(leftColor).toUpperCase() === String(rightColor).toUpperCase());
}

function midpoint(a: Position, b: Position): Position {
  return [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
}

function routeIdsFromList(value: RouteId[] | null | undefined): RouteId[] {
  if (!Array.isArray(value)) return [];
  return value.filter((routeId) => Boolean(routeId)).map(String);
}

function colorRouteEntries(value: ColorRouteTable): Array<[string, RouteId[]]> {
  const proto = Object.getPrototypeOf(value);
  if (proto !== Object.prototype && proto !== null) return [];
  const collected: Array<[string, RouteId[]]> = [];
  for (const [key, routes] of Object.entries(value)) {
    if (Array.isArray(routes)) collected.push([key, routes]);
  }
  return collected;
}

function routeIdsFromColorMap(colorRouteIds: ColorRouteTable, color: string | undefined): RouteId[] {
  const entries = colorRouteEntries(colorRouteIds);
  if (color != null) {
    const colorKey = String(color);
    for (const [key, routes] of entries) {
      if (key === colorKey) return routeIdsFromList(routes);
    }
  }
  const collected: RouteId[] = [];
  for (const [, routes] of entries) {
    collected.push(...routeIdsFromList(routes));
  }
  return [...new Set(collected.filter(Boolean))];
}

function activeRouteIdsForFeature(feature: BridgeFeature): RouteId[] {
  const properties = feature.properties ?? {};
  const colorRouteIds = properties.color_route_ids;
  if (Array.isArray(colorRouteIds)) return routeIdsFromList(colorRouteIds);
  if (colorRouteIds == null) return routeIdsFromList(properties.route_ids);
  const proto = Object.getPrototypeOf(colorRouteIds);
  if (proto === Object.prototype || proto === null) {
    return routeIdsFromColorMap(colorRouteIds, properties.color);
  }
  return routeIdsFromList(properties.route_ids);
}

// Downsample a polyline to roughly one point per stepM for proximity tests.
function sampleVertices(coords: Position[], stepM: number): Position[] {
  const out = [coords[0]];
  let acc = 0;
  for (let i = 1; i < coords.length; i += 1) {
    acc += haversineM(coords[i - 1], coords[i]);
    if (acc >= stepM || i === coords.length - 1) {
      out.push(coords[i]);
      acc = 0;
    }
  }
  return out;
}

function nearestVertex(point: Position, coords: Position[]): ProximityCandidate | null {
  let best: ProximityCandidate | null = null;
  for (let index = 0; index < coords.length; index += 1) {
    const c = coords[index];
    const d = haversineM(point, c);
    if (!best || d < best.d) best = { d, point: c, index };
  }
  return best;
}

function nearestPointOnPolyline(point: Position, coords: Position[]): ProjectedCandidate | null {
  const lat = point[1];
  const px = point[0] * metersPerDegLng(lat);
  const py = point[1] * M_PER_DEG_LAT;
  let best: ProjectedCandidate | null = null;
  for (let index = 0; index < coords.length - 1; index += 1) {
    const a = coords[index];
    const b = coords[index + 1];
    const ax = a[0] * metersPerDegLng(lat);
    const ay = a[1] * M_PER_DEG_LAT;
    const bx = b[0] * metersPerDegLng(lat);
    const by = b[1] * M_PER_DEG_LAT;
    const dx = bx - ax;
    const dy = by - ay;
    const len2 = dx * dx + dy * dy || 1e-12;
    const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / len2));
    const candidate: Position = [
      a[0] + (b[0] - a[0]) * t,
      a[1] + (b[1] - a[1]) * t,
    ];
    const d = haversineM(point, candidate);
    if (!best || d < best.d) {
      best = { d, point: candidate, index, t, coords };
    }
  }
  return best;
}

function vectorMeters(from: Position, to: Position): Vector {
  const lat = (from[1] + to[1]) / 2;
  return [(to[0] - from[0]) * metersPerDegLng(lat), (to[1] - from[1]) * M_PER_DEG_LAT];
}

function unitVector(from: Position, to: Position): Vector {
  const v = vectorMeters(from, to);
  const len = Math.hypot(v[0], v[1]);
  return len < 1e-9 ? [0, 0] : [v[0] / len, v[1] / len];
}

function normalizeVector(v: Vector): Vector {
  const len = Math.hypot(v[0], v[1]);
  return len < 1e-9 ? [0, 0] : [v[0] / len, v[1] / len];
}

function angleBetweenDeg(a: Vector, b: Vector): number {
  const aLen = Math.hypot(a[0], a[1]);
  const bLen = Math.hypot(b[0], b[1]);
  if (aLen === 0 || bLen === 0) return 180;
  const dot = (a[0] * b[0] + a[1] * b[1]) / (aLen * bLen);
  return (Math.acos(Math.max(-1, Math.min(1, dot))) * 180) / Math.PI;
}

function endpointContinuationVector(coords: Position[], endpointIndex: number): Vector {
  if (coords.length < 2) return [0, 0];
  if (endpointIndex === 0) {
    return vectorMeters(coords[1], coords[0]);
  }
  return vectorMeters(coords[coords.length - 2], coords[coords.length - 1]);
}

function vertexContinuationVectors(coords: Position[], index: number): Vector[] {
  if (coords.length < 2) return [[0, 0]];
  if (index <= 0) return [vectorMeters(coords[1], coords[0])];
  if (index >= coords.length - 1) return [vectorMeters(coords[coords.length - 2], coords[coords.length - 1])];
  return [vectorMeters(coords[index - 1], coords[index]), vectorMeters(coords[index + 1], coords[index])];
}

function minJoinAngleDeg(continuationVectors: Vector[], connectorVector: Vector): number {
  return Math.min(...continuationVectors.map((continuation) => angleBetweenDeg(continuation, connectorVector)));
}

function bestContinuationVector(continuationVectors: Vector[], referenceVector: Vector): Vector {
  let best = continuationVectors[0] ?? [0, 0];
  let bestAngle = Infinity;
  for (const continuation of continuationVectors) {
    const a = angleBetweenDeg(continuation, referenceVector);
    if (a < bestAngle) {
      bestAngle = a;
      best = continuation;
    }
  }
  return best;
}

function projectedContinuationVectors(candidate: ProjectedCandidate): Vector[] {
  const coords = candidate.coords;
  const index = candidate.index;
  if (!coords || coords.length < 2) return [[0, 0]];
  if (candidate.t <= 1e-6) {
    return [unitVector(candidate.point, coords[index + 1])];
  }
  if (candidate.t >= 1 - 1e-6) {
    return [unitVector(coords[index], candidate.point)];
  }
  return [
    unitVector(candidate.point, coords[index + 1]),
    unitVector(coords[index], candidate.point),
  ];
}

function projectedPointIsNearEndpoint(candidate: ProximityCandidate | null, coords: Position[], endpointSnapM: number): boolean {
  if (!candidate || !Array.isArray(coords) || coords.length < 2) return false;
  return (
    haversineM(candidate.point, coords[0]) <= endpointSnapM ||
    haversineM(candidate.point, coords[coords.length - 1]) <= endpointSnapM
  );
}

function projectAtLat(point: Position, originLat: number): Vector {
  return [point[0] * metersPerDegLng(originLat), point[1] * M_PER_DEG_LAT];
}

function unprojectAtLat(point: Vector, originLat: number): Position {
  return [point[0] / metersPerDegLng(originLat), point[1] / M_PER_DEG_LAT];
}

function hermiteConnector(start: Position, end: Position, startTangent: Vector, endTangent: Vector, sampleM = 5): Position[] {
  const originLat = (start[1] + end[1]) / 2;
  const p0 = projectAtLat(start, originLat);
  const p1 = projectAtLat(end, originLat);
  const distanceM = Math.hypot(p1[0] - p0[0], p1[1] - p0[1]);
  const handleM = Math.max(8, Math.min(45, distanceM * 0.8));
  const m0 = [startTangent[0] * handleM, startTangent[1] * handleM];
  const m1 = [endTangent[0] * handleM, endTangent[1] * handleM];
  const steps = Math.max(4, Math.ceil(distanceM / sampleM));
  const out: Position[] = [];
  for (let i = 0; i <= steps; i += 1) {
    const t = i / steps;
    const t2 = t * t;
    const t3 = t2 * t;
    const h00 = 2 * t3 - 3 * t2 + 1;
    const h10 = t3 - 2 * t2 + t;
    const h01 = -2 * t3 + 3 * t2;
    const h11 = t3 - t2;
    out.push(unprojectAtLat([
      h00 * p0[0] + h10 * m0[0] + h01 * p1[0] + h11 * m1[0],
      h00 * p0[1] + h10 * m0[1] + h01 * p1[1] + h11 * m1[1],
    ], originLat));
  }
  return out;
}

function polylineLengthM(coords: Position[]): number {
  let total = 0;
  for (let i = 1; i < coords.length; i += 1) total += haversineM(coords[i - 1], coords[i]);
  return total;
}

type ResolvedBridgeOptions = {
  minGapM: number;
  maxGapM: number;
  sampleM: number;
  maxJoinTurnDeg: number;
  curveSampleM: number;
  allowSubsetRouteConnectors: boolean;
  subsetConnectorMaxGapM: number;
  subsetConnectorEndpointSnapM: number;
};

function resolveBridgeOptions(options: BridgeOptions): ResolvedBridgeOptions {
  const maxGapM = options.maxGapM ?? 28;
  return {
    minGapM: options.minGapM ?? 6,
    maxGapM,
    sampleM: options.sampleM ?? 6,
    maxJoinTurnDeg: options.maxJoinTurnDeg ?? 60,
    curveSampleM: options.curveSampleM ?? 5,
    allowSubsetRouteConnectors: options.allowSubsetRouteConnectors ?? false,
    subsetConnectorMaxGapM: options.subsetConnectorMaxGapM ?? maxGapM,
    subsetConnectorEndpointSnapM: options.subsetConnectorEndpointSnapM ?? 12,
  };
}

function isSubsetRouteConnector(
  allowSubsetRouteConnectors: boolean,
  routeSafeIntegratedRepair: boolean,
  sharedRoutes: RouteId[],
  targetRoutes: RouteId[],
  source: BridgeFeature,
  target: BridgeFeature,
  near: ProximityCandidate,
  endpointSnapM: number,
): boolean {
  return (
    allowSubsetRouteConnectors &&
    !routeSafeIntegratedRepair &&
    !sameRouteSet(sharedRoutes, targetRoutes) &&
    sameColor(source, target) &&
    projectedPointIsNearEndpoint(near, target.geometry.coordinates, endpointSnapM)
  );
}

function considerTargetLine(
  source: BridgeFeature,
  endpoint: Position,
  target: BridgeFeature,
  targetSamples: Position[],
  sourceRoutes: RouteId[],
  options: ResolvedBridgeOptions,
  current: RepairTarget | null,
): { kind: "skip" } | { kind: "connected" } | { kind: "candidate"; candidate: RepairTarget } {
  if (target.properties.qa_orphan_severity === "error") return { kind: "skip" };
  const targetRoutes = activeRouteIdsForFeature(target);
  const sharedRoutes = sharedRouteIds(sourceRoutes, targetRoutes);
  if (sharedRoutes.length === 0) return { kind: "skip" };
  const near = nearestPointOnPolyline(endpoint, target.geometry.coordinates) ?? nearestVertex(endpoint, targetSamples);
  if (!near) return { kind: "skip" };
  const routeSafeIntegratedRepair = sameRouteSet(sharedRoutes, sourceRoutes);
  const routeSubsetConnector = isSubsetRouteConnector(
    options.allowSubsetRouteConnectors,
    routeSafeIntegratedRepair,
    sharedRoutes,
    targetRoutes,
    source,
    target,
    near,
    options.subsetConnectorEndpointSnapM,
  );
  if (!routeSafeIntegratedRepair && !routeSubsetConnector) return { kind: "skip" };
  if (near.d <= options.minGapM) return { kind: "connected" };
  const candidateMaxGapM = routeSubsetConnector ? options.subsetConnectorMaxGapM : options.maxGapM;
  if (near.d > candidateMaxGapM || (current && near.d >= current.d)) return { kind: "skip" };
  return {
    kind: "candidate",
    candidate: { ...near, fj: target, samples: targetSamples, sharedRoutes, routeSubsetConnector },
  };
}

function bridgeDebugFeature(
  source: BridgeFeature,
  candidate: RepairTarget,
  bridgeCoordinates: Position[],
  shouldCurve: boolean,
  bridgeLengthM: number,
): BridgeFeature {
  return {
    type: "Feature",
    geometry: { type: "LineString", coordinates: bridgeCoordinates },
    properties: {
      ...source.properties,
      visual_feature_type: "route_gap_bridge",
      route_id: candidate.sharedRoutes[0],
      representative_route_id: candidate.sharedRoutes[0],
      route_ids: candidate.sharedRoutes,
      color_route_ids: candidate.sharedRoutes,
      route_gap_bridge: true,
      route_gap_bridge_subset_connector: candidate.routeSubsetConnector,
      bridge_gap_m: Number(candidate.d.toFixed(2)),
      route_gap_bridge_curved: shouldCurve,
      length_m: Number(bridgeLengthM.toFixed(2)),
      bundle_materialization_role: null,
      lane_slot: 0,
      render_lane_slot: 0,
      lane_offset_baked: true,
    },
  };
}

function repairCandidateFromEndpoint(
  source: BridgeFeature,
  sourceIndex: number,
  endpointIndex: number,
  endpoint: Position,
  candidate: RepairTarget,
  options: ResolvedBridgeOptions,
): RepairCandidate {
  const sourceContinuation = endpointContinuationVector(source.geometry.coordinates, endpointIndex);
  const connectorVector = vectorMeters(endpoint, candidate.point);
  const candidateVectors = candidate.coords && candidate.t != null
    ? projectedContinuationVectors({ ...candidate, coords: candidate.coords, t: candidate.t })
    : vertexContinuationVectors(candidate.samples, candidate.index);
  const shouldCurve =
    angleBetweenDeg(sourceContinuation, connectorVector) > options.maxJoinTurnDeg ||
    minJoinAngleDeg(candidateVectors, connectorVector) > options.maxJoinTurnDeg;
  const bridgeCoordinates = shouldCurve
    ? hermiteConnector(
      endpoint,
      candidate.point,
      normalizeVector(sourceContinuation),
      bestContinuationVector(candidateVectors, connectorVector),
      options.curveSampleM,
    )
    : [endpoint, candidate.point];
  const bridgeLengthM = polylineLengthM(bridgeCoordinates);
  return {
    sourceIndex,
    endpointIndex,
    endpoint,
    targetPoint: candidate.point,
    routeKey: routeSetKey(candidate.sharedRoutes),
    midpoint: midpoint(endpoint, candidate.point),
    distanceM: candidate.d,
    bridgeCoordinates,
    bridgeLengthM,
    shouldCurve,
    sharedRoutes: candidate.sharedRoutes,
    integrationMode: candidate.routeSubsetConnector ? "append" : "integrate",
    sourceIsOrphan: source.properties.qa_orphan_severity === "error",
    debugFeature: bridgeDebugFeature(source, candidate, bridgeCoordinates, shouldCurve, bridgeLengthM),
  };
}

function collectRepairCandidates(
  lines: BridgeFeature[],
  samples: Position[][],
  options: ResolvedBridgeOptions,
): RepairCandidate[] {
  const repairCandidates: RepairCandidate[] = [];
  for (let i = 0; i < lines.length; i += 1) {
    const source = lines[i];
    const coords = source.geometry.coordinates;
    const sourceRoutes = activeRouteIdsForFeature(source);
    const endpoints: Array<[number, Position]> = [
      [0, coords[0]],
      [coords.length - 1, coords[coords.length - 1]],
    ];
    for (const [endpointIndex, endpoint] of endpoints) {
      let candidate: RepairTarget | null = null;
      let connected = false;
      for (let j = 0; j < lines.length; j += 1) {
        if (j === i) continue;
        const hit = considerTargetLine(source, endpoint, lines[j], samples[j], sourceRoutes, options, candidate);
        if (hit.kind === "connected") {
          connected = true;
          break;
        }
        if (hit.kind === "candidate") candidate = hit.candidate;
      }
      if (connected || !candidate) continue;
      repairCandidates.push(repairCandidateFromEndpoint(source, i, endpointIndex, endpoint, candidate, options));
    }
  }
  return repairCandidates;
}

function selectAcceptedRepairs(repairCandidates: RepairCandidate[], maxGapM: number): RepairCandidate[] {
  const ranked = [...repairCandidates].sort((left, right) => {
    if (left.sourceIsOrphan !== right.sourceIsOrphan) return left.sourceIsOrphan ? -1 : 1;
    return left.distanceM - right.distanceM;
  });
  const acceptedRepairs: RepairCandidate[] = [];
  const usedEndpoints = new Set<string>();
  for (const candidate of ranked) {
    const endpointKey = `${candidate.sourceIndex}:${candidate.endpointIndex}`;
    if (usedEndpoints.has(endpointKey)) continue;
    const overlapsExistingSeam = acceptedRepairs.some((existing) => (
      existing.routeKey === candidate.routeKey &&
      haversineM(existing.midpoint, candidate.midpoint) <= maxGapM
    ));
    if (overlapsExistingSeam) continue;
    usedEndpoints.add(endpointKey);
    acceptedRepairs.push(candidate);
  }
  return acceptedRepairs;
}

function applyIntegratedRepairs(
  features: BridgeFeature[],
  lines: BridgeFeature[],
  acceptedRepairs: RepairCandidate[],
): BridgeFeature[] {
  const repairsByLine = new Map<number, { start?: RepairCandidate; end?: RepairCandidate }>();
  for (const repair of acceptedRepairs) {
    if (repair.integrationMode === "append") continue;
    const current = repairsByLine.get(repair.sourceIndex) ?? {};
    if (repair.endpointIndex === 0) current.start = repair;
    else current.end = repair;
    repairsByLine.set(repair.sourceIndex, current);
  }
  const lineIndexByFeature = new Map(lines.map((feature, index) => [feature, index]));
  return features.map((feature) => {
    const lineIndex = lineIndexByFeature.get(feature);
    const repairs = lineIndex == null ? undefined : repairsByLine.get(lineIndex);
    if (!repairs) return feature;
    let coordinates = feature.geometry.coordinates;
    const repairList = [repairs.start, repairs.end].filter((repair): repair is RepairCandidate => Boolean(repair));
    if (repairs.start) {
      coordinates = [...repairs.start.bridgeCoordinates.slice().reverse(), ...coordinates.slice(1)];
    }
    if (repairs.end) {
      coordinates = [...coordinates.slice(0, -1), ...repairs.end.bridgeCoordinates];
    }
    return {
      ...feature,
      geometry: { ...feature.geometry, coordinates },
      properties: {
        ...feature.properties,
        route_gap_integrated: true,
        route_gap_integrated_count: repairList.length,
        route_gap_bridge_curved: repairList.some((repair) => repair.shouldCurve),
        bridge_gap_m: Number(Math.max(...repairList.map((repair) => repair.distanceM)).toFixed(2)),
        length_m: Number(polylineLengthM(coordinates).toFixed(2)),
      },
    };
  });
}

/**
 * Bridge small same-route gaps with additive connector features.
 *
 * @param {Array<GeoJSON.Feature>} features  LineString features (others passed through untouched)
 * @param {object} [options]
 * @param {number} [options.minGapM=6]  endpoints closer than this are already joined -> skip
 * @param {number} [options.maxGapM=28] only bridge gaps up to this (avoids chord-cutting real gaps)
 * @param {number} [options.sampleM=6]  proximity-sampling resolution
 * @param {boolean} [options.allowSubsetRouteConnectors=false]
 *   When true, same-color broad branch splits may add an exact shared-route
 *   connector feature instead of extending either broad feature in place.
 * @returns {{ features: Array, bridgeCount: number, bridges: Array }}
 *          features = original features + appended connectors
 */
export function bridgeRouteGaps(features: BridgeFeature[], options: BridgeOptions = {}) {
  const resolved = resolveBridgeOptions(options);
  const lines = features.filter(
    (feature) => feature.geometry?.type === "LineString" && Array.isArray(feature.geometry.coordinates) && feature.geometry.coordinates.length >= 2,
  );
  const samples = lines.map((feature) => sampleVertices(feature.geometry.coordinates, resolved.sampleM));
  const acceptedRepairs = selectAcceptedRepairs(
    collectRepairCandidates(lines, samples, resolved),
    resolved.maxGapM,
  );
  const repairedFeatures = applyIntegratedRepairs(features, lines, acceptedRepairs);
  const bridges: BridgeFeature[] = acceptedRepairs.map((repair, index) => ({
    ...repair.debugFeature,
    properties: {
      ...repair.debugFeature.properties,
      corridor_id: `gap-bridge-${index}`,
      bundle_id: `gap-bridge-${index}`,
    },
  }));
  const appendedConnectors = bridges.filter((bridge) => bridge.properties.route_gap_bridge_subset_connector);
  return { features: [...repairedFeatures, ...appendedConnectors], bridgeCount: acceptedRepairs.length, bridges };
}
