import type { LineStringGeometry, Position } from "./types.ts";

type PhysicalBundleSpine = {
  spine_id: string;
  geometry: LineStringGeometry;
  length_m?: number | null;
};

type PairOverlapOptions = {
  resampleM?: number;
  distMaxM?: number;
};

type PairOverlapResult = {
  avgDistM: number;
  sharedFractionShorter: number;
  sharedLenM: number;
  tangentDeltaAvgDeg: number;
  shorterSpineId: string;
  longerSpineId: string;
};

export function computePairOverlap(
  spineA: PhysicalBundleSpine,
  spineB: PhysicalBundleSpine,
  options?: PairOverlapOptions,
): PairOverlapResult;

export function resamplePolyline(coords: Position[], stepM: number): Position[];

export function pointToPolylineMinDistM(point: Position, polyline: Position[]): number;
