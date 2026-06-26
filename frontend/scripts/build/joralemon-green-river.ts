import type { Feature, LineStringGeometry, Position } from "./types.ts";

type JoralemonBbox = {
  minLon: number;
  maxLon: number;
  minLat: number;
  maxLat: number;
};

type JoralemonFeatureProperties = {
  corridor_id?: string;
  color?: string;
  route_ids?: string[];
  joralemon_green_river_smoothed?: boolean;
  joralemon_green_river_start_arc_m?: number;
  joralemon_green_river_end_arc_m?: number;
  joralemon_green_river_replaced_length_m?: number;
  [key: string]: unknown;
};

type JoralemonFeature = Feature<LineStringGeometry, JoralemonFeatureProperties>;

type ArcRange = {
  arcs: number[];
  startArc: number;
  endArc: number;
};

type JoralemonOptions = {
  bbox?: JoralemonBbox;
  marginM?: number;
  sampleM?: number;
  tangentSampleM?: number;
  handleFrac?: number;
  maxHandleM?: number;
};

type JoralemonResult = {
  features: JoralemonFeature[];
  diagnostics: {
    applied: boolean;
    replaced_length_m: number;
  };
};

const EARTH_RADIUS_M = 6371000;
const M_PER_DEG_LAT = 110574;
const GREEN = "#00933C";

const DEFAULT_BBOX = {
  minLon: -74.0118,
  maxLon: -74.0064,
  minLat: 40.6970,
  maxLat: 40.7000,
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

function projectAt(point: Position, lat: number): Position {
  return [point[0] * metersPerDegLng(lat), point[1] * M_PER_DEG_LAT];
}

function unprojectAt(point: Position, lat: number): Position {
  return [point[0] / metersPerDegLng(lat), point[1] / M_PER_DEG_LAT];
}

function inBBox(point: Position, bbox: JoralemonBbox): boolean {
  return (
    point[0] >= bbox.minLon &&
    point[0] <= bbox.maxLon &&
    point[1] >= bbox.minLat &&
    point[1] <= bbox.maxLat
  );
}

function isLineFeature(feature: JoralemonFeature): boolean {
  return (
    feature?.geometry?.type === "LineString" &&
    Array.isArray(feature.geometry.coordinates) &&
    feature.geometry.coordinates.length >= 2
  );
}

function routeIdsOf(feature: JoralemonFeature): string[] {
  return (feature.properties?.route_ids ?? []).map(String);
}

function isTargetGreenFeature(feature: JoralemonFeature, bbox: JoralemonBbox): boolean {
  if (!isLineFeature(feature)) return false;
  const color = String(feature.properties?.color ?? "").toUpperCase();
  const routes = routeIdsOf(feature);
  return (
    color === GREEN.toUpperCase() &&
    routes.includes("4") &&
    routes.includes("5") &&
    feature.geometry.coordinates.some((coord) => inBBox(coord, bbox))
  );
}

function cumulativeArcs(coords: Position[]): number[] {
  const arcs = [0];
  for (let index = 1; index < coords.length; index += 1) {
    arcs.push(arcs[index - 1] + haversineM(coords[index - 1], coords[index]));
  }
  return arcs;
}

function clonePosition(coord: Position): Position {
  return [coord[0], coord[1]];
}

function interpolateAtArc(coords: Position[], arcs: number[], targetArc: number): Position {
  if (targetArc <= 0) return clonePosition(coords[0]);
  const total = arcs[arcs.length - 1] ?? 0;
  if (targetArc >= total) return clonePosition(coords[coords.length - 1]);

  for (let index = 1; index < arcs.length; index += 1) {
    if (arcs[index] >= targetArc) {
      const span = arcs[index] - arcs[index - 1];
      const t = span <= 0 ? 0 : (targetArc - arcs[index - 1]) / span;
      const from = coords[index - 1];
      const to = coords[index];
      return [
        from[0] + (to[0] - from[0]) * t,
        from[1] + (to[1] - from[1]) * t,
      ];
    }
  }

  return clonePosition(coords[coords.length - 1]);
}

function arcRangeForBBox(coords: Position[], bbox: JoralemonBbox, marginM: number): ArcRange | null {
  const arcs = cumulativeArcs(coords);
  const inside: number[] = [];
  for (let index = 0; index < coords.length; index += 1) {
    if (inBBox(coords[index], bbox)) inside.push(arcs[index]);
  }
  if (inside.length === 0) return null;

  const total = arcs[arcs.length - 1] ?? 0;
  return {
    arcs,
    startArc: Math.max(0, Math.min(...inside) - marginM),
    endArc: Math.min(total, Math.max(...inside) + marginM),
  };
}

function unitVector(from: Position, to: Position): Position {
  const refLat = (from[1] + to[1]) / 2;
  const a = projectAt(from, refLat);
  const b = projectAt(to, refLat);
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const length = Math.hypot(dx, dy);
  if (length < 1e-9) return [0, 0];
  return [dx / length, dy / length];
}

function tangentAtArc(
  coords: Position[],
  arcs: number[],
  arc: number,
  direction: number,
  sampleM: number,
): Position {
  const total = arcs[arcs.length - 1] ?? 0;
  const fromArc = Math.max(0, Math.min(total, arc - direction * sampleM));
  const toArc = Math.max(0, Math.min(total, arc + direction * sampleM));
  const from = interpolateAtArc(coords, arcs, Math.min(fromArc, toArc));
  const to = interpolateAtArc(coords, arcs, Math.max(fromArc, toArc));
  return direction >= 0 ? unitVector(from, to) : unitVector(to, from);
}

function hermiteCurve(
  start: Position,
  end: Position,
  startUnit: Position,
  endUnit: Position,
  sampleM: number,
  handleFrac: number,
  maxHandleM: number,
): Position[] {
  const refLat = (start[1] + end[1]) / 2;
  const p0 = projectAt(start, refLat);
  const p1 = projectAt(end, refLat);
  const chordM = Math.hypot(p1[0] - p0[0], p1[1] - p0[1]);
  const handleM = Math.min(maxHandleM, chordM * handleFrac);
  const m0 = [startUnit[0] * handleM, startUnit[1] * handleM];
  const m1 = [endUnit[0] * handleM, endUnit[1] * handleM];
  const steps = Math.max(16, Math.ceil(chordM / sampleM));
  const output: Position[] = [];

  for (let index = 0; index <= steps; index += 1) {
    const t = index / steps;
    const t2 = t * t;
    const t3 = t2 * t;
    output.push(unprojectAt([
      (2 * t3 - 3 * t2 + 1) * p0[0] +
        (t3 - 2 * t2 + t) * m0[0] +
        (-2 * t3 + 3 * t2) * p1[0] +
        (t3 - t2) * m1[0],
      (2 * t3 - 3 * t2 + 1) * p0[1] +
        (t3 - 2 * t2 + t) * m0[1] +
        (-2 * t3 + 3 * t2) * p1[1] +
        (t3 - t2) * m1[1],
    ], refLat));
  }

  return output;
}

function replaceArc(
  coords: Position[],
  range: ArcRange,
  options: Required<Pick<JoralemonOptions, "sampleM" | "tangentSampleM" | "handleFrac" | "maxHandleM">>,
): Position[] | null {
  const {
    sampleM,
    tangentSampleM,
    handleFrac,
    maxHandleM,
  } = options;
  const { arcs, startArc, endArc } = range;
  if (endArc - startArc < 40) return null;

  const start = interpolateAtArc(coords, arcs, startArc);
  const end = interpolateAtArc(coords, arcs, endArc);
  const startTangent = tangentAtArc(coords, arcs, startArc, 1, tangentSampleM);
  const endTangent = tangentAtArc(coords, arcs, endArc, 1, tangentSampleM);
  const curve = hermiteCurve(start, end, startTangent, endTangent, sampleM, handleFrac, maxHandleM);

  const output: Position[] = [];
  for (let index = 0; index < coords.length; index += 1) {
    if (arcs[index] < startArc) output.push(coords[index]);
  }
  if (output.length === 0 || haversineM(output[output.length - 1], curve[0]) > 0.05) {
    output.push(curve[0]);
  }
  for (let index = 1; index < curve.length; index += 1) {
    output.push(curve[index]);
  }
  for (let index = 0; index < coords.length; index += 1) {
    if (arcs[index] > endArc) output.push(coords[index]);
  }

  return output;
}

function featureWithCoordinates(
  feature: JoralemonFeature,
  coordinates: Position[],
  diagnostics: ArcRange,
): JoralemonFeature {
  return {
    ...feature,
    geometry: {
      ...feature.geometry,
      coordinates,
    },
    properties: {
      ...feature.properties,
      joralemon_green_river_smoothed: true,
      joralemon_green_river_start_arc_m: Number(diagnostics.startArc.toFixed(2)),
      joralemon_green_river_end_arc_m: Number(diagnostics.endArc.toFixed(2)),
      joralemon_green_river_replaced_length_m: Number((diagnostics.endArc - diagnostics.startArc).toFixed(2)),
    },
  };
}

export function applyJoralemonGreenRiverSmoothing(
  features: JoralemonFeature[],
  options: JoralemonOptions = {},
): JoralemonResult {
  const bbox = options.bbox ?? DEFAULT_BBOX;
  const marginM = options.marginM ?? 260;
  const sampleM = options.sampleM ?? 6;
  const tangentSampleM = options.tangentSampleM ?? 90;
  const handleFrac = options.handleFrac ?? 0.45;
  const maxHandleM = options.maxHandleM ?? 420;
  let applied = false;
  let replacedLengthM = 0;

  const output = features.map((feature) => {
    if (!isTargetGreenFeature(feature, bbox)) return feature;
    const coords = feature.geometry.coordinates;
    const range = arcRangeForBBox(coords, bbox, marginM);
    if (!range) return feature;
    const nextCoords = replaceArc(coords, range, {
      sampleM,
      tangentSampleM,
      handleFrac,
      maxHandleM,
    });
    if (!nextCoords || nextCoords.length < 2) return feature;
    applied = true;
    replacedLengthM = range.endArc - range.startArc;
    return featureWithCoordinates(feature, nextCoords, range);
  });

  return {
    features: output,
    diagnostics: {
      applied,
      replaced_length_m: Number(replacedLengthM.toFixed(2)),
    },
  };
}
