import type { Position } from "./types.ts";

export function offsetPolylineOverExtent(
  coords: Position[],
  startArc: number,
  endArc: number,
  offsetMeters: number,
  taperM?: number,
): Position[];
