// Hammels Wye (Rockaway) junction connector.
//
// The A's three legs meet at the wye south of Broad Channel: the cross-bay
// approach, the Far Rockaway leg, and the Rockaway Park leg. In the OpenData
// geometry the east/west legs share a junction node, but the cross-bay leg
// stops ~46m short of it and two degenerate ~6m stub features dangle at its
// end — rendered as a visible break with a hook. This authored pass (see
// docs/subway-visual-line-fixes-update-2026-06-07.md for the pattern):
//
//   1. removes the degenerate stubs inside the wye bbox,
//   2. extends any A endpoint that stops short of the junction node onto it,
//      so Broad Channel -> Far Rockaway reads as one continuous line with the
//      Rockaway Park leg branching off.

const DEG_LAT_M = 111320;

// Tight bbox around the wye throat.
const WYE_BBOX = {
  minLon: -73.8135,
  maxLon: -73.806,
  minLat: 40.589,
  maxLat: 40.597,
};
const STUB_MAX_M = 12; // degenerate artifacts
const SNAP_MAX_M = 80; // endpoints within this of the node get connected
const NODE_TOUCH_M = 8; // already at the node

function metersXY(coord, lat0) {
  return [coord[0] * DEG_LAT_M * Math.cos((lat0 * Math.PI) / 180), coord[1] * DEG_LAT_M];
}

function distM(a, b, lat0) {
  const [ax, ay] = metersXY(a, lat0);
  const [bx, by] = metersXY(b, lat0);
  return Math.hypot(ax - bx, ay - by);
}

function inBbox(coord) {
  return (
    coord[0] >= WYE_BBOX.minLon &&
    coord[0] <= WYE_BBOX.maxLon &&
    coord[1] >= WYE_BBOX.minLat &&
    coord[1] <= WYE_BBOX.maxLat
  );
}

function lineLength(coords, lat0) {
  let total = 0;
  for (let i = 1; i < coords.length; i += 1) total += distM(coords[i - 1], coords[i], lat0);
  return total;
}

export function connectRockawayWye(features) {
  const lat0 = (WYE_BBOX.minLat + WYE_BBOX.maxLat) / 2;

  // A-features with an endpoint inside the wye bbox.
  const legs = [];
  const stubs = [];
  features.forEach((f, idx) => {
    if (f?.geometry?.type !== "LineString") return;
    if (!(f.properties?.route_ids ?? []).includes("A")) return;
    const cs = f.geometry.coordinates;
    if (!inBbox(cs[0]) && !inBbox(cs[cs.length - 1])) return;
    if (lineLength(cs, lat0) <= STUB_MAX_M) stubs.push(idx);
    else legs.push(f);
  });
  if (legs.length < 3) {
    return { connected: false, stubsRemoved: 0, extended: 0 };
  }

  // Junction node: the endpoint shared by two of the legs (the east/west
  // pair touch). Pick the in-bbox endpoint that another leg's endpoint sits
  // closest to.
  const endpoints = [];
  for (const f of legs) {
    const cs = f.geometry.coordinates;
    for (const [pos, coord] of [["start", cs[0]], ["end", cs[cs.length - 1]]]) {
      if (inBbox(coord)) endpoints.push({ f, pos, coord });
    }
  }
  let node = null;
  let bestPair = Infinity;
  for (let i = 0; i < endpoints.length; i += 1) {
    for (let j = i + 1; j < endpoints.length; j += 1) {
      if (endpoints[i].f === endpoints[j].f) continue;
      const d = distM(endpoints[i].coord, endpoints[j].coord, lat0);
      if (d < bestPair) {
        bestPair = d;
        node = endpoints[i].coord;
      }
    }
  }
  if (!node || bestPair > NODE_TOUCH_M) {
    return { connected: false, stubsRemoved: 0, extended: 0 };
  }

  // Extend short-stopping endpoints onto the node.
  let extended = 0;
  for (const ep of endpoints) {
    const d = distM(ep.coord, node, lat0);
    if (d <= NODE_TOUCH_M || d > SNAP_MAX_M) continue;
    const cs = ep.f.geometry.coordinates;
    if (ep.pos === "end") cs.push([...node]);
    else cs.unshift([...node]);
    ep.f.properties.rockaway_wye_connected = true;
    if (typeof ep.f.properties.length_m === "number") {
      ep.f.properties.length_m += d;
    }
    extended += 1;
  }

  // Remove degenerate stubs (descending index order).
  stubs.sort((a, b) => b - a);
  for (const idx of stubs) features.splice(idx, 1);

  return { connected: true, stubsRemoved: stubs.length, extended };
}
