// frontend/scripts/build/spine.mjs
// Pure helpers -- no fs, no globals. Imported by build-subway-visual-network.mjs.

const EARTH_RADIUS_M = 6371000;

function haversineM([lon1, lat1], [lon2, lat2]) {
  const toRad = (d) => (d * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(a));
}

function resample(coords, stepM) {
  // Walk the polyline, emit a point every stepM meters.
  if (coords.length < 2) return coords.slice();
  const out = [coords[0]];
  let carry = 0;
  for (let i = 1; i < coords.length; i++) {
    const a = coords[i - 1];
    const b = coords[i];
    const segLen = haversineM(a, b);
    let consumed = -carry;
    while (consumed + stepM <= segLen) {
      consumed += stepM;
      const t = consumed / segLen;
      out.push([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]);
    }
    carry = segLen - consumed;
  }
  const last = coords[coords.length - 1];
  const lastOut = out[out.length - 1];
  const dx = lastOut[0] - last[0];
  const dy = lastOut[1] - last[1];
  if (dx * dx + dy * dy > 1e-18) out.push(last);
  return out;
}

function nearestVertexDistanceM(point, polyline) {
  let best = Infinity;
  for (const p of polyline) {
    const d = haversineM(point, p);
    if (d < best) best = d;
  }
  return best;
}

function bidirectionalHausdorff(a, b, stepM = 5) {
  const ra = resample(a, stepM);
  const rb = resample(b, stepM);
  let ab = 0;
  for (const p of ra) {
    const d = nearestVertexDistanceM(p, rb);
    if (d > ab) ab = d;
  }
  let ba = 0;
  for (const p of rb) {
    const d = nearestVertexDistanceM(p, ra);
    if (d > ba) ba = d;
  }
  return Math.max(ab, ba);
}

function corridorBboxExpandedDeg(coords, expandM) {
  let minLon = Infinity, minLat = Infinity, maxLon = -Infinity, maxLat = -Infinity;
  for (const [lon, lat] of coords) {
    if (lon < minLon) minLon = lon;
    if (lat < minLat) minLat = lat;
    if (lon > maxLon) maxLon = lon;
    if (lat > maxLat) maxLat = lat;
  }
  const midLat = (minLat + maxLat) / 2;
  const latDeg = expandM / 111320;
  const lonDeg = expandM / Math.max(1, 111320 * Math.cos((midLat * Math.PI) / 180));
  return [minLon - lonDeg, minLat - latDeg, maxLon + lonDeg, maxLat + latDeg];
}

function bboxOverlap(a, b) {
  return !(a[2] < b[0] || b[2] < a[0] || a[3] < b[1] || b[3] < a[1]);
}

// ---------------------------------------------------------------------------
// Reserved for Phase 1.5: pixel-skeleton spine merging across corridors.
// NOT called from build-subway-visual-network.mjs in Phase 1 (spine assignment
// is currently 1:1 per Gate 2C corridor via buildSpineFromCorridor below).
// Tests for these helpers still run to keep them shippable when needed.
// ---------------------------------------------------------------------------
export function groupCorridorsByPhysicalTrack(corridors, { hausdorffMaxM = 15 } = {}) {
  // Union-find over corridors using bidirectional Hausdorff < hausdorffMaxM.
  const parent = corridors.map((_, i) => i);
  const find = (i) => (parent[i] === i ? i : (parent[i] = find(parent[i])));
  const union = (i, j) => { parent[find(i)] = find(j); };

  const bboxes = corridors.map((c) => corridorBboxExpandedDeg(c.geometry.coordinates, hausdorffMaxM));
  for (let i = 0; i < corridors.length; i++) {
    for (let j = i + 1; j < corridors.length; j++) {
      if (!bboxOverlap(bboxes[i], bboxes[j])) continue;  // bbox prefilter
      const h = bidirectionalHausdorff(
        corridors[i].geometry.coordinates,
        corridors[j].geometry.coordinates,
      );
      if (h < hausdorffMaxM) union(i, j);
    }
  }

  const groupsByRoot = new Map();
  for (let i = 0; i < corridors.length; i++) {
    const r = find(i);
    if (!groupsByRoot.has(r)) groupsByRoot.set(r, []);
    groupsByRoot.get(r).push(corridors[i]);
  }

  return [...groupsByRoot.values()].map((members, idx) => ({
    spine_id: `spine-${String(idx + 1).padStart(5, "0")}`,
    corridor_ids: members.map((m) => m.corridor_id),
    corridors: members,
    route_ids: [...new Set(members.flatMap((m) => m.route_ids))].sort(),
  }));
}

export function selectSpine(group) {
  const longest = group.corridors.reduce((best, cur) =>
    (cur.length_m ?? 0) > (best.length_m ?? 0) ? cur : best,
  );
  return {
    spine_id: group.spine_id,
    base_corridor_id: longest.corridor_id,
    method: "longest_member_edge",
    geometry: longest.geometry,
    route_ids: group.route_ids,
    corridor_ids: group.corridor_ids,
  };
}

export function medialAxisFallback(group, _options = {}) {
  // TODO Phase 1.5: pixel skeletonization on just this group's members.
  // For now, fall back to selectSpine.
  return selectSpine(group);
}

// ---------------------------------------------------------------------------
// Stage D helpers: spine assignment (1:1 mapping from Gate 2C corridors)
// ---------------------------------------------------------------------------

export function computeBaseSpineHash(coords) {
  let h = 5381;
  for (const [lon, lat] of coords) {
    const s = `${lon.toFixed(6)},${lat.toFixed(6)};`;
    for (let i = 0; i < s.length; i++) {
      h = ((h << 5) + h + s.charCodeAt(i)) | 0;
    }
  }
  return `h${(h >>> 0).toString(36)}`;
}

export function buildSpineFromCorridor(corridorFeature) {
  if (!corridorFeature?.geometry?.coordinates) {
    throw new Error(`buildSpineFromCorridor: corridorFeature.geometry.coordinates is required (corridor_id=${corridorFeature?.properties?.corridor_id ?? "<unknown>"})`);
  }
  const p = corridorFeature.properties;
  const corridor_id = p.corridor_id;
  const coords = corridorFeature.geometry.coordinates;
  return {
    spine_id: `spine-${corridor_id}`,
    base_corridor_id: corridor_id,
    method: p.base_geometry_selection ?? "quality_density_length",
    geometry: corridorFeature.geometry,
    base_spine_hash: computeBaseSpineHash(coords),
    route_ids: p.route_ids ?? [],
    source_edge_ids: p.source_edge_ids ?? [],
    source_shape_ids: p.source_shape_ids ?? [],
    length_m: p.length_m ?? 0,
  };
}
