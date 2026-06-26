"""Live-feed snapshot builders.

The ``live_feed`` router (``app.routers.live_feed``) keeps the HTTP + WebSocket
endpoints, the ticket auth, the warm-cache refresh signalling, and the
``_build_live_snapshot`` orchestrator (the perf-critical hot path). The pure
builder helpers it calls live here, split by responsibility. ``vehicle_enrichment``
is the first: stdlib-only segment-estimate math with no upstream dependencies.
"""
