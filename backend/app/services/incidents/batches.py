"""Coarse NYC geographic batches for the background incident job.

Centralizes the fixed citywide coverage taxonomy so the scheduler, scout,
and coverage index share one deterministic view. Batches are deliberately
coarse corridors/regions: the job must never fan out station-by-station,
and subway route IDs are not pinned to a single batch because many lines
span several boroughs or areas.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

NYC_ENVELOPE: tuple[float, float, float, float] = (40.45, -74.30, 40.95, -73.65)


@dataclass(frozen=True, slots=True)
class IncidentBatch:
    """Immutable description of one coarse incident coverage batch."""

    batch_id: str
    label: str
    boroughs: tuple[str, ...]
    bounds: tuple[float, float, float, float]
    focus_terms: tuple[str, ...]


INCIDENT_BATCHES: tuple[IncidentBatch, ...] = (
    IncidentBatch(
        batch_id="upper-manhattan",
        label="Upper Manhattan",
        boroughs=("Manhattan",),
        bounds=(40.79, -73.96, 40.91, -73.89),
        focus_terms=("harlem", "washington-heights", "inwood", "125th-street"),
    ),
    IncidentBatch(
        batch_id="midtown-manhattan",
        label="Midtown Manhattan",
        boroughs=("Manhattan",),
        bounds=(40.73, -74.02, 40.79, -73.94),
        focus_terms=("midtown", "times-square", "grand-central", "34th-street", "42nd-street"),
    ),
    IncidentBatch(
        batch_id="lower-manhattan",
        label="Lower Manhattan",
        boroughs=("Manhattan",),
        bounds=(40.70, -74.02, 40.73, -73.95),
        focus_terms=("lower-manhattan", "financial-district", "chinatown", "soho", "battery-park"),
    ),
    IncidentBatch(
        batch_id="downtown-northwest-brooklyn",
        label="Downtown & Northwest Brooklyn",
        boroughs=("Brooklyn",),
        bounds=(40.68, -74.02, 40.72, -73.94),
        focus_terms=(
            "downtown-brooklyn",
            "brooklyn-heights",
            "dumbo",
            "fort-greene",
            "williamsburg",
        ),
    ),
    IncidentBatch(
        batch_id="central-south-brooklyn",
        label="Central & South Brooklyn",
        boroughs=("Brooklyn",),
        bounds=(40.57, -74.03, 40.69, -73.90),
        focus_terms=("park-slope", "prospect-park", "crown-heights", "bedford-stuyvesant", "flatbush"),
    ),
    IncidentBatch(
        batch_id="western-queens",
        label="Western Queens",
        boroughs=("Queens",),
        bounds=(40.72, -73.96, 40.79, -73.86),
        focus_terms=("astoria", "long-island-city", "sunnyside", "jackson-heights", "elmhurst"),
    ),
    IncidentBatch(
        batch_id="eastern-queens-jamaica",
        label="Eastern Queens & Jamaica",
        boroughs=("Queens",),
        bounds=(40.63, -73.87, 40.78, -73.70),
        focus_terms=("jamaica", "flushing", "forest-hills", "kew-gardens", "jfk-airport"),
    ),
    IncidentBatch(
        batch_id="south-bronx",
        label="South Bronx",
        boroughs=("Bronx",),
        bounds=(40.80, -73.94, 40.85, -73.86),
        focus_terms=("south-bronx", "mott-haven", "hunts-point", "highbridge", "melrose"),
    ),
    IncidentBatch(
        batch_id="central-north-bronx",
        label="Central & North Bronx",
        boroughs=("Bronx",),
        bounds=(40.85, -73.93, 40.92, -73.82),
        focus_terms=("fordham", "kingsbridge", "riverdale", "norwood", "bedford-park"),
    ),
    IncidentBatch(
        batch_id="staten-island-interborough-bus",
        label="Staten Island & Interborough Bus",
        boroughs=("Staten Island",),
        bounds=(40.50, -74.26, 40.65, -74.03),
        focus_terms=(
            "staten-island",
            "verrazzano-narrows",
            "richmond-avenue",
            "hylan-boulevard",
            "interborough-bus",
        ),
    ),
)

_BATCH_BY_ID: dict[str, IncidentBatch] = {
    batch.batch_id.casefold(): batch for batch in INCIDENT_BATCHES
}


def incident_batch_ids() -> tuple[str, ...]:
    """Return the ten batch IDs in canonical scan order."""
    return tuple(batch.batch_id for batch in INCIDENT_BATCHES)


def get_incident_batch(batch_id: str) -> IncidentBatch | None:
    """Look up a batch by exact normalized ID (trim + casefold); no fuzzy match."""
    if not batch_id:
        return None
    return _BATCH_BY_ID.get(batch_id.strip().casefold())


def coverage_batch_ids_for_point(latitude: object, longitude: object) -> tuple[str, ...]:
    """Canonical coverage batch IDs whose inclusive bounds contain a point.

    Accepts only finite, plausible latitude/longitude values. Bounds are
    inclusive, so a point on a shared edge or corner can match several
    batches; an invalid or outside point matches none. This is a pure
    point-to-batch mapping and never queries per station.
    """
    try:
        lat, lon = float(latitude), float(longitude)
    except (TypeError, ValueError):
        return ()
    if not (isfinite(lat) and isfinite(lon)):
        return ()
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return ()
    return tuple(
        batch.batch_id
        for batch in INCIDENT_BATCHES
        if batch.bounds[0] <= lat <= batch.bounds[2]
        and batch.bounds[1] <= lon <= batch.bounds[3]
    )
