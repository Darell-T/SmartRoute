// frontend/scripts/build/lane-continuity-filter.ts
// Phase 3c: Build-pipeline helper for filtering bogus branch transitions
// and marking orphan lane features before artifact promotion.
//
// No fs, no globals. Pure functions only.

import type { Feature, LineStringGeometry, Position } from "./types.ts";

const EARTH_RADIUS_M = 6371000;

type LaneFeatureProperties = {
  lane_slot_source?: string;
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
  [key: string]: unknown;
};

type LaneFeature = Feature<LineStringGeometry, LaneFeatureProperties>;

function haversineM([lon1, lat1]: Position, [lon2, lat2]: Position): number {
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(a));
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
): { kept: LaneFeature[]; dropped: Array<{ feature: LaneFeature; reason: string }> } {
  const kept: LaneFeature[] = [];
  const dropped: Array<{ feature: LaneFeature; reason: string }> = [];

  for (const feature of bundleLaneFeatures) {
    const p = feature.properties;

    // Only examine branch_transition features; keep everything else.
    if (p.lane_slot_source !== "branch_transition") {
      kept.push(feature);
      continue;
    }

    const fromRoutes = p.bundle_id_from ? corridorRouteIndex.get(p.bundle_id_from) ?? new Set<string>() : new Set<string>();
    const toRoutes = p.bundle_id_to ? corridorRouteIndex.get(p.bundle_id_to) ?? new Set<string>() : new Set<string>();
    const colorRouteIds = p.color_route_ids ?? [];
    const routeIds = p.route_ids ?? [];
    const classification = p.transition_classification ?? "";
    const length = p.length_m ?? 0;

    // Rule (a): color must be in at least one route of the endpoint corridors
    const colorInFrom = colorRouteIds.some((r) => fromRoutes.has(r));
    const colorInTo = colorRouteIds.some((r) => toRoutes.has(r));

    let bogusReason: string | null = null;

    if (!colorInFrom && !colorInTo) {
      bogusReason = "bogus_route_mismatch:color_absent_from_both_endpoints";
    } else if (classification === "safe_same_route_continuation") {
      // Rule (b): intersection must be non-empty for this classification
      const intersection = routeIds.filter((r) => fromRoutes.has(r) && toRoutes.has(r));
      if (intersection.length === 0) {
        bogusReason = "bogus_classification:safe_same_route_but_empty_intersect";
      }
    } else if (classification === "likely_branch_exit" && length > 25) {
      // Rule (c): branch exits tightened to 25m
      bogusReason = `length_exceeds_25m:${length.toFixed(1)}m`;
    }

    if (bogusReason) {
      dropped.push({ feature, reason: bogusReason });
    } else {
      kept.push(feature);
    }
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
export function markOrphanLanes(bundleLaneFeatures: LaneFeature[], terminalStationIds: Set<string>): LaneFeature[] {
  // Build per-route endpoint adjacency map
  // routeEndpoints: route_id -> Map<endpointKey -> feature[]>
  const routeEndpoints = new Map<string, Map<string, LaneFeature[]>>();

  function coordKey(coord: Position): string {
    return `${coord[0].toFixed(5)},${coord[1].toFixed(5)}`;
  }

  function getEndpointKeys(f: LaneFeature): { fromKey: string; toKey: string } | null {
    const p = f.properties;
    const coords = f.geometry?.coordinates;
    if (!coords || coords.length < 2) return null;
    const fromKey = p.from_anchor_id ? `anchor:${p.from_anchor_id}` : `coord:${coordKey(coords[0])}`;
    const toKey = p.to_anchor_id ? `anchor:${p.to_anchor_id}` : `coord:${coordKey(coords[coords.length - 1])}`;
    return { fromKey, toKey };
  }

  for (const f of bundleLaneFeatures) {
    const routeIds = f.properties.route_ids ?? [];
    const keys = getEndpointKeys(f);
    if (!keys) continue;
    for (const r of routeIds) {
      if (!routeEndpoints.has(r)) routeEndpoints.set(r, new Map<string, LaneFeature[]>());
      const epMap = routeEndpoints.get(r);
      if (!epMap) continue;
      for (const key of [keys.fromKey, keys.toKey]) {
        if (!epMap.has(key)) epMap.set(key, []);
        epMap.get(key)?.push(f);
      }
    }
  }

  // For each feature, check per-route orphan status
  for (const f of bundleLaneFeatures) {
    const p = f.properties;
    const keys = getEndpointKeys(f);
    if (!keys) continue;

    const routeIds = p.route_ids ?? [];
    if (routeIds.length === 0) continue;

    // Skip branch_transition features — they connect adjacent bundles by design
    if (p.lane_slot_source === "branch_transition") continue;

    // For EVERY route carried by this feature, check if at least one neighbor exists
    let allRoutesOrphaned = true;
    for (const r of routeIds) {
      const epMap = routeEndpoints.get(r);
      if (!epMap) continue;
      const fromNeighbors = (epMap.get(keys.fromKey) ?? []).filter((g) => g !== f);
      const toNeighbors = (epMap.get(keys.toKey) ?? []).filter((g) => g !== f);
      if (fromNeighbors.length > 0 || toNeighbors.length > 0) {
        allRoutesOrphaned = false;
        break;
      }
    }

    if (!allRoutesOrphaned) continue;

    // Both endpoints isolated for all carried routes.
    // Check if either endpoint is a known terminal station.
    const fromStopId = p.from_stop_id;
    const toStopId = p.to_stop_id;
    const fromIsTerminal = fromStopId && terminalStationIds.has(String(fromStopId));
    const toIsTerminal = toStopId && terminalStationIds.has(String(toStopId));

    // Stamp the feature
    p.qa_orphan_origin = true;
    p.qa_orphan_from_is_terminal = !!fromIsTerminal;
    p.qa_orphan_to_is_terminal = !!toIsTerminal;
    p.qa_orphan_severity = (fromIsTerminal && toIsTerminal) ? "warn" : "error";
  }

  return bundleLaneFeatures;
}

// Drop lanes that markOrphanLanes flagged as error-severity orphans whose BOTH
// endpoints dangle and neither is a real terminal -- these are stray duplicates
// (e.g. the solo-E opendata-00028 stub that parallels the A/C/E spine), not real
// network. Everything else (terminal-anchored, single-end, or warn) is kept.
// Returns { features, removedCount }.
export function removeOrphanErrorLanes(features: LaneFeature[]): { features: LaneFeature[]; removedCount: number } {
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
