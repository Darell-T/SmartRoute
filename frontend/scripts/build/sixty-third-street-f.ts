// 63 St tunnel F membership.
//
// NYC OpenData draws the 63 St East River tunnel (Lexington Av-63 St ->
// Roosevelt Island -> 21 St-Queensbridge -> 36 St junction) as part of the
// "6 Avenue Local" M service line only; the F service line in the dataset
// does not include it. In reality -- and on the Apple Maps reference -- the
// 63 St tunnel is the F's crossing (the M uses it only on weekends), so the
// rendered F lane appeared out of nowhere at the 36 St junction.
//
// This is a membership-only authored pass in the spirit of the late-stage
// cartographic helpers (see docs/subway-visual-line-fixes-update-2026-06-07.md):
// it adds F to the route set of the orange features that traverse the tunnel
// bbox. Geometry is never touched; the color stays #FF6319.

import type { LineStringGeometry, Position } from "./types.ts";

type TunnelBbox = {
  minLon: number;
  maxLon: number;
  minLat: number;
  maxLat: number;
};

type SixtyThirdStreetFeatureProperties = {
  route_ids?: string[];
  color_route_ids?: string[];
  color?: string;
  sixty_third_f_membership_added?: boolean;
};

type SixtyThirdStreetFeature = {
  type: "Feature";
  geometry?: LineStringGeometry;
  properties?: SixtyThirdStreetFeatureProperties;
};

type AddSixtyThirdStreetFOptions = {
  bbox?: TunnelBbox;
};

type AddSixtyThirdStreetFSummary = {
  updated: number;
};

// Lexington Av-63 St through 21 St-Queensbridge, with margin.
const TUNNEL_BBOX = {
  minLon: -73.972,
  maxLon: -73.938,
  minLat: 40.752,
  maxLat: 40.767,
};

// The feature must genuinely run through the tunnel, not just clip a corner.
const MIN_VERTICES_IN_BBOX = 2;

const ORANGE = "#FF6319";

function inBbox(coord: Position, bbox: TunnelBbox): boolean {
  return (
    coord[0] >= bbox.minLon &&
    coord[0] <= bbox.maxLon &&
    coord[1] >= bbox.minLat &&
    coord[1] <= bbox.maxLat
  );
}

function sortedUniqueRoutes(routeIds: string[]): string[] {
  return [...routeIds].sort((a, b) => a.localeCompare(b, "en", { numeric: true }));
}

function vertexHitsInTunnel(coordinates: Position[], bbox: TunnelBbox): number {
  let hits = 0;
  for (const coord of coordinates) {
    if (!inBbox(coord, bbox)) continue;
    hits += 1;
    if (hits >= MIN_VERTICES_IN_BBOX) return hits;
  }
  return hits;
}

function isOrangeMTunnelWithoutF(
  feature: SixtyThirdStreetFeature,
  bbox: TunnelBbox,
): feature is SixtyThirdStreetFeature & { geometry: LineStringGeometry; properties: SixtyThirdStreetFeatureProperties } {
  if (feature.geometry?.type !== "LineString") return false;
  const props = feature.properties;
  if (!props) return false;
  if (String(props.color ?? "").toUpperCase() !== ORANGE) return false;
  const routeIds = props.route_ids ?? [];
  if (!routeIds.includes("M") || routeIds.includes("F")) return false;
  return vertexHitsInTunnel(feature.geometry.coordinates, bbox) >= MIN_VERTICES_IN_BBOX;
}

function addFMembership(props: SixtyThirdStreetFeatureProperties): void {
  const routeIds = props.route_ids ?? [];
  props.route_ids = sortedUniqueRoutes(["F", ...routeIds]);
  if (Array.isArray(props.color_route_ids)) {
    props.color_route_ids = sortedUniqueRoutes([
      "F",
      ...props.color_route_ids.filter((routeId) => routeId !== "F"),
    ]);
  }
  props.sixty_third_f_membership_added = true;
}

export function addSixtyThirdStreetF(
  features: SixtyThirdStreetFeature[] | null | undefined,
  options: AddSixtyThirdStreetFOptions = {},
): AddSixtyThirdStreetFSummary {
  const bbox = options.bbox ?? TUNNEL_BBOX;
  let updated = 0;

  for (const feature of features ?? []) {
    if (!isOrangeMTunnelWithoutF(feature, bbox)) continue;
    addFMembership(feature.properties);
    updated += 1;
  }

  return { updated };
}
