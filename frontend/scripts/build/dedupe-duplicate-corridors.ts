// Pure helper -- no fs, no globals. Removes a same-route corridor that is a
// near-duplicate (runs within parallelDistM of, and is >= overlapRatioMin
// contained by) a LONGER corridor that carries all of its routes. This kills
// duplicate OpenData full-line corridors -- e.g. the solo-E opendata-00028 that
// runs ~17 m alongside the real A/C/E spine opendata-00015 and renders as a
// stray parallel blue line. Genuine divergent same-route branches are kept.

type Coordinate = [number, number];

type LineStringFeature = {
  type?: "Feature";
  geometry?: {
    type?: string;
    coordinates?: Coordinate[];
  };
  properties?: {
    corridor_id?: string;
    route_ids?: string[];
    [key: string]: unknown;
  };
  [key: string]: unknown;
};

type DedupeOptions = {
  parallelDistM?: number;
  overlapRatioMin?: number;
};

const EARTH_RADIUS_M = 6371000;

function haversineM([lon1, lat1]: Coordinate, [lon2, lat2]: Coordinate): number {
  const r = Math.PI / 180;
  const dLat = (lat2 - lat1) * r;
  const dLon = (lon2 - lon1) * r;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(a));
}

function lengthM(coords: Coordinate[]): number {
  let sum = 0;
  for (let i = 1; i < coords.length; i += 1) {
    sum += haversineM(coords[i - 1], coords[i]);
  }
  return sum;
}

function sample(coords: Coordinate[], stepM: number): Coordinate[] {
  const out: Coordinate[] = [coords[0]];
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

function nearestM(point: Coordinate, points: Coordinate[]): number {
  let best = Infinity;
  for (const candidate of points) {
    const d = haversineM(point, candidate);
    if (d < best) best = d;
  }
  return best;
}

// Fraction of `shortCoords` vertices within distM of any vertex of the long line.
function containment(shortCoords: Coordinate[], longSamples: Coordinate[], distM: number): number {
  const samples = sample(shortCoords, Math.max(4, distM * 0.8));
  let near = 0;
  for (const point of samples) {
    if (nearestM(point, longSamples) <= distM) near += 1;
  }
  return samples.length ? near / samples.length : 0;
}

// The longer corridor must carry EVERY route of the shorter (so we never drop a
// route's only representation).
function longCarriesAllShortRoutes(longF: LineStringFeature, shortF: LineStringFeature): boolean {
  const longSet = new Set(longF.properties?.route_ids ?? []);
  const shortRoutes = shortF.properties?.route_ids ?? [];
  return shortRoutes.length > 0 && shortRoutes.every((routeId) => longSet.has(routeId));
}

function isLineFeature(feature: LineStringFeature): feature is LineStringFeature & {
  geometry: { type: "LineString"; coordinates: Coordinate[] };
} {
  return (
    feature.geometry?.type === "LineString" &&
    Array.isArray(feature.geometry.coordinates) &&
    feature.geometry.coordinates.length >= 2
  );
}

export function dedupeDuplicateCorridors(
  features: LineStringFeature[],
  options: DedupeOptions = {},
): { features: LineStringFeature[]; removedIds: string[] } {
  const { parallelDistM = 25, overlapRatioMin = 0.8 } = options;
  const lines = features.filter(isLineFeature);
  const withLen = lines
    .map((f) => ({ f, len: lengthM(f.geometry.coordinates) }))
    .sort((a, b) => b.len - a.len);

  const removedIds: string[] = [];
  const removedFeatures = new Set<LineStringFeature>();
  const kept: Array<{ f: LineStringFeature; samples: Coordinate[] }> = [];
  for (const { f } of withLen) {
    let duplicate = false;
    for (const keptFeature of kept) {
      if (!longCarriesAllShortRoutes(keptFeature.f, f)) continue;
      if (containment(f.geometry.coordinates, keptFeature.samples, parallelDistM) >= overlapRatioMin) {
        duplicate = true;
        break;
      }
    }
    if (duplicate) {
      removedFeatures.add(f);
      const corridorId = f.properties?.corridor_id;
      if (corridorId) removedIds.push(corridorId);
    } else {
      kept.push({ f, samples: sample(f.geometry.coordinates, parallelDistM * 0.8) });
    }
  }

  const removed = new Set(removedIds);
  return {
    features: features.filter((f) => {
      const corridorId = f.properties?.corridor_id;
      return !removedFeatures.has(f) && (!corridorId || !removed.has(corridorId));
    }),
    removedIds,
  };
}
