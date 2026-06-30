import { connectRockawayWye } from "../../rockaway-wye.ts";
import { addSixtyThirdStreetF } from "../../sixty-third-street-f.ts";
import { cleanStatenIslandLine } from "../../staten-island-cleanup.ts";
import type { LineFeature } from "../shared/types.ts";

type PostMottLocalFixesBundleArtifacts = {
  visualFeatures: LineFeature[];
};

type PostMottLocalFixesStageInput = {
  bundleArtifacts: PostMottLocalFixesBundleArtifacts;
};

export function applyPostMottLocalFixesStage({
  bundleArtifacts,
}: PostMottLocalFixesStageInput): void {
  // =====================================================================
  // 63 St tunnel F membership
  // =====================================================================
  // OpenData draws the 63 St tunnel as the M line only; the F (its real
  // owner, per Apple Maps) appeared out of nowhere at the 36 St junction.
  // Membership-only fix: the orange tunnel features gain F in route_ids.
  {
    const sixtyThird = addSixtyThirdStreetF(bundleArtifacts.visualFeatures);
    console.log(
      `[visual-network] QA 63 St tunnel F membership: features_updated=${sixtyThird.updated} ${sixtyThird.updated > 0 ? "PASS" : "FAIL (no orange M tunnel feature found)"}`,
    );
  }

  // =====================================================================
  // Staten Island Railway cleanup
  // =====================================================================
  // OpenData shatters the SIR into ~40 fragments (second-track slivers, yard
  // twigs, weave around St George). Keep the stitched Tottenville->St George
  // mainline, bridge its small seams, drop shadows and twigs.
  {
    const siSummary = cleanStatenIslandLine(bundleArtifacts.visualFeatures);
    console.log(
      `[visual-network] QA SIR cleanup: connected=${siSummary.connected ?? false} kept=${siSummary.kept} dropped=${siSummary.dropped} stitches=${siSummary.stitches} ${siSummary.connected ? "PASS" : "FAIL (terminals not connected; left untouched)"}`,
    );
  }

  // =====================================================================
  // Hammels Wye (Rockaway) junction connector
  // =====================================================================
  // The cross-bay A stops ~46m short of the east/west legs' junction node,
  // with degenerate stubs dangling at its end. Extend it onto the node so
  // Broad Channel -> Far Rockaway reads as one continuous line.
  {
    const wye = connectRockawayWye(bundleArtifacts.visualFeatures);
    console.log(
      `[visual-network] QA Rockaway wye: connected=${wye.connected} extended=${wye.extended} stubs_removed=${wye.stubsRemoved} ${wye.connected ? "PASS" : "FAIL (legs not found)"}`,
    );
  }
}
