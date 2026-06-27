// Pure helper -- no fs, no globals.
//
// Repairs the common "X at a branch split" artifact where a same-route branch
// endpoint overshoots across its sibling trunk. This does NOT add connectors and
// does NOT repair arbitrary interior crossings. It only snaps the first/last
// point of the overshooting feature to the actual segment intersection, removing
// the local overshoot so the two features share a split node.

import type { Feature, LineStringGeometry, Position } from "./types.ts";

const EARTH_RADIUS_M = 6371000;
const M_PER_DEG_LAT = 110574;

type JunctionFeatureProperties = {
  color?: string;
  color_route_ids?: string[] | Record<string, string[]>;
  route_ids?: string[];
  corridor_id?: string;
  bundle_id?: string;
  same_route_junction_fabric?: boolean;
  same_route_junction_fabric_repair_count?: number;
  same_route_junction_fabric_repairs?: Array<{ side: EndpointSide; distance_m: number }>;
  [key: string]: unknown;
};

type JunctionFeature = Feature<LineStringGeometry, JunctionFeatureProperties>;
type EndpointSide = "start" | "end";

type SegmentIntersection = {
  point: Position;
  t: number;
  u: number;
};

type EndpointCandidate = {
  side: EndpointSide;
  distanceM: number;
  point: Position;
  segmentIndex: number;
};

type IndexedEndpointCandidate = EndpointCandidate & {
  index: number;
  otherIndex: number;
};

type SameRouteJunctionOptions = {
  maxEndpointOvershootM?: number;
  minSegmentM?: number;
  allowSameColorSiblingRoutes?: boolean;
};

type JunctionRepair = {
  corridor_id: string | null;
  side: EndpointSide;
  distance_m: number;
  point: Position;
  other_index: number;
};

function metersPerDegLng(lat: number): number {
  return 111320 * Math.cos((lat * Math.PI) / 180);
}

function haversineM([lon1, lat1]: Position, [lon2, lat2]: Position): number {
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(a));
}

function activeRouteIdsForFeature(feature: JunctionFeature): string[] {
  const properties = feature.properties ?? {};
  const colorRouteIds = properties.color_route_ids;
  if (Array.isArray(colorRouteIds)) return colorRouteIds.map(String);
  if (colorRouteIds && typeof colorRouteIds === "object") {
    const color = properties.color;
    if (color && Array.isArray(colorRouteIds[color])) return colorRouteIds[color].map(String);
    return [...new Set(Object.values(colorRouteIds).flat().filter(Boolean).map(String))];
  }
  return Array.isArray(properties.route_ids) ? properties.route_ids.map(String) : [];
}

function shareActiveRoute(a: JunctionFeature, b: JunctionFeature): boolean {
  const routes = new Set(activeRouteIdsForFeature(a));
  return activeRouteIdsForFeature(b).some((routeId) => routes.has(routeId));
}

function sameColor(a: JunctionFeature, b: JunctionFeature): boolean {
  const ac = a.properties?.color;
  const bc = b.properties?.color;
  return Boolean(ac && bc && String(ac).toUpperCase() === String(bc).toUpperCase());
}

function compatibleJunctionColor(
  a: JunctionFeature,
  b: JunctionFeature,
  allowSameColorSiblingRoutes: boolean,
): boolean {
  if (!sameColor(a, b)) return false;
  if (shareActiveRoute(a, b)) return true;
  return allowSameColorSiblingRoutes;
}

function projectMeters(point: Position, originLat: number): Position {
  return [point[0] * metersPerDegLng(originLat), point[1] * M_PER_DEG_LAT];
}

function unprojectMeters(point: Position, originLat: number): Position {
  return [point[0] / metersPerDegLng(originLat), point[1] / M_PER_DEG_LAT];
}

function segmentIntersection(a: Position, b: Position, c: Position, d: Position): SegmentIntersection | null {
  const originLat = (a[1] + b[1] + c[1] + d[1]) / 4;
  const [x1, y1] = projectMeters(a, originLat);
  const [x2, y2] = projectMeters(b, originLat);
  const [x3, y3] = projectMeters(c, originLat);
  const [x4, y4] = projectMeters(d, originLat);
  const den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4);
  if (Math.abs(den) < 1e-9) return null;

  const t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / den;
  const u = ((x1 - x3) * (y1 - y2) - (y1 - y3) * (x1 - x2)) / den;
  if (t <= 1e-6 || t >= 1 - 1e-6 || u <= 1e-6 || u >= 1 - 1e-6) {
    return null;
  }

  return {
    point: unprojectMeters([x1 + (x2 - x1) * t, y1 + (y2 - y1) * t], originLat),
    t,
    u,
  };
}

function cumulativeArc(coords: Position[]): number[] {
  const arcs = [0];
  for (let i = 1; i < coords.length; i += 1) {
    arcs.push(arcs[i - 1] + haversineM(coords[i - 1], coords[i]));
  }
  return arcs;
}

function cloneFeatureWithCoordinates(feature: JunctionFeature, coordinates: Position[]): JunctionFeature {
  return {
    ...feature,
    geometry: {
      ...feature.geometry,
      coordinates,
    },
    properties: {
      ...(feature.properties ?? {}),
      same_route_junction_fabric: true,
      same_route_junction_fabric_repair_count:
        Number(feature.properties?.same_route_junction_fabric_repair_count ?? 0) + 1,
    },
  };
}

function normalizeEndpoint(
  coordinates: Position[],
  side: EndpointSide,
  point: Position,
  minSegmentM: number,
  segmentIndex: number | null = null,
): Position[] {
  const next = coordinates.map((coordinate) => coordinate.slice() as Position);
  if (side === "start") {
    if (segmentIndex !== null && segmentIndex > 0) {
      next.splice(0, segmentIndex, point);
    } else {
      next[0] = point;
    }
    while (next.length > 2 && haversineM(next[0], next[1]) < minSegmentM) {
      next.splice(1, 1);
    }
  } else {
    if (segmentIndex !== null && segmentIndex < next.length - 2) {
      next.splice(segmentIndex + 1, next.length - segmentIndex - 1, point);
    } else {
      next[next.length - 1] = point;
    }
    while (next.length > 2 && haversineM(next[next.length - 1], next[next.length - 2]) < minSegmentM) {
      next.splice(next.length - 2, 1);
    }
  }
  return next;
}

function candidateForSegment(
  coords: Position[],
  arcs: number[],
  segmentIndex: number,
  intersection: SegmentIntersection,
  maxEndpointOvershootM: number,
): EndpointCandidate | null {
  const segmentStartArc = arcs[segmentIndex];
  const segmentLengthM = haversineM(coords[segmentIndex], coords[segmentIndex + 1]);
  const intersectionArc = segmentStartArc + segmentLengthM * intersection.t;
  const totalArc = arcs[arcs.length - 1];

  const startDistanceM = intersectionArc;
  if (startDistanceM <= maxEndpointOvershootM) {
    return {
      side: "start",
      distanceM: startDistanceM,
      point: intersection.point,
      segmentIndex,
    };
  }

  const endDistanceM = totalArc - intersectionArc;
  if (endDistanceM <= maxEndpointOvershootM) {
    return {
      side: "end",
      distanceM: endDistanceM,
      point: intersection.point,
      segmentIndex,
    };
  }
  return null;
}

/**
 * @param {Array<GeoJSON.Feature>} features
 * @param {object} [options]
 * @param {number} [options.maxEndpointOvershootM=70]
 * @param {number} [options.minSegmentM=0.5]
 * @returns {{ features: Array, repairCount: number, repairs: Array }}
 */
export function repairSameRouteEndpointCrossings(
  features: JunctionFeature[],
  options: SameRouteJunctionOptions = {},
): { features: JunctionFeature[]; repairCount: number; repairs: JunctionRepair[] } {
  const {
    maxEndpointOvershootM = 70,
    minSegmentM = 0.5,
    allowSameColorSiblingRoutes = true,
  } = options;

  const lines = features
    .map((feature, index) => ({ feature, index }))
    .filter(({ feature }) =>
      feature.geometry?.type === "LineString" &&
      Array.isArray(feature.geometry.coordinates) &&
      feature.geometry.coordinates.length >= 2,
    );

  const bestByFeatureSide = new Map();

  function consider(index: number, candidate: EndpointCandidate | null, otherIndex: number): void {
    if (!candidate) return;
    const key = `${index}:${candidate.side}`;
    const existing = bestByFeatureSide.get(key);
    if (!existing || candidate.distanceM < existing.distanceM) {
      bestByFeatureSide.set(key, { ...candidate, index, otherIndex });
    }
  }

  for (let i = 0; i < lines.length; i += 1) {
    const left = lines[i].feature;
    const leftCoords = left.geometry.coordinates;
    for (let j = i + 1; j < lines.length; j += 1) {
      const right = lines[j].feature;
      if (!compatibleJunctionColor(left, right, allowSameColorSiblingRoutes)) continue;

      const rightCoords = right.geometry.coordinates;
      const leftArcs = cumulativeArc(leftCoords);
      const rightArcs = cumulativeArc(rightCoords);
      for (let li = 0; li < leftCoords.length - 1; li += 1) {
        for (let ri = 0; ri < rightCoords.length - 1; ri += 1) {
          const intersection = segmentIntersection(
            leftCoords[li],
            leftCoords[li + 1],
            rightCoords[ri],
            rightCoords[ri + 1],
          );
          if (!intersection) continue;

          consider(
            lines[i].index,
            candidateForSegment(leftCoords, leftArcs, li, intersection, maxEndpointOvershootM),
            lines[j].index,
          );
          const rightIntersection = {
            ...intersection,
            t: intersection.u,
          };
          consider(
            lines[j].index,
            candidateForSegment(rightCoords, rightArcs, ri, rightIntersection, maxEndpointOvershootM),
            lines[i].index,
          );
        }
      }
    }
  }

  if (bestByFeatureSide.size === 0) {
    return { features, repairCount: 0, repairs: [] };
  }

  const repairsByIndex = new Map<number, IndexedEndpointCandidate[]>();
  for (const candidate of bestByFeatureSide.values()) {
    if (!repairsByIndex.has(candidate.index)) repairsByIndex.set(candidate.index, []);
    repairsByIndex.get(candidate.index)?.push(candidate);
  }

  const nextFeatures = features.slice();
  const repairs: JunctionRepair[] = [];
  for (const [index, candidates] of repairsByIndex.entries()) {
    let coords = features[index].geometry.coordinates;
    for (const candidate of candidates.sort((a, b) => a.side.localeCompare(b.side))) {
      coords = normalizeEndpoint(coords, candidate.side, candidate.point, minSegmentM, candidate.segmentIndex);
      repairs.push({
        corridor_id: features[index].properties?.corridor_id ?? features[index].properties?.bundle_id ?? null,
        side: candidate.side,
        distance_m: Number(candidate.distanceM.toFixed(2)),
        point: candidate.point,
        other_index: candidate.otherIndex,
      });
    }
    nextFeatures[index] = cloneFeatureWithCoordinates(features[index], coords);
    nextFeatures[index].properties.same_route_junction_fabric_repairs = candidates.map((candidate) => ({
      side: candidate.side,
      distance_m: Number(candidate.distanceM.toFixed(2)),
    }));
  }

  return {
    features: nextFeatures,
    repairCount: repairs.length,
    repairs,
  };
}
