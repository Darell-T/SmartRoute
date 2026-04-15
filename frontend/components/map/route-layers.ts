import polyline from "@mapbox/polyline";
import type { RouteStep } from "@/types";

const MTA_COLORS: Record<string, string> = {
  A: "#0039A6", C: "#0039A6", E: "#0039A6",
  B: "#FF6319", D: "#FF6319", F: "#FF6319", M: "#FF6319",
  G: "#6CBE45",
  J: "#996633", Z: "#996633",
  L: "#A7A9AC",
  N: "#FCCC0A", Q: "#FCCC0A", R: "#FCCC0A", W: "#FCCC0A",
  "1": "#EE352E", "2": "#EE352E", "3": "#EE352E",
  "4": "#00933C", "5": "#00933C", "6": "#00933C",
  "7": "#B933AD",
  S: "#808183",
  SI: "#00A9CE",
};

const BUS_COLOR = "#0057B8";
const WALK_COLOR = "#FFFFFF";

export function getLineColor(line: string): string {
  return MTA_COLORS[line.toUpperCase()] ?? "#FFD700";
}

export function decodePolyline(encoded: string): [number, number][] {
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

export function getStepDuration(type: string): number {
  return STEP_DURATION[type] ?? 1000;
}

function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  const n = h.length === 3
    ? h.split("").map((c) => c + c).join("")
    : h;
  const v = parseInt(n, 16);
  return [(v >> 16) & 255, (v >> 8) & 255, v & 255];
}

function segmentLengths(coords: [number, number][]): { lens: number[]; total: number } {
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
  const hex = step.line_color || getLineColor(step.train_line || "");
  return hexToRgb(hex);
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
        for (let i = 1; i < coords.length; i++) timestamps.push(cursor + duration);
      } else {
        let traveled = 0;
        for (let i = 0; i < lens.length; i++) {
          traveled += lens[i];
          timestamps.push(cursor + duration * invEaseOutCubic(traveled / total));
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
