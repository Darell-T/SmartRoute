import { offsetPolylineOverExtent } from "./cross-color-spread.ts";
import type { Feature, LineStringGeometry, Position } from "./types.ts";

const EARTH_RADIUS_M = 6371000;

// Properties carried by the corridor LineString features this pass consumes and
// emits. Only the members read arithmetically/structurally below are named; the
// pipeline attaches many more stage-specific fields, hence the index signature.
type CorridorProperties = {
  corridor_id: string;
  route_ids?: string[];
  color?: string;
  length_m?: number | null;
  from_anchor_id?: string | null;
  to_anchor_id?: string | null;
  physical_bundle_spine_hash?: string | null;
  // Set on materialized output features (read by the renderer + tests).
  bundle_materialization_role?: string;
  lane_slot?: number;
  lane_slot_source?: string;
  [key: string]: unknown;
};

export type CorridorFeature = Feature<LineStringGeometry, CorridorProperties>;

type ArcSample = { coordinate: Position; arc: number };

type ArcRun = { startArc: number; endArc: number; sampleCount: number | null };

type ColorOrdering = { colors: string[]; overrideApplied: boolean };

type PhysicalBundleGroup = {
  physical_bundle_id: string;
  physical_bundle_spine_hash?: string | null;
  spine_ids?: string[];
  base_spine_id?: string | null;
  base_corridor_id?: string | null;
  confidence?: number;
  shared_extent_start_m?: number;
  shared_extent_end_m?: number;
  [key: string]: unknown;
};

type MaterializeOptions = {
  confidenceMin?: number;
  overlapDistMaxM?: number;
  sharedLenMinM?: number;
  splitSampleM?: number;
  fanoutBlendM?: number;
  minTailLengthM?: number;
  laneWidthM?: number;
  taperM?: number;
  compareRouteIds?: (a: string, b: string) => number;
  routeColorFor?: (routeId: string) => string;
  orderColorsForBundle?: (colors: string[]) => ColorOrdering;
  spinesById?: Map<string, unknown>;
};

type ResolvedOptions = {
  confidenceMin: number;
  overlapDistMaxM: number;
  sharedLenMinM: number;
  splitSampleM: number;
  fanoutBlendM: number;
  minTailLengthM: number;
  laneWidthM?: number;
  taperM?: number;
  compareRouteIds: (a: string, b: string) => number;
  routeColorFor: (routeId: string) => string;
  orderColorsForBundle: (colors: string[]) => ColorOrdering;
  spinesById: Map<string, unknown>;
};

type MaterializeDebug = {
  materializedBundleFeatures: CorridorFeature[];
  fanoutFeatures: CorridorFeature[];
  splitFeatures: CorridorFeature[];
  defectFeatures: Feature<LineStringGeometry, Record<string, unknown>>[];
};

function haversineM([lon1, lat1]: Position, [lon2, lat2]: Position): number {
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(a));
}

function polylineLengthM(coords: Position[]): number {
  let total = 0;
  for (let index = 1; index < coords.length; index += 1) {
    total += haversineM(coords[index - 1], coords[index]);
  }
  return total;
}

function cumulativeArcLengths(coords: Position[]): number[] {
  const arcs = [0];
  for (let index = 1; index < coords.length; index += 1) {
    arcs.push(arcs[index - 1] + haversineM(coords[index - 1], coords[index]));
  }
  return arcs;
}

function interpolateAtArc(coords: Position[], arcs: number[], targetArc: number): Position {
  if (targetArc <= 0) return coords[0];
  const total = arcs[arcs.length - 1];
  if (targetArc >= total) return coords[coords.length - 1];

  for (let index = 1; index < coords.length; index += 1) {
    if (arcs[index] >= targetArc) {
      const segmentLength = arcs[index] - arcs[index - 1];
      if (segmentLength === 0) return coords[index];
      const t = (targetArc - arcs[index - 1]) / segmentLength;
      const from = coords[index - 1];
      const to = coords[index];
      return [
        from[0] + (to[0] - from[0]) * t,
        from[1] + (to[1] - from[1]) * t,
      ];
    }
  }

  return coords[coords.length - 1];
}

function slicePolylineByArc(coords: Position[], startArc: number, endArc: number): Position[] {
  if (!Array.isArray(coords) || coords.length < 2) return [];
  const arcs = cumulativeArcLengths(coords);
  const total = arcs[arcs.length - 1];
  const start = Math.max(0, Math.min(total, startArc));
  const end = Math.max(0, Math.min(total, endArc));
  if (end - start <= 0.5) return [];

  const out = [interpolateAtArc(coords, arcs, start)];
  for (let index = 0; index < coords.length; index += 1) {
    const arc = arcs[index];
    if (arc > start && arc < end) out.push(coords[index]);
  }
  const endPoint = interpolateAtArc(coords, arcs, end);
  if (haversineM(out[out.length - 1], endPoint) > 0.01) out.push(endPoint);
  return out.length >= 2 ? out : [];
}

function resampleWithArc(coords: Position[], stepM: number): ArcSample[] {
  if (!Array.isArray(coords) || coords.length < 2) return [];
  const arcs = cumulativeArcLengths(coords);
  const total = arcs[arcs.length - 1];
  const out: ArcSample[] = [];
  for (let arc = 0; arc < total; arc += stepM) {
    out.push({ coordinate: interpolateAtArc(coords, arcs, arc), arc });
  }
  out.push({ coordinate: coords[coords.length - 1], arc: total });
  return out;
}

function densifyPolyline(coords: Position[], stepM: number): Position[] {
  return resampleWithArc(coords, stepM).map((sample) => sample.coordinate);
}

function nearestSampleDistanceM(point: Position, samples: ArcSample[]): number {
  let best = Infinity;
  for (const sample of samples) {
    const coord = (sample.coordinate ?? sample) as Position;
    const distance = haversineM(point, coord);
    if (distance < best) best = distance;
  }
  return best;
}

function longestTrueRun(
  samples: ArcSample[],
  predicate: (sample: ArcSample, index: number) => boolean,
): ArcRun | null {
  let best: { startIndex: number; endIndex: number } | null = null;
  let current: { startIndex: number; endIndex: number } | null = null;

  for (let index = 0; index < samples.length; index += 1) {
    if (predicate(samples[index], index)) {
      if (!current) current = { startIndex: index, endIndex: index };
      else current.endIndex = index;
      continue;
    }
    if (current && (!best || current.endIndex - current.startIndex > best.endIndex - best.startIndex)) {
      best = current;
    }
    current = null;
  }

  if (current && (!best || current.endIndex - current.startIndex > best.endIndex - best.startIndex)) {
    best = current;
  }

  if (!best) return null;

  return {
    startArc: samples[best.startIndex].arc,
    endArc: samples[best.endIndex].arc,
    sampleCount: best.endIndex - best.startIndex + 1,
  };
}

function uniqueSortedRouteIds(
  features: CorridorFeature[],
  compareRouteIds: (a: string, b: string) => number,
): string[] {
  return [...new Set(features.flatMap((feature) => feature.properties.route_ids ?? []))]
    .sort(compareRouteIds);
}

function colorRouteIdsFor(
  routeIds: string[],
  routeColorFor: (routeId: string) => string,
): Record<string, string[]> {
  const out: Record<string, string[]> = {};
  for (const routeId of routeIds) {
    const color = routeColorFor(routeId);
    if (!out[color]) out[color] = [];
    out[color].push(routeId);
  }
  return out;
}

function colorsForRoutes(
  routeIds: string[],
  routeColorFor: (routeId: string) => string,
  orderColorsForBundle: (colors: string[]) => ColorOrdering,
): string[] {
  const colors = [...new Set(routeIds.map((routeId) => routeColorFor(routeId)))];
  return orderColorsForBundle(colors).colors;
}

function laneSlotsForColors(colors: string[]): Record<string, number> {
  return Object.fromEntries(
    colors.map((color, index) => [color, index - (colors.length - 1) / 2]),
  );
}

function cloneFeatureWith(
  feature: CorridorFeature,
  geometry: LineStringGeometry,
  properties: Record<string, unknown>,
): CorridorFeature {
  return {
    type: "Feature",
    geometry,
    properties: {
      ...feature.properties,
      ...properties,
    },
  };
}

function corridorIdFromSpineId(spineId: string): string {
  return String(spineId).startsWith("spine-")
    ? String(spineId).slice("spine-".length)
    : String(spineId);
}

function sharedRunOnBase(
  baseCoords: Position[],
  memberFeatures: CorridorFeature[],
  options: ResolvedOptions,
): ArcRun | null {
  const baseSamples = resampleWithArc(baseCoords, options.splitSampleM);
  const memberSamples = memberFeatures.map((feature) =>
    resampleWithArc(feature.geometry.coordinates, options.splitSampleM),
  );

  return longestTrueRun(baseSamples, (sample) => {
    let nearMemberCount = 0;
    for (const samples of memberSamples) {
      if (nearestSampleDistanceM(sample.coordinate, samples) <= options.overlapDistMaxM) {
        nearMemberCount += 1;
      }
    }
    return nearMemberCount >= 2;
  });
}

function sharedRunOnMember(
  memberCoords: Position[],
  sharedCoords: Position[],
  options: ResolvedOptions,
): ArcRun | null {
  const memberSamples = resampleWithArc(memberCoords, options.splitSampleM);
  const sharedSamples = resampleWithArc(sharedCoords, options.splitSampleM);
  return longestTrueRun(
    memberSamples,
    (sample) => nearestSampleDistanceM(sample.coordinate, sharedSamples) <= options.overlapDistMaxM,
  );
}

function appendIfUseful(out: CorridorFeature[], feature: CorridorFeature, minLengthM: number): boolean {
  const length = polylineLengthM(feature.geometry.coordinates);
  if (length < minLengthM) return false;
  feature.properties.length_m = Number(length.toFixed(2));
  out.push(feature);
  return true;
}

function makeMaterializedFeatureId(bundleId: string, role: string, suffix: string): string {
  return `${bundleId}-${role}-${suffix}`.replace(/[^a-zA-Z0-9_-]/g, "-");
}

function addTailAndFanout({
  out,
  debug,
  member,
  bundleId,
  bundleSpineHash,
  sharedAnchorId,
  bundleLaneSlots,
  side,
  startArc,
  endArc,
  memberTotal,
  options,
}: {
  out: CorridorFeature[];
  debug: MaterializeDebug;
  member: CorridorFeature;
  bundleId: string;
  bundleSpineHash: string | null;
  sharedAnchorId: string;
  bundleLaneSlots: Record<string, number> | null | undefined;
  side: string;
  startArc: number;
  endArc: number;
  memberTotal: number;
  options: ResolvedOptions;
}): void {
  const memberCoords = member.geometry.coordinates;
  const routeIds = [...(member.properties.route_ids ?? [])].sort(options.compareRouteIds);
  const colorRouteIds = colorRouteIdsFor(routeIds, options.routeColorFor);
  const memberColors = colorsForRoutes(routeIds, options.routeColorFor, options.orderColorsForBundle);
  const memberColor = memberColors[0] ?? options.routeColorFor(routeIds[0] ?? "");
  const inheritedLaneSlot = Number(bundleLaneSlots?.[memberColor] ?? 0);
  const sourceCorridorId = member.properties.corridor_id;

  if (side === "before") {
    const fanoutStartArc = Math.max(0, endArc - options.fanoutBlendM);
    const tailCoords = slicePolylineByArc(memberCoords, 0, fanoutStartArc);
    const fanoutCoords = slicePolylineByArc(memberCoords, fanoutStartArc, endArc);

    appendIfUseful(
      out,
      cloneFeatureWith(member, { type: "LineString", coordinates: tailCoords }, {
        visual_feature_type: "materialized_physical_bundle_branch_tail",
        corridor_id: makeMaterializedFeatureId(bundleId, "branch-tail", `${sourceCorridorId}-before`),
        route_ids: routeIds,
        color_route_ids: colorRouteIds,
        source_corridor_id: sourceCorridorId,
        physical_bundle_id: bundleId,
        physical_bundle_spine_hash: bundleSpineHash,
        materialized_bundle_id: bundleId,
        bundle_materialization_role: "branch_tail",
        from_anchor_id: member.properties.from_anchor_id ?? null,
        to_anchor_id: `${bundleId}-${sourceCorridorId}-before-fanout-start`,
      }),
      options.minTailLengthM,
    );

    const fanout = cloneFeatureWith(member, { type: "LineString", coordinates: fanoutCoords }, {
      visual_feature_type: "materialized_physical_bundle_fanout",
      corridor_id: makeMaterializedFeatureId(bundleId, "fanout", `${sourceCorridorId}-before`),
      route_ids: routeIds,
      color_route_ids: colorRouteIds,
      source_corridor_id: sourceCorridorId,
      physical_bundle_id: bundleId,
      physical_bundle_spine_hash: bundleSpineHash,
      materialized_bundle_id: bundleId,
      bundle_materialization_role: "fanout",
      fanout_from_lane_slot: 0,
      fanout_to_lane_slot: inheritedLaneSlot,
      fanout_direction: "branch_to_shared",
      fanout_blend_m: options.fanoutBlendM,
      from_anchor_id: `${bundleId}-${sourceCorridorId}-before-fanout-start`,
      to_anchor_id: sharedAnchorId,
    });
    appendIfUseful(out, fanout, 1);
    debug.fanoutFeatures.push(fanout);
    return;
  }

  const fanoutEndArc = Math.min(memberTotal, startArc + options.fanoutBlendM);
  const fanoutCoords = slicePolylineByArc(memberCoords, startArc, fanoutEndArc);
  const tailCoords = slicePolylineByArc(memberCoords, fanoutEndArc, memberTotal);

  const fanout = cloneFeatureWith(member, { type: "LineString", coordinates: fanoutCoords }, {
    visual_feature_type: "materialized_physical_bundle_fanout",
    corridor_id: makeMaterializedFeatureId(bundleId, "fanout", `${sourceCorridorId}-after`),
    route_ids: routeIds,
    color_route_ids: colorRouteIds,
    source_corridor_id: sourceCorridorId,
    physical_bundle_id: bundleId,
    physical_bundle_spine_hash: bundleSpineHash,
    materialized_bundle_id: bundleId,
    bundle_materialization_role: "fanout",
    fanout_from_lane_slot: inheritedLaneSlot,
    fanout_to_lane_slot: 0,
    fanout_direction: "shared_to_branch",
    fanout_blend_m: options.fanoutBlendM,
    from_anchor_id: sharedAnchorId,
    to_anchor_id: `${bundleId}-${sourceCorridorId}-after-fanout-end`,
  });
  appendIfUseful(out, fanout, 1);
  debug.fanoutFeatures.push(fanout);

  appendIfUseful(
    out,
    cloneFeatureWith(member, { type: "LineString", coordinates: tailCoords }, {
      visual_feature_type: "materialized_physical_bundle_branch_tail",
      corridor_id: makeMaterializedFeatureId(bundleId, "branch-tail", `${sourceCorridorId}-after`),
      route_ids: routeIds,
      color_route_ids: colorRouteIds,
      source_corridor_id: sourceCorridorId,
      physical_bundle_id: bundleId,
      physical_bundle_spine_hash: bundleSpineHash,
      materialized_bundle_id: bundleId,
      bundle_materialization_role: "branch_tail",
      from_anchor_id: `${bundleId}-${sourceCorridorId}-after-fanout-end`,
      to_anchor_id: member.properties.to_anchor_id ?? null,
    }),
    options.minTailLengthM,
  );
}

export function materializePhysicalBundles(
  corridorFeatures: CorridorFeature[],
  physicalBundles: PhysicalBundleGroup[],
  rawOptions: MaterializeOptions = {},
) {
  const options = {
    confidenceMin: 0.75,
    overlapDistMaxM: 15,
    sharedLenMinM: 250,
    splitSampleM: 5,
    fanoutBlendM: 100,
    minTailLengthM: 15,
    compareRouteIds: (a: string, b: string) => String(a).localeCompare(String(b), "en", { numeric: true }),
    routeColorFor: () => "#808183",
    orderColorsForBundle: (colors: string[]) => ({ colors, overrideApplied: false }),
    spinesById: new Map(),
    ...rawOptions,
  } as ResolvedOptions;

  const featureByCorridorId = new Map(
    corridorFeatures.map((feature): [string, CorridorFeature] => [feature.properties.corridor_id, feature]),
  );
  const consumedCorridorIds = new Set<string>();
  const materializedFeatures: CorridorFeature[] = [];
  const debug: MaterializeDebug = {
    materializedBundleFeatures: [],
    fanoutFeatures: [],
    splitFeatures: [],
    defectFeatures: [],
  };

  for (const group of physicalBundles) {
    if ((group.confidence ?? 0) < options.confidenceMin) continue;

    const members = (group.spine_ids ?? [])
      .map((spineId) => featureByCorridorId.get(corridorIdFromSpineId(spineId)))
      .filter((feature): feature is CorridorFeature => Boolean(feature))
      .filter((feature) => !consumedCorridorIds.has(feature.properties.corridor_id));

    if (members.length < 2) continue;

    const requestedBaseCorridorId = group.base_spine_id
      ? corridorIdFromSpineId(group.base_spine_id)
      : group.base_corridor_id;
    let base = requestedBaseCorridorId
      ? members.find((member) => member.properties.corridor_id === requestedBaseCorridorId)
      : null;
    if (!base) {
      base = members[0];
      for (const member of members) {
        const length = member.properties.length_m ?? polylineLengthM(member.geometry.coordinates);
        const baseLength = base.properties.length_m ?? polylineLengthM(base.geometry.coordinates);
        if (length > baseLength) base = member;
      }
    }

    const baseRun: ArcRun | null =
      Number.isFinite(group.shared_extent_start_m) && Number.isFinite(group.shared_extent_end_m)
        ? {
            startArc: group.shared_extent_start_m as number,
            endArc: group.shared_extent_end_m as number,
            sampleCount: null,
          }
        : sharedRunOnBase(base.geometry.coordinates, members, options);
    const sharedLength = baseRun ? baseRun.endArc - baseRun.startArc : 0;
    if (!baseRun || sharedLength < options.sharedLenMinM) {
      debug.defectFeatures.push({
        type: "Feature",
        geometry: base.geometry,
        properties: {
          visual_feature_type: "materialized_bundle_defect",
          physical_bundle_id: group.physical_bundle_id,
          reason: "shared_run_too_short",
          shared_length_m: Number(sharedLength.toFixed(2)),
          member_corridor_ids: members.map((member) => member.properties.corridor_id),
        },
      });
      continue;
    }

    const sharedCoords = slicePolylineByArc(base.geometry.coordinates, baseRun.startArc, baseRun.endArc);
    if (sharedCoords.length < 2) {
      debug.defectFeatures.push({
        type: "Feature",
        geometry: base.geometry,
        properties: {
          visual_feature_type: "materialized_bundle_defect",
          physical_bundle_id: group.physical_bundle_id,
          reason: "shared_geometry_degenerate",
        },
      });
      continue;
    }

    const memberRuns = members.map((member) => ({
      member,
      run: sharedRunOnMember(member.geometry.coordinates, sharedCoords, options),
    }));
    const activeEntries = memberRuns.filter(
      (entry): entry is { member: CorridorFeature; run: ArcRun } => Boolean(entry.run),
    );
    if (activeEntries.length < 2) {
      debug.defectFeatures.push({
        type: "Feature",
        geometry: base.geometry,
        properties: {
          visual_feature_type: "materialized_bundle_defect",
          physical_bundle_id: group.physical_bundle_id,
          reason: "active_members_too_few",
          active_member_count: activeEntries.length,
          member_corridor_ids: members.map((member) => member.properties.corridor_id),
        },
      });
      continue;
    }

    const bundleId = group.physical_bundle_id;
    const bundleSpineHash = group.physical_bundle_spine_hash ?? base.properties.physical_bundle_spine_hash ?? null;
    const laneWidthM = options.laneWidthM ?? 8;
    const taperM = options.taperM ?? 40;

    // PER-COLOR centered slots: members of the SAME color collapse to one lane
    // (e.g. the yellow N/Q/R/W trunk reads as a single yellow line), while distinct
    // COLORS get distinct, deterministically-ordered slots that never swap sides.
    // Each member is then emitted as ONE continuous polyline pushed into its color's
    // lane over its shared extent with a taper at the ends (offsetPolylineOverExtent
    // keeps the vertex count and leaves the divergent portion untouched). No slicing.
    const activeMembers = activeEntries.map((entry) => entry.member);
    const bundleRouteIds = uniqueSortedRouteIds(activeMembers, options.compareRouteIds);
    const colors = colorsForRoutes(bundleRouteIds, options.routeColorFor, options.orderColorsForBundle);
    const laneSlots = laneSlotsForColors(colors); // { color: centered slot } -- same color shares a slot
    const memberCorridorIds = activeMembers.map((member) => member.properties.corridor_id);

    for (const { member, run: memberRun } of activeEntries) {
      consumedCorridorIds.add(member.properties.corridor_id);
      const memberCoords = member.geometry.coordinates;
      const slot = Number(laneSlots[member.properties.color as string] ?? 0);
      const offsetCoords =
        slot === 0
          ? memberCoords.map((c) => c)
          : offsetPolylineOverExtent(memberCoords, memberRun.startArc, memberRun.endArc, slot * laneWidthM, taperM);
      const memberRouteIds = [...(member.properties.route_ids ?? [])].sort(options.compareRouteIds);
      const laneFeature = cloneFeatureWith(member, { type: "LineString", coordinates: offsetCoords }, {
        visual_feature_type: "materialized_continuous_member",
        corridor_id: member.properties.corridor_id,
        route_ids: memberRouteIds,
        color_route_ids: colorRouteIdsFor(memberRouteIds, options.routeColorFor),
        color: member.properties.color,
        physical_bundle_id: bundleId,
        physical_bundle_spine_hash: bundleSpineHash,
        materialized_bundle_id: bundleId,
        bundle_materialization_role: "continuous_lane",
        lane_slot: slot,
        lane_slot_source: "physical_bundle_continuous",
        lane_offset_baked: true,
        source_corridor_id: member.properties.corridor_id,
        member_corridor_ids: memberCorridorIds,
        shared_extent_start_m: Number(memberRun.startArc.toFixed(2)),
        shared_extent_end_m: Number(memberRun.endArc.toFixed(2)),
        length_m: Number(polylineLengthM(offsetCoords).toFixed(2)),
      });
      materializedFeatures.push(laneFeature);
      debug.materializedBundleFeatures.push(laneFeature);
    }
  }

  const unchangedFeatures = corridorFeatures.filter(
    (feature) => !consumedCorridorIds.has(feature.properties.corridor_id),
  );

  return {
    features: [...materializedFeatures, ...unchangedFeatures],
    consumed_corridor_count: consumedCorridorIds.size,
    materialized_bundle_count: debug.materializedBundleFeatures.length,
    fanout_count: debug.fanoutFeatures.length,
    debug,
  };
}
