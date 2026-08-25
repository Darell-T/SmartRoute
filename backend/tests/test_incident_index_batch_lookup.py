"""Focused batch-reverse-index lookup tests for the deterministic index.

Requested coverage IDs read the affected-batch reverse index in the same
lookup that returns explicit coverage metadata. Kept separate from the main
index tests with an isolated in-memory cache so test order cannot leak state.
"""

import pytest

from app.services.incidents import index as incident_index
from app.services import cache


@pytest.fixture(autouse=True)
def isolated_cache():
    """Force the in-memory cache (never a developer's Redis) and clear it."""
    original_client = cache.redis_client
    cache.redis_client = None
    cache._mem.clear()
    try:
        yield
    finally:
        cache.redis_client = original_client
        cache._mem.clear()


def test_coverage_ids_read_batch_reverse_index_with_metadata():
    incident_index.set_coverage({"coverage_id": "upper-manhattan", "coverage_status": "current"})
    incident_id = incident_index.upsert_incident(
        {"source": "x_search", "source_id": "batch-lookup-1", "state": "confirmed",
         "advisor_eligible": True, "affected_batch_ids": ["upper-manhattan"], "affected_route_ids": ["Q"]}
    )
    result = incident_index.lookup_incidents(coverage_ids=["upper-manhattan"])
    assert len(result["incidents"]) == 1
    assert result["incidents"][0]["incident_id"] == incident_id
    assert result["coverage_status"] == "current"
    assert len(result["coverage"]) == 1


def test_batch_hits_dedupe_with_stop_and_route_hits():
    incident_id = incident_index.upsert_incident(
        {"source": "s", "source_id": "dedupe-batch-1", "state": "confirmed", "advisor_eligible": True,
         "affected_stop_ids": ["D24"], "affected_route_ids": ["Q"], "affected_batch_ids": ["central-south-brooklyn"]}
    )
    result = incident_index.lookup_incidents(stop_ids=["D24"], route_ids=["Q"], coverage_ids=["central-south-brooklyn"])
    assert [item["incident_id"] for item in result["incidents"]] == [incident_id]


def test_unknown_coverage_id_reads_no_batch_index():
    incident_index.upsert_incident({"source": "s", "source_id": "no-batch-1", "affected_batch_ids": ["upper-manhattan"]})
    result = incident_index.lookup_incidents(coverage_ids=["never-scanned"])
    assert result["incidents"] == []
    assert result["coverage_status"] == "unscanned"
