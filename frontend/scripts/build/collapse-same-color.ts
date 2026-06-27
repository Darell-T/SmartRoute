// Pure helper -- no fs, no globals.
//
// Collapse SAME-COLOR visual overlaps by route runs, not by whole feature.
// When one yellow/orange/green/etc. service runs on top of another for only part
// of its geometry, the shared run becomes one route-unioned visual feature and
// the diverging tails keep their original route ids. This avoids two failure
// modes from the old helper:
// - duplicate same-color lanes remaining visible on the same corridor
// - route ids being unioned onto an entire trunk when they only share a segment

import type { Feature, LineStringGeometry, Position, RouteId } from "./types.ts";

type CollapseFeatureProperties = {
  corridor_id?: unknown;
  segment_id?: unknown;
  id?: unknown;
  route_id?: unknown;
  route_ids?: RouteId[];
  color?: unknown;
  color_route_ids?: unknown;
  representative_route_id?: unknown;
  length_m?: number;
  qa_orphan_origin?: unknown;
  qa_orphan_from_is_terminal?: unknown;
  qa_orphan_to_is_terminal?: unknown;
  qa_orphan_severity?: unknown;
  same_color_tail?: boolean;
  same_color_tail_source_corridor_id?: string;
  same_color_shared_run?: boolean;
  same_color_shared_target_corridor_id?: string;
  same_color_shared_source_corridor_id?: string;
  same_color_shared_start_m?: number;
  same_color_shared_end_m?: number;
  same_color_target_tail?: boolean;
  same_color_collapsed_representative?: boolean;
  lane_slot_source?: unknown;
  [key: string]: unknown;
};

type CollapseFeature = Feature<LineStringGeometry, CollapseFeatureProperties>;

type CollapseOptions = {
  collapseDistM?: number;
  minOverlapM?: number;
};

type TargetMeta = {
  arcs: number[];
  lengthM: number;
};

type Projection = {
  point: Position;
  distM: number;
  arcM: number;
};

type TargetProjection = Projection & {
  target: CollapseFeature;
};

type OverlapRun = {
  source: CollapseFeature;
  target: CollapseFeature;
  sourceStartArc: number;
  sourceEndArc: number;
  sourceRunLengthM: number;
  targetStartArc: number;
  targetEndArc: number;
  sourceStartIndex: number;
  sourceEndIndex: number;
};

type ArcInterval = {
  startArc: number;
  endArc: number;
};

type CollapseResult = {
  features: CollapseFeature[];
  collapsedCount: number;
};

const EARTH_RADIUS_M = 6371000;
const M_PER_DEG_LAT = 110574;
const DUPLICATE_POINT_EPS_M = 0.2;
const MIN_TAIL_LENGTH_M = 120;
// Do not carve a same-color SHARED run shorter than this. A short overlap (e.g.
// the F/M briefly touching on a 6 Av approach) carved into its own route-unioned
// feature renders as a disconnected stub: it sits on the target centerline while
// the flanking same-route tails sit elsewhere. Below this length we simply leave
// the two same-color lines overlapping (visually one line) instead of carving.
const MIN_SHARED_RUN_M = 300;

const ROUTE_ORDER = [
  "1",
  "2",
  "3",
  "4",
  "5",
  "6",
  "6X",
  "7",
  "7X",
  "A",
  "B",
  "C",
  "D",
  "E",
  "F",
  "FX",
  "G",
  "J",
  "Z",
  "L",
  "M",
  "N",
  "Q",
  "R",
  "W",
  "S",
  "FS",
  "GS",
  "H",
  "SI",
];

function routeRank(routeId: unknown): number {
  const index = ROUTE_ORDER.indexOf(String(routeId));
  return index === -1 ? ROUTE_ORDER.length + String(routeId).charCodeAt(0) : index;
}

function sortRouteIds(routeIds: unknown[]): RouteId[] {
  return [...new Set(routeIds.map(String))].sort(
    (left, right) => routeRank(left) - routeRank(right) || left.localeCompare(right, "en", { numeric: true }),
  );
}

function metersPerDegLng(lat: number): number {
  return 111320 * Math.cos((lat * Math.PI) / 180);
}

function haversineM([lon1, lat1]: Position, [lon2, lat2]: Position): number {
  const r = Math.PI / 180;
  const dLat = (lat2 - lat1) * r;
  const dLon = (lon2 - lon1) * r;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(a));
}

function cumulativeArcs(coords: Position[]): number[] {
  const arcs = [0];
  for (let index = 1; index < coords.length; index += 1) {
    arcs.push(arcs[index - 1] + haversineM(coords[index - 1], coords[index]));
  }
  return arcs;
}

function lengthM(coords: Position[]): number {
  const arcs = cumulativeArcs(coords);
  return arcs[arcs.length - 1] ?? 0;
}

function clonePosition(position: Position): Position {
  return [position[0], position[1]];
}

function interpolateAtArc(coords: Position[], arcs: number[], arcM: number): Position | null {
  if (coords.length === 0) return null;
  if (arcM <= 0) return clonePosition(coords[0]);
  const total = arcs[arcs.length - 1] ?? 0;
  if (arcM >= total) return clonePosition(coords[coords.length - 1]);

  for (let index = 1; index < arcs.length; index += 1) {
    if (arcs[index] < arcM) continue;
    const prevArc = arcs[index - 1];
    const nextArc = arcs[index];
    const span = nextArc - prevArc || 1e-9;
    const t = (arcM - prevArc) / span;
    const previous = coords[index - 1];
    const next = coords[index];
    return [
      previous[0] + (next[0] - previous[0]) * t,
      previous[1] + (next[1] - previous[1]) * t,
    ];
  }

  return clonePosition(coords[coords.length - 1]);
}

function appendCoord(out: Position[], coord: Position | null): void {
  if (!coord) return;
  const previous = out[out.length - 1];
  if (previous && haversineM(previous, coord) <= DUPLICATE_POINT_EPS_M) return;
  out.push(clonePosition(coord));
}

function sliceByArc(coords: Position[], startArcM: number, endArcM: number): Position[] {
  const arcs = cumulativeArcs(coords);
  const total = arcs[arcs.length - 1] ?? 0;
  const start = Math.max(0, Math.min(total, startArcM));
  const end = Math.max(0, Math.min(total, endArcM));
  if (end - start <= DUPLICATE_POINT_EPS_M) return [];

  const out: Position[] = [];
  appendCoord(out, interpolateAtArc(coords, arcs, start));
  for (let index = 1; index < coords.length - 1; index += 1) {
    if (arcs[index] > start + DUPLICATE_POINT_EPS_M && arcs[index] < end - DUPLICATE_POINT_EPS_M) {
      appendCoord(out, coords[index]);
    }
  }
  appendCoord(out, interpolateAtArc(coords, arcs, end));
  return out.length >= 2 ? out : [];
}

// Nearest point on a polyline to p; returns { point, distM, arcM }.
function projectToPolyline(coords: Position[], arcs: number[], p: Position): Projection | null {
  let best: Projection | null = null;
  const mPerLng = metersPerDegLng(p[1]);
  const px = p[0] * mPerLng;
  const py = p[1] * M_PER_DEG_LAT;

  for (let index = 0; index < coords.length - 1; index += 1) {
    const a = coords[index];
    const b = coords[index + 1];
    const ax = a[0] * mPerLng;
    const ay = a[1] * M_PER_DEG_LAT;
    const bx = b[0] * mPerLng;
    const by = b[1] * M_PER_DEG_LAT;
    const dx = bx - ax;
    const dy = by - ay;
    const len2 = dx * dx + dy * dy || 1e-12;
    const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / len2));
    const point: Position = [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
    const distM = haversineM(point, p);
    const arcM = arcs[index] + ((arcs[index + 1] ?? arcs[index]) - arcs[index]) * t;
    if (!best || distM < best.distM) best = { point, distM, arcM };
  }

  return best;
}

function corridorId(feature: CollapseFeature): string {
  return String(feature.properties?.corridor_id ?? feature.properties?.segment_id ?? feature.properties?.id ?? "unknown");
}

function featureRouteIds(feature: CollapseFeature): RouteId[] {
  return Array.isArray(feature.properties?.route_ids) ? feature.properties.route_ids.map(String) : [];
}

function featureRouteRank(feature: CollapseFeature): number {
  const ranks = featureRouteIds(feature).map(routeRank);
  return ranks.length === 0 ? Number.MAX_SAFE_INTEGER : Math.min(...ranks);
}

function unionRoutes(...routeLists: unknown[][]): RouteId[] {
  return sortRouteIds(routeLists.flat().filter(Boolean));
}

function mergedColorRouteIds(target: CollapseFeature, source: CollapseFeature, routeIds: RouteId[]): RouteId[] {
  const targetColorRoutes = target.properties?.color_route_ids;
  const sourceColorRoutes = source.properties?.color_route_ids;
  if (Array.isArray(targetColorRoutes) || Array.isArray(sourceColorRoutes)) {
    return unionRoutes(
      Array.isArray(targetColorRoutes) ? targetColorRoutes : featureRouteIds(target),
      Array.isArray(sourceColorRoutes) ? sourceColorRoutes : featureRouteIds(source),
      routeIds,
    );
  }
  return routeIds;
}

function clearStaleOrphanQa(properties: CollapseFeatureProperties): CollapseFeatureProperties {
  const next = { ...properties };
  delete next.qa_orphan_origin;
  delete next.qa_orphan_from_is_terminal;
  delete next.qa_orphan_to_is_terminal;
  delete next.qa_orphan_severity;
  return next;
}

function cloneWithGeometry(
  feature: CollapseFeature,
  coords: Position[],
  properties: CollapseFeatureProperties,
): CollapseFeature {
  return {
    ...feature,
    geometry: { type: "LineString", coordinates: coords },
    properties: clearStaleOrphanQa({
      ...feature.properties,
      ...properties,
      length_m: lengthM(coords),
    }),
  };
}

function buildHits(
  source: CollapseFeature,
  targets: CollapseFeature[],
  collapseDistM: number,
  targetMeta: Map<CollapseFeature, TargetMeta>,
): Array<TargetProjection | null> {
  return source.geometry.coordinates.map((point) => {
    let best: TargetProjection | null = null;
    for (const target of targets) {
      const meta = targetMeta.get(target);
      if (!meta) continue;
      const projection = projectToPolyline(target.geometry.coordinates, meta.arcs, point);
      if (projection && projection.distM <= collapseDistM && (!best || projection.distM < best.distM)) {
        best = { ...projection, target };
      }
    }
    return best;
  });
}

function findOverlapRuns(
  source: CollapseFeature,
  hits: Array<TargetProjection | null>,
  minOverlapM: number,
  sourceArcs: number[],
): OverlapRun[] {
  const runs: OverlapRun[] = [];
  let index = 0;

  while (index < hits.length) {
    const firstHit = hits[index];
    if (!firstHit) {
      index += 1;
      continue;
    }

    const target = firstHit.target;
    let endIndex = index;
    while (endIndex + 1 < hits.length && hits[endIndex + 1]?.target === target) {
      endIndex += 1;
    }

    const sourceStartArc = sourceArcs[index];
    const sourceEndArc = sourceArcs[endIndex];
    const sourceRunLengthM = sourceEndArc - sourceStartArc;
    const targetArcs: number[] = [];
    for (let hitIndex = index; hitIndex <= endIndex; hitIndex += 1) {
      const hit = hits[hitIndex];
      if (hit) targetArcs.push(hit.arcM);
    }
    const targetStartArc = Math.min(...targetArcs);
    const targetEndArc = Math.max(...targetArcs);

    if (sourceRunLengthM >= minOverlapM && targetEndArc - targetStartArc >= minOverlapM * 0.75) {
      runs.push({
        source,
        target,
        sourceStartArc,
        sourceEndArc,
        sourceRunLengthM,
        targetStartArc,
        targetEndArc,
        sourceStartIndex: index,
        sourceEndIndex: endIndex,
      });
    }

    index = endIndex + 1;
  }

  return runs;
}

function mergeIntervals(intervals: ArcInterval[]): ArcInterval[] {
  const sorted = intervals
    .filter((interval) => interval.endArc - interval.startArc > DUPLICATE_POINT_EPS_M)
    .sort((left, right) => left.startArc - right.startArc || left.endArc - right.endArc);
  const merged: ArcInterval[] = [];

  for (const interval of sorted) {
    const previous = merged[merged.length - 1];
    if (!previous || interval.startArc > previous.endArc + DUPLICATE_POINT_EPS_M) {
      merged.push({ ...interval });
    } else {
      previous.endArc = Math.max(previous.endArc, interval.endArc);
    }
  }

  return merged;
}

function buildSourceTails(
  source: CollapseFeature,
  acceptedRuns: OverlapRun[],
  sourceLengthM: number,
): CollapseFeature[] {
  const intervals = mergeIntervals(
    acceptedRuns.map((run) => ({ startArc: run.sourceStartArc, endArc: run.sourceEndArc })),
  );
  const tails: CollapseFeature[] = [];
  let cursor = 0;
  let part = 0;

  for (const interval of intervals) {
    if (interval.startArc - cursor >= MIN_TAIL_LENGTH_M) {
      const coords = sliceByArc(source.geometry.coordinates, cursor, interval.startArc);
      if (lengthM(coords) >= MIN_TAIL_LENGTH_M) {
        part += 1;
        tails.push(
          cloneWithGeometry(source, coords, {
            corridor_id: `${corridorId(source)}-tail-${part}`,
            same_color_tail: true,
            same_color_tail_source_corridor_id: corridorId(source),
          }),
        );
      }
    }
    cursor = Math.max(cursor, interval.endArc);
  }

  if (sourceLengthM - cursor >= MIN_TAIL_LENGTH_M) {
    const coords = sliceByArc(source.geometry.coordinates, cursor, sourceLengthM);
    if (lengthM(coords) >= MIN_TAIL_LENGTH_M) {
      part += 1;
      tails.push(
        cloneWithGeometry(source, coords, {
          corridor_id: `${corridorId(source)}-tail-${part}`,
          same_color_tail: true,
          same_color_tail_source_corridor_id: corridorId(source),
        }),
      );
    }
  }

  return tails;
}

function buildTailsForIntervals(
  feature: CollapseFeature,
  intervalsToRemove: ArcInterval[],
  totalLengthM: number,
  propertiesForTail: CollapseFeatureProperties,
): CollapseFeature[] {
  const intervals = mergeIntervals(intervalsToRemove);
  const tails: CollapseFeature[] = [];
  let cursor = 0;
  let part = 0;

  for (const interval of intervals) {
    if (interval.startArc - cursor >= MIN_TAIL_LENGTH_M) {
      const coords = sliceByArc(feature.geometry.coordinates, cursor, interval.startArc);
      if (lengthM(coords) >= MIN_TAIL_LENGTH_M) {
        part += 1;
        tails.push(
          cloneWithGeometry(feature, coords, {
            ...propertiesForTail,
            corridor_id: `${corridorId(feature)}-tail-${part}`,
          }),
        );
      }
    }
    cursor = Math.max(cursor, interval.endArc);
  }

  if (totalLengthM - cursor >= MIN_TAIL_LENGTH_M) {
    const coords = sliceByArc(feature.geometry.coordinates, cursor, totalLengthM);
    if (lengthM(coords) >= MIN_TAIL_LENGTH_M) {
      part += 1;
      tails.push(
        cloneWithGeometry(feature, coords, {
          ...propertiesForTail,
          corridor_id: `${corridorId(feature)}-tail-${part}`,
        }),
      );
    }
  }

  return tails;
}

function buildSharedRunFeature(run: OverlapRun, index: number): CollapseFeature | null {
  const targetCoords = run.target.geometry.coordinates;
  const coords = sliceByArc(targetCoords, run.targetStartArc, run.targetEndArc);
  if (coords.length < 2) return null;
  const routeIds = unionRoutes(featureRouteIds(run.target), featureRouteIds(run.source));
  return cloneWithGeometry(run.target, coords, {
    corridor_id: `${corridorId(run.target)}-same-color-shared-${corridorId(run.source)}-${index}`,
    route_ids: routeIds,
    color_route_ids: mergedColorRouteIds(run.target, run.source, routeIds),
    representative_route_id: run.target.properties?.representative_route_id ?? routeIds[0],
    route_id: run.target.properties?.route_id ?? routeIds[0],
    same_color_shared_run: true,
    same_color_shared_target_corridor_id: corridorId(run.target),
    same_color_shared_source_corridor_id: corridorId(run.source),
    same_color_shared_start_m: run.targetStartArc,
    same_color_shared_end_m: run.targetEndArc,
    lane_slot_source: run.target.properties?.lane_slot_source ?? "same_color_merge",
  });
}

function isFullFeatureCollapse(run: OverlapRun, sourceLengthM: number, targetLengthM: number): boolean {
  const sourceCoverage = sourceLengthM > 0 ? run.sourceRunLengthM / sourceLengthM : 0;
  const targetCoverage = targetLengthM > 0 ? (run.targetEndArc - run.targetStartArc) / targetLengthM : 0;
  return sourceCoverage >= 0.9 && targetCoverage >= 0.9;
}

/**
 * Collapse same-color overlaps (run-based, partial-length aware).
 *
 * @param {Array} features
 * @param {object} [options]
 * @param {number} [options.collapseDistM=12]
 * @param {number} [options.minOverlapM=120]
 * @returns {{ features: Array, collapsedCount: number }}
 */
export function collapseSameColorOverlaps(
  features: CollapseFeature[],
  options: CollapseOptions = {},
): CollapseResult {
  const { collapseDistM = 12 } = options;
  const minOverlapM = options.minOverlapM ?? 120;
  const lines = features.filter(
    (feature) =>
      feature.geometry?.type === "LineString" &&
      Array.isArray(feature.geometry.coordinates) &&
      feature.geometry.coordinates.length >= 2,
  );

  const byColor = new Map<unknown, CollapseFeature[]>();
  for (const feature of lines) {
    const color = feature.properties?.color;
    if (!color) continue;
    if (!byColor.has(color)) byColor.set(color, []);
    byColor.get(color)?.push(feature);
  }

  const targetRouteUnions = new Map<CollapseFeature, RouteId[]>();
  const targetSuppressionIntervals = new Map<CollapseFeature, ArcInterval[]>();
  const replacementParts = new Map<CollapseFeature, CollapseFeature[]>();
  const dropped = new Set<CollapseFeature>();
  const sharedRuns: CollapseFeature[] = [];
  let collapsedCount = 0;

  for (const group of byColor.values()) {
    if (group.length < 2) continue;
    const targetMeta = new Map<CollapseFeature, TargetMeta>(
      group.map((feature) => [
        feature,
        {
          arcs: cumulativeArcs(feature.geometry.coordinates),
          lengthM: lengthM(feature.geometry.coordinates),
        },
      ]),
    );
    const ranked = group
      .map((feature) => ({ feature, length: targetMeta.get(feature)?.lengthM ?? 0 }))
      .sort(
        (left, right) =>
          featureRouteRank(left.feature) - featureRouteRank(right.feature) ||
          right.length - left.length ||
          corridorId(left.feature).localeCompare(corridorId(right.feature), "en", { numeric: true }),
      )
      .map((entry) => entry.feature);

    for (let index = 1; index < ranked.length; index += 1) {
      const source = ranked[index];
      if (dropped.has(source)) continue;
      const targets = ranked.slice(0, index).filter((target) => !dropped.has(target));
      if (targets.length === 0) continue;

      const sourceMeta = targetMeta.get(source);
      if (!sourceMeta) continue;
      const hits = buildHits(source, targets, collapseDistM, targetMeta);
      const runs = findOverlapRuns(source, hits, minOverlapM, sourceMeta.arcs);
      if (runs.length === 0) continue;

      const fullRun = runs.find((run) =>
        isFullFeatureCollapse(run, sourceMeta.lengthM, targetMeta.get(run.target)?.lengthM ?? 0),
      );
      if (fullRun) {
        const current = targetRouteUnions.get(fullRun.target) ?? featureRouteIds(fullRun.target);
        targetRouteUnions.set(fullRun.target, unionRoutes(current, featureRouteIds(source)));
        dropped.add(source);
        replacementParts.set(source, []);
        collapsedCount += 1;
        continue;
      }

      // Drop short overlaps: leave them as overlapping same-color geometry rather
      // than carving a stub. Only genuinely long shared runs become their own
      // route-unioned feature with the source split around them.
      const acceptedRuns = runs.filter(
        (run) =>
          run.sourceRunLengthM >= MIN_SHARED_RUN_M &&
          run.targetEndArc - run.targetStartArc >= MIN_SHARED_RUN_M * 0.6,
      );
      if (acceptedRuns.length === 0) continue;
      const tails = buildSourceTails(source, acceptedRuns, sourceMeta.lengthM);
      for (const run of acceptedRuns) {
        if (!targetSuppressionIntervals.has(run.target)) targetSuppressionIntervals.set(run.target, []);
        targetSuppressionIntervals.get(run.target)?.push({ startArc: run.targetStartArc, endArc: run.targetEndArc });
        const shared = buildSharedRunFeature(run, sharedRuns.length + 1);
        if (shared) sharedRuns.push(shared);
      }
      replacementParts.set(source, tails);
      collapsedCount += 1;
    }
  }

  const output: CollapseFeature[] = [];
  for (const feature of features) {
    if (replacementParts.has(feature)) {
      const replacements = replacementParts.get(feature);
      if (replacements) output.push(...replacements);
      continue;
    }

    if (targetSuppressionIntervals.has(feature)) {
      const routeIds = targetRouteUnions.get(feature) ?? featureRouteIds(feature);
      const intervals = targetSuppressionIntervals.get(feature);
      if (!intervals) continue;
      const tails = buildTailsForIntervals(
        feature,
        intervals,
        lengthM(feature.geometry.coordinates),
        {
          route_ids: routeIds,
          color_route_ids: Array.isArray(feature.properties?.color_route_ids) ? routeIds : feature.properties?.color_route_ids,
          same_color_target_tail: true,
          same_color_tail_source_corridor_id: corridorId(feature),
        },
      );
      output.push(...tails);
      continue;
    }

    if (targetRouteUnions.has(feature)) {
      const routeIds = targetRouteUnions.get(feature);
      output.push({
        ...feature,
        properties: {
          ...clearStaleOrphanQa({
            ...feature.properties,
            route_ids: routeIds,
            color_route_ids: Array.isArray(feature.properties?.color_route_ids) ? routeIds : feature.properties?.color_route_ids,
            same_color_collapsed_representative: true,
          }),
        },
      });
      continue;
    }

    output.push(feature);
  }

  output.push(...sharedRuns);

  return { features: output, collapsedCount };
}
