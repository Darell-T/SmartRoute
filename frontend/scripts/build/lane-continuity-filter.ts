// frontend/scripts/build/lane-continuity-filter.ts
// Phase 3c: Build-pipeline helper for filtering bogus branch transitions
// and marking orphan lane features before artifact promotion.
//
// No fs, no globals. Pure functions only.

import type { Feature, LineStringGeometry, Position } from "./types.ts";

type LaneFeatureProperties = {
  lane_slot_source?: string;
  bundle_id?: string;
  bundle_id_from?: string;
  bundle_id_to?: string;
  color_route_ids?: string[];
  route_ids?: string[];
  transition_classification?: string;
  length_m?: number;
  from_anchor_id?: string | null;
  to_anchor_id?: string | null;
  from_stop_id?: string | number | null;
  to_stop_id?: string | number | null;
  qa_orphan_origin?: boolean;
  qa_orphan_from_is_terminal?: boolean;
  qa_orphan_to_is_terminal?: boolean;
  qa_orphan_severity?: string;
};

type LaneFeature = Feature<LineStringGeometry, LaneFeatureProperties>;

type FilterResult = {
  kept: LaneFeature[];
  dropped: Array<{ feature: LaneFeature; reason: string }>;
};

type OrphanRemoval = {
  features: LaneFeature[];
  removedCount: number;
};

function routesAt(index: Map<string, Set<string>>, id: string | undefined) {
  if (!id) return new Set<string>();
  return index.get(id) ?? new Set<string>();
}

function bogusTransitionReason(
  properties: LaneFeatureProperties,
  corridorRouteIndex: Map<string, Set<string>>,
): string | null {
  if (properties.lane_slot_source !== "branch_transition") return null;
  const fromRoutes = routesAt(corridorRouteIndex, properties.bundle_id_from);
  const toRoutes = routesAt(corridorRouteIndex, properties.bundle_id_to);
  const colorRouteIds = properties.color_route_ids ?? [];
  const colorInFrom = colorRouteIds.some((routeId) => fromRoutes.has(routeId));
  const colorInTo = colorRouteIds.some((routeId) => toRoutes.has(routeId));
  if (!colorInFrom && !colorInTo) {
    return "bogus_route_mismatch:color_absent_from_both_endpoints";
  }
  const classification = properties.transition_classification ?? "";
  if (classification === "safe_same_route_continuation") {
    const routeIds = properties.route_ids ?? [];
    const intersection = routeIds.filter((routeId) => fromRoutes.has(routeId) && toRoutes.has(routeId));
    if (intersection.length === 0) {
      return "bogus_classification:safe_same_route_but_empty_intersect";
    }
  }
  const length = properties.length_m ?? 0;
  if (classification === "likely_branch_exit" && length > 25) {
    return `length_exceeds_25m:${length.toFixed(1)}m`;
  }
  return null;
}

/**
 * Filter bogus branch_transition features from a set of bundle lane features.
 *
 * A transition is BOGUS and DROPPED if any of:
 *  (a) Its color is not present in the route_ids of EITHER of its endpoint
 *      corridors (per bundle_id_from / bundle_id_to lookup via corridorRouteIndex).
 *  (b) It is classified as safe_same_route_continuation but route_ids intersection
 *      between the two endpoint corridors is empty.
 *  (c) It is classified as likely_branch_exit AND length > 25m.
 *
 * @param {GeoJSON.Feature[]} bundleLaneFeatures  All bundle lane features.
 * @param {Map<string, Set<string>>} corridorRouteIndex
 *   Map from bundle_id (or corridor_id) to Set of route_ids served.
 * @returns {{ kept: GeoJSON.Feature[], dropped: Array<{ feature: GeoJSON.Feature, reason: string }> }}
 */
export function filterBogusTransitions(
  bundleLaneFeatures: LaneFeature[],
  corridorRouteIndex: Map<string, Set<string>>,
): FilterResult {
  const kept: LaneFeature[] = [];
  const dropped: Array<{ feature: LaneFeature; reason: string }> = [];
  for (const feature of bundleLaneFeatures) {
    const reason = bogusTransitionReason(feature.properties, corridorRouteIndex);
    if (reason) dropped.push({ feature, reason });
    else kept.push(feature);
  }
  return { kept, dropped };
}

/**
 * Mark orphan lane features (features where BOTH endpoints have no adjacent
 * same-route neighbor AND neither endpoint is a real terminal station).
 *
 * Does NOT remove features — only stamps qa_orphan_origin: true.
 * The runtime can choose to hide stamped features via the debug overlay.
 *
 * @param {GeoJSON.Feature[]} bundleLaneFeatures
 * @param {Set<string>} terminalStationIds  Stop IDs of known route terminals.
 * @returns {GeoJSON.Feature[]}  Same array reference, with some features mutated.
 */
function coordKey(coord: Position): string {
  return `${coord[0].toFixed(5)},${coord[1].toFixed(5)}`;
}

function endpointKeysFor(feature: LaneFeature) {
  const properties = feature.properties;
  const coords = feature.geometry?.coordinates;
  if (!coords || coords.length < 2) return null;
  const fromKey = properties.from_anchor_id
    ? `anchor:${properties.from_anchor_id}`
    : `coord:${coordKey(coords[0])}`;
  const toKey = properties.to_anchor_id
    ? `anchor:${properties.to_anchor_id}`
    : `coord:${coordKey(coords[coords.length - 1])}`;
  return { fromKey, toKey };
}

function indexRouteEndpoints(bundleLaneFeatures: LaneFeature[]) {
  const routeEndpoints = new Map<string, Map<string, LaneFeature[]>>();
  for (const feature of bundleLaneFeatures) {
    const keys = endpointKeysFor(feature);
    if (!keys) continue;
    for (const routeId of feature.properties.route_ids ?? []) {
      let epMap = routeEndpoints.get(routeId);
      if (!epMap) {
        epMap = new Map<string, LaneFeature[]>();
        routeEndpoints.set(routeId, epMap);
      }
      for (const key of [keys.fromKey, keys.toKey]) {
        const bucket = epMap.get(key);
        if (bucket) bucket.push(feature);
        else epMap.set(key, [feature]);
      }
    }
  }
  return routeEndpoints;
}

function featureIsOrphaned(
  feature: LaneFeature,
  keys: { fromKey: string; toKey: string },
  routeEndpoints: Map<string, Map<string, LaneFeature[]>>,
) {
  const routeIds = feature.properties.route_ids ?? [];
  if (routeIds.length === 0) return false;
  for (const routeId of routeIds) {
    const epMap = routeEndpoints.get(routeId);
    if (!epMap) continue;
    const fromNeighbors = (epMap.get(keys.fromKey) ?? []).filter((other) => other !== feature);
    const toNeighbors = (epMap.get(keys.toKey) ?? []).filter((other) => other !== feature);
    if (fromNeighbors.length > 0 || toNeighbors.length > 0) return false;
  }
  return true;
}

function stampOrphan(feature: LaneFeature, terminalStationIds: Set<string>) {
  const properties = feature.properties;
  const fromIsTerminal = Boolean(
    properties.from_stop_id && terminalStationIds.has(String(properties.from_stop_id)),
  );
  const toIsTerminal = Boolean(
    properties.to_stop_id && terminalStationIds.has(String(properties.to_stop_id)),
  );
  properties.qa_orphan_origin = true;
  properties.qa_orphan_from_is_terminal = fromIsTerminal;
  properties.qa_orphan_to_is_terminal = toIsTerminal;
  properties.qa_orphan_severity = fromIsTerminal && toIsTerminal ? "warn" : "error";
}

export function markOrphanLanes(bundleLaneFeatures: LaneFeature[], terminalStationIds: Set<string>): LaneFeature[] {
  const routeEndpoints = indexRouteEndpoints(bundleLaneFeatures);
  for (const feature of bundleLaneFeatures) {
    if (feature.properties.lane_slot_source === "branch_transition") continue;
    const keys = endpointKeysFor(feature);
    if (!keys || !featureIsOrphaned(feature, keys, routeEndpoints)) continue;
    stampOrphan(feature, terminalStationIds);
  }
  return bundleLaneFeatures;
}

// Drop lanes that markOrphanLanes flagged as error-severity orphans whose BOTH
// endpoints dangle and neither is a real terminal -- these are stray duplicates
// (e.g. the solo-E opendata-00028 stub that parallels the A/C/E spine), not real
// network. Everything else (terminal-anchored, single-end, or warn) is kept.
// Returns { features, removedCount }.
export function removeOrphanErrorLanes(features: LaneFeature[]): OrphanRemoval {
  const kept = features.filter((f) => {
    const p = f.properties ?? {};
    const strayOrphan =
      p.qa_orphan_origin === true &&
      p.qa_orphan_severity === "error" &&
      p.qa_orphan_from_is_terminal === false &&
      p.qa_orphan_to_is_terminal === false;
    return !strayOrphan;
  });
  return { features: kept, removedCount: features.length - kept.length };
}
