// Pure helper -- no fs, no globals. Removes a same-route corridor that is a
// near-duplicate (runs within parallelDistM of, and is >= overlapRatioMin
// contained by) a LONGER corridor that carries all of its routes. This kills
// duplicate OpenData full-line corridors -- e.g. the solo-E opendata-00028 that
// runs ~17 m alongside the real A/C/E spine opendata-00015 and renders as a stray
// parallel blue line. Genuine divergent same-route branches are kept.

const EARTH_RADIUS_M = 6371000;

function haversineM([lon1, lat1], [lon2, lat2]) {
  const r = Math.PI / 180;
  const dLat = (lat2 - lat1) * r;
  const dLon = (lon2 - lon1) * r;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(a));
}

function lengthM(coords) {
  let s = 0;
  for (let i = 1; i < coords.length; i += 1) s += haversineM(coords[i - 1], coords[i]);
  return s;
}

function sample(coords, stepM) {
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

function nearestM(p, pts) {
  let best = Infinity;
  for (const q of pts) {
    const d = haversineM(p, q);
    if (d < best) best = d;
  }
  return best;
}

// Fraction of `shortCoords` vertices within distM of any vertex of the long line.
function containment(shortCoords, longSamples, distM) {
  const s = sample(shortCoords, Math.max(4, distM * 0.8));
  let near = 0;
  for (const p of s) if (nearestM(p, longSamples) <= distM) near += 1;
  return s.length ? near / s.length : 0;
}

// The longer corridor must carry EVERY route of the shorter (so we never drop a
// route's only representation).
function longCarriesAllShortRoutes(longF, shortF) {
  const longSet = new Set(longF.properties?.route_ids ?? []);
  const shortRoutes = shortF.properties?.route_ids ?? [];
  return shortRoutes.length > 0 && shortRoutes.every((r) => longSet.has(r));
}

export function dedupeDuplicateCorridors(features, options = {}) {
  const { parallelDistM = 25, overlapRatioMin = 0.8 } = options;
  const lines = features.filter(
    (f) => f.geometry?.type === "LineString" && Array.isArray(f.geometry.coordinates) && f.geometry.coordinates.length >= 2,
  );
  const withLen = lines
    .map((f) => ({ f, len: lengthM(f.geometry.coordinates) }))
    .sort((a, b) => b.len - a.len);

  const removedIds = [];
  const kept = []; // { f, samples }
  for (const { f } of withLen) {
    let dup = false;
    for (const k of kept) {
      if (!longCarriesAllShortRoutes(k.f, f)) continue;
      if (containment(f.geometry.coordinates, k.samples, parallelDistM) >= overlapRatioMin) {
        dup = true;
        break;
      }
    }
    if (dup) removedIds.push(f.properties.corridor_id);
    else kept.push({ f, samples: sample(f.geometry.coordinates, parallelDistM * 0.8) });
  }

  const removed = new Set(removedIds);
  return {
    features: features.filter((f) => !removed.has(f.properties?.corridor_id)),
    removedIds,
  };
}
