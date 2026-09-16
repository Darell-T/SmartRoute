import { applyBrightonBqChurchSpacing } from "../../brighton-bq-church-spacing.ts";
import { applyCulverFgProspectSmoothing } from "../../culver-fg-prospect-smoothing.ts";
import { applyJoralemonGreenRiverSmoothing } from "../../joralemon-green-river.ts";
import { applyNostrandEasternSchematic } from "../../nostrand-eastern-schematic.ts";
import { applyStNicholasBlueStraightening } from "../../st-nicholas-blue-straightening.ts";
import type { LineFeature } from "../shared/types.ts";

type AuthoredLocationPatchesBundleArtifacts = {
  visualFeatures?: LineFeature[];
};

type AuthoredLocationPatchesStageInput = {
  bundleArtifacts: AuthoredLocationPatchesBundleArtifacts;
};

function patchJoralemonGreenRiver(features: LineFeature[]): LineFeature[] {
  // The off-revenue pass correctly protects most visual geometry, but around
  // the East River/Joralemon crossing it can pull the green trunk onto a GTFS
  // trace with a small visible wiggle in open water. Preserve the crossing's
  // endpoints and surrounding geometry, but replace only that local water run
  // with a clean tangent-matched schematic curve.
  const joralemonGreenRiver = applyJoralemonGreenRiverSmoothing(features, {
    bbox: {
      minLon: -74.0118,
      maxLon: -74.0015,
      minLat: 40.6948,
      maxLat: 40.7010,
    },
    marginM: 360,
    sampleM: 6,
    tangentSampleM: 130,
    handleFrac: 0.42,
    maxHandleM: 650,
  });
  console.log(
    `[visual-network] QA Joralemon green river: applied=${joralemonGreenRiver.diagnostics.applied} replaced=${joralemonGreenRiver.diagnostics.replaced_length_m ?? 0}m`,
  );
  return joralemonGreenRiver.features;
}

function patchBrightonBqChurchSpacing(features: LineFeature[]): LineFeature[] {
  // The B/Q Brighton physical bundle is detected correctly, but the continuous
  // materializer offsets each source member's own OpenData geometry. Around the
  // gentle Church/Beverley bend those source curves are slightly inconsistent,
  // so the baked orange/yellow lanes pinch together. Rebalance only this local
  // shared-bundle run onto one smoothed centerline and keep the two lanes at a
  // stable Apple/Transit-style separation through the bend.
  const brightonBqSpacing = applyBrightonBqChurchSpacing(features, {
    targetSeparationM: 15,
    marginM: 650,
    blendM: 140,
    sampleM: 6,
  });
  console.log(
    `[visual-network] QA Brighton B/Q Church spacing: applied=${brightonBqSpacing.diagnostics.applied} strict_min=${brightonBqSpacing.diagnostics.min_separation_before_m ?? "n/a"}m->${brightonBqSpacing.diagnostics.min_separation_after_m ?? "n/a"}m core_min=${brightonBqSpacing.diagnostics.core_min_separation_after_m ?? "n/a"}m${brightonBqSpacing.diagnostics.reason ? ` reason=${brightonBqSpacing.diagnostics.reason}` : ""}`,
  );
  return brightonBqSpacing.features;
}

function patchCulverFgProspectSeam(features: LineFeature[]): LineFeature[] {
  // The F/G Culver corridor changes from a bundled green G lane to a solo G
  // lane around Prospect Av / Terrace Pl. Generic joint taper closes the seam,
  // but it does so by translating the G tail, leaving a subtle S-kink. Rebuild
  // only this local G chain from the neighboring F curve at stable separation.
  const culverFgProspect = applyCulverFgProspectSmoothing(features, {
    targetSeparationM: 14,
    marginM: 300,
    blendM: 140,
    sampleM: 6,
    smoothingPasses: 2,
  });
  console.log(
    `[visual-network] QA Culver F/G Prospect seam: applied=${culverFgProspect.diagnostics.applied} sep=${culverFgProspect.diagnostics.min_separation_before_m ?? "n/a"}m->${culverFgProspect.diagnostics.min_separation_after_m ?? "n/a"}m${culverFgProspect.diagnostics.reason ? ` reason=${culverFgProspect.diagnostics.reason}` : ""}`,
  );
  return culverFgProspect.features;
}

function patchStNicholasBlue(features: LineFeature[]): LineFeature[] {
  // Same-color joins around 145 St can leave the north A/C piece and south
  // A/C/E piece meeting a few meters off-axis. At map scale this reads as a
  // small disconnected blue kink beside St Nicholas Av. Straighten only this
  // local St Nicholas run onto one fitted axis and snap the 145 St seam.
  const stNicholasBlue = applyStNicholasBlueStraightening(features);
  console.log(
    `[visual-network] QA St Nicholas A/C straightening: applied=${stNicholasBlue.diagnostics.applied} features=${stNicholasBlue.diagnostics.target_feature_count} drift=${stNicholasBlue.diagnostics.max_perpendicular_before_m ?? "n/a"}m->${stNicholasBlue.diagnostics.max_perpendicular_after_m ?? "n/a"}m endpoint_clusters=${stNicholasBlue.diagnostics.snapped_endpoint_clusters ?? 0}${stNicholasBlue.diagnostics.reason ? ` reason=${stNicholasBlue.diagnostics.reason}` : ""}`,
  );
  return stNicholasBlue.features;
}

function patchNostrandEasternSplit(features: LineFeature[]): LineFeature[] {
  // Apple Maps draws this as one straight 3/4 Eastern Parkway trunk and one
  // smooth 2/5 branch peeling south. The source + bridge passes leave a small
  // hook on the restored 4-to-Utica tail and a backtracking first segment on the
  // 2/5 branch. Own that local split here, after off-revenue snapping has
  // settled the revenue geometry.
  const nostrandSchematic = applyNostrandEasternSchematic(features, {
    branchTurnSpanM: 420,
    trunkBlendM: 170,
    sampleM: 6,
  });
  console.log(
    `[visual-network] QA Nostrand/Eastern schematic: applied=${nostrandSchematic.diagnostics.applied} red_branch=${nostrandSchematic.diagnostics.red_branch_rebuilt} green_tail=${nostrandSchematic.diagnostics.green_tail_straightened} green_branch=${nostrandSchematic.diagnostics.green_branch_rebuilt}${nostrandSchematic.diagnostics.reason ? ` reason=${nostrandSchematic.diagnostics.reason}` : ""}`,
  );
  return nostrandSchematic.features;
}

export function applyAuthoredLocationPatchesStage({
  bundleArtifacts,
}: AuthoredLocationPatchesStageInput): void {
  const features = bundleArtifacts.visualFeatures;
  if (!features) return;
  bundleArtifacts.visualFeatures = patchNostrandEasternSplit(
    patchStNicholasBlue(
      patchCulverFgProspectSeam(
        patchBrightonBqChurchSpacing(
          patchJoralemonGreenRiver(features),
        ),
      ),
    ),
  );
}
