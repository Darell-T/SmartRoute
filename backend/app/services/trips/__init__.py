"""Trip-planning domain services.

The ``trips`` router (``app.routers.trips``) keeps the HTTP endpoints, the
request models, and ``_collect_recommendation`` (the ai_advisor touchpoint).
The route-evaluation, leg-enrichment, candidate-building, and incident logic
live here, split by responsibility:

- ``text``       -- rider-facing text sanitization (no internal deps)
- ``scoring``    -- route scoring + route-step accessors (depends on ``text``)
- ``enrichment`` -- gtfs/bus leg enrichment (independent)
- ``candidates`` -- model-output parsing + candidate building (``scoring``, ``text``)
- ``incidents``  -- incident markers + the off-hot-path background scan (``scoring``, ``text``)
"""
