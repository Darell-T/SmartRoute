import { offsetPolylineOverExtent } from "./cross-color-spread.ts";
import type { Feature, LineStringGeometry, Position } from "./types.ts";

const EARTH_RADIUS_M = 6371000;

// Properties carried by the corridor LineString features this pass consumes and
// emits. Only the members read arithmetically/structurally below are named; the
// pipeline attaches many more stage-specific fields, hence the index signature.
type ColorRouteTable = Record<string, string[]>;

type CorridorProperties = {
  corridor_id: string;
  route_ids?: string[];
  color?: string;
  length_m?: number | null;
  from_anchor_id?: string | null;
  to_anchor_id?: string | null;
  physical_bundle_spine_hash?: string | null;
  bundle_materialization_role?: string;
  lane_slot?: number;
  lane_slot_source?: string;
  visual_feature_type?: string;
  color_route_ids?: ColorRouteTable;
  source_corridor_id?: string;
  physical_bundle_id?: string;
  materialized_bundle_id?: string;
  lane_offset_baked?: boolean;
  member_corridor_ids?: string[];
  shared_extent_start_m?: number;
  shared_extent_end_m?: number;
  source_edge_ids?: string[];
  "source_shape_ids"?: string[];
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
  member_count?: number;
};

type SpineLookup = Map<string, {
  spine_id?: string;
  geometry?: { type?: string; coordinates?: Position[] };
  length_m?: number | null;
  route_ids?: string[];
}>;

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
  spinesById?: SpineLookup;
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
  spinesById: SpineLookup;
};

type BundleDefectProperties = {
  visual_feature_type: "materialized_bundle_defect";
  physical_bundle_id?: string;
  reason?: string;
  shared_length_m?: number;
  member_corridor_ids?: string[];
  active_member_count?: number;
};

type MaterializeDebug = {
  materializedBundleFeatures: CorridorFeature[];
  fanoutFeatures: CorridorFeature[];
  splitFeatures: CorridorFeature[];
  defectFeatures: Feature<LineStringGeometry, BundleDefectProperties>[];
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

function nearestSampleDistanceM(point: Position, samples: ArcSample[]): number {
  let best = Infinity;
  for (const sample of samples) {
    const distance = haversineM(point, sample.coordinate);
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
) {
  const out: ColorRouteTable = {};
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
  properties: Partial<CorridorProperties>,
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

function pickLongestCorridor(members: CorridorFeature[]): CorridorFeature {
  let base = members[0];
  for (const member of members) {
    const length = member.properties.length_m ?? polylineLengthM(member.geometry.coordinates);
    const baseLength = base.properties.length_m ?? polylineLengthM(base.geometry.coordinates);
    if (length > baseLength) base = member;
  }
  return base;
}

function resolveMaterializationBase(
  group: PhysicalBundleGroup,
  members: CorridorFeature[],
): CorridorFeature {
  const requestedBaseCorridorId = group.base_spine_id
    ? corridorIdFromSpineId(group.base_spine_id)
    : group.base_corridor_id;
  if (!requestedBaseCorridorId) return pickLongestCorridor(members);
  return members.find((member) => member.properties.corridor_id === requestedBaseCorridorId)
    ?? pickLongestCorridor(members);
}

function bundleDefect(
  geometry: LineStringGeometry,
  properties: Omit<BundleDefectProperties, "visual_feature_type">,
): Feature<LineStringGeometry, BundleDefectProperties> {
  return {
    type: "Feature",
    geometry,
    properties: {
      visual_feature_type: "materialized_bundle_defect",
      ...properties,
    },
  };
}

function finiteArcMeters(value: number | null | undefined): number | null {
  if (value == null) return null;
  if (value !== value || value === Infinity || value === -Infinity) return null;
  return value;
}

function sharedRunForGroup(
  group: PhysicalBundleGroup,
  base: CorridorFeature,
  members: CorridorFeature[],
  options: ResolvedOptions,
): ArcRun | null {
  const startArc = finiteArcMeters(group.shared_extent_start_m);
  const endArc = finiteArcMeters(group.shared_extent_end_m);
  if (startArc != null && endArc != null) {
    return { startArc, endArc, sampleCount: null };
  }
  return sharedRunOnBase(base.geometry.coordinates, members, options);
}

function emitContinuousMemberLanes(
  activeEntries: Array<{ member: CorridorFeature; run: ArcRun }>,
  bundleId: string,
  bundleSpineHash: string | null,
  options: ResolvedOptions,
): CorridorFeature[] {
  const laneWidthM = options.laneWidthM ?? 8;
  const taperM = options.taperM ?? 40;
  const activeMembers = activeEntries.map((entry) => entry.member);
  const bundleRouteIds = uniqueSortedRouteIds(activeMembers, options.compareRouteIds);
  const colors = colorsForRoutes(bundleRouteIds, options.routeColorFor, options.orderColorsForBundle);
  const laneSlots = laneSlotsForColors(colors);
  const memberCorridorIds = activeMembers.map((member) => member.properties.corridor_id);
  const lanes: CorridorFeature[] = [];

  for (const { member, run: memberRun } of activeEntries) {
    const memberCoords = member.geometry.coordinates;
    const slot = Number(laneSlots[String(member.properties.color ?? "")] ?? 0);
    const offsetCoords =
      slot === 0
        ? memberCoords.map((c) => c)
        : offsetPolylineOverExtent(memberCoords, memberRun.startArc, memberRun.endArc, slot * laneWidthM, taperM);
    const memberRouteIds = [...(member.properties.route_ids ?? [])].sort(options.compareRouteIds);
    lanes.push(cloneFeatureWith(member, { type: "LineString", coordinates: offsetCoords }, {
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
    }));
  }
  return lanes;
}

type BundleMaterialization = {
  lanes: CorridorFeature[];
  consumedIds: string[];
  defects: Feature<LineStringGeometry, BundleDefectProperties>[];
};

function materializeOnePhysicalBundle(
  group: PhysicalBundleGroup,
  members: CorridorFeature[],
  options: ResolvedOptions,
): BundleMaterialization {
  const base = resolveMaterializationBase(group, members);
  const baseRun = sharedRunForGroup(group, base, members, options);
  const sharedLength = baseRun ? baseRun.endArc - baseRun.startArc : 0;
  if (!baseRun || sharedLength < options.sharedLenMinM) {
    return {
      lanes: [],
      consumedIds: [],
      defects: [bundleDefect(base.geometry, {
        physical_bundle_id: group.physical_bundle_id,
        reason: "shared_run_too_short",
        shared_length_m: Number(sharedLength.toFixed(2)),
        member_corridor_ids: members.map((member) => member.properties.corridor_id),
      })],
    };
  }

  const sharedCoords = slicePolylineByArc(base.geometry.coordinates, baseRun.startArc, baseRun.endArc);
  if (sharedCoords.length < 2) {
    return {
      lanes: [],
      consumedIds: [],
      defects: [bundleDefect(base.geometry, {
        physical_bundle_id: group.physical_bundle_id,
        reason: "shared_geometry_degenerate",
      })],
    };
  }

  const memberRuns = members.map((member) => ({
    member,
    run: sharedRunOnMember(member.geometry.coordinates, sharedCoords, options),
  }));
  const activeEntries = memberRuns.filter(
    (entry): entry is { member: CorridorFeature; run: ArcRun } => Boolean(entry.run),
  );
  if (activeEntries.length < 2) {
    return {
      lanes: [],
      consumedIds: [],
      defects: [bundleDefect(base.geometry, {
        physical_bundle_id: group.physical_bundle_id,
        reason: "active_members_too_few",
        active_member_count: activeEntries.length,
        member_corridor_ids: members.map((member) => member.properties.corridor_id),
      })],
    };
  }

  const bundleSpineHash = group.physical_bundle_spine_hash ?? base.properties.physical_bundle_spine_hash ?? null;
  const lanes = emitContinuousMemberLanes(activeEntries, group.physical_bundle_id, bundleSpineHash, options);
  return {
    lanes,
    consumedIds: activeEntries.map((entry) => entry.member.properties.corridor_id),
    defects: [],
  };
}

function resolveMaterializeOptions(rawOptions: MaterializeOptions): ResolvedOptions {
  return {
    confidenceMin: rawOptions.confidenceMin ?? 0.75,
    overlapDistMaxM: rawOptions.overlapDistMaxM ?? 15,
    sharedLenMinM: rawOptions.sharedLenMinM ?? 250,
    splitSampleM: rawOptions.splitSampleM ?? 5,
    fanoutBlendM: rawOptions.fanoutBlendM ?? 100,
    minTailLengthM: rawOptions.minTailLengthM ?? 15,
    laneWidthM: rawOptions.laneWidthM,
    taperM: rawOptions.taperM,
    compareRouteIds: rawOptions.compareRouteIds
      ?? ((a: string, b: string) => String(a).localeCompare(String(b), "en", { numeric: true })),
    routeColorFor: rawOptions.routeColorFor ?? (() => "#808183"),
    orderColorsForBundle: rawOptions.orderColorsForBundle
      ?? ((colors: string[]) => ({ colors, overrideApplied: false })),
    spinesById: rawOptions.spinesById ?? new Map(),
  };
}

export function materializePhysicalBundles(
  corridorFeatures: CorridorFeature[],
  physicalBundles: PhysicalBundleGroup[],
  rawOptions: MaterializeOptions = {},
) {
  const options = resolveMaterializeOptions(rawOptions);

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

    const result = materializeOnePhysicalBundle(group, members, options);
    for (const id of result.consumedIds) consumedCorridorIds.add(id);
    materializedFeatures.push(...result.lanes);
    debug.materializedBundleFeatures.push(...result.lanes);
    debug.defectFeatures.push(...result.defects);
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
