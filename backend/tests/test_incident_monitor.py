"""Retirement guard for the old request-time incident monitor."""

from __future__ import annotations

import unittest
from pathlib import Path

from app.services.trips.route_incidents import scan as incidents


class RetiredIncidentMonitorTests(unittest.TestCase):
    def test_request_time_monitor_module_does_not_exist(self):
        services_root = Path(__file__).resolve().parents[1] / "app" / "services"
        assert not (services_root / "incident_monitor.py").exists()

    def test_rider_lookup_has_no_provider_scan_vocabulary(self):
        source = Path(incidents.__file__).read_text(encoding="utf-8")
        for forbidden in ("x_search", "web_search", "get_incidents"):
            assert forbidden not in source


if __name__ == "__main__":
    unittest.main()
