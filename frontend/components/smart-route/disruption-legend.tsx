import { IncidentLegend } from "@/components/map/incidents/incident-legend";
import { IncidentMarkerSvg } from "@/components/map/incidents/incident-marker-svg";
import { INCIDENT_TYPES } from "@/components/map/incidents/incident-marker-types";

interface Props {
  variant?: "inline" | "map";
  /** When provided, renders a close button that hides the legend. */
  onHide?: () => void;
}

/**
 * Map legend strip rendered along the bottom of the live feed map.
 *
 * Anatomy (default compact state):
 *   1. Eyebrow label "Map key"
 *   2. Incident sample (uses canonical `<IncidentMarkerSvg>` so the legend
 *      and the map agree visually)
 *   3. User location dot — visually distinct from any incident marker
 *      (cyan-only color reserved for medical incidents AND the user dot;
 *      they are kept apart through size + ring treatment, not just hue)
 *   4. Expandable `<details>` that exposes the full 8-type incident grid
 *      via `<IncidentLegend />`
 *
 * The expanded section uses the same `IncidentMarkerSvg` component as the
 * compact preview, so the visual language stays consistent.
 */
export function DisruptionLegend({ variant = "inline", onHide }: Props) {
  return (
    <section
      className="sr-disruption-legend"
      data-variant={variant}
      aria-label="Map legend"
    >
      <span className="sr-disruption-legend__label">Map key</span>

      <div className="sr-disruption-legend__item">
        <IncidentMarkerSvg
          type="general"
          size="S"
          state="default"
          decorative
          glow={false}
          className="sr-disruption-legend__marker"
        />
        <span>Incident</span>
      </div>

      <div className="sr-disruption-legend__item">
        <span className="sr-disruption-legend__user-dot" aria-hidden="true" />
        <span>User location</span>
      </div>

      <details className="sr-disruption-legend__types">
        <summary aria-label="Show incident marker types">
          <span>Incident types</span>
          <span className="sr-disruption-legend__count">{INCIDENT_TYPES.length}</span>
        </summary>
        <IncidentLegend showHeader />
      </details>

      {onHide ? (
        <button
          type="button"
          className="sr-disruption-legend__close"
          onClick={onHide}
          aria-label="Hide map key"
          title="Hide map key"
        >
          ×
        </button>
      ) : null}
    </section>
  );
}
