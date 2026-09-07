"""Ephemeral agent stores survive optional Redis command failures."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import redis
from app.services import cache
from app.services.agent import candidate_store, discovery_store
from app.services.agent.tools.transit import evidence_store as transit_evidence_store

QUOTA_EXCEEDED = "quota exceeded"


class _QuotaBlockedRedis:
    def pipeline(self):
        raise redis.exceptions.ResponseError(QUOTA_EXCEEDED)

    def get(self, _key):
        raise redis.exceptions.ResponseError(QUOTA_EXCEEDED)

    def setex(self, _key, _ttl, _value):
        raise redis.exceptions.ResponseError(QUOTA_EXCEEDED)


class _ReadMissPipeline:
    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        return False

    def watch(self, _key):
        return None

    def get(self, _key):
        return None


class _WriteFailsReadMissRedis:
    def pipeline(self):
        return _ReadMissPipeline()

    def get(self, _key):
        return None

    def setex(self, _key, _ttl, _value):
        raise redis.exceptions.ResponseError(QUOTA_EXCEEDED)


class EphemeralStoreCacheFailOpenTests(unittest.TestCase):
    def setUp(self) -> None:
        cache._mem.clear()
        cache._last_fail_open_log = 0.0

    def tearDown(self) -> None:
        cache._mem.clear()

    def test_discovery_set_store_and_load_use_process_memory_when_redis_rejects(self):
        with patch.object(cache, "redis_client", _QuotaBlockedRedis()):
            set_id = discovery_store.store_discovery_set(
                session_id="sess-discovery",
                places=[{"name": "Cafe", "address": "1 Main St"}],
                ttl_seconds=60,
            )
            record = discovery_store.load_discovery_set(
                set_id,
                session_id="sess-discovery",
            )

        assert record is not None
        assert record["places"][0]["name"] == "Cafe"

    def test_discovery_set_roundtrips_when_write_fails_and_redis_read_misses(self):
        with patch.object(cache, "redis_client", _WriteFailsReadMissRedis()):
            set_id = discovery_store.store_discovery_set(
                session_id="sess-discovery-miss",
                places=[{"name": "Cafe", "address": "1 Main St"}],
                ttl_seconds=60,
            )
            record = discovery_store.load_discovery_set(
                set_id,
                session_id="sess-discovery-miss",
            )

        assert record is not None
        assert record["places"][0]["name"] == "Cafe"

    def test_presented_place_identity_rewrite_uses_the_discovery_fallback(self):
        session = {}
        place = {"name": "Cafe", "address": "1 Main St"}
        with (
            patch.object(cache, "redis_client", _QuotaBlockedRedis()),
            patch.object(
                discovery_store,
                "new_place_id",
                side_effect=["pl_first", "pl_second"],
            ),
        ):
            first_set_id = discovery_store.store_discovery_set(
                session_id="sess-presented",
                places=[place],
                ttl_seconds=60,
            )
            first = discovery_store.load_discovery_set(
                first_set_id,
                session_id="sess-presented",
            )
            discovery_store.record_presented_places(
                session,
                session_id="sess-presented",
                discovery_set_id=first_set_id,
                places=first["places"],
            )

            second_set_id = discovery_store.store_discovery_set(
                session_id="sess-presented",
                places=[place],
                ttl_seconds=60,
            )
            second = discovery_store.load_discovery_set(
                second_set_id,
                session_id="sess-presented",
            )
            rewritten = discovery_store.record_presented_places(
                session,
                session_id="sess-presented",
                discovery_set_id=second_set_id,
                places=second["places"],
            )
            stored = discovery_store.load_discovery_set(
                second_set_id,
                session_id="sess-presented",
            )

        assert rewritten[0]["place_id"] == "pl_first"
        assert stored["places"][0]["place_id"] == "pl_first"

    def test_candidate_set_store_and_load_use_process_memory_when_redis_rejects(self):
        with patch.object(cache, "redis_client", _QuotaBlockedRedis()):
            set_id = candidate_store.store_candidate_set(
                session_id="sess-candidate",
                payload={"candidates": [{"candidate_id": "cd_one"}]},
                ttl_seconds=60,
            )
            record = candidate_store.load_candidate_set(
                set_id,
                session_id="sess-candidate",
            )

        assert record is not None
        assert record["candidates"][0]["candidate_id"] == "cd_one"

    def test_candidate_presentation_falls_back_to_process_memory_when_redis_rejects(
        self,
    ):
        with patch.object(cache, "redis_client", _QuotaBlockedRedis()):
            set_id = candidate_store.store_candidate_set(
                session_id="sess-present",
                payload={"candidates": [{"candidate_id": "cd_one"}]},
                ttl_seconds=60,
            )
            error = candidate_store.mark_presented(
                set_id,
                "cd_one",
                session_id="sess-present",
            )
            duplicate = candidate_store.mark_presented(
                set_id,
                "cd_one",
                session_id="sess-present",
            )
            record = candidate_store.load_candidate_set(
                set_id,
                session_id="sess-present",
            )

        assert error is None
        assert "already presented" in (duplicate or "")
        assert record["presented"]
        assert record["selected_candidate_id"] == "cd_one"

    def test_candidate_presentation_uses_mirror_when_write_fails_and_redis_misses(self):
        with patch.object(cache, "redis_client", _WriteFailsReadMissRedis()):
            set_id = candidate_store.store_candidate_set(
                session_id="sess-present-miss",
                payload={"candidates": [{"candidate_id": "cd_one"}]},
                ttl_seconds=60,
            )
            error = candidate_store.mark_presented(
                set_id,
                "cd_one",
                session_id="sess-present-miss",
            )
            duplicate = candidate_store.mark_presented(
                set_id,
                "cd_one",
                session_id="sess-present-miss",
            )
            record = candidate_store.load_candidate_set(
                set_id,
                session_id="sess-present-miss",
            )

        assert error is None
        assert "already presented" in (duplicate or "")
        assert record["presented"]

    def test_transit_evidence_store_and_load_use_process_memory_when_redis_rejects(
        self,
    ):
        with patch.object(cache, "redis_client", _QuotaBlockedRedis()):
            set_id = transit_evidence_store.store_evidence_set(
                session_id="sess-evidence",
                evidence={
                    "evidence_set_id": "te_one",
                    "requested_operation": "arrivals",
                },
                ttl_seconds=60,
            )
            record = transit_evidence_store.load_evidence_set(
                set_id,
                session_id="sess-evidence",
            )

        assert set_id == "te_one"
        assert record is not None
        assert record["requested_operation"] == "arrivals"
