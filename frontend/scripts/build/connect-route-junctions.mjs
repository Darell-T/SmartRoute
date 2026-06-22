// Pure helper -- no fs, no globals.
//
// Connects same-route branches that end in a dangling stub a short distance from
// their own route's trunk (e.g. a branch peeling off an express line, or two ends
// of a route that the corridor split left ~30-140 m apart -- too far for the 28 m
// bridge pass). For each dangling endpoint it ADDS a short connector LineString to
// the nearest point on another feature carrying the same route. Purely additive:
// it never moves or deletes existing geometry, so it cannot regress a line. A turn
// guard keeps it from welding a hard backwards hook.

const EARTH_RADIUS_M = 6371000;
const M_PER_DEG_LAT = 110574;

function metersPerDegLng(lat) {
  return 111320 * Math.cos((lat * Math.PI) / 180);
}

function haversineM([lon1, lat1], [lon2, lat2]) {
  const r = Math.PI / 180;
  const dLat = (lat2 - lat1) * r;
  const dLon = (lon2 - lon1) * r;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(a));
}

function projectToPolyline(coords, p) {
  let best = null;
  const mPerLng = metersPerDegLng(p[1]);
  const px = p[0] * mPerLng;
  const py = p[1] * M_PER_DEG_LAT;
  for (let i = 0; i < coords.length - 1; i += 1) {
    const a = coords[i];
    const b = coords[i + 1];
    const ax = a[0] * mPerLng, ay = a[1] * M_PER_DEG_LAT;
    const bx = b[0] * mPerLng, by = b[1] * M_PER_DEG_LAT;
    const dx = bx - ax, dy = by - ay;
    const len2 = dx * dx + dy * dy || 1e-12;
    const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / len2));
    const point = [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
    const d = haversineM(point, p);
    if (!best || d < best.distM) best = { point, distM: d };
  }
  return best;
}

function sharesRoute(a, b) {
  const set = new Set(a.properties?.route_ids ?? []);
  return (b.properties?.route_ids ?? []).some((r) => set.has(r));
}

function headingDeg(a, b) {
  const mPerLng = metersPerDegLng(a[1]);
  return (Math.atan2(b[1] - a[1], (b[0] - a[0]) * mPerLng / M_PER_DEG_LAT) * 180) / Math.PI;
}

/**
 * @param {Array} features
 * @param {object} [options]
 * @param {number} [options.danglingDistM=10]  endpoints with a same-route vertex closer than this are already joined
 * @param {number} [options.maxConnectM=140]   connect across gaps up to this
 * @param {number} [options.maxTurnDeg=85]     reject connectors that turn more than this from the stub's heading
 * @returns {{ features: Array, connectorCount: number }}
 */
export function connectRouteJunctions(features, options = {}) {
  const { danglingDistM = 10, maxConnectM = 140, maxTurnDeg = 85 } = options;
  const lines = features.filter(
    (f) => f.geometry?.type === "LineString" && Array.isArray(f.geometry.coordinates) && f.geometry.coordinates.length >= 2,
  );

  const connectors = [];
  const madeKey = new Set();

  for (const f of lines) {
    const c = f.geometry.coordinates;
    for (const isStart of [true, false]) {
      const endpoint = isStart ? c[0] : c[c.length - 1];
      const inner = isStart ? c[1] : c[c.length - 2];

      // already joined to a same-route piece?
      let joined = false;
      let best = null; // { point, distM }
      for (const g of lines) {
        if (g === f) continue;
        if (!sharesRoute(f, g)) continue;
        const proj = projectToPolyline(g.geometry.coordinates, endpoint);
        if (!proj) continue;
        if (proj.distM <= danglingDistM) { joined = true; break; }
        if (proj.distM <= maxConnectM && (!best || proj.distM < best.distM)) best = proj;
      }
      if (joined || !best) continue;

      // turn guard: the connector should roughly continue the stub forward, not hook back.
      const stubHeading = headingDeg(inner, endpoint);     // direction the stub points outward
      const connHeading = headingDeg(endpoint, best.point); // direction to the trunk
      const turn = Math.abs(((connHeading - stubHeading + 540) % 360) - 180);
      if (turn > maxTurnDeg) continue;

      const key = [endpoint, best.point].map((p) => `${p[0].toFixed(5)},${p[1].toFixed(5)}`).sort().join("|");
      if (madeKey.has(key)) continue;
      madeKey.add(key);

      connectors.push({
        type: "Feature",
        geometry: { type: "LineString", coordinates: [endpoint, best.point] },
        properties: {
          ...f.properties,
          visual_feature_type: "route_junction_connector",
          route_junction_connector: true,
          junction_connect_gap_m: Number(best.distM.toFixed(2)),
          bundle_materialization_role: null,
          lane_slot: 0,
          render_lane_slot: 0,
          lane_offset_baked: true,
          corridor_id: `junction-connector-${connectors.length}`,
          bundle_id: `junction-connector-${connectors.length}`,
        },
      });
    }
  }

  return { features: [...features, ...connectors], connectorCount: connectors.length };
}
