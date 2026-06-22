/**
 * Reusable incident-type legend grid.
 *
 * Renders one row per incident type using the canonical `<IncidentMarkerSvg>`
 * so the legend is visually identical to the markers shown on the map. Used
 * by:
 *   - `smart-route/disruption-legend.tsx` inside its expandable `<details>`
 *     popover (default consumer)
 *   - any future popup or settings panel that needs to teach users what
 *     each marker type means
 *
 * Static UI — safe to render React SVG markers here. Per the architecture
 * doctrine in `components/map/incidents/README.md`, only the on-canvas map
 * markers must come from the rasterized atlas.
 */
import { IncidentMarkerSvg } from "./incident-marker-svg";
import { INCIDENT_TYPES } from "./incident-marker-types";
import { INCIDENT_MARKER_TOKENS } from "./incident-marker-tokens";

interface IncidentLegendProps {
  /** Marker size to render in the grid. Defaults to "S" (compact). */
  size?: "S" | "M";
  /**
   * Optional className that wraps the grid. The default uses the
   * `.sr-disruption-legend__type-grid` class shared with the map legend.
   */
  className?: string;
  /**
   * Optional eyebrow row at the top of the grid. Hidden by default since
   * most consumers wrap the grid in a `<details>` element with its own
   * summary text.
   */
  showHeader?: boolean;
  /** Optional aria-label override for the grid container. */
  ariaLabel?: string;
}

export function IncidentLegend({
  size = "S",
  className = "sr-disruption-legend__type-grid",
  showHeader = false,
  ariaLabel = "Incident marker types",
}: IncidentLegendProps) {
  return (
    <div className={className} aria-label={ariaLabel}>
      {showHeader ? (
        <div className="sr-disruption-legend__type-header">
          <span>Incident markers</span>
          <span>SVG-ready</span>
        </div>
      ) : null}
      {INCIDENT_TYPES.map((type) => (
        <div key={type} className="sr-disruption-legend__type-item">
          <IncidentMarkerSvg
            type={type}
            size={size}
            state="default"
            decorative
            glow={false}
          />
          <span>{INCIDENT_MARKER_TOKENS[type].label}</span>
        </div>
      ))}
    </div>
  );
}
