import { applyCartographicJunctionOverrides } from "../cartographic-junction-overrides.ts";
import {
  buildMottHavenFiveSchematicLens,
  buildMottHavenSixSchematicMerge,
} from "../mott-haven-schematic.ts";
import { hermiteBetween } from "../offset-bow.ts";
import { replaceEndpointHairpin } from "../schematic-hairpin-arc.ts";
import { smoothSharpCorners } from "../smooth-polyline.ts";
import type { LineFeature, Position } from "./types.ts";
import {
  M_PER_DEG_LAT,
  distanceMeters,
  metersPerDegLng,
} from "./geometry-utils.ts";

type MottHavenBundleArtifacts = {
  visualFeatures?: LineFeature[];
};

type MottHavenStageInput = {
  bundleArtifacts: MottHavenBundleArtifacts;
};

// Visual QA gate: route-5 visual geometry near 149 St / Mott Haven must use a
// compact south-side schematic peel. GTFS is still used to remove bad OpenData
// excursions first, but the literal GTFS curl is not the Apple/Transit visual.
const MOTT_HAVEN_5_QA_BBOX = { minLon: -73.9335, maxLon: -73.9230, minLat: 40.8105, maxLat: 40.8230 };
const MOTT_HAVEN_5_QA_MAX_NORTH_LAT = 40.81795;
const MOTT_HAVEN_5_QA_MAX_TRUNK_DISTANCE_M = 3;
const MOTT_HAVEN_5_QA_MIN_TRUNK_JOIN_M = 230;
const MOTT_HAVEN_5_QA_WEST_BOW_LON_MAX = -73.93025;

export function applyMottHavenStage({
  bundleArtifacts,
}: MottHavenStageInput): void {
  // ----- Scoped cartographic junction overrides -----
  // Applied after the general geometry cleanup below. The Mott Haven 5 junction is
  // a cartographic exception: the GTFS-supported curl is technically valid, but it
  // renders as a north-side loop. Apple/Transit schematize it as a compact
  // south-side peel from E 149 St into the 4/5 Grand Concourse stem.

  if (bundleArtifacts.visualFeatures) {
    // (My schematic-hairpin-arc pass removed: it competed with the cartographic
    // junction override below and produced a redundant parallel path / lens at
    // Mott Haven. The cartographic override owns the 5-branch reshape.)
    void replaceEndpointHairpin;

    // ----- Authored Mott Haven 5 lens (Apple / Transit schematic) -----
    // South of 149 St-Grand Concourse the 4 and 5 share track, but Apple Maps and
    // the Transit app draw them as two parallel lines: the 4 runs straight on Grand
    // Concourse and the 5 bows WEST via Walton Av, then they rejoin -- an elongated
    // lens. Neither OpenData nor GTFS contains that lens (both have the tight Mott
    // Haven curl), so it is AUTHORED here as the single owner of this junction:
    //   * the 4 is made continuous (its north stem is joined to the 4/5 trunk),
    //   * the 5 branch is rebuilt as a local schematic lens: flat along E 149 St,
    //     closed at the top trunk split, west via Walton Av, then lower Y-merged
    //     back into the 4/5 trunk.
    // This deliberately stops preserving the real 5-from-east curl inside the
    // junction. The real route only feeds the authored E 149 St entry.
    // (Supersedes the cartographic override, which collapsed the 5 onto the trunk.)
    void applyCartographicJunctionOverrides;
    const LENS_SPAN_M = 310;  // lower Y-merge distance from the authored top split
    const SIX_MERGE_SPAN_M = 520; // route 6 joins the straight trunk near the circled 138 St merge
    // Straighten the 4/5 mainline onto Grand Concourse through the junction view, then blend
    // back to the true track below this latitude (just under the typical view ~40.808). The
    // real track bends SW toward the Harlem River below the merge; Apple/Transit draw it
    // straight down Grand Concourse and push that curve off-screen. Lower = curve pushed
    // further down but larger divergence from the true track.
    const LENS_STRAIGHTEN_TO_LAT = 40.806;
    const inBBox = (p: Position) =>
      p[0] >= MOTT_HAVEN_5_QA_BBOX.minLon && p[0] <= MOTT_HAVEN_5_QA_BBOX.maxLon &&
      p[1] >= MOTT_HAVEN_5_QA_BBOX.minLat && p[1] <= MOTT_HAVEN_5_QA_BBOX.maxLat;
    const lensTrunk = bundleArtifacts.visualFeatures.find((f) => (
      f.geometry?.type === "LineString" &&
      String(f.properties?.color ?? "").toUpperCase() === "#00933C" &&
      (f.properties?.route_ids ?? []).map(String).includes("4") &&
      (f.properties?.route_ids ?? []).map(String).includes("5") &&
      f.geometry.coordinates.some(inBBox)
    ));
    const lensBranch = bundleArtifacts.visualFeatures.find((f) => {
      if (f.geometry?.type !== "LineString") return false;
      if (String(f.properties?.color ?? "").toUpperCase() !== "#00933C") return false;
      const r = (f.properties?.route_ids ?? []).map(String);
      return r.includes("5") && !r.includes("4") && f.geometry.coordinates.some(inBBox);
    });
    let lensApplied = false;
    let lensBowWidthM = 0;
    let lensRejoinM = Infinity;
    let fourContinuous = false;
    let mainlineStraightened = false;
    let mainlineMaxBearingDevDeg = 0;
    let lensTopApproachLatSpreadM = Infinity;
    let lensMaxTurnDeg = Infinity;
    let lensParallelReferenceUsed = false;
    let lensParallelReferenceDistanceM = Infinity;
    let sixMergeApplied = false;
    let sixMergeRejoinM = Infinity;
    let sixMergeMaxTurnDeg = Infinity;
    if (lensTrunk && lensBranch) {
      let tc = lensTrunk.geometry.coordinates;
      // ---- (a) find the [4] stem and the Grand Concourse avenue bearing ----
      const fourStem = bundleArtifacts.visualFeatures.find((f) => {
        if (f.geometry?.type !== "LineString") return false;
        if (String(f.properties?.color ?? "").toUpperCase() !== "#00933C") return false;
        const r = (f.properties?.route_ids ?? []).map(String);
        return r.includes("4") && !r.includes("5") && f.geometry.coordinates.some(inBBox);
      });
      const twoReference = bundleArtifacts.visualFeatures.find((f) => {
        if (f.geometry?.type !== "LineString") return false;
        if (String(f.properties?.color ?? "").toUpperCase() !== "#EE352E") return false;
        const r = (f.properties?.route_ids ?? []).map(String);
        return r.includes("2") && f.geometry.coordinates.some(inBBox);
      });
      const sixBranch = bundleArtifacts.visualFeatures.find((f) => {
        if (f.geometry?.type !== "LineString") return false;
        if (String(f.properties?.color ?? "").toUpperCase() !== "#00933C") return false;
        const r = (f.properties?.route_ids ?? []).map(String);
        return r.includes("6") && !r.includes("4") && f.geometry.coordinates.some(inBBox);
      });
      const sixShared = bundleArtifacts.visualFeatures.find((f) => {
        if (f.geometry?.type !== "LineString") return false;
        if (String(f.properties?.color ?? "").toUpperCase() !== "#00933C") return false;
        const r = (f.properties?.route_ids ?? []).map(String);
        return r.includes("4") && r.includes("6") && f.geometry.coordinates.some(inBBox);
      });
      let avenueDir = null; // unit southbound direction of Grand Concourse (meters)
      if (fourStem) {
        const sc = fourStem.geometry.coordinates;
        const ks = metersPerDegLng(sc[sc.length - 1][1]);
        const d = [(sc[sc.length - 1][0] - sc[Math.max(0, sc.length - 6)][0]) * ks, (sc[sc.length - 1][1] - sc[Math.max(0, sc.length - 6)][1]) * M_PER_DEG_LAT];
        const l = Math.hypot(d[0], d[1]);
        if (l > 1) avenueDir = [d[0] / l, d[1] / l];
      }
      if (!avenueDir) {
        const k0 = metersPerDegLng(tc[0][1]);
        const j = Math.min(8, tc.length - 1);
        const d = [(tc[j][0] - tc[0][0]) * k0, (tc[j][1] - tc[0][1]) * M_PER_DEG_LAT];
        const l = Math.hypot(d[0], d[1]) || 1;
        avenueDir = [d[0] / l, d[1] / l];
      }
      // ---- (b) straighten the 4/5 mainline onto Grand Concourse; blend back below the view ----
      // The real track bends SW toward the Harlem River below the merge; Apple/Transit draw it
      // straight down Grand Concourse and push that curve off-screen. Re-aim the trunk from the
      // junction along the avenue bearing, then Hermite-blend back to the true track below
      // LENS_STRAIGHTEN_TO_LAT. tc stays one feature with unchanged endpoints (connectivity safe).
      if (avenueDir[1] < 0) {
        let blendIdx = -1;
        for (let i = 1; i < tc.length; i += 1) { if (tc[i][1] <= LENS_STRAIGHTEN_TO_LAT - 0.002) { blendIdx = i; break; } }
        if (blendIdx > 4) {
          const A = tc[0];
          const kA = metersPerDegLng(A[1]);
          const rayLenM = Math.abs(((LENS_STRAIGHTEN_TO_LAT - A[1]) * M_PER_DEG_LAT) / avenueDir[1]);
          const ray: Position[] = [];
          for (let d = 0; d <= rayLenM; d += 10) ray.push([A[0] + (avenueDir[0] * d) / kA, A[1] + (avenueDir[1] * d) / M_PER_DEG_LAT]);
          const rEnd = ray[ray.length - 1];
          const B = tc[blendIdx];
          const kB = metersPerDegLng(B[1]);
          const b2 = tc[Math.min(tc.length - 1, blendIdx + 8)];
          const eT = [(b2[0] - B[0]) * kB, (b2[1] - B[1]) * M_PER_DEG_LAT];
          const eL = Math.hypot(eT[0], eT[1]) || 1;
          const blendSeg = hermiteBetween(rEnd as Position, B as Position, avenueDir as Position, [eT[0] / eL, eT[1] / eL], { handleFrac: 0.5, sampleM: 8 });
          let merged = [...ray, ...blendSeg.slice(1), ...tc.slice(blendIdx + 1)];
          merged = smoothSharpCorners(merged, { angleThresholdDeg: 22, iterations: 3, ratio: 0.2, maxFilletM: 18 });
          tc = merged;
          lensTrunk.geometry.coordinates = tc;
          lensTrunk.properties.mott_haven_mainline_straightened = true;
          mainlineStraightened = true;
          // QA: mainline bearing must be ~constant through the view (junction .. view bottom)
          const baseBear = (Math.atan2(avenueDir[1], avenueDir[0]) * 180) / Math.PI;
          for (let i = 1; i < tc.length; i += 1) {
            if (tc[i][1] > A[1] || tc[i][1] < LENS_STRAIGHTEN_TO_LAT + 0.002) continue;
            const kk = metersPerDegLng(tc[i][1]);
            const seg = [(tc[i][0] - tc[i - 1][0]) * kk, (tc[i][1] - tc[i - 1][1]) * M_PER_DEG_LAT];
            if (Math.hypot(seg[0], seg[1]) < 1) continue;
            let dev = (Math.atan2(seg[1], seg[0]) * 180) / Math.PI - baseBear;
            while (dev > 180) dev -= 360; while (dev < -180) dev += 360;
            mainlineMaxBearingDevDeg = Math.max(mainlineMaxBearingDevDeg, Math.abs(dev));
          }
        }
      }
      // ---- (c) make the 4 continuous: join the [4] stem to the (straightened) trunk start ----
      if (fourStem) {
        const sc = fourStem.geometry.coordinates;
        const gap = distanceMeters(sc[sc.length - 1], tc[0]);
        if (gap > 20 && gap < 400) {
          const ks = metersPerDegLng(sc[sc.length - 1][1]);
          const sT = [(sc[sc.length - 1][0] - sc[Math.max(0, sc.length - 5)][0]) * ks, (sc[sc.length - 1][1] - sc[Math.max(0, sc.length - 5)][1]) * M_PER_DEG_LAT];
          const sl = Math.hypot(sT[0], sT[1]) || 1;
          const eT = [(tc[Math.min(4, tc.length - 1)][0] - tc[0][0]) * ks, (tc[Math.min(4, tc.length - 1)][1] - tc[0][1]) * M_PER_DEG_LAT];
          const el = Math.hypot(eT[0], eT[1]) || 1;
          const conn = hermiteBetween(sc[sc.length - 1], tc[0], [sT[0] / sl, sT[1] / sl], [eT[0] / el, eT[1] / el], { handleFrac: 0.5, sampleM: 6 });
          fourStem.geometry.coordinates = [...sc, ...conn.slice(1)];
          fourStem.properties.mott_haven_four_continuity = true;
          fourContinuous = true;
        }
      }
      // ---- (d) author the 5 schematic lens ----
      // The route-5 source geometry comes from the east and curls through the junction.
      // Apple/Transit instead draw a bounded schematic: E 149 St entry -> closed
      // top split -> Walton-side lens -> lower Y merge. Keep the upstream 5 route
      // connected, but do not let the real curl define the visible junction.
      const lens = buildMottHavenFiveSchematicLens({
        branchCoords: lensBranch.geometry.coordinates,
        trunkCoords: fourStem
          ? [...fourStem.geometry.coordinates, ...tc.slice(1)]
          : tc,
        parallelReferenceCoords: twoReference?.geometry?.coordinates ?? null,
        parallelOffsetM: 10,
        mergeDistanceM: LENS_SPAN_M,
        sampleM: 6,
      });
      if (lens.diagnostics.ok) {
        const spliced = lens.coordinates;
        lensBranch.geometry.coordinates = spliced;
        lensBranch.properties.mott_haven_lens = true;
        lensBranch.properties.mott_haven_schematic_lens = true;
        lensBranch.properties.mott_haven_lens_entry_point = lens.diagnostics.entryPoint;
        lensBranch.properties.mott_haven_lens_top_point = lens.diagnostics.topPoint;
        lensBranch.properties.mott_haven_lens_merge_point = lens.diagnostics.mergePoint;
        lensBranch.properties.mott_haven_lens_top_spread_m = Number(lens.diagnostics.topApproachLatSpreadM!.toFixed(2));
        lensBranch.properties.mott_haven_lens_max_turn_deg = Number(lens.diagnostics.maxTurnDeg!.toFixed(2));
        lensBranch.properties.mott_haven_parallel_reference_used = lens.diagnostics.parallelReferenceUsed;
        lensBranch.properties.mott_haven_parallel_reference_distance_m =
          lens.diagnostics.parallelReferenceDistanceM == null
            ? null
            : Number(lens.diagnostics.parallelReferenceDistanceM.toFixed(2));
        lensApplied = true;
        lensBowWidthM = lens.diagnostics.maxTrunkDistanceM!;
        lensRejoinM = lens.diagnostics.mergeDistanceM!;
        lensTopApproachLatSpreadM = lens.diagnostics.topApproachLatSpreadM!;
        lensMaxTurnDeg = lens.diagnostics.maxTurnDeg!;
        lensParallelReferenceUsed = Boolean(lens.diagnostics.parallelReferenceUsed);
        lensParallelReferenceDistanceM = lens.diagnostics.parallelReferenceDistanceM ?? Infinity;
      }
      // ---- (e) author the lower route-6 Y merge ----
      // OpenData/GTFS keep the route-6 approach as a lower sweeping curve that
      // reads as a second teardrop. Apple Maps instead lets the 6 branch enter
      // once and then become the shared trunk. Keep the east approach, but stop it
      // at the straight mainline and start the shared 4/6 segment there.
      if (sixBranch && sixShared) {
        const sixMerge = buildMottHavenSixSchematicMerge({
          branchCoords: sixBranch.geometry.coordinates,
          mainlineCoords: tc,
          mergeDistanceM: SIX_MERGE_SPAN_M,
          entryEastM: 430,
          entryNorthM: 120,
          sampleM: 6,
        });
        if (sixMerge.diagnostics.ok) {
          sixBranch.geometry.coordinates = sixMerge.coordinates;
          sixBranch.properties.mott_haven_six_merge = true;
          sixBranch.properties.mott_haven_six_merge_point = sixMerge.diagnostics.mergePoint;
          sixBranch.properties.mott_haven_six_merge_max_turn_deg =
            Number(sixMerge.diagnostics.maxTurnDeg!.toFixed(2));
          sixBranch.properties.mott_haven_six_merge_rejoin_m =
            Number(sixMerge.diagnostics.mergeDistanceM!.toFixed(2));

          sixShared.geometry.coordinates = sixMerge.sharedMainlineCoords;
          sixShared.properties.mott_haven_six_shared_mainline = true;
          sixShared.properties.mott_haven_six_merge_point = sixMerge.diagnostics.mergePoint;
          sixMergeApplied = true;
          sixMergeRejoinM = sixMerge.diagnostics.mergeDistanceM!;
          sixMergeMaxTurnDeg = sixMerge.diagnostics.maxTurnDeg!;
        }
      }
    }
    const lensTopApproachOk = lensParallelReferenceUsed
      ? lensParallelReferenceDistanceM <= 25
      : lensTopApproachLatSpreadM <= 15;
    const sixMergeOk = sixMergeApplied && sixMergeRejoinM <= 2 && sixMergeMaxTurnDeg <= 70;
    const qaPass = lensApplied && fourContinuous && lensBowWidthM >= 120 && lensBowWidthM <= 260 && lensRejoinM <= 4
      && lensTopApproachOk && lensMaxTurnDeg <= 65
      && mainlineStraightened && mainlineMaxBearingDevDeg <= 6
      && sixMergeOk;
    console.log(
      `[visual-network] QA Mott Haven 5/6 schematic:  five_applied=${lensApplied} four_continuous=${fourContinuous} bow=${lensBowWidthM.toFixed(0)}m rejoin=${lensRejoinM.toFixed(1)}m top_spread=${lensTopApproachLatSpreadM.toFixed(1)}m parallel_ref=${lensParallelReferenceUsed}:${lensParallelReferenceDistanceM.toFixed(1)}m max_turn=${lensMaxTurnDeg.toFixed(1)}deg straightened=${mainlineStraightened} bearing_dev=${mainlineMaxBearingDevDeg.toFixed(1)}deg six_merge=${sixMergeApplied}:${sixMergeRejoinM.toFixed(1)}m/${sixMergeMaxTurnDeg.toFixed(1)}deg ${qaPass ? "PASS" : "FAIL"}`,
    );
    if (!qaPass) {
      console.error(
        "[visual-network] *** QA FAIL: Mott Haven 5/6 schematic (5 lens, 4 continuity, straight mainline, or lower 6 merge) not authored as expected. ***",
      );
      process.exit(1);
    }
  }
}
