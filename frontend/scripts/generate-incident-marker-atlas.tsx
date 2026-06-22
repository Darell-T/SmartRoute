/**
 * SmartRoute incident marker atlas scaffold.
 *
 * Target output:
 * - frontend/public/incident-markers/incident-marker-atlas@3x.png
 * - frontend/public/incident-markers/incident-marker-atlas.json
 *
 * Render every `${type}-${size}-${state}` combination:
 * 8 incident types x 3 sizes x 3 states = 72 sprites.
 *
 * Implementation note:
 * Use ReactDOMServer to render IncidentMarkerSvg, then rasterize each SVG with
 * a build-time renderer such as sharp or resvg. Keep rasterization out of the
 * runtime map path. Atlas entries must use anchorX = width / 2, anchorY = height,
 * and mask = false so deck.gl IconLayer anchors at the pin tip.
 *
 * This file is intentionally a scaffold in this pass because the legend/static
 * component is being validated before the map incident layer is swapped over.
 */
import { INCIDENT_TYPES } from "../components/map/incidents/incident-marker-types";
import { INCIDENT_MARKER_SIZES } from "../components/map/incidents/incident-marker-tokens";

const STATES = ["default", "pulse", "selected"] as const;
const ATLAS_SCALE = 3;

function main() {
  const spriteCount =
    INCIDENT_TYPES.length *
    Object.keys(INCIDENT_MARKER_SIZES).length *
    STATES.length;

  console.log(
    `SmartRoute incident atlas scaffold: ${spriteCount} sprites at ${ATLAS_SCALE}x.`,
  );
  console.log("Next step: install/configure a build-time SVG rasterizer and emit the PNG + JSON mapping.");
}

main();
