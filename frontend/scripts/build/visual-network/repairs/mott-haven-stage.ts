import {
  buildMottHavenFiveSchematicLens,
  buildMottHavenSixSchematicMerge,
} from "../../mott-haven-schematic.ts";
import { hermiteBetween } from "../../offset-bow.ts";
import { smoothSharpCorners } from "../../smooth-polyline.ts";
import type { LineFeature, Position } from "../shared/types.ts";
import {
  M_PER_DEG_LAT,
  distanceMeters,
  metersPerDegLng,
} from "../shared/geometry-utils.ts";
import { routeIdsOf } from "../shared/route-config.ts";

type MottHavenBundleArtifacts = {
  visualFeatures?: LineFeature[];
};

type MottHavenStageInput = {
  bundleArtifacts: MottHavenBundleArtifacts;
};

type MottHavenJunction = {
  trunk: LineFeature;
  branch5: LineFeature;
  fourStem?: LineFeature;
  twoReference?: LineFeature;
  sixBranch?: LineFeature;
  sixShared?: LineFeature;
};

type MainlineStraightenResult = {
  coords: Position[];
  straightened: boolean;
  maxBearingDevDeg: number;
};

type MottHavenQaState = {
  lensApplied: boolean;
  lensBowWidthM: number;
  lensRejoinM: number;
  fourContinuous: boolean;
  mainlineStraightened: boolean;
  mainlineMaxBearingDevDeg: number;
  lensTopApproachLatSpreadM: number;
  lensMaxTurnDeg: number;
  lensParallelReferenceUsed: boolean;
  lensParallelReferenceDistanceM: number;
  sixMergeApplied: boolean;
  sixMergeRejoinM: number;
  sixMergeMaxTurnDeg: number;
};

const GREEN = "#00933C";
const RED = "#EE352E";

// Visual QA gate: route-5 visual geometry near 149 St / Mott Haven must use a
// compact south-side schematic peel. GTFS is still used to remove bad OpenData
// excursions first, but the literal GTFS curl is not the Apple/Transit visual.
const MOTT_HAVEN_5_QA_BBOX = { minLon: -73.9335, maxLon: -73.9230, minLat: 40.8105, maxLat: 40.8230 };
const LENS_SPAN_M = 310;
const SIX_MERGE_SPAN_M = 520;
const LENS_STRAIGHTEN_TO_LAT = 40.806;

function inMottHavenBbox(point: Position): boolean {
  return (
    point[0] >= MOTT_HAVEN_5_QA_BBOX.minLon &&
    point[0] <= MOTT_HAVEN_5_QA_BBOX.maxLon &&
    point[1] >= MOTT_HAVEN_5_QA_BBOX.minLat &&
    point[1] <= MOTT_HAVEN_5_QA_BBOX.maxLat
  );
}

function findGreenMember(
  features: LineFeature[],
  pred: (routes: string[]) => boolean,
): LineFeature | undefined {
  return features.find((feature) => (((feature).geometry?.type === "LineString" &&
    String((feature).properties?.color ?? "").toUpperCase() === GREEN &&
    (feature).geometry.coordinates.some(inMottHavenBbox))) && pred(routeIdsOf(feature.properties)));
}

function findMottHavenJunction(features: LineFeature[]): MottHavenJunction | null {
  const trunk = findGreenMember(features, (routes) => routes.includes("4") && routes.includes("5"));
  const branch5 = findGreenMember(features, (routes) => routes.includes("5") && !routes.includes("4"));
  if (!trunk || !branch5) return null;
  return {
    trunk,
    branch5,
    fourStem: findGreenMember(features, (routes) => routes.includes("4") && !routes.includes("5")),
    twoReference: features.find((feature) => (
      feature.geometry?.type === "LineString" &&
      String(feature.properties?.color ?? "").toUpperCase() === RED &&
      routeIdsOf(feature.properties).includes("2") &&
      feature.geometry.coordinates.some(inMottHavenBbox)
    )),
    sixBranch: findGreenMember(features, (routes) => routes.includes("6") && !routes.includes("4")),
    sixShared: findGreenMember(features, (routes) => routes.includes("4") && routes.includes("6")),
  };
}

function straightenMottHavenMainline(
  trunk: LineFeature,
  fourStem: LineFeature | undefined,
): MainlineStraightenResult {
  let avenueDir: Position | undefined;
  const trunkCoords = trunk.geometry.coordinates;
  if (fourStem) {
    const sc = fourStem.geometry.coordinates;
    const ks = metersPerDegLng(sc[sc.length - 1][1]);
    const from = sc[Math.max(0, sc.length - 6)];
    const d: Position = [
      (sc[sc.length - 1][0] - from[0]) * ks,
      (sc[sc.length - 1][1] - from[1]) * M_PER_DEG_LAT,
    ];
    const length = Math.hypot(d[0], d[1]);
    if (length > 1) avenueDir = [d[0] / length, d[1] / length];
  }
  if (!avenueDir) {
    const k0 = metersPerDegLng(trunkCoords[0][1]);
    const j = Math.min(8, trunkCoords.length - 1);
    const d: Position = [
      (trunkCoords[j][0] - trunkCoords[0][0]) * k0,
      (trunkCoords[j][1] - trunkCoords[0][1]) * M_PER_DEG_LAT,
    ];
    const length = Math.hypot(d[0], d[1]) || 1;
    avenueDir = [d[0] / length, d[1] / length];
  }
  let coords = trunk.geometry.coordinates;
  if (avenueDir[1] >= 0) return { coords, straightened: false, maxBearingDevDeg: 0 };
  let blendIdx = -1;
  for (let i = 1; i < coords.length; i += 1) {
    if (coords[i][1] <= LENS_STRAIGHTEN_TO_LAT - 0.002) {
      blendIdx = i;
      break;
    }
  }
  if (blendIdx <= 4) return { coords, straightened: false, maxBearingDevDeg: 0 };
  const start = coords[0];
  const kA = metersPerDegLng(start[1]);
  const rayLenM = Math.abs(((LENS_STRAIGHTEN_TO_LAT - start[1]) * M_PER_DEG_LAT) / avenueDir[1]);
  const ray: Position[] = [];
  for (let d = 0; d <= rayLenM; d += 10) {
    ray.push([start[0] + (avenueDir[0] * d) / kA, start[1] + (avenueDir[1] * d) / M_PER_DEG_LAT]);
  }
  const rEnd = ray[ray.length - 1];
  const blendPoint = coords[blendIdx];
  const kB = metersPerDegLng(blendPoint[1]);
  const b2 = coords[Math.min(coords.length - 1, blendIdx + 8)];
  const eT = [(b2[0] - blendPoint[0]) * kB, (b2[1] - blendPoint[1]) * M_PER_DEG_LAT];
  const eL = Math.hypot(eT[0], eT[1]) || 1;
  const blendSeg = hermiteBetween(rEnd, blendPoint, avenueDir, [eT[0] / eL, eT[1] / eL], {
    handleFrac: 0.5,
    sampleM: 8,
  });
  coords = smoothSharpCorners(
    [...ray, ...blendSeg.slice(1), ...coords.slice(blendIdx + 1)],
    { angleThresholdDeg: 22, iterations: 3, ratio: 0.2, maxFilletM: 18 },
  );
  trunk.geometry.coordinates = coords;
  trunk.properties.mott_haven_mainline_straightened = true;
  return {
    coords,
    straightened: true,
    maxBearingDevDeg: maxMainlineBearingDev(coords, start, avenueDir),
  };
}

function maxMainlineBearingDev(coords: Position[], start: Position, avenueDir: Position): number {
  const baseBear = (Math.atan2(avenueDir[1], avenueDir[0]) * 180) / Math.PI;
  let maxDev = 0;
  for (let i = 1; i < coords.length; i += 1) {
    if (coords[i][1] > start[1] || coords[i][1] < LENS_STRAIGHTEN_TO_LAT + 0.002) continue;
    const kk = metersPerDegLng(coords[i][1]);
    const seg = [(coords[i][0] - coords[i - 1][0]) * kk, (coords[i][1] - coords[i - 1][1]) * M_PER_DEG_LAT];
    if (Math.hypot(seg[0], seg[1]) < 1) continue;
    let dev = (Math.atan2(seg[1], seg[0]) * 180) / Math.PI - baseBear;
    while (dev > 180) dev -= 360;
    while (dev < -180) dev += 360;
    maxDev = Math.max(maxDev, Math.abs(dev));
  }
  return maxDev;
}

function joinFourStemToTrunk(fourStem: LineFeature | undefined, trunkCoords: Position[]): boolean {
  if (!fourStem) return false;
  const sc = fourStem.geometry.coordinates;
  const gap = distanceMeters(sc[sc.length - 1], trunkCoords[0]);
  if (gap <= 20 || gap >= 400) return false;
  const ks = metersPerDegLng(sc[sc.length - 1][1]);
  const from = sc[Math.max(0, sc.length - 5)];
  const sT = [(sc[sc.length - 1][0] - from[0]) * ks, (sc[sc.length - 1][1] - from[1]) * M_PER_DEG_LAT];
  const sl = Math.hypot(sT[0], sT[1]) || 1;
  const toward = trunkCoords[Math.min(4, trunkCoords.length - 1)];
  const eT = [(toward[0] - trunkCoords[0][0]) * ks, (toward[1] - trunkCoords[0][1]) * M_PER_DEG_LAT];
  const el = Math.hypot(eT[0], eT[1]) || 1;
  const conn = hermiteBetween(
    sc[sc.length - 1],
    trunkCoords[0],
    [sT[0] / sl, sT[1] / sl],
    [eT[0] / el, eT[1] / el],
    { handleFrac: 0.5, sampleM: 6 },
  );
  fourStem.geometry.coordinates = [...sc, ...conn.slice(1)];
  fourStem.properties.mott_haven_four_continuity = true;
  return true;
}

function authorFiveSchematicLens(
  junction: MottHavenJunction,
  trunkCoords: Position[],
  qa: MottHavenQaState,
): void {
  const lens = buildMottHavenFiveSchematicLens({
    branchCoords: junction.branch5.geometry.coordinates,
    trunkCoords: junction.fourStem
      ? [...junction.fourStem.geometry.coordinates, ...trunkCoords.slice(1)]
      : trunkCoords,
    parallelReferenceCoords: junction.twoReference?.geometry?.coordinates ?? null,
    parallelOffsetM: 10,
    mergeDistanceM: LENS_SPAN_M,
    sampleM: 6,
  });
  if (!lens.diagnostics.ok) return;
  applyFiveLensResult(junction.branch5, qa, lens);
}

function applyFiveLensResult(
  branch: LineFeature,
  qa: MottHavenQaState,
  lens: ReturnType<typeof buildMottHavenFiveSchematicLens>,
): void {
  const { diagnostics } = lens;
  const props = branch.properties;
  props.mott_haven_lens = true;
  props.mott_haven_schematic_lens = true;
  props.mott_haven_lens_entry_point = diagnostics.entryPoint;
  props.mott_haven_lens_top_point = diagnostics.topPoint;
  props.mott_haven_lens_merge_point = diagnostics.mergePoint;
  props.mott_haven_lens_top_spread_m = Number((diagnostics.topApproachLatSpreadM ?? 0).toFixed(2));
  props.mott_haven_lens_max_turn_deg = Number((diagnostics.maxTurnDeg ?? 0).toFixed(2));
  props.mott_haven_parallel_reference_used = diagnostics.parallelReferenceUsed;
  props.mott_haven_parallel_reference_distance_m = diagnostics.parallelReferenceDistanceM == null
    ? null
    : Number(diagnostics.parallelReferenceDistanceM.toFixed(2));
  branch.geometry.coordinates = lens.coordinates;
  qa.lensApplied = true;
  qa.lensBowWidthM = diagnostics.maxTrunkDistanceM ?? 0;
  qa.lensRejoinM = diagnostics.mergeDistanceM ?? Infinity;
  qa.lensTopApproachLatSpreadM = diagnostics.topApproachLatSpreadM ?? Infinity;
  qa.lensMaxTurnDeg = diagnostics.maxTurnDeg ?? Infinity;
  qa.lensParallelReferenceUsed = Boolean(diagnostics.parallelReferenceUsed);
  qa.lensParallelReferenceDistanceM = diagnostics.parallelReferenceDistanceM ?? Infinity;
}

function authorSixSchematicMerge(
  junction: MottHavenJunction,
  trunkCoords: Position[],
  qa: MottHavenQaState,
): void {
  if (!junction.sixBranch || !junction.sixShared) return;
  const sixMerge = buildMottHavenSixSchematicMerge({
    branchCoords: junction.sixBranch.geometry.coordinates,
    mainlineCoords: trunkCoords,
    mergeDistanceM: SIX_MERGE_SPAN_M,
    entryEastM: 430,
    entryNorthM: 120,
    sampleM: 6,
  });
  if (!sixMerge.diagnostics.ok) return;
  junction.sixBranch.geometry.coordinates = sixMerge.coordinates;
  junction.sixBranch.properties.mott_haven_six_merge = true;
  junction.sixBranch.properties.mott_haven_six_merge_point = sixMerge.diagnostics.mergePoint;
  junction.sixBranch.properties.mott_haven_six_merge_max_turn_deg =
    Number((sixMerge.diagnostics.maxTurnDeg ?? 0).toFixed(2));
  junction.sixBranch.properties.mott_haven_six_merge_rejoin_m =
    Number((sixMerge.diagnostics.mergeDistanceM ?? 0).toFixed(2));
  junction.sixShared.geometry.coordinates = sixMerge.sharedMainlineCoords;
  junction.sixShared.properties.mott_haven_six_shared_mainline = true;
  junction.sixShared.properties.mott_haven_six_merge_point = sixMerge.diagnostics.mergePoint;
  qa.sixMergeApplied = true;
  qa.sixMergeRejoinM = sixMerge.diagnostics.mergeDistanceM ?? Infinity;
  qa.sixMergeMaxTurnDeg = sixMerge.diagnostics.maxTurnDeg ?? Infinity;
}

function logMottHavenQa(qa: MottHavenQaState): void {
  let pass = true;
  if ([
    qa.lensApplied,
    qa.lensBowWidthM >= 120,
    qa.lensBowWidthM <= 260,
    qa.lensRejoinM <= 4,
    qa.lensMaxTurnDeg <= 65,
  ].includes(false)) pass = false;
  const lensReferenceOk = qa.lensParallelReferenceUsed
    ? qa.lensParallelReferenceDistanceM <= 25
    : qa.lensTopApproachLatSpreadM <= 15;
  if (!lensReferenceOk) pass = false;
  if (!qa.fourContinuous || !qa.mainlineStraightened) pass = false;
  if (qa.mainlineMaxBearingDevDeg > 6) pass = false;
  if (!(qa.sixMergeApplied && qa.sixMergeRejoinM <= 2 && qa.sixMergeMaxTurnDeg <= 70)) pass = false;
  console.log(
    `[visual-network] QA Mott Haven 5/6 schematic:  five_applied=${qa.lensApplied} four_continuous=${qa.fourContinuous} bow=${qa.lensBowWidthM.toFixed(0)}m rejoin=${qa.lensRejoinM.toFixed(1)}m top_spread=${qa.lensTopApproachLatSpreadM.toFixed(1)}m parallel_ref=${qa.lensParallelReferenceUsed}:${qa.lensParallelReferenceDistanceM.toFixed(1)}m max_turn=${qa.lensMaxTurnDeg.toFixed(1)}deg straightened=${qa.mainlineStraightened} bearing_dev=${qa.mainlineMaxBearingDevDeg.toFixed(1)}deg six_merge=${qa.sixMergeApplied}:${qa.sixMergeRejoinM.toFixed(1)}m/${qa.sixMergeMaxTurnDeg.toFixed(1)}deg ${pass ? "PASS" : "FAIL"}`,
  );
  if (pass) return;
  console.error(
    "[visual-network] *** QA FAIL: Mott Haven 5/6 schematic (5 lens, 4 continuity, straight mainline, or lower 6 merge) not authored as expected. ***",
  );
  process.exit(1);
}

export function applyMottHavenStage({
  bundleArtifacts,
}: MottHavenStageInput): void {
  // Applied after the general geometry cleanup below. The Mott Haven 5 junction is
  // a cartographic exception: the GTFS-supported curl is technically valid, but it
  // renders as a north-side loop. Apple/Transit schematize it as a compact
  // south-side peel from E 149 St into the 4/5 Grand Concourse stem.
  if (!bundleArtifacts.visualFeatures) return;
  const junction = findMottHavenJunction(bundleArtifacts.visualFeatures);
  const qa = ({
    lensApplied: false,
    lensBowWidthM: 0,
    lensRejoinM: Infinity,
    fourContinuous: false,
    mainlineStraightened: false,
    mainlineMaxBearingDevDeg: 0,
    lensTopApproachLatSpreadM: Infinity,
    lensMaxTurnDeg: Infinity,
    lensParallelReferenceUsed: false,
    lensParallelReferenceDistanceM: Infinity,
    sixMergeApplied: false,
    sixMergeRejoinM: Infinity,
    sixMergeMaxTurnDeg: Infinity,
});
  if (junction) {
    const mainline = straightenMottHavenMainline(junction.trunk, junction.fourStem);
    qa.mainlineStraightened = mainline.straightened;
    qa.mainlineMaxBearingDevDeg = mainline.maxBearingDevDeg;
    qa.fourContinuous = joinFourStemToTrunk(junction.fourStem, mainline.coords);
    authorFiveSchematicLens(junction, mainline.coords, qa);
    authorSixSchematicMerge(junction, mainline.coords, qa);
  }
  logMottHavenQa(qa);
}
