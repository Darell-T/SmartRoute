import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from app.routers import trips
from app.services import admission
from app.services.trips.direct_plan import DirectTripError
from fastapi import HTTPException

PRINCIPAL = "v1.test-principal-opaque-123456"


def _request(principal: str | None = PRINCIPAL):
    headers = {} if principal is None else {"X-SmartRoute-Principal": principal}
    return SimpleNamespace(headers=headers, app=SimpleNamespace(state=SimpleNamespace(gtfs=None)))


def _payload():
    return trips.TripRequest(origin_lat=40.7, origin_lng=-73.9, destination="Grand Central")


class TripAdmissionTests(unittest.IsolatedAsyncioTestCase):
    def test_complete_enrichment_step_has_field_specific_bounds(self):
        step = {
            "type": "SUBWAY", "route_id": "A", "departure_stop": "Jay", "arrival_stop": "59 St",
            "minutes_until_train_arrives": -1, "minutes_until_arrival": 8,
            "route_total_minutes": 20, "route_total_seconds": 1200, "duration_minutes": 18,
            "distance_meters": 4300, "stop_count": 5, "segment_index": 1,
            "departure_coords": {"latitude": 40.7, "longitude": -73.9},
            "arrival_coords": {"latitude": 40.8, "longitude": -73.95},
            "intermediate_stop_locations": [{"name": "Canal", "lat": 40.72, "lng": -74.0}],
        }
        assert trips._enrichment_steps_are_bounded([step])
        for key, value in (("route_total_minutes", -1), ("stop_count", 1.5), ("unknown", True)):
            with self.subTest(key=key):
                changed = {**step, key: value}
                assert not trips._enrichment_steps_are_bounded([changed])
    async def test_missing_principal_rejects_before_route_provider(self):
        with patch.object(
            trips.direct_plan, "plan_direct_trip", new_callable=AsyncMock
        ) as plan, pytest.raises(HTTPException) as error:
            await trips.plan_trip(_request(None), _payload())
        assert error.value.status_code == 403
        plan.assert_not_awaited()

    async def test_admission_denial_rejects_before_route_provider(self):
        with patch.object(
            trips.admission,
            "acquire",
            new_callable=AsyncMock,
            side_effect=admission.AdmissionDenied(429, "rate_limited", 1),
        ), patch.object(
            trips.direct_plan, "plan_direct_trip", new_callable=AsyncMock
        ) as plan, pytest.raises(HTTPException) as error:
            await trips.plan_trip(_request(), _payload())
        assert error.value.status_code == 429
        plan.assert_not_awaited()

    async def test_admitted_provider_error_releases_lease_once(self):
        lease = admission.AdmissionLease(PRINCIPAL, "trip", "test-lease")
        with patch.object(
            trips.admission, "acquire", new_callable=AsyncMock, return_value=lease
        ), patch.object(trips.admission, "release", new_callable=AsyncMock) as release, patch.object(
            trips.direct_plan,
            "plan_direct_trip",
            new_callable=AsyncMock,
            side_effect=DirectTripError(502, "Upstream routing provider error"),
        ) as plan, pytest.raises(HTTPException):
            await trips.plan_trip(_request(), _payload())
        plan.assert_awaited_once()
        release.assert_awaited_once_with(lease)

    async def test_cancellation_releases_lease_once(self):
        lease = admission.AdmissionLease(PRINCIPAL, "trip", "test-cancel")
        with patch.object(
            trips.admission, "acquire", new_callable=AsyncMock, return_value=lease
        ), patch.object(
            trips.admission, "release", new_callable=AsyncMock
        ) as release, patch.object(
            trips.direct_plan,
            "plan_direct_trip",
            new_callable=AsyncMock,
            side_effect=asyncio.CancelledError,
        ), pytest.raises(asyncio.CancelledError):
            await trips.plan_trip(_request(), _payload())
        release.assert_awaited_once_with(lease)
