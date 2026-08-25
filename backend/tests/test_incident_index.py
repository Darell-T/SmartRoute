"""Focused tests for the deterministic incident index and coverage metadata."""

import asyncio
import json
import time
import unittest
from unittest.mock import patch

import pytest

from app.services.incidents import index as incident_index
from app.services.incidents.normalization import sanitize_source_records, source_identity_pairs
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


def _incident_key(incident_id: str) -> str:
    return f"{incident_index.INCIDENT_PREFIX}{incident_id}"


def _coverage_key(coverage_id: str) -> str:
    return f"{incident_index.COVERAGE_PREFIX}{coverage_id}"


def _expire_record(key: str) -> None:
    record = json.loads(cache.cache_get(key))
    record["expires_at"] = time.time() - 60
    cache.cache_set(key, json.dumps(record), 3600)


def test_stable_id_across_order_case_and_duplicates():
    base = {
        "source": "x_search",
        "source_id": "item-7",
        "location_name": "Atlantic Av",
        "description": "Escalator outage",
        "impact_scope": "nearby",
        "affected_stop_ids": ["D24", "d24", "D24", "B38"],
        "affected_route_ids": ["Q", "q", "B"],
        "affected_corridor_ids": ["c1", "C1"],
    }
    reordered = {
        **base,
        "affected_stop_ids": ["B38", "D24", "D24"],
        "affected_route_ids": ["B", "Q", "Q"],
        "affected_corridor_ids": ["C1", "c1"],
        "location_name": "ATLANTIC AV",
    }
    case_changed = {**base, "source": "X_SEARCH", "source_id": "ITEM-7"}
    assert incident_index.incident_id_for(base) == incident_index.incident_id_for(reordered)
    assert incident_index.incident_id_for(base) == incident_index.incident_id_for(case_changed)


def test_distinct_stable_source_ids_produce_distinct_ids():
    one = incident_index.incident_id_for({"source": "ny511", "source_id": "alert-1"})
    two = incident_index.incident_id_for({"source": "ny511", "source_id": "alert-2"})
    assert one != two
    assert one.startswith("inc_")


def test_source_records_pairs_are_sorted_and_deduped():
    first = {
        "source_records": [
            {"provider": "y", "id": "2"},
            {"source": "x", "source_id": "1"},
            {"source": "x", "source_id": "1"},
        ]
    }
    reordered = {"source_records": [{"source": "x", "source_id": "1"}, {"provider": "y", "id": "2"}]}
    different = {"source_records": [{"source": "x", "source_id": "3"}]}
    assert incident_index.incident_id_for(first) == incident_index.incident_id_for(reordered)
    assert incident_index.incident_id_for(first) != incident_index.incident_id_for(different)


def test_fallback_identity_stability():
    base = {
        "location_name": "14 St",
        "description": "Delays",
        "impact_scope": "station",
        "affected_stop_ids": ["D24", "F14", "d24"],
        "affected_route_ids": ["Q", "q", "B"],
        "affected_corridor_ids": ["c1", "C1"],
    }
    variant = {
        **base,
        "affected_stop_ids": ["F14", "D24", "F14", "d24"],
        "affected_route_ids": ["B", "Q", "q", "Q"],
        "affected_corridor_ids": ["c1", "C1", "c1"],
        "description": "DELAYS",
        "location_name": " 14 St ",
    }
    different = {**base, "description": "Signal problem"}
    assert incident_index.incident_id_for(base) == incident_index.incident_id_for(variant)
    assert incident_index.incident_id_for(base) != incident_index.incident_id_for(different)


def test_repeated_upsert_dedupes_and_cleans_obsolete_indexes():
    incident = {
        "source": "ny511",
        "source_id": "alert-9",
        "state": "confirmed",
        "location_name": "Atlantic Av",
        "affected_route_ids": ["Q"],
        "affected_stop_ids": ["D24"],
    }
    incident_id = incident_index.upsert_incident(incident)
    assert incident_id == incident_index.incident_id_for(incident)
    incident_index.upsert_incident({**incident, "affected_route_ids": ["q", "Q"]})
    incident_index.upsert_incident(incident)

    found = incident_index.lookup_incidents(route_ids=["Q"])
    assert len(found["incidents"]) == 1
    assert found["incidents"][0]["incident_id"] == incident_id

    incident_index.upsert_incident(
        {**incident, "affected_route_ids": ["B"], "affected_stop_ids": ["D24"]}
    )
    assert incident_index.lookup_incidents(route_ids=["Q"])["incidents"] == []
    assert incident_index.lookup_incidents(route_ids=["q"])["incidents"] == []
    assert len(incident_index.lookup_incidents(route_ids=["B"])["incidents"]) == 1
    assert len(incident_index.lookup_incidents(stop_ids=["D24"])["incidents"]) == 1


def test_first_seen_at_preserved_across_equivalent_upserts():
    incident = {
        "source": "s",
        "source_id": "f1",
        "location_name": "L",
        "affected_stop_ids": ["D1"],
    }
    incident_index.upsert_incident(incident)
    first = incident_index.lookup_incidents(stop_ids=["D1"])["incidents"][0]["first_seen_at"]
    time.sleep(0.01)
    incident_index.upsert_incident({**incident, "description": "more detail"})
    record = incident_index.lookup_incidents(stop_ids=["D1"])["incidents"][0]
    assert record["first_seen_at"] == first
    assert record["updated_at"] >= first


def test_batch_reverse_index_maintained():
    incident = {"source": "s", "source_id": "b-1", "batch_ids": ["batch-a"]}
    incident_id = incident_index.upsert_incident(incident)
    batch_key = f"{incident_index.BATCH_INDEX_PREFIX}batch-a"
    assert incident_index._read_index(batch_key) == [incident_id]


@pytest.mark.parametrize(
    "state",
    ["unconfirmed", "confirmed", "rejected", "refreshing", "stale", "resolved"],
)
def test_all_valid_lifecycle_states(state):
    incident_id = incident_index.upsert_incident(
        {"source": "s", "source_id": f"state-{state}", "affected_stop_ids": ["D2"]}
    )
    assert incident_index.set_incident_state(incident_id, state) is True
    record = json.loads(cache.cache_get(_incident_key(incident_id)))
    assert record["state"] == state


def test_invalid_input_state_falls_back_to_unconfirmed():
    incident_id = incident_index.upsert_incident(
        {"state": "bogus", "affected_stop_ids": ["D3"]}
    )
    record = json.loads(cache.cache_get(_incident_key(incident_id)))
    assert record["state"] == "unconfirmed"


def test_set_incident_state_rejects_unknown_state():
    incident_id = incident_index.upsert_incident({"affected_stop_ids": ["D4"]})
    with pytest.raises(ValueError):
        incident_index.set_incident_state(incident_id, "bogus")


def test_set_incident_state_missing_record_returns_false():
    assert incident_index.set_incident_state("inc_missing_record", "confirmed") is False


def test_rejected_and_resolved_are_filtered_from_lookup():
    incident_id = incident_index.upsert_incident(
        {
            "source": "s",
            "source_id": "filter-1",
            "state": "confirmed",
            "affected_stop_ids": ["D5"],
        }
    )
    assert len(incident_index.lookup_incidents(stop_ids=["D5"])["incidents"]) == 1
    assert incident_index.set_incident_state(incident_id, "rejected") is True
    assert incident_index.lookup_incidents(stop_ids=["D5"])["incidents"] == []
    assert incident_index.set_incident_state(incident_id, "resolved") is True
    assert incident_index.lookup_incidents(stop_ids=["D5"])["incidents"] == []
    assert incident_index.set_incident_state(incident_id, "confirmed") is True
    assert len(incident_index.lookup_incidents(stop_ids=["D5"])["incidents"]) == 1


def test_expired_active_record_returns_stale():
    incident_id = incident_index.upsert_incident(
        {
            "source": "s",
            "source_id": "exp-1",
            "state": "confirmed",
            "affected_stop_ids": ["D6"],
        }
    )
    _expire_record(_incident_key(incident_id))
    found = incident_index.lookup_incidents(stop_ids=["D6"])["incidents"]
    assert len(found) == 1
    assert found[0]["state"] == "stale"


def test_current_coverage_with_empty_incidents_stays_current():
    incident_index.set_coverage({"coverage_id": "cov-1", "coverage_status": "current"})
    result = incident_index.lookup_incidents(stop_ids=["D99"], coverage_ids=["cov-1"])
    assert result["incidents"] == []
    assert result["coverage_status"] == "current"
    assert len(result["coverage"]) == 1


def test_absent_coverage_is_unscanned_even_with_incidents():
    incident_index.upsert_incident(
        {"source": "s", "source_id": "cov-2", "affected_stop_ids": ["D7"]}
    )
    result = incident_index.lookup_incidents(stop_ids=["D7"])
    assert result["coverage_status"] == "unscanned"
    assert len(result["incidents"]) >= 1
    missing = incident_index.lookup_incidents(stop_ids=["D7"], coverage_ids=["never-scanned"])
    assert missing["coverage_status"] == "unscanned"


def test_mixed_coverage_aggregates_partial():
    incident_index.set_coverage({"coverage_id": "cur", "coverage_status": "current"})
    incident_index.set_coverage({"coverage_id": "unav", "coverage_status": "unavailable"})
    result = incident_index.lookup_incidents(coverage_ids=["cur", "unav"])
    assert result["coverage_status"] == "partial"
    assert len(result["coverage"]) == 2
    mixed_missing = incident_index.lookup_incidents(coverage_ids=["cur", "not-stored"])
    assert mixed_missing["coverage_status"] == "partial"
    assert len(mixed_missing["coverage"]) == 1


def test_expired_current_coverage_reads_stale():
    incident_index.set_coverage({"coverage_id": "cov-exp", "coverage_status": "current"})
    _expire_record(_coverage_key("cov-exp"))
    coverage = incident_index.get_coverage("cov-exp")
    assert coverage["coverage_status"] == "stale"
    stored = json.loads(cache.cache_get(_coverage_key("cov-exp")))
    assert stored["coverage_status"] == "current"
    result = incident_index.lookup_incidents(coverage_ids=["cov-exp"])
    assert result["coverage_status"] == "stale"
    assert result["coverage"][0]["coverage_status"] == "stale"


def test_unavailable_coverage_stays_unavailable_when_expired():
    incident_index.set_coverage({"coverage_id": "cov-unav", "coverage_status": "unavailable"})
    _expire_record(_coverage_key("cov-unav"))
    coverage = incident_index.get_coverage("cov-unav")
    assert coverage["coverage_status"] == "unavailable"


@pytest.mark.parametrize("status", ["complete", "failed", "not_triggered", "bogus"])
def test_invalid_coverage_status_becomes_unscanned(status):
    incident_index.set_coverage({"coverage_id": "cov-bad", "coverage_status": status})
    coverage = incident_index.get_coverage("cov-bad")
    assert coverage["coverage_status"] == "unscanned"


def test_set_coverage_without_id_is_noop():
    assert incident_index.set_coverage({"coverage_status": "current"}) is None


def test_bytes_cache_values_decode():
    incident_id = incident_index.upsert_incident(
        {"source": "s", "source_id": "bytes-1", "affected_stop_ids": ["D8"]}
    )
    raw = cache.cache_get(_incident_key(incident_id))
    assert isinstance(raw, str)
    cache.cache_set(_incident_key(incident_id), raw.encode("utf-8"), 3600)
    found = incident_index.lookup_incidents(stop_ids=["D8"])["incidents"]
    assert len(found) == 1
    assert found[0]["incident_id"] == incident_id

    incident_index.set_coverage({"coverage_id": "cov-bytes", "coverage_status": "current"})
    cov_raw = cache.cache_get(_coverage_key("cov-bytes"))
    cache.cache_set(_coverage_key("cov-bytes"), cov_raw.encode("utf-8"), 3600)
    assert incident_index.get_coverage("cov-bytes")["coverage_status"] == "current"


def test_scalar_string_sequences_are_single_values():
    incident_id = incident_index.upsert_incident(
        {
            "description": "scalar string lists",
            "affected_stop_ids": "D24",
            "affected_route_ids": "q",
            "affected_corridor_ids": "downtown",
            "affected_batch_ids": "batch-1",
            "source_coverage": "x_search",
        }
    )
    found = incident_index.lookup_incidents(stop_ids="D24")
    assert len(found["incidents"]) == 1
    record = found["incidents"][0]
    assert record["incident_id"] == incident_id
    assert record["affected_stop_ids"] == ["D24"]
    assert record["affected_route_ids"] == ["Q"]
    assert record["affected_corridor_ids"] == ["downtown"]
    assert record["affected_batch_ids"] == ["batch-1"]
    assert record["source_coverage"] == ["x_search"]
    assert len(incident_index.lookup_incidents(route_ids="q")["incidents"]) == 1
    assert len(incident_index.lookup_incidents(corridor_ids="downtown")["incidents"]) == 1


def test_list_normalization_bounds_and_dedupe():
    incident = {
        "description": "bounds",
        "affected_stop_ids": [f"D{i}" for i in range(1, 41)],
        "affected_route_ids": ["q", "Q", "B"] * 20,
        "affected_corridor_ids": [f"c{i}" for i in range(1, 21)],
        "affected_batch_ids": [f"b{i}" for i in range(1, 21)],
        "source_coverage": [f"x{i}" for i in range(1, 13)],
    }
    incident_index.upsert_incident(incident)
    record = incident_index.lookup_incidents(stop_ids=["D1"])["incidents"][0]
    assert len(record["affected_stop_ids"]) == 24
    assert record["affected_route_ids"] == ["Q", "B"]
    assert len(record["affected_corridor_ids"]) == 12
    assert len(record["affected_batch_ids"]) == 12
    assert len(record["source_coverage"]) == 8


def test_source_records_allowlist_and_bounds():
    records = [
        {
            "provider": "MTA Alerts",
            "id": "r1",
            "source_url": "https://x",
            "observed_at": "2026-01-01T00:00:00Z",
            "nested": {"raw": "payload"},
            "internal": "drop me",
        },
        {"source": "MTA", "source_id": "r2", "citation_url": "https://y"},
        "not a dict",
        {},
        {"observed_at": "2026-01-02T00:00:00Z"},
        {"source": "z", "source_id": "r3"},
        {"source": "z", "source_id": "r4"},
        {"source": "z", "source_id": "r5"},
        {"source": "z", "source_id": "r6"},
        {"source": "z", "source_id": "r7"},
        {"source": "z", "source_id": "r8"},
    ]
    incident_index.upsert_incident(
        {"description": "allowlist", "affected_route_ids": ["Q"], "source_records": records}
    )
    stored = incident_index.lookup_incidents(route_ids=["Q"])["incidents"][0]["source_records"]
    assert len(stored) == 8
    assert stored[0] == {
        "source": "MTA Alerts",
        "source_id": "r1",
        "source_url": "https://x",
        "observed_at": "2026-01-01T00:00:00Z",
    }
    assert "nested" not in stored[0]
    assert "internal" not in stored[0]
    assert stored[1] == {"source": "MTA", "source_id": "r2", "source_url": "https://y"}
    assert all(
        set(record) <= {"source", "source_id", "source_url", "observed_at"}
        for record in stored
    )
    assert all(isinstance(value, str) for record in stored for value in record.values())


def test_source_records_drop_container_values_in_allowlisted_fields():
    sanitized = sanitize_source_records(
        [
            {
                "source": {"nested": "payload"},
                "source_id": ["a", "b"],
                "source_url": ("https://x", "https://y"),
                "observed_at": {"at": "2026-01-01T00:00:00Z"},
                "provider": "MTA",
                "id": "r1",
            },
            {"source": "MTA", "source_id": "r2", "source_url": {"url": "https://z"}},
        ]
    )
    assert sanitized == [
        {"source": "MTA", "source_id": "r1"},
        {"source": "MTA", "source_id": "r2"},
    ]
    assert all(isinstance(value, str) for record in sanitized for value in record.values())
    assert sanitize_source_records(
        [
            {
                "source": {"a": "b"},
                "source_id": {"c": "d"},
                "source_url": ["https://x"],
                "observed_at": ("2026-01-01T00:00:00Z",),
            }
        ]
    ) == []


def test_malformed_top_level_source_records_are_ignored():
    for malformed in (
        "MTA Alerts",
        {"provider": "MTA", "id": "r1"},
        {"a", "b"},
        42,
        None,
    ):
        assert sanitize_source_records(malformed) == []

    incident = {
        "description": "malformed provenance",
        "affected_stop_ids": ["D9"],
        "source_records": "MTA Alerts",
    }
    assert incident_index.incident_id_for(incident) == incident_index.incident_id_for(
        {**incident, "source_records": {"provider": "MTA", "id": "r1"}}
    )


def test_top_level_pair_supersedes_corroborating_record_pairs():
    incident = {
        "source": "primary",
        "source_id": "p-1",
        "source_records": [{"source": "corroborating", "source_id": "c-1"}],
    }
    assert source_identity_pairs(incident) == [("primary", "p-1")]
    assert source_identity_pairs({**incident, "source_records": []}) == [("primary", "p-1")]
    assert source_identity_pairs(
        {**incident, "source_records": [{"source": "x", "source_id": "2"}]}
    ) == [("primary", "p-1")]
    assert source_identity_pairs(
        {"source_records": [{"source": "b", "source_id": "2"}, {"source": "a", "source_id": "1"}]}
    ) == [("a", "1"), ("b", "2")]


def test_top_level_source_identity_stable_across_corroboration_changes():
    base = {"source": "x_search", "source_id": "item-7"}
    with_records = {
        **base,
        "source_records": [
            {"source": "ny511", "source_id": "alert-1"},
            {"source": "MTA", "source_id": "r2"},
        ],
    }
    changed_records = {
        **base,
        "source_records": [
            {"source": "MTA", "source_id": "r2"},
            {"source": "extra", "source_id": "r3"},
            {"source": "ny511", "source_id": "alert-1"},
        ],
    }
    assert incident_index.incident_id_for(base) == incident_index.incident_id_for(with_records)
    assert incident_index.incident_id_for(base) == incident_index.incident_id_for(changed_records)
    assert incident_index.incident_id_for(base) != incident_index.incident_id_for(
        {"source": "x_search", "source_id": "item-8"}
    )



class IncidentIndexAsyncLookupTests(unittest.IsolatedAsyncioTestCase):
    """The request path reads through the bounded async lookup."""

    def setUp(self) -> None:
        self.original_client = cache.redis_client
        cache.redis_client = None
        cache._mem.clear()

    def tearDown(self) -> None:
        cache.redis_client = self.original_client
        cache._mem.clear()

    async def test_async_lookup_matches_sync_lookup(self) -> None:
        incident_index.set_coverage(
            {"coverage_id": "central-south-brooklyn", "coverage_status": "current"}
        )
        incident_id = incident_index.upsert_incident(
            {
                "source": "x_search",
                "source_id": "async-1",
                "state": "confirmed",
                "advisor_eligible": True,
                "affected_stop_ids": ["D24"],
                "affected_route_ids": ["Q"],
                "affected_batch_ids": ["central-south-brooklyn"],
            }
        )

        sync_result = incident_index.lookup_incidents(
            stop_ids=["D24"],
            route_ids=["Q"],
            coverage_ids=["central-south-brooklyn"],
        )
        async_result = await incident_index.lookup_incidents_async(
            stop_ids=["D24"],
            route_ids=["Q"],
            coverage_ids=["central-south-brooklyn"],
        )

        self.assertEqual(async_result["lookup_kind"], "index")
        self.assertEqual(async_result["coverage_status"], "current")
        self.assertEqual(
            [item["incident_id"] for item in async_result["incidents"]],
            [incident_id],
        )
        self.assertEqual(async_result["incidents"], sync_result["incidents"])
        self.assertEqual(async_result["coverage"], sync_result["coverage"])

    async def test_async_lookup_is_bounded_when_cache_reads_stall(self) -> None:
        def _stalled_batch_read(_keys, **_kwargs):
            time.sleep(0.2)
            return {}

        with patch.object(cache, "cache_get_many", side_effect=_stalled_batch_read), patch.object(
            incident_index, "INCIDENT_LOOKUP_TIMEOUT_S", 0.01
        ):
            with self.assertRaises(asyncio.TimeoutError):
                await incident_index.lookup_incidents_async(stop_ids=["D24"])

    async def test_async_lookup_without_tokens_returns_empty_lookup(self) -> None:
        result = await incident_index.lookup_incidents_async()
        self.assertEqual(result["incidents"], [])
        self.assertEqual(result["coverage"], [])
        self.assertEqual(result["coverage_status"], "unscanned")
