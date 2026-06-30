import { colocateSameColorStretches } from "../../colocate-same-color.ts";
import { taperBakedJointSteps } from "../../joint-offset-taper.ts";
import { snapDanglingSameColorEndpoints } from "../../snap-dangling-same-color.ts";
import type { SameColorJunctionStageInput } from "./same-color-junction-types.ts";

export function applySameColorJunctionStage({
  bundleArtifacts,
  sameColorSnapDistM,
  fanoutBlendM,
}: SameColorJunctionStageInput): void {
  // ----- Same-color convergence snap -----
  // At junctions where several routes of one color merge onto a trunk (B/D + F + M
  // onto 6 Av; the 5 into the 4/5 trunk), each lane is its own feature and one can
  // stop a few meters short of the trunk -- it renders as a line that "does not
  // touch". This snaps such a dangling endpoint onto the same-color sibling it is
  // converging into (distance-decreasing test, so genuine parallel lanes like the
  // SI double-track are left alone).
  let sameColorConvergenceSnappedCount = 0;
  if (bundleArtifacts.visualFeatures) {
    const snap = snapDanglingSameColorEndpoints(bundleArtifacts.visualFeatures, {
      snapDistM: sameColorSnapDistM,
    });
    bundleArtifacts.visualFeatures = snap.features;
    sameColorConvergenceSnappedCount = snap.snappedCount;
  }
  console.log(
    `[visual-network] same-color convergence snap: endpoints=${sameColorConvergenceSnappedCount} (<=${sameColorSnapDistM}m, converging)`,
  );

  // ----- Same-color co-location: one ribbon per color, Apple-style -----
  // On Queens Blvd the F express track runs ~18m from the F+M local track for
  // ~5km; both are orange and Apple draws ONE ribbon there, but 18m reads as a
  // clear double strand from ~z13.5 up. Pull the route-poorer lane onto its
  // same-color sibling wherever they run parallel 10-30m apart for >= 500m.
  // Closer pairs (Lex 4+5/4+6 at ~6m) already fuse in paint and are skipped.
  const colocateResult = bundleArtifacts.visualFeatures
    ? colocateSameColorStretches(
        bundleArtifacts.visualFeatures.filter(
          (feature) => feature.properties?.visual_feature_type === "bundle_lane",
        ),
        { minGapM: 10, maxGapM: 30, minStretchM: 500, blendM: fanoutBlendM },
      )
    : { count: 0, stretches: [] };
  console.log(
    `[visual-network] same-color co-location:      ${colocateResult.count} stretch(es)` +
      (colocateResult.count
        ? ` (${colocateResult.stretches.map((s) => `${s.routes}:${s.lengthM}m`).join(", ")})`
        : ""),
  );

  // ----- Joint-offset tapers: flatten lane-slot steps at corridor joints -----
  // Where the same route continues into an adjacent piece with a different
  // lane_slot (G at Terrace Pl, F at Delancey, 1/2/3 near Times Sq), the baked
  // endpoints land a few meters apart LATERALLY and the gap bridge below would
  // join them with a sharp sideways step. Warp the more-offset lane's tail
  // onto its neighbor over FANOUT_BLEND_M instead. Must run here -- after tail
  // splitting/clips produced the final lane set, before bridging.
  if (bundleArtifacts.visualFeatures) {
    const jointTaperResult = taperBakedJointSteps(
      bundleArtifacts.visualFeatures.filter(
        (feature) => feature.properties?.visual_feature_type === "bundle_lane",
      ),
      { blendM: fanoutBlendM },
    );
    // Drop the tiny pre-existing stitch connectors the warp made redundant
    // (they would dangle 6m off the now-flush joint).
    const beforeDrop = bundleArtifacts.visualFeatures.length;
    bundleArtifacts.visualFeatures = bundleArtifacts.visualFeatures.filter(
      (feature) => feature.properties?.joint_offset_taper_drop !== true,
    );
    const droppedStitches = beforeDrop - bundleArtifacts.visualFeatures.length;
    console.log(
      `[visual-network] joint-offset tapers:         ${jointTaperResult.count} joint(s) flattened, ${droppedStitches} stale stitch(es) dropped` +
        (jointTaperResult.count
          ? ` (${jointTaperResult.joints.map((j) => `${j.routes}@${j.gapM}m`).join(", ")})`
          : ""),
    );
  }
}
