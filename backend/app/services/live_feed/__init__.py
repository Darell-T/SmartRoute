"""Live-feed service ownership.

``snapshot`` owns the rider-specific response projection, nearby issue
derivation, and vehicle-enrichment orchestration. ``network_snapshot`` owns
the process-wide realtime generation, while ``vehicle_enrichment`` remains a
separate helper for bounded segment and stop context. The router package owns
HTTP/WebSocket transport and authentication; this package intentionally has no
eager exports.
"""
