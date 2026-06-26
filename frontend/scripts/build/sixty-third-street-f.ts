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

import type { Feature, LineStringGeometry, Position } from "./types.ts";

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
  [key: string]: unknown;
};

type SixtyThirdStreetFeature = Feature<LineStringGeometry, SixtyThirdStreetFeatureProperties>;

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

export function addSixtyThirdStreetF(
  features: SixtyThirdStreetFeature[] | null | undefined,
  options: AddSixtyThirdStreetFOptions = {},
): AddSixtyThirdStreetFSummary {
  const bbox = options.bbox ?? TUNNEL_BBOX;
  let updated = 0;

  for (const feature of features ?? []) {
    if (feature?.geometry?.type !== "LineString") continue;
    const props = feature.properties ?? {};
    if (String(props.color ?? "").toUpperCase() !== ORANGE) continue;
    const routeIds = props.route_ids ?? [];
    if (!routeIds.includes("M") || routeIds.includes("F")) continue;

    let hits = 0;
    for (const coord of feature.geometry.coordinates) {
      if (inBbox(coord, bbox)) {
        hits += 1;
        if (hits >= MIN_VERTICES_IN_BBOX) break;
      }
    }
    if (hits < MIN_VERTICES_IN_BBOX) continue;

    props.route_ids = ["F", ...routeIds].sort((a, b) =>
      a.localeCompare(b, "en", { numeric: true }),
    );
    if (Array.isArray(props.color_route_ids)) {
      props.color_route_ids = ["F", ...props.color_route_ids.filter((r) => r !== "F")]
        .sort((a, b) => a.localeCompare(b, "en", { numeric: true }));
    }
    props.sixty_third_f_membership_added = true;
    updated += 1;
  }

  return { updated };
}
