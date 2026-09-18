from __future__ import annotations

import hashlib
import importlib
import json
import unittest
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.routers.live_feed.router import (
    service_alert_id,
    service_alert_signatures,
)
from app.routers.live_feed.socket import next_service_alert_message
from app.services.live_feed.network_snapshot import network_snapshot_store

live_feed_router_mod = importlib.import_module("app.routers.live_feed.router")


class FakeGtfs:
    def __init__(self, locations: dict, error: Exception | None = None):
        self.locations = locations
        self.error = error
        self.requested: list[str] | None = None

    def get_stop_locations(self, stop_ids: list[str]):
        self.requested = list(stop_ids)
        if self.error is not None:
            raise self.error
        return self.locations


class AlertStopNameTests(unittest.IsolatedAsyncioTestCase):
    async def _payload(self, alerts, gtfs):
        snapshot = SimpleNamespace(service_alerts=tuple(alerts), updated_at=1)
        with patch.object(
            network_snapshot_store,
            "get_or_refresh",
            AsyncMock(return_value=snapshot),
        ):
            return await live_feed_router_mod._service_alerts_payload(gtfs)

    async def test_exact_stop_id_lookup_fills_unique_first_seen_names(self):
        payload = await self._payload(
            [MappingProxyType({"stop_ids": ["Q01N", "R16"]})],
            FakeGtfs({
                "Q01N": {"stop_name": "Canal St"},
                "R16": {"stop_name": "Times Sq-42 St"},
            }),
        )
        assert payload["alerts"][0]["stop_names"] == ["Canal St", "Times Sq-42 St"]

    async def test_directional_child_stop_falls_back_to_parent_key(self):
        payload = await self._payload(
            [MappingProxyType({"stop_ids": ["Q01N"]})],
            FakeGtfs({"Q01": {"stop_name": "Canal St"}}),
        )
        assert payload["alerts"][0]["stop_names"] == ["Canal St"]

    async def test_duplicate_resolved_names_keep_first_seen_order(self):
        payload = await self._payload(
            [MappingProxyType({"stop_ids": ["Q01N", "Q01S"]})],
            FakeGtfs({
                "Q01N": {"stop_name": "Canal St"},
                "Q01S": {"stop_name": "Canal St"},
            }),
        )
        assert payload["alerts"][0]["stop_names"] == ["Canal St"]

    async def test_missing_gtfs_leaves_alerts_without_stop_names(self):
        payload = await self._payload(
            [MappingProxyType({"alert_id": "a1", "stop_ids": ["Q01N"]})],
            None,
        )
        assert "stop_names" not in payload["alerts"][0]
        assert payload["alerts"][0]["alert_id"] == "a1"

    async def test_lookup_failure_leaves_alerts_without_stop_names(self):
        payload = await self._payload(
            [MappingProxyType({"alert_id": "a1", "stop_ids": ["Q01N"]})],
            FakeGtfs({}, error=RuntimeError("gtfs down")),
        )
        assert "stop_names" not in payload["alerts"][0]
        assert payload["alerts"][0]["alert_id"] == "a1"


class ServiceAlertIdentityTests(unittest.TestCase):
    def test_prefers_alert_id_and_falls_back_to_routes_and_start(self):
        assert service_alert_id({"alert_id": "lmm:planned_work:1"}, 0) == "lmm:planned_work:1"
        assert service_alert_id({"route_ids": ["Q", "N"], "start": 99}, 3) == "Q-N-99"
        assert service_alert_id({"routeIds": ["A"]}, 7) == "A-7"
        assert service_alert_id({}, 4) == "system-4"

    def test_signatures_hash_the_full_alert_and_collapse_duplicate_ids(self):
        first = {"alert_id": "dup", "header": "one"}
        second = {"alert_id": "dup", "header": "two"}
        signatures = service_alert_signatures([first, second])
        expected = hashlib.sha256(
            json.dumps(second, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        assert signatures == {"dup": expected}


class ServiceAlertChangeDetectionTests(unittest.TestCase):
    def test_first_tick_is_snapshot_with_empty_changed_ids(self):
        payload = {"alerts": [{"alert_id": "a1"}], "updated_at": 10}
        signatures = service_alert_signatures(payload["alerts"])
        message = next_service_alert_message(payload, signatures, {}, False)
        assert message == {
            "type": "SERVICE_SNAPSHOT",
            "data": payload,
            "changed_alert_ids": [],
        }

    def test_identical_signatures_emit_heartbeat_instead_of_update(self):
        payload = {"alerts": [{"alert_id": "a1", "header": "same"}], "updated_at": 11}
        signatures = service_alert_signatures(payload["alerts"])
        message = next_service_alert_message(payload, signatures, signatures, True)
        assert message == {"type": "SERVICE_HEARTBEAT", "updated_at": 11}

    def test_changed_and_new_ids_appear_removed_ids_do_not(self):
        previous = service_alert_signatures([
            {"alert_id": "keep", "header": "old"},
            {"alert_id": "gone", "header": "bye"},
        ])
        current_alerts = [
            {"alert_id": "keep", "header": "new"},
            {"alert_id": "fresh", "header": "hi"},
        ]
        signatures = service_alert_signatures(current_alerts)
        payload = {"alerts": current_alerts, "updated_at": 12}
        message = next_service_alert_message(payload, signatures, previous, True)
        assert message["type"] == "SERVICE_UPDATE"
        assert message["data"] is payload
        assert message["changed_alert_ids"] == ["keep", "fresh"]
        assert "gone" not in message["changed_alert_ids"]
