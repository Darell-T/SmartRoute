import polyline from "@mapbox/polyline";
import type { RouteStep } from "@/types";
import { getRouteColor } from "@/lib/mta-colors";

const BUS_COLOR = "#0057B8";
const WALK_COLOR = "#FFFFFF";

// Route line color comes from the single source of truth in lib/mta-colors.json.
export function getLineColor(line: string): string {
  return getRouteColor(line);
}

function decodePolyline(encoded: string): [number, number][] {
  const decoded = polyline.decode(encoded);
  return decoded.map(([lat, lng]: [number, number]) => [lng, lat]);
}

export type StepType = "WALK" | "SUBWAY" | "BUS" | string;

export interface Trip {
  path: [number, number][];
  timestamps: number[];
  color: [number, number, number];
  width: number;
  type: StepType;
}

export interface BuiltTrips {
  trips: Trip[];
  stepCoords: [number, number][][];
  stepEndTimes: number[];
  totalDuration: number;
}

const STEP_DURATION: Record<string, number> = {
  WALK: 1200,
  SUBWAY: 2400,
  BUS: 1800,
};
const STEP_PAUSE = 180;

function invEaseOutCubic(p: number): number {
  if (p <= 0) return 0;
  if (p >= 1) return 1;
  return 1 - Math.cbrt(1 - p);
}

function getStepDuration(type: string): number {
  return STEP_DURATION[type] ?? 1000;
}

function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  const n =
    h.length === 3
      ? h
          .split("")
          .map((c) => c + c)
          .join("")
      : h;
  const v = parseInt(n, 16);
  return [(v >> 16) & 255, (v >> 8) & 255, v & 255];
}

function segmentLengths(coords: [number, number][]): {
  lens: number[];
  total: number;
} {
  const lens: number[] = [];
  let total = 0;
  for (let i = 1; i < coords.length; i++) {
    const dx = coords[i][0] - coords[i - 1][0];
    const dy = coords[i][1] - coords[i - 1][1];
    const d = Math.sqrt(dx * dx + dy * dy);
    lens.push(d);
    total += d;
  }
  return { lens, total };
}

function stepColor(step: RouteStep): [number, number, number] {
  if (step.type === "WALK") return hexToRgb(WALK_COLOR);
  if (step.type === "BUS") return hexToRgb(BUS_COLOR);
  return hexToRgb(getLineColor(step.train_line || ""));
}

function stepWidth(step: RouteStep): number {
  if (step.type === "WALK") return 4;
  if (step.type === "BUS") return 5;
  return 6;
}

export function buildTrips(steps: RouteStep[], fitSettleMs = 1200): BuiltTrips {
  const trips: Trip[] = [];
  const stepCoords: [number, number][][] = [];
  const stepEndTimes: number[] = [];
  let cursor = fitSettleMs;

  for (const step of steps) {
    const coords: [number, number][] = step.polyline?.encodedPolyline
      ? decodePolyline(step.polyline.encodedPolyline)
      : [];
    stepCoords.push(coords);

    const duration = getStepDuration(step.type);

    if (coords.length >= 2) {
      const { lens, total } = segmentLengths(coords);
      const timestamps: number[] = [cursor];
      if (total === 0) {
        for (let i = 1; i < coords.length; i++)
          timestamps.push(cursor + duration);
      } else {
        let traveled = 0;
        for (let i = 0; i < lens.length; i++) {
          traveled += lens[i];
          timestamps.push(
            cursor + duration * invEaseOutCubic(traveled / total),
          );
        }
      }

      trips.push({
        path: coords,
        timestamps,
        color: stepColor(step),
        width: stepWidth(step),
        type: step.type,
      });
    }

    stepEndTimes.push(cursor + duration);
    cursor += duration + STEP_PAUSE;
  }

  return { trips, stepCoords, stepEndTimes, totalDuration: cursor };
}
